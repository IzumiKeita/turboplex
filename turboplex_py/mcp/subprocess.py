"""Subprocess management with drain and heartbeat."""

import os
import subprocess
import sys
import threading
import time

from .errors import ToolTimeout
from .win32 import win_assign_job_object, win_close_job_object, kill_tree_windows


def subprocess_env() -> dict:
    """Create environment dict with UTF-8 defaults."""
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def env_timeout_s(*names: str, default: float) -> float:
    """Read timeout from environment variable."""
    for name in names:
        v = os.environ.get(name)
        if v is None:
            continue
        try:
            return float(v)
        except Exception:
            continue
    return default


def safe_close(stream) -> None:
    """Close a stream silently."""
    try:
        if stream:
            stream.close()
    except Exception:
        pass


def terminate_process(p: subprocess.Popen, grace_s: float) -> None:
    """Terminate process gracefully -> forcefully with Windows Job Object support."""
    # Windows: use Job Object if available
    win_close_job_object(p)

    # Traditional method as fallback
    try:
        p.terminate()
    except Exception:
        pass

    try:
        p.wait(timeout=grace_s)
        return
    except Exception:
        pass

    # Windows: taskkill as fallback
    if sys.platform == "win32":
        kill_tree_windows(p.pid)

    # Last resort: kill
    try:
        if sys.platform != "win32" or p.poll() is None:
            p.kill()
    except Exception:
        pass

    try:
        p.wait(timeout=5.0)
    except Exception:
        pass


def run_popen_with_drain_and_heartbeat(
    cmd: list[str],
    *,
    phase: str,
    timeout_s: float,
    cwd: str,
    env: dict,
) -> tuple[int, str, str]:
    """Run subprocess with output draining and heartbeat logging.
    
    Uses communicate() in a daemon thread for robust cross-platform I/O handling.
    This avoids the deadlock issues with manual thread draining on Windows.
    """
    from subprocess import PIPE, Popen

    hb_s = env_timeout_s("TPX_MCP_HEARTBEAT_S", default=1.0)
    kill_grace_s = env_timeout_s("TPX_MCP_TERMINATE_GRACE_S", default=2.0)
    max_chars = int(env_timeout_s("TPX_MCP_DRAIN_MAX_CHARS", default=2_000_000.0))

    p = Popen(
        cmd,
        stdout=PIPE,
        stderr=PIPE,
        stdin=PIPE,  # Important: provide stdin to avoid hanging on input prompts
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=env,
        bufsize=1,
    )
    
    # Immediately close stdin to signal "no input" - prevents processes waiting for input
    try:
        p.stdin.close()
    except Exception:
        pass

    # Assign to Job Object on Windows for automatic kill-tree
    win_assign_job_object(p)

    # Use communicate() in a daemon thread - more robust than manual draining
    result = {"rc": None, "stdout": "", "stderr": ""}
    communicate_done = threading.Event()
    
    def _comm_thread():
        try:
            # Add internal timeout to communicate to prevent indefinite blocking
            comm_timeout = timeout_s + 5.0  # Give communicate a bit more time than external timeout
            stdout, stderr = p.communicate(timeout=comm_timeout)
            result["stdout"] = stdout[:max_chars] if stdout else ""
            result["stderr"] = stderr[:max_chars] if stderr else ""
            result["rc"] = p.poll()
        except subprocess.TimeoutExpired:
            result["stderr"] = f"communicate timeout after {comm_timeout}s - subprocess hung"
            result["rc"] = -1
            # Force kill the process tree
            try:
                terminate_process(p, 1.0)
            except Exception:
                pass
        except Exception as e:
            result["stderr"] = f"communicate error: {e}"
            result["rc"] = -1
        finally:
            communicate_done.set()
    
    comm_thread = threading.Thread(target=_comm_thread, daemon=True)
    comm_thread.start()
    
    # Wait for completion with timeout and heartbeat
    t0 = time.perf_counter()
    next_hb = t0 + hb_s
    timed_out = False
    
    try:
        while not communicate_done.wait(timeout=0.1):
            now = time.perf_counter()
            if now - t0 >= timeout_s:
                # Timeout!
                timed_out = True
                terminate_process(p, kill_grace_s)
                safe_close(p.stdout)
                safe_close(p.stderr)
                raise ToolTimeout(phase=phase, timeout_s=timeout_s)
            if now >= next_hb:
                next_hb = now + hb_s
                try:
                    real_err = getattr(sys, "__stderr__", None) or sys.stderr
                    real_err.write(f"[tpx-mcp] heartbeat phase={phase} elapsed_s={now - t0:.1f}\n")
                    real_err.flush()
                except Exception:
                    pass
    finally:
        # Always ensure communicate_done is set to prevent infinite loop
        if not communicate_done.is_set():
            communicate_done.set()
        # Cleanup
        if not timed_out:
            win_close_job_object(p)
        safe_close(p.stdout)
        safe_close(p.stderr)
        # Wait for thread to finish with a reasonable timeout
        comm_thread.join(timeout=2.0)
        # If thread is still alive, force kill process
        if comm_thread.is_alive():
            try:
                p.kill()
                p.wait(timeout=1.0)
            except Exception:
                pass
    
    return int(result["rc"]) if result["rc"] is not None else -1, result["stdout"], result["stderr"]
