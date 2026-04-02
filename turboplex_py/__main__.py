#!/usr/bin/env python3
"""Wrapper that calls the Rust binary for main commands, but handles collect/run directly."""

# CRITICAL: Bootstrap SQLAlchemy patcher BEFORE any imports that might import SQLAlchemy
# This ensures all DB operations are intercepted and made lazy
import os

# Initialize colorama for Windows ANSI color support
try:
    import colorama
    colorama.init(autoreset=True)
except ImportError:
    pass  # colorama not installed, colors may not work on Windows

if os.environ.get("TURBOTEST_SUBPROCESS") == "1":
    try:
        from turboplex_py.compat.bootstrap import ensure_patchers
        ensure_patchers()
    except Exception:
        pass  # Ignore bootstrap errors, we'll handle them later

import sys
import subprocess

def _parse_pytest_run_batch_args(argv: list[str]) -> tuple[str | None, str | None]:
    nodeids_json = None
    out_json = None
    i = 0
    while i < len(argv):
        if argv[i] == "--nodeids-json" and i + 1 < len(argv):
            nodeids_json = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--out-json" and i + 1 < len(argv):
            out_json = argv[i + 1]
            i += 2
            continue
        i += 1
    return nodeids_json, out_json


def pytest_run_batch_main(nodeids_json: str, out_json: str | None) -> None:
    import json
    import os
    import pathlib
    import time

    try:
        if os.path.isfile(nodeids_json):
            nodeids_json = pathlib.Path(nodeids_json).read_text(
                encoding="utf-8", errors="replace"
            )
        nodeids = json.loads(nodeids_json)
        if not isinstance(nodeids, list):
            raise ValueError("nodeids_json must be a list")
        nodeids = [str(x) for x in nodeids if str(x).strip()]
    except Exception as e:
        payload = {"results": [], "error": f"Invalid nodeids JSON: {e}"}
        if out_json:
            pathlib.Path(out_json).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        return

    results_by_nodeid: dict[str, dict] = {}

    class Plugin:
        def pytest_runtest_logreport(self, report):
            if report.when not in ("setup", "call", "teardown"):
                return
            entry = results_by_nodeid.get(report.nodeid)
            if entry is None:
                entry = {"nodeid": report.nodeid, "stages": {}}
                results_by_nodeid[report.nodeid] = entry
            stage = entry["stages"].get(report.when)
            if stage is None:
                stage = {}
                entry["stages"][report.when] = stage
            stage["outcome"] = report.outcome
            stage["duration_ms"] = int((getattr(report, "duration", 0.0) or 0.0) * 1000)
            if report.outcome == "failed":
                lr = getattr(report, "longreprtext", None)
                if lr:
                    stage["error"] = str(lr)
            if report.outcome == "skipped":
                lr = getattr(report, "longreprtext", None)
                if lr:
                    stage["skip_reason"] = str(lr).strip()

    t0 = time.perf_counter()
    exitstatus = 0
    try:
        import pytest

        exitstatus = pytest.main(["-q", *nodeids], plugins=[Plugin()])
    except Exception as e:
        payload = {"results": [], "error": f"pytest crashed: {e}"}
        if out_json:
            pathlib.Path(out_json).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        return

    run_ms = int((time.perf_counter() - t0) * 1000)
    out_results: list[dict] = []

    for nodeid in nodeids:
        entry = results_by_nodeid.get(nodeid) or {"nodeid": nodeid, "stages": {}}
        stages = entry.get("stages") or {}
        call = stages.get("call") or {}
        setup = stages.get("setup") or {}
        teardown = stages.get("teardown") or {}

        stage = call or setup or teardown
        outcome = stage.get("outcome") or "failed"
        duration_ms = int(stage.get("duration_ms") or 0)
        error = stage.get("error")
        skip_reason = stage.get("skip_reason")

        passed = outcome == "passed"
        skipped = outcome == "skipped"
        if skipped:
            passed = True

        out_results.append(
            {
                "nodeid": nodeid,
                "passed": passed,
                "skipped": skipped,
                "skip_reason": skip_reason,
                "duration_ms": duration_ms,
                "error": error,
            }
        )

    payload = {"results": out_results, "exitstatus": exitstatus, "duration_ms": run_ms}
    if out_json:
        pathlib.Path(out_json).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

def _parse_collect_args(argv: list[str]) -> tuple[list[str], str | None]:
    out_json = None
    paths: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--out-json" and i + 1 < len(argv):
            out_json = argv[i + 1]
            i += 2
            continue
        paths.append(argv[i])
        i += 1
    if not paths:
        paths = ["."]
    return paths, out_json


def _parse_run_args(argv: list[str]) -> tuple[str | None, str | None, str | None]:
    path = None
    qual = None
    out_json = None
    i = 0
    while i < len(argv):
        if argv[i] == "--path" and i + 1 < len(argv):
            path = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--qual" and i + 1 < len(argv):
            qual = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--out-json" and i + 1 < len(argv):
            out_json = argv[i + 1]
            i += 2
            continue
        i += 1
    return path, qual, out_json


def _parse_run_batch_args(argv: list[str]) -> tuple[str | None, str | None]:
    batch_json = None
    out_json = None
    i = 0
    while i < len(argv):
        if argv[i] == "--batch-json" and i + 1 < len(argv):
            batch_json = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--out-json" and i + 1 < len(argv):
            out_json = argv[i + 1]
            i += 2
            continue
        i += 1
    return batch_json, out_json


def main():
    # Check if this is a subprocess call from Rust binary (via environment variable)
    if os.environ.get("TURBOTEST_SUBPROCESS") == "1":
        # We're inside Rust's subprocess call - handle directly without recursion
        from turboplex_py.collector import collect_main
        from turboplex_py.runner import run_main, run_batch_main
        
        if len(sys.argv) > 1 and sys.argv[1] == "collect":
            paths, out_json = _parse_collect_args(sys.argv[2:])
            collect_main(paths, out_json=out_json)
        elif len(sys.argv) > 1 and sys.argv[1] == "run":
            path, qual, out_json = _parse_run_args(sys.argv[2:])
            if path and qual:
                run_main(path, qual, out_json=out_json)
            else:
                print("Error: --path and --qual required for run", file=sys.stderr)
                sys.exit(1)
        elif len(sys.argv) > 1 and sys.argv[1] == "run-batch":
            batch_json, out_json = _parse_run_batch_args(sys.argv[2:])
            if batch_json:
                run_batch_main(batch_json, out_json=out_json)
            else:
                print("Error: --batch-json required for run-batch", file=sys.stderr)
                sys.exit(1)
        elif len(sys.argv) > 1 and sys.argv[1] == "pytest-run-batch":
            nodeids_json, out_json = _parse_pytest_run_batch_args(sys.argv[2:])
            if nodeids_json:
                pytest_run_batch_main(nodeids_json, out_json=out_json)
            else:
                print("Error: --nodeids-json required for pytest-run-batch", file=sys.stderr)
                sys.exit(1)
        else:
            print("Error: Unknown subcommand", file=sys.stderr)
            sys.exit(1)
        return
    
    # Normal mode - check if this is a direct call to collect, run, or run-batch (subcommand)
    if len(sys.argv) > 1 and sys.argv[1] in ("collect", "run", "run-batch", "pytest-run-batch"):
        # Set TURBOTEST_SUBPROCESS to indicate we're in TurboPlex mode
        os.environ["TURBOTEST_SUBPROCESS"] = "1"
        
        # Bootstrap patchers for direct mode too
        try:
            from turboplex_py.compat.bootstrap import ensure_patchers
            ensure_patchers()
        except Exception:
            pass
        
        # Import and run the Python module directly
        from turboplex_py.collector import collect_main
        from turboplex_py.runner import run_main, run_batch_main
        
        if sys.argv[1] == "collect":
            # collect takes paths as remaining args
            paths, out_json = _parse_collect_args(sys.argv[2:])
            collect_main(paths, out_json=out_json)
        elif sys.argv[1] == "run":
            # run takes --path and --qual
            args = sys.argv[2:]
            path, qual, out_json = _parse_run_args(args)
            if path and qual:
                run_main(path, qual, out_json=out_json)
            else:
                print("Error: --path and --qual required for run", file=sys.stderr)
                sys.exit(1)
        elif sys.argv[1] == "run-batch":
            # run-batch takes --batch-json
            args = sys.argv[2:]
            batch_json, out_json = _parse_run_batch_args(args)
            if batch_json:
                run_batch_main(batch_json, out_json=out_json)
            else:
                print("Error: --batch-json required for run-batch", file=sys.stderr)
                sys.exit(1)
        elif sys.argv[1] == "pytest-run-batch":
            args = sys.argv[2:]
            nodeids_json, out_json = _parse_pytest_run_batch_args(args)
            if nodeids_json:
                pytest_run_batch_main(nodeids_json, out_json=out_json)
            else:
                print("Error: --nodeids-json required for pytest-run-batch", file=sys.stderr)
                sys.exit(1)
    else:
        # Find the Rust binary - look in package directory first, then PATH
        package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        binary_paths = [
            os.path.join(package_dir, "target", "release", "turboplex.exe"),
            os.path.join(package_dir, "target", "debug", "turboplex.exe"),
            "turboplex.exe",  # PATH
        ]
        
        binary = None
        for path in binary_paths:
            if os.path.exists(path):
                binary = path
                break
        
        if binary is None:
            # Try to find it in common locations
            possible_paths = [
                r"C:\test\testengine\turboptest\target\release\turboplex.exe",
                r"C:\test\testengine\turboptest\target\debug\turboplex.exe",
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    binary = path
                    break
        
        if binary is None:
            print("Error: turboptest binary not found. Please build it with: cargo build --release", file=sys.stderr)
            sys.exit(1)
        
        # Forward all arguments to the Rust binary
        result = subprocess.run([binary] + sys.argv[1:])
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
