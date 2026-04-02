"""Parametrize handling: extract info, kwargs, and cache fallback."""

from __future__ import annotations

import json
import pathlib
from typing import Any, Callable


def _get_parametrize_info(fn: Callable[..., Any], parametrize_index: int, test_path: str = None, qualname: str = None) -> dict | None:
    """Extrae información completa de parametrize incluyendo call_spec."""
    if not hasattr(fn, 'pytestmark'):
        # Fallback: intentar leer desde cache si no hay markers
        if test_path and qualname:
            return _get_parametrize_from_cache(test_path, qualname, parametrize_index)
        return None
    
    markers = fn.pytestmark
    if not isinstance(markers, (list, tuple)):
        markers = [markers]
    
    for marker in markers:
        if hasattr(marker, 'name') and marker.name == 'parametrize':
            args = getattr(marker, 'args', [])
            if len(args) >= 2:
                arg_names = args[0]
                test_values = args[1]
                
                if isinstance(arg_names, str):
                    arg_names = [a.strip() for a in arg_names.split(',')]
                
                # Obtener valores para este índice
                if parametrize_index < len(test_values):
                    values = test_values[parametrize_index]
                    if not isinstance(values, (list, tuple)):
                        values = (values,)
                    
                    # Construir call_spec (mapeo nombre -> valor)
                    call_spec = {}
                    for i, arg_name in enumerate(arg_names):
                        if i < len(values):
                            # Serializar valor para JSON
                            val = values[i]
                            if isinstance(val, (int, float, bool, str, type(None))):
                                call_spec[arg_name] = val
                            else:
                                call_spec[arg_name] = repr(val)
                    
                    kwargs = {'arg_names': arg_names}
                    if hasattr(marker, 'kwargs') and marker.kwargs:
                        ids = marker.kwargs.get('ids', [])
                        if ids and parametrize_index < len(ids):
                            kwargs['id'] = ids[parametrize_index]
                    
                    return {
                        "index": parametrize_index,
                        "call_spec": call_spec,
                        **kwargs
                    }
    return None


def _get_parametrize_from_cache(test_path: str, qualname: str, parametrize_index: int) -> dict | None:
    """Fallback: recupera información de parametrize desde el cache de TurboPlex.
    
    Lee .turboplex_cache/collected_tests.json cuando los markers no están disponibles.
    """
    cache_path = pathlib.Path('.turboplex_cache/collected_tests.json')
    if not cache_path.exists():
        return None
    
    try:
        data = json.loads(cache_path.read_text(encoding='utf-8'))
        tests = data.get('tests', [])
        
        # Buscar test que coincida con path y qualname
        for test in tests:
            if test.get('path') == test_path and test.get('qualname') == qualname:
                parametrize_data = test.get('parametrize')
                if parametrize_data and parametrize_data.get('index') == parametrize_index:
                    return {
                        'index': parametrize_index,
                        'call_spec': parametrize_data.get('call_spec', {}),
                        'arg_names': parametrize_data.get('arg_names', []),
                    }
    except Exception:
        pass  # Cache no disponible o corrupto
    
    return None


# Alias para compatibilidad
def _get_parametrize_kwargs(fn: Callable[..., Any], parametrize_index: int) -> dict[str, Any]:
    """Extract parameters from @pytest.mark.parametrize for a given index."""
    info = _get_parametrize_info(fn, parametrize_index)
    if info:
        return info.get('call_spec', {})
    return {}
