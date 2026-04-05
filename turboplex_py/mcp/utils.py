"""MCP utility functions."""

import functools
import inspect
import json
import os
import secrets
import shutil
import socket
import sys
import time
from typing import Any, Callable


def uuid_v7() -> str:
    """Generate a UUID v7 string."""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    uuid_int = (ts_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0x2 << 62) | rand_b
    hex32 = f"{uuid_int:032x}"
    return (
        f"{hex32[0:8]}-"
        f"{hex32[8:12]}-"
        f"{hex32[12:16]}-"
        f"{hex32[16:20]}-"
        f"{hex32[20:32]}"
    )


def json_normalize(payload):
    """Normalize payload using DecimalEncoder if available."""
    try:
        from turboplex_py.collector import DecimalEncoder
    except ImportError:
        # DecimalEncoder no disponible, retornar payload sin modificar
        return payload
    except Exception as e:
        # Otro error de import (ej: circular), loggear pero no ocultar
        import sys
        sys.stderr.write(f"[TPX WARNING] Error importando DecimalEncoder: {e}\n")
        return payload
    try:
        import json

        return json.loads(json.dumps(payload, cls=DecimalEncoder))
    except Exception:
        return payload


def resolve_python_executable() -> str:
    """Resolve the Python executable to use, prioritizing venv.

    Environment variables:
        TPX_PYTHON_EXE: Override Python executable path.
        TPX_MCP_DEBUG: If set, logs diagnostic info to stderr.
    """
    import sys as _sys

    debug = os.environ.get("TPX_MCP_DEBUG")
    override = os.environ.get("TPX_PYTHON_EXE")

    if override:
        # Validate that TPX_PYTHON_EXE exists and is executable
        if os.path.isfile(override) and os.access(override, os.X_OK):
            if debug:
                _sys.stderr.write(f"[TPX DEBUG] Using TPX_PYTHON_EXE: {override}\n")
                _sys.stderr.flush()
            return override
        # If invalid, warn but continue to fallback
        _sys.stderr.write(f"[TPX WARNING] TPX_PYTHON_EXE points to invalid executable: {override}. Falling back.\n")
        _sys.stderr.flush()

    # En venv, sys.executable es el python del venv (correcto)
    # sys._base_executable apunta al Python global (incorrecto para venv)
    exe = _sys.executable
    if isinstance(exe, str) and exe and os.path.basename(exe).lower().startswith("python"):
        if debug:
            _sys.stderr.write(f"[TPX DEBUG] Using sys.executable: {exe}\n")
            _sys.stderr.flush()
        return exe

    # Fallback a _base_executable solo si sys.executable no es válido
    base = getattr(_sys, "_base_executable", None)
    if isinstance(base, str) and base and os.path.basename(base).lower().startswith("python"):
        if debug:
            _sys.stderr.write(f"[TPX DEBUG] Using sys._base_executable fallback: {base}\n")
            _sys.stderr.flush()
        return base

    found = shutil.which("python") or shutil.which("py")
    if debug and found:
        _sys.stderr.write(f"[TPX DEBUG] Using which() fallback: {found}\n")
        _sys.stderr.flush()
    
    # Blindaje: Asegurar que siempre retornamos un ejecutable válido
    result = found or exe
    if not result:
        raise RuntimeError(
            "No se encontró ningún ejecutable de Python. "
            "Verifica que Python esté instalado y en PATH, "
            "o establece TPX_PYTHON_EXE apuntando al ejecutable correcto."
        )
    return result


# =============================================================================
# MÓDULO 1: PRE-FLIGHT HEALTH CHECK (v0.3.6+)
# Inspiración: Maelstrom - Abortar ejecución temprano si dependencias críticas
# no están listas, evitando tracebacks de 100 líneas.
# =============================================================================


class HealthCheckError(RuntimeError):
    """Error específico de pre-flight health check."""
    pass


class HealthCheckReport:
    """Reporte detallado de health check."""
    def __init__(self):
        self.checks: dict[str, dict] = {}
        self.passed = True

    def add_check(self, name: str, passed: bool, message: str, details: dict | None = None):
        self.checks[name] = {
            "passed": passed,
            "message": message,
            "details": details or {}
        }
        if not passed:
            self.passed = False

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "summary": f"{sum(1 for c in self.checks.values() if c['passed'])}/{len(self.checks)} checks passed"
        }


def check_postgres_connectivity(
    host: str | None = None,
    port: int | None = None,
    timeout: float = 3.0
) -> tuple[bool, str]:
    """Check PostgreSQL connectivity via socket (sin necesidad de psycopg2).

    Args:
        host: Host de PostgreSQL (default: PGHOST env var o localhost)
        port: Puerto de PostgreSQL (default: PGPORT env var o 5432)
        timeout: Timeout en segundos para la conexión de socket

    Returns:
        (passed, message) tuple
    """
    host = host or os.environ.get("PGHOST", "localhost")
    port = port or int(os.environ.get("PGPORT", "5432"))

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()

        if result == 0:
            return True, f"PostgreSQL accesible en {host}:{port}"
        else:
            return False, f"No se puede conectar a PostgreSQL en {host}:{port} (error {result})"
    except Exception as e:
        return False, f"Error verificando PostgreSQL: {e}"


def check_env_file(env_path: str = ".env") -> tuple[bool, str, dict]:
    """Check existencia y legibilidad del archivo .env.

    Returns:
        (passed, message, details) tuple con info de variables encontradas
    """
    details = {"path": env_path, "exists": False, "readable": False, "variables": 0}

    if not os.path.exists(env_path):
        return False, f"Archivo {env_path} no encontrado", details

    details["exists"] = True

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()
        details["readable"] = True

        # Contar variables (líneas que no están vacías ni comentadas)
        vars_found = sum(
            1 for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#") and "=" in line
        )
        details["variables"] = vars_found

        # Verificar variables críticas
        critical_vars = ["DATABASE_URL", "PGHOST", "PGUSER", "PGPASSWORD"]
        found_critical = [v for v in critical_vars if v in content]
        details["critical_vars_found"] = found_critical

        return True, f".env legible con {vars_found} variables ({len(found_critical)} críticas)", details
    except Exception as e:
        return False, f"Error leyendo {env_path}: {e}", details


def check_dependency_versions() -> tuple[bool, str, dict]:
    """Check versiones mínimas de dependencias críticas.

    Requisitos:
        - SQLAlchemy >= 2.0.0
        - pytest >= 7.0.0

    Returns:
        (passed, message, details) con versiones detectadas
    """
    details = {}
    all_passed = True

    checks = [
        ("sqlalchemy", "SQLAlchemy", "2.0.0"),
        ("pytest", "pytest", "7.0.0"),
    ]

    for module_name, display_name, min_version in checks:
        try:
            mod = __import__(module_name)
            version = getattr(mod, "__version__", "unknown")
            details[module_name] = {"version": version, "required": min_version}

            # Parse versión simple (major.minor.patch)
            def parse_ver(v: str) -> tuple:
                try:
                    return tuple(int(x) for x in v.split(".")[:3])
                except:
                    return (0, 0, 0)

            actual = parse_ver(version)
            required = parse_ver(min_version)

            if actual >= required:
                details[module_name]["passed"] = True
            else:
                details[module_name]["passed"] = False
                all_passed = False
        except ImportError:
            details[module_name] = {"version": "not_installed", "required": min_version, "passed": False}
            all_passed = False

    if all_passed:
        msg = "Todas las dependencias críticas cumplen versiones mínimas"
    else:
        failed = [k for k, v in details.items() if not v.get("passed")]
        msg = f"Dependencias con versiones insuficientes: {', '.join(failed)}"

    return all_passed, msg, details


def run_health_checks(
    check_postgres: bool = True,
    check_env: bool = True,
    check_deps: bool = True,
    env_path: str = ".env"
) -> HealthCheckReport:
    """Ejecuta todos los health checks y retorna reporte.

    Args:
        check_postgres: Verificar conectividad PostgreSQL
        check_env: Verificar archivo .env
        check_deps: Verificar versiones de dependencias
        env_path: Ruta al archivo .env

    Returns:
        HealthCheckReport con resultados detallados
    """
    report = HealthCheckReport()

    if check_postgres:
        passed, msg = check_postgres_connectivity()
        report.add_check("postgres_connectivity", passed, msg)

    if check_env:
        passed, msg, details = check_env_file(env_path)
        report.add_check("env_file", passed, msg, details)

    if check_deps:
        passed, msg, details = check_dependency_versions()
        report.add_check("dependency_versions", passed, msg, details)

    return report


def preflight_guard(
    check_postgres: bool = True,
    check_env: bool = True,
    check_deps: bool = True,
    env_path: str = ".env"
) -> HealthCheckReport:
    """Guardián de pre-flight: lanza HealthCheckError si checks fallan.

    Usar al inicio de operaciones críticas MCP para abortar temprano
    con mensajes claros en lugar de tracebacks confusos.

    Raises:
        HealthCheckError: Si algún check falla, con reporte detallado

    Returns:
        HealthCheckReport si todos los checks pasan
    """
    report = run_health_checks(check_postgres, check_env, check_deps, env_path)

    if not report.passed:
        failed = [name for name, check in report.checks.items() if not check["passed"]]
        raise HealthCheckError(
            f"Pre-flight health check fallido: {', '.join(failed)}. "
            f"Revise la configuración antes de continuar."
        )

    return report


def preflight_guard_decorator(
    check_postgres: bool = True,
    check_env: bool = True,
    check_deps: bool = True,
    env_path: str = ".env"
) -> Callable:
    """Decorador para aplicar preflight_guard a funciones MCP.

    Ejemplo:
        @preflight_guard_decorator()
        def discover(paths=None):
            # Solo ejecuta si health checks pasan
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            preflight_guard(check_postgres, check_env, check_deps, env_path)
            return func(*args, **kwargs)
        return wrapper
    return decorator


# =============================================================================
# MÓDULO 2: AUTOPSIA AUTOMÁTICA (v0.3.6+)
# Inspiración: Debugging de Memoria - Captura estado de variables locales
# en el frame de excepción para análisis post-mortem.
# =============================================================================


def _scrub_value(obj: Any, max_depth: int = 3, current_depth: int = 0) -> Any:
    """Limpia (scrub) objetos no serializables para JSON seguro.

    Estrategia:
        - Primitivos (int, str, bool, None): pasan directo
        - dict/list/tuple: recursivo con límite de profundidad
        - Objetos con __dict__: extraer atributos básicos
        - Objetos no serializables (DB sessions, files, etc.): repr() limitado

    Args:
        obj: Objeto a limpiar
        max_depth: Profundidad máxima de recursión
        current_depth: Profundidad actual

    Returns:
        Objeto seguro para JSON serialization
    """
    if current_depth >= max_depth:
        return f"<{type(obj).__name__}> (max depth reached)"

    # None
    if obj is None:
        return None

    # Primitivos
    if isinstance(obj, (bool, int, float, str)):
        return obj

    # Bytes - convertir a string representativa
    if isinstance(obj, bytes):
        if len(obj) > 100:
            return f"<bytes len={len(obj)}>"
        try:
            return obj.decode('utf-8', errors='replace')
        except:
            return f"<bytes len={len(obj)}>"

    # Listas y tuplas
    if isinstance(obj, (list, tuple)):
        result = []
        for item in obj[:50]:  # Limitar a 50 items
            result.append(_scrub_value(item, max_depth, current_depth + 1))
        if len(obj) > 50:
            result.append(f"<... {len(obj) - 50} more items>")
        return result

    # Diccionarios
    if isinstance(obj, dict):
        result = {}
        for k, v in list(obj.items())[:50]:  # Limitar a 50 keys
            # Solo keys string serializables
            key = str(k) if not isinstance(k, str) else k
            result[key] = _scrub_value(v, max_depth, current_depth + 1)
        if len(obj) > 50:
            result["<...>"] = f"{len(obj) - 50} more keys"
        return result

    # Excepciones - extraer info útil
    if isinstance(obj, BaseException):
        return {
            "type": type(obj).__name__,
            "message": str(obj)[:500],
            "args": _scrub_value(getattr(obj, 'args', ()), max_depth, current_depth + 1)
        }

    # Objetos con __dict__ - extraer atributos básicos
    if hasattr(obj, '__dict__') and not callable(obj):
        try:
            attrs = {}
            for name in list(obj.__dict__.keys())[:20]:
                val = getattr(obj, name)
                if not callable(val):
                    attrs[name] = _scrub_value(val, max_depth, current_depth + 1)
            return {
                "_type": type(obj).__name__,
                "_module": type(obj).__module__,
                "_attrs": attrs
            }
        except:
            pass

    # Tipos comunes no serializables - representación segura
    type_name = type(obj).__name__

    # Sesiones de DB, conexiones, archivos
    if type_name in ('Session', 'sessionmaker', 'Connection', 'Engine',
                     'BufferedReader', 'BufferedWriter', 'TextIOWrapper',
                     'socket', 'SocketType', 'Thread', 'Process', 'Lock',
                     'RLock', 'Event', 'Condition', 'Semaphore'):
        return f"<{type_name}> (non-serializable resource)"

    # Callable/functions
    if callable(obj):
        return f"<function {getattr(obj, '__name__', type_name)}>"

    # Fallback: repr limitado
    try:
        r = repr(obj)
        if len(r) > 200:
            r = r[:200] + "..."
        return f"<{type_name}> {r}"
    except:
        return f"<{type_name}> (unrepresentable)"


def capture_autopsy(exc: BaseException, max_frames: int = 3) -> dict:
    """Captura autopsia de excepción con variables locales.

    Usa el módulo inspect para capturar el estado de variables locales
    en los frames donde ocurrió la excepción.

    Args:
        exc: La excepción capturada
        max_frames: Número máximo de frames a capturar desde el traceback

    Returns:
        dict con autopsia serializable segura:
        {
            "exception_type": str,
            "exception_message": str,
            "frames": [
                {
                    "filename": str,
                    "function": str,
                    "lineno": int,
                    "locals": dict  # scrubbed
                }
            ]
        }
    """
    autopsy = {
        "exception_type": type(exc).__name__,
        "exception_message": str(exc)[:1000],
        "frames": []
    }

    # Obtener traceback
    tb = getattr(exc, '__traceback__', None)
    if tb is None:
        return autopsy

    # Recorrer frames del traceback
    frames = []
    current = tb
    while current is not None and len(frames) < max_frames:
        frame = current.tb_frame

        frame_info = {
            "filename": frame.f_code.co_filename,
            "function": frame.f_code.co_name,
            "lineno": current.tb_lineno,
            "locals": {}
        }

        # Capturar y limpiar variables locales
        try:
            locals_dict = dict(frame.f_locals)
            for name, value in list(locals_dict.items())[:30]:  # Limitar a 30 vars
                if not name.startswith('__'):  # Ignorar dunder privados
                    frame_info["locals"][name] = _scrub_value(value)
        except Exception as e:
            frame_info["locals_error"] = f"Error capturando locals: {e}"

        frames.append(frame_info)
        current = current.tb_next

    autopsy["frames"] = frames
    return autopsy


def autopsy_from_dict(result: dict, exc: BaseException) -> dict:
    """Añade autopsia a un resultado de test fallido.

    Args:
        result: Diccionario de resultado del test (ej: {"passed": False, ...})
        exc: Excepción que causó el fallo

    Returns:
        Resultado modificado con campo 'autopsy' añadido
    """
    result = dict(result)  # Copia para no mutar original
    result["autopsy"] = capture_autopsy(exc)
    return result


class AutopsyJSONEncoder(json.JSONEncoder):
    """JSON Encoder que maneja objetos no serializables vía scrubbing."""

    def default(self, obj):
        return _scrub_value(obj)


# =============================================================================
# MÓDULO 3: SCHEMA SYNC GUARD (SSG) - v0.3.6+
# El "Alembicazo Preventivo" - Evita correr tests si DB está desincronizada
# =============================================================================


class SchemaSyncError(HealthCheckError):
    """Error específico de desincronización entre DB y migraciones Alembic."""
    pass


def find_alembic_config(root_path: str | os.PathLike | None = None) -> str | None:
    """Busca alembic.ini en el proyecto.

    Args:
        root_path: Directorio raíz donde buscar (default: cwd)

    Returns:
        Path a alembic.ini o None si no existe
    """
    if root_path is None:
        root_path = os.getcwd()

    alembic_path = os.path.join(root_path, "alembic.ini")
    if os.path.isfile(alembic_path):
        return alembic_path

    # Buscar en parent directories (hasta 3 niveles)
    for _ in range(3):
        parent = os.path.dirname(root_path)
        if parent == root_path:
            break
        root_path = parent
        alembic_path = os.path.join(root_path, "alembic.ini")
        if os.path.isfile(alembic_path):
            return alembic_path

    return None


def _get_database_url(db_url: str | None = None) -> str | None:
    """Resuelve la URL de conexión a PostgreSQL.

    Jerarquía:
        1. Parámetro explícito db_url
        2. Environment variable DATABASE_URL
        3. Construir desde PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE
        4. Default: postgresql://postgres:postgres@localhost:5432/postgres

    Returns:
        URL de conexión o None si no se puede determinar
    """
    if db_url:
        return db_url

    # 2. DATABASE_URL env var
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url

    # 3. Construir desde componentes
    host = os.environ.get("PGHOST", "localhost")
    port = os.environ.get("PGPORT", "5432")
    user = os.environ.get("PGUSER", "postgres")
    password = os.environ.get("PGPASSWORD", "postgres")
    database = os.environ.get("PGDATABASE", "postgres")

    # Escapar caracteres especiales en password
    password = password.replace("%", "%25").replace("@", "%40").replace(":", "%3A")

    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def get_database_version(db_url: str | None = None, timeout_ms: int = 500) -> tuple[str | None, str | None]:
    """Obtiene la versión actual de Alembic desde la base de datos.

    Args:
        db_url: URL de conexión (auto-detect si None)
        timeout_ms: Timeout para la consulta

    Returns:
        (version_hash, error_message) tuple:
        - version_hash: El hash de versión o None si error
        - error_message: Descripción del error o None si éxito
    """
    url = _get_database_url(db_url)
    if not url:
        return None, "No se pudo determinar DATABASE_URL"

    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import SQLAlchemyError

        # Crear engine con timeout corto
        connect_timeout_s = max(1, (int(timeout_ms) + 999) // 1000)
        engine = create_engine(
            url,
            connect_args={"connect_timeout": connect_timeout_s},
            execution_options={"statement_timeout": timeout_ms}
        )

        with engine.connect() as conn:
            # Verificar si tabla alembic_version existe
            result = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'alembic_version'
                )
            """))
            table_exists = result.scalar()

            if not table_exists:
                engine.dispose()
                return None, "Tabla alembic_version no existe (DB no inicializada con Alembic)"

            # Obtener versión actual
            result = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            row = result.fetchone()

            engine.dispose()

            if row:
                return row[0], None
            else:
                return None, "Tabla alembic_version vacía"

    except SQLAlchemyError as e:
        return None, f"Error SQLAlchemy: {e}"
    except ImportError:
        return None, "SQLAlchemy no instalado"
    except Exception as e:
        return None, f"Error inesperado: {e}"


def get_alembic_head(alembic_ini_path: str | None = None) -> tuple[str | None, str | None]:
    """Obtiene el revision 'head' actual de las migraciones Alembic.

    Usa la API de Alembic directamente (sin subprocess) para máxima velocidad.

    Args:
        alembic_ini_path: Path a alembic.ini (auto-detect si None)

    Returns:
        (head_hash, error_message) tuple:
        - head_hash: El hash del head o None si error
        - error_message: Descripción del error o None si éxito
    """
    if alembic_ini_path is None:
        alembic_ini_path = find_alembic_config()

    if not alembic_ini_path:
        return None, "No se encontró alembic.ini"

    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        # Cargar configuración Alembic
        alembic_cfg = Config(alembic_ini_path)
        script = ScriptDirectory.from_config(alembic_cfg)

        # Obtener head actual
        head = script.get_current_head()

        if head:
            return head, None
        else:
            return None, "No hay migraciones disponibles (head es None)"

    except ImportError:
        return None, "Alembic no instalado"
    except Exception as e:
        return None, f"Error leyendo migraciones: {e}"


def check_alembic_sync(
    root_path: str | os.PathLike | None = None,
    db_url: str | None = None,
    timeout_ms: int = 500
) -> tuple[bool, str, dict]:
    """Valida sincronización entre base de datos y migraciones Alembic.

    El "Freno de Mano" que evita correr tests con DB desincronizada.

    Args:
        root_path: Directorio raíz del proyecto
        db_url: URL de conexión a PostgreSQL
        timeout_ms: Timeout para operaciones de DB

    Returns:
        (passed, message, details) tuple compatible con HealthCheckReport
    """
    details = {
        "alembic_detected": False,
        "db_version": None,
        "head_version": None,
        "synced": False
    }

    # 1. Detectar alembic.ini
    alembic_path = find_alembic_config(root_path)

    if not alembic_path:
        # Silenciosamente skip si no hay Alembic
        return True, "Alembic no detectado, skip", details

    details["alembic_detected"] = True
    details["alembic_ini_path"] = alembic_path

    # 2. Obtener head de migraciones
    head_hash, head_error = get_alembic_head(alembic_path)

    if head_error:
        return False, f"Error obteniendo head: {head_error}", details

    details["head_version"] = head_hash

    # 3. Obtener versión de DB
    db_hash, db_error = get_database_version(db_url, timeout_ms)

    if db_error:
        # Si la tabla no existe, es error grave
        if "no existe" in db_error.lower() or "not initialized" in db_error.lower():
            return False, f"DB no inicializada: {db_error}", details
        return False, f"Error leyendo DB: {db_error}", details

    details["db_version"] = db_hash

    # 4. Comparar versiones
    if db_hash == head_hash:
        details["synced"] = True
        return True, f"DB sincronizada con head [{head_hash[:8]}...]", details
    else:
        details["synced"] = False
        return (
            False,
            f"Freno de Mano: Base de Datos desincronizada. "
            f"Tu DB está en [{db_hash[:8]}...] pero el código espera [{head_hash[:8]}...]. "
            f'Corre "alembic upgrade head" antes de continuar.',
            details
        )


def preflight_guard(
    check_postgres: bool = True,
    check_env: bool = True,
    check_deps: bool = True,
    check_alembic: bool = True,
    env_path: str = ".env",
    db_url: str | None = None,
    alembic_timeout_ms: int = 500
) -> HealthCheckReport:
    """Guardián de pre-flight: lanza HealthCheckError si checks fallan.

    Versión extendida con Schema Sync Guard (SSG) - v0.3.6+

    Args:
        check_postgres: Verificar conectividad PostgreSQL
        check_env: Verificar archivo .env
        check_deps: Verificar versiones de dependencias
        check_alembic: Verificar sincronización Alembic (nuevo en v0.3.6+)
        env_path: Ruta al archivo .env
        db_url: URL de conexión a PostgreSQL (para SSG)
        alembic_timeout_ms: Timeout para check de Alembic

    Raises:
        HealthCheckError: Si algún check falla
        SchemaSyncError: Si la DB está desincronizada (subclase de HealthCheckError)

    Returns:
        HealthCheckReport si todos los checks pasan
    """
    # Asegurar que existe carpeta .tplex/logs para logs
    _ensure_tplex_logs()

    report = run_health_checks(check_postgres, check_env, check_deps, env_path)

    # Schema Sync Guard (v0.3.6+)
    if check_alembic:
        passed, msg, details = check_alembic_sync(None, db_url, alembic_timeout_ms)
        report.add_check("alembic_sync", passed, msg, details)

        # Si falla por desincronización, usar SchemaSyncError específico
        if not passed and details.get("db_version") and details.get("head_version"):
            raise SchemaSyncError(msg)

    if not report.passed:
        failed = [name for name, check in report.checks.items() if not check["passed"]]
        raise HealthCheckError(
            f"Pre-flight health check fallido: {', '.join(failed)}. "
            f"Revise la configuración antes de continuar."
        )

    return report


# =============================================================================
# MÓDULO 4: ASYNC BUFFERED LOGGING SYSTEM (v0.3.6+)
# Logs exclusivamente en .tplex/logs/tpx_mcp_session.log
# Buffer de memoria con flush asíncrono para rendimiento F1
# =============================================================================

import threading
import queue
from pathlib import Path
from datetime import datetime

# Global logger instance (singleton)
_tplex_logger: "TplexLogger | None" = None
_tplex_logger_lock = threading.Lock()


class TplexLogger:
    """Logger asíncrono con buffer de memoria para carpeta .tplex/logs/.

    Características:
        - Escribe en .tplex/logs/tpx_mcp_session.log
        - Buffer en memoria con flush asíncrono
        - Auto-crea carpetas .tplex/logs/ si no existen
        - Mode 'append' para preservar logs entre sesiones
    """

    def __init__(
        self,
        log_file: str | None = None,
        buffer_size: int = 1000,
        flush_interval_ms: float = 100.0,
        max_file_size_mb: float = 50.0
    ):
        """Inicializa el logger.

        Args:
            log_file: Path al archivo log (default: .tplex/logs/tpx_mcp_session.log)
            buffer_size: Tamaño del buffer antes de flush forzado
            flush_interval_ms: Intervalo máximo entre flushes
            max_file_size_mb: Tamaño máximo antes de rotación
        """
        if log_file is None:
            log_file = self._default_log_path()

        self.log_file = Path(log_file)
        self.buffer_size = buffer_size
        self.flush_interval_ms = flush_interval_ms
        self.max_file_size_bytes = max_file_size_mb * 1024 * 1024

        # Buffer de memoria thread-safe
        self._buffer: queue.Queue[str] = queue.Queue(maxsize=buffer_size * 2)
        self._lock = threading.Lock()
        self._flush_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_flush = time.time()

        # Auto-crear estructura de carpetas
        self._ensure_directories()

        # Iniciar thread de flush asíncrono
        self._start_flush_thread()

    def _default_log_path(self) -> str:
        """Retorna path por defecto: .tplex/logs/tpx_mcp_session.log"""
        return str(Path.cwd() / ".tplex" / "logs" / "tpx_mcp_session.log")

    def _ensure_directories(self) -> None:
        """Crea .tplex/logs/ si no existe."""
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            sys.stderr.write(f"[TPX WARNING] No se pudo crear {self.log_file.parent}: {e}\n")

    def _start_flush_thread(self) -> None:
        """Inicia thread de flush asíncrono."""
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

    def _flush_loop(self) -> None:
        """Loop de flush asíncrono en background."""
        while not self._stop_event.is_set():
            time.sleep(self.flush_interval_ms / 1000.0)
            self._flush_to_disk()

    def _flush_to_disk(self) -> None:
        """Escribe buffer acumulado a disco (append mode)."""
        lines: list[str] = []
        try:
            while not self._buffer.empty() and len(lines) < self.buffer_size:
                lines.append(self._buffer.get_nowait())
        except queue.Empty:
            pass

        if not lines:
            return

        # Verificar rotación por tamaño
        try:
            if self.log_file.exists() and self.log_file.stat().st_size > self.max_file_size_bytes:
                self._rotate_log()
        except Exception:
            pass

        # Escribir en append mode
        try:
            with open(self.log_file, "a", encoding="utf-8", errors="replace") as f:
                f.writelines(lines)
            self._last_flush = time.time()
        except Exception as e:
            sys.stderr.write(f"[TPX LOG ERROR] Fallo escritura log: {e}\n")

    def _rotate_log(self) -> None:
        """Rota archivo de log si excede tamaño máximo."""
        try:
            backup = self.log_file.with_suffix(".log.1")
            if backup.exists():
                backup.unlink()
            self.log_file.rename(backup)
        except Exception:
            pass

    def log(self, level: str, message: str, source: str = "MCP") -> None:
        """Añade mensaje al buffer.

        Args:
            level: Nivel de log (DEBUG, INFO, WARNING, ERROR)
            message: Mensaje a loggear
            source: Origen del mensaje
        """
        timestamp = datetime.now().isoformat()
        line = f"[{timestamp}] [{level:8}] [{source}] {message}\n"

        try:
            # Non-blocking put
            self._buffer.put_nowait(line)
        except queue.Full:
            # Buffer lleno, forzar flush sincrónico
            self._flush_to_disk()
            try:
                self._buffer.put_nowait(line)
            except queue.Full:
                # Si sigue lleno, descartar (mejor que bloquear)
                pass

        # Verificar si necesita flush inmediato (error crítico)
        if level in ("ERROR", "CRITICAL"):
            self._flush_to_disk()

    def debug(self, message: str, source: str = "MCP") -> None:
        """Log nivel DEBUG."""
        self.log("DEBUG", message, source)

    def info(self, message: str, source: str = "MCP") -> None:
        """Log nivel INFO."""
        self.log("INFO", message, source)

    def warning(self, message: str, source: str = "MCP") -> None:
        """Log nivel WARNING."""
        self.log("WARNING", message, source)

    def error(self, message: str, source: str = "MCP") -> None:
        """Log nivel ERROR (con flush inmediato)."""
        self.log("ERROR", message, source)

    def flush(self) -> None:
        """Fuerza flush sincrónico del buffer a disco."""
        self._flush_to_disk()

    def close(self) -> None:
        """Cierra logger, hace flush final y detiene thread."""
        self._stop_event.set()
        self._flush_to_disk()
        if self._flush_thread and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=2.0)

    def get_log_path(self) -> str:
        """Retorna path absoluto del archivo de log."""
        return str(self.log_file.absolute())


def get_tplex_logger() -> TplexLogger:
    """Obtiene instancia singleton del logger TPlex.

    Returns:
        TplexLogger configurado para .tplex/logs/tpx_mcp_session.log
    """
    global _tplex_logger

    if _tplex_logger is None:
        with _tplex_logger_lock:
            if _tplex_logger is None:
                _tplex_logger = TplexLogger()

    return _tplex_logger


def _ensure_tplex_logs() -> None:
    """Asegura que exista la estructura .tplex/logs/.

    Llama internamente a get_tplex_logger() que crea carpetas.
    """
    logger = get_tplex_logger()
    logger.info("TPlex logging system initialized", "SYSTEM")


def log_to_tplex(level: str, message: str, source: str = "MCP") -> None:
    """Función helper para loggear a .tplex/logs/.

    Args:
        level: DEBUG, INFO, WARNING, ERROR
        message: Mensaje
        source: Origen (default: MCP)
    """
    logger = get_tplex_logger()
    logger.log(level, message, source)


def log_autopsy(autopsy_data: dict, test_id: str = "unknown") -> None:
    """Loggear datos de autopsia a archivo.

    Args:
        autopsy_data: Dict con autopsia capturada
        test_id: Identificador del test
    """
    logger = get_tplex_logger()

    try:
        import json
        autopsy_json = json.dumps(autopsy_data, cls=AutopsyJSONEncoder, indent=2)
        logger.log("AUTOPSY", f"Test {test_id}:\n{autopsy_json}", "AUTOPSY")
        logger.flush()  # Asegurar que autopsia se persiste
    except Exception as e:
        logger.error(f"Error loggeando autopsia: {e}", "AUTOPSY")


def log_health_check(report: HealthCheckReport) -> None:
    """Loggear reporte de health check.

    Args:
        report: HealthCheckReport con resultados
    """
    logger = get_tplex_logger()

    data = report.to_dict()
    status = "PASS" if data["passed"] else "FAIL"

    logger.log("HEALTH", f"Health Check {status}: {data['summary']}", "HEALTH")

    for name, check in data["checks"].items():
        status_icon = "✓" if check["passed"] else "✗"
        logger.log("HEALTH", f"  {status_icon} {name}: {check['message']}", "HEALTH")

    logger.flush()


def log_schema_sync(details: dict) -> None:
    """Loggear resultado de Schema Sync Guard.

    Args:
        details: Dict con detalles de sincronización
    """
    logger = get_tplex_logger()

    if details.get("synced"):
        logger.info(
            f"Schema Sync: DB alineada con head [{details.get('head_version', 'N/A')[:8]}...]",
            "SSG"
        )
    elif not details.get("alembic_detected"):
        logger.info("Schema Sync: Alembic no detectado, skip", "SSG")
    else:
        logger.error(
            f"Schema Sync DESALINEADA: DB=[{details.get('db_version', 'N/A')[:8]}...] "
            f"Head=[{details.get('head_version', 'N/A')[:8]}...]",
            "SSG"
        )
        logger.flush()


# Función para registrar cierre limpio al finalizar
import atexit

def _cleanup_tplex_logger() -> None:
    """Cleanup function para cerrar logger al salir."""
    global _tplex_logger
    if _tplex_logger:
        _tplex_logger.info("TPlex logging system shutting down", "SYSTEM")
        _tplex_logger.close()
        _tplex_logger = None

atexit.register(_cleanup_tplex_logger)
