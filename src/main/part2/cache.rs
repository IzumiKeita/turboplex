//! Test result caching utilities

use serde_json::json;
use std::fs;

use turboplex::utils::fs::atomic_write_json;
use turboplex::TestResult;

use super::super::part1::{get_test_results_cache_dir, RuntimePythonEnv};

/// Load a cached test result if it exists, was successful, and fingerprint matches
pub(crate) fn load_cached_pass_result(
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
    // Verify fingerprint to invalidate on DB/env changes
    let cached_fingerprint = parsed.get("fingerprint").and_then(|v| v.as_str());
    if cached_fingerprint != Some(&env.fingerprint) {
        return None;
    }
    Some(TestResult {
        test_name: test_name.to_string(),
        passed: true,
        cached: true,
        duration_ms: 0,
        error: None,
        enriched_data: Some(json!({"fixture_source": "cached", "os_warm": false})),
    })
}

/// Save a successful test result to cache with fingerprint for invalidation
/// Uses atomic write (temp file + rename) to prevent race condition corruption
pub(crate) fn save_cached_pass_result(env: &RuntimePythonEnv, cache_key: &str) {
    let dir = get_test_results_cache_dir(&env.cwd);
    let _ = fs::create_dir_all(&dir);
    let path = dir.join(format!("{}.json", cache_key));
    let payload = json!({ "passed": true, "fingerprint": env.fingerprint });
    if let Ok(text) = serde_json::to_string(&payload) {
        let _ = atomic_write_json(&path, &text);
    }
}

/// Check if a test result indicates a skipped test
pub(crate) fn is_skipped_result(result: &TestResult) -> bool {
    result
        .enriched_data
        .as_ref()
        .and_then(|v| v.get("skipped"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}
