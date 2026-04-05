//! Test runner execution engine - Core runner module
//!
//! This is the main entry point for test execution, coordinating
//! caching, batching, parallel execution, and result aggregation.

use serde_json::json;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::mpsc::channel;
use std::thread;

use turboplex::{load_config, TestResult};
use walkdir::WalkDir;

use super::super::output::{emit_error, OutputOptions, OutputState, TestEvent};
use super::super::part1::{
    compute_file_hash, compute_text_hash, get_test_cache_dir, get_tplex_dir,
    get_tplex_failures_dir, get_tplex_reports_dir, RuntimePythonEnv,
};
use super::super::pytest_compat::{
    parse_pytest_run_batch_results, read_text_best_effort, run_pytest_run_batch, run_pytest_test,
};
use super::batch::run_test_item_batch;
use super::cache::{is_skipped_result, load_cached_pass_result, save_cached_pass_result};
use super::collection::{get_or_collect_tests, resolve_test_path};
use super::reports::{cleanup_old_reports, generate_failure_report, update_latest_report_link};
use super::temp::temp_json_path;

/// v0.3.4 TurboGuide Health Check: Detect conftest.py bottlenecks
fn health_check_conftest(cwd: &Path) {
    // Look for conftest.py in the project
    for entry in WalkDir::new(cwd).max_depth(3).into_iter().flatten() {
        let path = entry.path();
        if path.file_name() == Some(std::ffi::OsStr::new("conftest.py")) {
            if let Ok(metadata) = fs::metadata(path) {
                let size_kb = metadata.len() / 1024;
                if size_kb > 50 {
                    eprintln!(
                        "⚠️  TURBO_HEALTH: {} file is large ({}KB). This may slow down discovery.",
                        path.display(),
                        size_kb
                    );
                    eprintln!("   💡 Consider using lazy imports or splitting into turbofix.py");
                }
            }
        }
    }
}

/// Main test execution entry point
pub(crate) fn run_tests_with_paths(
    paths_to_use: &[String],
    watch_mode: bool,
    env: &RuntimePythonEnv,
    out: &OutputOptions,
) {
    // v0.3.4: TurboGuide Health Check
    health_check_conftest(&env.cwd);

    // v0.3.4: Create .tplex/ infrastructure preemptively
    let _ = fs::create_dir_all(get_tplex_dir(&env.cwd));
    let _ = fs::create_dir_all(get_tplex_reports_dir(&env.cwd));
    let _ = fs::create_dir_all(get_tplex_failures_dir(&env.cwd));
    let _ = fs::create_dir_all(get_test_cache_dir(&env.cwd));

    // v0.3.4: Migrate legacy files from root to .tplex/
    migrate_legacy_files(&env.cwd);

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

    cleanup_old_reports(&env.cwd, 20);

    // Generate JSON report in .tplex/reports/
    let reports_dir = get_tplex_reports_dir(&env.cwd);
    let _ = fs::create_dir_all(&reports_dir);
    let timestamp = chrono::Local::now().format("%Y%m%d_%H%M%S");
    let jsonl_path = reports_dir.join(format!("report_{}.json", timestamp));

    let (_, failed) = state
        .finalize(
            &env.cwd,
            report_path.as_deref().map(Path::new),
            Some(&jsonl_path),
        )
        .unwrap_or((0, 1));

    // Create/update tplex_last_run.log in root as single point of contact
    let last_run_log = env.cwd.join("tplex_last_run.log");
    let log_content = format!(
        "Last run: {}\nStatus: {}\nJSON report: {}\n",
        timestamp,
        if failed == 0 { "SUCCESS" } else { "FAILED" },
        jsonl_path.to_string_lossy()
    );
    let _ = fs::write(&last_run_log, log_content);

    if !watch_mode && failed > 0 {
        std::process::exit(1);
    }
}

/// v0.3.4 Migration: Move legacy files from root to .tplex/ subdirectories
fn migrate_legacy_files(cwd: &Path) {
    use std::fs;

    // Move tplex_*.json files to .tplex/reports/
    let reports_dir = get_tplex_reports_dir(cwd);
    if let Ok(entries) = fs::read_dir(cwd) {
        for entry in entries.filter_map(|e| e.ok()) {
            let name = entry.file_name();
            let name_str = name.to_string_lossy();

            // Migrate JSON reports: tplex_*.json -> .tplex/reports/
            if name_str.starts_with("tplex_") && name_str.ends_with(".json") {
                let dest = reports_dir.join(&*name_str);
                let _ = fs::rename(entry.path(), dest);
            }

            // Migrate failure reports: failures_*.md -> .tplex/failures/
            if name_str.starts_with("failures_") && name_str.ends_with(".md") {
                let failures_dir = get_tplex_failures_dir(cwd);
                let dest = failures_dir.join(&*name_str);
                let _ = fs::rename(entry.path(), dest);
            }

            // Clean up old latest_failures.md from root (symlink recreated in .tplex/failures/)
            if name_str == "latest_failures.md" {
                let _ = fs::remove_file(entry.path());
            }
        }
    }
}
