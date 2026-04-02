"""Lightweight MCP tool payload schema/builders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolError:
    code: str
    message: str
    vendor_code: str | int | None = None
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.vendor_code is not None:
            out["vendor_code"] = self.vendor_code
        if self.details:
            out["details"] = self.details
        return out


@dataclass
class ToolPayload:
    tool: str
    ok: bool
    run_id: str
    mode: str
    summary: dict[str, Any]
    data: dict[str, Any]
    logs: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] | None = None
    schema_version: str = "tpx.mcp.tool.v1"

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "tool": self.tool,
            "ok": self.ok,
            "runId": self.run_id,
            "mode": self.mode,
            "summary": self.summary,
            "logs": self.logs,
            "data": self.data,
        }
        if self.artifacts is not None:
            out["artifacts"] = self.artifacts
        return out


def payload_ok(
    *,
    tool: str,
    run_id: str,
    mode: str,
    summary: dict[str, Any],
    data: dict[str, Any],
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return ToolPayload(
        tool=tool,
        ok=True,
        run_id=run_id,
        mode=mode,
        summary=summary,
        data=data,
        artifacts=artifacts,
    ).as_dict()


def payload_error(
    *,
    tool: str,
    run_id: str,
    mode: str,
    duration_ms: int,
    error: ToolError,
) -> dict[str, Any]:
    return ToolPayload(
        tool=tool,
        ok=False,
        run_id=run_id,
        mode=mode,
        summary={"duration_ms": duration_ms},
        data={"error": error.as_dict()},
    ).as_dict()