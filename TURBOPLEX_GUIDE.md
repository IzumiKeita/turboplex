# TurboPlex - Complete Implementation Guide

> **TurboPlex** is a hybrid Rust+Python test runner designed to execute Pytest tests faster through parallel execution, smart caching, and native compatibility with database fixtures.

---

## Table of Contents

1. [TurboPlex Architecture](#1-turboplex-architecture)
2. [Core Components](#2-core-components)
3. [Installation & Setup](#3-installation--setup)
4. [The Pydantic Problem & Collection Phase](#4-the-pydantic-problem--collection-phase)
5. [The Solution: Hybrid conftest.py with Lazy Imports](#5-the-solution-hybrid-conftestpy-with-lazy-imports)
6. [Step-by-Step Implementation](#6-step-by-step-implementation)
7. [Common Errors & Solutions](#7-common-errors--solutions)
8. [Advanced Troubleshooting](#8-advanced-troubleshooting)
9. [Best Practices](#9-best-practices)
10. [Comparison: TurboPlex vs Pytest](#10-comparison-turboplex-vs-pytest)
11. [Roadmap & Future Improvements](#11-roadmap--future-improvements)

---

## 1. TurboPlex Architecture

### 1.1 Overview

TurboPlex operates in three distinct phases:

```
┌─────────────────────────────────────────────────────────────┐
│                    PHASE 1: COLLECTION                       │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │  Rust tpx    │───→│Python Collector│───→│  JSON Cache  │   │
│  │   (main)     │    │ (turboplex_py)│    │              │   │
│  └──────────────┘    └──────────────┘    └──────────────┘   │
│         │                                              │     │
│         │         Discovers tests without running      │     │
│         │         Uses AST parsing + lazy imports      │     │
└─────────┼──────────────────────────────────────────────┼─────┘
          │                                              │
          ▼                                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    PHASE 2: EXECUTION                        │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │  Rust tpx    │───→│  Python Runner │───→│  subprocess  │   │
│  │  (parallel)  │    │ (turboplex_py)│    │  per test    │   │
│  └──────────────┘    └──────────────┘    └──────────────┘   │
│         │                                              │     │
│         │         Executes tests in parallel           │     │
│         │         Manages fixtures per test            │     │
└─────────┼──────────────────────────────────────────────┼─────┘
          │                                              │
          ▼                                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    PHASE 3: REPORTING                        │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │   Results    │───→│  Result Cache │───→│  JSON Output │   │
│  │   Aggregated │    │               │    │  or terminal │   │
│  └──────────────┘    └──────────────┘    └──────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 Data Flow

1. **Collection Phase**: The Python collector scans test files using AST parsing to find test functions without running heavy imports.

2. **Caching**: Discovered tests are stored in `.turboplex_cache/collected_tests.json` with a hash of file contents.

3. **Parallel Execution**: Each test runs in a separate Python process, enabling parallelization and isolation.

4. **Fixture Management**: TurboPlex manages fixtures natively, supporting `scope`, `autouse`, and fixture dependencies.

---

## 2. Core Components

### 2.1 Rust Components (`tpx` executable)

| File | Purpose |
|---------|-----------|
| `src/main/mod.rs` | CLI entry point |
| `src/main/part1.rs` | Utilities and help |
| `src/main/part2.rs` | `get_or_collect_tests()` - collection and cache logic |
| `src/test_runner/python.rs` | `run_python_test()` - individual test execution |
| `src/indexer/mod.rs` | AI Analysis module (`--analyze` command) |

### 2.2 Python Components (`turboplex_py` module)

| File | Purpose |
|---------|-----------|
| `turboplex_py/__main__.py` | CLI entry point |
| `turboplex_py/collector.py` | Test discovery via AST |
| `turboplex_py/runner.py` | Test runner with enriched JSON output |
| `turboplex_py/pytest_bridge.py` | Pytest ↔ TurboPlex fixture bridge |
| `turboplex_py/fixtures.py` | `@fixture` decorator compatible with pytest |
| `turboplex_py/db_lazy_patcher.py` | SQLAlchemy lazy loading patcher |

### 2.3 Critical Environment Variables

```bash
# TurboPlex mode (activates hybrid conftest)
export TURBOPLEX_MODE=1

# Debug mode (shows internal execution)
export RUST_LOG=debug

# Disable SQLAlchemy warnings
export SQLALCHEMY_SILENCE_UBER_WARNING=1
```

---

## 3. Installation & Setup

### 3.1 Requirements

- Python 3.8+
- Rust toolchain (for building from source)
- pytest 7.0+ (optional, for `--compat` mode)

### 3.2 Installation

```bash
# From PyPI
pip install turboplex

# Verify installation
tpx --help
```

### 3.3 Basic Usage

```bash
# Run a single test
tpx --path tests/test_simple.py

# Run multiple directories
tpx --path tests/ --path tests/integration/

# Auto-discover and run tests
tpx

# Watch mode
tpx --watch --path tests/

# AI Analysis of test failures
tpx --analyze
```

---

## 4. The Pydantic Problem & Collection Phase

### 4.1 The Problem

When importing Pydantic models during test discovery, the process can fail due to:
- Missing environment variables
- Database connections required at import time
- Circular dependencies

### 4.2 The TurboPlex Solution

Uses **AST parsing** instead of actual imports:

```python
# collector.py - discovers tests without importing
import ast

def discover_tests(file_path):
    with open(file_path) as f:
        tree = ast.parse(f.read())
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name.startswith('test_'):
                yield node.name  # Found test without importing!
```

---

## 5. The Solution: Hybrid conftest.py with Lazy Imports

### 5.1 conftest.py Structure

```python
import os

if os.getenv('TURBOPLEX_MODE'):
    # TurboPlex mode - use lazy imports
    from turboplex_py.db_lazy_patcher import patch_sqlalchemy
    patch_sqlalchemy()
else:
    # Standard pytest mode - normal imports
    import pytest
    from myapp.database import db_session
```

### 5.2 Database Fixtures

```python
# conftest.py
import os
import pytest

if os.getenv('TURBOPLEX_MODE'):
    from turboplex_py.db_fixtures import db, client
else:
    @pytest.fixture
    def db():
        from myapp.database import get_db
        return get_db()
```

---

## 6. Step-by-Step Implementation

### 6.1 Project Structure

```
your-project/
├── backend/
│   ├── tests/
│   │   ├── conftest.py          # Hybrid conftest
│   │   ├── test_api.py
│   │   └── test_models.py
│   └── src/
├── .turboplex_cache/            # Auto-generated
└── turboplex_full_report.json   # Generated on test run
```

### 6.2 Initial Setup

```bash
# 1. Install TurboPlex
pip install turboplex

# 2. Copy hybrid conftest
cp /path/to/turboplex/examples/conftest_hybrid.py backend/tests/conftest.py

# 3. Run tests
cd backend && tpx --path tests/
```

---

## 7. Common Errors & Solutions

### 7.1 "Fixture not found"

**Error:**
```
parameter 'db' has no @fixture and no default
```

**Solution:**
Ensure the fixture is defined in conftest.py with lazy import:

```python
import os

if os.getenv('TURBOPLEX_MODE'):
    from turboplex_py.db_fixtures import db
else:
    import pytest
    
    @pytest.fixture
    def db():
        from myapp.database import get_db
        return get_db()
```

### 7.2 "ModuleNotFoundError during collection"

**Error:**
```
ModuleNotFoundError: No module named 'myapp'
```

**Solution:**
Add project path to PYTHONPATH:

```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)/backend/src"
tpx --path tests/
```

### 7.3 "Database connection failed"

**Error:**
```
sqlalchemy.exc.OperationalError: could not connect to server
```

**Solution:**
Use lazy database patching:

```python
# conftest.py
import os

if os.getenv('TURBOPLEX_MODE'):
    from turboplex_py.db_lazy_patcher import patch_sqlalchemy
    patch_sqlalchemy()
```

---

## 8. Advanced Troubleshooting

### 8.1 Enable Debug Mode

```bash
export RUST_LOG=debug
tpx --path tests/ 2>&1 | tee debug.log
```

### 8.2 Check Cache Validity

```bash
# Clear cache and re-collect
rm -rf .turboplex_cache/
tpx --path tests/
```

### 8.3 Compare with pytest

```bash
# Run with pytest for comparison
pytest tests/test_failing.py -v

# Run with tpx
tpx --path tests/test_failing.py 2>&1 | head -100
```

### 8.4 Using --analyze for AI Debugging

```bash
# Run tests and generate full report
tpx --path tests/

# Analyze failures
tpx --analyze
```

Output:
```
════════════════════════════════════════════════════════════
          TurboPlex Analysis Report
════════════════════════════════════════════════════════════

📊 Summary
   Total:  199
   Passed: 35
   Failed: 164
   Rate:   17.6%

🚨 Critical Issues
   • 45 tests have database connectivity issues
   • 12 tests have import errors

📋 Error Categories
   [45] AuthError: Expected 200 got 403
   [32] DatabaseError: Unique constraint violation
   ...

💡 Top Recommendations
   1. Priority 45: Check authentication fixtures
   2. Priority 32: Implement database cleanup between tests
```

---

## 9. Best Practices

### 9.1 Test Organization

- Keep test files with `test_` prefix
- Use `_test.py` suffix for discovery
- Organize tests by module: `tests/unit/`, `tests/integration/`

### 9.2 Fixture Management

- Use `@fixture` decorator from `turboplex_py`
- Define database fixtures in conftest.py
- Use `scope="module"` for expensive fixtures

### 9.3 Performance Optimization

- Enable caching: default enabled
- Use `--watch` for TDD workflows
- Run `--analyze` periodically to identify slow tests

---

## 10. Comparison: TurboPlex vs Pytest

| Feature | pytest | TurboPlex (tpx) |
|---------|--------|-----------------|
| **Time per test** | ~6s | ~1.5s (4x faster) |
| **Caching** | No | Yes (SHA-256) |
| **Parallel Execution** | pytest-xdist | Native (Rayon) |
| **M2M Report** | No | `.tplex_report.json` |
| **AI Analysis** | No | `--analyze` command |
| **Watch Mode** | pytest-watch | Native (`--watch`) |
| **Collection Speed** | Slow (imports all) | Fast (AST parsing) |

### 10.1 Performance Benchmarks

**Production Suite (~200 tests):**

| Tool | Time | per test |
|------|------|----------|
| **pytest** | ~340s | ~1.7s |
| **tpx (cold)** | ~180s | ~0.9s |
| **tpx (cached)** | **~25s** | **~0.13s** |

🖥️ **Tested on:**
- CPU: Ryzen 7 5700X3D (8C/16T)
- RAM: 16GB DDR4 @ 3600MHz
- Storage: Crucial P3 NVMe Gen3 (1TB)

---

## 11. Roadmap & Future Improvements

### 11.1 Planned Features

- [ ] Native async/await support
- [ ] pytest plugin ecosystem integration
- [ ] VS Code extension for test explorer
- [ ] Distributed test execution across multiple machines

### 11.2 Current Limitations

- Fixtures with `scope="session"` require special handling
- Some pytest plugins may need adapters
- Windows path handling (use forward slashes in config)

---

## Appendix A: Setup Script

```bash
#!/bin/bash
# setup_turboplex.sh

echo "=== TurboPlex Setup ==="

# 1. Install
cd /path/to/your-project
source .venv/bin/activate
pip install -e /path/to/turboplex

# 2. Copy conftest
cp /path/to/turboplex/examples/conftest_hybrid.py backend/tests/conftest.py

echo "=== Setup Complete ==="
echo "Run: cd backend && tpx --path tests/"
```

---

## Appendix B: FAQ

### Q: Can I use TurboPlex with Django?
**A:** Yes, but you'll need to adapt conftest to use `django.test.TestCase` or `pytest-django`.

### Q: Does it work with async/await?
**A:** Partial support. Async fixtures require special handling in `pytest_bridge.py`.

### Q: Can I mix tests with and without DB?
**A:** Yes, the hybrid conftest automatically detects which fixtures each test needs.

### Q: How do I debug a test that only fails in TurboPlex?
**A:** Run the test individually with pytest, then compare environments:
```bash
pytest tests/test_failing.py -v --tb=long
tpx --path tests/test_failing.py 2>&1 | head -100
```

### Q: Where do I report bugs?
**A:** GitHub Issues on the TurboPlex repository with:
- tpx version
- Full output with `RUST_DEBUG`
- conftest contents (without sensitive data)
- Exact command used

---

**Documentation created:** March 2026  
**TurboPlex Version:** 0.2.10  
**Author:** TurboPlex Team
