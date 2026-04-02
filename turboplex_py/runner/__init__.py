"""Run a single test; print one JSON line to stdout; tracebacks on stderr."""

from __future__ import annotations

import logging
import sys

# Configure logging to stderr to keep stdout clean for JSON
for h in logging.root.handlers[:]:
    logging.root.removeHandler(h)
h = logging.StreamHandler(sys.stderr)
h.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
logging.root.addHandler(h)
logging.root.setLevel(logging.CRITICAL)

# Disable SQLAlchemy and other noisy libraries
logging.getLogger("sqlalchemy.engine").setLevel(logging.CRITICAL)
logging.getLogger("sqlalchemy").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

# Public API re-exports
from .execution import run_test, run_single_test, run_test_batch, run_main, run_batch_main

__all__ = [
    "run_test",
    "run_single_test",
    "run_test_batch",
    "run_main",
    "run_batch_main",
]
