"""Fixture Adapter - Convierte fixtures de pytest a TurboPlex.

Este módulo adapta el sistema de fixtures de pytest al sistema de TurboPlex,
permitiendo usar @pytest.fixture como si fuera @turboplex_py.fixture.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Generator, List, Optional, TypeVar
from functools import wraps

from .fixtures import fixture as turboplex_fixture
from .pytest_bridge import PytestBridge, PytestFixtureInfo

F = TypeVar("F", bound=Callable[..., Any])


class FixtureAdapter:
    """Adapta fixtures de pytest al formato de TurboPlex."""
    
    def __init__(self, bridge: PytestBridge):
        self.bridge = bridge
        self._adapted_fixtures: Dict[str, Callable] = {}
        self._active_generators: Dict[str, Generator] = {}
    
    def adapt_fixture(self, fixture_info: PytestFixtureInfo) -> Callable:
        """Convierte un PytestFixtureInfo a un decorator @fixture de TurboPlex."""
        
        if fixture_info.name in self._adapted_fixtures:
            return self._adapted_fixtures[fixture_info.name]
        
        def decorator(fn: F) -> F:
            @turboplex_fixture
            @wraps(fn)
            def wrapper(*args, **kwargs):
                # Resolver dependencias primero
                deps = {}
                for dep_name in fixture_info.dependencies:
                    deps[dep_name] = self._resolve_dependency(dep_name)
                
                # Actualizar kwargs con dependencias resueltas
                deps.update(kwargs)
                
                # Llamar al fixture original
                if fixture_info.has_yield:
                    # Es un generador - manejar setup/teardown
                    gen = fn(**deps)
                    self._active_generators[fixture_info.name] = gen
                    try:
                        value = next(gen)
                        return value
                    except StopIteration:
                        return None
                else:
                    # Función normal
                    return fn(**deps)
            
            return wrapper
        
        # Guardar referencia
        self._adapted_fixtures[fixture_info.name] = decorator
        return decorator
    
    def _resolve_dependency(self, name: str) -> Any:
        """Resuelve una dependencia de fixture."""
        # Primero buscar en valores ya cacheados
        if name in self.bridge._fixture_values:
            return self.bridge._fixture_values[name]
        
        # Si no, obtener del bridge
        return self.bridge.get_fixture_value(name)
    
    def cleanup_fixture(self, name: str):
        """Limpia un fixture adaptado (ejecuta teardown)."""
        if name in self._active_generators:
            gen = self._active_generators.pop(name)
            try:
                next(gen)  # Continuar generador (ejecuta finally/yield from)
            except StopIteration:
                pass


class FixtureInjector:
    """Inyecta fixtures de pytest en funciones de test."""
    
    def __init__(self, bridge: PytestBridge):
        self.bridge = bridge
        self.adapter = FixtureAdapter(bridge)
    
    def inject_fixtures(self, test_func: Callable, test_path: str) -> Callable:
        """Crea una versión de la función con fixtures inyectados."""
        
        # Analizar parámetros de la función
        sig = inspect.signature(test_func)
        params = list(sig.parameters.keys())
        
        # Identificar cuáles son fixtures
        fixture_params = []
        for param in params:
            if self._is_fixture(param):
                fixture_params.append(param)
        
        if not fixture_params:
            # No hay fixtures que inyectar
            return test_func
        
        # Crear wrapper que inyecta fixtures
        @wraps(test_func)
        def wrapper():
            # Resolver todos los fixtures necesarios
            kwargs = {}
            
            # Resolver en orden de dependencias
            for fixture_name in fixture_params:
                kwargs[fixture_name] = self._resolve_fixture(fixture_name)
            
            # Llamar función original con fixtures inyectados
            try:
                result = test_func(**kwargs)
                return result
            finally:
                # Cleanup de fixtures (en orden inverso)
                for fixture_name in reversed(fixture_params):
                    self._cleanup_fixture(fixture_name)
        
        return wrapper
    
    def _is_fixture(self, name: str) -> bool:
        """Verifica si un nombre corresponde a un fixture registrado."""
        # Buscar en el fixture manager del bridge
        fixture_info = self.bridge.fixture_manager.get(name)
        if fixture_info:
            return True
        
        # También buscar en conftest cargado
        if self.bridge._conftest_module and hasattr(self.bridge._conftest_module, name):
            obj = getattr(self.bridge._conftest_module, name)
            # Verificar si es un fixture (tiene marca de pytest)
            if hasattr(obj, '_pytestfixturefunction'):
                return True
            # O si está en la lista de fixtures conocidos
            if callable(obj) and name in self.bridge.fixture_manager.fixtures:
                return True
        
        return False
    
    def _resolve_fixture(self, name: str) -> Any:
        """Resuelve un fixture por nombre."""
        # Usar el bridge para obtener el valor
        return self.bridge.get_fixture_value(name)
    
    def _cleanup_fixture(self, name: str):
        """Limpia un fixture después de usarlo."""
        self.bridge.cleanup_fixture(name)
        self.adapter.cleanup_fixture(name)


def adapt_pytest_fixtures(bridge: Optional[PytestBridge] = None) -> Callable:
    """Decorator factory: Adapta automáticamente fixtures de pytest.
    
    Uso:
        @adapt_pytest_fixtures()
        def test_con_db(db):
            # 'db' viene del conftest.py de pytest
            pass
    """
    def decorator(fn: F) -> F:
        # Si no hay bridge, crear uno para el módulo del test
        nonlocal bridge
        if bridge is None:
            test_file = inspect.getfile(fn)
            bridge = create_bridge_for_test(test_file)
        
        if bridge is None:
            # No hay conftest.py, usar función original
            return fn
        
        # Crear inyector y envolver función
        injector = FixtureInjector(bridge)
        return injector.inject_fixtures(fn, inspect.getfile(fn))
    
    return decorator


# Importar factory del bridge
from .pytest_bridge import create_bridge_for_test
