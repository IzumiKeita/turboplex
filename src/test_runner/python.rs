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

fn discover_venv_python(project_root: &Path) -> Option<String> {
    // Check for .venv or venv in project root
    let venv_paths = [".venv", "venv", ".env", "env"];
    
    for venv_name in &venv_paths {
        let venv_path = project_root.join(venv_name);
        if venv_path.exists() && venv_path.is_dir() {
            // Check for Scripts/python.exe (Windows) or bin/python (Unix)
            let python_exe = if cfg!(windows) {
                venv_path.join("Scripts").join("python.exe")
            } else {
                venv_path.join("bin").join("python")
            };
            
            if python_exe.exists() {
                return Some(python_exe.to_string_lossy().to_string());
            }
        }
    }
    
    None
}

fn get_effective_python_interpreter(interpreter: &str, project_path: Option<&Path>) -> String {
    // If interpreter is explicitly set to something other than "python", use it
    if interpreter != "python" {
        return interpreter.to_string();
    }
    
    // Try to find venv Python in project path
    if let Some(proj) = project_path {
        if let Some(venv_python) = discover_venv_python(proj) {
            return venv_python;
        }
        
        // Also check parent directories for venv
        for parent in proj.ancestors().take(3) {
            if let Some(venv_python) = discover_venv_python(parent) {
                return venv_python;
            }
        }
    }
    
    // Check PYTHON_HOME env var (Windows)
    if cfg!(windows) {
        if let Ok(python_home) = std::env::var("PYTHON_HOME") {
            let python_exe = PathBuf::from(&python_home).join("python.exe");
            if python_exe.exists() {
                return python_exe.to_string_lossy().to_string();
            }
        }
    }
    
    // Fall back to the provided interpreter
    interpreter.to_string()
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

#[derive(Debug, Deserialize, Serialize)]
struct PythonRunResponse {
    passed: bool,
    duration_ms: u64,
    test_info: Option<TestInfo>,
    error_context: Option<ErrorContext>,
    fixtures_used: Option<Vec<String>>,
    stdout: Option<String>,
    stderr: Option<String>,
    // Legacy field for backward compatibility
    error: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
struct TestInfo {
    path: String,
    qualname: String,
    lineno: u32,
}

#[derive(Debug, Deserialize, Serialize)]
struct ErrorContext {
    #[serde(rename = "type")]
    error_type: String,
    message: String,
    diff: Option<DiffInfo>,
    traceback: Vec<StackFrame>,
    #[serde(rename = "locals_slim")]
    locals_slim: std::collections::HashMap<String, String>,
}

#[derive(Debug, Deserialize, Serialize)]
struct DiffInfo {
    expected: Vec<String>,
    actual: Vec<String>,
    operator: String,
}

#[derive(Debug, Deserialize, Serialize)]
struct StackFrame {
    file: String,
    line: u32,
    function: String,
    snippet: Vec<String>,
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

    // Determine project path from test paths (convert to absolute if needed)
    let current_dir = std::env::current_dir().ok();
    let project_path = py.test_paths.first()
        .and_then(|p| {
            let path = Path::new(p);
            // Convert to absolute path if relative
            let abs_path = if path.is_absolute() {
                path.to_path_buf()
            } else {
                current_dir.as_ref()?.join(path)
            };
            abs_path.parent().map(|p| p.to_path_buf())
        })
        .or_else(|| py.project_path.as_ref().map(Path::new).map(|p| p.to_path_buf()));

    // Get effective interpreter with venv detection
    let actual_interpreter = get_effective_python_interpreter(&interpreter, project_path.as_deref());

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
) -> (TestResult, Option<serde_json::Value>) {
    let py = python_config_effective(cfg);
    let (interpreter, module) = py.effective();
    let timeout_ms = cfg.execution.default_timeout_ms;
    let label = item.label();

    let source = PathBuf::from(&item.path);
    let cache_key = item.cache_key();

    // Determine project path from test file
    let project_path = source.parent();
    let actual_interpreter = get_effective_python_interpreter(&interpreter, project_path);

    if cfg.execution.cache_enabled {
        if let Some(cached) = check_cache(cache_dir, &source, &cache_key) {
            let _ = progress.lock().map(|pb| pb.inc(1));
            return (TestResult {
                test_name: label,
                passed: cached.passed,
                cached: true,
                duration_ms: cached.duration_ms,
                error: cached.error,
                enriched_data: None, // Cache no tiene datos enriquecidos
            }, None);
        }
    }

    let mut cmd = Command::new(&actual_interpreter);
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

    // DEBUG: Log the command being executed
    eprintln!("[DEBUG] Running test: {}", label);
    eprintln!("[DEBUG] Command: {:?}", cmd);
    eprintln!("[DEBUG] Working dir: {:?}", std::env::current_dir().ok());

    let start = Instant::now();
    let captured = match run_process_with_timeout(&mut cmd, Duration::from_millis(timeout_ms)) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[DEBUG] Process execution failed: {}", e);
            let _ = progress.lock().map(|pb| pb.inc(1));
            return (TestResult {
                test_name: label.clone(),
                passed: false,
                cached: false,
                duration_ms: start.elapsed().as_millis() as u64,
                error: Some(format!("Process execution failed: {}", e)),
                enriched_data: None,
            }, None);
        }
    };
    let duration_ms = start.elapsed().as_millis() as u64;

    // DEBUG: Log process results
    eprintln!("[DEBUG] Process exited with status: {:?}", captured.status);
    eprintln!("[DEBUG] Timed out: {}", captured.timed_out);
    
    let stdout_preview = String::from_utf8_lossy(&captured.stdout);
    let stderr_preview = String::from_utf8_lossy(&captured.stderr);
    eprintln!("[DEBUG] stdout preview (first 500 chars): {}", &stdout_preview[..stdout_preview.len().min(500)]);
    eprintln!("[DEBUG] stderr preview (first 500 chars): {}", &stderr_preview[..stderr_preview.len().min(500)]);

    let file_text = fs::read_to_string(&out_json_path).ok();
    let _ = fs::remove_file(&out_json_path);
    
    // Variable to store raw JSON for report generation
    let mut json_raw: Option<serde_json::Value> = None;

    let mut result = if captured.timed_out {
        TestResult {
            test_name: label.clone(),
            passed: false,
            cached: false,
            duration_ms,
            error: Some(format!("Timed out after {}ms (process killed)", timeout_ms)),
            enriched_data: None,
        }
    } else {
        match file_text.and_then(|t| serde_json::from_str::<PythonRunResponse>(t.trim()).ok()) {
            Some(j) => {
                // Keep the raw JSON for report generation
                json_raw = Some(serde_json::to_value(&j).unwrap_or(serde_json::Value::Null));
                
                // Extract error from either new error_context or legacy error field
                let error_msg = if let Some(ref ctx) = j.error_context {
                    // Build enriched error message from error_context
                    let mut parts = vec![
                        format!("{}: {}", ctx.error_type, ctx.message),
                        format!("Location: {}:{}", 
                            ctx.traceback.first().map(|f| f.file.clone()).unwrap_or_default(),
                            ctx.traceback.first().map(|f| f.line).unwrap_or(0)),
                    ];
                    if let Some(ref diff) = ctx.diff {
                        parts.push(format!("Expected: {:?}, Got: {:?}", diff.expected, diff.actual));
                    }
                    // Add first frame snippet
                    if let Some(first_frame) = ctx.traceback.first() {
                        let snippet_preview: Vec<String> = first_frame.snippet.iter()
                            .filter(|s| s.contains(">"))
                            .cloned()
                            .collect();
                        if !snippet_preview.is_empty() {
                            parts.push(format!("Code:\n{}", snippet_preview.join("\n")));
                        }
                    }
                    Some(parts.join("\n"))
                } else {
                    j.error
                };
                
                TestResult {
                    test_name: label.clone(),
                    passed: j.passed && captured.status == Some(0),
                    cached: false,
                    duration_ms: j.duration_ms.max(duration_ms),
                    error: error_msg.or_else(|| {
                        if captured.status != Some(0) {
                            Some(format!("exit status {}", captured.status.unwrap_or(-1)))
                        } else {
                            None
                        }
                    }),
                    enriched_data: json_raw.clone(),
                }
            },
            None => {
                let stdout = String::from_utf8_lossy(&captured.stdout).trim().to_string();
                TestResult {
                    test_name: label.clone(),
                    passed: false,
                    cached: false,
                    duration_ms,
                    error: Some(format!("Missing/invalid out-json; stdout tail: {}", stdout)),
                    enriched_data: None,
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
                enriched_data: None,
            },
        );
    }

    let _ = progress.lock().map(|pb| pb.inc(1));
    (result, json_raw)
}
