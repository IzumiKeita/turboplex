use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct ExpectedOutput {
    #[serde(alias = "expected_exit_code")]
    pub status_code: Option<i32>,
    pub contains: Option<String>,
    #[serde(default = "default_timeout")]
    pub timeout_ms: Option<u64>,
}

fn default_timeout() -> Option<u64> {
    Some(5000)
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct TestDefinition {
    #[serde(alias = "test_name")]
    pub name: String,
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(alias = "expected_output")]
    pub expected: Option<ExpectedOutput>,
    #[serde(default)]
    pub priority: String,
}

#[derive(Debug, Deserialize, Clone, Default)]
pub struct PythonConfig {
    #[serde(default)]
    pub enabled: bool,
    pub interpreter: Option<String>,
    pub module: Option<String>,
    #[serde(default)]
    pub test_paths: Vec<String>,
    pub pythonpath: Option<String>,
    pub project_path: Option<String>,
}

impl PythonConfig {
    pub fn effective(&self) -> (String, String) {
        (
            self.interpreter
                .clone()
                .unwrap_or_else(|| "python".to_string()),
            self.module
                .clone()
                .unwrap_or_else(|| "turboplex_py".to_string()),
        )
    }
}

#[derive(Debug, Deserialize, Clone)]
pub struct TurboConfig {
    pub execution: ExecutionConfig,
    pub reporting: Option<ReportingConfig>,
    #[serde(default)]
    pub python: Option<PythonConfig>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct ExecutionConfig {
    pub max_workers: Option<usize>,
    #[serde(default)]
    pub use_tokio: bool,
    #[serde(default = "default_timeout_cfg")]
    pub default_timeout_ms: u64,
    #[serde(default = "default_true")]
    pub parallel_suites: bool,
    pub cache_dir: Option<String>,
    #[serde(default = "default_true")]
    pub cache_enabled: bool,
    #[serde(default = "default_worker_restart_interval")]
    pub worker_restart_interval: usize,
}

fn default_timeout_cfg() -> u64 {
    30000
}

fn default_worker_restart_interval() -> usize {
    50
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Deserialize, Clone)]
pub struct ReportingConfig {
    #[serde(default = "default_true")]
    pub verbose: bool,
    #[serde(default = "default_true")]
    pub show_duration: bool,
}

impl Default for TurboConfig {
    fn default() -> Self {
        Self {
            execution: ExecutionConfig {
                max_workers: Some(num_cpus::get()),
                use_tokio: false,
                default_timeout_ms: 30000,
                parallel_suites: true,
                cache_dir: Some(".turbocache".to_string()),
                cache_enabled: true,
                worker_restart_interval: 50,
            },
            reporting: Some(ReportingConfig {
                verbose: true,
                show_duration: true,
            }),
            python: None,
        }
    }
}

#[derive(Clone)]
pub struct TestContext {
    pub config: TurboConfig,
    pub cache_dir: PathBuf,
    pub progress: Arc<Mutex<indicatif::ProgressBar>>,
    pub spinner_style: indicatif::ProgressStyle,
}

#[derive(Debug, Clone)]
pub struct CapturedOutput {
    pub status: Option<i32>,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    pub timed_out: bool,
}

pub fn load_config(path: &str) -> TurboConfig {
    match std::fs::read_to_string(path) {
        Ok(content) => toml::from_str(&content).unwrap_or_default(),
        Err(_) => TurboConfig::default(),
    }
}

pub fn python_config_effective(cfg: &TurboConfig) -> PythonConfig {
    cfg.python.clone().unwrap_or_default()
}
