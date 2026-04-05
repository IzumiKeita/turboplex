from __future__ import annotations

import inspect
import os
import pathlib
import time
import traceback
import unittest
from typing import Any

from .base import BaseAdapter
from ..environment import _load_module


class UnittestAdapter(BaseAdapter):
    def discover(self, paths: list[str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        def looks_like_test_file(p: pathlib.Path) -> bool:
            name = p.name
            if not name.endswith(".py"):
                return False
            if name.startswith("test_"):
                return True
            if name.endswith("_test.py"):
                return True
            return False

        def iter_files(path_str: str) -> list[pathlib.Path]:
            p = pathlib.Path(path_str)
            if p.is_file():
                return [p]
            if p.is_dir():
                out: list[pathlib.Path] = []
                for root, _, files in os.walk(p):
                    for fn in files:
                        fp = pathlib.Path(root) / fn
                        if looks_like_test_file(fp):
                            out.append(fp)
                return out
            return []

        for p in paths:
            for file_path in iter_files(p):
                if not looks_like_test_file(file_path):
                    continue
                try:
                    mod = _load_module(file_path.resolve())
                except Exception:
                    continue

                for name, obj in vars(mod).items():
                    if not inspect.isclass(obj):
                        continue
                    try:
                        if not issubclass(obj, unittest.TestCase):
                            continue
                    except Exception:
                        continue
                    if obj is unittest.TestCase:
                        continue

                    for meth_name, meth in vars(obj).items():
                        if not callable(meth):
                            continue
                        if not meth_name.startswith("test"):
                            continue
                        lineno = 0
                        try:
                            _, lineno = inspect.getsourcelines(meth)
                        except Exception:
                            pass
                        items.append(
                            {
                                "path": str(file_path),
                                "qualname": f"{obj.__name__}::{meth_name}",
                                "lineno": int(lineno or 0),
                                "kind": "unittest",
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

            if "::" not in qualname:
                return {"passed": False, "duration_ms": 0, "error": f"invalid qualname: {qualname}"}

            class_name, method_name = qualname.split("::", 1)
            mod = _load_module(p.resolve())
            cls = getattr(mod, class_name, None)
            if cls is None or not inspect.isclass(cls):
                return {"passed": False, "duration_ms": 0, "error": f"class not found: {class_name}"}
            if not issubclass(cls, unittest.TestCase):
                return {"passed": False, "duration_ms": 0, "error": f"class is not TestCase: {class_name}"}
            if not hasattr(cls, method_name):
                return {"passed": False, "duration_ms": 0, "error": f"method not found: {qualname}"}

            test = cls(methodName=method_name)
            result = unittest.TestResult()
            test.run(result)
            dt = int((time.perf_counter() - t0) * 1000)

            if result.skipped:
                _t, reason = result.skipped[0]
                return {
                    "passed": True,
                    "duration_ms": dt,
                    "error": None,
                    "skipped": True,
                    "skip_reason": str(reason),
                }

            if result.errors:
                _t, tb = result.errors[0]
                return {"passed": False, "duration_ms": dt, "error": str(tb)[:20000]}
            if result.failures:
                _t, tb = result.failures[0]
                return {"passed": False, "duration_ms": dt, "error": str(tb)[:20000]}

            return {"passed": result.wasSuccessful(), "duration_ms": dt, "error": None}
        except BaseException as e:
            dt = int((time.perf_counter() - t0) * 1000)
            logger.error(f"unittest adapter crashed: {e}", "ADAPTER")
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            return {"passed": False, "duration_ms": dt, "error": tb[:20000]}
        finally:
            end_test_transaction()
