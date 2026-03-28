use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::SystemTime;
use std::time::{Duration, Instant};

use super::cache::{check_cache, save_cache};
use super::config::{python_config_effective, PythonConfig, TurboConfig};
use super::process::run_process_with_timeout;
use super::result::TestResult;

static TMP_COUNTER: AtomicU64 = AtomicU64::new(0);

fn temp_json_path(prefix: &str) -> PathBuf {
    let n = TMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    let ts = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "{}_{}_{}_{}.json",
        prefix,
        std::process::id(),
        ts,
        n
    ))
}

fn discover_python_path(project_root: &Path) -> Option<String> {
    let src_path = project_root.join("src");
    if src_path.exists() && src_path.is_dir() {
        return Some(src_path.to_string_lossy().to_string());
    }

    let app_path = project_root.join("app");
    if app_path.exists() && app_path.is_dir() {
        return Some(app_path.to_string_lossy().to_string());
    }

    let main_py = project_root.join("main.py");
    if main_py.exists() {
        return Some(project_root.to_string_lossy().to_string());
    }

    for entry in std::fs::read_dir(project_root)
        .ok()
        .into_iter()
        .flatten()
        .flatten()
    {
        let path = entry.path();
        if path.is_dir() && path.join("__init__.py").exists() {
            return Some(path.to_string_lossy().to_string());
        }
    }

    None
}

fn apply_pythonpath(cmd: &mut Command, py: &PythonConfig) {
    let mut paths = Vec::new();

    if let Some(ref extra) = py.pythonpath {
        paths.push(extra.clone());
    }

    if let Some(ref proj) = py.project_path {
        let proj_path = PathBuf::from(proj);
        if !proj_path.exists() {
            eprintln!("ERROR: project_path '{}' does not exist", proj);
            std::process::exit(1);
        }

        if let Some(detected) = discover_python_path(&proj_path) {
            paths.push(detected);
        } else {
            eprintln!(
                "ERROR: Could not find Python project structure in '{}'",
                proj
            );
            eprintln!("Expected: src/, app/, or main.py");
            std::process::exit(1);
        }
    }

    if !paths.is_empty() {
        let sep = if cfg!(windows) { ";" } else { ":" };
        let merged = paths.join(sep);

        let key = "PYTHONPATH";
        let final_value = match std::env::var_os(key) {
            Some(existing) => format!("{}{}{}", merged, sep, existing.to_string_lossy()),
            None => merged,
        };
        cmd.env(key, &final_value);
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct PythonCollectedItem {
    pub path: String,
    pub qualname: String,
    pub lineno: u32,
    pub kind: String,
}

impl PythonCollectedItem {
    pub fn label(&self) -> String {
        let base = Path::new(&self.path)
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or(self.path.as_str());
        format!("{}::{}", base, self.qualname)
    }

    pub fn cache_key(&self) -> String {
        format!("py::{}::{}", self.path, self.qualname)
    }
}

#[derive(Debug, Deserialize)]
struct PythonCollectResponse {
    items: Vec<PythonCollectedItem>,
}

#[derive(Debug, Deserialize)]
struct PythonRunResponse {
    passed: bool,
    duration_ms: u64,
    error: Option<String>,
}

pub fn collect_python_tests(cfg: &TurboConfig) -> Result<Vec<PythonCollectedItem>, String> {
    let py = python_config_effective(cfg);
    if !py.enabled {
        return Ok(vec![]);
    }
    let (interpreter, module) = py.effective();
    if py.test_paths.is_empty() {
        return Err("python.test_paths is empty".to_string());
    }

    let actual_interpreter = if cfg!(windows) && interpreter == "python" {
        if let Ok(python_path) = std::env::var("PYTHON_HOME") {
            format!("{}\\python.exe", python_path)
        } else {
            interpreter.clone()
        }
    } else {
        interpreter.clone()
    };

    let mut cmd = Command::new(&actual_interpreter);
    cmd.arg("-m");
    cmd.arg(&module);
    cmd.arg("collect");
    let out_json_path = temp_json_path("tpx_collect");
    for p in &py.test_paths {
        cmd.arg(p);
    }
    cmd.arg("--out-json");
    cmd.arg(&out_json_path);

    apply_pythonpath(&mut cmd, &py);

    cmd.env("SQLALCHEMY_SILENCE_UBER_WARNING", "1");
    cmd.env("SQLALCHEMY_LOG", "0");
    cmd.env("TURBOTEST_SUBPROCESS", "1");
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("PYTHONUNBUFFERED", "1");

    let timeout_ms = 120_000.max(cfg.execution.default_timeout_ms);
    let captured = run_process_with_timeout(&mut cmd, Duration::from_millis(timeout_ms))
        .map_err(|e| format!("python collect spawn failed: {}", e))?;

    let file_text = fs::read_to_string(&out_json_path)
        .map_err(|e| format!("python collect did not produce out-json file: {}", e));
    let _ = fs::remove_file(&out_json_path);

    if captured.timed_out {
        return Err(format!("python collect timed out after {}ms", timeout_ms));
    }
    if captured.status != Some(0) {
        let err = String::from_utf8_lossy(&captured.stderr);
        return Err(format!(
            "python collect failed (status {:?}): {}",
            captured.status, err
        ));
    }

    let text = file_text?;
    let parsed: PythonCollectResponse = serde_json::from_str(text.trim())
        .map_err(|e| format!("collect JSON parse error: {} (file: {:?})", e, text))?;
    Ok(parsed.items)
}

pub(crate) fn run_python_item_fixed(
    item: &PythonCollectedItem,
    cfg: &TurboConfig,
    cache_dir: &Path,
    progress: &Arc<Mutex<indicatif::ProgressBar>>,
) -> TestResult {
    let py = python_config_effective(cfg);
    let (interpreter, module) = py.effective();
    let timeout_ms = cfg.execution.default_timeout_ms;
    let label = item.label();

    let source = PathBuf::from(&item.path);
    let cache_key = item.cache_key();

    if cfg.execution.cache_enabled {
        if let Some(cached) = check_cache(cache_dir, &source, &cache_key) {
            let _ = progress.lock().map(|pb| pb.inc(1));
            return TestResult {
                test_name: label,
                passed: cached.passed,
                cached: true,
                duration_ms: cached.duration_ms,
                error: cached.error,
            };
        }
    }

    let mut cmd = Command::new(&interpreter);
    cmd.arg("-m");
    cmd.arg(&module);
    cmd.arg("run");
    cmd.arg("--path");
    cmd.arg(&item.path);
    cmd.arg("--qual");
    cmd.arg(&item.qualname);
    let out_json_path = temp_json_path("tpx_run");
    cmd.arg("--out-json");
    cmd.arg(&out_json_path);

    apply_pythonpath(&mut cmd, &py);

    cmd.env("SQLALCHEMY_SILENCE_UBER_WARNING", "1");
    cmd.env("SQLALCHEMY_LOG", "0");
    cmd.env("TURBOTEST_SUBPROCESS", "1");
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("PYTHONUNBUFFERED", "1");

    let start = Instant::now();
    let captured = match run_process_with_timeout(&mut cmd, Duration::from_millis(timeout_ms)) {
        Ok(c) => c,
        Err(e) => {
            let _ = progress.lock().map(|pb| pb.inc(1));
            return TestResult {
                test_name: label.clone(),
                passed: false,
                cached: false,
                duration_ms: start.elapsed().as_millis() as u64,
                error: Some(e.to_string()),
            };
        }
    };
    let duration_ms = start.elapsed().as_millis() as u64;

    let file_text = fs::read_to_string(&out_json_path).ok();
    let _ = fs::remove_file(&out_json_path);

    let mut result = if captured.timed_out {
        TestResult {
            test_name: label.clone(),
            passed: false,
            cached: false,
            duration_ms,
            error: Some(format!("Timed out after {}ms (process killed)", timeout_ms)),
        }
    } else {
        match file_text.and_then(|t| serde_json::from_str::<PythonRunResponse>(t.trim()).ok()) {
            Some(j) => TestResult {
                test_name: label.clone(),
                passed: j.passed && captured.status == Some(0),
                cached: false,
                duration_ms: j.duration_ms.max(duration_ms),
                error: j.error.or_else(|| {
                    if captured.status != Some(0) {
                        Some(format!("exit status {}", captured.status.unwrap_or(-1)))
                    } else {
                        None
                    }
                }),
            },
            None => {
                let stdout = String::from_utf8_lossy(&captured.stdout).trim().to_string();
                TestResult {
                    test_name: label.clone(),
                    passed: false,
                    cached: false,
                    duration_ms,
                    error: Some(format!("Missing/invalid out-json; stdout tail: {}", stdout)),
                }
            }
        }
    };

    if !result.passed && result.error.is_none() {
        let err = String::from_utf8_lossy(&captured.stderr);
        if !err.trim().is_empty() {
            result.error = Some(err.trim().to_string());
        }
    }

    if cfg.execution.cache_enabled && result.passed {
        save_cache(
            cache_dir,
            &source,
            &cache_key,
            &TestResult {
                test_name: result.test_name.clone(),
                passed: true,
                cached: false,
                duration_ms: result.duration_ms,
                error: None,
            },
        );
    }

    let _ = progress.lock().map(|pb| pb.inc(1));
    result
}
