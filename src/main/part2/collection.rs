//! Test collection and discovery with caching

use serde_json::json;
use std::fs;
use std::path::PathBuf;

use walkdir::WalkDir;

use super::super::part1::{
    get_collected_tests_cache_path, get_test_cache_dir, get_test_files_hash, RuntimePythonEnv,
};
use super::super::pytest_compat::{apply_python_encoding_env, run_pytest_collect};
use super::temp::temp_json_path;

/// Resolve a test path relative to the environment's working directory
pub(crate) fn resolve_test_path(env: &RuntimePythonEnv, path: &str) -> PathBuf {
    let p = PathBuf::from(path);
    if p.is_absolute() {
        p
    } else {
        env.cwd.join(p)
    }
}

/// Run Python test collector subprocess
fn run_python_collector(
    paths: &[String],
    env: &RuntimePythonEnv,
) -> Result<Vec<serde_json::Value>, String> {
    if env.compat {
        return run_pytest_collect(paths, env, 0);
    }

    let mut cmd = std::process::Command::new(&env.interpreter);
    cmd.current_dir(&env.cwd);
    cmd.arg("-m");
    cmd.arg(&env.module);
    let subcmd = match env.execution_mode {
        super::super::ExecutionMode::Native => "collect",
        super::super::ExecutionMode::Pytest => "collect",
        super::super::ExecutionMode::Unittest => "unittest-collect",
        super::super::ExecutionMode::Behave => "behave-collect",
    };
    cmd.arg(subcmd);
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

/// Get or collect tests, using cache when valid
pub(crate) fn get_or_collect_tests(
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
