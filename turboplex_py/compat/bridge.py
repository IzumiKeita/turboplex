"""Pytest Bridge for TurboPlex - Compatibilidad total con fixtures de pytest.

Este módulo permite a TurboPlex ejecutar tests escritos para pytest,
interceptando y adaptando automáticamente fixtures, hooks y plugins.
Incluye soporte nativo para fixtures built-in de pytest (tmp_path, monkeypatch, capsys).
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import pathlib
import sys
import tempfile
import io
from dataclasses import dataclass, field
from typing import Any, Callable, Generator, Optional, Dict, List, Set, TextIO
import logging
import time

# Configurar logging
logger = logging.getLogger(__name__)


# =============================================================================
# Built-in Fixtures Implementation
# =============================================================================

class Monkeypatch:
    """Implementación de monkeypatch fixture de pytest."""
    
    def __init__(self):
        self._env_before: Dict[str, Optional[str]] = {}
        self._attr_before: List[tuple] = []
        self._cwd_before: Optional[str] = None
    
    def setenv(self, name: str, value: str, prepend: Optional[str] = None):
        """Set environment variable."""
        if name not in self._env_before:
            self._env_before[name] = os.environ.get(name)
        if prepend is not None:
            value = prepend + value
        os.environ[name] = value
    
    def delenv(self, name: str, raising: bool = True):
        """Delete environment variable."""
        if name not in self._env_before:
            self._env_before[name] = os.environ.get(name)
        if raising and name not in os.environ:
            raise KeyError(name)
        os.environ.pop(name, None)
    
    def setattr(self, target: Any, name: str, value: Any, raising: bool = True):
        """Set attribute on target."""
        old_value = getattr(target, name, None)
        self._attr_before.append((target, name, old_value))
        setattr(target, name, value)
    
    def delattr(self, target: Any, name: str, raising: bool = True):
        """Delete attribute from target."""
        old_value = getattr(target, name, None)
        self._attr_before.append((target, name, old_value))
        if raising and not hasattr(target, name):
            raise AttributeError(name)
        delattr(target, name)
    
    def chdir(self, path: str):
        """Change current working directory."""
        if self._cwd_before is None:
            self._cwd_before = os.getcwd()
        os.chdir(path)
    
    def undo(self):
        """Undo all monkeypatched changes."""
        # Restore environment variables
        for name, value in self._env_before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._env_before.clear()
        
        # Restore attributes
        for target, name, value in reversed(self._attr_before):
            try:
                setattr(target, name, value)
            except Exception:
                pass
        self._attr_before.clear()
        
        # Restore working directory
        if self._cwd_before is not None:
            os.chdir(self._cwd_before)
            self._cwd_before = None


class CapturedIO:
    """Objecto para capturar stdout/stderr."""
    
    def __init__(self, stdout: str, stderr: str):
        self.out = stdout
        self.err = stderr
        self.stdout = stdout
        self.stderr = stderr
    
    def __repr__(self):
        return f"CapturedIO(out={self.out!r}, err={self.err!r})"


class CapsysFixture:
    """Implementación de capsys fixture de pytest."""
    
    def __init__(self):
        self._stdout_buffer = io.StringIO()
        self._stderr_buffer = io.StringIO()
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
    
    def _start_capture(self):
        """Start capturing stdout/stderr."""
        sys.stdout = self._stdout_buffer
        sys.stderr = self._stderr_buffer
    
    def _stop_capture(self) -> CapturedIO:
        """Stop capturing and return captured output."""
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
        
        stdout = self._stdout_buffer.getvalue()
        stderr = self._stderr_buffer.getvalue()
        
        return CapturedIO(stdout, stderr)
    
    def readouterr(self) -> CapturedIO:
        """Read captured output."""
        return CapturedIO(
            self._stdout_buffer.getvalue(),
            self._stderr_buffer.getvalue()
        )
    
    def _finalize(self):
        """Restore original stdout/stderr."""
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr


@dataclass
class PytestFixtureInfo:
    """Información de un fixture de pytest extraído del AST."""
    name: str
    function_name: str
    scope: str = "function"  # function, class, module, session
    autouse: bool = False
    params: Optional[List[Any]] = None
    dependencies: List[str] = field(default_factory=list)
    has_yield: bool = False
    source_file: str = ""
    lineno: int = 0


@dataclass  
class PytestHookInfo:
    """Información de un hook de pytest."""
    name: str
    function: Callable
    priority: int = 0


class HookManager:
    """Gestiona hooks de pytest."""
    
    # Hooks críticos que debemos soportar
    PYTEST_HOOKS = [
        "pytest_sessionstart",
        "pytest_sessionfinish",
        "pytest_collection_modifyitems",
        "pytest_runtest_setup",
        "pytest_runtest_call",
        "pytest_runtest_teardown",
        "pytest_runtest_makereport",
        "pytest_fixture_setup",
        "pytest_fixture_post_finalizer",
    ]
    
    def __init__(self):
        self.hooks: Dict[str, List[Callable]] = {name: [] for name in self.PYTEST_HOOKS}
        self._call_history: List[tuple] = []
    
    def register(self, hook_name: str, func: Callable, priority: int = 0):
        """Registra un hook."""
        if hook_name not in self.hooks:
            logger.warning(f"Hook desconocido: {hook_name}")
            return
        
        self.hooks[hook_name].append(func)
        # Ordenar por prioridad (menor = primero)
        self.hooks[hook_name].sort(key=lambda f: getattr(f, '_priority', 0))
        logger.debug(f"Registrado hook {hook_name}: {func.__name__}")
    
    def call(self, hook_name: str, **kwargs) -> List[Any]:
        """Ejecuta todos los hooks registrados para un nombre."""
        if hook_name not in self.hooks:
            return []
        
        results = []
        for func in self.hooks[hook_name]:
            try:
                # Filtrar kwargs según los parámetros de la función
                sig = inspect.signature(func)
                valid_params = set(sig.parameters.keys())
                filtered_kwargs = {k: v for k, v in kwargs.items() if k in valid_params}
                
                result = func(**filtered_kwargs)
                results.append(result)
                self._call_history.append((hook_name, func.__name__, "success"))
            except Exception as e:
                logger.error(f"Error en hook {hook_name}.{func.__name__}: {e}")
                self._call_history.append((hook_name, func.__name__, f"error: {e}"))
        
        return results
    
    def get_history(self) -> List[tuple]:
        """Devuelve historial de llamadas a hooks."""
        return self._call_history.copy()


class FixtureManager:
    """Gestiona fixtures de pytest convertidos a TurboPlex."""
    
    def __init__(self):
        self.fixtures: Dict[str, PytestFixtureInfo] = {}
        self._fixture_cache: Dict[str, Any] = {}
        self._active_generators: Dict[str, Generator] = {}
    
    def register(self, fixture_info: PytestFixtureInfo):
        """Registra un fixture."""
        self.fixtures[fixture_info.name] = fixture_info
        logger.debug(f"Registrado fixture: {fixture_info.name} (scope={fixture_info.scope})")
    
    def get(self, name: str) -> Optional[PytestFixtureInfo]:
        """Obtiene información de un fixture."""
        return self.fixtures.get(name)
    
    def list_dependencies(self, name: str) -> List[str]:
        """Lista las dependencias de un fixture."""
        fixture = self.fixtures.get(name)
        if not fixture:
            return []
        return fixture.dependencies
    
    def resolve_order(self, fixture_names: List[str]) -> List[str]:
        """Resuelve el orden de ejecución de fixtures según dependencias."""
        # Implementación simple de ordenamiento topológico
        resolved = []
        visited = set()
        temp_mark = set()
        
        def visit(name):
            if name in temp_mark:
                raise ValueError(f"Dependencia circular detectada: {name}")
            if name in visited:
                return
            
            temp_mark.add(name)
            fixture = self.fixtures.get(name)
            if fixture:
                for dep in fixture.dependencies:
                    visit(dep)
            temp_mark.remove(name)
            visited.add(name)
            resolved.append(name)
        
        for name in fixture_names:
            visit(name)
        
        return resolved


class PytestBridge:
    """Bridge principal que adapta pytest a TurboPlex."""
    
    # Built-in fixtures nativos que TurboPlex provee
    BUILTIN_FIXTURES = {'tmp_path', 'monkeypatch', 'capsys'}
    
    def __init__(self, conftest_path: Optional[str] = None):
        self.conftest_path = conftest_path
        self.fixture_manager = FixtureManager()
        self.hook_manager = HookManager()
        self._conftest_module: Optional[Any] = None
        self._is_loaded = False
        
        # Cache de fixtures resueltos
        self._fixture_values: Dict[str, Any] = {}
        
        # Track de fixtures built-in que necesitan cleanup
        self._builtin_fixtures_to_cleanup: Set[str] = set()
    
    def find_conftest(self, test_path: str) -> Optional[str]:
        """Busca el conftest.py más cercano al test."""
        path = pathlib.Path(test_path).resolve()
        
        # Buscar hacia arriba en la jerarquía
        for parent in [path] + list(path.parents):
            conftest = parent / "conftest.py"
            if conftest.exists():
                return str(conftest)
        
        return None
    
    def load_conftest_lazy(self, conftest_path: Optional[str] = None) -> bool:
        """Carga conftest.py de forma lazy usando AST parsing.
        
        Evita ejecutar código pesado al importar.
        """
        if self._is_loaded:
            return True
        
        conftest_path = conftest_path or self.conftest_path
        if not conftest_path or not os.path.exists(conftest_path):
            logger.debug(f"No se encontró conftest.py en {conftest_path}")
            return False
        
        try:
            # Fase 1: Parsear AST sin ejecutar
            with open(conftest_path, 'r', encoding='utf-8') as f:
                source = f.read()
            
            tree = ast.parse(source)
            
            # Extraer fixtures del AST
            fixtures = self._extract_fixtures_from_ast(tree, conftest_path)
            for fixture_info in fixtures:
                self.fixture_manager.register(fixture_info)
            
            # Extraer hooks del AST
            hooks = self._extract_hooks_from_ast(tree)
            for hook_name, func_name in hooks:
                # Los hooks se registran cuando se carga el módulo real
                pass
            
            self.conftest_path = conftest_path
            self._is_loaded = True
            
            logger.info(f"Conftest cargado lazy: {conftest_path}")
            logger.info(f"  - Fixtures: {len(fixtures)}")
            logger.info(f"  - Hooks: {len(hooks)}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error cargando conftest.py: {e}")
            return False
    
    def load_conftest_full(self) -> bool:
        """Carga el conftest.py completo (con ejecución de código)."""
        import time
        start = time.perf_counter()
        
        print(f"[BRIDGE DEBUG] load_conftest_full() llamado", flush=True)
        print(f"[BRIDGE DEBUG] conftest_path: {self.conftest_path}", flush=True)
        print(f"[BRIDGE DEBUG] _conftest_module: {self._conftest_module}", flush=True)
        
        if self._conftest_module is not None:
            print(f"[BRIDGE DEBUG] conftest ya cargado, retornando True", flush=True)
            return True
        
        if not self.conftest_path:
            print(f"[BRIDGE DEBUG] ERROR: conftest_path es None o vacío", flush=True)
            return False
        
        if not os.path.exists(self.conftest_path):
            print(f"[BRIDGE DEBUG] ERROR: conftest no existe: {self.conftest_path}", flush=True)
            return False
        
        print(f"[BRIDGE DEBUG] Intentando cargar conftest desde: {self.conftest_path}", flush=True)
        
        # Activar lazy patcher antes de cargar
        from ..db.lazy_patcher import get_patcher
        patcher = get_patcher()
        patcher.patch_all()
        
        try:
            # Cargar el módulo
            spec = importlib.util.spec_from_file_location("conftest", self.conftest_path)
            if not spec or not spec.loader:
                return False
            
            module = importlib.util.module_from_spec(spec)
            
            # Ejecutar el módulo (operaciones DB serán lazy)
            spec.loader.exec_module(module)
            
            self._conftest_module = module
            self._db_patcher = patcher  # Guardar referencia
            
            # Registrar hooks encontrados
            for hook_name in self.hook_manager.PYTEST_HOOKS:
                if hasattr(module, hook_name):
                    self.hook_manager.register(hook_name, getattr(module, hook_name))
            
            # TURBO_GUIDE: Measure time and suggest lazy imports if slow
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(f"Conftest loaded completely (with lazy patcher): {self.conftest_path}")
            logger.info(f"  - Pending DDL operations: {patcher.get_pending_count()}")
            
            if elapsed_ms > 1000:  # 1 second threshold
                print(f"\n⚠️  TURBO_GUIDE: conftest.py loaded in {elapsed_ms:.0f}ms (slow profile)", flush=True)
                print(f"   The file {self.conftest_path} has heavy imports slowing down discovery.", flush=True)
                print(f"   💡 Tip: Use 'lazy imports' to speed up:", flush=True)
                print(f"", flush=True)
                print(f"      # BEFORE (slow):", flush=True)
                print(f"      from myapp.database import db  # Executes on import", flush=True)
                print(f"", flush=True)
                print(f"      # AFTER (fast):", flush=True)
                print(f"      import os", flush=True)
                print(f"      if os.getenv('TURBOPLEX_MODE'):", flush=True)
                print(f"          pass  # TurboPlex mode - deferred imports", flush=True)
                print(f"      else:", flush=True)
                print(f"          from myapp.database import db", flush=True)
                print(f"", flush=True)
                print(f"   Or use: tpx --light --path tests/  (skips heavy conftest.py)", flush=True)
                print(f"   Documentation: TURBOPLEX_GUIDE.md section 'Hybrid conftest.py'\n", flush=True)
            
            return True
            
        except Exception as e:
            print(f"[BRIDGE DEBUG] ERROR cargando conftest: {e}", flush=True)
            import traceback
            print(f"[BRIDGE DEBUG] Traceback: {traceback.format_exc()}", flush=True)
            return False
    
    def _extract_fixtures_from_ast(self, tree: ast.AST, source_file: str) -> List[PytestFixtureInfo]:
        """Extrae información de fixtures del AST."""
        fixtures = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Buscar decorador @pytest.fixture
                is_fixture, scope, autouse, params = self._parse_fixture_decorators(node)
                
                if is_fixture:
                    # Extraer dependencias de los argumentos
                    dependencies = []
                    for arg in node.args.args:
                        if arg.arg not in ('self', 'cls', 'request'):
                            dependencies.append(arg.arg)
                    
                    # Detectar si usa yield (generador)
                    has_yield = any(
                        isinstance(n, ast.Yield) 
                        for n in ast.walk(node)
                    )
                    
                    fixture_info = PytestFixtureInfo(
                        name=node.name,
                        function_name=node.name,
                        scope=scope,
                        autouse=autouse,
                        params=params,
                        dependencies=dependencies,
                        has_yield=has_yield,
                        source_file=source_file,
                        lineno=node.lineno,
                    )
                    fixtures.append(fixture_info)
        
        return fixtures
    
    def _parse_fixture_decorators(self, node: ast.FunctionDef) -> tuple:
        """Parsea los decoradores de pytest.fixture."""
        is_fixture = False
        scope = "function"
        autouse = False
        params = None
        
        for decorator in node.decorator_list:
            # @pytest.fixture
            # @pytest.fixture(scope="module")
            # @pytest.fixture(autouse=True)
            
            if isinstance(decorator, ast.Call):
                # Decorador con argumentos
                func = decorator.func
                if isinstance(func, ast.Attribute):
                    if func.attr == "fixture":
                        is_fixture = True
                        
                        # Parsear argumentos
                        for kw in decorator.keywords:
                            if kw.arg == "scope":
                                if isinstance(kw.value, ast.Constant):
                                    scope = kw.value.value
                            elif kw.arg == "autouse":
                                if isinstance(kw.value, ast.Constant):
                                    autouse = kw.value.value
                            elif kw.arg == "params":
                                if isinstance(kw.value, ast.List):
                                    params = []  # Simplificado
                
                elif isinstance(func, ast.Name):
                    if func.id == "fixture":
                        is_fixture = True
            
            elif isinstance(decorator, ast.Attribute):
                if decorator.attr == "fixture":
                    is_fixture = True
            
            elif isinstance(decorator, ast.Name):
                if decorator.id == "fixture":
                    is_fixture = True
        
        return is_fixture, scope, autouse, params
    
    def _extract_hooks_from_ast(self, tree: ast.AST) -> List[tuple]:
        """Extrae nombres de hooks del AST."""
        hooks = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name.startswith("pytest_"):
                    hooks.append((node.name, node.lineno))
        
        return hooks
    
    def get_fixture_value(self, name: str) -> Any:
        """Obtiene el valor de un fixture (resolviendo dependencias).
        
        Soporta fixtures de conftest.py y fixtures built-in de pytest
        (tmp_path, monkeypatch, capsys) generados nativamente por TurboPlex.
        """
        # Verificar cache
        if name in self._fixture_values:
            return self._fixture_values[name]
        
        # Verificar si es un fixture built-in nativo
        if name in self.BUILTIN_FIXTURES:
            value = self._create_builtin_fixture(name)
            self._fixture_values[name] = value
            self._builtin_fixtures_to_cleanup.add(name)
            return value
        
        # Cargar conftest completo si es necesario
        if not self._conftest_module:
            self.load_conftest_full()
        
        if not self._conftest_module:
            raise RuntimeError(f"No se pudo cargar conftest para obtener fixture {name}")
        
        # Detectar si es un fixture de DB y hacer flush de DDL pendientes
        if self._is_db_fixture(name):
            self._flush_ddl_if_needed()
        
        # Obtener fixture del módulo
        if not hasattr(self._conftest_module, name):
            raise RuntimeError(f"Fixture {name} no encontrado en conftest")
        
        fixture_func = getattr(self._conftest_module, name)
        
        # FIX: Detectar y extraer función subyacente de fixtures pytest
        # Los fixtures @pytest.fixture están envueltos y no pueden llamarse directamente
        actual_func = fixture_func
        fixture_type = type(fixture_func)
        
        # Caso 1: FixtureFunctionDefinition de pytest (tiene _fixture_function)
        if hasattr(fixture_func, '_fixture_function'):
            actual_func = fixture_func._fixture_function
        # Caso 2: _pytestfixturefunction marker
        elif hasattr(fixture_func, '_pytestfixturefunction'):
            import inspect
            try:
                unwrapped = inspect.unwrap(fixture_func)
                if callable(unwrapped) and unwrapped is not fixture_func:
                    actual_func = unwrapped
            except Exception:
                if hasattr(fixture_func, '__wrapped__'):
                    actual_func = fixture_func.__wrapped__
                elif hasattr(fixture_func, 'func'):
                    actual_func = fixture_func.func
        
        fixture_func = actual_func
        
        # Resolver dependencias recursivamente
        fixture_info = self.fixture_manager.get(name)
        kwargs = {}
        
        if fixture_info:
            for dep in fixture_info.dependencies:
                kwargs[dep] = self.get_fixture_value(dep)
        
        # Ejecutar fixture
        if fixture_info and fixture_info.has_yield:
            # Es un generador
            gen = fixture_func(**kwargs)
            value = next(gen)
            self.fixture_manager._active_generators[name] = gen
        else:
            # Función normal
            value = fixture_func(**kwargs)
        
        self._fixture_values[name] = value
        return value
    
    def _create_builtin_fixture(self, name: str) -> Any:
        """Crea un fixture built-in nativo de pytest.
        
        Implementa tmp_path, monkeypatch, y capsys sin depender de conftest.py.
        """
        if name == 'tmp_path':
            # Crear directorio temporal único para el test
            tmp_dir = tempfile.mkdtemp(prefix='tpx_tmp_')
            return pathlib.Path(tmp_dir)
        
        elif name == 'monkeypatch':
            # Crear instancia de Monkeypatch
            return Monkeypatch()
        
        elif name == 'capsys':
            # Crear instancia de CapsysFixture y empezar captura
            capsys = CapsysFixture()
            capsys._start_capture()
            return capsys
        
        else:
            raise RuntimeError(f"Fixture built-in {name} no implementado")
    
    def _is_db_fixture(self, name: str) -> bool:
        """Detecta si un fixture es de base de datos."""
        db_fixture_names = {'db', 'session', 'engine', 'connection', 'client', 'async_client'}
        return name in db_fixture_names or 'db' in name.lower() or 'session' in name.lower()
    
    def _flush_ddl_if_needed(self):
        """Ejecuta operaciones DDL pendientes si hay patcher activo."""
        if hasattr(self, '_db_patcher') and self._db_patcher:
            if self._db_patcher.is_patched() and self._db_patcher.get_pending_count() > 0:
                logger.info(f"Flushing {self._db_patcher.get_pending_count()} operaciones DDL pendientes")
                self._db_patcher.flush_ddl()
                # Desactivar patcher después de flush para comportamiento normal
                self._db_patcher.unpatch_all()
    
    def cleanup_fixture(self, name: str):
        """Limpia un fixture (ejecuta teardown si es generador o built-in)."""
        # Manejar cleanup de fixtures built-in
        if name in self._builtin_fixtures_to_cleanup and name in self._fixture_values:
            value = self._fixture_values[name]
            
            if name == 'monkeypatch' and isinstance(value, Monkeypatch):
                # Undo all monkeypatched changes
                value.undo()
            elif name == 'capsys' and isinstance(value, CapsysFixture):
                # Restore stdout/stderr
                value._finalize()
            elif name == 'tmp_path':
                # Opcional: limpiar directorio temporal
                # Nota: Por defecto no eliminamos para debugging
                pass
            
            self._builtin_fixtures_to_cleanup.discard(name)
        
        # Manejar cleanup de fixtures de conftest (generadores)
        if name in self.fixture_manager._active_generators:
            gen = self.fixture_manager._active_generators.pop(name)
            try:
                next(gen)  # Continuar hasta el final
            except StopIteration:
                pass
        
        if name in self._fixture_values:
            del self._fixture_values[name]
    
    def call_hook(self, hook_name: str, **kwargs) -> List[Any]:
        """Ejecuta un hook de pytest."""
        # Asegurar que conftest esté cargado
        if not self._conftest_module:
            self.load_conftest_full()
        
        return self.hook_manager.call(hook_name, **kwargs)
    
    def get_stats(self) -> dict:
        """Devuelve estadísticas del bridge."""
        return {
            "fixtures_registered": len(self.fixture_manager.fixtures),
            "hooks_registered": sum(len(h) for h in self.hook_manager.hooks.values()),
            "conftest_path": self.conftest_path,
            "is_loaded": self._is_loaded,
            "is_full_loaded": self._conftest_module is not None,
            "hook_history": self.hook_manager.get_history(),
        }


def create_bridge_for_test(test_path: str) -> Optional[PytestBridge]:
    """Factory function: Crea un bridge para un test específico."""
    bridge = PytestBridge()
    conftest = bridge.find_conftest(test_path)
    
    if conftest:
        bridge.load_conftest_lazy(conftest)
        return bridge
    
    return None
