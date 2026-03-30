"""MCP errors and exceptions."""


class ToolTimeout(Exception):
    """Raised when a tool operation times out."""

    def __init__(self, *, phase: str, timeout_s: float):
        super().__init__(f"{phase} timed out after {timeout_s}s")
        self.phase = phase
        self.timeout_s = timeout_s

    def as_error(self) -> dict:
        return {
            "kind": "timeout",
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
            "kind": "subprocess_failed",
            "phase": self.phase,
            "returncode": self.returncode,
            "message": str(self),
        }
        if self.stderr is not None:
            out["stderr"] = self.stderr
        if self.stdout is not None:
            out["stdout"] = self.stdout
        return out
