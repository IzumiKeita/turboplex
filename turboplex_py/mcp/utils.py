"""MCP utility functions."""

import os
import secrets
import shutil
import sys
import time


def uuid_v7() -> str:
    """Generate a UUID v7 string."""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    uuid_int = (ts_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0x2 << 62) | rand_b
    hex32 = f"{uuid_int:032x}"
    return (
        f"{hex32[0:8]}-"
        f"{hex32[8:12]}-"
        f"{hex32[12:16]}-"
        f"{hex32[16:20]}-"
        f"{hex32[20:32]}"
    )


def json_normalize(payload):
    """Normalize payload using DecimalEncoder if available."""
    try:
        from turboplex_py.collector import DecimalEncoder
    except Exception:
        return payload
    try:
        import json

        return json.loads(json.dumps(payload, cls=DecimalEncoder))
    except Exception:
        return payload


def resolve_python_executable() -> str:
    """Resolve the Python executable to use, prioritizing venv.

    Environment variables:
        TPX_PYTHON_EXE: Override Python executable path.
        TPX_MCP_DEBUG: If set, logs diagnostic info to stderr.
    """
    import sys as _sys

    debug = os.environ.get("TPX_MCP_DEBUG")
    override = os.environ.get("TPX_PYTHON_EXE")

    if override:
        # Validate that TPX_PYTHON_EXE exists and is executable
        if os.path.isfile(override) and os.access(override, os.X_OK):
            if debug:
                _sys.stderr.write(f"[TPX DEBUG] Using TPX_PYTHON_EXE: {override}\n")
                _sys.stderr.flush()
            return override
        # If invalid, warn but continue to fallback
        _sys.stderr.write(f"[TPX WARNING] TPX_PYTHON_EXE points to invalid executable: {override}. Falling back.\n")
        _sys.stderr.flush()

    # En venv, sys.executable es el python del venv (correcto)
    # sys._base_executable apunta al Python global (incorrecto para venv)
    exe = _sys.executable
    if isinstance(exe, str) and exe and os.path.basename(exe).lower().startswith("python"):
        if debug:
            _sys.stderr.write(f"[TPX DEBUG] Using sys.executable: {exe}\n")
            _sys.stderr.flush()
        return exe

    # Fallback a _base_executable solo si sys.executable no es válido
    base = getattr(_sys, "_base_executable", None)
    if isinstance(base, str) and base and os.path.basename(base).lower().startswith("python"):
        if debug:
            _sys.stderr.write(f"[TPX DEBUG] Using sys._base_executable fallback: {base}\n")
            _sys.stderr.flush()
        return base

    found = shutil.which("python") or shutil.which("py")
    if debug and found:
        _sys.stderr.write(f"[TPX DEBUG] Using which() fallback: {found}\n")
        _sys.stderr.flush()
    return found or exe
