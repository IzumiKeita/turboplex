use serde_json::json;
use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::Instant;
use turboplex::TestResult;

use super::part1::RuntimePythonEnv;

pub(crate) fn apply_python_encoding_env(cmd: &mut Command) {
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("PYTHONUNBUFFERED", "1");
}

pub(crate) fn run_pytest_collect(
    paths: &[String],
    env: &RuntimePythonEnv,
    worker_id: usize,
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
    cmd.env("TURBOPLEX_MODE", "1");
    cmd.env("TURBOPLEX_WORKER_ID", format!("worker_{}", worker_id));
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

pub(crate) fn run_pytest_test(
    env: &RuntimePythonEnv,
    nodeid: &str,
    worker_id: usize,
) -> TestResult {
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
    cmd.env("TURBOPLEX_MODE", "1");
    cmd.env("TURBOPLEX_WORKER_ID", format!("worker_{}", worker_id));

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
                enriched_data: None,
            }
        }
        Err(e) => TestResult {
            test_name: nodeid.to_string(),
            passed: false,
            cached: false,
            duration_ms,
            error: Some(format!("Failed to run pytest: {}", e)),
            enriched_data: None,
        },
    }
}

pub(crate) fn run_pytest_run_batch(
    env: &RuntimePythonEnv,
    nodeids_json_arg: &str,
    worker_id: usize,
    out_json_path: &Path,
) {
    let mut cmd = Command::new(&env.interpreter);
    cmd.current_dir(&env.cwd);
    cmd.arg("-m");
    cmd.arg(&env.module);
    cmd.arg("pytest-run-batch");
    cmd.arg("--nodeids-json");
    cmd.arg(nodeids_json_arg);
    cmd.arg("--out-json");
    cmd.arg(out_json_path);
    if let Some(pp) = &env.pythonpath {
        cmd.env("PYTHONPATH", pp);
    }
    apply_python_encoding_env(&mut cmd);
    cmd.env("TURBOPLEX_MODE", "1");
    cmd.env("TURBOPLEX_WORKER_ID", format!("worker_{}", worker_id));
    let _ = cmd.output();
}

pub(crate) fn parse_pytest_run_batch_results(
    nodeids: &[String],
    text: &str,
) -> HashMap<String, TestResult> {
    let mut out: HashMap<String, TestResult> = HashMap::new();
    let parsed: serde_json::Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(_) => return out,
    };
    let Some(results) = parsed.get("results").and_then(|v| v.as_array()) else {
        return out;
    };

    for r in results {
        let nodeid = r.get("nodeid").and_then(|v| v.as_str()).unwrap_or("");
        if nodeid.is_empty() {
            continue;
        }
        let passed = r.get("passed").and_then(|v| v.as_bool()).unwrap_or(false);
        let duration_ms = r.get("duration_ms").and_then(|v| v.as_u64()).unwrap_or(0);
        let error = r
            .get("error")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        let skipped = r.get("skipped").and_then(|v| v.as_bool()).unwrap_or(false);
        let skip_reason = r
            .get("skip_reason")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        let enriched_data = if skipped {
            Some(json!({
                "skipped": true,
                "skip_reason": skip_reason
            }))
        } else {
            None
        };
        out.insert(
            nodeid.to_string(),
            TestResult {
                test_name: nodeid.to_string(),
                passed,
                cached: false,
                duration_ms,
                error,
                enriched_data,
            },
        );
    }

    for nodeid in nodeids {
        if !out.contains_key(nodeid) {
            out.insert(
                nodeid.to_string(),
                TestResult {
                    test_name: nodeid.to_string(),
                    passed: false,
                    cached: false,
                    duration_ms: 0,
                    error: Some("Missing pytest-run-batch result".to_string()),
                    enriched_data: None,
                },
            );
        }
    }

    out
}

pub(crate) fn read_text_best_effort(path: &Path) -> Option<String> {
    fs::read_to_string(path).ok()
}
