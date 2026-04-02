"""Test discovery and execution functions."""

import json
import os
import sys
import tempfile
import time

from .config import load_mcp_config
from .errors import ToolSubprocessError
from .subprocess import subprocess_env, run_popen_with_drain_and_heartbeat
from .utils import resolve_python_executable


def _debug_log(msg: str) -> None:
    """Log debug message if TPX_MCP_DEBUG is set."""
    if os.environ.get("TPX_MCP_DEBUG"):
        sys.stderr.write(f"[TPX DEBUG] {msg}\n")
        sys.stderr.flush()


def _build_pytest_cmd(base_cmd: list, paths: list = None) -> list:
    """Build pytest command with optional light collect mode."""
    cmd = base_cmd.copy()
    
    # Always disable xdist to avoid double Python spawn
    cmd.extend(["-p", "no:xdist"])
    
    # Light collect mode: skip conftest loading completely (faster, avoids DB migrations)
    if os.environ.get("TPX_MCP_LIGHT_COLLECT", "").lower() in ("1", "true", "yes"):
        # --noconftest prevents ALL conftest.py from being loaded
        # This makes collect much faster when conftests do heavy setup (like DB migrations)
        cmd.append("--noconftest")
        _debug_log("Using light collect mode (--noconftest)")
    
    if paths:
        cmd.extend(paths)
    
    return cmd


def _run_pytest_with_diagnostics(cmd: list, phase: str, timeout_s: float) -> tuple:
    """Run pytest command with debug tracing and better error info."""
    python_exe = resolve_python_executable()
    
    _debug_log(f"{phase}: Python: {python_exe}")
    _debug_log(f"{phase}: Command: {' '.join(cmd)}")
    _debug_log(f"{phase}: Timeout: {timeout_s}s")
    _debug_log(f"{phase}: CWD: {os.getcwd()}")
    
    rc, stdout, stderr = run_popen_with_drain_and_heartbeat(
        cmd,
        phase=phase,
        timeout_s=timeout_s,
        cwd=os.getcwd(),
        env=subprocess_env(),
    )
    
    _debug_log(f"{phase}: Exit code: {rc}")
    
    if rc != 0:
        # Enhanced error with diagnostic info
        err_msg = f"pytest failed (exit {rc})"
        if stderr:
            err_msg += f"\nStderr: {stderr[:500]}"
        if stdout:
            err_msg += f"\nStdout: {stdout[:500]}"
        err_msg += f"\nPython used: {python_exe}"
        err_msg += f"\nCommand: {' '.join(cmd)}"
        
        # Check for common issues
        if "no module named" in (stderr + stdout).lower():
            err_msg += "\n[Hint] Missing Python module - check venv dependencies"
        if "permission denied" in (stderr + stdout).lower():
            err_msg += "\n[Hint] Permission denied - check file permissions"
        
        _debug_log(f"{phase}: Error: {err_msg[:200]}...")
        raise ToolSubprocessError(
            phase=phase,
            returncode=rc,
            stderr=err_msg,
            stdout=stdout,
        )
    
    return rc, stdout, stderr


def turboplex_collect(paths):
    """Collect tests using turboplex collector."""
    fd, out_path = tempfile.mkstemp(prefix="tpx_collect_", suffix=".json")
    os.close(fd)
    try:
        cmd = [
            resolve_python_executable(),
            "-m",
            "turboplex_py",
            "collect",
            *paths,
            "--out-json",
            out_path,
        ]
        cfg = load_mcp_config()
        timeout_s = cfg.turboplex_collect_timeout_s
        rc, stdout, stderr = run_popen_with_drain_and_heartbeat(
            cmd,
            phase="turboplex_collect",
            timeout_s=timeout_s,
            cwd=os.getcwd(),
            env=subprocess_env(),
        )
        if rc != 0:
            raise ToolSubprocessError(
                phase="turboplex_collect",
                returncode=rc,
                stderr=stderr,
                stdout=stdout,
            )
        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
            raw = json.loads(f.read())
        items = raw.get("items") if isinstance(raw, dict) else None
        return items if isinstance(items, list) else []
    finally:
        try:
            os.remove(out_path)
        except Exception:
            pass


def turboplex_run_one(path: str, qual: str):
    """Run a single test using turboplex runner."""
    fd, out_path = tempfile.mkstemp(prefix="tpx_run_", suffix=".json")
    os.close(fd)
    try:
        cmd = [
            resolve_python_executable(),
            "-m",
            "turboplex_py",
            "run",
            "--path",
            path,
            "--qual",
            qual,
            "--out-json",
            out_path,
        ]
        cfg = load_mcp_config()
        timeout_s = cfg.turboplex_run_timeout_s
        rc, stdout, stderr = run_popen_with_drain_and_heartbeat(
            cmd,
            phase="turboplex_run",
            timeout_s=timeout_s,
            cwd=os.getcwd(),
            env=subprocess_env(),
        )
        if rc not in (0, 1):
            raise ToolSubprocessError(
                phase="turboplex_run",
                returncode=rc,
                stderr=stderr,
                stdout=stdout,
            )
        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
            return json.loads(f.read())
    finally:
        try:
            os.remove(out_path)
        except Exception:
            pass


def pytest_collect(paths):
    """Collect tests using pytest --collect-only."""
    def _looks_like_test_file(filename: str) -> bool:
        if not filename.endswith(".py"):
            return False
        if filename.startswith("test_"):
            return True
        if filename.endswith("_test.py"):
            return True
        return False

    checked = 0
    max_checked = 10_000
    found_any = False
    for p in paths:
        if checked >= max_checked:
            break
        if os.path.isfile(p):
            checked += 1
            if _looks_like_test_file(os.path.basename(p)):
                found_any = True
                break
            continue
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                for fn in files:
                    checked += 1
                    if checked >= max_checked:
                        break
                    if _looks_like_test_file(fn):
                        found_any = True
                        break
                if found_any or checked >= max_checked:
                    break
        if found_any:
            break

    if not found_any:
        return []

    python_exe = resolve_python_executable()
    base_cmd = [python_exe, "-m", "pytest", "--collect-only", "-q"]
    cmd = _build_pytest_cmd(base_cmd, paths)
    
    cfg = load_mcp_config()
    timeout_s = cfg.pytest_collect_timeout_s
    
    rc, stdout, stderr = _run_pytest_with_diagnostics(cmd, "pytest_collect", timeout_s)
    
    items = []
    for line in (l.strip() for l in stdout.splitlines()):
        if not line or "::" not in line:
            continue
        path_part = line.split("::", 1)[0].strip()
        if not path_part.endswith(".py"):
            continue
        items.append({"path": path_part, "qualname": line, "kind": "pytest"})
    return items


def pytest_run(nodeid):
    """Run a single test using pytest."""
    t0 = time.perf_counter()
    python_exe = resolve_python_executable()
    base_cmd = [python_exe, "-m", "pytest", "-q", nodeid]
    cmd = _build_pytest_cmd(base_cmd)
    
    cfg = load_mcp_config()
    timeout_s = cfg.pytest_run_timeout_s
    
    try:
        rc, stdout, stderr = _run_pytest_with_diagnostics(cmd, "pytest_run", timeout_s)
    except ToolSubprocessError as e:
        # Re-raise with duration info
        dt = int((time.perf_counter() - t0) * 1000)
        return {"passed": False, "duration_ms": dt, "error": str(e.stderr)[:1000]}
    
    dt = int((time.perf_counter() - t0) * 1000)
    passed = rc == 0
    err = None
    if not passed:
        err = (stderr or "").strip() or (stdout or "").strip() or "pytest failed"
        err += f"\n[Python: {python_exe}]"
    return {"passed": passed, "duration_ms": dt, "error": err}
