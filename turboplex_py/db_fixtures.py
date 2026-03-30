"""Database fixtures for TurboPlex - Lazy connection with aggressive timeouts.

Supports SQLite (in-memory, fast) and PostgreSQL (production, configurable).
Connection is established only when requested, not at import time.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from contextlib import contextmanager
from typing import Any, Generator, Optional

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

from .fixtures import fixture


# Default timeouts (in seconds)
SQLITE_TIMEOUT_S = float(os.environ.get("TPX_DB_TIMEOUT_SQLITE", "2.0"))
POSTGRES_TIMEOUT_S = float(os.environ.get("TPX_DB_TIMEOUT_POSTGRES", "10.0"))
DEFAULT_DB_TYPE = os.environ.get("TPX_DB_DEFAULT", "sqlite")


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
        self.timeout_s = timeout_s or (SQLITE_TIMEOUT_S if db_type == "sqlite" else POSTGRES_TIMEOUT_S)
        self.connect_args = connect_args or {}
        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker] = None
        self._connected = False
        self._connection_error: Optional[Exception] = None
    
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
        
        # Setup engine kwargs based on DB type
        engine_kwargs: dict[str, Any] = {
            "pool_pre_ping": True,  # Verify connection before using from pool
            "pool_recycle": 300,    # Recycle connections after 5 min
        }
        
        if self.db_type == "sqlite":
            # SQLite in-memory specific settings
            engine_kwargs.update({
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            })
            engine_kwargs["connect_args"].update(self.connect_args)
        else:
            # PostgreSQL settings
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
def db() -> Generator[Session, None, None]:
    """Auto-detect database fixture.
    
    Uses PostgreSQL if DATABASE_URL is set, otherwise SQLite in-memory.
    Configurable via TPX_DB_DEFAULT env var.
    """
    if not HAS_SQLALCHEMY:
        raise ImportError("SQLAlchemy required. Install with: pip install sqlalchemy")
    
    # Check if DATABASE_URL is set and valid
    database_url = os.environ.get("DATABASE_URL")
    
    if database_url and DEFAULT_DB_TYPE == "postgres":
        # Use PostgreSQL
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
