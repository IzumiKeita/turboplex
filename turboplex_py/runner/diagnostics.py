"""Error context, variable serialization, and assertion parsing for enriched reports."""

from __future__ import annotations

import pathlib
import re
import traceback
from typing import Any


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
