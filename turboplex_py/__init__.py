"""Public API for tests: ``from turboplex_py import skip, skipif, fixture``.

Also provides database fixtures via ``from turboplex_py.db_fixtures import db, client``.
"""

from __future__ import annotations

from .fixtures import fixture
from .markers import skip, skipif

__all__ = ["fixture", "skip", "skipif", "__version__"]
__version__ = "0.3.0"
