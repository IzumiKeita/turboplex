"""Centralized MCP runtime configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(names: tuple[str, ...], default: float) -> float:
    for name in names:
        raw = os.environ.get(name)
        if raw is None:
            continue
        try:
            return float(raw)
        except Exception:
            continue
    return default


def _env_int(names: tuple[str, ...], default: int) -> int:
    for name in names:
        raw = os.environ.get(name)
        if raw is None:
            continue
        try:
            return int(raw)
        except Exception:
            continue
    return default


@dataclass(frozen=True)
class McpConfig:
    pytest_collect_timeout_s: float
    pytest_run_timeout_s: float
    turboplex_collect_timeout_s: float
    turboplex_run_timeout_s: float
    test_timeout_s: float
    heartbeat_s: float
    terminate_grace_s: float
    drain_max_chars: int
    logs_max_chars: int
    db_strict_dirty: bool
    db_metrics_enabled: bool
    db_isolation_mode: str
    db_worker_prefix: str
    db_dirty_track_max_tables: int


def load_mcp_config() -> McpConfig:
    def _env_bool(names: tuple[str, ...], default: bool) -> bool:
        for name in names:
            raw = os.environ.get(name)
            if raw is None:
                continue
            v = raw.strip().lower()
            if v in ("1", "true", "yes", "on"):
                return True
            if v in ("0", "false", "no", "off"):
                return False
        return default

    def _env_str(names: tuple[str, ...], default: str) -> str:
        for name in names:
            raw = os.environ.get(name)
            if raw is not None and raw.strip():
                return raw.strip()
        return default

    return McpConfig(
        pytest_collect_timeout_s=_env_float(
            ("TPX_MCP_PYTEST_COLLECT_TIMEOUT_S", "TPX_PYTEST_COLLECT_TIMEOUT_S"),
            120.0,
        ),
        pytest_run_timeout_s=_env_float(
            ("TPX_MCP_PYTEST_RUN_TIMEOUT_S", "TPX_PYTEST_RUN_TIMEOUT_S"),
            60.0,
        ),
        turboplex_collect_timeout_s=_env_float(("TPX_MCP_TURBOPLEX_COLLECT_TIMEOUT_S",), 120.0),
        turboplex_run_timeout_s=_env_float(("TPX_MCP_TURBOPLEX_RUN_TIMEOUT_S",), 60.0),
        test_timeout_s=_env_float(("TPX_MCP_TEST_TIMEOUT_S",), 120.0),
        heartbeat_s=_env_float(("TPX_MCP_HEARTBEAT_S",), 1.0),
        terminate_grace_s=_env_float(("TPX_MCP_TERMINATE_GRACE_S",), 2.0),
        drain_max_chars=_env_int(("TPX_MCP_DRAIN_MAX_CHARS",), 2_000_000),
        logs_max_chars=_env_int(("TPX_MCP_LOGS_MAX_CHARS",), 20_000),
        db_strict_dirty=_env_bool(("TPX_DB_STRICT_DIRTY",), False),
        db_metrics_enabled=_env_bool(("TPX_DB_METRICS_ENABLED",), True),
        db_isolation_mode=_env_str(("TPX_DB_ISOLATION_MODE",), "auto"),
        db_worker_prefix=_env_str(("TPX_DB_WORKER_PREFIX",), "tpx_w"),
        db_dirty_track_max_tables=_env_int(("TPX_DB_DIRTY_TRACK_MAX_TABLES",), 12),
    )