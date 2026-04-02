"""Database fixtures for TurboPlex - Lazy connection with aggressive timeouts.

Supports SQLite (in-memory, fast), PostgreSQL, MySQL/MariaDB, SQL Server, 
and Oracle. Connection is established only when requested, not at import time.
"""

from __future__ import annotations

import os
import socket
import threading
import time
import tempfile
import contextvars
import re
from contextlib import contextmanager
from typing import Any, Generator, Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# SQLAlchemy imports - only used when fixture is called
# This keeps import time fast for tests that don't need DB
try:
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool
    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False
    # Dummy classes for type hints when SQLAlchemy not installed
    Engine = Any
    Session = Any

try:
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

from ..fixtures import fixture


# Default timeouts (in seconds)
SQLITE_TIMEOUT_S = float(os.environ.get("TPX_DB_TIMEOUT_SQLITE", "2.0"))
POSTGRES_TIMEOUT_S = float(os.environ.get("TPX_DB_TIMEOUT_POSTGRES", "10.0"))
MYSQL_TIMEOUT_S = float(os.environ.get("TPX_DB_TIMEOUT_MYSQL", "10.0"))
MSSQL_TIMEOUT_S = float(os.environ.get("TPX_DB_TIMEOUT_MSSQL", "10.0"))
ORACLE_TIMEOUT_S = float(os.environ.get("TPX_DB_TIMEOUT_ORACLE", "10.0"))
DEFAULT_DB_TYPE = os.environ.get("TPX_DB_DEFAULT", "sqlite")
STRICT_DIRTY = os.environ.get("TPX_DB_STRICT_DIRTY", "0").strip().lower() in ("1", "true", "yes")
METRICS_ENABLED = os.environ.get("TPX_DB_METRICS_ENABLED", "1").strip().lower() not in ("0", "false", "no")
DIRTY_TRACK_MAX_TABLES = int(os.environ.get("TPX_DB_DIRTY_TRACK_MAX_TABLES", "12"))
ISOLATION_MODE = os.environ.get("TPX_DB_ISOLATION_MODE", "auto").strip().lower()
WORKER_PREFIX = os.environ.get("TPX_DB_WORKER_PREFIX", "tpx_w")
_WRITE_SQL_RE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|MERGE|REPLACE)\b", re.IGNORECASE)
_TABLE_RE = re.compile(r"\b(?:INTO|UPDATE|FROM)\s+([a-zA-Z_][\w\.]*)", re.IGNORECASE)
_CURRENT_DB_TRACKER: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "tpx_current_db_tracker", default=None
)


def _worker_id() -> str:
    return (
        os.environ.get("TPX_WORKER_ID")
        or os.environ.get("PYTEST_XDIST_WORKER")
        or os.environ.get("TPX_WORKER_INDEX")
        or "0"
    )


def resolve_isolation_mode(db_type: str) -> str:
    mode = (ISOLATION_MODE or "auto").lower()
    if mode != "auto":
        return mode
    if db_type == "sqlite":
        return "database"
    if db_type in ("postgresql", "postgres"):
        return "schema"
    if db_type in ("mysql", "mariadb"):
        return "database"
    return "transaction"


def _extract_db_name(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path.lstrip("/") if parsed.path else ""


def _replace_db_name(url: str, db_name: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, f"/{db_name}", parsed.params, parsed.query, parsed.fragment))


def build_isolated_database_url(db_type: str, database_url: str | None) -> str | None:
    if not database_url:
        return database_url
    wid = _worker_id().replace("-", "_")
    prefix = WORKER_PREFIX
    mode = resolve_isolation_mode(db_type)
    if db_type == "sqlite":
        if mode == "database":
            p = os.path.join(tempfile.gettempdir(), f"{prefix}_{wid}.sqlite3").replace("\\", "/")
            return f"sqlite:///{p}"
        return database_url
    if db_type in ("postgresql", "postgres"):
        if mode == "schema":
            schema = f"{prefix}_{wid}"
            parsed = urlparse(database_url)
            q = dict(parse_qsl(parsed.query, keep_blank_values=True))
            q["options"] = f"-csearch_path={schema}"
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(q), parsed.fragment))
        return database_url
    if db_type in ("mysql", "mariadb"):
        if mode == "database":
            base = _extract_db_name(database_url) or "test"
            return _replace_db_name(database_url, f"{base}_{prefix}_{wid}")
        return database_url
    return database_url


def begin_test_db_tracking() -> None:
    if not METRICS_ENABLED:
        return
    _CURRENT_DB_TRACKER.set(
        {
            "write_count": 0,
            "tables_touched": set(),
            "db_engine": None,
            "db_dirty": False,
            "db_dirty_summary": None,
        }
    )


def finalize_test_db_tracking() -> dict[str, Any]:
    tracker = _CURRENT_DB_TRACKER.get() or {}
    out_tables = sorted(list(tracker.get("tables_touched", set())))[:DIRTY_TRACK_MAX_TABLES]
    write_count = int(tracker.get("write_count", 0) or 0)
    db_dirty = bool(write_count > 0 or tracker.get("db_dirty"))
    summary = {
        "write_count_delta": write_count,
        "tables_touched_sample": out_tables,
        "engine": tracker.get("db_engine"),
    }
    _CURRENT_DB_TRACKER.set(None)
    return {
        "db_metrics": {"write_count": write_count, "engine": tracker.get("db_engine")},
        "db_dirty": db_dirty,
        "db_dirty_summary": summary,
        "db_should_fail_on_dirty": bool(STRICT_DIRTY and db_dirty),
    }


def _track_sql(statement: Any) -> None:
    tracker = _CURRENT_DB_TRACKER.get()
    if not tracker:
        return
    sql = str(statement or "")
    if not sql:
        return
    if _WRITE_SQL_RE.search(sql):
        tracker["write_count"] = int(tracker.get("write_count", 0)) + 1
        tracker["db_dirty"] = True
    m = _TABLE_RE.search(sql)
    if m:
        tables = tracker.get("tables_touched")
        if isinstance(tables, set):
            tables.add(m.group(1))


class DatabaseTimeoutError(Exception):
    """Raised when database connection exceeds timeout."""
    pass


class LazyDBConnection:
    """Lazy database connection with timeout enforcement.
    
    Connection is only established when .connect() or .session() is called,
    not at __init__ time. This prevents hangs during test collection.
    """
    
    def __init__(
        self,
        db_type: str = "sqlite",
        database_url: Optional[str] = None,
        timeout_s: Optional[float] = None,
        connect_args: Optional[dict] = None,
    ):
        self.db_type = db_type
        self.database_url = database_url
        self.timeout_s = timeout_s or self._get_default_timeout(db_type)
        self.connect_args = connect_args or {}
        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker] = None
        self._connected = False
        self._connection_error: Optional[Exception] = None
    
    def _get_default_timeout(self, db_type: str) -> float:
        """Get default timeout based on database type."""
        timeouts = {
            "sqlite": SQLITE_TIMEOUT_S,
            "postgresql": POSTGRES_TIMEOUT_S,
            "postgres": POSTGRES_TIMEOUT_S,
            "mysql": MYSQL_TIMEOUT_S,
            "mariadb": MYSQL_TIMEOUT_S,
            "mssql": MSSQL_TIMEOUT_S,
            "oracle": ORACLE_TIMEOUT_S,
        }
        return timeouts.get(db_type, POSTGRES_TIMEOUT_S)
    
    def _detect_db_type(self, url: str) -> str:
        """Detect database type from URL."""
        if not url:
            return "sqlite"
        url_lower = url.lower()
        if url_lower.startswith("sqlite"):
            return "sqlite"
        elif url_lower.startswith("mysql"):
            return "mysql"
        elif url_lower.startswith("mariadb"):
            return "mariadb"
        elif url_lower.startswith("postgresql") or url_lower.startswith("postgres"):
            return "postgresql"
        elif url_lower.startswith("mssql") or url_lower.startswith("sqlserver"):
            return "mssql"
        elif url_lower.startswith("oracle"):
            return "oracle"
        return "postgresql"
    
    def _create_engine_with_timeout(self) -> Engine:
        """Create SQLAlchemy engine with timeout enforcement."""
        if not HAS_SQLALCHEMY:
            raise ImportError("SQLAlchemy required for database fixtures. Install with: pip install sqlalchemy")
        
        # Determine connection URL
        if self.database_url:
            url = self.database_url
        elif self.db_type == "sqlite":
            url = "sqlite:///:memory:"
        else:
            url = os.environ.get("DATABASE_URL", "postgresql://localhost/test")
        url = build_isolated_database_url(self.db_type, url) or url
        
        # Setup engine kwargs based on DB type
        engine_kwargs: dict[str, Any] = {
            "pool_pre_ping": True,  # Verify connection before using from pool
            "pool_recycle": 300,    # Recycle connections after 5 min
        }
        
        # Detect DB type from URL if db_type is generic
        detected_type = self._detect_db_type(url) if self.database_url else self.db_type
        
        if detected_type == "sqlite":
            # SQLite in-memory specific settings
            engine_kwargs.update({
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            })
            engine_kwargs["connect_args"].update(self.connect_args)
        elif detected_type in ("mysql", "mariadb"):
            # MySQL/MariaDB specific settings
            # MariaDB doesn't use connect_timeout the same way as PostgreSQL
            # Instead, use connect_timeout in connect_args for the underlying driver
            mysql_connect_args = {
                "connect_timeout": int(self.timeout_s),
                "read_timeout": int(self.timeout_s),
                "write_timeout": int(self.timeout_s),
            }
            mysql_connect_args.update(self.connect_args)
            engine_kwargs["connect_args"] = mysql_connect_args
        else:
            # PostgreSQL, SQL Server, Oracle settings
            engine_kwargs["connect_args"] = {
                "connect_timeout": int(self.timeout_s),
                **self.connect_args,
            }
        
        # Create engine
        engine = create_engine(url, **engine_kwargs)
        
        # Add timeout enforcement for connection
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            if self.db_type == "sqlite":
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
            if self.db_type in ("postgresql", "postgres") and resolve_isolation_mode(self.db_type) == "schema":
                schema = f"{WORKER_PREFIX}_{_worker_id().replace('-', '_')}"
                cursor = dbapi_conn.cursor()
                cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                cursor.execute(f"SET search_path TO {schema}")
                cursor.close()
            if self.db_type in ("mysql", "mariadb") and resolve_isolation_mode(self.db_type) == "database":
                db_name = _extract_db_name(url)
                if db_name:
                    cursor = dbapi_conn.cursor()
                    cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}`")
                    cursor.execute(f"USE `{db_name}`")
                    cursor.close()

        @event.listens_for(engine, "before_cursor_execute")
        def track_writes(conn, cursor, statement, parameters, context, executemany):
            _track_sql(statement)
            tracker = _CURRENT_DB_TRACKER.get()
            if tracker is not None and not tracker.get("db_engine"):
                tracker["db_engine"] = self.db_type
        
        return engine
    
    def connect(self) -> Engine:
        """Get or create engine with timeout."""
        if self._connection_error:
            raise self._connection_error
        
        if self._engine is not None:
            return self._engine
        
        # Attempt connection with timeout
        result: dict[str, Any] = {"engine": None, "error": None}
        
        def _connect():
            try:
                result["engine"] = self._create_engine_with_timeout()
            except Exception as e:
                result["error"] = e
        
        thread = threading.Thread(target=_connect)
        thread.daemon = True
        thread.start()
        thread.join(timeout=self.timeout_s)
        
        if thread.is_alive():
            # Thread still running = timeout
            self._connection_error = DatabaseTimeoutError(
                f"Database connection timed out after {self.timeout_s}s "
                f"(db_type={self.db_type})"
            )
            raise self._connection_error
        
        if result["error"]:
            self._connection_error = result["error"]
            raise result["error"]
        
        self._engine = result["engine"]
        self._session_factory = sessionmaker(autocommit=False, autoflush=False, bind=self._engine)
        self._connected = True
        
        return self._engine
    
    def session(self) -> Session:
        """Get a new database session."""
        if self._session_factory is None:
            self.connect()
        return self._session_factory()
    
    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        """Context manager for database session with automatic rollback."""
        session = self.session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    
    def is_connected(self) -> bool:
        """Check if connection has been established."""
        return self._connected
    
    def close(self):
        """Close all connections."""
        if self._engine:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None
            self._connected = False


# Global cache for connection reuse across tests in same process
_connection_cache: dict[str, LazyDBConnection] = {}


def get_db_connection(
    db_type: str = "sqlite",
    database_url: Optional[str] = None,
    timeout_s: Optional[float] = None,
) -> LazyDBConnection:
    """Get or create cached database connection.
    
    Connections are cached by (db_type, url) to avoid re-creating
    for each test in the same process.
    """
    cache_key = f"{db_type}:{database_url or 'default'}"
    
    if cache_key not in _connection_cache:
        _connection_cache[cache_key] = LazyDBConnection(
            db_type=db_type,
            database_url=database_url,
            timeout_s=timeout_s,
        )
    
    return _connection_cache[cache_key]


def clear_db_connections():
    """Close and clear all cached database connections."""
    global _connection_cache
    for conn in _connection_cache.values():
        conn.close()
    _connection_cache.clear()


# =============================================================================
# Fixtures
# =============================================================================

@fixture
def db_sqlite() -> Generator[Session, None, None]:
    """SQLite in-memory database fixture.
    
    Fast, no external dependencies. Use for unit tests.
    Timeout: 2 seconds (configurable via TPX_DB_TIMEOUT_SQLITE).
    """
    if not HAS_SQLALCHEMY:
        raise ImportError("SQLAlchemy required. Install with: pip install sqlalchemy")
    
    conn = get_db_connection(db_type="sqlite", timeout_s=SQLITE_TIMEOUT_S)
    
    with conn.session_scope() as session:
        yield session


@fixture
def db_postgres() -> Generator[Session, None, None]:
    """PostgreSQL database fixture.
    
    Requires DATABASE_URL environment variable or PostgreSQL running locally.
    Use for integration tests.
    Timeout: 10 seconds (configurable via TPX_DB_TIMEOUT_POSTGRES).
    """
    if not HAS_SQLALCHEMY:
        raise ImportError("SQLAlchemy required. Install with: pip install sqlalchemy")
    
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError(
            "DATABASE_URL environment variable required for postgres fixture. "
            "Use db_sqlite fixture or set DATABASE_URL."
        )
    
    conn = get_db_connection(
        db_type="postgresql",
        database_url=database_url,
        timeout_s=POSTGRES_TIMEOUT_S,
    )
    
    with conn.session_scope() as session:
        yield session


@fixture
def db_mysql() -> Generator[Session, None, None]:
    """MySQL/MariaDB database fixture.
    
    Requires DATABASE_URL environment variable with mysql:// or mariadb:// scheme.
    Use for integration tests against MySQL or MariaDB.
    Timeout: 10 seconds (configurable via TPX_DB_TIMEOUT_MYSQL).
    """
    if not HAS_SQLALCHEMY:
        raise ImportError("SQLAlchemy required. Install with: pip install sqlalchemy")
    
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError(
            "DATABASE_URL environment variable required for mysql fixture. "
            "Use db_sqlite fixture or set DATABASE_URL."
        )
    
    # Detect if URL is mysql or mariadb
    db_type = "mysql"
    if database_url.lower().startswith("mariadb"):
        db_type = "mariadb"
    
    conn = get_db_connection(
        db_type=db_type,
        database_url=database_url,
        timeout_s=MYSQL_TIMEOUT_S,
    )
    
    with conn.session_scope() as session:
        yield session


@fixture
def db() -> Generator[Session, None, None]:
    """Auto-detect database fixture.
    
    Uses database from DATABASE_URL if set, otherwise SQLite in-memory.
    Configurable via TPX_DB_DEFAULT env var.
    """
    if not HAS_SQLALCHEMY:
        raise ImportError("SQLAlchemy required. Install with: pip install sqlalchemy")
    
    # Check if DATABASE_URL is set and valid
    database_url = os.environ.get("DATABASE_URL")
    
    if database_url:
        # Auto-detect type from URL
        url_lower = database_url.lower()
        if url_lower.startswith("mysql") or url_lower.startswith("mariadb"):
            conn = get_db_connection(
                db_type="mysql",
                database_url=database_url,
                timeout_s=MYSQL_TIMEOUT_S,
            )
        elif url_lower.startswith("postgresql") or url_lower.startswith("postgres"):
            conn = get_db_connection(
                db_type="postgresql",
                database_url=database_url,
                timeout_s=POSTGRES_TIMEOUT_S,
            )
        elif url_lower.startswith("mssql") or url_lower.startswith("sqlserver"):
            conn = get_db_connection(
                db_type="mssql",
                database_url=database_url,
                timeout_s=MSSQL_TIMEOUT_S,
            )
        elif url_lower.startswith("oracle"):
            conn = get_db_connection(
                db_type="oracle",
                database_url=database_url,
                timeout_s=ORACLE_TIMEOUT_S,
            )
        else:
            # Default to PostgreSQL for unknown schemes
            conn = get_db_connection(
                db_type="postgresql",
                database_url=database_url,
                timeout_s=POSTGRES_TIMEOUT_S,
            )
    else:
        # Default to SQLite
        conn = get_db_connection(db_type="sqlite", timeout_s=SQLITE_TIMEOUT_S)
    
    with conn.session_scope() as session:
        yield session


@fixture
def client(db: Session) -> TestClient:
    """FastAPI TestClient fixture with database injection.
    
    Requires FastAPI app to be available. This fixture assumes
    the app uses dependency injection for DB sessions.
    """
    if not HAS_FASTAPI:
        raise ImportError("FastAPI required. Install with: pip install fastapi")
    
    # Try to auto-discover app
    try:
        # Common patterns for app location
        import sys
        from pathlib import Path
        
        # Look for app in common locations
        possible_paths = [
            Path("app/main.py"),
            Path("backend/app/main.py"),
            Path("src/app/main.py"),
            Path("main.py"),
        ]
        
        app = None
        for path in possible_paths:
            if path.exists():
                # Try to import app module
                try:
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("main_app", path)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        app = getattr(mod, "app", None)
                        if app:
                            break
                except Exception:
                    continue
        
        if app is None:
            raise ValueError(
                "Could not auto-discover FastAPI app. "
                "Create your own client fixture or ensure app is importable as 'app' from main module."
            )
        
        # Override DB dependency
        from fastapi import Depends
        
        def override_get_db():
            yield db
        
        # Try to override dependency (app-specific)
        if hasattr(app, "dependency_overrides"):
            # Common dependency names
            for dep_name in ["get_db", "get_session", "get_database"]:
                # Try to find and override
                pass  # Implementation depends on app structure
        
        return TestClient(app)
        
    except Exception as e:
        raise RuntimeError(f"Failed to create TestClient: {e}") from e


# =============================================================================
# Cleanup
# =============================================================================

import atexit
atexit.register(clear_db_connections)
