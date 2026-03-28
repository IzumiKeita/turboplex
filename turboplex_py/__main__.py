#!/usr/bin/env python3
"""Wrapper that calls the Rust binary for main commands, but handles collect/run directly."""

import os
import sys
import subprocess

def main():
    # Check if this is a subprocess call from Rust binary (via environment variable)
    if os.environ.get("TURBOTEST_SUBPROCESS") == "1":
        # We're inside Rust's subprocess call - handle directly without recursion
        from turboplex_py.collector import collect_main
        from turboplex_py.runner import run_main
        
        if len(sys.argv) > 1 and sys.argv[1] == "collect":
            paths = sys.argv[2:] if len(sys.argv) > 2 else ["."]
            collect_main(paths)
        elif len(sys.argv) > 1 and sys.argv[1] == "run":
            path = None
            qual = None
            i = 2
            while i < len(sys.argv):
                if sys.argv[i] == "--path" and i + 1 < len(sys.argv):
                    path = sys.argv[i + 1]
                    i += 2
                elif sys.argv[i] == "--qual" and i + 1 < len(sys.argv):
                    qual = sys.argv[i + 1]
                    i += 2
                else:
                    i += 1
            if path and qual:
                run_main(path, qual)
            else:
                print("Error: --path and --qual required for run", file=sys.stderr)
                sys.exit(1)
        else:
            print("Error: Unknown subcommand", file=sys.stderr)
            sys.exit(1)
        return
    
    # Normal mode - check if this is a direct call to collect or run (subcommand)
    if len(sys.argv) > 1 and sys.argv[1] in ("collect", "run"):
        # Import and run the Python module directly
        from turboplex_py.collector import collect_main
        from turboplex_py.runner import run_main
        
        if sys.argv[1] == "collect":
            # collect takes paths as remaining args
            collect_main(sys.argv[2:] if len(sys.argv) > 2 else ["."])
        elif sys.argv[1] == "run":
            # run takes --path and --qual
            args = sys.argv[2:]
            path = None
            qual = None
            i = 0
            while i < len(args):
                if args[i] == "--path" and i + 1 < len(args):
                    path = args[i + 1]
                    i += 2
                elif args[i] == "--qual" and i + 1 < len(args):
                    qual = args[i + 1]
                    i += 2
                else:
                    i += 1
            if path and qual:
                run_main(path, qual)
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
