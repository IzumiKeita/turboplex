//! Batch test execution module

use serde_json::json;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::Instant;

use turboplex::TestResult;

use super::super::part1::RuntimePythonEnv;
use super::super::pytest_compat::apply_python_encoding_env;
use super::cache::{is_skipped_result, save_cached_pass_result};
use super::collection::resolve_test_path;
use super::temp::temp_json_path;

/// Execute a batch of tests in a single Python subprocess
pub(crate) fn run_python_test_batch(
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
    let subcmd = match env.execution_mode {
        super::super::ExecutionMode::Native => "run-batch",
        super::super::ExecutionMode::Pytest => "run-batch",
        super::super::ExecutionMode::Unittest => "unittest-run-batch",
        super::super::ExecutionMode::Behave => "behave-run-batch",
    };
    cmd.arg(subcmd);
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

/// Execute a batch with cache key tracking
pub(crate) fn run_test_item_batch(
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
