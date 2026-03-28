use std::path::Path;
use std::process::Command;
use std::time::{Duration, Instant};

use super::cache::{check_cache, save_cache, shell_cache_key};
use super::config::{TestContext, TestDefinition};
use super::process::run_process_with_timeout;
use super::result::TestResult;

pub fn run_test(test: &TestDefinition, test_file: &Path, ctx: &TestContext) -> TestResult {
    let cache_key = shell_cache_key(test);
    if ctx.config.execution.cache_enabled {
        if let Some(cached) = check_cache(&ctx.cache_dir, test_file, &cache_key) {
            return cached;
        }
    }

    let timeout_ms = test
        .expected
        .as_ref()
        .and_then(|e| e.timeout_ms)
        .unwrap_or(ctx.config.execution.default_timeout_ms);

    let mut shell_cmd = Command::new(&test.command);
    shell_cmd.args(&test.args);

    let start = Instant::now();
    let captured = run_process_with_timeout(&mut shell_cmd, Duration::from_millis(timeout_ms));
    let duration_ms = start.elapsed().as_millis() as u64;

    let result = match captured {
        Ok(cap) if cap.timed_out => TestResult {
            test_name: test.name.clone(),
            passed: false,
            cached: false,
            duration_ms,
            error: Some(format!("Timed out after {}ms (process killed)", timeout_ms)),
        },
        Ok(cap) => {
            let exit_code = cap.status.unwrap_or(-1);
            let expected_code = test
                .expected
                .as_ref()
                .and_then(|e| e.status_code)
                .unwrap_or(0);

            let mut error = None;
            let mut passed = exit_code == expected_code;

            if passed {
                if let Some(ref expected_str) =
                    test.expected.as_ref().and_then(|e| e.contains.clone())
                {
                    let output_str = String::from_utf8_lossy(&cap.stdout);
                    if !output_str.contains(expected_str) {
                        passed = false;
                        error = Some(format!("Output does not contain '{}'", expected_str));
                    }
                }
            } else {
                error = Some(format!(
                    "Expected exit code {}, got {}",
                    expected_code, exit_code
                ));
            }

            TestResult {
                test_name: test.name.clone(),
                passed,
                cached: false,
                duration_ms,
                error,
            }
        }
        Err(e) => TestResult {
            test_name: test.name.clone(),
            passed: false,
            cached: false,
            duration_ms,
            error: Some(e.to_string()),
        },
    };

    if ctx.config.execution.cache_enabled && !result.cached && result.passed {
        save_cache(&ctx.cache_dir, test_file, &cache_key, &result);
    }

    result
}
