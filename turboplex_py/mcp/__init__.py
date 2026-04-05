"""MCP Server package for TurboPlex."""

from .errors import ToolTimeout, ToolSubprocessError, classify_db_error, classify_resource_error
from .config import load_mcp_config, McpConfig
from .schema import ToolError, ToolPayload, payload_ok, payload_error
from .utils import (
    uuid_v7,
    json_normalize,
    resolve_python_executable,
    # Pre-Flight Health Check (v0.3.6+)
    HealthCheckError,
    HealthCheckReport,
    check_postgres_connectivity,
    check_env_file,
    check_dependency_versions,
    run_health_checks,
    preflight_guard,
    preflight_guard_decorator,
    # Schema Sync Guard (SSG) - v0.3.6+
    SchemaSyncError,
    find_alembic_config,
    get_alembic_head,
    get_database_version,
    check_alembic_sync,
    # Async Buffered Logging - v0.3.6+
    TplexLogger,
    get_tplex_logger,
    log_to_tplex,
    log_autopsy,
    log_health_check,
    log_schema_sync,
    # Autopsia Automática (v0.3.6+)
    capture_autopsy,
    autopsy_from_dict,
    AutopsyJSONEncoder,
    _scrub_value,
)
from .io import StdoutJsonRpcGuard, install_stdio_guard, tool_json, attach_logs
from .win32 import kill_tree_windows, win_assign_job_object, win_close_job_object
from .subprocess import (
    subprocess_env,
    env_timeout_s,
    safe_close,
    terminate_process,
    run_popen_with_drain_and_heartbeat,
)
from .collect import turboplex_collect, turboplex_run_one, turboplex_doctor, pytest_collect, pytest_run
# Transactional Testing (TPX Inyector) - v0.3.6+
from .transactional import (
    install_transactional_interceptor,
    uninstall_transactional_interceptor,
    begin_test_transaction,
    end_test_transaction,
    TransactionalTestContext,
    patch_sqlalchemy_if_imported,
)

__all__ = [
    "ToolTimeout",
    "ToolSubprocessError",
    "classify_db_error",
    "classify_resource_error",
    "McpConfig",
    "load_mcp_config",
    "ToolError",
    "ToolPayload",
    "payload_ok",
    "payload_error",
    "uuid_v7",
    "json_normalize",
    "resolve_python_executable",
    # Pre-Flight Health Check (v0.3.6+)
    "HealthCheckError",
    "HealthCheckReport",
    "check_postgres_connectivity",
    "check_env_file",
    "check_dependency_versions",
    "run_health_checks",
    "preflight_guard",
    "preflight_guard_decorator",
    # Schema Sync Guard (SSG) - v0.3.6+
    "SchemaSyncError",
    "find_alembic_config",
    "get_alembic_head",
    "get_database_version",
    "check_alembic_sync",
    # Async Buffered Logging - v0.3.6+
    "TplexLogger",
    "get_tplex_logger",
    "log_to_tplex",
    "log_autopsy",
    "log_health_check",
    "log_schema_sync",
    # Autopsia Automática (v0.3.6+)
    "capture_autopsy",
    "autopsy_from_dict",
    "AutopsyJSONEncoder",
    "_scrub_value",
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
    # Transactional Testing (TPX Inyector) - v0.3.6+
    "install_transactional_interceptor",
    "uninstall_transactional_interceptor",
    "begin_test_transaction",
    "end_test_transaction",
    "TransactionalTestContext",
    "patch_sqlalchemy_if_imported",
]
