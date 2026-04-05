"""TPX Inyector - Transactional Testing Nativo para TurboPlex v0.3.6+

Este módulo implementa un sys.meta_path interceptor que detecta automáticamente
cuando el código del usuario importa SQLAlchemy y envuelve create_engine para
forzar transacciones con SAVEPOINT y ROLLBACK al final de cada test.

Totalmente invisible para el desarrollador - no requiere fixtures especiales.
"""

import sys
import functools
import importlib.abc
import importlib.machinery
import threading
from types import ModuleType
from typing import Any, Callable, Optional

# Thread-local para rastrear estado de transacción por test
_transaction_state = threading.local()


class _TransactionalState:
    """Estado de transacción para un test en ejecución."""

    def __init__(self):
        self.connection_txs: dict[int, "_ConnectionTx"] = {}


class _ConnectionTx:
    def __init__(self, conn: Any, outer: Any | None, nested: Any | None):
        self.conn = conn
        self.outer = outer
        self.nested = nested


class _SQLAlchemyLoaderWrapper(importlib.abc.Loader):
    def __init__(self, fullname: str, loader: importlib.abc.Loader):
        self._fullname = fullname
        self._loader = loader

    def create_module(self, spec):
        create = getattr(self._loader, "create_module", None)
        if callable(create):
            return create(spec)
        return None

    def exec_module(self, module: ModuleType) -> None:
        self._loader.exec_module(module)
        if self._fullname == "sqlalchemy":
            _sqlalchemy_hook.wrap_sqlalchemy(module)


class _SQLAlchemyMetaPathFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Optional[list[str]] = None, target: Optional[ModuleType] = None):
        if fullname != "sqlalchemy":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None or spec.loader is None:
            return spec
        if isinstance(spec.loader, importlib.abc.Loader):
            spec.loader = _SQLAlchemyLoaderWrapper(fullname, spec.loader)
        return spec


class _SQLAlchemyImportHook:
    """Hook que se ejecuta después de importar sqlalchemy.

    Envuelve create_engine para forzar modo transaccional.
    """

    def __init__(self):
        self._wrapped = False
        self._original_sa_create_engine: Callable[..., Any] | None = None

    def wrap_sqlalchemy(self, sqlalchemy_module: ModuleType) -> None:
        """Envuelve create_engine en el módulo sqlalchemy."""
        if self._wrapped:
            return

        if not hasattr(sqlalchemy_module, 'create_engine'):
            return

        original_create_engine = sqlalchemy_module.create_engine
        self._original_sa_create_engine = original_create_engine

        @functools.wraps(original_create_engine)
        def transactional_create_engine(*args, **kwargs):
            """Versión transaccional de create_engine.

            Configura el engine para usar SAVEPOINTS y transacciones anidadas.
            """
            if "isolation_level" not in kwargs:
                kwargs["isolation_level"] = "SERIALIZABLE"
            engine = original_create_engine(*args, **kwargs)
            return _wrap_engine_for_transactions(engine)

        sqlalchemy_module.create_engine = transactional_create_engine

        try:
            from sqlalchemy.engine import Connection as _SAConnection

            if not hasattr(_SAConnection, "_tpx_original_commit"):
                _SAConnection._tpx_original_commit = _SAConnection.commit

                @functools.wraps(_SAConnection.commit)
                def _tpx_commit(self, *args, **kwargs):
                    state = _get_transaction_state()
                    if state and _is_test_transaction_active() and id(self) in state.connection_txs:
                        return _commit_savepoint_and_rearm(self)
                    return _SAConnection._tpx_original_commit(self, *args, **kwargs)

                _SAConnection.commit = _tpx_commit

            if not hasattr(_SAConnection, "_tpx_original_rollback"):
                _SAConnection._tpx_original_rollback = _SAConnection.rollback

                @functools.wraps(_SAConnection.rollback)
                def _tpx_rollback(self, *args, **kwargs):
                    state = _get_transaction_state()
                    if state and _is_test_transaction_active() and id(self) in state.connection_txs:
                        return _rollback_savepoint_and_rearm(self)
                    return _SAConnection._tpx_original_rollback(self, *args, **kwargs)

                _SAConnection.rollback = _tpx_rollback
        except Exception:
            pass

        self._wrapped = True


def _wrap_engine_for_transactions(engine: Any) -> Any:
    """Envuelve un engine SQLAlchemy para forzar transacciones.

    Args:
        engine: Engine SQLAlchemy original

    Returns:
        Engine envuelto con comportamiento transaccional
    """
    original_connect = engine.connect
    original_begin = getattr(engine, 'begin', None)

    @functools.wraps(original_connect)
    def transactional_connect(*args, **kwargs):
        """Crea conexión con transacción automática."""
        conn = original_connect(*args, **kwargs)
        conn = _wrap_connection_for_transactions(conn)
        if _is_test_transaction_active():
            _activate_connection_for_test(conn)
        return conn

    engine.connect = transactional_connect

    # También envolver begin si existe
    if original_begin:
        @functools.wraps(original_begin)
        def transactional_begin(*args, **kwargs):
            """Inicia transacción con SAVEPOINT."""
            return original_begin(*args, **kwargs)

        engine.begin = transactional_begin

    return engine


def _wrap_connection_for_transactions(conn: Any) -> Any:
    """Envuelve una conexión para rastrear transacciones.

    Args:
        conn: Conexión SQLAlchemy original

    Returns:
        Conexión envuelta con rastreo de transacciones
    """
    original_begin = conn.begin
    original_commit = conn.commit
    original_rollback = conn.rollback
    original_close = conn.close

    @functools.wraps(original_begin)
    def tracked_begin(*args, **kwargs):
        """Inicia transacción y la rastrea."""
        trans = original_begin(*args, **kwargs)
        _track_transaction(trans)
        return trans

    @functools.wraps(original_commit)
    def tracked_commit(*args, **kwargs):
        if _is_test_transaction_active():
            return _commit_savepoint_and_rearm(conn)
        return original_commit(*args, **kwargs)

    @functools.wraps(original_rollback)
    def tracked_rollback(*args, **kwargs):
        if _is_test_transaction_active():
            return _rollback_savepoint_and_rearm(conn)
        return original_rollback(*args, **kwargs)

    @functools.wraps(original_close)
    def tracked_close(*args, **kwargs):
        """Cierra conexión con rollback si hay transacción pendiente."""
        _rollback_connection_and_forget(conn)
        return original_close(*args, **kwargs)

    conn.begin = tracked_begin
    conn.commit = tracked_commit
    conn.rollback = tracked_rollback
    conn.close = tracked_close

    return conn


def _track_transaction(trans: Any) -> None:
    state = _get_transaction_state()
    if state is None:
        return
    return


def _get_transaction_state() -> Optional[_TransactionalState]:
    """Obtiene el estado de transacción del thread actual."""
    if not hasattr(_transaction_state, 'state'):
        _transaction_state.state = _TransactionalState()
    return _transaction_state.state


def _is_test_transaction_active() -> bool:
    return hasattr(_transaction_state, "state")


def _activate_connection_for_test(conn: Any) -> None:
    state = _get_transaction_state()
    if state is None:
        return
    key = id(conn)
    if key in state.connection_txs:
        return
    outer = None
    nested = None
    try:
        outer = conn.begin()
        begin_nested = getattr(conn, "begin_nested", None)
        if callable(begin_nested):
            nested = begin_nested()
    except Exception:
        nested = None
    state.connection_txs[key] = _ConnectionTx(conn, outer, nested)


def _commit_savepoint_and_rearm(conn: Any) -> None:
    state = _get_transaction_state()
    if state is None:
        return None
    tx = state.connection_txs.get(id(conn))
    if tx is None:
        return None
    current_nested = None
    get_nested = getattr(conn, "get_nested_transaction", None)
    if callable(get_nested):
        try:
            current_nested = get_nested()
        except Exception:
            current_nested = None
    if current_nested is not None and tx.nested is not None and current_nested is not tx.nested:
        try:
            current_nested.commit()
        except Exception:
            pass
        return None
    if tx.nested is not None:
        try:
            tx.nested.commit()
        except Exception:
            pass
    begin_nested = getattr(conn, "begin_nested", None)
    if callable(begin_nested):
        try:
            tx.nested = begin_nested()
        except Exception:
            tx.nested = None
    return None


def _rollback_savepoint_and_rearm(conn: Any) -> None:
    state = _get_transaction_state()
    if state is None:
        return None
    tx = state.connection_txs.get(id(conn))
    if tx is None:
        return None
    current_nested = None
    get_nested = getattr(conn, "get_nested_transaction", None)
    if callable(get_nested):
        try:
            current_nested = get_nested()
        except Exception:
            current_nested = None
    if current_nested is not None and tx.nested is not None and current_nested is not tx.nested:
        try:
            current_nested.rollback()
        except Exception:
            pass
        return None
    if tx.nested is not None:
        try:
            tx.nested.rollback()
        except Exception:
            pass
    begin_nested = getattr(conn, "begin_nested", None)
    if callable(begin_nested):
        try:
            tx.nested = begin_nested()
        except Exception:
            tx.nested = None
    return None


def _rollback_connection_and_forget(conn: Any) -> None:
    state = _get_transaction_state()
    if state is None:
        return
    key = id(conn)
    tx = state.connection_txs.pop(key, None)
    if tx is None:
        return
    _rollback_connection_tx(tx)


def _rollback_connection_tx(tx: _ConnectionTx) -> None:
    try:
        if tx.nested is not None and hasattr(tx.nested, "rollback"):
            tx.nested.rollback()
    except Exception:
        pass
    try:
        if tx.outer is not None and hasattr(tx.outer, "rollback"):
            tx.outer.rollback()
    except Exception:
        pass
    try:
        if hasattr(tx.conn, "close"):
            tx.conn.close()
    except Exception:
        pass


def _rollback_all_transactions() -> None:
    """Hace rollback de todas las transacciones pendientes."""
    state = _get_transaction_state()
    if state:
        items = list(state.connection_txs.values())
        state.connection_txs.clear()
        for tx in reversed(items):
            _rollback_connection_tx(tx)


def _clear_transaction_state() -> None:
    """Limpia el estado de transacción del thread actual."""
    if hasattr(_transaction_state, 'state'):
        _rollback_all_transactions()
        delattr(_transaction_state, 'state')


# Singleton del hook
_sqlalchemy_hook = _SQLAlchemyImportHook()
_meta_finder: _SQLAlchemyMetaPathFinder | None = None


def install_transactional_interceptor() -> None:
    """Instala el interceptor transaccional para SQLAlchemy.

    Esta función debe llamarse antes de que los tests importen SQLAlchemy.
    Una vez instalado, todas las llamadas a create_engine serán envueltas
    automáticamente para forzar transacciones con SAVEPOINT.

    Ejemplo:
        >>> from turboplex_py.mcp.transactional import install_transactional_interceptor
        >>> install_transactional_interceptor()
        >>> # Ahora los imports de sqlalchemy serán interceptados
    """
    global _meta_finder
    if _meta_finder is None:
        _meta_finder = _SQLAlchemyMetaPathFinder()
    if _meta_finder not in sys.meta_path:
        sys.meta_path.insert(0, _meta_finder)

    # Si sqlalchemy ya está importado, envolverlo ahora
    if 'sqlalchemy' in sys.modules:
        _sqlalchemy_hook.wrap_sqlalchemy(sys.modules['sqlalchemy'])


def uninstall_transactional_interceptor() -> None:
    """Desinstala el interceptor transaccional."""
    global _meta_finder
    if _meta_finder and _meta_finder in sys.meta_path:
        sys.meta_path.remove(_meta_finder)


def begin_test_transaction() -> None:
    """Inicia una transacción de test.

    Llama esta función al inicio de cada test para preparar el estado.
    """
    _clear_transaction_state()
    _transaction_state.state = _TransactionalState()


def end_test_transaction() -> None:
    """Finaliza una transacción de test con rollback.

    Llama esta función al final de cada test para limpiar.
    """
    _rollback_all_transactions()
    _clear_transaction_state()


class TransactionalTestContext:
    """Context manager para transacciones de test.

    Uso:
        with TransactionalTestContext():
            # Cualquier operación DB aquí se rollback al salir
            db.add(user)
            db.commit()  # No persiste realmente
    """

    def __enter__(self):
        begin_test_transaction()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        end_test_transaction()
        return False  # No suprimir excepciones


def patch_sqlalchemy_if_imported() -> bool:
    """Si sqlalchemy ya está importado, lo parchea inmediatamente.

    Returns:
        True si se aplicó el parche, False si no
    """
    if 'sqlalchemy' in sys.modules:
        _sqlalchemy_hook.wrap_sqlalchemy(sys.modules['sqlalchemy'])
        return True
    return False


# Auto-instalar si se importa este módulo
install_transactional_interceptor()
