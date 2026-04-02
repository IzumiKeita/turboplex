"""JSON emission for test results — both enhanced and legacy formats."""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from .diagnostics import (
    _get_context_window,
    _get_test_lineno,
    _parse_assertion_error,
    _serialize_local_slim,
)


def _emit_enhanced(
    passed: bool,
    duration_ms: int,
    test_path: str,
    test_qual: str,
    error: Exception | None = None,
    fixtures_used: list[str] | None = None,
    parametrize_info: dict | None = None,
    fixture_source: str | None = None,
) -> dict[str, Any]:
    """Emite JSON enriquecido con ventana de contexto."""
    
    payload: dict[str, Any] = {
        "passed": passed,
        "duration_ms": duration_ms,
        "test_info": {
            "path": test_path,
            "qualname": test_qual,
            "lineno": _get_test_lineno(test_path, test_qual),
            "parametrize": parametrize_info
        }
    }
    
    if error:
        error_type = type(error).__name__
        error_msg = str(error)
        
        # Parsear diff si es AssertionError
        diff = None
        if isinstance(error, AssertionError):
            diff = _parse_assertion_error(error)
        
        # Construir traceback con ventana de contexto
        tb_frames = []
        if hasattr(error, '__traceback__') and error.__traceback__:
            for frame in traceback.extract_tb(error.__traceback__):
                tb_frames.append({
                    "file": frame.filename,
                    "line": frame.lineno,
                    "function": frame.name,
                    "snippet": _get_context_window(frame.filename, frame.lineno, window_size=3)
                })
        
        # Capturar locals slim del último frame
        locals_slim = {}
        if hasattr(error, '__traceback__') and error.__traceback__:
            last_frame = error.__traceback__.tb_frame
            for name, value in last_frame.f_locals.items():
                if not name.startswith('__'):
                    locals_slim[name] = _serialize_local_slim(name, value)
        
        payload["error_context"] = {
            "type": error_type,
            "message": error_msg,
            "diff": diff,
            "traceback": tb_frames,
            "locals_slim": locals_slim
        }
        
        # Legacy field for backward compatibility
        payload["error"] = error_msg
    else:
        payload["error"] = None
    
    if fixtures_used:
        payload["fixtures_used"] = fixtures_used
    if fixture_source:
        payload["fixture_source"] = fixture_source
    
    return payload


def _emit(
    passed: bool,
    duration_ms: int,
    error: str | None = None,
    *,
    skipped: bool = False,
    skip_reason: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "passed": passed,
        "duration_ms": duration_ms,
        "error": error,
    }
    if skipped:
        payload["skipped"] = True
    if skip_reason is not None:
        payload["skip_reason"] = skip_reason
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
