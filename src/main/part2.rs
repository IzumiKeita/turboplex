use colored::Colorize;
use serde_json::json;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::channel;
use std::thread;
use std::time::{Instant, SystemTime};
use turboplex::{load_config, TestResult};
use walkdir::WalkDir;

use super::part1::{
    compute_file_hash, compute_text_hash, get_collected_tests_cache_path, get_test_cache_dir,
    get_test_files_hash, get_test_results_cache_dir, RuntimePythonEnv,
};

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

fn apply_python_encoding_env(cmd: &mut Command) {
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("PYTHONUNBUFFERED", "1");
}

fn generate_failure_report(
    results: &[(PathBuf, TestResult)],
    _test_paths: &[String],
    base_dir: &Path,
) {
    let mut report = json!({
        "timestamp": chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
        "total_tests": results.len(),
        "failed_count": results.iter().filter(|(_, r)| !r.passed).count(),
        "failures": []
    });

    for (path, result) in results {
        if !result.passed {
            let file_content = fs::read_to_string(path).unwrap_or_default();
            let lines: Vec<&str> = file_content.lines().collect();

            let error_msg = result.error.as_deref().unwrap_or("");
            let line_no = result
                .error
                .as_ref()
                .and_then(|e| {
                    e.lines()
                        .find(|l| l.contains("line"))
                        .and_then(|l| l.split("line").last()?.trim().parse::<usize>().ok())
                })
                .unwrap_or(0);

            let context_start = line_no.saturating_sub(6);
            let context_end = (line_no + 5).min(lines.len());
            let context: Vec<String> = (context_start..context_end)
                .map(|i| {
                    let prefix = if i + 1 == line_no { ">>> " } else { "    " };
                    format!("{}{}: {}", prefix, i + 1, lines.get(i).unwrap_or(&""))
                })
                .collect();

            let failure = json!({
                "test": result.test_name,
                "file": path.to_string_lossy(),
                "line": line_no,
                "error": error_msg,
                "duration_ms": result.duration_ms,
                "context": context
            });

            report["failures"].as_array_mut().unwrap().push(failure);
        }
    }

    let report_path = base_dir.join(".tplex_report.json");
    if let Ok(content) = serde_json::to_string_pretty(&report) {
        let _ = fs::write(report_path, content);
        println!("\n{} Report generated: .tplex_report.json", "📄".yellow());
    }
}

fn run_pytest_collect(
    paths: &[String],
    env: &RuntimePythonEnv,
) -> Result<Vec<serde_json::Value>, String> {
    let mut cmd = Command::new(&env.interpreter);
    cmd.current_dir(&env.cwd);
    cmd.arg("-m");
    cmd.arg("pytest");
    cmd.arg("--collect-only");
    cmd.arg("-q");
    cmd.args(paths);
    if let Some(pp) = &env.pythonpath {
        cmd.env("PYTHONPATH", pp);
    }
    apply_python_encoding_env(&mut cmd);
    let output = cmd
        .output()
        .map_err(|e| format!("Failed to run pytest collect: {}", e))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("Pytest collect failed: {}", stderr));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut items: Vec<serde_json::Value> = Vec::new();
    for line in stdout.lines().map(|l| l.trim()).filter(|l| !l.is_empty()) {
        let path_part = line.split("::").next().unwrap_or("").trim();
        if path_part.is_empty() || !path_part.ends_with(".py") {
            continue;
        }
        items.push(json!({
            "path": path_part,
            "qualname": line,
            "kind": "pytest"
        }));
    }
    Ok(items)
}

fn run_python_collector(
    paths: &[String],
    env: &RuntimePythonEnv,
) -> Result<Vec<serde_json::Value>, String> {
    if env.compat {
        return run_pytest_collect(paths, env);
    }

    let mut cmd = Command::new(&env.interpreter);
    cmd.current_dir(&env.cwd);
    cmd.arg("-m");
    cmd.arg(&env.module);
    cmd.arg("collect");
    cmd.args(paths);
    let out_json_path = temp_json_path("tpx_collect");
    cmd.arg("--out-json");
    cmd.arg(&out_json_path);
    if let Some(pp) = &env.pythonpath {
        cmd.env("PYTHONPATH", pp);
    }
    cmd.env("SQLALCHEMY_SILENCE_UBER_WARNING", "1");
    cmd.env("SQLALCHEMY_LOG", "0");
    cmd.env("TURBOTEST_SUBPROCESS", "1");
    apply_python_encoding_env(&mut cmd);

    let output = cmd
        .output()
        .map_err(|e| format!("Failed to run collector: {}", e))?;

    let file_text = fs::read_to_string(&out_json_path)
        .map_err(|e| format!("collector did not produce out-json file: {}", e));
    let _ = fs::remove_file(&out_json_path);

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        let mut msg = String::from("Collector failed");
        if !stderr.is_empty() {
            msg.push_str(&format!("; stderr: {}", stderr));
        } else if !stdout.is_empty() {
            msg.push_str(&format!("; stdout: {}", stdout));
        }
        return Err(msg);
    }

    let text = file_text?;
    let parsed: serde_json::Value = serde_json::from_str(text.trim())
        .map_err(|e| format!("Failed to parse collector out-json: {}", e))?;

    Ok(parsed["items"].as_array().cloned().unwrap_or_default())
}

fn load_cached_pass_result(
    env: &RuntimePythonEnv,
    cache_key: &str,
    test_name: &str,
) -> Option<TestResult> {
    let dir = get_test_results_cache_dir(&env.cwd);
    let path = dir.join(format!("{}.json", cache_key));
    let text = fs::read_to_string(path).ok()?;
    let parsed: serde_json::Value = serde_json::from_str(&text).ok()?;
    if parsed.get("passed").and_then(|v| v.as_bool()) != Some(true) {
        return None;
    }
    Some(TestResult {
        test_name: test_name.to_string(),
        passed: true,
        cached: true,
        duration_ms: 0,
        error: None,
    })
}

fn save_cached_pass_result(env: &RuntimePythonEnv, cache_key: &str) {
    let dir = get_test_results_cache_dir(&env.cwd);
    let _ = fs::create_dir_all(&dir);
    let path = dir.join(format!("{}.json", cache_key));
    let payload = json!({ "passed": true });
    if let Ok(text) = serde_json::to_string(&payload) {
        let _ = fs::write(path, text);
    }
}

fn run_python_test(env: &RuntimePythonEnv, path: &str, qual: &str) -> TestResult {
    let start = Instant::now();

    let mut cmd = Command::new(&env.interpreter);
    cmd.current_dir(&env.cwd);
    cmd.arg("-m");
    cmd.arg(&env.module);
    cmd.arg("run");
    cmd.arg("--path");
    cmd.arg(path);
    cmd.arg("--qual");
    cmd.arg(qual);
    let out_json_path = temp_json_path("tpx_run");
    cmd.arg("--out-json");
    cmd.arg(&out_json_path);
    if let Some(pp) = &env.pythonpath {
        cmd.env("PYTHONPATH", pp);
    }
    cmd.env("SQLALCHEMY_SILENCE_UBER_WARNING", "1");
    cmd.env("SQLALCHEMY_LOG", "0");
    cmd.env("TURBOTEST_SUBPROCESS", "1");
    apply_python_encoding_env(&mut cmd);

    let output = cmd.output();

    let duration_ms = start.elapsed().as_millis() as u64;

    match output {
        Ok(out) => {
            let file_text = fs::read_to_string(&out_json_path).ok();
            let _ = fs::remove_file(&out_json_path);

            if let Some(text) = file_text {
                if let Ok(resp) = serde_json::from_str::<serde_json::Value>(text.trim()) {
                    return TestResult {
                        test_name: qual.to_string(),
                        passed: resp
                            .get("passed")
                            .and_then(|v| v.as_bool())
                            .unwrap_or(false),
                        cached: false,
                        duration_ms: resp
                            .get("duration_ms")
                            .and_then(|v| v.as_u64())
                            .unwrap_or(duration_ms),
                        error: resp.get("error").and_then(|v| v.as_str()).map(String::from),
                    };
                }
            }

            let stdout = String::from_utf8_lossy(&out.stdout);
            if let Ok(resp) = serde_json::from_str::<serde_json::Value>(stdout.trim()) {
                TestResult {
                    test_name: qual.to_string(),
                    passed: resp
                        .get("passed")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false),
                    cached: false,
                    duration_ms: resp
                        .get("duration_ms")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(duration_ms),
                    error: resp.get("error").and_then(|v| v.as_str()).map(String::from),
                }
            } else {
                TestResult {
                    test_name: qual.to_string(),
                    passed: out.status.success(),
                    cached: false,
                    duration_ms,
                    error: if out.status.success() {
                        None
                    } else {
                        Some(String::from_utf8_lossy(&out.stderr).to_string())
                    },
                }
            }
        }
        Err(e) => TestResult {
            test_name: qual.to_string(),
            passed: false,
            cached: false,
            duration_ms,
            error: Some(format!("Failed to run test: {}", e)),
        },
    }
}

fn run_pytest_test(env: &RuntimePythonEnv, nodeid: &str) -> TestResult {
    let start = Instant::now();

    let mut cmd = Command::new(&env.interpreter);
    cmd.current_dir(&env.cwd);
    cmd.arg("-m");
    cmd.arg("pytest");
    cmd.arg("-q");
    cmd.arg(nodeid);
    if let Some(pp) = &env.pythonpath {
        cmd.env("PYTHONPATH", pp);
    }
    apply_python_encoding_env(&mut cmd);
    let output = cmd.output();

    let duration_ms = start.elapsed().as_millis() as u64;
    match output {
        Ok(out) => {
            let passed = out.status.success();
            let stderr = String::from_utf8_lossy(&out.stderr);
            let stdout = String::from_utf8_lossy(&out.stdout);
            let err = if passed {
                None
            } else if !stderr.trim().is_empty() {
                Some(stderr.to_string())
            } else if !stdout.trim().is_empty() {
                Some(stdout.to_string())
            } else {
                Some("pytest failed".to_string())
            };
            TestResult {
                test_name: nodeid.to_string(),
                passed,
                cached: false,
                duration_ms,
                error: err,
            }
        }
        Err(e) => TestResult {
            test_name: nodeid.to_string(),
            passed: false,
            cached: false,
            duration_ms,
            error: Some(format!("Failed to run pytest: {}", e)),
        },
    }
}

fn run_test_item(env: &RuntimePythonEnv, path: &str, qual: &str) -> TestResult {
    let resolved = resolve_test_path(env, path);
    let file_hash = compute_file_hash(&resolved).unwrap_or_else(|| "none".to_string());
    let cache_key_raw = format!(
        "{}|{}|{}|{}",
        resolved.to_string_lossy(),
        qual,
        file_hash,
        env.fingerprint
    );
    let cache_key = compute_text_hash(&cache_key_raw);
    if let Some(cached) = load_cached_pass_result(env, &cache_key, qual) {
        return cached;
    }
    let result = if env.compat {
        run_pytest_test(env, qual)
    } else {
        run_python_test(env, path, qual)
    };
    if result.passed {
        save_cached_pass_result(env, &cache_key);
    }
    result
}

fn get_or_collect_tests(
    paths: &[String],
    env: &RuntimePythonEnv,
) -> Result<Vec<serde_json::Value>, String> {
    let cache_dir = get_test_cache_dir(&env.cwd);
    let cache_file = get_collected_tests_cache_path(&env.cwd);
    let hash_file = cache_dir.join("files_hash.txt");

    let mut test_files: Vec<PathBuf> = Vec::new();
    for p in paths {
        let pb = resolve_test_path(env, p);
        if pb.is_file() {
            test_files.push(pb);
        } else {
            let walker = WalkDir::new(&pb).max_depth(10);
            for entry in walker.into_iter().filter_map(|e| e.ok()) {
                if entry.file_type().is_file() {
                    let name = entry.file_name().to_string_lossy();
                    if (name.starts_with("test_") || name.ends_with("_test.py"))
                        && name.ends_with(".py")
                    {
                        test_files.push(entry.path().to_path_buf());
                    }
                }
            }
        }
    }

    let current_hash = get_test_files_hash(&test_files, &env.fingerprint);

    if let (Ok(cached_content), Ok(stored_hash)) = (
        fs::read_to_string(&cache_file),
        fs::read_to_string(&hash_file),
    ) {
        if stored_hash.trim() == current_hash {
            let parsed: serde_json::Value = serde_json::from_str(&cached_content)
                .map_err(|e| format!("Invalid cache: {}", e))?;
            return Ok(parsed["items"].as_array().cloned().unwrap_or_default());
        }
    }

    println!("  {} Collecting tests...", "📦".cyan());
    let items = run_python_collector(paths, env)?;

    let _ = fs::create_dir_all(&cache_dir);
    let cache_content = json!({ "items": items }).to_string();
    let _ = fs::write(&cache_file, &cache_content);
    let _ = fs::write(&hash_file, &current_hash);

    Ok(items)
}

pub(crate) fn resolve_test_path(env: &RuntimePythonEnv, path: &str) -> PathBuf {
    let p = PathBuf::from(path);
    if p.is_absolute() {
        p
    } else {
        env.cwd.join(p)
    }
}

pub(crate) fn run_tests_with_paths(
    paths_to_use: &[String],
    watch_mode: bool,
    env: &RuntimePythonEnv,
) {
    let config_paths = [
        "turbo_config.toml",
        "../turbo_config.toml",
        "./pyproject.toml",
    ];
    let config = config_paths
        .iter()
        .find(|p| std::path::Path::new(p).exists())
        .map(|p| load_config(p))
        .unwrap_or_default();

    println!(
        "\n[Config]: workers={}",
        config.execution.max_workers.unwrap_or(8)
    );
    println!("\n{} {}", "📁".cyan().bold(), "Test directories:".bold());
    for p in paths_to_use {
        println!("   - {}", p);
    }

    let test_items = match get_or_collect_tests(paths_to_use, env) {
        Ok(items) => items,
        Err(e) => {
            eprintln!("{} Failed to collect tests: {}", "ERROR".red(), e);
            return;
        }
    };

    let total_tests = test_items.len();
    if total_tests == 0 {
        println!("\n{}", "No tests found.".yellow());
        return;
    }

    println!("\n{} Found {} tests", "✓".green(), total_tests);
    println!("\n{} Running tests...\n", "⚡".cyan().bold());

    let num_threads = config.execution.max_workers.unwrap_or(8).min(total_tests);
    let (tx, rx) = channel();

    let chunks: Vec<_> = test_items
        .chunks((total_tests / num_threads).max(1))
        .collect();

    let handles: Vec<_> = chunks
        .iter()
        .map(|chunk| {
            let tx = tx.clone();
            let chunk: Vec<serde_json::Value> = chunk.to_vec();
            let env = env.clone();
            thread::spawn(move || {
                let mut results = Vec::new();
                for item in &chunk {
                    let path = item["path"].as_str().unwrap_or("");
                    let qual = item["qualname"].as_str().unwrap_or("");
                    let resolved = resolve_test_path(&env, path);
                    let result = run_test_item(&env, path, qual);
                    results.push((resolved, result));
                }
                let _ = tx.send(results);
            })
        })
        .collect();

    drop(tx);

    let mut all_results = Vec::new();
    for r in rx {
        all_results.extend(r);
    }

    for h in handles {
        let _ = h.join();
    }

    let mut passed = 0;
    let mut failed = 0;

    for (path, result) in &all_results {
        let file = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("unknown");
        let label = format!("{} :: {}", file, result.test_name);

        if result.passed {
            println!(
                "  {} [PASS] {} ({}ms)",
                "OK".green(),
                label.bold(),
                result.duration_ms
            );
            passed += 1;
        } else {
            println!(
                "  {} [FAIL] {} ({}ms)",
                "XX".red(),
                label.bold(),
                result.duration_ms
            );
            if let Some(ref err) = result.error {
                println!("        |-- Error: {}", err.red());
            }
            failed += 1;
        }
    }

    println!(
        "\n  Results: {} passed, {} failed",
        passed.to_string().green(),
        failed.to_string().red()
    );

    if failed > 0 {
        generate_failure_report(&all_results, paths_to_use, &env.cwd);
    }

    if !watch_mode && failed > 0 {
        std::process::exit(1);
    }
}
