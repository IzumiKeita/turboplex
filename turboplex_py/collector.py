"""Discover test functions; print a single JSON object to stdout (no other prints)."""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import os
import pathlib
import sys
from decimal import Decimal
from typing import Any

# Suppress SQLAlchemy logging via environment BEFORE importing
os.environ["SQLALCHEMY_SILENCE_UBER_WARNING"] = "1"
os.environ["SQLALCHEMY_LOG"] = "0"

# Completely suppress any logging before imports
import logging
logging.disable(logging.CRITICAL)

# Configure logging to stderr to keep stdout clean for JSON
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
logging.root.addHandler(_handler)
logging.root.setLevel(logging.WARNING)

# Also disable SQLAlchemy logging 
logging.getLogger("sqlalchemy.engine").setLevel(logging.ERROR)
logging.getLogger("sqlalchemy").setLevel(logging.ERROR)
logging.getLogger("sqlalchemy.pool").setLevel(logging.ERROR)

class DecimalEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def _get_pytest_parametrize(obj):
    """Extract pytest.mark.parametrize info from a test function."""
    if not hasattr(obj, "__wrapped__"):
        return None
    
    # Try to get parametrize from pytest.mark
    marker = getattr(obj, "pytestmark", [])
    if isinstance(marker, list):
        for m in marker:
            if hasattr(m, "name") and m.name == "parametrize":
                return m
            if hasattr(m, "args") and len(m.args) >= 2:
                return m
    return None


def _expand_parametrized_tests(mod, name, obj, p_str):
    """Expand tests with @pytest.mark.parametrize into multiple test cases."""
    items = []
    
    # Try to get parametrize info from the test function
    parametrize_marker = None
    
    # Check for pytest.mark.parametrize
    if hasattr(obj, "pytestmark"):
        for mark in obj.pytestmark:
            if hasattr(mark, "name") and mark.name == "parametrize":
                parametrize_marker = mark
                break
            # Handle list of markers
            if isinstance(mark, list):
                for m in mark:
                    if hasattr(m, "name") and m.name == "parametrize":
                        parametrize_marker = m
                        break
    
    if parametrize_marker is None:
        # No parametrize, return single item
        items.append({
            "path": p_str,
            "qualname": name,
            "lineno": int(obj.__code__.co_firstlineno),
            "kind": "function",
        })
        return items
    
    # Extract parameter info
    try:
        # Try different ways to get the parameters
        if hasattr(parametrize_marker, "args"):
            args = parametrize_marker.args
        else:
            return items
        
        if len(args) < 2:
            return items
            
        # Get argument names and values
        if isinstance(args[0], str):
            arg_names = [a.strip() for a in args[0].split(",")]
        elif isinstance(args[0], (list, tuple)):
            arg_names = list(args[0])
        else:
            return items
            
        test_values = args[1]
        if not isinstance(test_values, (list, tuple)):
            return items
        
        # Create a parametrized test for each value
        for i, values in enumerate(test_values):
            if not isinstance(values, (list, tuple)):
                values = (values,)
            
            # Create parameter string for this test case
            if len(arg_names) == 1:
                param_id = str(values[0])
            else:
                param_id = "_".join(str(v) for v in values)
            
            # Truncate long param IDs
            if len(param_id) > 30:
                param_id = param_id[:30] + "..."
            
            items.append({
                "path": p_str,
                "qualname": f"{name}[{i}]",  # Use index for uniqueness
                "lineno": int(obj.__code__.co_firstlineno),
                "kind": "function",
                "parametrize": {
                    "argnames": arg_names,
                    "values": values,
                    "index": i,
                }
            })
    except Exception as e:
        # If we can't parse parametrize, return single item
        print(f"Warning: Could not parse parametrize for {name}: {e}", file=sys.stderr)
        items.append({
            "path": p_str,
            "qualname": name,
            "lineno": int(obj.__code__.co_firstlineno),
            "kind": "function",
        })
    
    return items


def _iter_test_files(paths: list[str]) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for raw in paths:
        p = pathlib.Path(raw)
        if p.is_file() and (p.name.startswith("test_") or p.name.endswith("_test.py")) and p.suffix == ".py":
            out.append(p.resolve())
            continue
        if p.is_dir():
            out.extend(sorted(p.resolve().rglob("test_*.py")))
            out.extend(sorted(p.resolve().rglob("*_test.py")))
    seen: set[pathlib.Path] = set()
    uniq: list[pathlib.Path] = []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def _load_module(path: pathlib.Path, timeout_s: float = 30.0, conftest_dir: str | None = None):
    """Load module with optional timeout to prevent hanging on heavy imports.
    
    Activates SQLAlchemy lazy patcher if conftest.py exists to avoid DB operations
    during test collection.
    """
    import threading
    import signal
    
    # Check if there's a conftest.py in the same directory or parent
    patcher = None
    test_dir = path.parent
    for parent in [test_dir] + list(test_dir.parents):
        conftest = parent / "conftest.py"
        if conftest.exists():
            try:
                from .db_lazy_patcher import get_patcher
                patcher = get_patcher()
                patcher.patch_all()
                logging.debug(f"Lazy patcher activated for {path} (found conftest at {conftest})")
            except Exception as e:
                logging.warning(f"Could not activate lazy patcher: {e}")
            break
    
    spec = importlib.util.spec_from_file_location(f"turbopy_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    
    # Use threading to implement timeout for exec_module
    result = {"exc": None, "done": False}
    
    def _exec():
        try:
            spec.loader.exec_module(mod)
            result["done"] = True
        except Exception as e:
            result["exc"] = e
    
    thread = threading.Thread(target=_exec, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)
    
    if not result["done"] and result["exc"] is None:
        raise TimeoutError(f"Module import timed out after {timeout_s}s: {path}")
    if result["exc"]:
        raise result["exc"]
    
    return mod


def collect(paths: list[str], import_timeout_s: float = 60.0) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in _iter_test_files(paths):
        try:
            mod = _load_module(path, timeout_s=import_timeout_s)
        except TimeoutError as e:
            print(f"collect: timeout importing {path}: {e}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"collect: skip import {path}: {e}", file=sys.stderr)
            continue
        p_str = str(path)
        for name, obj in inspect.getmembers(mod):
            if name.startswith("test_") and inspect.isfunction(obj) and obj.__module__ == mod.__name__:
                # Check for @pytest.mark.parametrize and expand if needed
                expanded = _expand_parametrized_tests(mod, name, obj, p_str)
                items.extend(expanded)
        for name, cls in inspect.getmembers(mod, inspect.isclass):
            if not name.startswith("Test"):
                continue
            if cls.__module__ != mod.__name__:
                continue
            for mname, meth in inspect.getmembers(cls, inspect.isfunction):
                if not mname.startswith("test_"):
                    continue
                if meth.__qualname__.split(".")[0] != name:
                    continue
                items.append(
                    {
                        "path": p_str,
                        "qualname": f"{name}::{mname}",
                        "lineno": int(meth.__code__.co_firstlineno),
                        "kind": "method",
                    }
                )
    return items


def collect_main(paths: list[str], out_json: str | None = None) -> None:
    payload = {"items": collect(paths)}
    if out_json:
        p = pathlib.Path(out_json)
        p.write_text(json.dumps(payload, cls=DecimalEncoder, ensure_ascii=False), encoding="utf-8")
        return
    json.dump(payload, sys.stdout, cls=DecimalEncoder)
    sys.stdout.write("\n")
