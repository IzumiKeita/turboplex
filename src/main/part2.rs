use colored::Colorize;
use serde_json::json;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::channel;
use std::thread;
use std::time::{Instant, SystemTime};
use turboplex::{load_config, TestResult};
use walkdir::WalkDir;

use super::output::{emit_error, OutputMode, OutputOptions, OutputState, TestEvent};
use super::part1::{
    compute_file_hash, compute_text_hash, get_collected_tests_cache_path, get_test_cache_dir,
    get_test_files_hash, get_test_results_cache_dir, RuntimePythonEnv,
};

static TMP_COUNTER: AtomicU64 = AtomicU64::new(0);

fn temp_json_path(prefix: &str) -> PathBuf {
    let n = TMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    let ts = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "{}_{}_{}_{}.json",
        prefix,
        std::process::id(),
        ts,
        n
    ))
}

fn apply_python_encoding_env(cmd: &mut Command) {
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("PYTHONUNBUFFERED", "1");
}

fn generate_failure_report(
    results: &[(PathBuf, TestResult)],
    _test_paths: &[String],
    base_dir: &Path,
) -> Option<PathBuf> {
    // Agrupar errores por fingerprint para de-duplicación
    let mut error_groups: HashMap<String, Vec<serde_json::Value>> = HashMap::new();
    
    for (path, result) in results {
        if !result.passed {
            let file_content = fs::read_to_string(path).unwrap_or_default();
            let lines: Vec<&str> = file_content.lines().collect();

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

            let context_start = line_no.saturating_sub(6);
            let context_end = (line_no + 5).min(lines.len());
            let context: Vec<String> = (context_start..context_end)
                .map(|i| {
                    let prefix = if i + 1 == line_no { ">>> " } else { "    " };
                    format!("{}{}: {}", prefix, i + 1, lines.get(i).unwrap_or(&""))
                })
                .collect();

            // Generar fingerprint del error para agrupación
            let fingerprint = compute_error_fingerprint(error_msg, line_no);

            let failure = json!({
                "test": result.test_name,
                "file": normalize_path_for_json(path),
                "line": line_no,
                "error": error_msg,
                "duration_ms": result.duration_ms,
                "context": context
            });

            error_groups.entry(fingerprint).or_default().push(failure);
        }
    }

    // Construir reporte con de-duplicación
    let mut deduped_failures = Vec::new();
    for (fingerprint, group) in error_groups {
        let count = group.len();
        let representative = group[0].clone();
        
        let deduped = if count > 1 {
            // Limitar a 3 ejemplos representativos
            let examples: Vec<serde_json::Value> = group.iter().take(3).cloned().collect();
            json!({
                "fingerprint": fingerprint,
                "occurrences": count,
                "representative": representative,
                "examples": examples
            })
        } else {
            representative
        };
        
        deduped_failures.push(deduped);
    }

    let report = json!({
        "timestamp": chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
        "total_tests": results.len(),
        "failed_count": results.iter().filter(|(_, r)| !r.passed).count(),
        "unique_failures": deduped_failures.len(),
        "failures": deduped_failures
    });

    let timestamp = chrono::Local::now().format("%Y%m%d_%H%M%S").to_string();
    
    // Crear carpeta .tplex_reports/ si no existe
    let reports_dir = base_dir.join(".tplex_reports");
    let _ = fs::create_dir_all(&reports_dir);
    
    // Guardar reporte con timestamp dentro de .tplex_reports/
    let report_path = reports_dir.join(format!(".tplex_report_{}.json", timestamp));
    if let Ok(content) = serde_json::to_string_pretty(&report) {
        if fs::write(&report_path, content).is_ok() {
            // Actualizar enlace en raíz al último reporte
            let _ = update_latest_report_link(base_dir, &report_path);
            
            // Limpiar reportes antiguos dentro de .tplex_reports/
            let _ = cleanup_old_reports(&reports_dir, 20);
            
            return Some(report_path);
        }
    }
    None
}

/// Crea/actualiza el enlace/copia .tplex_report.json apuntando al último reporte
fn update_latest_report_link(base_dir: &Path, latest_report: &Path) -> std::io::Result<()> {
    let link_path = base_dir.join(".tplex_report.json");
    
    // En Windows, los symlinks requieren privilegios especiales, así que copiamos
    #[cfg(windows)]
    {
        fs::copy(latest_report, &link_path)?;
    }
    
    // En Unix, podemos usar symlinks
    #[cfg(not(windows))]
    {
        // Remover enlace existente si existe
        let _ = fs::remove_file(&link_path);
        std::os::unix::fs::symlink(latest_report, &link_path)?;
    }
    
    Ok(())
}

/// Limpia reportes antiguos manteniendo solo los últimos N
fn cleanup_old_reports(base_dir: &Path, keep_count: usize) -> std::io::Result<()> {
    let mut reports: Vec<(PathBuf, std::time::SystemTime)> = Vec::new();
    
    // Buscar todos los reportes con timestamp
    for entry in fs::read_dir(base_dir)? {
        let entry = entry?;
        let path = entry.path();
        let name = path.file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("");
        
        // Filtrar archivos .tplex_report_*.json (con timestamp)
        if name.starts_with(".tplex_report_") && name.ends_with(".json") {
            if let Ok(metadata) = entry.metadata() {
                if let Ok(modified) = metadata.modified() {
                    reports.push((path, modified));
                }
            }
        }
    }
    
    // Ordenar por fecha de modificación (más reciente primero)
    reports.sort_by(|a, b| b.1.cmp(&a.1));
    
    // Eliminar reportes excedentes
    if reports.len() > keep_count {
        for (path, _) in reports.iter().skip(keep_count) {
            let _ = fs::remove_file(path);
        }
    }
    
    Ok(())
}

/// Genera un fingerprint para agrupar errores similares
fn compute_error_fingerprint(error_msg: &str, line_no: usize) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    
    // Normalizar el mensaje de error
    let normalized = error_msg
        .to_lowercase()
        .replace(|c: char| c.is_ascii_punctuation() && c != '_', " ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    
    // Usar las primeras 5 palabras + rango de línea para fingerprint
    let key_words: Vec<&str> = normalized.split_whitespace().take(5).collect();
    let line_bucket = line_no / 10; // Agrupar líneas cercanas
    
    let mut hasher = DefaultHasher::new();
    key_words.hash(&mut hasher);
    line_bucket.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

/// Normaliza rutas a formato cross-platform (forward slashes)
fn normalize_path_for_json(path: &Path) -> String {
    path.to_string_lossy()
        .replace('\\', "/")
        .replace("//", "/")
}

fn run_pytest_collect(
    paths: &[String],
    env: &RuntimePythonEnv,
    worker_id: usize,
) -> Result<Vec<serde_json::Value>, String> {
    let mut cmd = Command::new(&env.interpreter);
    cmd.current_dir(&env.cwd);
    cmd.arg("-m");
    cmd.arg("pytest");
    cmd.arg("--collect-only");
    cmd.arg("-q");
    cmd.args(paths);
    if let Some(pp) = &env.pythonpath {
        cmd.env("PYTHONPATH", pp);
    }
    apply_python_encoding_env(&mut cmd);
    // Blindaje TurboPlex: forzar variables de entorno independientemente del modo
    cmd.env("TURBOPLEX_MODE", "1");
    cmd.env("TURBOPLEX_WORKER_ID", format!("worker_{}", worker_id));
    let output = cmd
        .output()
        .map_err(|e| format!("Failed to run pytest collect: {}", e))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("Pytest collect failed: {}", stderr));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut items: Vec<serde_json::Value> = Vec::new();
    for line in stdout.lines().map(|l| l.trim()).filter(|l| !l.is_empty()) {
        let path_part = line.split("::").next().unwrap_or("").trim();
        if path_part.is_empty() || !path_part.ends_with(".py") {
            continue;
        }
        items.push(json!({
            "path": path_part,
            "qualname": line,
            "kind": "pytest"
        }));
    }
    Ok(items)
}

fn run_python_collector(
    paths: &[String],
    env: &RuntimePythonEnv,
) -> Result<Vec<serde_json::Value>, String> {
    if env.compat {
        return run_pytest_collect(paths, env, 0); // Worker 0 para collect
    }

    eprintln!("[RUST DEBUG] Starting Python collector");
    eprintln!("[RUST DEBUG] Interpreter: {}", env.interpreter);
    eprintln!("[RUST DEBUG] Module: {}", env.module);
    eprintln!("[RUST DEBUG] Paths: {:?}", paths);
    eprintln!("[RUST DEBUG] CWD: {:?}", env.cwd);

    let mut cmd = Command::new(&env.interpreter);
    cmd.current_dir(&env.cwd);
    cmd.arg("-m");
    cmd.arg(&env.module);
    cmd.arg("collect");
    cmd.args(paths);
    let out_json_path = temp_json_path("tpx_collect");
    cmd.arg("--out-json");
    cmd.arg(&out_json_path);
    if let Some(pp) = &env.pythonpath {
        cmd.env("PYTHONPATH", pp);
        eprintln!("[RUST DEBUG] PYTHONPATH: {}", pp);
    }
    cmd.env("SQLALCHEMY_SILENCE_UBER_WARNING", "1");
    cmd.env("SQLALCHEMY_LOG", "0");
    cmd.env("TURBOTEST_SUBPROCESS", "1");
    apply_python_encoding_env(&mut cmd);

    eprintln!("[RUST DEBUG] Running command: {:?}", cmd);
    eprintln!("[RUST DEBUG] Waiting for collector output...");

    let output = cmd
        .output()
        .map_err(|e| format!("Failed to run collector: {}", e))?;

    eprintln!("[RUST DEBUG] Collector finished with status: {:?}", output.status);
    eprintln!("[RUST DEBUG] Collector stderr: {}", String::from_utf8_lossy(&output.stderr).trim());

    let file_text = fs::read_to_string(&out_json_path)
        .map_err(|e| format!("collector did not produce out-json file: {}", e));
    let _ = fs::remove_file(&out_json_path);

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        let mut msg = String::from("Collector failed");
        if !stderr.is_empty() {
            msg.push_str(&format!("; stderr: {}", stderr));
        } else if !stdout.is_empty() {
            msg.push_str(&format!("; stdout: {}", stdout));
        }
        return Err(msg);
    }

    let text = file_text?;
    eprintln!("[RUST DEBUG] Collector output file content (first 200 chars): {}", &text[..text.len().min(200)]);
    
    let parsed: serde_json::Value = serde_json::from_str(text.trim())
        .map_err(|e| format!("Failed to parse collector out-json: {}", e))?;

    let items = parsed["items"].as_array().cloned().unwrap_or_default();
    eprintln!("[RUST DEBUG] Collected {} tests", items.len());

    Ok(items)
}

fn load_cached_pass_result(
    env: &RuntimePythonEnv,
    cache_key: &str,
    test_name: &str,
) -> Option<TestResult> {
    let dir = get_test_results_cache_dir(&env.cwd);
    let path = dir.join(format!("{}.json", cache_key));
    let text = fs::read_to_string(path).ok()?;
    let parsed: serde_json::Value = serde_json::from_str(&text).ok()?;
    if parsed.get("passed").and_then(|v| v.as_bool()) != Some(true) {
        return None;
    }
    Some(TestResult {
        test_name: test_name.to_string(),
        passed: true,
        cached: true,
        duration_ms: 0,
        error: None,
        enriched_data: None,
    })
}

fn save_cached_pass_result(env: &RuntimePythonEnv, cache_key: &str) {
    let dir = get_test_results_cache_dir(&env.cwd);
    let _ = fs::create_dir_all(&dir);
    let path = dir.join(format!("{}.json", cache_key));
    let payload = json!({ "passed": true });
    if let Ok(text) = serde_json::to_string(&payload) {
        let _ = fs::write(path, text);
    }
}

fn is_skipped_result(result: &TestResult) -> bool {
    result
        .enriched_data
        .as_ref()
        .and_then(|v| v.get("skipped"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}

fn run_python_test(env: &RuntimePythonEnv, path: &str, qual: &str) -> TestResult {
    let start = Instant::now();

    let mut cmd = Command::new(&env.interpreter);
    cmd.current_dir(&env.cwd);
    cmd.arg("-m");
    cmd.arg(&env.module);
    cmd.arg("run");
    cmd.arg("--path");
    cmd.arg(path);
    cmd.arg("--qual");
    cmd.arg(qual);
    let out_json_path = temp_json_path("tpx_run");
    cmd.arg("--out-json");
    cmd.arg(&out_json_path);
    if let Some(pp) = &env.pythonpath {
        cmd.env("PYTHONPATH", pp);
    }
    cmd.env("SQLALCHEMY_SILENCE_UBER_WARNING", "1");
    cmd.env("SQLALCHEMY_LOG", "0");
    cmd.env("TURBOTEST_SUBPROCESS", "1");
    apply_python_encoding_env(&mut cmd);

    let output = cmd.output();

    let duration_ms = start.elapsed().as_millis() as u64;

    match output {
        Ok(out) => {
            let file_text = fs::read_to_string(&out_json_path).ok();
            let _ = fs::remove_file(&out_json_path);

            if let Some(text) = file_text {
                if let Ok(resp) = serde_json::from_str::<serde_json::Value>(text.trim()) {
                    return TestResult {
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
                        enriched_data: Some(resp),
                    };
                }
            }

            let stdout = String::from_utf8_lossy(&out.stdout);
            if let Ok(resp) = serde_json::from_str::<serde_json::Value>(stdout.trim()) {
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
                    enriched_data: Some(resp),
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
                    enriched_data: None,
                }
            }
        }
        Err(e) => TestResult {
            test_name: qual.to_string(),
            passed: false,
            cached: false,
            duration_ms,
            error: Some(format!("Failed to run test: {}", e)),
            enriched_data: None,
        },
    }
}

fn run_pytest_test(env: &RuntimePythonEnv, nodeid: &str, worker_id: usize) -> TestResult {
    let start = Instant::now();

    let mut cmd = Command::new(&env.interpreter);
    cmd.current_dir(&env.cwd);
    cmd.arg("-m");
    cmd.arg("pytest");
    cmd.arg("-q");
    cmd.arg(nodeid);
    if let Some(pp) = &env.pythonpath {
        cmd.env("PYTHONPATH", pp);
    }
    apply_python_encoding_env(&mut cmd);
    // Blindaje TurboPlex: forzar variables de entorno independientemente del modo
    cmd.env("TURBOPLEX_MODE", "1");
    cmd.env("TURBOPLEX_WORKER_ID", format!("worker_{}", worker_id));
    let output = cmd.output();

    let duration_ms = start.elapsed().as_millis() as u64;
    match output {
        Ok(out) => {
            let passed = out.status.success();
            let stderr = String::from_utf8_lossy(&out.stderr);
            let stdout = String::from_utf8_lossy(&out.stdout);
            let err = if passed {
                None
            } else if !stderr.trim().is_empty() {
                Some(stderr.to_string())
            } else if !stdout.trim().is_empty() {
                Some(stdout.to_string())
            } else {
                Some("pytest failed".to_string())
            };
            TestResult {
                test_name: nodeid.to_string(),
                passed,
                cached: false,
                duration_ms,
                error: err,
                enriched_data: None,
            }
        }
        Err(e) => TestResult {
            test_name: nodeid.to_string(),
            passed: false,
            cached: false,
            duration_ms,
            error: Some(format!("Failed to run pytest: {}", e)),
            enriched_data: None,
        },
    }
}

fn run_test_item(env: &RuntimePythonEnv, path: &str, qual: &str, worker_id: usize) -> TestResult {
    let resolved = resolve_test_path(env, path);
    let file_hash = compute_file_hash(&resolved).unwrap_or_else(|| "none".to_string());
    let cache_key_raw = format!(
        "{}|{}|{}|{}",
        resolved.to_string_lossy(),
        qual,
        file_hash,
        env.fingerprint
    );
    let cache_key = compute_text_hash(&cache_key_raw);
    if let Some(cached) = load_cached_pass_result(env, &cache_key, qual) {
        return cached;
    }
    let result = if env.compat {
        run_pytest_test(env, qual, worker_id)
    } else {
        run_python_test(env, path, qual)
    };
    if result.passed && !is_skipped_result(&result) {
        save_cached_pass_result(env, &cache_key);
    }
    result
}

fn get_or_collect_tests(
    paths: &[String],
    env: &RuntimePythonEnv,
) -> Result<Vec<serde_json::Value>, String> {
    eprintln!("[RUST DEBUG] get_or_collect_tests() called");
    
    let cache_dir = get_test_cache_dir(&env.cwd);
    let cache_file = get_collected_tests_cache_path(&env.cwd);
    let hash_file = cache_dir.join("files_hash.txt");
    
    eprintln!("[RUST DEBUG] Cache dir: {:?}", cache_dir);
    eprintln!("[RUST DEBUG] Cache file: {:?}", cache_file);

    let mut test_files: Vec<PathBuf> = Vec::new();
    eprintln!("[RUST DEBUG] Walking test paths...");
    for p in paths {
        eprintln!("[RUST DEBUG] Processing path: {}", p);
        let pb = resolve_test_path(env, p);
        if pb.is_file() {
            eprintln!("[RUST DEBUG] Found file: {:?}", pb);
            test_files.push(pb);
        } else {
            eprintln!("[RUST DEBUG] Walking directory: {:?}", pb);
            let walker = WalkDir::new(&pb).max_depth(10);
            for entry in walker.into_iter().filter_map(|e| e.ok()) {
                if entry.file_type().is_file() {
                    let name = entry.file_name().to_string_lossy();
                    if (name.starts_with("test_") || name.ends_with("_test.py"))
                        && name.ends_with(".py")
                    {
                        eprintln!("[RUST DEBUG] Found test file: {}", name);
                        test_files.push(entry.path().to_path_buf());
                    }
                }
            }
        }
    }
    eprintln!("[RUST DEBUG] Total test files found: {}", test_files.len());

    let current_hash = get_test_files_hash(&test_files, &env.fingerprint);
    eprintln!("[RUST DEBUG] Current hash: {}", current_hash);

    if let (Ok(cached_content), Ok(stored_hash)) = (
        fs::read_to_string(&cache_file),
        fs::read_to_string(&hash_file),
    ) {
        eprintln!("[RUST DEBUG] Cache exists, stored_hash: {}", stored_hash.trim());
        if stored_hash.trim() == current_hash {
            eprintln!("[RUST DEBUG] Cache HIT, returning cached tests");
            let parsed: serde_json::Value = serde_json::from_str(&cached_content)
                .map_err(|e| format!("Invalid cache: {}", e))?;
            return Ok(parsed["items"].as_array().cloned().unwrap_or_default());
        }
        eprintln!("[RUST DEBUG] Cache MISS, collecting fresh");
    } else {
        eprintln!("[RUST DEBUG] No cache found, collecting fresh");
    }

    eprintln!("[RUST DEBUG] About to call run_python_collector...");
    let items = run_python_collector(paths, env)?;
    eprintln!("[RUST DEBUG] run_python_collector returned {} items", items.len());

    let _ = fs::create_dir_all(&cache_dir);
    let cache_content = json!({ "items": items }).to_string();
    let _ = fs::write(&cache_file, &cache_content);
    let _ = fs::write(&hash_file, &current_hash);

    Ok(items)
}

pub(crate) fn resolve_test_path(env: &RuntimePythonEnv, path: &str) -> PathBuf {
    let p = PathBuf::from(path);
    if p.is_absolute() {
        p
    } else {
        env.cwd.join(p)
    }
}

/// Perfil de hardware detectado para orquestación inteligente
#[derive(Debug, Clone, Copy)]
enum HardwareTier {
    Low,   // <= 2 cores: 100ms stagger, workers = cores
    Mid,   // 3-8 cores: 50ms stagger, workers = cores  
    High,  // > 8 cores: 0ms stagger, workers = cores * 1.5
}

/// Detecta el perfil de hardware y retorna (tier, workers, stagger_ms)
fn detect_hardware_profile() -> (HardwareTier, usize, u64) {
    let cores = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(4);
    
    let tier = if cores <= 2 {
        HardwareTier::Low
    } else if cores <= 8 {
        HardwareTier::Mid
    } else {
        HardwareTier::High
    };
    
    let stagger_ms = match tier {
        HardwareTier::Low => 100,
        HardwareTier::Mid => 50,
        HardwareTier::High => 0,
    };
    
    let workers = match tier {
        HardwareTier::Low => cores,
        HardwareTier::Mid => cores,
        HardwareTier::High => (cores as f64 * 1.5) as usize,
    };
    
    (tier, workers, stagger_ms)
}

pub(crate) fn run_tests_with_paths(
    paths_to_use: &[String],
    watch_mode: bool,
    env: &RuntimePythonEnv,
    out: &OutputOptions,
) {
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

    // Detectar perfil de hardware para orquestación inteligente
    let (tier, detected_workers, stagger_ms) = detect_hardware_profile();
    let tier_name = match tier {
        HardwareTier::Low => "Low",
        HardwareTier::Mid => "Mid", 
        HardwareTier::High => "High",
    };

    if out.mode == OutputMode::Verbose {
        println!(
            "\n[Hardware] Detected: {} tier, {} workers, {}ms stagger",
            tier_name, detected_workers, stagger_ms
        );
        println!(
            "[Config]: workers={}",
            config.execution.max_workers.unwrap_or(detected_workers)
        );
        println!("\n{} {}", "📁".cyan().bold(), "Test directories:".bold());
        for p in paths_to_use {
            println!("   - {}", p);
        }
    }

    if out.mode == OutputMode::Verbose {
        println!("Collecting tests...");
    }
    let test_items = match get_or_collect_tests(paths_to_use, env) {
        Ok(items) => items,
        Err(e) => {
            let msg = format!("Failed to collect tests: {}", e);
            let _ = emit_error(out, &env.cwd, &msg);
            if !out.wants_json() {
                eprintln!("ERROR {}", msg);
            }
            return;
        }
    };

    let total_tests = test_items.len();
    if total_tests == 0 {
        let state = OutputState::new(out.clone(), 0);
        let _ = state.finalize(&env.cwd, None, None);
        return;
    }

    let mut state = OutputState::new(out.clone(), total_tests);

    // Usar workers detectados o el valor de config (config tiene prioridad si está seteado)
    let effective_workers = config.execution.max_workers.unwrap_or(detected_workers);
    let num_threads = effective_workers.min(total_tests);
    let (tx, rx) = channel();

    let chunks: Vec<_> = test_items
        .chunks((total_tests / num_threads).max(1))
        .collect();

    let handles: Vec<_> = chunks
        .iter()
        .enumerate()
        .map(|(worker_id, chunk)| {
            let tx = tx.clone();
            let chunk: Vec<serde_json::Value> = chunk.to_vec();
            let env = env.clone();
            let restart_interval = config.execution.worker_restart_interval;
            thread::spawn(move || {
                // Staggering dinámico según perfil de hardware detectado
                thread::sleep(std::time::Duration::from_millis(stagger_ms * worker_id as u64));
                
                let mut test_count = 0usize;
                for item in &chunk {
                    let path = item["path"].as_str().unwrap_or("");
                    let qual = item["qualname"].as_str().unwrap_or("");
                    let resolved = resolve_test_path(&env, path);
                    
                    // Batching: reiniciar worker cada N tests para liberar Pagefile
                    if restart_interval > 0 && test_count > 0 && test_count % restart_interval == 0 {
                        if env.compat {
                            // En modo compat, no hay reinicio explícito (pytest maneja su propio proceso)
                        } else {
                            // Log de reinicio en modo verbose
                            eprintln!("[Worker {}] Restarting after {} tests (batching)", worker_id, test_count);
                        }
                    }
                    
                    let result = run_test_item(&env, path, qual, worker_id);
                    let _ = tx.send(TestEvent::Finished {
                        path: resolved,
                        result,
                    });
                    test_count += 1;
                }
            })
        })
        .collect();

    drop(tx);

    for ev in rx {
        state.push(ev);
    }

    for h in handles {
        let _ = h.join();
    }

    let failed_any = state.results().iter().any(|(_, r)| !r.passed);
    let report_path = if failed_any {
        generate_failure_report(state.results(), paths_to_use, &env.cwd)
    } else {
        None
    };

    let (_, failed) = state
        .finalize(&env.cwd, report_path.as_deref(), None)
        .unwrap_or((0, 1));

    if !watch_mode && failed > 0 {
        std::process::exit(1);
    }
}
