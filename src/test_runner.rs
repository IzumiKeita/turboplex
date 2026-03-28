use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::hash_map::DefaultHasher;
use std::fs::{self, File};
use std::hash::{Hash, Hasher};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use wait_timeout::ChildExt;
use walkdir::WalkDir;

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

#[derive(Debug, Deserialize)]
struct YamlSuiteFile {
    #[allow(dead_code)]
    name: Option<String>,
    tests: Vec<SuiteTestEntry>,
}

#[derive(Debug, Deserialize)]
struct SuiteTestEntry {
    name: String,
    command: String,
    #[serde(default)]
    args: Vec<String>,
    #[serde(default)]
    expected_exit_code: Option<i32>,
    #[serde(default)]
    timeout_ms: Option<u64>,
    #[serde(default)]
    contains: Option<String>,
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

fn discover_python_path(project_root: &Path) -> Option<String> {
    let src_path = project_root.join("src");
    if src_path.exists() && src_path.is_dir() {
        return Some(src_path.to_string_lossy().to_string());
    }

    let app_path = project_root.join("app");
    if app_path.exists() && app_path.is_dir() {
        return Some(app_path.to_string_lossy().to_string());
    }

    let main_py = project_root.join("main.py");
    if main_py.exists() {
        return Some(project_root.to_string_lossy().to_string());
    }

    for entry in std::fs::read_dir(project_root).ok().into_iter().flatten() {
        if let Ok(entry) = entry {
            let path = entry.path();
            if path.is_dir() && path.join("__init__.py").exists() {
                return Some(path.to_string_lossy().to_string());
            }
        }
    }

    None
}

fn apply_pythonpath(cmd: &mut Command, py: &PythonConfig) {
    let mut paths = Vec::new();

    if let Some(ref extra) = py.pythonpath {
        paths.push(extra.clone());
    }

    if let Some(ref proj) = py.project_path {
        let proj_path = PathBuf::from(proj);
        if !proj_path.exists() {
            eprintln!("ERROR: project_path '{}' does not exist", proj);
            std::process::exit(1);
        }

        if let Some(detected) = discover_python_path(&proj_path) {
            paths.push(detected);
        } else {
            eprintln!(
                "ERROR: Could not find Python project structure in '{}'",
                proj
            );
            eprintln!("Expected: src/, app/, or main.py");
            std::process::exit(1);
        }
    }

    if !paths.is_empty() {
        let sep = if cfg!(windows) { ";" } else { ":" };
        let merged = paths.join(sep);

        let key = "PYTHONPATH";
        let final_value = match std::env::var_os(key) {
            Some(existing) => format!("{}{}{}", merged, sep, existing.to_string_lossy()),
            None => merged,
        };
        cmd.env(key, &final_value);
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
}

fn default_timeout_cfg() -> u64 {
    30000
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
    match fs::read_to_string(path) {
        Ok(content) => toml::from_str(&content).unwrap_or_default(),
        Err(_) => TurboConfig::default(),
    }
}

pub fn compute_file_hash(path: &Path) -> Option<String> {
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

pub fn compute_string_hash(s: &str) -> u64 {
    let mut hasher = DefaultHasher::new();
    s.hash(&mut hasher);
    hasher.finish()
}

fn normalize_shell_command(command: &str, args: &[String]) -> (String, Vec<String>) {
    if !args.is_empty() {
        return (command.to_string(), args.to_vec());
    }
    let needs_shell = command.contains(' ') || command.contains('\t');
    if !needs_shell {
        return (command.to_string(), vec![]);
    }
    #[cfg(windows)]
    {
        (
            "cmd".to_string(),
            vec!["/C".to_string(), command.to_string()],
        )
    }
    #[cfg(not(windows))]
    {
        (
            "sh".to_string(),
            vec!["-c".to_string(), command.to_string()],
        )
    }
}

fn suite_entry_to_definition(entry: SuiteTestEntry) -> TestDefinition {
    let (command, args) = normalize_shell_command(&entry.command, &entry.args);
    TestDefinition {
        name: entry.name,
        command,
        args,
        expected: Some(ExpectedOutput {
            status_code: entry.expected_exit_code,
            contains: entry.contains,
            timeout_ms: entry.timeout_ms,
        }),
        priority: String::new(),
    }
}

fn parse_yaml_test_file(_path: &Path, content: &str) -> Vec<TestDefinition> {
    if let Ok(def) = serde_yaml::from_str::<TestDefinition>(content) {
        let (command, args) = normalize_shell_command(&def.command, &def.args);
        return vec![TestDefinition {
            command,
            args,
            ..def
        }];
    }
    if let Ok(suite) = serde_yaml::from_str::<YamlSuiteFile>(content) {
        return suite
            .tests
            .into_iter()
            .map(suite_entry_to_definition)
            .collect();
    }
    Vec::new()
}

fn get_cache_path(cache_dir: &Path, test_file: &Path, cache_key: &str) -> PathBuf {
    let mut hasher = DefaultHasher::new();
    test_file.hash(&mut hasher);
    cache_key.hash(&mut hasher);
    let hash = hasher.finish();
    cache_dir.join(format!("{:016x}.cache", hash))
}

fn shell_cache_key(test: &TestDefinition) -> String {
    format!("{}:{:?}", test.command, test.args)
}

fn check_cache(cache_dir: &Path, test_file: &Path, cache_key: &str) -> Option<TestResult> {
    let cache_path = get_cache_path(cache_dir, test_file, cache_key);
    let yaml_hash = compute_file_hash(test_file)?;
    let yaml_modified = fs::metadata(test_file).ok()?.modified().ok()?;

    if let Ok(cache_content) = fs::read_to_string(&cache_path) {
        let cache_parts: Vec<&str> = cache_content.split('\n').collect();
        if cache_parts.len() >= 3 {
            let cached_yaml_hash = cache_parts[0];
            let cached_result = cache_parts[1];
            let cached_time: u64 = cache_parts[2].parse().unwrap_or(0);

            if cached_yaml_hash == yaml_hash {
                let yaml_modified_secs = yaml_modified
                    .duration_since(std::time::SystemTime::UNIX_EPOCH)
                    .map(|d| d.as_secs())
                    .unwrap_or(0);

                if cached_time >= yaml_modified_secs {
                    return Some(TestResult {
                        test_name: cached_result.to_string(),
                        passed: true,
                        cached: true,
                        duration_ms: 0,
                        error: None,
                    });
                }
            }
        }
    }
    None
}

fn save_cache(cache_dir: &Path, test_file: &Path, cache_key: &str, result: &TestResult) {
    let cache_path = get_cache_path(cache_dir, test_file, cache_key);
    if let Some(yaml_hash) = compute_file_hash(test_file) {
        if let Ok(metadata) = fs::metadata(test_file) {
            if let Ok(modified) = metadata.modified() {
                let modified_secs = modified
                    .duration_since(std::time::SystemTime::UNIX_EPOCH)
                    .map(|d| d.as_secs())
                    .unwrap_or(0);
                let cache_content =
                    format!("{}\n{}\n{}\n", yaml_hash, result.test_name, modified_secs);
                let _ = fs::create_dir_all(cache_dir);
                let _ = fs::write(&cache_path, cache_content);
            }
        }
    }
}

#[derive(Debug, Clone)]
pub struct TestResult {
    pub test_name: String,
    pub passed: bool,
    pub cached: bool,
    pub duration_ms: u64,
    pub error: Option<String>,
}

pub fn run_process_with_timeout(
    cmd: &mut Command,
    timeout: Duration,
) -> std::io::Result<CapturedOutput> {
    cmd.stdin(Stdio::null());
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());

    let mut child = cmd.spawn()?;
    let mut stdout_pipe = child.stdout.take().expect("stdout piped");
    let mut stderr_pipe = child.stderr.take().expect("stderr piped");

    let stdout_handle = thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stdout_pipe.read_to_end(&mut buf);
        buf
    });
    let stderr_handle = thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stderr_pipe.read_to_end(&mut buf);
        buf
    });

    let wait_result = match child.wait_timeout(timeout) {
        Ok(r) => r,
        Err(e) => {
            let _ = child.kill();
            let _ = child.wait();
            let _ = stdout_handle.join();
            let _ = stderr_handle.join();
            return Err(e);
        }
    };

    match wait_result {
        Some(status) => {
            let stdout = stdout_handle.join().unwrap_or_default();
            let stderr = stderr_handle.join().unwrap_or_default();
            Ok(CapturedOutput {
                status: status.code(),
                stdout,
                stderr,
                timed_out: false,
            })
        }
        None => {
            let _ = child.kill();
            let _ = child.wait();
            let stdout = stdout_handle.join().unwrap_or_default();
            let stderr = stderr_handle.join().unwrap_or_default();
            Ok(CapturedOutput {
                status: None,
                stdout,
                stderr,
                timed_out: true,
            })
        }
    }
}

pub fn run_test(test: &TestDefinition, test_file: &Path, ctx: &TestContext) -> TestResult {
    let cache_key = shell_cache_key(test);
    if ctx.config.execution.cache_enabled {
        if let Some(cached) = check_cache(&ctx.cache_dir, test_file, &cache_key) {
            return cached;
        }
    }

    let timeout_ms = test
        .expected
        .as_ref()
        .and_then(|e| e.timeout_ms)
        .unwrap_or(ctx.config.execution.default_timeout_ms);

    let mut shell_cmd = Command::new(&test.command);
    shell_cmd.args(&test.args);

    let start = Instant::now();
    let captured = run_process_with_timeout(&mut shell_cmd, Duration::from_millis(timeout_ms));
    let duration_ms = start.elapsed().as_millis() as u64;

    let result = match captured {
        Ok(cap) if cap.timed_out => TestResult {
            test_name: test.name.clone(),
            passed: false,
            cached: false,
            duration_ms,
            error: Some(format!("Timed out after {}ms (process killed)", timeout_ms)),
        },
        Ok(cap) => {
            let exit_code = cap.status.unwrap_or(-1);
            let expected_code = test
                .expected
                .as_ref()
                .and_then(|e| e.status_code)
                .unwrap_or(0);

            let mut error = None;
            let mut passed = exit_code == expected_code;

            if passed {
                if let Some(ref expected_str) =
                    test.expected.as_ref().and_then(|e| e.contains.clone())
                {
                    let output_str = String::from_utf8_lossy(&cap.stdout);
                    if !output_str.contains(expected_str) {
                        passed = false;
                        error = Some(format!("Output does not contain '{}'", expected_str));
                    }
                }
            } else {
                error = Some(format!(
                    "Expected exit code {}, got {}",
                    expected_code, exit_code
                ));
            }

            TestResult {
                test_name: test.name.clone(),
                passed,
                cached: false,
                duration_ms,
                error,
            }
        }
        Err(e) => TestResult {
            test_name: test.name.clone(),
            passed: false,
            cached: false,
            duration_ms,
            error: Some(e.to_string()),
        },
    };

    if ctx.config.execution.cache_enabled && !result.cached && result.passed {
        save_cache(&ctx.cache_dir, test_file, &cache_key, &result);
    }

    result
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct PythonCollectedItem {
    pub path: String,
    pub qualname: String,
    pub lineno: u32,
    pub kind: String,
}

impl PythonCollectedItem {
    pub fn label(&self) -> String {
        let base = Path::new(&self.path)
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or(self.path.as_str());
        format!("{}::{}", base, self.qualname)
    }

    pub fn cache_key(&self) -> String {
        format!("py::{}::{}", self.path, self.qualname)
    }
}

#[derive(Debug, Deserialize)]
struct PythonCollectResponse {
    items: Vec<PythonCollectedItem>,
}

#[derive(Debug, Deserialize)]
struct PythonRunResponse {
    passed: bool,
    duration_ms: u64,
    error: Option<String>,
}

pub fn python_config_effective(cfg: &TurboConfig) -> PythonConfig {
    cfg.python.clone().unwrap_or_default()
}

pub fn collect_python_tests(cfg: &TurboConfig) -> Result<Vec<PythonCollectedItem>, String> {
    let py = python_config_effective(cfg);
    if !py.enabled {
        return Ok(vec![]);
    }
    let (interpreter, module) = py.effective();
    if py.test_paths.is_empty() {
        return Err("python.test_paths is empty".to_string());
    }

    // On Windows, use python.exe explicitly
    let actual_interpreter = if cfg!(windows) && interpreter == "python" {
        // Try to find python.exe in PATH
        if let Ok(python_path) = std::env::var("PYTHON_HOME") {
            format!("{}\\python.exe", python_path)
        } else {
            // Use the interpreter as-is, let the OS find it in PATH
            interpreter.clone()
        }
    } else {
        interpreter.clone()
    };

    let mut cmd = Command::new(&actual_interpreter);
    cmd.arg("-m");
    cmd.arg(&module);
    cmd.arg("collect");
    for p in &py.test_paths {
        cmd.arg(p);
    }

    // Don't set PYTHONPATH - let Python find the module via installed package

    apply_pythonpath(&mut cmd, &py);

    cmd.env("SQLALCHEMY_SILENCE_UBER_WARNING", "1");
    cmd.env("SQLALCHEMY_LOG", "0");
    cmd.env("TURBOTEST_SUBPROCESS", "1");

    // Use longer timeout for collect (120 seconds) since importing test modules can be slow
    let timeout_ms = 120_000.max(cfg.execution.default_timeout_ms);
    let captured = run_process_with_timeout(&mut cmd, Duration::from_millis(timeout_ms))
        .map_err(|e| format!("python collect spawn failed: {}", e))?;

    if captured.timed_out {
        return Err(format!("python collect timed out after {}ms", timeout_ms));
    }
    if captured.status != Some(0) {
        let err = String::from_utf8_lossy(&captured.stderr);
        return Err(format!(
            "python collect failed (status {:?}): {}",
            captured.status, err
        ));
    }

    let text = String::from_utf8(captured.stdout)
        .map_err(|e| format!("collect stdout not utf-8: {}", e))?;
    let parsed: PythonCollectResponse = serde_json::from_str(text.trim())
        .map_err(|e| format!("collect JSON parse error: {} (stdout: {:?})", e, text))?;
    Ok(parsed.items)
}

fn run_python_item_fixed(
    item: &PythonCollectedItem,
    cfg: &TurboConfig,
    cache_dir: &Path,
    progress: &Arc<Mutex<indicatif::ProgressBar>>,
) -> TestResult {
    let py = python_config_effective(cfg);
    let (interpreter, module) = py.effective();
    let timeout_ms = cfg.execution.default_timeout_ms;
    let label = item.label();

    let source = PathBuf::from(&item.path);
    let cache_key = item.cache_key();

    if cfg.execution.cache_enabled {
        if let Some(cached) = check_cache(cache_dir, &source, &cache_key) {
            let _ = progress.lock().map(|pb| pb.inc(1));
            return TestResult {
                test_name: label,
                passed: cached.passed,
                cached: true,
                duration_ms: cached.duration_ms,
                error: cached.error,
            };
        }
    }

    let mut cmd = Command::new(&interpreter);
    cmd.arg("-m");
    cmd.arg(&module);
    cmd.arg("run");
    cmd.arg("--path");
    cmd.arg(&item.path);
    cmd.arg("--qual");
    cmd.arg(&item.qualname);

    apply_pythonpath(&mut cmd, &py);

    cmd.env("SQLALCHEMY_SILENCE_UBER_WARNING", "1");
    cmd.env("SQLALCHEMY_LOG", "0");
    cmd.env("TURBOTEST_SUBPROCESS", "1");

    let start = Instant::now();
    let captured = match run_process_with_timeout(&mut cmd, Duration::from_millis(timeout_ms)) {
        Ok(c) => c,
        Err(e) => {
            let _ = progress.lock().map(|pb| pb.inc(1));
            return TestResult {
                test_name: label.clone(),
                passed: false,
                cached: false,
                duration_ms: start.elapsed().as_millis() as u64,
                error: Some(e.to_string()),
            };
        }
    };
    let duration_ms = start.elapsed().as_millis() as u64;

    let mut result = if captured.timed_out {
        TestResult {
            test_name: label.clone(),
            passed: false,
            cached: false,
            duration_ms,
            error: Some(format!("Timed out after {}ms (process killed)", timeout_ms)),
        }
    } else {
        let text = String::from_utf8_lossy(&captured.stdout).trim().to_string();

        let json_text = text
            .lines()
            .filter(|line| line.trim().starts_with('{') && line.contains("passed"))
            .last()
            .unwrap_or(&text)
            .to_string();

        let json_ok = serde_json::from_str::<PythonRunResponse>(&json_text);
        match json_ok {
            Ok(j) => TestResult {
                test_name: label.clone(),
                passed: j.passed && captured.status == Some(0),
                cached: false,
                duration_ms: j.duration_ms.max(duration_ms),
                error: j.error.or_else(|| {
                    if captured.status != Some(0) {
                        Some(format!("exit status {}", captured.status.unwrap_or(-1)))
                    } else {
                        None
                    }
                }),
            },
            Err(e) => TestResult {
                test_name: label.clone(),
                passed: false,
                cached: false,
                duration_ms,
                error: Some(format!(
                    "Invalid JSON on stdout: {} (parse: {})",
                    json_text, e
                )),
            },
        }
    };

    if !result.passed && result.error.is_none() {
        let err = String::from_utf8_lossy(&captured.stderr);
        if !err.trim().is_empty() {
            result.error = Some(err.trim().to_string());
        }
    }

    if cfg.execution.cache_enabled && result.passed {
        save_cache(
            cache_dir,
            &source,
            &cache_key,
            &TestResult {
                test_name: result.test_name.clone(),
                passed: true,
                cached: false,
                duration_ms: result.duration_ms,
                error: None,
            },
        );
    }

    let _ = progress.lock().map(|pb| pb.inc(1));
    result
}

pub fn discover_tests(tests_dir: &str) -> Vec<(PathBuf, TestDefinition)> {
    let mut tests = Vec::new();

    for entry in WalkDir::new(tests_dir)
        .follow_links(true)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        let path = entry.path();
        let is_yaml = path
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| e.eq_ignore_ascii_case("yaml") || e.eq_ignore_ascii_case("yml"))
            .unwrap_or(false);
        if !is_yaml {
            continue;
        }
        if let Ok(content) = fs::read_to_string(path) {
            let defs = parse_yaml_test_file(path, &content);
            for def in defs {
                tests.push((path.to_path_buf(), def));
            }
        }
    }

    tests
}

#[derive(Clone)]
pub enum TestJob {
    Shell { file: PathBuf, def: TestDefinition },
    Python { item: PythonCollectedItem },
}

pub fn run_jobs_parallel(jobs: &[TestJob], ctx: &TestContext) -> Vec<(PathBuf, TestResult)> {
    let cache_dir = ctx.cache_dir.clone();
    let progress = ctx.progress.clone();
    let config = ctx.config.clone();

    jobs.par_iter()
        .map(|job| match job {
            TestJob::Shell { file, def } => {
                let result = run_test(def, file, ctx);
                (file.clone(), result)
            }
            TestJob::Python { item } => {
                let path = PathBuf::from(&item.path);
                let result = run_python_item_fixed(item, &config, &cache_dir, &progress);
                (path, result)
            }
        })
        .collect()
}

/// DISCOVERY ENGINE: Auto-detect test directories
/// Searches recursively from the current directory for test_*.py files
/// Excludes .git, venv, __pycache__, and other common ignore patterns
pub fn discover_test_paths(max_depth: usize) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    let current_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));

    // Common directories to exclude
    let exclude_patterns = [
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
        ".tox",
        ".eggs",
        "*.egg-info",
        ".coverage",
        ".hypothesis",
    ];

    for entry in WalkDir::new(&current_dir)
        .max_depth(max_depth)
        .follow_links(false)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        let path = entry.path();

        // Skip if any parent is in exclude patterns
        let should_skip = path.components().any(|c| {
            let component = c.as_os_str().to_string_lossy();
            exclude_patterns.iter().any(|p| {
                if p.starts_with('*') {
                    component.ends_with(&p[1..])
                } else {
                    component == *p
                }
            })
        });

        if should_skip {
            continue;
        }

        // Check if it's a test file
        if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
            if name.starts_with("test_") && name.ends_with(".py") {
                if let Some(parent) = path.parent() {
                    if !paths.contains(&parent.to_path_buf()) {
                        paths.push(parent.to_path_buf());
                    }
                }
            }
        }
    }

    paths.sort();
    paths.dedup();
    paths
}
