from __future__ import annotations

import contextlib
import io
import os
import pathlib
import time
import traceback
from typing import Any

from .base import BaseAdapter


class BehaveAdapter(BaseAdapter):
    def discover(self, paths: list[str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        def iter_features(path_str: str) -> list[pathlib.Path]:
            p = pathlib.Path(path_str)
            if p.is_file() and p.name.endswith(".feature"):
                return [p]
            if p.is_dir():
                out: list[pathlib.Path] = []
                for root, _, files in os.walk(p):
                    for fn in files:
                        if fn.endswith(".feature"):
                            out.append(pathlib.Path(root) / fn)
                return out
            return []

        for p in paths:
            for fp in iter_features(p):
                items.append(
                    {
                        "path": str(fp),
                        "qualname": fp.name,
                        "lineno": 0,
                        "kind": "behave",
                    }
                )

        return items

    def execute(self, path: str, qualname: str) -> dict[str, Any]:
        from turboplex_py.mcp.transactional import begin_test_transaction, end_test_transaction
        from turboplex_py.mcp.utils import get_tplex_logger

        logger = get_tplex_logger()
        begin_test_transaction()
        t0 = time.perf_counter()
        try:
            p = pathlib.Path(path)
            if not p.is_file():
                return {"passed": False, "duration_ms": 0, "error": f"not a file: {path}"}
            if p.suffix != ".feature":
                return {"passed": False, "duration_ms": 0, "error": f"not a .feature file: {path}"}

            out = io.StringIO()
            err = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    import importlib

                    m = importlib.import_module("behave.__main__")
                    main_fn = getattr(m, "main", None)
                    if not callable(main_fn):
                        return {"passed": False, "duration_ms": 0, "error": "behave main() not found"}
                    rc = main_fn([str(p)])
                except SystemExit as e:
                    rc = int(getattr(e, "code", 1) or 0)

            dt = int((time.perf_counter() - t0) * 1000)
            passed = int(rc or 0) == 0
            if passed:
                return {"passed": True, "duration_ms": dt, "error": None}

            combined = (err.getvalue() or "") + "\n" + (out.getvalue() or "")
            combined = combined.strip()
            if not combined:
                combined = f"behave failed with exit code {rc}"
            return {"passed": False, "duration_ms": dt, "error": combined[:20000]}
        except ImportError as e:
            dt = int((time.perf_counter() - t0) * 1000)
            return {"passed": False, "duration_ms": dt, "error": f"behave not installed: {e}"}
        except BaseException as e:
            dt = int((time.perf_counter() - t0) * 1000)
            logger.error(f"behave adapter crashed: {e}", "ADAPTER")
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            return {"passed": False, "duration_ms": dt, "error": tb[:20000]}
        finally:
            end_test_transaction()
