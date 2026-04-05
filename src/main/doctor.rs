//! TurboPlex Doctor - Diagnostic tool for project health check
//!
//! The doctor analyzes the project and provides recommendations
//! without modifying any code (safe diagnostic mode).

use colored::Colorize;
use serde::Serialize;
use serde_json::json;
use std::fs;
use std::io::Read;
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;
use wait_timeout::ChildExt;
use walkdir::WalkDir;

use super::part1::{
    get_test_cache_dir, get_tplex_dir, get_tplex_failures_dir, get_tplex_reports_dir,
    RuntimePythonEnv,
};
use super::ExecutionMode;

#[derive(Clone, Copy)]
pub struct DoctorOptions {
    pub json: bool,
    pub fail_on_warn: bool,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum CheckStatus {
    Ok,
    Warn,
    Fail,
}

#[derive(Debug, Serialize, Clone)]
struct CheckResult {
    id: String,
    layer: String,
    status: CheckStatus,
    message: String,
    recommendation: Option<String>,
    details: serde_json::Value,
}

#[derive(Debug, Serialize)]
struct DoctorReport {
    doctor_version: String,
    project_root: String,
    execution_mode: String,
    checks: Vec<CheckResult>,
    summary: serde_json::Value,
}

/// Run full project diagnosis
pub fn diagnose_project(cwd: &Path, env: &RuntimePythonEnv, opts: DoctorOptions) -> i32 {
    let checks = vec![
        check_infrastructure(cwd),
        check_performance(cwd),
        check_compatibility(cwd),
        check_integrity(cwd),
        check_python_runtime(env),
        check_db_connectivity(),
    ];

    let failed = checks
        .iter()
        .filter(|c| c.status == CheckStatus::Fail)
        .count();
    let warned = checks
        .iter()
        .filter(|c| c.status == CheckStatus::Warn)
        .count();

    let summary = json!({
        "failed": failed,
        "warned": warned,
        "ok": failed == 0 && warned == 0,
    });

    let execution_mode = match env.execution_mode {
        ExecutionMode::Native => "native",
        ExecutionMode::Pytest => "pytest",
        ExecutionMode::Unittest => "unittest",
        ExecutionMode::Behave => "behave",
    };

    let report = DoctorReport {
        doctor_version: env!("CARGO_PKG_VERSION").to_string(),
        project_root: cwd.to_string_lossy().to_string(),
        execution_mode: execution_mode.to_string(),
        checks,
        summary: summary.clone(),
    };

    if opts.json {
        println!(
            "{}",
            serde_json::to_string_pretty(&report).unwrap_or_else(|_| "{}".to_string())
        );
    } else {
        print_human(&report);
    }

    if failed > 0 {
        return 1;
    }
    if opts.fail_on_warn && warned > 0 {
        return 1;
    }
    0
}

/// Check infrastructure layer (.tplex/ directory)
fn check_infrastructure(cwd: &Path) -> CheckResult {
    let tplex_dir = get_tplex_dir(cwd);
    if !tplex_dir.exists() {
        return CheckResult {
            id: "infrastructure.tplex_dir".to_string(),
            layer: "infrastructure".to_string(),
            status: CheckStatus::Ok,
            message: ".tplex/ not present yet (will be created on first run)".to_string(),
            recommendation: None,
            details: json!({"path": tplex_dir.to_string_lossy()}),
        };
    }

    let test_file = tplex_dir.join(".doctor_test");
    match fs::write(&test_file, "test") {
        Ok(()) => {
            let _ = fs::remove_file(&test_file);
            CheckResult {
                id: "infrastructure.tplex_dir".to_string(),
                layer: "infrastructure".to_string(),
                status: CheckStatus::Ok,
                message: ".tplex/ directory is writable".to_string(),
                recommendation: None,
                details: json!({"path": tplex_dir.to_string_lossy()}),
            }
        }
        Err(e) => CheckResult {
            id: "infrastructure.tplex_dir".to_string(),
            layer: "infrastructure".to_string(),
            status: CheckStatus::Fail,
            message: ".tplex/ directory is not writable".to_string(),
            recommendation: Some(
                "Fix filesystem permissions for .tplex/ (write/rename)".to_string(),
            ),
            details: json!({"path": tplex_dir.to_string_lossy(), "error": e.to_string()}),
        },
    }
}

/// Check performance layer (conftest.py size, load time)
fn check_performance(cwd: &Path) -> CheckResult {
    let mut heavy = Vec::new();

    // Check conftest.py files for size
    for entry in WalkDir::new(cwd).max_depth(3).into_iter().flatten() {
        let path = entry.path();
        if path.file_name() == Some(std::ffi::OsStr::new("conftest.py")) {
            if let Ok(metadata) = fs::metadata(path) {
                let size_kb = metadata.len() / 1024;
                if size_kb > 50 {
                    heavy.push(json!({"path": path.to_string_lossy(), "size_kb": size_kb}));
                }
            }
        }
    }

    if heavy.is_empty() {
        return CheckResult {
            id: "performance.conftest_size".to_string(),
            layer: "performance".to_string(),
            status: CheckStatus::Ok,
            message: "No heavy conftest.py detected".to_string(),
            recommendation: None,
            details: json!({}),
        };
    }

    CheckResult {
        id: "performance.conftest_size".to_string(),
        layer: "performance".to_string(),
        status: CheckStatus::Warn,
        message: "Detected heavy conftest.py (may slow discovery/import)".to_string(),
        recommendation: Some(
            "Move heavy imports inside fixtures or split conftest into smaller modules".to_string(),
        ),
        details: json!({"heavy_conftest": heavy}),
    }
}

/// Check compatibility layer (analyze recent reports for fixture issues)
fn check_compatibility(cwd: &Path) -> CheckResult {
    let reports_dir = get_tplex_reports_dir(cwd);
    if !reports_dir.exists() {
        return CheckResult {
            id: "compatibility.reports".to_string(),
            layer: "compatibility".to_string(),
            status: CheckStatus::Ok,
            message: "No reports found yet".to_string(),
            recommendation: None,
            details: json!({"reports_dir": reports_dir.to_string_lossy()}),
        };
    }

    let mut report_files: Vec<PathBuf> = Vec::new();
    if let Ok(entries) = fs::read_dir(&reports_dir) {
        for entry in entries.filter_map(|e| e.ok()) {
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if name_str.starts_with("report_") && name_str.ends_with(".json") {
                report_files.push(entry.path());
            }
        }
    }

    report_files.sort_by(|a, b| {
        let meta_a = fs::metadata(a).ok();
        let meta_b = fs::metadata(b).ok();
        match (meta_a, meta_b) {
            (Some(ma), Some(mb)) => {
                let time_a = ma.modified().ok();
                let time_b = mb.modified().ok();
                match (time_a, time_b) {
                    (Some(ta), Some(tb)) => tb.cmp(&ta),
                    _ => std::cmp::Ordering::Equal,
                }
            }
            _ => std::cmp::Ordering::Equal,
        }
    });

    let recent_reports: Vec<_> = report_files.into_iter().take(5).collect();
    if recent_reports.is_empty() {
        return CheckResult {
            id: "compatibility.reports".to_string(),
            layer: "compatibility".to_string(),
            status: CheckStatus::Ok,
            message: "No reports found yet".to_string(),
            recommendation: None,
            details: json!({"reports_dir": reports_dir.to_string_lossy()}),
        };
    }

    let mut fixture_like = 0usize;
    let mut schema_blocked = 0usize;
    let mut health_failed = 0usize;
    let mut parse_fail = 0usize;

    for rp in &recent_reports {
        match fs::read_to_string(rp) {
            Ok(content) => match serde_json::from_str::<serde_json::Value>(&content) {
                Ok(v) => {
                    if let Some(code) = v.pointer("/data/error/code").and_then(|x| x.as_str()) {
                        if code == "SCHEMA_SYNC_BLOCKED" {
                            schema_blocked += 1;
                        }
                        if code == "HEALTH_CHECK_FAILED" {
                            health_failed += 1;
                        }
                    }
                    if let Some(results) = v.pointer("/data/results").and_then(|x| x.as_array()) {
                        for r in results {
                            if let Some(code) = r.pointer("/db_error/code").and_then(|x| x.as_str())
                            {
                                if code == "db_dirty_state" {
                                    health_failed += 1;
                                }
                            }
                            if let Some(err) = r.get("error") {
                                if let Some(s) = err.as_str() {
                                    let lower = s.to_lowercase();
                                    if lower.contains("fixture")
                                        && (lower.contains("not found") || lower.contains("error"))
                                    {
                                        fixture_like += 1;
                                    }
                                }
                            }
                        }
                    }
                }
                Err(_) => parse_fail += 1,
            },
            Err(_) => parse_fail += 1,
        }
    }

    let details = json!({
        "recent_reports": recent_reports.iter().map(|p| p.to_string_lossy().to_string()).collect::<Vec<_>>(),
        "fixture_like_failures": fixture_like,
        "schema_sync_blocked": schema_blocked,
        "health_check_failed": health_failed,
        "parse_failures": parse_fail,
    });

    if schema_blocked > 0 {
        return CheckResult {
            id: "compatibility.reports".to_string(),
            layer: "compatibility".to_string(),
            status: CheckStatus::Fail,
            message: "Schema Sync Guard blocked recent runs (SCHEMA_SYNC_BLOCKED)".to_string(),
            recommendation: Some(
                "Run Alembic migrations (alembic upgrade head) and re-run".to_string(),
            ),
            details,
        };
    }

    if fixture_like >= 5 {
        return CheckResult {
            id: "compatibility.reports".to_string(),
            layer: "compatibility".to_string(),
            status: CheckStatus::Warn,
            message: "Frequent fixture-related failures detected in recent reports".to_string(),
            recommendation: Some(
                "If you rely on pytest plugins/fixtures, try: tpx --compat --path tests/"
                    .to_string(),
            ),
            details,
        };
    }

    if parse_fail > 0 {
        return CheckResult {
            id: "compatibility.reports".to_string(),
            layer: "compatibility".to_string(),
            status: CheckStatus::Warn,
            message: "Some recent reports could not be parsed".to_string(),
            recommendation: Some(
                "Check for truncated/corrupt report files under .tplex/reports/".to_string(),
            ),
            details,
        };
    }

    CheckResult {
        id: "compatibility.reports".to_string(),
        layer: "compatibility".to_string(),
        status: CheckStatus::Ok,
        message: "No compatibility issues detected in recent reports".to_string(),
        recommendation: None,
        details,
    }
}

/// Check integrity layer (atomic write verification)
fn check_integrity(cwd: &Path) -> CheckResult {
    let reports_dir = get_tplex_reports_dir(cwd);
    let cache_dir = get_test_cache_dir(cwd);
    let failures_dir = get_tplex_failures_dir(cwd);
    let logs_dir = get_tplex_dir(cwd).join("logs");

    let mut tmp = Vec::new();
    for d in [&reports_dir, &cache_dir, &failures_dir, &logs_dir] {
        if !d.exists() {
            continue;
        }
        if let Ok(entries) = fs::read_dir(d) {
            for entry in entries.filter_map(|e| e.ok()) {
                let name = entry.file_name();
                let name_str = name.to_string_lossy();
                if name_str.ends_with(".tmp") {
                    tmp.push(entry.path().to_string_lossy().to_string());
                }
            }
        }
    }

    if !tmp.is_empty() {
        return CheckResult {
            id: "integrity.tmp_files".to_string(),
            layer: "integrity".to_string(),
            status: CheckStatus::Fail,
            message: "Found incomplete atomic write operations (.tmp files)".to_string(),
            recommendation: Some(
                "Delete stale .tmp files if no run is active, then re-run doctor".to_string(),
            ),
            details: json!({"tmp_files": tmp}),
        };
    }

    let latest = reports_dir.join("report_latest.json");
    if reports_dir.exists() && !latest.exists() {
        return CheckResult {
            id: "integrity.latest_report".to_string(),
            layer: "integrity".to_string(),
            status: CheckStatus::Warn,
            message: "Missing report_latest.json (history exists but latest link missing)"
                .to_string(),
            recommendation: Some(
                "Run a test suite once to regenerate report_latest.json".to_string(),
            ),
            details: json!({"reports_dir": reports_dir.to_string_lossy(), "expected": latest.to_string_lossy()}),
        };
    }

    CheckResult {
        id: "integrity.tmp_files".to_string(),
        layer: "integrity".to_string(),
        status: CheckStatus::Ok,
        message: "No interrupted writes detected (.tmp files)".to_string(),
        recommendation: None,
        details: json!({}),
    }
}

fn check_python_runtime(env: &RuntimePythonEnv) -> CheckResult {
    let mut required = vec![("turboplex_py", "import turboplex_py")];
    required.push(("mcp_server", "import turboplex_py.mcp.server"));

    if env.execution_mode == ExecutionMode::Behave {
        required.push(("behave", "import behave"));
    }

    let mut failures = Vec::new();
    for (name, code) in required {
        let (ok, details) = run_python_snippet(env, code, Duration::from_secs(2));
        if !ok {
            failures.push(json!({"check": name, "details": details}));
        }
    }

    if !failures.is_empty() {
        let rec = if env.execution_mode == ExecutionMode::Behave {
            Some("Install missing deps in your venv (e.g., pip install behave) and ensure turboplex is installed".to_string())
        } else {
            Some(
                "Ensure the selected Python env can import turboplex_py (pip install -e .)"
                    .to_string(),
            )
        };
        return CheckResult {
            id: "runtime.python_imports".to_string(),
            layer: "runtime".to_string(),
            status: CheckStatus::Fail,
            message: "Python runtime is not ready (import failures)".to_string(),
            recommendation: rec,
            details: json!({"interpreter": env.interpreter, "pythonpath": env.pythonpath, "failures": failures}),
        };
    }

    CheckResult {
        id: "runtime.python_imports".to_string(),
        layer: "runtime".to_string(),
        status: CheckStatus::Ok,
        message: "Python runtime imports look healthy".to_string(),
        recommendation: None,
        details: json!({"interpreter": env.interpreter, "pythonpath": env.pythonpath}),
    }
}

fn check_db_connectivity() -> CheckResult {
    let url = std::env::var("DATABASE_URL")
        .ok()
        .or_else(|| std::env::var("TEST_DATABASE_URL").ok());

    let Some(url) = url else {
        return CheckResult {
            id: "db.connectivity".to_string(),
            layer: "db".to_string(),
            status: CheckStatus::Ok,
            message: "No DATABASE_URL/TEST_DATABASE_URL set (skipping DB probe)".to_string(),
            recommendation: None,
            details: json!({}),
        };
    };

    let parsed = parse_postgres_host_port(&url);
    let Some((host, port)) = parsed else {
        return CheckResult {
            id: "db.connectivity".to_string(),
            layer: "db".to_string(),
            status: CheckStatus::Warn,
            message: "DB probe skipped (unsupported URL format)".to_string(),
            recommendation: Some("Set DATABASE_URL/TEST_DATABASE_URL in postgresql://... format for connectivity checks".to_string()),
            details: json!({"url": url}),
        };
    };

    let addr: SocketAddr = match format!("{}:{}", host, port).parse() {
        Ok(a) => a,
        Err(e) => {
            return CheckResult {
                id: "db.connectivity".to_string(),
                layer: "db".to_string(),
                status: CheckStatus::Warn,
                message: "DB probe skipped (invalid host/port)".to_string(),
                recommendation: None,
                details: json!({"url": url, "host": host, "port": port, "error": e.to_string()}),
            }
        }
    };

    match TcpStream::connect_timeout(&addr, Duration::from_secs(1)) {
        Ok(_) => CheckResult {
            id: "db.connectivity".to_string(),
            layer: "db".to_string(),
            status: CheckStatus::Ok,
            message: "DB TCP connectivity OK".to_string(),
            recommendation: None,
            details: json!({"host": host, "port": port}),
        },
        Err(e) => CheckResult {
            id: "db.connectivity".to_string(),
            layer: "db".to_string(),
            status: CheckStatus::Warn,
            message: "DB not reachable within 1s (TCP probe)".to_string(),
            recommendation: Some(
                "Check DB host/port routing, VPN, firewall, or start the DB service".to_string(),
            ),
            details: json!({"host": host, "port": port, "error": e.to_string()}),
        },
    }
}

fn parse_postgres_host_port(url: &str) -> Option<(String, u16)> {
    let url = url.trim();
    if !(url.starts_with("postgresql://") || url.starts_with("postgresql+")) {
        return None;
    }
    let pos = url.find("://")?;
    let rest = &url[pos + 3..];
    let host_part = if let Some(at) = rest.rfind('@') {
        &rest[at + 1..]
    } else {
        rest
    };
    let host_part = host_part.split('/').next().unwrap_or(host_part);
    let mut it = host_part.split(':');
    let host = it.next()?.to_string();
    let port = it
        .next()
        .and_then(|p| p.parse::<u16>().ok())
        .unwrap_or(5432);
    Some((host, port))
}

fn run_python_snippet(
    env: &RuntimePythonEnv,
    code: &str,
    timeout: Duration,
) -> (bool, serde_json::Value) {
    let mut cmd = Command::new(&env.interpreter);
    cmd.current_dir(&env.cwd);
    cmd.arg("-c").arg(code);
    cmd.stdin(Stdio::null());
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
    if let Some(pp) = env.pythonpath.as_ref() {
        cmd.env("PYTHONPATH", pp);
    }

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return (false, json!({"spawn_error": e.to_string()})),
    };

    match child.wait_timeout(timeout).ok().flatten() {
        Some(status) => {
            let mut out = String::new();
            let mut err = String::new();
            if let Some(mut o) = child.stdout.take() {
                let _ = o.read_to_string(&mut out);
            }
            if let Some(mut e) = child.stderr.take() {
                let _ = e.read_to_string(&mut err);
            }
            (
                status.success(),
                json!({"exit_code": status.code(), "stdout": truncate(&out, 2000), "stderr": truncate(&err, 2000)}),
            )
        }
        None => {
            let _ = child.kill();
            let _ = child.wait();
            (false, json!({"timeout_ms": timeout.as_millis()}))
        }
    }
}

fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max {
        return s.to_string();
    }
    let mut out = s[..max].to_string();
    out.push_str("...(truncated)");
    out
}

fn print_human(report: &DoctorReport) {
    println!(
        "\n{}",
        format!("🏥 TURBOPLEX DOCTOR - v{}", report.doctor_version)
            .cyan()
            .bold()
    );
    println!("{}", "═".repeat(60).cyan());
    println!(
        "{} {}",
        "Project:".bold(),
        report.project_root.to_string().dimmed()
    );
    println!(
        "{} {}",
        "Mode:".bold(),
        report.execution_mode.to_string().dimmed()
    );

    let mut by_layer: std::collections::BTreeMap<&str, Vec<&CheckResult>> =
        std::collections::BTreeMap::new();
    for c in &report.checks {
        by_layer.entry(&c.layer).or_default().push(c);
    }

    for (layer, items) in by_layer {
        println!("\n{}", format!("🔍 Layer: {}", layer).bold());
        for c in items {
            let tag = match c.status {
                CheckStatus::Ok => "[OK]".green(),
                CheckStatus::Warn => "[!]".yellow(),
                CheckStatus::Fail => "[X]".red(),
            };
            println!("   {} {}", tag, c.message);
            if let Some(rec) = &c.recommendation {
                println!("      {} {}", "→".dimmed(), rec);
            }
        }
    }

    println!("\n{}", "═".repeat(60).cyan());
    println!("{}", "📋 DIAGNOSIS SUMMARY".bold());
    let failed = report
        .summary
        .get("failed")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let warned = report
        .summary
        .get("warned")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    if failed == 0 && warned == 0 {
        println!("\n   {} Your project is healthy!", "✅".green());
    } else {
        if failed > 0 {
            println!("\n   {} Failures: {}", "⛔".red(), failed);
        }
        if warned > 0 {
            println!("\n   {} Warnings: {}", "⚠️".yellow(), warned);
        }
    }
    println!();
}
