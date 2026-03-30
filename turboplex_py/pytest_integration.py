"""Pytest Bridge Integration - Integra el bridge con collector y runner.

Este módulo conecta el PytestBridge con el sistema de descubrimiento 
y ejecución de tests de TurboPlex.
"""

from __future__ import annotations

import os
import pathlib
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple
import logging

from .pytest_bridge import PytestBridge, create_bridge_for_test
from .fixture_adapter import FixtureInjector
from .plugin_adapters import get_plugin_manager

logger = logging.getLogger(__name__)


class PytestCompatMode:
    """Activa modo compatibilidad pytest para un test."""
    
    def __init__(self, test_path: str):
        self.test_path = test_path
        self.bridge: Optional[PytestBridge] = None
        self.injector: Optional[FixtureInjector] = None
        self._initialized = False
    
    def initialize(self) -> bool:
        """Inicializa el modo compatibilidad."""
        if self._initialized:
            return True
        
        # Buscar conftest.py
        self.bridge = create_bridge_for_test(self.test_path)
        
        if self.bridge is None:
            logger.debug(f"No se encontró conftest.py para {self.test_path}")
            return False
        
        # Cargar conftest lazy (rápido)
        if not self.bridge.load_conftest_lazy():
            return False
        
        # Crear injector de fixtures
        self.injector = FixtureInjector(self.bridge)
        
        self._initialized = True
        logger.info(f"Modo compatibilidad pytest activado para {self.test_path}")
        logger.info(f"  - Fixtures: {len(self.bridge.fixture_manager.fixtures)}")
        return True
    
    def get_test_params(self, test_func: Callable) -> List[str]:
        """Obtiene los parámetros de un test (que son fixtures de pytest)."""
        if not self._initialized:
            return []
        
        import inspect
        sig = inspect.signature(test_func)
        params = []
        
        for name in sig.parameters:
            # Verificar si es un fixture registrado
            if self.bridge.fixture_manager.get(name):
                params.append(name)
        
        return params
    
    def prepare_test(self, test_func: Callable) -> Callable:
        """Prepara una función de test con fixtures inyectados."""
        if not self._initialized or self.injector is None:
            return test_func
        
        # Adaptar plugins (asyncio, anyio)
        adapted = get_plugin_manager().adapt_test(test_func)
        
        # Inyectar fixtures
        return self.injector.inject_fixtures(adapted, self.test_path)
    
    def setup_test(self, test_name: str):
        """Setup antes de ejecutar un test."""
        if not self._initialized or self.bridge is None:
            return
        
        # Llamar hooks de pytest
        self.bridge.call_hook(
            "pytest_runtest_setup",
            item=MockPytestItem(test_name, self.test_path),
        )
    
    def teardown_test(self, test_name: str, outcome: str = "passed"):
        """Teardown después de ejecutar un test."""
        if not self._initialized or self.bridge is None:
            return
        
        # Llamar hooks de pytest
        self.bridge.call_hook(
            "pytest_runtest_teardown",
            item=MockPytestItem(test_name, self.test_path),
            nextitem=None,
        )
    
    def session_start(self):
        """Inicio de sesión de tests."""
        if not self._initialized or self.bridge is None:
            return
        
        # Cargar conftest completo (lazy load completo)
        self.bridge.load_conftest_full()
        
        # Llamar hook de inicio de sesión
        self.bridge.call_hook(
            "pytest_sessionstart",
            session=MockPytestSession(self.bridge.conftest_path),
        )
    
    def session_finish(self, exitstatus: int = 0):
        """Fin de sesión de tests."""
        if not self._initialized or self.bridge is None:
            return
        
        # Llamar hook de fin de sesión
        self.bridge.call_hook(
            "pytest_sessionfinish",
            session=MockPytestSession(self.bridge.conftest_path),
            exitstatus=exitstatus,
        )


# Mock objects para simular objetos de pytest

class MockPytestItem:
    """Simula un Item de pytest (test individual)."""
    
    def __init__(self, name: str, path: str):
        self.name = name
        self.nodeid = f"{path}::{name}"
        self.fspath = pathlib.Path(path)
        self.path = self.fspath
        self.location = (str(self.fspath), 0, name)
    
    def __repr__(self):
        return f"MockPytestItem({self.nodeid})"


class MockPytestSession:
    """Simula una Session de pytest."""
    
    def __init__(self, conftest_path: Optional[str] = None):
        self.config = MockPytestConfig()
        self.items = []
        self._conftest_path = conftest_path
    
    @property
    def fspath(self):
        if self._conftest_path:
            return pathlib.Path(self._conftest_path).parent
        return pathlib.Path.cwd()


class MockPytestConfig:
    """Simula Config de pytest."""
    
    def __init__(self):
        self.option = MockOptions()
        self._hook = MockHook()
    
    def getoption(self, name: str, default=None):
        return getattr(self.option, name, default)


class MockOptions:
    """Simula options de pytest."""
    
    def __init__(self):
        self.verbose = 0
        self.capture = "fd"
        self.no_header = False
        self.no_summary = False


class MockHook:
    """Simula el hook system de pytest."""
    
    def __init__(self):
        pass
    
    def pytest_runtest_setup(self, item):
        pass


# Funciones de ayuda para integración

def detect_conftest_paths(test_paths: List[str]) -> Dict[str, str]:
    """Detecta qué tests tienen conftest.py y devuelve mapeo."""
    mapping = {}
    
    for test_path in test_paths:
        path = pathlib.Path(test_path).resolve()
        
        # Buscar conftest.py
        for parent in [path] + list(path.parents):
            conftest = parent / "conftest.py"
            if conftest.exists():
                mapping[str(test_path)] = str(conftest)
                break
    
    return mapping


def has_pytest_fixtures(test_path: str) -> bool:
    """Verifica si un archivo de test usa fixtures de pytest."""
    try:
        with open(test_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        # Buscar patrones típicos de fixtures de pytest
        indicators = [
            "def test_",  # Funciones de test
            "(db)",       # Parámetro db común
            "(client)",   # Parámetro client común
            "(session)",  # Parámetro session
            "(request)", # Fixture request de pytest
        ]
        
        # Buscar si hay funciones con parámetros que podrían ser fixtures
        import ast
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return False
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name.startswith("test_"):
                    # Tiene parámetros además de self/cls?
                    args = [arg.arg for arg in node.args.args]
                    non_self_args = [a for a in args if a not in ('self', 'cls')]
                    if non_self_args:
                        return True
        
        return False
        
    except Exception as e:
        logger.debug(f"Error verificando fixtures en {test_path}: {e}")
        return False


# Cache de modos compatibilidad
_compat_modes: Dict[str, PytestCompatMode] = {}


def get_compat_mode(test_path: str) -> Optional[PytestCompatMode]:
    """Obtiene (o crea) un modo compatibilidad para un test."""
    global _compat_modes
    
    if test_path in _compat_modes:
        return _compat_modes[test_path]
    
    # Verificar si tiene fixtures de pytest
    if not has_pytest_fixtures(test_path):
        return None
    
    # Crear nuevo modo
    mode = PytestCompatMode(test_path)
    if mode.initialize():
        _compat_modes[test_path] = mode
        return mode
    
    return None


def clear_compat_cache():
    """Limpia el cache de modos compatibilidad."""
    global _compat_modes
    _compat_modes.clear()
