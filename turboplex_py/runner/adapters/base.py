from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseAdapter(ABC):
    @abstractmethod
    def discover(self, paths: list[str]) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def execute(self, path: str, qualname: str) -> dict[str, Any]:
        raise NotImplementedError
