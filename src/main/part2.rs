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

use super::output::{emit_error, OutputOptions, OutputState, TestEvent};
use super::part1::{
    compute_file_hash, compute_text_hash, get_collected_tests_cache_path, get_test_cache_dir,
    get_test_files_hash, get_test_results_cache_dir, RuntimePythonEnv,
};
use super::pytest_compat::{
    apply_python_encoding_env, parse_pytest_run_batch_results, read_text_best_effort,
    run_pytest_collect, run_pytest_run_batch, run_pytest_test,
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

fn generate_failure_report(
    results: &[(PathBuf, TestResult)],
    _paths: &[String],
    cwd: &Path,
) -> Option<String> {
    let timestamp = chrono::Local::now().format("%Y%m%d_%H%M%S");
    let report_file = cwd.join(format!("failures_{}.md", timestamp));

    let mut content = String::new();
    content.push_str("# Failure Report\n\n");
    content.push_str(&format!("Generated: {}\n\n", chrono::Local::now()));

    let failed: Vec<_> = results.iter().filter(|(_, r)| !r.passed).collect();

    if failed.is_empty() {
        content.push_str("No failures to report!\n");
    } else {
        content.push_str(&format!("## Failed Tests ({} total)\n\n", failed.len()));
        for (path, result) in failed {
            content.push_str(&format!("### {}\n", result.test_name));
            content.push_str(&format!("- **Path**: `{}`\n", path.display()));
            if let Some(error) = &result.error {
                content.push_str(&format!("- **Error**: {}\n", error));
            }
            content.push('\n');
        }
    }

    if fs::write(&report_file, content).is_ok() {
        Some(report_file.to_string_lossy().to_string())
    } else {
        None
    }
}

fn update_latest_report_link(report_path: &str, cwd: &Path) {
    let latest_link = cwd.join("latest_failures.md");
    let _ = std::fs::remove_file(&latest_link);
    #[cfg(unix)]
    let _ = std::os::unix::fs::symlink(report_path, &latest_link);
    #[cfg(windows)]
    let _ = std::fs::write(&latest_link, format!("See: {}", report_path));
}

fn cleanup_old_reports(cwd: &Path, keep_count: usize) {
    let mut reports: Vec<_> = std::fs::read_dir(cwd)
        .ok()
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .filter(|e| {
            let name = e.file_name();
            let name_str = name.to_string_lossy();
            name_str.starts_with("failures_") && name_str.ends_with(".md")
        })
        .map(|e| (e.metadata().ok().and_then(|m| m.modified().ok()), e.path()))
        .collect();

    reports.sort_by(|a, b| b.0.cmp(&a.0));

    for (_, path) in reports.iter().skip(keep_count) {
        let _ = std::fs::remove_file(path);
    }
}

fn run_python_collector(
    paths: &[String],
    env: &RuntimePythonEnv,
) -> Result<Vec<serde_json::Value>, String> {
    if env.compat {
        return run_pytest_collect(paths, env, 0);
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

    let items = parsed["items"].as_array().cloned().unwrap_or_default();
    Ok(items)
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
        enriched_data: Some(json!({"fixture_source": "cached"})),
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

fn is_skipped_result(result: &TestResult) -> bool {
    result
        .enriched_data
        .as_ref()
        .and_then(|v| v.get("skipped"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}

// NUEVA FUNCION: Ejecutar batch de tests
fn run_python_test_batch(
    env: &RuntimePythonEnv,
    tests: &[(String, String)],
    worker_id: usize,
) -> Vec<TestResult> {
    if tests.is_empty() {
        return Vec::new();
    }

    let start = Instant::now();

    // Crear batch JSON
    let test_items: Vec<serde_json::Value> = tests
        .iter()
        .map(|(path, qual)| {
            json!({
                "path": path,
                "qual": qual
            })
        })
        .collect();
    let batch_json = json!(test_items).to_string();
    let mut batch_json_file: Option<PathBuf> = None;
    let batch_json_arg: String = if batch_json.len() > 20_000 {
        let p = temp_json_path("tpx_batch_in");
        if fs::write(&p, &batch_json).is_ok() {
            batch_json_file = Some(p.clone());
            p.to_string_lossy().to_string()
        } else {
            batch_json
        }
    } else {
        batch_json
    };

    let mut cmd = Command::new(&env.interpreter);
    cmd.current_dir(&env.cwd);
    cmd.arg("-m");
    cmd.arg(&env.module);
    cmd.arg("run-batch");
    cmd.arg("--batch-json");
    cmd.arg(&batch_json_arg);

    let out_json_path = temp_json_path("tpx_batch");
    cmd.arg("--out-json");
    cmd.arg(&out_json_path);

    if let Some(pp) = &env.pythonpath {
        cmd.env("PYTHONPATH", pp);
    }
    cmd.env("SQLALCHEMY_SILENCE_UBER_WARNING", "1");
    cmd.env("SQLALCHEMY_LOG", "0");
    cmd.env("TURBOTEST_SUBPROCESS", "1");
    cmd.env("TURBOPLEX_MODE", "1");
    cmd.env("TURBOPLEX_WORKER_ID", format!("worker_{}", worker_id));
    apply_python_encoding_env(&mut cmd);

    let _ = cmd.output();

    let batch_duration = start.elapsed().as_millis() as u64;

    // Leer resultados del batch
    let mut results = Vec::new();
    if let Ok(text) = fs::read_to_string(&out_json_path) {
        let _ = fs::remove_file(&out_json_path);
        if let Ok(resp) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(batch_results) = resp.get("results").and_then(|r| r.as_array()) {
                for (i, result) in batch_results.iter().enumerate() {
                    if let Some((_, qual)) = tests.get(i) {
                        let passed = result
                            .get("passed")
                            .and_then(|v| v.as_bool())
                            .unwrap_or(false);
                        let duration_ms = result
                            .get("duration_ms")
                            .and_then(|v| v.as_u64())
                            .unwrap_or(0);
                        let error = result
                            .get("error")
                            .and_then(|v| v.as_str())
                            .map(String::from);

                        results.push(TestResult {
                            test_name: qual.clone(),
                            passed,
                            cached: false,
                            duration_ms,
                            error,
                            enriched_data: Some(result.clone()),
                        });
                    }
                }
            }
        }
    } else {
        let _ = fs::remove_file(&out_json_path);
    }

    // Si no se pudieron parsear resultados, crear resultados de error
    if results.is_empty() {
        for (_, qual) in tests {
            results.push(TestResult {
                test_name: qual.clone(),
                passed: false,
                cached: false,
                duration_ms: batch_duration / tests.len() as u64,
                error: Some("Batch execution failed".to_string()),
                enriched_data: None,
            });
        }
    }

    if let Some(p) = batch_json_file {
        let _ = fs::remove_file(p);
    }

    results
}

// NUEVA FUNCION: Ejecutar batch con cache keys
fn run_test_item_batch(
    env: &RuntimePythonEnv,
    tests: &[(String, String, String)],
    worker_id: usize,
) -> Vec<(PathBuf, TestResult)> {
    let batch: Vec<(String, String)> = tests
        .iter()
        .map(|(path, qual, _)| (path.clone(), qual.clone()))
        .collect();

    let results = run_python_test_batch(env, &batch, worker_id);

    tests
        .iter()
        .zip(results)
        .map(|((path, _qual, cache_key), result)| {
            let resolved = resolve_test_path(env, path);
            if result.passed && !is_skipped_result(&result) {
                save_cached_pass_result(env, cache_key);
            }
            (resolved, result)
        })
        .collect()
}

pub(crate) fn resolve_test_path(env: &RuntimePythonEnv, path: &str) -> PathBuf {
    let p = PathBuf::from(path);
    if p.is_absolute() {
        p
    } else {
        env.cwd.join(p)
    }
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

    let items = run_python_collector(paths, env)?;

    let _ = fs::create_dir_all(&cache_dir);
    let cache_content = json!({ "items": items }).to_string();
    let _ = fs::write(&cache_file, &cache_content);
    let _ = fs::write(&hash_file, &current_hash);

    Ok(items)
}

pub(crate) fn run_tests_with_paths(
    paths_to_use: &[String],
    watch_mode: bool,
    env: &RuntimePythonEnv,
    out: &OutputOptions,
) {
    let config = load_config("turbo_config.toml");

    let effective_workers = std::env::var("TPX_WORKERS")
        .ok()
        .and_then(|s| s.parse::<usize>().ok())
        .or(config.execution.max_workers)
        .unwrap_or(4);

    let test_items = match get_or_collect_tests(paths_to_use, env) {
        Ok(items) => items,
        Err(e) => {
            let msg = format!("Failed to collect tests: {}", e);
            let _ = emit_error(out, &env.cwd, &msg);
            if !out.wants_json() {
                eprintln!("{}", msg);
            }
            return;
        }
    };

    let total_tests = test_items.len();
    if total_tests == 0 {
        return;
    }

    let mut state = OutputState::new(out.clone(), total_tests);
    let num_threads = effective_workers.min(total_tests);
    let (tx, rx) = channel();

    // Crear chunks y preparar batches con cache keys
    let chunk_size = (total_tests / num_threads).max(1);
    let chunks: Vec<Vec<serde_json::Value>> =
        test_items.chunks(chunk_size).map(|c| c.to_vec()).collect();

    let handles: Vec<_> = chunks
        .iter()
        .enumerate()
        .map(|(worker_id, chunk)| {
            let tx = tx.clone();
            let env = env.clone();

            // Preparar batch con cache keys: (path, qual, cache_key)
            let batch: Vec<(String, String, String)> = chunk
                .iter()
                .map(|item| {
                    let path = item["path"].as_str().unwrap_or("").to_string();
                    let qual = item["qualname"].as_str().unwrap_or("").to_string();
                    let resolved = resolve_test_path(&env, &path);
                    let file_hash =
                        compute_file_hash(&resolved).unwrap_or_else(|| "none".to_string());
                    let cache_key_raw = format!(
                        "{}|{}|{}|{}",
                        resolved.to_string_lossy(),
                        qual,
                        file_hash,
                        env.fingerprint
                    );
                    let cache_key = compute_text_hash(&cache_key_raw);
                    (path, qual, cache_key)
                })
                .collect();

            thread::spawn(move || {
                // Check cache for each test first
                let mut uncached_tests = Vec::new();
                let mut cached_results = Vec::new();

                for (path, qual, cache_key) in &batch {
                    if let Some(cached) = load_cached_pass_result(&env, cache_key, qual) {
                        let resolved = resolve_test_path(&env, path);
                        cached_results.push((resolved, cached));
                    } else {
                        uncached_tests.push((path.clone(), qual.clone(), cache_key.clone()));
                    }
                }

                // Send cached results immediately
                for (resolved, result) in cached_results {
                    let _ = tx.send(TestEvent::Finished {
                        path: resolved,
                        result,
                    });
                }

                // Execute uncached tests in batch
                if !uncached_tests.is_empty() {
                    if env.compat {
                        if env.compat_session {
                            let nodeids: Vec<String> =
                                uncached_tests.iter().map(|(_, q, _)| q.clone()).collect();
                            let nodeids_json = json!(nodeids).to_string();

                            let mut nodeids_file: Option<PathBuf> = None;
                            let nodeids_arg: String = if nodeids_json.len() > 20_000 {
                                let p = temp_json_path("tpx_pytest_nodeids");
                                if fs::write(&p, &nodeids_json).is_ok() {
                                    nodeids_file = Some(p.clone());
                                    p.to_string_lossy().to_string()
                                } else {
                                    nodeids_json
                                }
                            } else {
                                nodeids_json
                            };

                            let out_json_path = temp_json_path("tpx_pytest_out");
                            run_pytest_run_batch(&env, &nodeids_arg, worker_id, &out_json_path);

                            let text = read_text_best_effort(&out_json_path).unwrap_or_default();
                            let _ = fs::remove_file(&out_json_path);
                            if let Some(p) = nodeids_file {
                                let _ = fs::remove_file(p);
                            }

                            let mut results_map = parse_pytest_run_batch_results(&nodeids, &text);

                            for (path, qual, cache_key) in uncached_tests {
                                let resolved = resolve_test_path(&env, &path);
                                let result = results_map.remove(&qual).unwrap_or(TestResult {
                                    test_name: qual.clone(),
                                    passed: false,
                                    cached: false,
                                    duration_ms: 0,
                                    error: Some("Missing pytest-run-batch result".to_string()),
                                    enriched_data: None,
                                });
                                if result.passed && !is_skipped_result(&result) {
                                    save_cached_pass_result(&env, &cache_key);
                                }
                                let _ = tx.send(TestEvent::Finished {
                                    path: resolved,
                                    result,
                                });
                            }
                        } else {
                            for (path, qual, cache_key) in uncached_tests {
                                let resolved = resolve_test_path(&env, &path);
                                let result = run_pytest_test(&env, &qual, worker_id);
                                if result.passed && !is_skipped_result(&result) {
                                    save_cached_pass_result(&env, &cache_key);
                                }
                                let _ = tx.send(TestEvent::Finished {
                                    path: resolved,
                                    result,
                                });
                            }
                        }
                    } else {
                        let results = run_test_item_batch(&env, &uncached_tests, worker_id);
                        for (resolved, result) in results {
                            let _ = tx.send(TestEvent::Finished {
                                path: resolved,
                                result,
                            });
                        }
                    }
                }
            })
        })
        .collect();

    drop(tx);

    for ev in rx {
        state.push(ev);
    }

    for h in handles {
        let _ = h.join();
    }

    let failed_any = state.results().iter().any(|(_, r)| !r.passed);
    let report_path = if failed_any {
        generate_failure_report(state.results(), paths_to_use, &env.cwd)
    } else {
        None
    };

    if let Some(ref path) = report_path {
        update_latest_report_link(path, &env.cwd);
    }

    cleanup_old_reports(&env.cwd, 5);

    let (_, failed) = state
        .finalize(&env.cwd, report_path.as_deref().map(Path::new), None)
        .unwrap_or((0, 1));

    if !watch_mode && failed > 0 {
        std::process::exit(1);
    }
}
