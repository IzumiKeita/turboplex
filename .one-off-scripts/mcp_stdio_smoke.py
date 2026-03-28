import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import UUID


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here.parent, *here.parents]:
        if (p / "pyproject.toml").is_file():
            return p
    return here.parents[1]


def _find_tpx_exe(root: Path) -> str:
    candidates = [
        root / "target" / "release" / "tpx.exe",
        root / "target" / "debug" / "tpx.exe",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    which = shutil.which("tpx")
    if which:
        return which
    raise RuntimeError("No se encontró tpx.exe (target/release|debug) ni tpx en PATH")


def _start_reader(stream, out_q: "queue.Queue[str | None]") -> threading.Thread:
    def _run():
        try:
            for line in iter(stream.readline, ""):
                out_q.put(line)
        finally:
            out_q.put(None)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _read_jsonrpc_line(
    proc: subprocess.Popen,
    stdout_q: "queue.Queue[str | None]",
    *,
    timeout_s: float,
) -> dict[str, Any]:
    t_end = time.time() + timeout_s
    while time.time() < t_end:
        if proc.poll() is not None:
            raise RuntimeError(f"Proceso MCP terminó con code={proc.returncode}")
        try:
            line = stdout_q.get(timeout=min(0.25, max(0.01, t_end - time.time())))
        except queue.Empty:
            continue
        if line is None:
            raise RuntimeError("STDOUT cerrado antes de recibir JSON-RPC")
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            raise RuntimeError(f"STDOUT no es JSON: {line[:200]}")
        if not isinstance(obj, dict) or obj.get("jsonrpc") != "2.0":
            raise RuntimeError(f"STDOUT no es JSON-RPC: {line[:200]}")
        return obj
    raise TimeoutError("Timeout esperando respuesta JSON-RPC")


def _send(proc: subprocess.Popen, msg: dict[str, Any]) -> None:
    wire = json.dumps(msg, separators=(",", ":"), ensure_ascii=False)
    proc.stdin.write(wire + "\n")
    proc.stdin.flush()


def _await_id(
    proc: subprocess.Popen,
    stdout_q: "queue.Queue[str | None]",
    req_id: int,
    *,
    timeout_s: float,
) -> dict[str, Any]:
    t_end = time.time() + timeout_s
    while time.time() < t_end:
        obj = _read_jsonrpc_line(proc, stdout_q, timeout_s=max(0.1, t_end - time.time()))
        if obj.get("id") == req_id:
            return obj
    raise TimeoutError(f"Timeout esperando id={req_id}")


def _extract_envelope(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        content = obj.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text" and isinstance(first.get("text"), str):
                try:
                    inner = json.loads(first["text"])
                except Exception:
                    inner = None
                if isinstance(inner, dict) and inner.get("schemaVersion") == "tpx.mcp.tool.v1":
                    return inner
        if obj.get("schemaVersion") == "tpx.mcp.tool.v1":
            return obj
        for v in obj.values():
            found = _extract_envelope(v)
            if found:
                return found
    if isinstance(obj, list):
        for v in obj:
            found = _extract_envelope(v)
            if found:
                return found
    return None


def _assert_uuidv7(run_id: str) -> None:
    u = UUID(run_id)
    if (u.version or 0) != 7:
        raise RuntimeError(f"runId no es UUIDv7: {run_id}")


def main() -> int:
    root = _repo_root()
    exe = _find_tpx_exe(root)
    smoke_paths = ["turboplex_py"]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    proc = subprocess.Popen(
        [exe, "mcp"],
        cwd=str(root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )

    try:
        stdout_q: "queue.Queue[str | None]" = queue.Queue()
        stderr_q: "queue.Queue[str | None]" = queue.Queue()
        _start_reader(proc.stdout, stdout_q)
        _start_reader(proc.stderr, stderr_q)

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "tpx-smoke", "version": "0.0.0"},
                },
            },
        )
        _await_id(proc, stdout_q, 1, timeout_s=15.0)

        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = _await_id(proc, stdout_q, 2, timeout_s=15.0)
        tools_blob = json.dumps(resp, ensure_ascii=False)
        if "discover" not in tools_blob or "run" not in tools_blob:
            raise RuntimeError("tools/list no incluye discover y run")

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "discover", "arguments": {"paths": smoke_paths, "compat": False}},
            },
        )
        resp = _await_id(proc, stdout_q, 3, timeout_s=30.0)
        envl = _extract_envelope(resp)
        if not envl or envl.get("tool") != "discover":
            raise RuntimeError(f"discover no devolvió envelope esperado: {json.dumps(resp, ensure_ascii=False)[:2000]}")
        _assert_uuidv7(envl["runId"])

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "run", "arguments": {"selection": [], "compat": False}},
            },
        )
        resp = _await_id(proc, stdout_q, 4, timeout_s=30.0)
        envl = _extract_envelope(resp)
        if not envl or envl.get("tool") != "run":
            raise RuntimeError(f"run (compat=false) no devolvió envelope esperado: {json.dumps(resp, ensure_ascii=False)[:2000]}")
        _assert_uuidv7(envl["runId"])

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "discover", "arguments": {"paths": smoke_paths, "compat": True}},
            },
        )
        resp = _await_id(proc, stdout_q, 5, timeout_s=60.0)
        envl = _extract_envelope(resp)
        if not envl or envl.get("tool") != "discover":
            raise RuntimeError(f"discover (compat=true) no devolvió envelope esperado: {json.dumps(resp, ensure_ascii=False)[:2000]}")
        _assert_uuidv7(envl["runId"])

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "run", "arguments": {"selection": [], "compat": True}},
            },
        )
        resp = _await_id(proc, stdout_q, 6, timeout_s=60.0)
        envl = _extract_envelope(resp)
        if not envl or envl.get("tool") != "run":
            raise RuntimeError(f"run (compat=true) no devolvió envelope esperado: {json.dumps(resp, ensure_ascii=False)[:2000]}")
        _assert_uuidv7(envl["runId"])

        return 0
    except Exception as e:
        tail = []
        try:
            while True:
                line = stderr_q.get_nowait()
                if line is None:
                    break
                line = line.strip()
                if line:
                    tail.append(line)
        except Exception:
            pass
        if tail:
            raise RuntimeError(f"{e}\n\nSTDERR (tail):\n" + "\n".join(tail[-40:])) from e
        raise
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
