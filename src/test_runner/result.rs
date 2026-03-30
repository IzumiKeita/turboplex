#[derive(Debug, Clone)]
pub struct TestResult {
    pub test_name: String,
    pub passed: bool,
    pub cached: bool,
    pub duration_ms: u64,
    pub error: Option<String>,
    pub enriched_data: Option<serde_json::Value>, // JSON raw del Python runner
}
