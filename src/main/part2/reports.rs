//! Failure report generation and management

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use turboplex::utils::fs::atomic_write_json;
use turboplex::TestResult;

use super::super::part1::{get_tplex_failures_dir, get_tplex_reports_dir};

/// Extract the error category (first line of error message)
fn extract_error_category(error: &str) -> String {
    error.lines().next().unwrap_or("Unknown error").to_string()
}

/// Generate a markdown failure report with error categorization
/// Saves to .tplex/failures/
pub(crate) fn generate_failure_report(
    results: &[(PathBuf, TestResult)],
    _paths: &[String],
    cwd: &Path,
) -> Option<String> {
    let timestamp = chrono::Local::now().format("%Y%m%d_%H%M%S");
    let failures_dir = get_tplex_failures_dir(cwd);
    let _ = fs::create_dir_all(&failures_dir);
    let report_file = failures_dir.join(format!("failures_{}.md", timestamp));

    let mut content = String::new();
    content.push_str("# Failure Report\n\n");
    content.push_str(&format!("Generated: {}\n\n", chrono::Local::now()));

    let failed: Vec<_> = results.iter().filter(|(_, r)| !r.passed).collect();

    if failed.is_empty() {
        content.push_str("No failures to report!\n");
    } else {
        // Group failures by error category
        let mut categories: HashMap<String, Vec<(&PathBuf, &TestResult)>> = HashMap::new();

        for (path, result) in &failed {
            let category = result
                .error
                .as_ref()
                .map(|e| extract_error_category(e))
                .unwrap_or_else(|| "Unknown error".to_string());

            categories.entry(category).or_default().push((path, result));
        }

        // Summary section
        content.push_str("## Summary\n\n");
        content.push_str(&format!("- **Total Failed Tests**: {}\n", failed.len()));
        content.push_str(&format!("- **Error Categories**: {}\n\n", categories.len()));

        // Categories sorted by count (most frequent first)
        let mut sorted_categories: Vec<_> = categories.iter().collect();
        sorted_categories.sort_by(|a, b| b.1.len().cmp(&a.1.len()));

        content.push_str("## Error Categories\n\n");

        for (idx, (category, tests)) in sorted_categories.iter().enumerate() {
            content.push_str(&format!(
                "### {}. {} ({} test{})\n\n",
                idx + 1,
                category,
                tests.len(),
                if tests.len() == 1 { "" } else { "s" }
            ));

            // List all affected files
            for (path, result) in *tests {
                content.push_str(&format!(
                    "- `{}` - `{}`\n",
                    path.display(),
                    result.test_name
                ));
            }

            content.push('\n');
        }

        // Detailed failures section (optional - for full error messages)
        content.push_str("---\n\n");
        content.push_str("## Detailed Error Messages\n\n");
        content.push_str("*Expand sections above for individual test details*\n");
    }

    if atomic_write_json(&report_file, &content).is_ok() {
        Some(report_file.to_string_lossy().to_string())
    } else {
        None
    }
}

/// Update the latest_failures.md symlink/link in .tplex/failures/
pub(crate) fn update_latest_report_link(report_path: &str, cwd: &Path) {
    let failures_dir = get_tplex_failures_dir(cwd);
    let latest_link = failures_dir.join("latest_failures.md");
    let _ = std::fs::remove_file(&latest_link);
    #[cfg(unix)]
    let _ = std::os::unix::fs::symlink(report_path, &latest_link);
    #[cfg(windows)]
    let _ = std::fs::write(&latest_link, format!("See: {}", report_path));
}

/// Clean up old reports in .tplex/ subdirectories, keeping only the most recent N
pub(crate) fn cleanup_old_reports(cwd: &Path, keep_count: usize) {
    // Clean up old failure reports (.md) in .tplex/failures/
    let failures_dir = get_tplex_failures_dir(cwd);
    let mut md_reports: Vec<_> = std::fs::read_dir(&failures_dir)
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

    md_reports.sort_by(|a, b| b.0.cmp(&a.0));

    for (_, path) in md_reports.iter().skip(keep_count) {
        let _ = std::fs::remove_file(path);
    }

    // Clean up old JSON reports (report_*.json) in .tplex/reports/
    let reports_dir = get_tplex_reports_dir(cwd);
    let mut json_reports: Vec<_> = std::fs::read_dir(&reports_dir)
        .ok()
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .filter(|e| {
            let name = e.file_name();
            let name_str = name.to_string_lossy();
            name_str.starts_with("report_") && name_str.ends_with(".json")
        })
        .map(|e| (e.metadata().ok().and_then(|m| m.modified().ok()), e.path()))
        .collect();

    json_reports.sort_by(|a, b| b.0.cmp(&a.0));

    for (_, path) in json_reports.iter().skip(keep_count) {
        let _ = std::fs::remove_file(path);
    }
}
