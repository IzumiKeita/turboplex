import sys


def _uuid_v7() -> str:
    import secrets
    import time

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


class _StdoutJsonRpcGuard:
    def __init__(self, underlying, *, mode: str, max_buffer_chars: int = 2_000_000):
        self._u = underlying
        self._mode = mode
        self._buf = ""
        self._max = max_buffer_chars
        self._real_stderr = getattr(sys, "__stderr__", None) or sys.stderr

    def write(self, s):
        if not s:
            return 0
        text = str(s)
        self._buf += text
        if len(self._buf) > self._max:
            self._handle_non_jsonrpc(self._buf)
            self._buf = ""
            return len(text)
        while "\n" in self._buf:
            line, rest = self._buf.split("\n", 1)
            self._buf = rest
            if line == "":
                self._u.write("\n")
                continue
            if self._is_jsonrpc_line(line):
                self._u.write(line + "\n")
            else:
                self._handle_non_jsonrpc(line + "\n")
        return len(text)

    def flush(self):
        if self._buf:
            if self._is_jsonrpc_line(self._buf):
                self._u.write(self._buf)
            else:
                self._handle_non_jsonrpc(self._buf)
            self._buf = ""
        return self._u.flush()

    def _is_jsonrpc_line(self, line: str) -> bool:
        try:
            import json

            obj = json.loads(line)
            return isinstance(obj, dict) and obj.get("jsonrpc") == "2.0"
        except Exception:
            return False

    def _handle_non_jsonrpc(self, text: str) -> None:
        if self._mode == "failfast":
            raise RuntimeError("stdout non-JSON-RPC blocked")
        self._real_stderr.write(text)
        try:
            self._real_stderr.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._u, name)


def _install_stdio_guard() -> None:
    import os

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception:
        pass

    mode = os.environ.get("TPX_MCP_STDOUT_MODE", "redirect").strip().lower()
    sys.stdout = _StdoutJsonRpcGuard(sys.stdout, mode=mode)


def _json_normalize(payload):
    try:
        from turboplex_py.collector import DecimalEncoder
    except Exception:
        return payload
    try:
        import json

        return json.loads(json.dumps(payload, cls=DecimalEncoder))
    except Exception:
        return payload


class _ToolTimeout(Exception):
    def __init__(self, *, phase: str, timeout_s: float):
        super().__init__(f"{phase} timed out after {timeout_s}s")
        self.phase = phase
        self.timeout_s = timeout_s

    def as_error(self) -> dict:
        return {
            "kind": "timeout",
            "phase": self.phase,
            "timeout_s": self.timeout_s,
            "message": str(self),
        }


class _ToolSubprocessError(Exception):
    def __init__(
        self,
        *,
        phase: str,
        returncode: int | None,
        stderr: str | None,
        stdout: str | None = None,
    ):
        msg = stderr.strip() if isinstance(stderr, str) and stderr.strip() else "subprocess failed"
        super().__init__(f"{phase}: {msg}")
        self.phase = phase
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout

    def as_error(self) -> dict:
        out = {
            "kind": "subprocess_failed",
            "phase": self.phase,
            "returncode": self.returncode,
            "message": str(self),
        }
        if self.stderr is not None:
            out["stderr"] = self.stderr
        if self.stdout is not None:
            out["stdout"] = self.stdout
        return out


def _subprocess_env():
    import os

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _turboplex_collect(paths):
    import json
    import os
    import subprocess
    import tempfile

    fd, out_path = tempfile.mkstemp(prefix="tpx_collect_", suffix=".json")
    os.close(fd)
    try:
        cmd = [sys.executable, "-m", "turboplex_py", "collect", *paths, "--out-json", out_path]
        timeout_s = float(os.environ.get("TPX_MCP_TURBOPLEX_COLLECT_TIMEOUT_S", "120"))
        try:
            out = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=os.getcwd(),
                env=_subprocess_env(),
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            raise _ToolTimeout(phase="turboplex_collect", timeout_s=timeout_s)
        if out.returncode != 0:
            raise _ToolSubprocessError(
                phase="turboplex_collect",
                returncode=out.returncode,
                stderr=out.stderr,
                stdout=out.stdout,
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


def _turboplex_run_one(path: str, qual: str):
    import json
    import os
    import subprocess
    import tempfile

    fd, out_path = tempfile.mkstemp(prefix="tpx_run_", suffix=".json")
    os.close(fd)
    try:
        cmd = [
            sys.executable,
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
        timeout_s = float(os.environ.get("TPX_MCP_TURBOPLEX_RUN_TIMEOUT_S", "60"))
        try:
            out = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=os.getcwd(),
                env=_subprocess_env(),
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            raise _ToolTimeout(phase="turboplex_run", timeout_s=timeout_s)
        if out.returncode not in (0, 1):
            raise _ToolSubprocessError(
                phase="turboplex_run",
                returncode=out.returncode,
                stderr=out.stderr,
                stdout=out.stdout,
            )
        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
            return json.loads(f.read())
    finally:
        try:
            os.remove(out_path)
        except Exception:
            pass


def _pytest_collect(paths):
    import os
    import subprocess

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

    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", *paths]
    timeout_s = float(os.environ.get("TPX_MCP_PYTEST_COLLECT_TIMEOUT_S", "120"))
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.getcwd(),
            env=_subprocess_env(),
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        raise _ToolTimeout(phase="pytest_collect", timeout_s=timeout_s)
    if out.returncode != 0:
        raise _ToolSubprocessError(
            phase="pytest_collect",
            returncode=out.returncode,
            stderr=out.stderr,
            stdout=out.stdout,
        )
    items = []
    for line in (l.strip() for l in out.stdout.splitlines()):
        if not line or "::" not in line:
            continue
        path_part = line.split("::", 1)[0].strip()
        if not path_part.endswith(".py"):
            continue
        items.append({"path": path_part, "qualname": line, "kind": "pytest"})
    return items


def _pytest_run(nodeid):
    import os
    import subprocess
    import time

    t0 = time.perf_counter()
    cmd = [sys.executable, "-m", "pytest", "-q", nodeid]
    timeout_s = float(os.environ.get("TPX_MCP_PYTEST_RUN_TIMEOUT_S", "60"))
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.getcwd(),
            env=_subprocess_env(),
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        raise _ToolTimeout(phase="pytest_run", timeout_s=timeout_s)
    dt = int((time.perf_counter() - t0) * 1000)
    passed = out.returncode == 0
    err = None
    if not passed:
        err = (out.stderr or "").strip() or (out.stdout or "").strip() or "pytest failed"
    return {"passed": passed, "duration_ms": dt, "error": err}


def _build_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as e:
        raise RuntimeError(
            "No se pudo importar el SDK 'mcp'. Instala la dependencia con: pip install mcp"
        ) from e

    mcp = FastMCP("TurboPlex", json_response=True)

    @mcp.tool()
    def ping() -> str:
        return "pong"

    @mcp.tool()
    def turboplex_version() -> str:
        try:
            from importlib.metadata import version

            return version("turboplex")
        except Exception:
            return "dev"

    @mcp.tool()
    def discover(paths: list[str] | None = None, compat: bool = False) -> str:
        import contextlib
        import io
        import os
        import time

        run_id = _uuid_v7()
        t0 = time.perf_counter()
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            try:
                paths = paths or ["."]
                if compat:
                    raw_items = _pytest_collect(paths)
                    mode = "pytest"
                    items = raw_items
                else:
                    raw_items = _turboplex_collect(paths)
                    mode = "turboplex"
                    items = _json_normalize(raw_items)
                root = os.getcwd()
                for it in items:
                    p = it.get("path")
                    if isinstance(p, str):
                        rel = os.path.relpath(p, start=root)
                        it["path"] = rel.replace("\\", "/")
                payload = {
                    "schemaVersion": "tpx.mcp.tool.v1",
                    "tool": "discover",
                    "ok": True,
                    "runId": run_id,
                    "mode": mode,
                    "summary": {"total": len(items), "duration_ms": int((time.perf_counter() - t0) * 1000)},
                    "logs": {},
                    "data": {"items": items},
                }
            except Exception as e:
                if isinstance(e, (_ToolTimeout, _ToolSubprocessError)):
                    err = e.as_error()
                else:
                    err = str(e)
                payload = {
                    "schemaVersion": "tpx.mcp.tool.v1",
                    "tool": "discover",
                    "ok": False,
                    "runId": run_id,
                    "mode": "pytest" if compat else "turboplex",
                    "summary": {"duration_ms": int((time.perf_counter() - t0) * 1000)},
                    "logs": {},
                    "data": {"error": err},
                }
        _attach_logs(payload, buf_out.getvalue(), buf_err.getvalue())
        return _tool_json(payload)

    @mcp.tool()
    def run(selection: list[dict] | list[str], compat: bool = False, max_workers: int | None = None) -> str:
        import concurrent.futures
        import contextlib
        import io
        import os
        import time

        run_id = _uuid_v7()
        t0 = time.perf_counter()
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            try:
                results = []
                root = os.getcwd()
                if selection is None:
                    raise RuntimeError("selection es requerido")

                if compat:
                    if not isinstance(selection, list):
                        raise RuntimeError("compat=true requiere selection: string[] (nodeids)")
                    nodeids = []
                    for s in selection:
                        if isinstance(s, str):
                            nodeids.append(s)
                        elif isinstance(s, dict):
                            nodeids.append(str(s.get("nodeid") or s.get("qualname") or ""))
                        else:
                            nodeids.append(str(s))
                    nodeids = [n for n in nodeids if n]
                    if nodeids:
                        workers = max_workers or min(8, len(nodeids), (os.cpu_count() or 1))
                        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                            futs = {ex.submit(_pytest_run, nid): nid for nid in nodeids}
                            for fut in concurrent.futures.as_completed(futs):
                                nid = futs[fut]
                                r = fut.result()
                                results.append({"test": nid, "path": nid.split("::", 1)[0], **r})
                    mode = "pytest"
                else:
                    if not isinstance(selection, list):
                        raise RuntimeError("compat=false requiere selection: object[] con {path, qualname}")
                    items = []
                    for s in selection:
                        if not isinstance(s, dict):
                            raise RuntimeError("compat=false requiere selection: object[] con {path, qualname}")
                        path = s.get("path")
                        qual = s.get("qualname")
                        if not isinstance(path, str) or not isinstance(qual, str) or not path or not qual:
                            continue
                        items.append((path, qual))
                    if items:
                        workers = max_workers or min(8, len(items), (os.cpu_count() or 1))
                        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                            futs = {ex.submit(_turboplex_run_one, p, q): (p, q) for (p, q) in items}
                            for fut in concurrent.futures.as_completed(futs):
                                p, q = futs[fut]
                                r = fut.result()
                                relp = os.path.relpath(p, start=root).replace("\\", "/")
                                results.append({"test": q, "path": relp, **_json_normalize(r)})
                    mode = "turboplex"

                results.sort(key=lambda r: (r.get("path") or "", r.get("test") or ""))
                failed = sum(1 for r in results if not r.get("passed"))
                dt = int((time.perf_counter() - t0) * 1000)
                payload = {
                    "schemaVersion": "tpx.mcp.tool.v1",
                    "tool": "run",
                    "ok": True,
                    "runId": run_id,
                    "mode": mode,
                    "summary": {"total": len(results), "failed": failed, "passed": failed == 0, "duration_ms": dt},
                    "logs": {},
                    "data": {"results": results},
                }
            except Exception as e:
                if isinstance(e, (_ToolTimeout, _ToolSubprocessError)):
                    err = e.as_error()
                else:
                    err = str(e)
                payload = {
                    "schemaVersion": "tpx.mcp.tool.v1",
                    "tool": "run",
                    "ok": False,
                    "runId": run_id,
                    "mode": "pytest" if compat else "turboplex",
                    "summary": {"duration_ms": int((time.perf_counter() - t0) * 1000)},
                    "logs": {},
                    "data": {"error": err},
                }
        _attach_logs(payload, buf_out.getvalue(), buf_err.getvalue())
        return _tool_json(payload)

    @mcp.tool()
    def get_report(path: str | None = None) -> str:
        import contextlib
        import io
        import json
        import os
        import pathlib
        import time

        run_id = _uuid_v7()
        t0 = time.perf_counter()
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            try:
                p = pathlib.Path(path) if path else pathlib.Path(os.getcwd()) / ".tplex_report.json"
                rel = os.path.relpath(str(p), start=os.getcwd()).replace("\\", "/")
                if not p.is_file():
                    payload = {
                        "schemaVersion": "tpx.mcp.tool.v1",
                        "tool": "get_report",
                        "ok": True,
                        "runId": run_id,
                        "mode": "turboplex",
                        "summary": {"found": False, "duration_ms": int((time.perf_counter() - t0) * 1000)},
                        "logs": {},
                        "data": {"found": False, "path": rel, "report": None},
                        "artifacts": [{"kind": "report", "uri": rel, "contentType": "application/json"}],
                    }
                else:
                    report = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                    payload = {
                        "schemaVersion": "tpx.mcp.tool.v1",
                        "tool": "get_report",
                        "ok": True,
                        "runId": run_id,
                        "mode": "turboplex",
                        "summary": {"found": True, "duration_ms": int((time.perf_counter() - t0) * 1000)},
                        "logs": {},
                        "data": {"found": True, "path": rel, "report": report},
                        "artifacts": [{"kind": "report", "uri": rel, "contentType": "application/json"}],
                    }
            except Exception as e:
                payload = {
                    "schemaVersion": "tpx.mcp.tool.v1",
                    "tool": "get_report",
                    "ok": False,
                    "runId": run_id,
                    "mode": "turboplex",
                    "summary": {"duration_ms": int((time.perf_counter() - t0) * 1000)},
                    "logs": {},
                    "data": {"error": str(e)},
                }
        _attach_logs(payload, buf_out.getvalue(), buf_err.getvalue())
        return _tool_json(payload)

    return mcp


def _attach_logs(payload: dict, captured_stdout: str, captured_stderr: str) -> None:
    max_chars = 20_000
    out = captured_stdout or ""
    err = captured_stderr or ""
    truncated = False
    if len(out) > max_chars:
        out = out[:max_chars]
        truncated = True
    if len(err) > max_chars:
        err = err[:max_chars]
        truncated = True
    logs = {}
    if out:
        logs["stdout"] = out
    if err:
        logs["stderr"] = err
    if truncated:
        logs["truncated"] = True
    payload["logs"] = logs


def _tool_json(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    _install_stdio_guard()
    try:
        mcp = _build_server()
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        mcp.run(transport="stdio")
    except TypeError:
        mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
