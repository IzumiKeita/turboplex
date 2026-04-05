# TurboPlex Flags: Inventario y Smoke Tests

Fecha: 2026-04-03
Version: v0.3.3

## Objetivo
Documentar todas las flags (CLI, variables de entorno, configuracion TOML y feature flags), su ubicacion en codigo y el resultado de smoke tests ejecutados.

## Metodologia resumida
- Build release: `cargo build --release`
- Smoke tests CLI y env vars usando `tpx` y `python -c` para confirmar lectura de variables.
- Validacion de defaults directamente en codigo.

## CLI Flags (Rust)
Ubicacion principal: `src/main/mod.rs` y help en `src/main/part1.rs`.

| Flag | Uso | Default | Lectura/efecto | Smoke test |
| --- | --- | --- | --- | --- |
| --help, -h | Muestra ayuda | N/A | `print_help()` | OK (salida de ayuda) |
| --path, -p | Ruta de tests | auto discovery | construye `test_paths` | OK (tests ejecutados) |
| --watch, -w | Watch mode | off | `watch_mode` | NO (requiere proceso persistente) |
| --compat | Ejecuta via pytest batch | off | `compat=true` | NO (depende pytest/DB) |
| --compat-session | Alias de --compat | off | `compat=true` | NO |
| --compat-per-test | Pytest por test | off | `compat_per_test=true` | NO (depende pytest/DB) |
| --light | Activa `TPX_MCP_LIGHT_COLLECT` | off | set env var | Validado por env var (ver MCP) |
| --quiet | Modo silencioso | on si no verbose/json | `OutputMode::Quiet` | OK (`tpx --quiet`) |
| --verbose | Modo detallado | off | `OutputMode::Verbose` | OK (`tpx --verbose`) |
| --json | Emite JSON por stdout | off | `OutputMode::Json` | OK (emision JSON) |
| --out-json <path> | JSON a archivo | none | `out_json` | OK (archivo generado) |
| --workers, -j | Numero de workers | none | set `TPX_WORKERS` | OK (`--workers 1`) |
| --analyze | Analiza turboplex_full_report.json | off | modo analyze | NO (requiere jsonl previo) |

## Variables de entorno (Rust)

| Variable | Uso | Default | Lectura/efecto | Smoke test |
| --- | --- | --- | --- | --- |
| TPX_WORKERS | Override workers | config/4 | `run_tests_with_paths` | OK (set via `--workers`) |
| TPX_PYTHON_EXE | Fuerza interprete | auto | `build_runtime_python_env` | OK (apunta a venv) |

## MCP / Python env vars
Ubicacion: `turboplex_py/mcp/*.py`.

| Variable | Uso | Default | Lectura/efecto | Smoke test |
| --- | --- | --- | --- | --- |
| TPX_MCP_DEBUG | Debug a stderr | off | `_debug_log` | OK (imprime log) |
| TPX_MCP_LIGHT_COLLECT | `--noconftest` | off | `_build_pytest_cmd` | OK (incluye `--noconftest`) |
| TPX_MCP_STDOUT_MODE | redirect/failfast | redirect | `StdoutJsonRpcGuard` | OK (bloquea stdout) |
| TPX_MCP_PYTEST_COLLECT_TIMEOUT_S | timeout collect | 120 | `load_mcp_config` | OK |
| TPX_MCP_PYTEST_RUN_TIMEOUT_S | timeout run | 60 | `load_mcp_config` | OK |
| TPX_MCP_TURBOPLEX_COLLECT_TIMEOUT_S | timeout collect | 120 | `load_mcp_config` | OK |
| TPX_MCP_TURBOPLEX_RUN_TIMEOUT_S | timeout run | 60 | `load_mcp_config` | OK |
| TPX_MCP_TEST_TIMEOUT_S | timeout test | 120 | `load_mcp_config` | OK |
| TPX_MCP_HEARTBEAT_S | heartbeat | 1.0 | `load_mcp_config` | OK |
| TPX_MCP_TERMINATE_GRACE_S | grace | 2.0 | `load_mcp_config` | OK |
| TPX_MCP_DRAIN_MAX_CHARS | max drain | 2000000 | `load_mcp_config` | OK |
| TPX_MCP_LOGS_MAX_CHARS | max logs | 20000 | `load_mcp_config` | OK |

## DB env vars (Python)
Ubicacion: `turboplex_py/db/fixtures.py`.

| Variable | Uso | Default | Smoke test |
| --- | --- | --- | --- |
| TPX_DB_TIMEOUT_SQLITE | Timeout sqlite | 2.0 | OK |
| TPX_DB_TIMEOUT_POSTGRES | Timeout postgres | 10.0 | OK |
| TPX_DB_TIMEOUT_MYSQL | Timeout mysql | 10.0 | OK |
| TPX_DB_TIMEOUT_MSSQL | Timeout mssql | 10.0 | OK |
| TPX_DB_TIMEOUT_ORACLE | Timeout oracle | 10.0 | OK |
| TPX_DB_DEFAULT | DB default | sqlite | OK |
| TPX_DB_STRICT_DIRTY | Fail on dirty | 0 | OK |
| TPX_DB_METRICS_ENABLED | metrics | 1 | OK |
| TPX_DB_DIRTY_TRACK_MAX_TABLES | max tables | 12 | OK |
| TPX_DB_ISOLATION_MODE | isolation mode | auto | OK |
| TPX_DB_WORKER_PREFIX | prefix | tpx_w | OK |
| TPX_WORKER_ID | worker id | fallback | NO (no forzado) |
| TPX_WORKER_INDEX | worker index | fallback | NO (no forzado) |

## Configuracion TOML (turbo_config.toml)
Ubicacion: `src/test_runner/config.rs`.

Defaults principales:
- execution.max_workers: num_cpus
- execution.use_tokio: false
- execution.default_timeout_ms: 30000
- execution.parallel_suites: true
- execution.cache_dir: .turbocache
- execution.cache_enabled: true
- execution.worker_restart_interval: 50
- reporting.verbose: true
- reporting.show_duration: true
- python.enabled: false (si `python` es None)
- python.interpreter/module/test_paths/pythonpath/project_path: None/[]

Smoke test: NO (no existe `turbo_config.toml` en repo durante esta corrida).

## Feature Flags (Cargo)
Ubicacion: `Cargo.toml`.

| Feature | Uso | Default | Smoke test |
| --- | --- | --- | --- |
| debug-logging | Logs debug condicionales | off | OK (build con `--features debug-logging`) |

## Evidencia (comandos ejecutados)
- `tpx --quiet --path .\tests\benchmark\test_bench_0010.py --out-json .\tplex_smoke_quiet.json`
- `tpx --verbose --path .\tests\benchmark\test_bench_0010.py --out-json .\tplex_smoke_verbose.json`
- `tpx --workers 1 --path .\tests\benchmark\test_bench_0010.py --out-json .\tplex_smoke_workers_flag.json`
- `TPX_RUNNER_LIGHT=1 tpx --path .\tests\benchmark\test_bench_0010.py --out-json .\tplex_smoke_runner_light.json`
- `TPX_PYTHON_EXE=<venv> tpx --path .\tests\benchmark\test_bench_0010.py --out-json .\tplex_smoke_python_exe.json`
- `TPX_MCP_LIGHT_COLLECT=1 python -c "from turboplex_py.mcp.collect import _build_pytest_cmd; print(...)"`
- `TPX_MCP_DEBUG=1 python -c "from turboplex_py.mcp.collect import _debug_log; _debug_log(...)"`
- `TPX_MCP_STDOUT_MODE=failfast python -c "... install_stdio_guard ..."`
- `TPX_MCP_* python -c "from turboplex_py.mcp.config import load_mcp_config; print(load_mcp_config())"`
- `TPX_DB_* python -c "from turboplex_py.db import fixtures; print(...)"`

## Observaciones y limitaciones
- Los tests de `tests/benchmark/test_bench_0010.py` fallaron por dependencias externas (DB/MySQL), pero los flags se leyeron correctamente.
- `--watch` y `--analyze` no se ejecutaron en smoke tests por requerir flujo persistente o artefactos previos.
- `--compat`/`--compat-per-test` requieren pytest y DB en condiciones estables; no se ejecutaron en esta corrida.
