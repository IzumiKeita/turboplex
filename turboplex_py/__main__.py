#!/usr/bin/env python3
"""Wrapper that calls the Rust binary for main commands, but handles collect/run directly."""

# CRITICAL: Bootstrap SQLAlchemy patcher BEFORE any imports that might import SQLAlchemy
# This ensures all DB operations are intercepted and made lazy
import os
if os.environ.get("TURBOTEST_SUBPROCESS") == "1":
    try:
        from turboplex_py.pytest_bootstrap import ensure_patchers
        ensure_patchers()
    except Exception:
        pass  # Ignore bootstrap errors, we'll handle them later

import sys
import subprocess

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


def main():
    # Check if this is a subprocess call from Rust binary (via environment variable)
    if os.environ.get("TURBOTEST_SUBPROCESS") == "1":
        # We're inside Rust's subprocess call - handle directly without recursion
        from turboplex_py.collector import collect_main
        from turboplex_py.runner import run_main
        
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
        else:
            print("Error: Unknown subcommand", file=sys.stderr)
            sys.exit(1)
        return
    
    # Normal mode - check if this is a direct call to collect or run (subcommand)
    if len(sys.argv) > 1 and sys.argv[1] in ("collect", "run"):
        # Set TURBOTEST_SUBPROCESS to indicate we're in TurboPlex mode
        os.environ["TURBOTEST_SUBPROCESS"] = "1"
        
        # Bootstrap patchers for direct mode too
        try:
            from turboplex_py.pytest_bootstrap import ensure_patchers
            ensure_patchers()
        except Exception:
            pass
        
        # Import and run the Python module directly
        from turboplex_py.collector import collect_main
        from turboplex_py.runner import run_main
        
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
