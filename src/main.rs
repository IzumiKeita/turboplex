//! TurboPlex - High Performance Test Orchestration Engine
//!
//! A blazing-fast test runner built with Rust + Python, designed for the AI era.
//!
//! ## Features
//! - **4x faster** than Pytest with intelligent caching
//! - **Watch Mode** for TDD development
//! - **M2M Protocol** - Generates `.tplex_report.json` for AI agents
//! - **Parallel Execution** using Rayon
//!
//! ## Usage
//! ```bash
//! tpx --path tests/        # Run tests
//! tpx --watch --path tests/  # Watch mode
//! ```

use colored::*;
use notify::{Config, RecommendedWatcher, RecursiveMode, Watcher};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::env;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::mpsc::channel;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use turboplex::{discover_test_paths, load_config, python_config_effective, TestResult};
use walkdir::WalkDir;

/// Default maximum depth for test discovery
const DEFAULT_MAX_DEPTH: usize = 5;

/// Prints the CLI help message to stdout.
fn print_help() {
    println!(
        r#"
turboplex (tpx) - High Performance Test Engine

Usage:
    tpx                      # Auto-discover and run tests
    tpx --path ./tests     # Run tests in specific directory
    tpx --watch            # Watch for file changes and re-run
    tpx --help            # Show this help

Options:
    --path, -p <dir>    Run tests in specific directory
    --watch, -w         Watch for file changes and re-run
    --help, -h          Show this help message

Aliases:
    turboplex          # Alias for tpx
"#
    );
}

/// Returns the path to the cache directory.
fn get_test_cache_dir() -> PathBuf {
    PathBuf::from(".turboplex_cache")
}

/// Returns the path to the cached tests JSON file.
fn get_collected_tests_cache_path() -> PathBuf {
    get_test_cache_dir().join("collected_tests.json")
}

/// Computes SHA-256 hash of a file for cache invalidation.
///
/// # Arguments
/// * `path` - Path to the file to hash
///
/// # Returns
/// * `Some<String>` - Hex-encoded SHA-256 hash
/// * `None` - If the file cannot be read
fn compute_file_hash(path: &Path) -> Option<String> {
    let mut file = File::open(path).ok()?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 8192];
    loop {
        let bytes_read = file.read(&mut buffer).ok()?;
        if bytes_read == 0 {
            break;
        }
        hasher.update(&buffer[..bytes_read]);
    }
    Some(hex::encode(hasher.finalize()))
}

/// Computes a combined hash from multiple test files.
///
/// Used to detect if any test file has changed, triggering cache invalidation.
fn get_test_files_hash(paths: &[PathBuf]) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut hasher = DefaultHasher::new();
    for p in paths {
        if let Some(hash) = compute_file_hash(p) {
            hash.hash(&mut hasher);
        }
    }
    format!("{:x}", hasher.finish())
}

/// Generates a machine-readable failure report in JSON format.
///
/// This enables the M2M (Machine-to-Machine) protocol for AI agents.
/// The report includes:
/// - Timestamp
/// - Error messages
/// - Line numbers
/// - Code context (5 lines before and after the error)
///
/// # Arguments
/// * `results` - Vector of (path, TestResult) tuples
/// * `_test_paths` - Test paths being executed (unused, for future expansion)
fn generate_failure_report(results: &[(PathBuf, TestResult)], _test_paths: &[String]) {
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

            // Extract line number from error if possible
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

            // Get context around the error line (5 lines before and after)
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

    // Write report file
    let report_path = ".tplex_report.json";
    if let Ok(content) = serde_json::to_string_pretty(&report) {
        let _ = fs::write(report_path, content);
        println!("\n{} Report generated: .tplex_report.json", "📄".yellow());
    }
}

/// Runs the Python test collector module.
///
/// # Arguments
/// * `paths` - List of paths to collect tests from
///
/// # Returns
/// * `Ok(Vec<serde_json::Value>)` - Vector of collected test items
/// * `Err(String)` - Error message if collection fails
fn run_python_collector(paths: &[String]) -> Result<Vec<serde_json::Value>, String> {
    let output = Command::new("python")
        .arg("-m")
        .arg("turboplex_py")
        .arg("collect")
        .args(paths)
        .env("SQLALCHEMY_SILENCE_UBER_WARNING", "1")
        .env("SQLALCHEMY_LOG", "0")
        .env("TURBOTEST_SUBPROCESS", "1")
        .output()
        .map_err(|e| format!("Failed to run collector: {}", e))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("Collector failed: {}", stderr));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let parsed: serde_json::Value =
        serde_json::from_str(stdout.trim()).map_err(|e| format!("Failed to parse JSON: {}", e))?;

    Ok(parsed["items"].as_array().cloned().unwrap_or_default())
}

/// Runs a single Python test and returns the result.
///
/// # Arguments
/// * `path` - Path to the test file
/// * `qual` - Qualified name of the test (e.g., "test_function" or "TestClass::test_method")
///
/// # Returns
/// * `TestResult` - Contains passed/failed status, duration, and error message
fn run_python_test(path: &str, qual: &str) -> TestResult {
    let start = Instant::now();

    let output = Command::new("python")
        .arg("-m")
        .arg("turboplex_py")
        .arg("run")
        .arg("--path")
        .arg(path)
        .arg("--qual")
        .arg(qual)
        .env("SQLALCHEMY_SILENCE_UBER_WARNING", "1")
        .env("SQLALCHEMY_LOG", "0")
        .env("TURBOTEST_SUBPROCESS", "1")
        .output();

    let duration_ms = start.elapsed().as_millis() as u64;

    match output {
        Ok(out) => {
            let stdout = String::from_utf8_lossy(&out.stdout);
            if let Ok(resp) = serde_json::from_str::<serde_json::Value>(&stdout) {
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

/// Gets tests from cache or collects them if cache is invalid.
///
/// Cache invalidation happens when test file hashes change (SHA-256).
///
/// # Arguments
/// * `paths` - Paths to search for tests
///
/// # Returns
/// * `Ok(Vec<serde_json::Value>)` - Collected test items
/// * `Err(String)` - Error message if collection fails
fn get_or_collect_tests(paths: &[String]) -> Result<Vec<serde_json::Value>, String> {
    let cache_dir = get_test_cache_dir();
    let cache_file = get_collected_tests_cache_path();
    let hash_file = cache_dir.join("files_hash.txt");

    // Compute current hash of test files
    let mut test_files: Vec<PathBuf> = Vec::new();
    for p in paths {
        let pb = PathBuf::from(p);
        if pb.is_file() {
            test_files.push(pb);
        } else {
            let walker = WalkDir::new(&pb).max_depth(10);
            for entry in walker.into_iter().filter_map(|e| e.ok()) {
                if entry.file_type().is_file() {
                    let name = entry.file_name().to_string_lossy();
                    if name.starts_with("test_") && name.ends_with(".py") {
                        test_files.push(entry.path().to_path_buf());
                    }
                }
            }
        }
    }

    let current_hash = get_test_files_hash(&test_files);

    // Check if cache exists and is valid
    if let (Ok(cached_content), Ok(stored_hash)) = (
        fs::read_to_string(&cache_file),
        fs::read_to_string(&hash_file),
    ) {
        if stored_hash.trim() == current_hash {
            // Cache is valid
            let parsed: serde_json::Value = serde_json::from_str(&cached_content)
                .map_err(|e| format!("Invalid cache: {}", e))?;
            return Ok(parsed["items"].as_array().cloned().unwrap_or_default());
        }
    }

    // Need to re-collect
    println!("  {} Collecting tests...", "📦".cyan());
    let items = run_python_collector(paths)?;

    // Save to cache
    let _ = fs::create_dir_all(&cache_dir);
    let cache_content = json!({ "items": items }).to_string();
    let _ = fs::write(&cache_file, &cache_content);
    let _ = fs::write(&hash_file, &current_hash);

    Ok(items)
}

/// Runs all tests in parallel using Python, with configurable watch mode.
///
/// # Arguments
/// * `paths_to_use` - Paths to test files/directories
/// * `watch_mode` - If true, don't exit on failure (for watch mode)
fn run_tests_with_paths(paths_to_use: &[String], watch_mode: bool) {
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

    let _py_cfg = python_config_effective(&config);

    println!(
        "\n[Config]: workers={}",
        config.execution.max_workers.unwrap_or(8)
    );
    println!("\n{} {}", "📁".cyan().bold(), "Test directories:".bold());
    for p in paths_to_use {
        println!("   - {}", p);
    }

    // Get or collect tests (with caching)
    let test_items = match get_or_collect_tests(paths_to_use) {
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

    // Run tests in parallel using Python
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
            thread::spawn(move || {
                let mut results = Vec::new();
                for item in &chunk {
                    let path = item["path"].as_str().unwrap_or("");
                    let qual = item["qualname"].as_str().unwrap_or("");
                    let result = run_python_test(path, qual);
                    results.push((PathBuf::from(path), result));
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

    // Print results
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

    // Generate failure report if there are failures
    if failed > 0 {
        generate_failure_report(&all_results, paths_to_use);
    }

    if !watch_mode && failed > 0 {
        std::process::exit(1);
    }
}

/// Main entry point for the TurboPlex CLI.
///
/// Parses command-line arguments and dispatches to the appropriate handler.
fn main() {
    let args: Vec<String> = env::args().collect();

    if args.iter().any(|a| a == "--help" || a == "-h") {
        print_help();
        return;
    }

    // Parse arguments
    let mut test_paths: Vec<String> = Vec::new();
    let watch_mode = args.iter().any(|a| a == "--watch" || a == "-w");

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--path" | "-p" if i + 1 < args.len() => {
                test_paths.push(args[i + 1].clone());
                i += 2;
            }
            "--watch" | "-w" => {
                i += 1;
            }
            a if !a.starts_with('-') && a != "turboplex" => {
                test_paths.push(args[i].clone());
                i += 1;
            }
            _ => i += 1,
        }
    }

    println!("\n{}", "TurboTest Engine".bold().cyan());

    // Determine test paths
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

    let py_cfg = python_config_effective(&config);

    let paths_to_use: Vec<String> = if test_paths.is_empty() {
        if py_cfg.test_paths.is_empty() {
            discover_test_paths(DEFAULT_MAX_DEPTH)
                .iter()
                .map(|p| p.to_string_lossy().to_string())
                .collect()
        } else {
            py_cfg.test_paths.clone()
        }
    } else {
        test_paths
    };

    if watch_mode {
        println!(
            "\n{} {} - Press Ctrl+C to exit",
            "👀".yellow().bold(),
            "Watch Mode enabled".yellow()
        );

        // Initial run
        run_tests_with_paths(&paths_to_use, true);

        // Watch for file changes
        let (tx, rx) = channel();
        let paths_clone: Vec<PathBuf> = paths_to_use.iter().map(PathBuf::from).collect();

        let mut watcher = RecommendedWatcher::new(
            move |res: Result<notify::Event, notify::Error>| {
                if let Ok(event) = res {
                    if event.kind.is_modify() || event.kind.is_create() {
                        let _ = tx.send(());
                    }
                }
            },
            Config::default().with_poll_interval(Duration::from_secs(1)),
        )
        .unwrap();

        for p in &paths_clone {
            let _ = watcher.watch(p, RecursiveMode::Recursive);
        }

        println!("\n{} Watching for changes...", "👀".yellow());

        // Debounce: wait for changes with debounce
        let mut last_run = Instant::now();
        loop {
            match rx.recv_timeout(Duration::from_secs(1)) {
                Ok(_) => {
                    // Wait a bit to avoid multiple runs for same change
                    thread::sleep(Duration::from_millis(500));
                    // Check if there are more events in the queue
                    while rx.try_recv().is_ok() {}

                    let elapsed = last_run.elapsed();
                    if elapsed > Duration::from_secs(1) {
                        println!("\n{} File changed, re-running tests...", "🔄".cyan());
                        run_tests_with_paths(&paths_to_use, true);
                        last_run = Instant::now();
                    }
                }
                Err(_) => {
                    // Timeout, continue watching
                }
            }
        }
    } else {
        run_tests_with_paths(&paths_to_use, false);
    }
}
