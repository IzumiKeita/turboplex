import os
import shutil
import subprocess
import sys
import threading
import time


def subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def resolve_python_executable() -> str:
    override = os.environ.get("TPX_PYTHON_EXE")
    if override:
        return override

    base = getattr(sys, "_base_executable", None)
    if isinstance(base, str) and base and os.path.basename(base).lower().startswith("python"):
        return base

    exe = sys.executable
    if isinstance(exe, str) and exe and os.path.basename(exe).lower().startswith("python"):
        return exe

    found = shutil.which("python") or shutil.which("py")
    return found or exe


def env_timeout_s(*names: str, default: float) -> float:
    for name in names:
        v = os.environ.get(name)
        if v is None:
            continue
        try:
            return float(v)
        except Exception:
            continue
    return default


def env_interval_s(*names: str, default: float) -> float:
    for name in names:
        v = os.environ.get(name)
        if v is None:
            continue
        try:
            x = float(v)
            if x > 0:
                return x
        except Exception:
            continue
    return default


def write_heartbeat(text: str) -> None:
    s = (getattr(sys, "__stderr__", None) or sys.stderr)
    try:
        s.write(text + "\n")
        s.flush()
    except Exception:
        pass


def run_popen_with_drain_and_heartbeat(
    cmd: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout_s: float,
    heartbeat_s: float,
    heartbeat_label: str,
) -> tuple[int | None, str, str, bool]:
    p = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    stdout_buf = bytearray()
    stderr_buf = bytearray()

    def _drain(pipe, sink: bytearray):
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                sink.extend(chunk)
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_drain, args=(p.stdout, stdout_buf), daemon=True)
    t_err = threading.Thread(target=_drain, args=(p.stderr, stderr_buf), daemon=True)
    t_out.start()
    t_err.start()

    stop = threading.Event()

    def _beat():
        if heartbeat_s <= 0:
            return
        next_t = time.monotonic() + heartbeat_s
        while not stop.is_set():
            now = time.monotonic()
            if now >= next_t:
                write_heartbeat(f"[tpx] heartbeat {heartbeat_label}")
                next_t = now + heartbeat_s
            time.sleep(0.1)

    t_hb = threading.Thread(target=_beat, daemon=True)
    t_hb.start()

    timed_out = False
    try:
        p.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=2.0)
        except Exception:
            pass
        try:
            p.kill()
        except Exception:
            pass
        try:
            p.wait(timeout=2.0)
        except Exception:
            pass
    finally:
        stop.set()

    try:
        t_out.join(timeout=2.0)
        t_err.join(timeout=2.0)
    except Exception:
        pass

    stdout = stdout_buf.decode("utf-8", errors="replace")
    stderr = stderr_buf.decode("utf-8", errors="replace")
    return (p.returncode, stdout, stderr, timed_out)

