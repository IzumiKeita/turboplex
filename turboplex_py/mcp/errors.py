"""MCP errors and exceptions."""
from __future__ import annotations

from typing import Any


class ToolTimeout(Exception):
    """Raised when a tool operation times out."""

    def __init__(self, *, phase: str, timeout_s: float):
        super().__init__(f"{phase} timed out after {timeout_s}s")
        self.phase = phase
        self.timeout_s = timeout_s

    def as_error(self) -> dict:
        return {
            "code": "timeout",
            "phase": self.phase,
            "timeout_s": self.timeout_s,
            "message": str(self),
        }


class ToolSubprocessError(Exception):
    """Raised when a subprocess fails."""

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
            "code": "subprocess_failed",
            "phase": self.phase,
            "returncode": self.returncode,
            "message": str(self),
        }
        if self.stderr is not None:
            out["stderr"] = self.stderr
        if self.stdout is not None:
            out["stdout"] = self.stdout
        return out


def classify_db_error(exc: Any) -> dict:
    """Classify database exception into generic + vendor code."""
    msg = str(exc) if exc is not None else ""
    lower = msg.lower()
    vendor_code = None
    generic = "db_internal"

    if "deadlock" in lower:
        generic = "db_deadlock"
    elif "timeout" in lower or "timed out" in lower:
        generic = "db_timeout"
    elif "integrity" in lower or "duplicate" in lower or "unique" in lower or "foreign key" in lower:
        generic = "db_integrity_violation"
    elif "connect" in lower or "connection" in lower:
        generic = "db_connection"
    elif "syntax" in lower:
        generic = "db_syntax"

    # Try common attributes from DB drivers
    for attr in ("sqlstate", "pgcode", "errno", "code"):
        if hasattr(exc, attr):
            try:
                val = getattr(exc, attr)
                if val is not None and val != "":
                    vendor_code = val
                    break
            except Exception:
                pass

    return {"code": generic, "vendor_code": vendor_code, "message": msg}
