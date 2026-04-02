"""Database environment setup, type detection, bootstrap, and module loading."""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import sys
from types import ModuleType

_MODULE_CACHE: dict[str, tuple[int, ModuleType]] = {}


def _load_module(path: pathlib.Path):
    p = path.resolve()
    try:
        mtime_ns = p.stat().st_mtime_ns
    except Exception:
        mtime_ns = 0

    key = str(p)
    cached = _MODULE_CACHE.get(key)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]

    mod_name = f"turbopy_run_{p.stem}"
    if mod_name in sys.modules:
        try:
            del sys.modules[mod_name]
        except Exception:
            pass

    spec = importlib.util.spec_from_file_location(mod_name, p)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MODULE_CACHE[key] = (mtime_ns, mod)
    return mod


def _setup_database_env():
    """Configura variables de entorno para conexión DB desde config o env existentes.
    
    Detecta automáticamente el tipo de base de datos desde DATABASE_URL y aplica
    defaults específicos por dialecto (PostgreSQL, MySQL/MariaDB, etc.).
    """
    # Check de seguridad: si DATABASE_URL no apunta a DB de test, forzar override
    test_db_pattern = os.environ.get('TEST_DB_PATTERN', '.*test.*')
    if 'DATABASE_URL' in os.environ:
        current_url = os.environ['DATABASE_URL']
        # Extraer el nombre de la base de datos de la URL (agnóstico al esquema)
        # Soporta: postgresql://user:pass@host/dbname, mysql://user:pass@host:port/dbname, etc.
        db_name_match = re.search(r'/([^/?]+)(?:\?|$)', current_url)
        db_name = db_name_match.group(1) if db_name_match else current_url
        
        # Si el nombre de la DB no coincide con el patrón de test y existe _TEST_DATABASE_URL, forzar override
        if not re.search(test_db_pattern, db_name, re.IGNORECASE):
            if '_TEST_DATABASE_URL' in os.environ:
                os.environ['DATABASE_URL'] = os.environ['_TEST_DATABASE_URL']
        return

    # Detectar tipo de DB desde DATABASE_URL si existe, o usar default
    db_url = os.environ.get('DATABASE_URL', '')
    db_type = _detect_db_type(db_url) if db_url else 'postgresql'

    # Defaults específicos por tipo de base de datos
    defaults_by_type = {
        'mysql': {
            'DB_HOST': 'localhost',
            'DB_PORT': '3306',
            'DB_USER': 'root',
            'DB_PASSWORD': '',
            'DB_NAME': 'test_db'
        },
        'mariadb': {
            'DB_HOST': 'localhost',
            'DB_PORT': '3306',
            'DB_USER': 'root',
            'DB_PASSWORD': '',
            'DB_NAME': 'test_db'
        },
        'postgresql': {
            'DB_HOST': 'localhost',
            'DB_PORT': '5432',
            'DB_USER': 'postgres',
            'DB_PASSWORD': '',
            'DB_NAME': 'test_db'
        },
        'mssql': {
            'DB_HOST': 'localhost',
            'DB_PORT': '1433',
            'DB_USER': 'sa',
            'DB_PASSWORD': '',
            'DB_NAME': 'test_db'
        },
        'oracle': {
            'DB_HOST': 'localhost',
            'DB_PORT': '1521',
            'DB_USER': 'system',
            'DB_PASSWORD': '',
            'DB_NAME': 'TESTDB'
        },
        'sqlite': {
            'DB_HOST': '',
            'DB_PORT': '',
            'DB_USER': '',
            'DB_PASSWORD': '',
            'DB_NAME': ':memory:'
        }
    }

    # Usar defaults del tipo detectado o postgresql como fallback
    defaults = defaults_by_type.get(db_type, defaults_by_type['postgresql'])

    for key, default_val in defaults.items():
        if key not in os.environ:
            os.environ[key] = default_val


def _detect_db_type(database_url: str) -> str:
    """Detecta el tipo de base de datos desde la URL de conexión.
    
    Args:
        database_url: URL de conexión SQLAlchemy
        
    Returns:
        Tipo de base de datos: 'mysql', 'mariadb', 'postgresql', 'mssql', 'oracle', 'sqlite'
    """
    if not database_url:
        return 'postgresql'
    
    url_lower = database_url.lower()
    
    if url_lower.startswith('mysql'):
        return 'mysql'
    elif url_lower.startswith('mariadb'):
        return 'mariadb'
    elif url_lower.startswith('postgresql') or url_lower.startswith('postgres'):
        return 'postgresql'
    elif url_lower.startswith('mssql') or url_lower.startswith('sqlserver'):
        return 'mssql'
    elif url_lower.startswith('oracle'):
        return 'oracle'
    elif url_lower.startswith('sqlite'):
        return 'sqlite'
    else:
        # Fallback a postgresql si no se puede detectar
        return 'postgresql'


def _preload_pos_retail_models():
    """Pre-carga modelos de pos_retail si están disponibles."""
    try:
        import pos_retail.models
        # Forzar import de modelos comunes para que estén en memoria
        from pos_retail.models import User, Empresa, Cliente
    except ImportError:
        pass  # pos_retail no disponible, continuar sin pre-carga


def _bootstrap_test_environment():
    """Bootstrap completo: DB env + pre-import de modelos."""
    if os.environ.get("TPX_RUNNER_LIGHT", "").strip() in ("1", "true", "yes", "on"):
        return
    _setup_database_env()
    _preload_pos_retail_models()
    # Auto-cargar conftest.py desde tests/ si existe
    try:
        tests_dir = os.path.join(os.getcwd(), "tests")
        conftest_path = os.path.join(tests_dir, "conftest.py")
        if os.path.isfile(conftest_path):
            if tests_dir not in sys.path:
                sys.path.insert(0, tests_dir)
            import conftest as _tpx_conftest  # noqa: F401
    except Exception:
        pass
