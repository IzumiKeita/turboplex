use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;

#[derive(Debug, Deserialize)]
pub struct TestRecord {
    pub test_name: String,
    pub path: String,
    pub passed: bool,
    pub duration_ms: u64,
    pub error_context: Option<ErrorContext>,
    pub test_info: Option<TestInfo>,
}

#[derive(Debug, Deserialize)]
pub struct ErrorContext {
    #[serde(rename = "type")]
    pub error_type: String,
    pub message: String,
    pub diff: Option<DiffInfo>,
    pub traceback: Vec<StackFrame>,
    pub locals_slim: HashMap<String, String>,
}

#[derive(Debug, Deserialize)]
pub struct DiffInfo {
    pub expected: Vec<String>,
    pub actual: Vec<String>,
    pub operator: String,
}

#[derive(Debug, Deserialize)]
pub struct StackFrame {
    pub file: String,
    pub line: u32,
    pub function: String,
    pub snippet: Vec<String>,
}

#[derive(Debug, Deserialize)]
pub struct TestInfo {
    pub path: String,
    pub qualname: String,
    pub lineno: u32,
}

#[derive(Debug, Serialize, Clone)]
pub struct ErrorCategory {
    pub category: String,
    pub count: usize,
    pub pattern: String,
    pub affected_tests: Vec<String>,
    pub suggested_fix: String,
}

#[derive(Debug, Serialize)]
pub struct AnalysisReport {
    pub total_tests: usize,
    pub passed: usize,
    pub failed: usize,
    pub categories: Vec<ErrorCategory>,
    pub critical_issues: Vec<String>,
    pub recommendations: Vec<String>,
}

pub fn analyze_report(jsonl_path: &Path) -> Result<AnalysisReport, String> {
    let file = File::open(jsonl_path).map_err(|e| format!("Cannot open report: {}", e))?;
    let reader = BufReader::new(file);

    let mut records: Vec<TestRecord> = Vec::new();

    for line in reader.lines() {
        let line = line.map_err(|e| format!("Read error: {}", e))?;
        if let Ok(record) = serde_json::from_str::<TestRecord>(&line) {
            records.push(record);
        }
    }

    let total = records.len();
    let passed = records.iter().filter(|r| r.passed).count();
    let failed = total - passed;

    let categories = categorize_errors(&records);
    let critical_issues = find_critical_issues(&records);
    let recommendations = generate_recommendations(&categories);

    Ok(AnalysisReport {
        total_tests: total,
        passed,
        failed,
        categories,
        critical_issues,
        recommendations,
    })
}

fn categorize_errors(records: &[TestRecord]) -> Vec<ErrorCategory> {
    let mut categories: HashMap<String, Vec<&TestRecord>> = HashMap::new();

    for record in records.iter().filter(|r| !r.passed) {
        if let Some(ref ctx) = record.error_context {
            let cat = categorize_single_error(ctx);
            categories.entry(cat).or_default().push(record);
        }
    }

    categories
        .into_iter()
        .map(|(cat, tests)| ErrorCategory {
            category: cat.clone(),
            count: tests.len(),
            pattern: extract_pattern(&cat, &tests),
            affected_tests: tests.iter().map(|t| t.test_name.clone()).collect(),
            suggested_fix: suggest_fix(&cat, &tests),
        })
        .collect()
}

fn categorize_single_error(ctx: &ErrorContext) -> String {
    if ctx.error_type == "AssertionError" {
        if let Some(ref diff) = ctx.diff {
            if diff.expected == vec!["200"] && diff.actual == vec!["403"] {
                return "AuthError: Expected 200 got 403".to_string();
            }
            if diff.expected == vec!["201"] || diff.actual == vec!["201"] {
                return "AssertionError: Creation status mismatch".to_string();
            }
        }
        return "AssertionError: General".to_string();
    }

    if ctx.message.contains("UniqueViolation") || ctx.message.contains("duplicate key") {
        return "DatabaseError: Unique constraint violation".to_string();
    }

    if ctx.message.contains("UndefinedTable") || ctx.message.contains("does not exist") {
        return "DatabaseError: Missing table".to_string();
    }

    if ctx.message.contains("ImportError") || ctx.message.contains("ModuleNotFound") {
        return "ImportError: Missing module".to_string();
    }

    if ctx.message.contains("Fixture") || ctx.message.contains("fixture") {
        return "FixtureError: Fixture setup failed".to_string();
    }

    ctx.error_type.clone()
}

fn extract_pattern(category: &str, tests: &[&TestRecord]) -> String {
    if category.contains("403") {
        "Authorization failures across multiple endpoints".to_string()
    } else if category.contains("Database") {
        "Database state or schema issues".to_string()
    } else {
        format!("{} tests affected", tests.len())
    }
}

fn suggest_fix(category: &str, _tests: &[&TestRecord]) -> String {
    if category.contains("403") {
        "Check authentication fixtures - ensure tests have valid credentials or bypass auth in test mode".to_string()
    } else if category.contains("UniqueViolation") {
        "Implement database cleanup between tests or use unique identifiers per test".to_string()
    } else if category.contains("UndefinedTable") {
        "Ensure migrations run before tests or check table names in queries".to_string()
    } else {
        "Review test setup and dependencies".to_string()
    }
}

fn find_critical_issues(records: &[TestRecord]) -> Vec<String> {
    let mut issues = Vec::new();

    let db_errors = records
        .iter()
        .filter(|r| !r.passed)
        .filter(|r| {
            r.error_context
                .as_ref()
                .map(|ctx| ctx.message.contains("database") || ctx.message.contains("connection"))
                .unwrap_or(false)
        })
        .count();

    if db_errors > 10 {
        issues.push(format!(
            "{} tests have database connectivity issues - check DB configuration",
            db_errors
        ));
    }

    let import_errors = records
        .iter()
        .filter(|r| !r.passed)
        .filter(|r| {
            r.error_context
                .as_ref()
                .map(|ctx| {
                    ctx.error_type == "ImportError" || ctx.error_type == "ModuleNotFoundError"
                })
                .unwrap_or(false)
        })
        .count();

    if import_errors > 5 {
        issues.push(format!(
            "{} tests have import errors - dependencies may be missing",
            import_errors
        ));
    }

    issues
}

fn generate_recommendations(categories: &[ErrorCategory]) -> Vec<String> {
    let mut recs = Vec::new();

    let mut sorted = categories.to_vec();
    sorted.sort_by(|a, b| b.count.cmp(&a.count));

    for cat in sorted.iter().take(3) {
        recs.push(format!(
            "Priority {}: {} - {}",
            cat.count, cat.category, cat.suggested_fix
        ));
    }

    recs
}
