"""Fixture Adapter - Convierte fixtures de pytest a TurboPlex.

Este módulo adapta el sistema de fixtures de pytest al sistema de TurboPlex,
permitiendo usar @pytest.fixture como si fuera @turboplex_py.fixture.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Dict, Generator, List, Optional, TypeVar, Tuple
from functools import wraps

from ..fixtures import fixture as turboplex_fixture
from .bridge import PytestBridge, PytestFixtureInfo

F = TypeVar("F", bound=Callable[..., Any])

# Diccionario de kwargs inyectados por el runner (ej: parametrización, mocks, etc.)
_EXTRA_KWARGS: Dict[str, Any] = {}


class LogCaptureHandler(logging.Handler):
    """Handler para capturar logs durante tests (equivalente a caplog de pytest).
    
    Proporciona acceso a los registros de logging capturados durante la ejecución
    de un test, permitiendo verificar mensajes, niveles y contenido.
    
    Ejemplo de uso:
        def test_something(caplog):
            logger.info("test message")
            assert "test message" in caplog.text
    """
    
    def __init__(self, level: int = logging.NOTSET):
        super().__init__(level)
        self.records: List[logging.LogRecord] = []
        
    def emit(self, record: logging.LogRecord) -> None:
        """Captura un registro de log."""
        self.records.append(record)
        
    @property
    def record_tuples(self) -> List[Tuple[str, int, str]]:
        """Retorna tuplas de (logger_name, level, message)."""
        return [(r.name, r.levelno, r.message) for r in self.records]
        
    @property
    def messages(self) -> List[str]:
        """Retorna solo los mensajes de los registros."""
        return [r.message for r in self.records]
        
    @property
    def text(self) -> str:
        """Retorna todos los mensajes como un string unido por newlines."""
        return '\n'.join(self.messages)
    
    @property
    def handler(self) -> 'LogCaptureHandler':
        """Retorna el handler (para compatibilidad con algunos tests)."""
        return self
        
    def clear(self) -> None:
        """Limpia todos los registros capturados."""
        self.records.clear()


def _gather_extra_kwargs(test_func: Callable) -> Dict[str, Any]:
    """Recolecta kwargs extra inyectados por el runner para la función dada.
    
    Filtra _EXTRA_KWARGS para incluir solo los argumentos que:
    - Están en la signatura de la función
    - No están marcados como protected_args (ej: @patch, @parametrize)
    
    Args:
        test_func: Función de test a inyectar
        
    Returns:
        Dict con los kwargs extra que deben inyectarse
    """
    import inspect
    from typing import get_type_hints
    
    # Obtener parámetros de la función
    sig = inspect.signature(test_func)
    func_params = set(sig.parameters.keys())
    
    # Obtener argumentos protegidos de decoradores
    protected_args = set()
    if hasattr(test_func, 'pytestmark'):
        markers = test_func.pytestmark
        if not isinstance(markers, (list, tuple)):
            markers = [markers]
        
        for marker in markers:
            if hasattr(marker, 'name') and marker.name == 'parametrize':
                if hasattr(marker, 'args') and marker.args:
                    argnames = marker.args[0]
                    if isinstance(argnames, str):
                        protected_args.update(a.strip() for a in argnames.split(','))
                    elif isinstance(argnames, (list, tuple)):
                        protected_args.update(argnames)
            elif hasattr(marker, 'name') and marker.name in ('patch', 'mock_patch'):
                if hasattr(marker, 'kwargs') and marker.kwargs:
                    target = marker.kwargs.get('target')
                    if target and isinstance(target, str):
                        protected_args.add(target)
    
    # Filtrar _EXTRA_KWARGS: solo params que están en fn y no en protected_args
    extra_kwargs = {}
    for name, value in _EXTRA_KWARGS.items():
        if name in func_params and name not in protected_args:
            extra_kwargs[name] = value
    
    return extra_kwargs


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
    
    # Built-in fixtures de pytest NO soportados nativamente (requieren --compat)
    UNSUPPORTED_BUILTINS = {
        'capfd', 'capsysbinary', 'monkeypatch_session',
        'tmp_path_factory', 'tmp_dir_factory', 'cache', 'record_property',
        'recwarn', 'caplog_session', 'request', 'pytestconfig'
    }
    
    def __init__(self, bridge: PytestBridge):
        self.bridge = bridge
        self.adapter = FixtureAdapter(bridge)
        self._module_fixture_values: Dict[str, Any] = {}
        self._resolving_tt: set[str] = set()
    
    def _get_protected_args(self, test_func: Callable) -> set:
        """Detecta argumentos ya cubiertos por decoradores de pytest/mock."""
        protected = set()
        
        if not hasattr(test_func, 'pytestmark'):
            return protected
        
        markers = test_func.pytestmark
        if not isinstance(markers, (list, tuple)):
            markers = [markers]
        
        for marker in markers:
            # @pytest.mark.parametrize -> args[0] contiene nombres de parámetros
            if hasattr(marker, 'name') and marker.name == 'parametrize':
                if hasattr(marker, 'args') and marker.args:
                    argnames = marker.args[0]
                    if isinstance(argnames, str):
                        protected.update(a.strip() for a in argnames.split(','))
                    elif isinstance(argnames, (list, tuple)):
                        protected.update(argnames)
            
            # @mock.patch / @patch -> kwargs 'target' o 'new' define el nombre del parámetro
            elif hasattr(marker, 'name') and marker.name in ('patch', 'mock_patch'):
                if hasattr(marker, 'kwargs') and marker.kwargs:
                    target = marker.kwargs.get('target')
                    if target and isinstance(target, str):
                        protected.add(target)
        
        return protected
    
    def inject_fixtures(self, test_func: Callable, test_path: str) -> Callable:
        """Crea una versión de la función con fixtures inyectados."""
        
        # Analizar parámetros de la función
        sig = inspect.signature(test_func)
        params = list(sig.parameters.keys())
        
        # Obtener argumentos ya cubiertos por decoradores (@patch, @parametrize)
        protected_args = self._get_protected_args(test_func)
        
        # Obtener kwargs extra inyectados por el runner
        extra_kwargs = _gather_extra_kwargs(test_func)
        
        # Identificar cuáles son fixtures (excluyendo argumentos protegidos y extra_kwargs)
        fixture_params = []
        for param in params:
            if param not in protected_args and param not in extra_kwargs and self._is_fixture(param):
                fixture_params.append(param)
        
        if not fixture_params and not extra_kwargs:
            # No hay nada que inyectar
            return test_func
        
        # Crear wrapper que inyecta fixtures y extra_kwargs
        @wraps(test_func)
        def wrapper():
            # Resolver kwargs extra primero (del runner)
            kwargs = dict(extra_kwargs)
            
            # Resolver fixtures para argumentos restantes
            for fixture_name in fixture_params:
                if fixture_name not in kwargs:
                    kwargs[fixture_name] = self._resolve_fixture(fixture_name)
            
            # Llamar función original con todo inyectado
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
        # Built-in fixtures nativos de TurboPlex
        if name in self.bridge.BUILTIN_FIXTURES:
            return True
        
        # caplog es un fixture built-in de pytest para capturar logs
        if name == 'caplog':
            return True
        
        # Buscar en el fixture manager del bridge (fixtures de pytest detectadas por AST)
        fixture_info = self.bridge.fixture_manager.get(name)
        if fixture_info:
            return True
        
        # Buscar en conftest cargado
        if self.bridge._conftest_module and hasattr(self.bridge._conftest_module, name):
            obj = getattr(self.bridge._conftest_module, name)
            # Verificar si es un fixture de pytest (tiene marca de pytest)
            if hasattr(obj, '_pytestfixturefunction'):
                return True
            # Verificar si es un fixture de TurboPlex (tiene marca _tt_fixture)
            if hasattr(obj, '_tt_fixture') and getattr(obj, '_tt_fixture'):
                return True
            # O si está en la lista de fixtures conocidos del bridge
            if callable(obj) and name in self.bridge.fixture_manager.fixtures:
                return True
            # Verificar si está en el registro __tt_fixtures__ del módulo
            tt_fixtures = getattr(self.bridge._conftest_module, '__tt_fixtures__', {})
            if isinstance(tt_fixtures, dict) and name in tt_fixtures:
                return True
        
        return False
    
    def _resolve_fixture(self, name: str, test_path: str = "") -> Any:
        """Resuelve un fixture por nombre con diagnóstico mejorado."""
        import inspect
        
        # Check for unsupported built-in fixture
        if name in self.UNSUPPORTED_BUILTINS:
            raise RuntimeError(
                f"\n🔧 TURBO_FIX: Fixture '{name}' is a pytest built-in not natively supported.\n"
                f"   This fixture requires full pytest compatibility mode.\n"
                f"\n   💡 QUICK FIX:\n"
                f"      tpx --compat --path {test_path or 'tests/'}\n"
                f"\n   📚 Documentation: TURBOPLEX_GUIDE.md section 'Compatibility Mode'\n"
            )
        
        # Native TurboPlex built-in fixtures
        if name in self.bridge.BUILTIN_FIXTURES:
            return self.bridge.get_fixture_value(name)
        
        # caplog: create LogCaptureHandler to capture logs
        if name == 'caplog':
            handler = LogCaptureHandler()
            logging.getLogger().addHandler(handler)
            return handler
        
        # First search in module values (fixtures defined in test file)
        if name in self._module_fixture_values:
            return self._module_fixture_values[name]
        
        # If it's a TurboPlex fixture in conftest, execute it correctly
        if self.bridge._conftest_module:
            # Check in __tt_fixtures__ registry
            tt_fixtures = getattr(self.bridge._conftest_module, '__tt_fixtures__', {})
            if isinstance(tt_fixtures, dict) and name in tt_fixtures:
                return self._call_tt_fixture(name, tt_fixtures[name])
            
            # Check if it's a module attribute with _tt_fixture mark
            if hasattr(self.bridge._conftest_module, name):
                obj = getattr(self.bridge._conftest_module, name)
                if hasattr(obj, '_tt_fixture') and getattr(obj, '_tt_fixture'):
                    return self._call_tt_fixture(name, obj)
        
        # If not found, provide detailed diagnosis
        if not self.bridge._conftest_module:
            # conftest.py exists but didn't load completely
            if self.bridge.conftest_path:
                raise RuntimeError(
                    f"\n🔧 TURBO_FIX: Could not load conftest.py to resolve fixture '{name}'\n"
                    f"   Location: {self.bridge.conftest_path}\n"
                    f"\n   Possible causes:\n"
                    f"   1. conftest.py has heavy imports that fail\n"
                    f"   2. There's a syntax error in the file\n"
                    f"\n   💡 SOLUTIONS:\n"
                    f"   - Use lazy imports in conftest.py:\n"
                    f"     import os\n"
                    f"     if os.getenv('TURBOPLEX_MODE'): pass\n"
                    f"     else: from myapp import db\n"
                    f"   - Or use compat mode: tpx --compat --path {test_path or 'tests/'}\n"
                )
        
        # If we get here, the fixture doesn't exist
        available = list(self.bridge.fixture_manager.fixtures.keys())[:10]
        suggestions = []
        if available:
            suggestions.append(f"   Available fixtures: {', '.join(available)}")
        suggestions.append(f"   Use: tpx --compat --path {test_path or 'tests/'} for full compatibility")
        
        raise RuntimeError(
            f"\n🔧 TURBO_FIX: Fixture '{name}' not found\n"
            f"\n   {chr(10).join(suggestions)}\n"
            f"\n   📚 Verify the fixture is defined in conftest.py\n"
        )

    def _call_tt_fixture(self, name: str, fn: Callable[..., Any], test_path: str = "") -> Any:
        import inspect

        if name in self._module_fixture_values:
            return self._module_fixture_values[name]
        if name in self._resolving_tt:
            raise RuntimeError(
                f"\n🔧 TURBO_FIX: Circular dependency detected in fixture '{name}'\n"
                f"   Fixtures cannot depend circularly on each other.\n"
                f"   Review the dependencies in your conftest.py\n"
            )
        self._resolving_tt.add(name)
        try:
            sig = inspect.signature(fn)
            deps: Dict[str, Any] = {}
            for pname, p in sig.parameters.items():
                if p.kind != inspect.Parameter.POSITIONAL_OR_KEYWORD:
                    raise RuntimeError(
                        f"\n🔧 TURBO_FIX: Fixture '{name}' has an invalid parameter '{pname}'.\n"
                        f"   Only positional or keyword parameters are allowed.\n"
                        f"   *args or **kwargs parameters are not supported in fixtures.\n"
                    )
                if self._is_fixture(pname):
                    deps[pname] = self._resolve_fixture(pname, test_path)
                elif p.default is inspect.Parameter.empty:
                    raise RuntimeError(
                        f"\n🔧 TURBO_FIX: Fixture '{name}' requires '{pname}' which is not registered.\n"
                        f"   Make sure '{pname}' is defined as a fixture before using it.\n"
                        f"   Or define a default value: def {name}({pname}=None)\n"
                    )

            if inspect.isgeneratorfunction(fn):
                gen = fn(**deps)
                try:
                    value = next(gen)
                except StopIteration:
                    value = None
                self.adapter._active_generators[name] = gen
            else:
                value = fn(**deps)

            self._module_fixture_values[name] = value
            return value
        finally:
            self._resolving_tt.discard(name)
    
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
from .bridge import create_bridge_for_test
