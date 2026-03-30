"""Run a single test; print one JSON line to stdout; tracebacks on stderr."""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import os
import pathlib
import re
import sys
import time
import traceback
from typing import Any, Callable

# Configure logging to stderr to keep stdout clean for JSON
for h in logging.root.handlers[:]:
    logging.root.removeHandler(h)
h = logging.StreamHandler(sys.stderr)
h.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
logging.root.addHandler(h)
logging.root.setLevel(logging.CRITICAL)

# Disable SQLAlchemy and other noisy libraries
logging.getLogger("sqlalchemy.engine").setLevel(logging.CRITICAL)
logging.getLogger("sqlalchemy").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

from .fixtures import build_kwargs_for_callable
from .markers import skip_check
from .pytest_integration import get_compat_mode, has_pytest_fixtures


def _invoke_function_with_pytest_bridge(mod, fn, parametrize_index=None):
    """Ejecuta función con soporte para fixtures de pytest."""
    import inspect
    
    # Verificar si la función usa fixtures de pytest
    sig = inspect.signature(fn)
    params = [p for p in sig.parameters.keys() if p not in ('self', 'cls')]
    
    if not params:
        # No tiene parámetros que puedan ser fixtures
        return _invoke_function_original(mod, fn, parametrize_index)
    
    # Intentar obtener modo compatibilidad
    test_file = getattr(mod, '__file__', None)
    if test_file:
        compat_mode = get_compat_mode(test_file)
        if compat_mode:
            # Usar bridge de pytest
            try:
                compat_mode.session_start()
                compat_mode.setup_test(fn.__name__)
                
                # Preparar función con fixtures inyectados
                wrapped_fn = compat_mode.prepare_test(fn)
                
                # Ejecutar
                if parametrize_index is not None:
                    # TODO: Manejar parametrize con bridge
                    wrapped_fn()
                else:
                    wrapped_fn()
                
                compat_mode.teardown_test(fn.__name__, "passed")
                return
                
            except Exception as e:
                compat_mode.teardown_test(fn.__name__, "failed")
                raise
    
    # Fallback: usar comportamiento original
    return _invoke_function_original(mod, fn, parametrize_index)


def _invoke_function_original(mod, fn, parametrize_index=None):
    """Comportamiento original sin bridge."""
    do_skip, reason = skip_check(fn)
    if do_skip:
        raise _Skipped(reason or "")

    # Get parametrize kwargs BEFORE calling build_kwargs_for_callable
    parametrize_kwargs = None
    if parametrize_index is not None:
        parametrize_kwargs = _get_parametrize_kwargs(fn, parametrize_index)
    
    kwargs = build_kwargs_for_callable(mod, fn, skip_self=False, parametrize_kwargs=parametrize_kwargs)
    
    fn(**kwargs)


def _invoke_method(mod, cls: type, meth: Callable[..., Any]) -> None:
    do_skip, reason = skip_check(meth)
    if do_skip:
        raise _Skipped(reason or "")

    inst = cls()
    kwargs = build_kwargs_for_callable(mod, meth, skip_self=True)
    meth(inst, **kwargs)


def _get_context_window(file_path: str, error_line: int, window_size: int = 3) -> list[str]:
    """
    Captura ventana de 7 líneas (3 + hot + 3) preservando indentación.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception:
        return []
    
    # Convertir a 0-indexed
    hot_idx = error_line - 1
    start_idx = max(0, hot_idx - window_size)
    end_idx = min(len(lines), hot_idx + window_size + 1)
    
    snippet = []
    for i in range(start_idx, end_idx):
        line_num = i + 1
        line_content = lines[i].rstrip('\n')  # Preservar indentación, quitar solo newline
        
        # Marcar línea del error con >
        if i == hot_idx:
            snippet.append(f"{line_num}:> {line_content}")
        else:
            snippet.append(f"{line_num}: {line_content}")
    
    return snippet


def _serialize_local_slim(name: str, value: Any) -> str:
    """
    Serializa variable local de forma compacta pero informativa.
    """
    type_name = type(value).__name__
    
    # Primitivos: mostrar directamente
    if isinstance(value, (int, float, bool, type(None))):
        return str(value)
    
    # Strings: mostrar con comillas, truncar si es muy largo
    if isinstance(value, str):
        if len(value) > 100:
            return f"'{value[:50]}...{value[-30:]}' ({len(value)} chars)"
        return f"'{value}'"
    
    # Respuestas HTTP: resumen del status
    if hasattr(value, 'status_code'):
        content_preview = ""
        if hasattr(value, 'text'):
            text = value.text
            content_preview = f", body='{text[:50]}...'" if len(text) > 50 else f", body='{text}'"
        return f"<{type_name} [{value.status_code}]{content_preview}>"
    
    # Objetos SQLAlchemy: __repr__ simplificado
    if hasattr(value, '__table__') or type_name in ('User', 'Empresa', 'Cliente', 'Usuario'):
        # Intentar extraer campos clave
        key_fields = ['id', 'email', 'name', 'is_active', 'status', 'nombre', 'rut', 'codigo']
        field_values = {}
        for field in key_fields:
            if hasattr(value, field):
                try:
                    field_val = getattr(value, field)
                    field_values[field] = field_val
                except:
                    pass
        
        if field_values:
            fields_str = ", ".join(f"{k}={v}" for k, v in field_values.items())
            return f"{type_name}({fields_str})"
        return f"<{type_name} obj>"
    
    # Listas/Tuplas: resumen
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return "[]"
        if len(value) <= 3:
            items = [_serialize_local_slim(f"item_{i}", item) for i, item in enumerate(value)]
            return f"[{', '.join(items)}]"
        # Lista grande: mostrar primeros 2 y contador
        first_items = [_serialize_local_slim("", v) for v in value[:2]]
        return f"[{', '.join(first_items)}, ... +{len(value)-2} more items]"
    
    # Diccionarios: resumen de keys
    if isinstance(value, dict):
        if len(value) == 0:
            return "{}"
        if len(value) <= 3:
            items = [f"{repr(k)}: {_serialize_local_slim('', v)}" 
                    for k, v in list(value.items())[:3]]
            return "{" + ", ".join(items) + "}"
        return f"{{{', '.join(repr(k) for k in list(value.keys())[:3])}, ... +{len(value)-3} more keys}}"
    
    # Fallback: __repr__ o nombre de clase
    try:
        repr_val = repr(value)
        if len(repr_val) > 100:
            return f"<{type_name}: {repr_val[:50]}...>"
        return repr_val
    except:
        return f"<{type_name} obj>"


def _parse_assertion_error(error: AssertionError) -> dict | None:
    """
    Intenta extraer expected/actual de AssertionError para formato diff.
    """
    error_str = str(error)
    
    # Caso 1: Comparación directa en el mensaje
    # "Expected 200 but got 403" -> expected=["200"], actual=["403"]
    match = re.search(r'[Ee]xpected\s+(\S+)\s+(?:but\s+)?[Gg]ot\s+(\S+)', error_str)
    if match:
        return {
            "expected": [match.group(1)],
            "actual": [match.group(2)],
            "operator": "=="
        }
    
    # Caso 2: assert left == right (de traceback)
    # Buscar en las líneas de código del traceback
    if hasattr(error, '__traceback__'):
        for frame in traceback.extract_tb(error.__traceback__):
            code = frame.line
            if code and 'assert' in code:
                # Buscar patrones como: assert x == y, assert a != b
                match = re.search(r'assert\s+(\S+)\s*([=!]=)\s*(\S+)', code)
                if match:
                    left = match.group(1)
                    op = match.group(2)
                    right = match.group(3)
                    return {
                        "expected": [right] if op == "==" else [f"not {right}"],
                        "actual": [left],
                        "operator": op
                    }
    
    # Caso 3: assertEqual de unittest style
    # "400 != 200" -> expected=["200"], actual=["400"]
    match = re.search(r'(\S+)\s*!=\s*(\S+)', error_str)
    if match:
        return {
            "expected": [match.group(2)],
            "actual": [match.group(1)],
            "operator": "=="
        }
    
    return None


def _get_test_lineno(path_str: str, qual: str) -> int:
    """
    Obtiene el número de línea de la función de test.
    """
    try:
        path = pathlib.Path(path_str)
        with open(path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        # Parsear el archivo para encontrar la función
        import ast
        tree = ast.parse(source)
        
        # Manejar métodos de clase (qual = "ClassName::method_name")
        target_name = qual
        if "::" in qual:
            target_name = qual.split("::")[-1]
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == target_name:
                return node.lineno
    except Exception:
        pass
    return 0


def _get_fixtures_used() -> list[str]:
    """
    Obtiene la lista de fixtures usados (si está disponible).
    """
    # Esta función será extendida por pytest_bridge para trackear fixtures
    return []


class _Skipped(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _emit_enhanced(
    passed: bool,
    duration_ms: int,
    test_path: str,
    test_qual: str,
    error: Exception | None = None,
    fixtures_used: list[str] | None = None
) -> dict[str, Any]:
    """Emite JSON enriquecido con ventana de contexto."""
    
    payload: dict[str, Any] = {
        "passed": passed,
        "duration_ms": duration_ms,
        "test_info": {
            "path": test_path,
            "qualname": test_qual,
            "lineno": _get_test_lineno(test_path, test_qual)
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


def _load_module(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"turbopy_run_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get_parametrize_kwargs(fn: Callable[..., Any], parametrize_index: int) -> dict[str, Any]:
    """Extract parameters from @pytest.mark.parametrize for a given index."""
    parametrize_marker = None
    if hasattr(fn, "pytestmark"):
        for mark in fn.pytestmark:
            if hasattr(mark, "name") and mark.name == "parametrize":
                parametrize_marker = mark
                break
            # Handle list of markers
            if isinstance(mark, list):
                for m in mark:
                    if hasattr(m, "name") and m.name == "parametrize":
                        parametrize_marker = m
                        break
    
    if not parametrize_marker:
        return {}
    
    try:
        if hasattr(parametrize_marker, "args"):
            args = parametrize_marker.args
            if len(args) >= 2:
                arg_names = args[0]
                test_values = args[1]
                
                if isinstance(arg_names, str):
                    arg_names = [a.strip() for a in arg_names.split(",")]
                
                # Get the specific set of values for this test
                values = test_values[parametrize_index]
                if not isinstance(values, (list, tuple)):
                    values = (values,)
                
                # Build kwargs from parameters
                kwargs = {}
                for i, arg_name in enumerate(arg_names):
                    if i < len(values):
                        kwargs[arg_name] = values[i]
                return kwargs
    except Exception as e:
        raise RuntimeError(f"Could not resolve parametrize for index {parametrize_index}: {e}")
    
    return {}


def _invoke_function(mod, fn: Callable[..., Any], parametrize_index: int = None) -> None:
    """Ejecuta función, usando bridge de pytest si es necesario."""
    do_skip, reason = skip_check(fn)
    if do_skip:
        raise _Skipped(reason or "")
    
    # Verificar si usa fixtures de pytest
    import inspect
    sig = inspect.signature(fn)
    params = [p for p in sig.parameters.keys() if p not in ('self', 'cls')]
    
    if params:
        # Intentar usar bridge
        test_file = getattr(mod, '__file__', None)
        if test_file:
            from .pytest_integration import get_compat_mode
            compat_mode = get_compat_mode(test_file)
            if compat_mode:
                try:
                    compat_mode.session_start()
                    compat_mode.setup_test(fn.__name__)
                    wrapped_fn = compat_mode.prepare_test(fn)
                    wrapped_fn()
                    compat_mode.teardown_test(fn.__name__, "passed")
                    return
                except Exception:
                    compat_mode.teardown_test(fn.__name__, "failed")
                    raise
    
    # Fallback: comportamiento original
    parametrize_kwargs = None
    if parametrize_index is not None:
        parametrize_kwargs = _get_parametrize_kwargs(fn, parametrize_index)
    
    kwargs = build_kwargs_for_callable(mod, fn, skip_self=False, parametrize_kwargs=parametrize_kwargs)
    fn(**kwargs)


def run_test(path_str: str, qual: str) -> dict[str, Any]:
    path = pathlib.Path(path_str)
    if not path.is_file():
        return _emit_enhanced(False, 0, path_str, qual, error=Exception(f"not a file: {path_str}"))

    # Check for parametrize index in qualname (e.g., "test_func[0]")
    parametrize_index = None
    if "[" in qual and qual.endswith("]"):
        base_qual, idx_str = qual.rsplit("[", 1)
        try:
            parametrize_index = int(idx_str.rstrip("]"))
            qual = base_qual
        except ValueError:
            pass

    t0 = time.perf_counter()
    try:
        mod = _load_module(path.resolve())
    except Exception as e:
        dt = int((time.perf_counter() - t0) * 1000)
        traceback.print_exc(file=sys.stderr)
        return _emit_enhanced(False, dt, path_str, qual, error=e)

    try:
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
    except _Skipped as sk:
        dt = int((time.perf_counter() - t0) * 1000)
        # Para tests skipped, usar formato simple
        payload: dict[str, Any] = {"passed": True, "duration_ms": dt, "error": None, "skipped": True}
        if sk.reason:
            payload["skip_reason"] = sk.reason
        return payload
    except Exception as e:
        dt = int((time.perf_counter() - t0) * 1000)
        traceback.print_exc(file=sys.stderr)
        return _emit_enhanced(False, dt, path_str, qual, error=e)

    dt = int((time.perf_counter() - t0) * 1000)
    return _emit_enhanced(True, dt, path_str, qual)


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
