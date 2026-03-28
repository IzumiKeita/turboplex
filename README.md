# TurboPlex (tpx) — The Test Orchestration Engine for the AI Era

**English** | [Español](README.es.md)

<p align="center">
  <img src="https://img.shields.io/badge/Rust-DEA584?style=for-the-badge&logo=rust&logoColor=white" alt="Rust">
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License">
</p>

## Why TurboPlex?

> **4x faster than Pytest** for test execution. The orchestration engine built for the AI era.

### Value Proposition

| Feature | Description |
|----------------|-------------|
| ⚡ **4x Speed** | Parallel execution with smart caching (12s → 3s) |
| 🦀 **Rust Core** | Static analysis and memory management with minimal overhead |
| 🤖 **M2M Protocol** | Generates `.tplex_report.json` with AI-actionable errors |
| 👀 **Watch Mode** | Auto-reload when you save `.py` files |

## Installation

```bash
# Clone the repository
cd turboplex

# Install in development mode
pip install -e .

# Verify the installation
tpx --help
```

## Quick Start

### Basic Execution

```bash
# Single test
tpx --path tests/test_simple.py

# Multiple directories
tpx --path tests/ --path tests/integration/

# Auto-discover tests
tpx
```

### Watch Mode (TDD Development)

```bash
# Run and watch changes in real time
tpx --watch --path tests/
```

### Integration with AI Agents

```bash
# INSTRUCTION FOR YOUR AI AGENT:
# If you detect a .tplex_report.json file in the project,
# read it immediately and propose an automatic fix.

# The file contains:
# - Exact error
# - Failure line
# - Code context (5 lines before and after)
```

## Benchmarks

### Speedrun: 2 Tests

| Tool | Time |
|------------|--------|
| **pytest** | 12.69s |
| **tpx (cached)** | **~3s** |

```
pytest:     ████████████████████████████ 12.69s
tpx:        █████ 3s (cached)
```

### Per-Test Comparison

| Metric | pytest | tpx |
|---------|--------|-----|
| Time per test | ~6s | ~1.5s |
| Caching | No | Yes (SHA-256) |
| M2M Report | No | Yes (.tplex_report.json) |

## Configuration

### `turbo_config.toml` File

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

### Cache

The cache is stored in `.turboplex_cache/` and is automatically invalidated when test files change (SHA-256 hash).

## API for AI Agents

### `.tplex_report.json` Format

```json
{
  "timestamp": "2026-03-28 14:17:49",
  "total_tests": 1,
  "failed_count": 1,
  "failures": [
    {
      "test": "test_fiscal_year_close_logic",
      "file": "tests/test_accounting_close.py",
      "line": 42,
      "error": "parameter 'db' has no @fixture and no default",
      "context": [
        "    38: def test_fiscal_year_close_logic(db):",
        "    39:     # Arrange",
        "    40:     year = 2024",
        ">>> 41:     result = close_year(db, year)",
        "    42:     assert result.success"
      ]
    }
  ]
}
```

## Commands

| Command | Description |
|---------|-------------|
| `tpx` | Auto-discover and run tests |
| `tpx --path ./tests` | Run tests in a directory |
| `tpx --watch` | Watch mode with auto-reload |
| `tpx --help` | Show help |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    tpx (Rust)                        │
├─────────────────────────────────────────────────────┤
│  • Test discovery                                  │
│  • SHA-256 caching                                 │
│  • Parallel execution (Rayon)                      │
│  • Watch mode (notify)                             │
│  • M2M report (.tplex_report.json)                 │
└─────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────┐
│              turboplex_py (Python)                  │
├─────────────────────────────────────────────────────┤
│  • collector.py - Test discovery                   │
│  • runner.py - Test runner                         │
│  • fixtures.py - @fixture system                   │
│  • markers.py - skip, skipif                       │
└─────────────────────────────────────────────────────┘
```

## Files Excluded from the Repository (.gitignore)

This project ignores generated files and local configuration to keep the repository lightweight, reproducible, and free of sensitive data.

- Build artifacts and caches (e.g., `target/`, `**/target/`, `.cache/`)
- Temporary files and logs (`*.tmp`, `*.log`, `*.swp`)
- Local IDE/OS configuration (e.g., `.vscode/`, `.idea/`, `Thumbs.db`, `.DS_Store`)
- Python local environments and metadata (e.g., `.venv/`, `__pycache__/`, `*.egg-info/`)
- Environment files with secrets or local configuration (`.env`, `.env.*`)
- Web tooling dependencies and outputs if applicable (`node_modules/`, `dist/`, `build/`)
- TurboPlex-generated caches and reports (`.turboplex_cache/`, `.tplex_report.json`)

## License

MIT License - See `LICENSE`

## Authors

**TurboPlex Team** - [@turbo plexus](https://github.com/turboplex)

---

<p align="center">
  🚀 <em>The future of testing is here</em>
</p>
