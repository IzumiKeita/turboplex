#[derive(Debug, Clone)]
pub struct TestResult {
    pub test_name: String,
    pub passed: bool,
    pub cached: bool,
    pub duration_ms: u64,
    pub error: Option<String>,
}
