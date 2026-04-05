"""Core test execution: run_test, run_single_test, run_test_batch, and CLI entry points."""

from __future__ import annotations

import gc
import inspect
import json
import os
import pathlib
import sys
import time
import traceback
from typing import Any, Optional

from .emit import _emit_enhanced
from .environment import _bootstrap_test_environment, _load_module
from ..db.fixtures import begin_test_db_tracking, finalize_test_db_tracking
from ..mcp.transactional import begin_test_transaction, end_test_transaction
from .invocation import (
    _invoke_function,
    _invoke_method,
    _Skipped,
    _get_pytest_skip_exception,
    clear_last_fixture_source,
    get_last_fixture_source,
)
from .parametrize import _get_parametrize_info

# Track last module for GC optimization
_last_module_path: Optional[str] = None


def run_test(path_str: str, qual: str) -> dict[str, Any]:
    global _last_module_path
    
    # Bootstrap: configurar DB env y pre-importar modelos antes de cargar tests
    _bootstrap_test_environment()
    
    path = pathlib.Path(path_str)
    if not path.is_file():
        return _emit_enhanced(False, 0, path_str, qual, error=Exception(f"not a file: {path_str}"))
    
    # GC agresivo: ejecutar gc.collect() al cambiar de módulo
    current_module = str(path.resolve())
    if _last_module_path is not None and _last_module_path != current_module:
        gc.collect()  # Liberar memoria del módulo anterior
    _last_module_path = current_module
    
    # Check for parametrize index in qualname (e.g., "test_func[0]")
    parametrize_index = None
    original_qual = qual
    if "[" in qual and qual.endswith("]"):
        base_qual, idx_str = qual.rsplit("[", 1)
        try:
            parametrize_index = int(idx_str.rstrip("]"))
            qual = base_qual
        except ValueError:
            pass
    
    # RSS monitoring: medir memoria al inicio
    rss_start: Optional[int] = None
    rss_end: Optional[int] = None
    try:
        import psutil
        process = psutil.Process(os.getpid())
        rss_start = process.memory_info().rss
    except Exception:
        pass  # psutil no disponible, continuar sin monitoreo
    
    t0 = time.perf_counter()
    begin_test_db_tracking()
    begin_test_transaction()
    should_flush_logs = False
    try:
        try:
            mod = _load_module(path.resolve())
        except Exception as e:
            dt = int((time.perf_counter() - t0) * 1000)
            traceback.print_exc(file=sys.stderr)
            res = _emit_enhanced(False, dt, path_str, qual, error=e)
            res.update(finalize_test_db_tracking())
            should_flush_logs = True
            return res

        parametrize_info = None
        try:
            if "::" not in qual:
                fn = getattr(mod, qual)
                if parametrize_index is not None and inspect.isfunction(fn):
                    parametrize_info = _get_parametrize_info(fn, parametrize_index, str(path), qual)
        except Exception:
            pass

        try:
            clear_last_fixture_source()
            if "::" in qual:
                cname, mname = qual.split("::", 1)
                cls = getattr(mod, cname)
                meth = getattr(cls, mname)
                if not inspect.isfunction(meth):
                    raise RuntimeError(f"{qual!r} is not a plain instance method in v1")
                _invoke_method(mod, cls, meth)
            else:
                fn = getattr(mod, qual)
                if not inspect.isfunction(fn):
                    raise RuntimeError(f"{qual!r} is not a function")
                _invoke_function(mod, fn, parametrize_index=parametrize_index)
            fixture_source = get_last_fixture_source()
        except _Skipped as sk:
            dt = int((time.perf_counter() - t0) * 1000)
            payload: dict[str, Any] = {"passed": True, "duration_ms": dt, "error": None, "skipped": True}
            fixture_source = get_last_fixture_source()
            if fixture_source:
                payload["fixture_source"] = fixture_source
            if sk.reason:
                payload["skip_reason"] = sk.reason
            payload.update(finalize_test_db_tracking())
            return payload
        except BaseException as e:
            skip_exc = _get_pytest_skip_exception()
            if skip_exc is not None and isinstance(e, skip_exc):
                dt = int((time.perf_counter() - t0) * 1000)
                payload = {"passed": True, "duration_ms": dt, "error": None, "skipped": True}
                fixture_source = get_last_fixture_source()
                if fixture_source:
                    payload["fixture_source"] = fixture_source
                reason = getattr(e, "msg", None)
                if not reason:
                    reason = getattr(e, "reason", None)
                if not reason and getattr(e, "args", None):
                    try:
                        reason = e.args[0]
                    except Exception:
                        reason = None
                if reason:
                    payload["skip_reason"] = str(reason)
                payload.update(finalize_test_db_tracking())
                return payload
            if e.__class__.__name__ == "Skipped" and e.__class__.__module__ == "_pytest.outcomes":
                dt = int((time.perf_counter() - t0) * 1000)
                payload = {"passed": True, "duration_ms": dt, "error": None, "skipped": True}
                fixture_source = get_last_fixture_source()
                if fixture_source:
                    payload["fixture_source"] = fixture_source
                reason = getattr(e, "msg", None)
                if reason:
                    payload["skip_reason"] = str(reason)
                payload.update(finalize_test_db_tracking())
                return payload
            if not isinstance(e, Exception):
                raise
            dt = int((time.perf_counter() - t0) * 1000)
            traceback.print_exc(file=sys.stderr)
            res = _emit_enhanced(
                False,
                dt,
                path_str,
                original_qual,
                error=e,
                parametrize_info=parametrize_info,
                fixture_source=get_last_fixture_source(),
            )
            res.update(finalize_test_db_tracking())
            should_flush_logs = True
            return res

        dt = int((time.perf_counter() - t0) * 1000)
        result = _emit_enhanced(
            True,
            dt,
            path_str,
            original_qual,
            parametrize_info=parametrize_info,
            fixture_source=fixture_source,
        )
        db_info = finalize_test_db_tracking()
        result.update(db_info)
        if db_info.get("db_should_fail_on_dirty"):
            result["passed"] = False
            result["error"] = "Dirty DB state detected (TPX_DB_STRICT_DIRTY=1)"
            result["db_error"] = {
                "code": "db_dirty_state",
                "vendor_code": None,
                "message": "Dirty DB state detected (TPX_DB_STRICT_DIRTY=1)",
                "details": db_info.get("db_dirty_summary"),
            }

        try:
            import psutil
            process = psutil.Process(os.getpid())
            rss_end = process.memory_info().rss
        except Exception:
            pass

        if rss_start is not None and rss_end is not None:
            result["rss_start_bytes"] = rss_start
            result["rss_end_bytes"] = rss_end
            result["rss_delta_bytes"] = rss_end - rss_start

        return result
    finally:
        end_test_transaction()
        if should_flush_logs:
            try:
                from ..mcp.utils import get_tplex_logger

                get_tplex_logger().flush()
            except Exception:
                pass


def run_test_batch(test_items: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Ejecuta un batch de tests en el mismo proceso.
    
    Args:
        test_items: Lista de dicts con 'path' y 'qual' para cada test
        
    Returns:
        Lista de resultados de cada test
    """
    results = []
    
    # Bootstrap único para todo el batch
    _bootstrap_test_environment()
    
    for item in test_items:
        path_str = item.get("path", "")
        qual = item.get("qual", "")
        
        result = run_single_test(path_str, qual)
        results.append(result)
    
    return results


def run_single_test(path_str: str, qual: str) -> dict[str, Any]:
    """Ejecuta un solo test (versión interna sin bootstrap global).
    
    Similar a run_test pero sin llamar _bootstrap_test_environment()
    ya que eso se hace una vez por batch.
    """
    global _last_module_path
    
    path = pathlib.Path(path_str)
    if not path.is_file():
        return _emit_enhanced(False, 0, path_str, qual, error=Exception(f"not a file: {path_str}"))
    
    # GC al cambiar de módulo
    current_module = str(path.resolve())
    if _last_module_path is not None and _last_module_path != current_module:
        gc.collect()
    _last_module_path = current_module
    
    # Check for parametrize index
    parametrize_index = None
    original_qual = qual
    if "[" in qual and qual.endswith("]"):
        base_qual, idx_str = qual.rsplit("[", 1)
        try:
            parametrize_index = int(idx_str.rstrip("]"))
            qual = base_qual
        except ValueError:
            pass
    
    t0 = time.perf_counter()
    begin_test_db_tracking()
    begin_test_transaction()
    should_flush_logs = False
    try:
        try:
            mod = _load_module(path.resolve())
        except Exception as e:
            dt = int((time.perf_counter() - t0) * 1000)
            traceback.print_exc(file=sys.stderr)
            res = _emit_enhanced(False, dt, path_str, qual, error=e)
            res.update(finalize_test_db_tracking())
            should_flush_logs = True
            return res

        parametrize_info = None
        try:
            if "::" not in qual:
                fn = getattr(mod, qual)
                if parametrize_index is not None and inspect.isfunction(fn):
                    parametrize_info = _get_parametrize_info(fn, parametrize_index, str(path), qual)
        except Exception:
            pass

        try:
            clear_last_fixture_source()
            if "::" in qual:
                cname, mname = qual.split("::", 1)
                cls = getattr(mod, cname)
                meth = getattr(cls, mname)
                if not inspect.isfunction(meth):
                    raise RuntimeError(f"{qual!r} is not a plain instance method in v1")
                _invoke_method(mod, cls, meth)
            else:
                fn = getattr(mod, qual)
                if not inspect.isfunction(fn):
                    raise RuntimeError(f"{qual!r} is not a function")
                _invoke_function(mod, fn, parametrize_index=parametrize_index)
            fixture_source = get_last_fixture_source()
        except _Skipped as sk:
            dt = int((time.perf_counter() - t0) * 1000)
            payload: dict[str, Any] = {"passed": True, "duration_ms": dt, "error": None, "skipped": True}
            fixture_source = get_last_fixture_source()
            if fixture_source:
                payload["fixture_source"] = fixture_source
            if sk.reason:
                payload["skip_reason"] = sk.reason
            payload.update(finalize_test_db_tracking())
            return payload
        except BaseException as e:
            skip_exc = _get_pytest_skip_exception()
            if skip_exc is not None and isinstance(e, skip_exc):
                dt = int((time.perf_counter() - t0) * 1000)
                payload = {"passed": True, "duration_ms": dt, "error": None, "skipped": True}
                fixture_source = get_last_fixture_source()
                if fixture_source:
                    payload["fixture_source"] = fixture_source
                reason = getattr(e, "msg", None)
                if not reason:
                    reason = getattr(e, "reason", None)
                if not reason and getattr(e, "args", None):
                    try:
                        reason = e.args[0]
                    except Exception:
                        pass
                if reason:
                    payload["skip_reason"] = str(reason)
                payload.update(finalize_test_db_tracking())
                return payload
            if e.__class__.__name__ == "Skipped" and e.__class__.__module__ == "_pytest.outcomes":
                dt = int((time.perf_counter() - t0) * 1000)
                payload = {"passed": True, "duration_ms": dt, "error": None, "skipped": True}
                fixture_source = get_last_fixture_source()
                if fixture_source:
                    payload["fixture_source"] = fixture_source
                reason = getattr(e, "msg", None)
                if reason:
                    payload["skip_reason"] = str(reason)
                payload.update(finalize_test_db_tracking())
                return payload
            if not isinstance(e, Exception):
                raise
            dt = int((time.perf_counter() - t0) * 1000)
            traceback.print_exc(file=sys.stderr)
            res = _emit_enhanced(
                False,
                dt,
                path_str,
                original_qual,
                error=e,
                parametrize_info=parametrize_info,
                fixture_source=get_last_fixture_source(),
            )
            res.update(finalize_test_db_tracking())
            should_flush_logs = True
            return res

        dt = int((time.perf_counter() - t0) * 1000)
        result = _emit_enhanced(
            True,
            dt,
            path_str,
            original_qual,
            parametrize_info=parametrize_info,
            fixture_source=fixture_source,
        )
        db_info = finalize_test_db_tracking()
        result.update(db_info)
        if db_info.get("db_should_fail_on_dirty"):
            result["passed"] = False
            result["error"] = "Dirty DB state detected (TPX_DB_STRICT_DIRTY=1)"
            result["db_error"] = {
                "code": "db_dirty_state",
                "vendor_code": None,
                "message": "Dirty DB state detected (TPX_DB_STRICT_DIRTY=1)",
                "details": db_info.get("db_dirty_summary"),
            }
        return result
    finally:
        end_test_transaction()
        if should_flush_logs:
            try:
                from ..mcp.utils import get_tplex_logger

                get_tplex_logger().flush()
            except Exception:
                pass


def run_main(path_str: str, qual: str, out_json: str | None = None) -> None:
    payload = run_test(path_str, qual)
    passed = bool(payload.get("passed"))
    if out_json:
        pathlib.Path(out_json).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        sys.exit(0 if passed else 1)
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    sys.exit(0 if passed else 1)


def run_batch_main(batch_json: str, out_json: str | None = None) -> None:
    """CLI entry point para ejecución en batch.
    
    Args:
        batch_json: JSON string con lista de tests [{'path': '...', 'qual': '...'}, ...]
        out_json: Path opcional para escribir resultados
    """
    try:
        if os.path.isfile(batch_json):
            batch_json = pathlib.Path(batch_json).read_text(encoding="utf-8", errors="replace")
        test_items = json.loads(batch_json)
        if not isinstance(test_items, list):
            raise ValueError("batch_json must be a list")
    except Exception as e:
        error_result = {"error": f"Invalid batch JSON: {e}", "passed": False}
        if out_json:
            pathlib.Path(out_json).write_text(json.dumps(error_result), encoding="utf-8")
        json.dump(error_result, sys.stdout)
        sys.exit(1)
    
    results = run_test_batch(test_items)
    
    all_passed = all(r.get("passed", False) for r in results)
    
    output = {"results": results, "total": len(results), "passed": sum(1 for r in results if r.get("passed"))}
    
    if out_json:
        pathlib.Path(out_json).write_text(
            json.dumps(output, ensure_ascii=False), encoding="utf-8"
        )
    else:
        json.dump(output, sys.stdout)
        sys.stdout.write("\n")
    
    sys.exit(0 if all_passed else 1)
