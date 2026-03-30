"""MCP Server package for TurboPlex."""

from .errors import ToolTimeout, ToolSubprocessError
from .utils import uuid_v7, json_normalize, resolve_python_executable
from .io import StdoutJsonRpcGuard, install_stdio_guard, tool_json, attach_logs
from .win32 import kill_tree_windows, win_assign_job_object, win_close_job_object
from .subprocess import (
    subprocess_env,
    env_timeout_s,
    safe_close,
    terminate_process,
    run_popen_with_drain_and_heartbeat,
)
from .collect import turboplex_collect, turboplex_run_one, pytest_collect, pytest_run

__all__ = [
    "ToolTimeout",
    "ToolSubprocessError",
    "uuid_v7",
    "json_normalize",
    "resolve_python_executable",
    "StdoutJsonRpcGuard",
    "install_stdio_guard",
    "tool_json",
    "attach_logs",
    "kill_tree_windows",
    "win_assign_job_object",
    "win_close_job_object",
    "subprocess_env",
    "env_timeout_s",
    "safe_close",
    "terminate_process",
    "run_popen_with_drain_and_heartbeat",
    "turboplex_collect",
    "turboplex_run_one",
    "pytest_collect",
    "pytest_run",
]
