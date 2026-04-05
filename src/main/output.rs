use indicatif::{ProgressBar, ProgressStyle};
use serde_json::json;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;
use turboplex::utils::fs::atomic_write_json;
use turboplex::TestResult;

#[cfg(feature = "debug-logging")]
macro_rules! debug_log {
    ($($arg:tt)*) => {
        eprintln!($($arg)*)
    };
}

#[cfg(not(feature = "debug-logging"))]
macro_rules! debug_log {
    ($($arg:tt)*) => {};
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum OutputMode {
    Quiet,
    Verbose,
    Json,
}

#[derive(Clone)]
pub(crate) struct OutputOptions {
    pub(crate) mode: OutputMode,
    pub(crate) out_json: Option<PathBuf>,
}

impl OutputOptions {
    pub(crate) fn wants_json(&self) -> bool {
        matches!(self.mode, OutputMode::Json)
    }
}

pub(crate) enum TestEvent {
    Finished { path: PathBuf, result: TestResult },
}

pub(crate) fn emit_error(opts: &OutputOptions, cwd: &Path, message: &str) -> Result<(), String> {
    let payload = json!({
        "schemaVersion": "tpx.cli.run.v1",
        "ok": false,
        "summary": {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "duration_ms": 0,
        },
        "error": {
            "message": message,
        },
        "artifacts": {
            "report": serde_json::Value::Null,
        },
        "data": {
            "cwd": cwd.to_string_lossy().replace('\\', "/"),
            "results": [],
        }
    });

    if let Some(out_path) = opts.out_json.as_ref() {
        write_json_file(out_path, &payload)?;
    }

    if opts.wants_json() {
        let s = serde_json::to_string(&payload).map_err(|e| e.to_string())?;
        println!("{}", s);
    }
    Ok(())
}

pub(crate) struct OutputState {
    opts: OutputOptions,
    start: Instant,
    pb: Option<ProgressBar>,
    results: Vec<(PathBuf, TestResult)>,
}

impl OutputState {
    pub(crate) fn new(opts: OutputOptions, total: usize) -> Self {
        let pb = if opts.wants_json() {
            None
        } else {
            Some(progress_bar(total))
        };

        Self {
            opts,
            start: Instant::now(),
            pb,
            results: Vec::with_capacity(total),
        }
    }

    pub(crate) fn push(&mut self, event: TestEvent) {
        match event {
            TestEvent::Finished { path, mut result } => {
                if result.cached && result.duration_ms > 0 {
                    if result.duration_ms > 5 {
                        debug_log!(
                            "Cache integrity warning: cached duration_ms={}ms for {}",
                            result.duration_ms,
                            result.test_name
                        );
                    }
                    result.duration_ms = 0;
                }
                if let Some(pb) = &self.pb {
                    pb.inc(1);
                    match self.opts.mode {
                        OutputMode::Verbose => pb.println(format_result_line(&path, &result)),
                        OutputMode::Quiet => {
                            if !result.passed {
                                pb.println(format_result_line(&path, &result));
                            }
                        }
                        OutputMode::Json => {}
                    }
                }
                self.results.push((path, result));
            }
        }
    }

    pub(crate) fn finalize(
        mut self,
        cwd: &Path,
        report_path: Option<&Path>,
        jsonl_path: Option<&Path>, // NUEVO: path para turboplex_full_report.json
    ) -> Result<(usize, usize), String> {
        let duration_ms = self.start.elapsed().as_millis() as u64;
        self.results.sort_by(|a, b| {
            a.0.cmp(&b.0)
                .then_with(|| a.1.test_name.cmp(&b.1.test_name))
        });

        let skipped = self.results.iter().filter(|(_, r)| is_skipped(r)).count();
        let failed = self.results.iter().filter(|(_, r)| !r.passed).count();
        let passed = self
            .results
            .iter()
            .filter(|(_, r)| r.passed && !is_skipped(r))
            .count();

        // NUEVO: Generar reporte JSONL si se proporciona path
        if let Some(jsonl_path) = jsonl_path {
            let _ = write_jsonl_report(&self.results, jsonl_path);
        }

        let payload = build_run_payload(cwd, &self.results, duration_ms, report_path);
        if let Some(out_path) = self.opts.out_json.as_ref() {
            write_json_file(out_path, &payload)?;
        }

        if self.opts.wants_json() {
            let s = serde_json::to_string(&payload).map_err(|e| e.to_string())?;
            println!("{}", s);
            return Ok((passed, failed));
        }

        if let Some(pb) = self.pb.take() {
            pb.finish_and_clear();
        }

        if skipped > 0 {
            println!(
                "Results: {} passed, {} failed, {} skipped ({}ms)",
                passed, failed, skipped, duration_ms
            );
        } else {
            println!(
                "Results: {} passed, {} failed ({}ms)",
                passed, failed, duration_ms
            );
        }
        Ok((passed, failed))
    }

    pub(crate) fn results(&self) -> &[(PathBuf, TestResult)] {
        &self.results
    }
}

fn progress_bar(total: usize) -> ProgressBar {
    let pb = ProgressBar::new(total as u64);
    let style = ProgressStyle::with_template("{bar:40.cyan/blue} {pos}/{len} {msg}")
        .unwrap_or_else(|_| ProgressStyle::default_bar());
    pb.set_style(style);
    pb.set_message("running");
    pb
}

fn format_label(path: &Path, test_name: &str) -> String {
    let file = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("unknown");
    format!("{} :: {}", file, test_name)
}

fn format_result_line(path: &Path, result: &TestResult) -> String {
    let label = format_label(path, &result.test_name);
    if is_skipped(result) {
        let reason = skip_reason(result).unwrap_or_else(|| "skipped".to_string());
        format!("SKIP {} ({}ms) {}", label, result.duration_ms, reason)
    } else if result.passed {
        format!("PASS {} ({}ms)", label, result.duration_ms)
    } else {
        let err = result.error.as_deref().unwrap_or("failed");
        let first = err.lines().next().unwrap_or(err).trim();
        let short = if first.len() > 240 {
            format!("{}...", &first[..240])
        } else {
            first.to_string()
        };
        format!("FAIL {} ({}ms) {}", label, result.duration_ms, short)
    }
}

fn is_skipped(result: &TestResult) -> bool {
    result
        .enriched_data
        .as_ref()
        .and_then(|v| v.get("skipped"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}

fn skip_reason(result: &TestResult) -> Option<String> {
    result
        .enriched_data
        .as_ref()
        .and_then(|v| v.get("skip_reason"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
}

fn fixture_source(result: &TestResult) -> Option<String> {
    if result.cached {
        return Some("cached".to_string());
    }
    result
        .enriched_data
        .as_ref()
        .and_then(|v| v.get("fixture_source"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
}

fn os_warm(result: &TestResult) -> bool {
    if result.cached {
        return false;
    }
    result
        .enriched_data
        .as_ref()
        .and_then(|v| v.get("os_warm"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}

fn build_run_payload(
    cwd: &Path,
    results: &[(PathBuf, TestResult)],
    duration_ms: u64,
    report_path: Option<&Path>,
) -> serde_json::Value {
    let mut passed = 0usize;
    let mut failed = 0usize;
    let mut skipped = 0usize;

    // Group failed tests by error category using BTreeMap for sorted keys
    let mut failures_by_type: std::collections::BTreeMap<String, Vec<serde_json::Value>> =
        std::collections::BTreeMap::new();
    let mut results_payload: Vec<serde_json::Value> = Vec::with_capacity(results.len());

    for (p, r) in results {
        let is_sk = is_skipped(r);
        let rel = p
            .strip_prefix(cwd)
            .unwrap_or(p)
            .to_string_lossy()
            .replace('\\', "/");
        results_payload.push(json!({
            "test": r.test_name,
            "path": rel,
            "duration_ms": r.duration_ms,
            "cached": r.cached,
            "os_warm": os_warm(r),
            "passed": r.passed,
            "skipped": is_sk,
            "error": if r.passed { None } else { r.error.as_ref() },
        }));
        if is_sk {
            skipped += 1;
        } else if r.passed {
            passed += 1;
        } else {
            failed += 1;
            // Group by first line of error
            if let Some(ref err) = r.error {
                let category = err.lines().next().unwrap_or("Unknown error").to_string();
                let error_entry = json!({
                    "test": r.test_name,
                    "path": rel,
                    "duration_ms": r.duration_ms,
                    "cached": r.cached,
                    "os_warm": os_warm(r),
                });
                failures_by_type
                    .entry(category)
                    .or_default()
                    .push(error_entry);
            }
        }
    }

    let report_rel = report_path.map(|p| {
        p.strip_prefix(cwd)
            .unwrap_or(p)
            .to_string_lossy()
            .replace('\\', "/")
    });

    json!({
        "schemaVersion": "tpx.cli.run.v1",
        "ok": failed == 0,
        "summary": {
            "total": results.len(),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "duration_ms": duration_ms,
        },
        "data": {
            "cwd": cwd.to_string_lossy().replace('\\', "/"),
            "results": results_payload,
        },
        "artifacts": {
            "report": report_rel,
        },
        "failures_by_type": failures_by_type,
    })
}

fn write_jsonl_report(results: &[(PathBuf, TestResult)], report_path: &Path) -> Result<(), String> {
    // Ensure parent directory exists
    if let Some(parent) = report_path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).map_err(|e| format!("Cannot create directory: {}", e))?;
        }
    }

    // Build content first, then write atomically
    let mut lines: Vec<String> = Vec::new();
    for (path, result) in results {
        let line = json!({
            "test_name": result.test_name,
            "path": path.to_string_lossy(),
            "passed": result.passed,
            "duration_ms": result.duration_ms,
            "cached": result.cached,
            "os_warm": os_warm(result),
            "fixture_source": fixture_source(result),
            // Si falló, incluir el error_context completo del Python runner
            "error_context": if result.passed { None } else { result.enriched_data.as_ref().and_then(|d| d.get("error_context")) },
            "test_info": result.enriched_data.as_ref().and_then(|d| d.get("test_info")),
            "fixtures_used": result.enriched_data.as_ref().and_then(|d| d.get("fixtures_used")),
        });
        lines.push(serde_json::to_string(&line).map_err(|e| e.to_string())?);
    }

    let content = lines.join("\n");
    atomic_write_json(report_path, &content).map_err(|e| format!("Write error: {}", e))?;

    Ok(())
}

fn write_json_file(path: &Path, payload: &serde_json::Value) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
    }
    let text = serde_json::to_string_pretty(payload).map_err(|e| e.to_string())?;
    atomic_write_json(path, &text).map_err(|e| e.to_string())
}
