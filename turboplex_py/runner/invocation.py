"""Function and method invocation with pytest bridge support."""

from __future__ import annotations

import inspect
from typing import Any, Callable

from ..fixtures import build_kwargs_for_callable
from ..markers import skip_check
from ..compat.integration import get_compat_mode


_PYTEST_SKIP_EXCEPTION: Any = None
_PYTEST_SKIP_EXCEPTION_READY = False
_LAST_FIXTURE_SOURCE: str | None = None


class _Skipped(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _set_last_fixture_source(source: str | None) -> None:
    global _LAST_FIXTURE_SOURCE
    _LAST_FIXTURE_SOURCE = source


def get_last_fixture_source() -> str | None:
    return _LAST_FIXTURE_SOURCE


def clear_last_fixture_source() -> None:
    _set_last_fixture_source(None)


def _get_pytest_skip_exception() -> Any:
    global _PYTEST_SKIP_EXCEPTION, _PYTEST_SKIP_EXCEPTION_READY
    if _PYTEST_SKIP_EXCEPTION_READY:
        return _PYTEST_SKIP_EXCEPTION
    _PYTEST_SKIP_EXCEPTION_READY = True
    try:
        import pytest

        _PYTEST_SKIP_EXCEPTION = getattr(getattr(pytest, "skip", None), "Exception", None)
    except Exception:
        _PYTEST_SKIP_EXCEPTION = None
    return _PYTEST_SKIP_EXCEPTION


def _invoke_function_original(mod, fn, parametrize_index=None, *, skip_self: bool = False):
    """Comportamiento original sin bridge."""
    from .parametrize import _get_parametrize_kwargs

    do_skip, reason = skip_check(fn)
    if do_skip:
        raise _Skipped(reason or "")

    # Get parametrize kwargs BEFORE calling build_kwargs_for_callable
    parametrize_kwargs = None
    if parametrize_index is not None:
        parametrize_kwargs = _get_parametrize_kwargs(fn, parametrize_index)

    fixture_sources: dict[str, str] = {}
    kwargs = build_kwargs_for_callable(
        mod,
        fn,
        skip_self=skip_self,
        parametrize_kwargs=parametrize_kwargs,
        fixture_sources=fixture_sources,
    )
    if fixture_sources:
        _set_last_fixture_source("native")
    elif _LAST_FIXTURE_SOURCE is None:
        _set_last_fixture_source(None)
    return kwargs


def _invoke_with_optional_bridge(
    mod,
    callable_obj: Callable[..., Any],
    *,
    test_name: str,
    call_target: Callable[[dict[str, Any]], None],
    parametrize_index: int | None = None,
    skip_self: bool = False,
) -> None:
    """Invoca funciones/métodos usando bridge pytest si aplica."""
    _set_last_fixture_source(None)

    sig = inspect.signature(callable_obj)
    params = [p for p in sig.parameters.keys() if p not in ("self", "cls")]

    if params:
        test_file = getattr(mod, "__file__", None)
        if test_file:
            compat_mode = get_compat_mode(test_file)
            if compat_mode:
                try:
                    compat_mode.session_start()
                    compat_mode.setup_test(test_name)
                    wrapped = compat_mode.prepare_test(callable_obj)
                    wrapped()
                    compat_mode.teardown_test(test_name, "passed")
                    _set_last_fixture_source("bridge")
                    return
                except Exception:
                    compat_mode.teardown_test(test_name, "failed")
                    raise

    kwargs = _invoke_function_original(
        mod,
        callable_obj,
        parametrize_index=parametrize_index,
        skip_self=skip_self,
    )
    call_target(kwargs)


def _invoke_method(mod, cls: type, meth: Callable[..., Any]) -> None:
    do_skip, reason = skip_check(meth)
    if do_skip:
        raise _Skipped(reason or "")

    inst = cls()
    _invoke_with_optional_bridge(
        mod,
        meth,
        test_name=meth.__name__,
        skip_self=True,
        call_target=lambda kwargs: meth(inst, **kwargs),
    )


def _invoke_function(mod, fn: Callable[..., Any], parametrize_index: int = None) -> None:
    """Ejecuta función, usando bridge de pytest si es necesario."""
    do_skip, reason = skip_check(fn)
    if do_skip:
        raise _Skipped(reason or "")

    _invoke_with_optional_bridge(
        mod,
        fn,
        test_name=fn.__name__,
        parametrize_index=parametrize_index,
        skip_self=False,
        call_target=lambda kwargs: fn(**kwargs),
    )
