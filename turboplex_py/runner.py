"""Run a single test; print one JSON line to stdout; tracebacks on stderr."""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import pathlib
import sys
import time
import traceback
from typing import Any, Callable

# Configure logging to stderr to keep stdout clean for JSON
for h in logging.root.handlers[:]:
    logging.root.removeHandler(h)
h = logging.StreamHandler(sys.stderr)
h.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
logging.root.addHandler(h)
logging.root.setLevel(logging.CRITICAL)

# Disable SQLAlchemy and other noisy libraries
logging.getLogger("sqlalchemy.engine").setLevel(logging.CRITICAL)
logging.getLogger("sqlalchemy").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

from .fixtures import build_kwargs_for_callable
from .markers import skip_check


def _emit(
    passed: bool,
    duration_ms: int,
    error: str | None = None,
    *,
    skipped: bool = False,
    skip_reason: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "passed": passed,
        "duration_ms": duration_ms,
        "error": error,
    }
    if skipped:
        payload["skipped"] = True
    if skip_reason is not None:
        payload["skip_reason"] = skip_reason
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def _load_module(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"turbopy_run_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get_parametrize_kwargs(fn: Callable[..., Any], parametrize_index: int) -> dict[str, Any]:
    """Extract parameters from @pytest.mark.parametrize for a given index."""
    parametrize_marker = None
    if hasattr(fn, "pytestmark"):
        for mark in fn.pytestmark:
            if hasattr(mark, "name") and mark.name == "parametrize":
                parametrize_marker = mark
                break
            # Handle list of markers
            if isinstance(mark, list):
                for m in mark:
                    if hasattr(m, "name") and m.name == "parametrize":
                        parametrize_marker = m
                        break
    
    if not parametrize_marker:
        return {}
    
    try:
        if hasattr(parametrize_marker, "args"):
            args = parametrize_marker.args
            if len(args) >= 2:
                arg_names = args[0]
                test_values = args[1]
                
                if isinstance(arg_names, str):
                    arg_names = [a.strip() for a in arg_names.split(",")]
                
                # Get the specific set of values for this test
                values = test_values[parametrize_index]
                if not isinstance(values, (list, tuple)):
                    values = (values,)
                
                # Build kwargs from parameters
                kwargs = {}
                for i, arg_name in enumerate(arg_names):
                    if i < len(values):
                        kwargs[arg_name] = values[i]
                return kwargs
    except Exception as e:
        raise RuntimeError(f"Could not resolve parametrize for index {parametrize_index}: {e}")
    
    return {}


def _invoke_function(mod, fn: Callable[..., Any], parametrize_index: int = None) -> None:
    do_skip, reason = skip_check(fn)
    if do_skip:
        raise _Skipped(reason or "")

    # Get parametrize kwargs BEFORE calling build_kwargs_for_callable
    parametrize_kwargs = None
    if parametrize_index is not None:
        parametrize_kwargs = _get_parametrize_kwargs(fn, parametrize_index)
    
    kwargs = build_kwargs_for_callable(mod, fn, skip_self=False, parametrize_kwargs=parametrize_kwargs)
    
    fn(**kwargs)


def _invoke_method(mod, cls: type, meth: Callable[..., Any]) -> None:
    do_skip, reason = skip_check(meth)
    if do_skip:
        raise _Skipped(reason or "")

    inst = cls()
    kwargs = build_kwargs_for_callable(mod, meth, skip_self=True)
    meth(inst, **kwargs)


class _Skipped(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def run_main(path_str: str, qual: str) -> None:
    path = pathlib.Path(path_str)
    if not path.is_file():
        _emit(False, 0, f"not a file: {path_str}")
        sys.exit(1)

    # Check for parametrize index in qualname (e.g., "test_func[0]")
    parametrize_index = None
    if "[" in qual and qual.endswith("]"):
        base_qual, idx_str = qual.rsplit("[", 1)
        try:
            parametrize_index = int(idx_str.rstrip("]"))
            qual = base_qual
        except ValueError:
            pass

    t0 = time.perf_counter()
    try:
        mod = _load_module(path.resolve())
    except Exception:
        dt = int((time.perf_counter() - t0) * 1000)
        traceback.print_exc(file=sys.stderr)
        _emit(False, dt, "import failed")
        sys.exit(1)

    try:
        if "::" in qual:
            cname, mname = qual.split("::", 1)
            cls = getattr(mod, cname)
            meth = getattr(cls, mname)
            if not inspect.isfunction(meth):
                raise RuntimeError(f"{qual!r} is not a plain instance method in v1")
            _invoke_method(mod, cls, meth)
        else:
            fn = getattr(mod, qual)
            if not inspect.isfunction(fn):
                raise RuntimeError(f"{qual!r} is not a function")
            _invoke_function(mod, fn, parametrize_index=parametrize_index)
    except _Skipped as sk:
        dt = int((time.perf_counter() - t0) * 1000)
        _emit(True, dt, None, skipped=True, skip_reason=sk.reason or None)
        sys.exit(0)
    except Exception as e:
        dt = int((time.perf_counter() - t0) * 1000)
        traceback.print_exc(file=sys.stderr)
        _emit(False, dt, str(e))
        sys.exit(1)

    dt = int((time.perf_counter() - t0) * 1000)
    _emit(True, dt, None)
    sys.exit(0)
