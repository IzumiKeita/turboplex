"""Minimal skip / skipif markers (pytest-like, v1)."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class _Skip:
    """``@skip`` / ``@skip()`` / ``@skip("reason")`` / ``@skip(reason="…")``."""

    def __call__(self, arg: Any = None, *, reason: str = "") -> Any:
        if callable(arg):
            setattr(arg, "__tt_skip__", (True, reason or ""))
            return arg
        msg = arg if isinstance(arg, str) else reason

        def deco(fn: F) -> F:
            setattr(fn, "__tt_skip__", (True, msg or ""))
            return fn

        return deco


skip = _Skip()


def skipif(condition: bool, *, reason: str = "") -> Callable[[F], F]:
    """Skip when *condition* is true (evaluated at import/decorator time)."""

    def deco(fn: F) -> F:
        setattr(fn, "__tt_skipif__", (bool(condition), reason))
        return fn

    return deco


def skip_check(fn: Callable[..., Any]) -> tuple[bool, str | None]:
    """Return (should_skip, reason_or_none)."""
    sk = getattr(fn, "__tt_skip__", None)
    if sk is not None:
        active, msg = sk
        if active:
            text = msg or "skipped"
            return True, text

    si = getattr(fn, "__tt_skipif__", None)
    if si is not None:
        cond, msg = si
        if cond:
            text = msg or "skipped (skipif)"
            return True, text

    return False, None

