use std::collections::HashMap;
use std::fs::File;
use std::io::Write;
use std::path::Path;

use crate::TestResult;

/// Generate JUnit XML report from test results
pub fn generate_junit_xml(
    results: &[(String, TestResult)],
    suite_name: &str,
    output_path: &Path,
) -> Result<(), std::io::Error> {
    let mut xml = String::new();

    // Count stats
    let total = results.len();
    let failures = results.iter().filter(|(_, r)| !r.passed).count();
    let errors = 0usize;
    let skipped = 0usize;

    // Calculate total time
    let total_time_sec: f64 = results
        .iter()
        .map(|(_, r)| r.duration_ms as f64 / 1000.0)
        .sum();

    // XML header
    xml.push_str(r#"<?xml version="1.0" encoding="UTF-8"?>"#);
    xml.push('\n');
    xml.push_str(&format!(
        r#"<testsuites name="{}" tests="{}" failures="{}" errors="{}" skipped="{}" time="{:.3}">"#,
        escape_xml(suite_name),
        total,
        failures,
        errors,
        skipped,
        total_time_sec
    ));
    xml.push('\n');

    // Group by file path for testcases
    let mut by_file: HashMap<String, Vec<(String, TestResult)>> = HashMap::new();
    for (path, result) in results.iter() {
        by_file
            .entry(path.clone())
            .or_default()
            .push((path.clone(), result.clone()));
    }

    // Generate testsuite for each file
    for (file_path, tests) in by_file.iter() {
        let file_total = tests.len();
        let file_failures = tests.iter().filter(|(_, r)| !r.passed).count();
        let file_time: f64 = tests
            .iter()
            .map(|(_, r)| r.duration_ms as f64 / 1000.0)
            .sum();

        xml.push_str(&format!(
            r#"  <testsuite name="{}" tests="{}" failures="{}" errors="0" skipped="0" time="{:.3}">"#,
            escape_xml(file_path),
            file_total,
            file_failures,
            file_time
        ));
        xml.push('\n');

        // Test cases
        for (test_path, result) in tests.iter() {
            let test_name = extract_test_name(test_path);
            let time_sec = result.duration_ms as f64 / 1000.0;

            xml.push_str(&format!(
                r#"    <testcase name="{}" classname="{}" time="{:.3}">"#,
                escape_xml(&test_name),
                escape_xml(file_path),
                time_sec
            ));

            if !result.passed {
                if let Some(ref error) = result.error {
                    xml.push('\n');
                    xml.push_str(&format!(
                        r#"      <failure message="{}" type="AssertionError"><![CDATA[{}]]></failure>"#,
                        escape_xml(&truncate(error, 100)),
                        error
                    ));
                    xml.push('\n');
                    xml.push_str("    </testcase>");
                } else {
                    xml.push('>');
                    xml.push('\n');
                    xml.push_str(r#"      <failure message="Test failed" type="AssertionError"/>"#);
                    xml.push('\n');
                    xml.push_str("    </testcase>");
                }
            } else {
                xml.push_str("/>");
            }
            xml.push('\n');
        }

        xml.push_str("  </testsuite>");
        xml.push('\n');
    }

    xml.push_str("</testsuites>");
    xml.push('\n');

    // Write to file
    let mut file = File::create(output_path)?;
    file.write_all(xml.as_bytes())?;

    Ok(())
}

/// Escape XML special characters
fn escape_xml(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

/// Extract test name from full path (e.g., "tests/test_foo.py::test_bar" -> "test_bar")
fn extract_test_name(path: &str) -> String {
    if let Some(pos) = path.find("::") {
        path[pos + 2..].to_string()
    } else {
        path.to_string()
    }
}

/// Truncate string to max length
fn truncate(s: &str, max_len: usize) -> String {
    if s.len() > max_len {
        format!("{}...", &s[..max_len - 3])
    } else {
        s.to_string()
    }
}
