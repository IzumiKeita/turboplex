"""MCP Server for TurboPlex - refactored to use mcp package."""

import concurrent.futures
import contextlib
import io
import os
import pathlib
import sys
import time

from turboplex_py.mcp import (
    ToolTimeout,
    ToolSubprocessError,
    uuid_v7,
    json_normalize,
    install_stdio_guard,
    tool_json,
    attach_logs,
    turboplex_collect,
    turboplex_run_one,
    pytest_collect,
    pytest_run,
    env_timeout_s,  # Added for per-test timeout
)


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
        run_id = uuid_v7()
        t0 = time.perf_counter()
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            try:
                paths = paths or ["."]
                if compat:
                    raw_items = pytest_collect(paths)
                    mode = "pytest"
                    items = raw_items
                else:
                    raw_items = turboplex_collect(paths)
                    mode = "turboplex"
                    items = json_normalize(raw_items)
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
                if isinstance(e, (ToolTimeout, ToolSubprocessError)):
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
        attach_logs(payload, buf_out.getvalue(), buf_err.getvalue())
        return tool_json(payload)

    @mcp.tool()
    def run(selection: list[dict] | list[str], compat: bool = False, max_workers: int | None = None) -> str:
        run_id = uuid_v7()
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
                        # Per-test timeout from environment
                        test_timeout_s = env_timeout_s("TPX_MCP_TEST_TIMEOUT_S", default=120.0)
                        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                            futs = {ex.submit(pytest_run, nid): nid for nid in nodeids}
                            for fut in concurrent.futures.as_completed(futs):
                                nid = futs[fut]
                                try:
                                    r = fut.result(timeout=test_timeout_s)
                                except concurrent.futures.TimeoutError:
                                    r = {"passed": False, "duration_ms": int(test_timeout_s * 1000), "error": f"Test timeout after {test_timeout_s}s"}
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
                        # Per-test timeout from environment
                        test_timeout_s = env_timeout_s("TPX_MCP_TEST_TIMEOUT_S", default=60.0)
                        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                            futs = {ex.submit(turboplex_run_one, p, q): (p, q) for (p, q) in items}
                            for fut in concurrent.futures.as_completed(futs):
                                p, q = futs[fut]
                                try:
                                    r = fut.result(timeout=test_timeout_s)
                                except concurrent.futures.TimeoutError:
                                    r = {"passed": False, "duration_ms": int(test_timeout_s * 1000), "error": f"Test timeout after {test_timeout_s}s"}
                                relp = os.path.relpath(p, start=root).replace("\\", "/")
                                results.append({"test": q, "path": relp, **json_normalize(r)})
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
                if isinstance(e, (ToolTimeout, ToolSubprocessError)):
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
        attach_logs(payload, buf_out.getvalue(), buf_err.getvalue())
        return tool_json(payload)

    @mcp.tool()
    def get_report(path: str | None = None) -> str:
        run_id = uuid_v7()
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
                    import json
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
        attach_logs(payload, buf_out.getvalue(), buf_err.getvalue())
        return tool_json(payload)

    return mcp


def main() -> int:
    install_stdio_guard()
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
