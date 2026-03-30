"""SQLAlchemy Lazy Patcher - Enfoque Conservador

Intercepta SOLO operaciones DDL pesadas (metadata.create_all, metadata.drop_all)
Mantiene engine y conexiones reales para compatibilidad con eventos SQLAlchemy.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from functools import wraps

logger = logging.getLogger(__name__)

# Lista de operaciones DDL pendientes
_pending_ddl: List[Tuple[str, Any, ...]] = []
_original_functions: Dict[str, Callable] = {}
_patched = False



def _is_ddl_statement(statement) -> bool:
    """Detecta si una sentencia es DDL (CREATE, DROP, ALTER, etc.)."""
    if statement is None:
        return False
    
    # Convertir a string
    sql_str = str(statement)
    
    # Patrones DDL
    ddl_patterns = [
        r'^\s*DROP\s+',
        r'^\s*CREATE\s+',
        r'^\s*ALTER\s+',
        r'^\s*TRUNCATE\s+',
        r'^\s*RENAME\s+',
        r'^\s*COMMENT\s+',
        r'^\s*GRANT\s+',
        r'^\s*REVOKE\s+',
        r'^\s*ANALYZE\s+',
        r'^\s*VACUUM\s+',
        r'^\s*REINDEX\s+',
        r'^\s*CLUSTER\s+',
        r'^\s*REFRESH\s+',
    ]
    
    sql_upper = sql_str.upper()
    for pattern in ddl_patterns:
        if re.search(pattern, sql_upper, re.IGNORECASE):
            return True
    
    return False


def _lazy_metadata_create_all(self, bind=None, tables=None, checkfirst=True, **kwargs):
    """Intercepta metadata.create_all() - operación pesada."""
    logger.debug(f"DDL: metadata.create_all() aplazado")
    _pending_ddl.append(('create_all', self, bind, tables, checkfirst, kwargs))


def _lazy_metadata_drop_all(self, bind=None, tables=None, checkfirst=True, **kwargs):
    """Intercepta metadata.drop_all()."""
    logger.debug(f"DDL: metadata.drop_all() aplazado")
    _pending_ddl.append(('drop_all', self, bind, tables, checkfirst, kwargs))


class SQLAlchemyLazyPatcher:
    """Patcher conservador - solo DDL, engine real."""
    
    def __init__(self):
        self._patched = False
    
    def patch_all(self):
        """Aplica parches solo a metadata operations."""
        global _patched, _original_functions
        
        if self._patched:
            return
        
        try:
            from sqlalchemy import schema as sa_schema
            
            # Guardar originales
            _original_functions['metadata_create_all'] = sa_schema.MetaData.create_all
            _original_functions['metadata_drop_all'] = sa_schema.MetaData.drop_all
            
            # Aplicar parches solo a DDL
            sa_schema.MetaData.create_all = _lazy_metadata_create_all
            sa_schema.MetaData.drop_all = _lazy_metadata_drop_all
            
            self._patched = True
            logger.info("SQLAlchemy Patcher activado (modo conservador)")
            
        except ImportError:
            logger.warning("SQLAlchemy no disponible")
    
    def unpatch_all(self):
        """Restaura funciones originales."""
        global _patched
        
        if not self._patched:
            return
        
        try:
            from sqlalchemy import schema as sa_schema
            
            if 'metadata_create_all' in _original_functions:
                sa_schema.MetaData.create_all = _original_functions['metadata_create_all']
            if 'metadata_drop_all' in _original_functions:
                sa_schema.MetaData.drop_all = _original_functions['metadata_drop_all']
            
            _original_functions.clear()
            _patched = False
            logger.info("SQLAlchemy Patcher desactivado")
            
        except ImportError:
            pass
    
    def flush_ddl(self, bind=None):
        """Ejecuta DDL pendiente."""
        global _pending_ddl
        
        if not _pending_ddl:
            return
        
        logger.info(f"Ejecutando {len(_pending_ddl)} operaciones DDL")
        
        for op in _pending_ddl:
            op_type = op[0]
            try:
                if op_type == 'create_all':
                    _, metadata, op_bind, tables, checkfirst, kwargs = op
                    real_bind = bind or op_bind
                    if real_bind and 'metadata_create_all' in _original_functions:
                        original = _original_functions['metadata_create_all']
                        original(metadata, bind=real_bind, tables=tables, 
                                checkfirst=checkfirst, **kwargs)
                        
                elif op_type == 'drop_all':
                    _, metadata, op_bind, tables, checkfirst, kwargs = op
                    real_bind = bind or op_bind
                    if real_bind and 'metadata_drop_all' in _original_functions:
                        original = _original_functions['metadata_drop_all']
                        original(metadata, bind=real_bind, tables=tables,
                                checkfirst=checkfirst, **kwargs)
                        
            except Exception as e:
                logger.error(f"Error en DDL {op_type}: {e}")
                raise
        
        _pending_ddl.clear()
    
    def get_pending_count(self) -> int:
        return len(_pending_ddl)
    
    def is_patched(self) -> bool:
        return self._patched


# Instancia global
_patcher_instance: Optional[SQLAlchemyLazyPatcher] = None


def get_patcher() -> SQLAlchemyLazyPatcher:
    global _patcher_instance
    if _patcher_instance is None:
        _patcher_instance = SQLAlchemyLazyPatcher()
    return _patcher_instance
