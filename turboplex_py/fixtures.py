"""Function-scope fixtures with simple dependency chain (v1)."""

from __future__ import annotations

import importlib.util
import inspect
import os
from types import ModuleType
from typing import Any, Callable


def fixture(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Register a fixture callable by its function name on the module."""
    setattr(fn, "_tt_fixture", True)
    # Use the module globals dict so registration works during importlib exec_module
    reg = fn.__globals__.setdefault("__tt_fixtures__", {})
    if not isinstance(reg, dict):
        raise TypeError("@fixture registry corrupted")
    reg[fn.__name__] = fn
    return fn


def _load_turbofix_fixtures(test_module: ModuleType) -> dict[str, Callable[..., Any]]:
    """Load fixtures from turbofix.py in the same directory as the test module."""
    test_file = getattr(test_module, "__file__", None)
    if not test_file:
        return {}
    
    test_dir = os.path.dirname(test_file)
    turbofix_path = os.path.join(test_dir, "turbofix.py")
    
    if not os.path.exists(turbofix_path):
        return {}
    
    try:
        spec = importlib.util.spec_from_file_location("turbofix", turbofix_path)
        if spec is None or spec.loader is None:
            return {}
        turbofix_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(turbofix_mod)
        return _fixtures_map(turbofix_mod)
    except Exception:
        return {}


def _fixtures_map(mod: ModuleType) -> dict[str, Callable[..., Any]]:
    raw = mod.__dict__.get("__tt_fixtures__")
    return dict(raw) if isinstance(raw, dict) else {}


def _resolve_one(
    mod: ModuleType,
    fixtures: dict[str, Callable[..., Any]],
    name: str,
    cache: dict[str, Any],
    stack: set[str],
) -> Any:
    if name in cache:
        return cache[name]
    if name in stack:
        raise RuntimeError(f"cyclic fixture dependency involving {name!r}")
    if name not in fixtures:
        raise RuntimeError(f"unknown fixture {name!r}")

    fix_fn = fixtures[name]
    if not getattr(fix_fn, "_tt_fixture", False):
        raise RuntimeError(f"{name!r} is not a @fixture")

    stack.add(name)
    
    # Check if it's a generator function (uses yield)
    import inspect
    if inspect.isgeneratorfunction(fix_fn):
        # First resolve dependencies for the generator function
        sig = inspect.signature(fix_fn)
        kwargs: dict[str, Any] = {}
        for pname, p in sig.parameters.items():
            if p.kind != inspect.Parameter.POSITIONAL_OR_KEYWORD:
                raise RuntimeError(
                    f"fixture {name!r}: parameter {pname!r} must be positional/keyword"
                )
            if pname in fixtures:
                kwargs[pname] = _resolve_one(mod, fixtures, pname, cache, stack)
            elif p.default is inspect.Parameter.empty:
                raise RuntimeError(
                    f"fixture {name!r}: no registered fixture for parameter {pname!r}"
                )
        
        # Create generator with resolved dependencies and get first value
        gen = fix_fn(**kwargs)
        try:
            value = next(gen)
            # Store generator for potential cleanup (not implemented yet)
            cache[name + "_generator"] = gen
            cache[name] = value
            return value
        except StopIteration:
            raise RuntimeError(f"fixture {name!r} generator yielded no value")
    else:
        try:
            sig = inspect.signature(fix_fn)
            kwargs: dict[str, Any] = {}
            for pname, p in sig.parameters.items():
                if p.kind != inspect.Parameter.POSITIONAL_OR_KEYWORD:
                    raise RuntimeError(
                        f"fixture {name!r}: parameter {pname!r} must be positional/keyword"
                    )
                if pname in fixtures:
                    kwargs[pname] = _resolve_one(mod, fixtures, pname, cache, stack)
                elif p.default is inspect.Parameter.empty:
                    raise RuntimeError(
                        f"fixture {name!r}: no registered fixture for parameter {pname!r}"
                    )
            cache[name] = fix_fn(**kwargs)
            return cache[name]
        finally:
            stack.remove(name)


def build_kwargs_for_callable(
    mod: ModuleType,
    fn: Callable[..., Any],
    *,
    skip_self: bool,
    parametrize_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build keyword args for *fn* from registered fixtures (function-scope, one shot)."""
    fixtures = _fixtures_map(mod)
    
    # Also load fixtures from turbofix.py in the test directory
    turbofix_fixtures = _load_turbofix_fixtures(mod)
    fixtures.update(turbofix_fixtures)
    
    sig = inspect.signature(fn)
    cache: dict[str, Any] = {}
    stack: set[str] = set()
    kwargs: dict[str, Any] = {}

    # Pre-inject parametrize kwargs if provided
    if parametrize_kwargs:
        kwargs.update(parametrize_kwargs)

    seen_self = False
    for pname, p in sig.parameters.items():
        if skip_self and not seen_self and pname == "self":
            seen_self = True
            continue
        if p.kind != inspect.Parameter.POSITIONAL_OR_KEYWORD:
            raise RuntimeError(
                f"test callable: parameter {pname!r} must be positional/keyword (v1)"
            )

        # Skip if already provided by parametrize
        if pname in kwargs:
            continue

        if pname in fixtures:
            kwargs[pname] = _resolve_one(mod, fixtures, pname, cache, stack)
        elif p.default is inspect.Parameter.empty:
            raise RuntimeError(
                f"parameter {pname!r} has no @fixture and no default"
            )

    return kwargs
