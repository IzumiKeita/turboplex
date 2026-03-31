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

TurboPlex generates machine-readable artifacts:

- `.tplex_report.json` and timestamped `.tplex_report_%Y%m%d_%H%M%S.json`
- `turboplex_full_report.json` (JSONL) with richer error context for large suites

## Cache

Cache lives in `.turboplex_cache/` and is invalidated when:
- test files change (hash)
- runtime fingerprint changes (Python version, deps hash, PYTHONPATH, flags)

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
- `TPX_MCP_TURBOPLEX_COLLECT_TIMEOUT_S` (default 120)
- `TPX_MCP_TURBOPLEX_RUN_TIMEOUT_S` (default 60)
- `TPX_MCP_PYTEST_COLLECT_TIMEOUT_S` / `TPX_PYTEST_COLLECT_TIMEOUT_S` (default 120)
- `TPX_MCP_PYTEST_RUN_TIMEOUT_S` / `TPX_PYTEST_RUN_TIMEOUT_S` (default 60)

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

Common issues:

- “Fixture not found” in native mode
  - Use `--compat` if you rely on complex pytest fixtures/plugins
- Slow discovery
  - Use `--light` (or `TPX_MCP_LIGHT_COLLECT=1`) to avoid heavy conftest imports
- “ModuleNotFoundError” during run
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
