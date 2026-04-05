# TurboPlex (tpx) — Fast Test Orchestration for Python (Rust core)

**English** | [Español](README.es.md)

<p align="center">
  <img src="https://img.shields.io/badge/Rust-DEA584?style=for-the-badge&logo=rust&logoColor=white" alt="Rust">
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License">
</p>

TurboPlex is a hybrid **Rust + Python** test runner that accelerates large suites with:
- fast test discovery
- parallel execution
- SHA-based caching
- structured JSON reports for IDEs and AI agents

## TL;DR

```bash
pip install turboplex
tpx --path tests/
```

## Contents

- [Why TurboPlex](#why-turboplex)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Modes](#modes)
- [Windows / venv (TPX_PYTHON_EXE)](#windows--venv-tpx_python_exe)
- [Skipping tests (pytest.skip)](#skipping-tests-pytestskip)
- [Reports](#reports)
- [Cache](#cache)
- [MCP server (IDE integration)](#mcp-server-ide-integration)
- [Configuration](#configuration)
- [Benchmarks](#benchmarks)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [License](#license)

## Why TurboPlex?

TurboPlex focuses on making “run tests” fast and machine-consumable:

| Feature | What you get |
|---|---|
| Fast discovery | AST-based collection avoids heavy imports where possible |
| Parallel execution | Isolated Python subprocess per test (or pytest in compat mode) |
| Smart caching | Cache pass results keyed by file hash + runtime fingerprint |
| Structured output | Stable JSON schema for tooling, MCP, and analysis |
| Watch mode | Rerun suite on file save |

## Installation

From PyPI:

```bash
pip install turboplex
tpx --help
```

From source (dev):

```bash
git clone https://github.com/IzumiKeita/turboplex.git
cd turboplex
pip install -e .
tpx --help
```

## Quick Start

Run a directory:

```bash
tpx --path tests/
```

Run multiple paths:

```bash
tpx --path tests/ --path tests/integration/
```

Auto-discover:

```bash
tpx
```

Watch mode:

```bash
tpx --watch --path tests/
```

## Modes

### Native mode (default)

Best for suites where:
- imports can be expensive (DB bootstraps, migrations, Pydantic config)
- you want faster iteration with caching and structured results

### Pytest compatibility mode (`--compat`)

Use this when you need full pytest semantics/plugins:

```bash
tpx --compat --path tests/
```

### Unittest mode (`--unittest`)

Runs `unittest.TestCase` suites via the adapter layer:

```bash
tpx --unittest --path tests/
```

Notes:
- Not compatible with `--compat` (choose one execution mode).
- Each `TestCase::test_*` is executed with transactional DB isolation (rollback in `finally`).

### Behave mode (`--behave`)

Runs BDD `.feature` files via the adapter layer (requires `behave` installed in the selected Python env):

```bash
tpx --behave --path features/
```

Notes:
- Not compatible with `--compat` (choose one execution mode).
- Execution runs under the same transactional DB isolation and buffered logging as native runs.

### Light collect (`--light`)

Skips `conftest.py` import during discovery. Useful when `conftest.py` performs heavy work on import.

```bash
tpx --compat --light
```

Environment alternative:

```bash
export TPX_MCP_LIGHT_COLLECT=1  # Linux/Mac
set TPX_MCP_LIGHT_COLLECT=1     # Windows
```

## Windows / venv (TPX_PYTHON_EXE)

TurboPlex resolves the Python interpreter using this precedence:
1. `TPX_PYTHON_EXE` (if set and path exists)
2. `python.interpreter` from `turbo_config.toml` (if set and path exists)
3. Auto-detected `.venv/venv/.env/env` near the project
4. System `python`

Example (force a secondary venv):

```powershell
$env:TPX_PYTHON_EXE = (Resolve-Path .\.venv_alt\Scripts\python.exe).Path
tpx --path tests/
```

CLI prints a confirmation line:
- `Using TPX_PYTHON_EXE: C:\path\to\.venv_alt\Scripts\python.exe`

## Skipping tests (pytest.skip)

In native mode, `pytest.skip(...)` is recognized and reported as a skip (not a failure):
- CLI prints `SKIP ...` lines
- summary includes `skipped`
- JSON includes `skipped: true` and `skip_reason`

Example summary:

```
Results: 120 passed, 0 failed, 5 skipped (24000ms)
```

## Reports

TurboPlex generates machine-readable artifacts in `.tplex/`:

- `.tplex/reports/report_%Y%m%d_%H%M%S.json` — timestamped JSON reports (20-file rotation)
- `.tplex/reports/report_latest.json` — symlink to most recent report
- `.tplex/failures/failures_%Y%m%d_%H%M%S.md` — categorized failure reports (20-file rotation)
- `tplex_last_run.log` — single contact point in project root
- `.tplex/logs/tpx_mcp_session.log` — buffered MCP/SSG/runner logs (async flush)

All writes are atomic (temp + rename) to prevent corruption.

## Cache

Cache lives in `.tplex/cache/` and is invalidated when:
- test files change (SHA256 hash)
- runtime fingerprint changes (Python version, deps hash, PYTHONPATH, flags)

Environment fingerprinting includes `TPX_DB_*` variables for DB-aware invalidation.

## MCP server (IDE integration)

Start the MCP server:

```bash
tpx mcp
```

Notes:
- `--out-json` avoids stdout pollution for JSON-RPC toolchains.
- subprocesses force UTF-8 (`PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`, `PYTHONUNBUFFERED=1`).

Common MCP env vars:
- `TPX_PYTHON_EXE`
- `TPX_MCP_LIGHT_COLLECT=1`
- `TPX_MCP_DEBUG=1`
- `TPX_MCP_STDOUT_MODE=redirect|failfast`
- `TPX_MCP_TEST_TIMEOUT_S` (default 120)
- `TPX_MCP_TURBOPLEX_COLLECT_TIMEOUT_S` (default 120)
- `TPX_MCP_TURBOPLEX_RUN_TIMEOUT_S` (default 60)
- `TPX_MCP_PYTEST_COLLECT_TIMEOUT_S` / `TPX_PYTEST_COLLECT_TIMEOUT_S` (default 120)
- `TPX_MCP_PYTEST_RUN_TIMEOUT_S` / `TPX_PYTEST_RUN_TIMEOUT_S` (default 60)
- `TPX_MCP_HEARTBEAT_S` (default 1)
- `TPX_MCP_TERMINATE_GRACE_S` (default 2)
- `TPX_MCP_DRAIN_MAX_CHARS` (default 2000000)
- `TPX_MCP_LOGS_MAX_CHARS` (default 20000)

Error contract (`ok=false`):
- `data.error.code` in `timeout | subprocess_failed | invalid_input | not_found | internal_error | SCHEMA_SYNC_BLOCKED | HEALTH_CHECK_FAILED`
- `data.error.message` is always human-readable
- `data.error.details` may include `phase`, `returncode`, `timeout_s`, and truncated stderr/stdout metadata

DB guardrails (v0.3.6):
- Schema Sync Guard (SSG) blocks execution when Alembic head != DB version (`SCHEMA_SYNC_BLOCKED`).
- Pre-flight health checks return a stable error code (`HEALTH_CHECK_FAILED`) instead of ambiguous internal errors.

Tool response shape (`discover`, `run`, `get_report`):
- top-level: `schemaVersion`, `tool`, `ok`, `runId`, `mode`, `summary`, `logs`, `data`
- `run.summary` also includes `workers_used`, `timeouts`, `subprocess_failures`
- DB-first additions:
  - `data.results[].db_metrics.write_count`
  - `data.results[].db_dirty`
  - `data.results[].db_dirty_summary`
  - `run.summary.db_write_count_total`
  - `run.summary.db_dirty_tests`

Integration coverage added today:
- `tests/test_mcp_db_integration.py` validates MCP `run` DB metrics with SQLite writes.
- strict dirty policy verified:
  - `TPX_DB_STRICT_DIRTY=0` -> run can pass while reporting `db_dirty`.
  - `TPX_DB_STRICT_DIRTY=1` -> run fails with `db_error.code=db_dirty_state`.
- subprocess-only variant added with `xfail` guard on Windows for occasional native crash `0xC0000005` (Access Violation).

DB hardening env vars:
- `TPX_DB_STRICT_DIRTY=0|1` (default `0`, fail test only when dirty + strict enabled)
- `TPX_DB_METRICS_ENABLED=0|1` (default `1`)
- `TPX_DB_ISOLATION_MODE=auto|schema|database|transaction` (default `auto`)
- `TPX_DB_WORKER_PREFIX=tpx_w`
- `TPX_DB_DIRTY_TRACK_MAX_TABLES=12`

Example MCP config:

```json
{
  "mcpServers": {
    "tpx": {
      "command": "tpx",
      "args": ["mcp"],
      "env": {
        "TPX_PYTHON_EXE": "/path/to/venv/bin/python",
        "TPX_MCP_LIGHT_COLLECT": "1",
        "TPX_MCP_DEBUG": "1"
      }
    }
  }
}
```

## Configuration

`turbo_config.toml` example:

```toml
[execution]
max_workers = 8
default_timeout_ms = 30000
cache_enabled = true

[python]
enabled = true
interpreter = "python"
module = "turboplex_py"
test_paths = ["tests"]
project_path = "."
```

## Benchmarks

Production suite (~200 tests):

| Tool | Time | per test |
|---|---:|---:|
| pytest | ~340s | ~1.7s |
| tpx (cold) | ~180s | ~0.9s |
| tpx (cached) | ~25s | ~0.13s |

## Troubleshooting

TurboPlex includes the **TurboGuide** system to help diagnose issues automatically:

### Built-in Diagnostics

| Command | Purpose |
|---------|---------|
| `tpx --doctor` | Run full project health check |
| `tpx --doctor --json` | Emit a machine-readable doctor report (CI/IDE friendly) |
| `tpx --doctor --fail-on-warn` | Exit non-zero if warnings are present (strict CI) |
| MCP `doctor` tool | Runs `tpx --doctor --json` via MCP and returns the structured report |
| `tpx --light` | Skip heavy conftest.py imports (fast mode) |
| `tpx --compat` | Full pytest compatibility mode |

The `--doctor` command analyzes:
- **Infrastructure**: `.tplex/` directory health and permissions
- **Performance**: Detects heavy conftest.py files (>50KB)
- **Compatibility**: Parses recent JSON reports for fixture / SSG / health issues
- **Integrity**: Detects interrupted atomic writes across `.tplex/*` (reports/cache/failures/logs)
- **Runtime**: Verifies Python import readiness (including behave when using `--behave`)
- **DB Probe**: Optional TCP reachability check when `DATABASE_URL` is set

### Common Issues

- **"Fixture not found"** or **fixture errors** in native mode
  - TurboPlex will show ` TURBO_FIX` with specific guidance
  - Use `--compat` if you rely on complex pytest fixtures/plugins
  - Run `tpx --doctor` to diagnose fixture compatibility issues

- **Slow discovery** (>1000ms conftest.py load time)
  - TurboPlex will show ` TURBO_GUIDE` with lazy imports example
  - Use `--light` (or `TPX_MCP_LIGHT_COLLECT=1`) to skip heavy imports
  - Run `tpx --doctor` to detect large conftest.py files

- **"ModuleNotFoundError"** during run
  - Ensure your project is on `PYTHONPATH` or configured via `turbo_config.toml`

## Development

Build wheels with maturin:

```bash
python -m pip install maturin
python -m maturin build --release -o dist
python -m maturin sdist -o dist
```

## License

MIT License — see `LICENSE`
