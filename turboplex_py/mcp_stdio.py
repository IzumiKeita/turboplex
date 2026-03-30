import os
import sys


class StdoutJsonRpcGuard:
    def __init__(self, underlying, *, mode: str, max_buffer_chars: int = 2_000_000):
        self._u = underlying
        self._mode = mode
        self._buf = ""
        self._max = max_buffer_chars
        self._real_stderr = getattr(sys, "__stderr__", None) or sys.stderr

    def write(self, s):
        if not s:
            return 0
        text = str(s)
        self._buf += text
        if len(self._buf) > self._max:
            self._handle_non_jsonrpc(self._buf)
            self._buf = ""
            return len(text)
        while "\n" in self._buf:
            line, rest = self._buf.split("\n", 1)
            self._buf = rest
            if line == "":
                self._u.write("\n")
                continue
            if self._is_jsonrpc_line(line):
                self._u.write(line + "\n")
            else:
                self._handle_non_jsonrpc(line + "\n")
        return len(text)

    def flush(self):
        if self._buf:
            if self._is_jsonrpc_line(self._buf):
                self._u.write(self._buf)
            else:
                self._handle_non_jsonrpc(self._buf)
            self._buf = ""
        return self._u.flush()

    def _is_jsonrpc_line(self, line: str) -> bool:
        try:
            import json

            obj = json.loads(line)
            return isinstance(obj, dict) and obj.get("jsonrpc") == "2.0"
        except Exception:
            return False

    def _handle_non_jsonrpc(self, text: str) -> None:
        if self._mode == "failfast":
            raise RuntimeError("stdout non-JSON-RPC blocked")
        self._real_stderr.write(text)
        try:
            self._real_stderr.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._u, name)


def install_stdio_guard() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception:
        pass

    mode = os.environ.get("TPX_MCP_STDOUT_MODE", "redirect").strip().lower()
    sys.stdout = StdoutJsonRpcGuard(sys.stdout, mode=mode)

