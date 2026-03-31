use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use turboplex::{load_config, python_config_effective, PythonConfig};
use walkdir::WalkDir;

pub(crate) const DEFAULT_MAX_DEPTH: usize = 5;

pub(crate) fn print_help() {
    println!(
        r#"
turboplex (tpx) - High Performance Test Engine

Usage:
    tpx                      # Auto-discover and run tests
    tpx mcp                  # Inicia el servidor MCP (stdio)
    tpx --path ./tests     # Run tests in specific directory
    tpx --watch            # Watch for file changes and re-run
    tpx --compat           # Ejecuta los tests vía pytest (fixtures/conftest)
    tpx --light            # Collect rápido sin cargar conftest.py (ideal para MCP/IDE)
    tpx --help            # Show this help

Options:
    --path, -p <dir>    Run tests in specific directory
    --watch, -w         Watch for file changes and re-run
    --compat            Delegar ejecución a pytest
    --light             Modo light: collect sin conftest (rápido, sin DB setup)
    --quiet             Modo silencioso (solo fallos + resumen)
    --verbose           Modo detallado (línea por test)
    --json              Emite un único JSON por stdout (sin logs)
    --out-json <path>   Escribe el JSON final a un archivo (backup del IDE)
    --help, -h          Show this help message

MCP Environment Variables:
    TPX_PYTHON_EXE              # Forzar Python específico (ej: C:\venv\Scripts\python.exe)
    TPX_MCP_DEBUG=1             # Activar debug tracing a stderr
    TPX_MCP_LIGHT_COLLECT=1     # Collect rápido sin cargar conftest.py (--confcutdir)
                                 # Ideal para proyectos donde conftest.py inicializa DB/migraciones
    TPX_MCP_STDOUT_MODE         # redirect (default) o failfast para JSON-RPC
    TPX_MCP_PYTEST_COLLECT_TIMEOUT_S   # Timeout collect (default: 120s)
    TPX_MCP_PYTEST_RUN_TIMEOUT_S         # Timeout run (default: 60s)

Examples:
    # Proyecto con conftest.py pesado (ej: crea tablas DB al importar)
    set TPX_MCP_LIGHT_COLLECT=1
    tpx --compat

    # IDE con MCP (Windsurf/Cursor) - configura en mcp_config.json:
    # "env": {{ "TPX_MCP_LIGHT_COLLECT": "1", "TPX_PYTHON_EXE": "..." }}

Aliases:
    turboplex          # Alias for tpx
"#
    );
}

pub(crate) fn run_mcp_server() -> i32 {
    let env = build_runtime_python_env(false, "mcp=1");
    let mut cmd = Command::new(&env.interpreter);
    cmd.current_dir(&env.cwd);
    cmd.arg("-m").arg("turboplex_py.mcp_server");
    cmd.env("PYTHONUNBUFFERED", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("PYTHONUTF8", "1");
    if let Some(pp) = &env.pythonpath {
        cmd.env("PYTHONPATH", pp);
    }
    let status = cmd
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status();

    match status {
        Ok(s) => s.code().unwrap_or(1),
        Err(e) => {
            eprintln!("ERROR: No se pudo iniciar el servidor MCP: {}", e);
            1
        }
    }
}

pub(crate) fn get_test_cache_dir(base_dir: &Path) -> PathBuf {
    base_dir.join(".turboplex_cache")
}

pub(crate) fn get_collected_tests_cache_path(base_dir: &Path) -> PathBuf {
    get_test_cache_dir(base_dir).join("collected_tests.json")
}

pub(crate) fn get_test_results_cache_dir(base_dir: &Path) -> PathBuf {
    get_test_cache_dir(base_dir).join("test_results")
}

pub(crate) fn compute_text_hash(text: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    hex::encode(hasher.finalize())
}

pub(crate) fn compute_file_hash(path: &Path) -> Option<String> {
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

pub(crate) fn get_test_files_hash(paths: &[PathBuf], extra_fingerprint: &str) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut hasher = DefaultHasher::new();
    for p in paths {
        if let Some(hash) = compute_file_hash(p) {
            hash.hash(&mut hasher);
        }
    }
    extra_fingerprint.hash(&mut hasher);
    format!("{:x}", hasher.finish())
}

#[derive(Clone)]
pub(crate) struct RuntimePythonEnv {
    pub(crate) interpreter: String,
    pub(crate) module: String,
    pub(crate) cwd: PathBuf,
    pub(crate) pythonpath: Option<String>,
    pub(crate) fingerprint: String,
    pub(crate) compat: bool,
}

fn is_root_marker_dir(dir: &Path) -> bool {
    dir.join("backend").is_dir()
        || dir.join("pytest.ini").is_file()
        || dir.join("pyproject.toml").is_file()
}

fn find_project_root(start: &Path) -> PathBuf {
    let mut current = start.to_path_buf();
    loop {
        if is_root_marker_dir(&current) {
            return current;
        }
        let parent = match current.parent() {
            Some(p) => p.to_path_buf(),
            None => return start.to_path_buf(),
        };
        if parent == current {
            return start.to_path_buf();
        }
        current = parent;
    }
}

fn pythonpath_candidates(root: &Path, py_cfg: &PythonConfig) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();

    if let Some(extra) = py_cfg.pythonpath.clone() {
        out.push(extra);
    }

    let backend = root.join("backend");
    if backend.is_dir() {
        out.push(backend.to_string_lossy().to_string());
        let backend_src = backend.join("src");
        if backend_src.is_dir() {
            out.push(backend_src.to_string_lossy().to_string());
        }
    } else {
        out.push(root.to_string_lossy().to_string());
        let src = root.join("src");
        if src.is_dir() {
            out.push(src.to_string_lossy().to_string());
        }
    }

    out
}

fn merge_pythonpath(paths: &[String]) -> Option<String> {
    let mut uniq: Vec<String> = Vec::new();
    for p in paths {
        if !p.trim().is_empty() && !uniq.contains(p) {
            uniq.push(p.clone());
        }
    }
    if uniq.is_empty() {
        return None;
    }
    let sep = if cfg!(windows) { ";" } else { ":" };
    Some(uniq.join(sep))
}

fn detect_python_version(
    interpreter: &str,
    cwd: &Path,
    pythonpath: Option<&str>,
) -> Option<String> {
    let mut cmd = Command::new(interpreter);
    cmd.current_dir(cwd);
    cmd.arg("-c");
    cmd.arg("import platform; print(platform.python_version())");
    if let Some(pp) = pythonpath {
        cmd.env("PYTHONPATH", pp);
    }
    let out = cmd.output().ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

fn detect_deps_hash(root: &Path) -> Option<String> {
    let candidates = [
        "poetry.lock",
        "uv.lock",
        "requirements.txt",
        "requirements-dev.txt",
    ];
    for name in candidates {
        let p = root.join(name);
        if p.is_file() {
            return compute_file_hash(&p);
        }
    }
    None
}

pub(crate) fn build_runtime_python_env(compat: bool, extra_flags: &str) -> RuntimePythonEnv {
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

    let py_cfg = python_config_effective(&config);

    // Jerarquía de intérpretes: Variable > Config > .venv Autodetect > default
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    
    // 1. Primero: TPX_PYTHON_EXE (variable de entorno)
    let interpreter = std::env::var("TPX_PYTHON_EXE")
        .and_then(|path| {
            if std::path::Path::new(&path).exists() {
                eprintln!("Using TPX_PYTHON_EXE: {}", path);
                Ok(path)
            } else {
                Err(std::env::VarError::NotPresent)
            }
        })
        // 2. Segundo: py_cfg.interpreter (configuración)
        .or_else(|_| {
            py_cfg.interpreter.as_ref().and_then(|path| {
                if std::path::Path::new(path).exists() {
                    eprintln!("Using config interpreter: {}", path);
                    Some(path.clone())
                } else {
                    None
                }
            }).ok_or(std::env::VarError::NotPresent)
        })
        // 3. Tercero: .venv autodetect
        .or_else(|_| {
            find_venv_python(&cwd).ok_or(std::env::VarError::NotPresent)
        })
        // 4. Default: "python"
        .unwrap_or_else(|_| {
            eprintln!("No specific Python found, using default: python");
            "python".to_string()
        });
    
    let module = py_cfg
        .module
        .clone()
        .unwrap_or_else(|| "turboplex_py".to_string());

    let proj_cwd = if let Some(proj) = py_cfg.project_path.clone() {
        PathBuf::from(proj)
    } else {
        cwd.clone()
    };
    let root = find_project_root(&proj_cwd);
    let pythonpath = merge_pythonpath(&pythonpath_candidates(&root, &py_cfg));
    let pyver = detect_python_version(&interpreter, &root, pythonpath.as_deref())
        .unwrap_or_else(|| "unknown".to_string());
    let deps = detect_deps_hash(&root).unwrap_or_else(|| "none".to_string());
    let pp = pythonpath.clone().unwrap_or_else(|| "none".to_string());
    let fingerprint_raw = format!(
        "py={};deps={};pp={};compat={};flags={}",
        pyver, deps, pp, compat, extra_flags
    );
    let fingerprint = compute_text_hash(&fingerprint_raw);

    RuntimePythonEnv {
        interpreter,
        module,
        cwd: root,
        pythonpath,
        fingerprint,
        compat,
    }
}

fn find_venv_python(start_dir: &Path) -> Option<String> {
    let venv_names = [".venv", "venv", ".env", "env"];
    
    // Check current dir and ancestors
    for dir in std::iter::once(start_dir).chain(start_dir.ancestors().take(4)) {
        for venv_name in &venv_names {
            let venv_path = dir.join(venv_name);
            if venv_path.is_dir() {
                let python_exe = if cfg!(windows) {
                    venv_path.join("Scripts").join("python.exe")
                } else {
                    venv_path.join("bin").join("python")
                };
                
                if python_exe.exists() {
                    eprintln!("Found venv Python: {}", python_exe.display());
                    return Some(python_exe.to_string_lossy().to_string());
                }
            }
        }
    }
    
    None
}

pub(crate) fn discover_test_paths_from(base_dir: &Path, max_depth: usize) -> Vec<PathBuf> {
    let mut paths = Vec::new();

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
        ".turboplex_cache",
    ];

    for entry in WalkDir::new(base_dir)
        .max_depth(max_depth)
        .follow_links(false)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        let path = entry.path();
        let should_skip = path.components().any(|c| {
            let component = c.as_os_str().to_string_lossy();
            exclude_patterns.iter().any(|p| {
                if let Some(stripped) = p.strip_prefix('*') {
                    component.ends_with(stripped)
                } else {
                    component == *p
                }
            })
        });
        if should_skip {
            continue;
        }
        if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
            if (name.starts_with("test_") || name.ends_with("_test.py")) && name.ends_with(".py") {
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
