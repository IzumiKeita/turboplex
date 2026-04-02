"""TurboPlex Pytest Compatibility - Bootstrap para lazy loading.

Este módulo debe ejecutarse ANTES de cualquier import de SQLAlchemy
para interceptar todas las operaciones de DB desde el inicio.
"""

from __future__ import annotations

import sys
import logging

logger = logging.getLogger(__name__)


def install_sqlalchemy_patcher():
    """Instala el patcher de SQLAlchemy lo más temprano posible."""
    try:
        # Importar y activar el patcher
        from ..db.lazy_patcher import get_patcher
        patcher = get_patcher()
        patcher.patch_all()
        logger.info("SQLAlchemy patcher instalado en bootstrap")
        return True
    except Exception as e:
        logger.warning(f"No se pudo instalar SQLAlchemy patcher: {e}")
        return False


def install_all_patchers():
    """Instala todos los patchers necesarios para compatibilidad pytest."""
    results = {
        'sqlalchemy': install_sqlalchemy_patcher(),
    }
    return results


# Auto-instalar al importar este módulo
_patchers_installed = False

def ensure_patchers():
    """Asegura que los patchers estén instalados (llamar al inicio del proceso)."""
    global _patchers_installed
    if not _patchers_installed:
        install_all_patchers()
        _patchers_installed = True
