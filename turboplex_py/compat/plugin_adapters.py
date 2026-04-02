"""Plugin Adapters - Adapta plugins populares de pytest a TurboPlex.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Optional, Coroutine
from functools import wraps


class AsyncioPluginAdapter:
    """Adapta pytest-asyncio para funcionar con TurboPlex."""
    
    def __init__(self):
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
    
    def adapt_async_test(self, test_func: Callable) -> Callable:
        """Adapta una función async para ejecutarse con asyncio."""
        
        if not inspect.iscoroutinefunction(test_func):
            # No es async, devolver original
            return test_func
        
        @wraps(test_func)
        def wrapper(*args, **kwargs):
            # Obtener o crear event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Ejecutar función async
            return loop.run_until_complete(test_func(*args, **kwargs))
        
        return wrapper
    
    def adapt_async_fixture(self, fixture_func: Callable) -> Callable:
        """Adapta un fixture async para funcionar con TurboPlex."""
        
        if not inspect.isasyncgenfunction(fixture_func):
            # No es async generator, devolver original
            return fixture_func
        
        @wraps(fixture_func)
        def wrapper(*args, **kwargs):
            # Obtener o crear event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Crear async generator
            agen = fixture_func(*args, **kwargs)
            
            # Avanzar al primer yield (setup)
            async def advance():
                try:
                    return await agen.asend(None)
                except StopAsyncIteration:
                    return None
            
            value = loop.run_until_complete(advance())
            
            # Retornar valor y guardar generator para cleanup
            return value
        
        return wrapper


class AnyioPluginAdapter:
    """Adapta pytest-anyio para funcionar con TurboPlex."""
    
    def __init__(self):
        self._backend = "asyncio"  # Default backend
    
    def adapt_test(self, test_func: Callable, backend: str = "asyncio") -> Callable:
        """Adapta una función para anyio."""
        
        if not inspect.iscoroutinefunction(test_func):
            return test_func
        
        @wraps(test_func)
        def wrapper(*args, **kwargs):
            # Usar anyio si está disponible
            try:
                import anyio
                return anyio.run(
                    lambda: test_func(*args, **kwargs),
                    backend=backend
                )
            except ImportError:
                # Fallback a asyncio
                return AsyncioPluginAdapter().adapt_async_test(test_func)(*args, **kwargs)
        
        return wrapper


class PluginManager:
    """Gestiona plugins de pytest adaptados."""
    
    SUPPORTED_PLUGINS = {
        "pytest-asyncio": AsyncioPluginAdapter,
        "pytest-anyio": AnyioPluginAdapter,
    }
    
    def __init__(self):
        self._adapters: dict[str, Any] = {}
        self._detected_plugins: set[str] = set()
    
    def detect_plugins(self) -> list[str]:
        """Detecta qué plugins de pytest están instalados."""
        detected = []
        
        for plugin_name in self.SUPPORTED_PLUGINS:
            try:
                # Intentar importar el plugin
                __import__(plugin_name.replace("-", "_"))
                detected.append(plugin_name)
                self._detected_plugins.add(plugin_name)
            except ImportError:
                pass
        
        return detected
    
    def get_adapter(self, plugin_name: str) -> Optional[Any]:
        """Obtiene el adaptador para un plugin."""
        if plugin_name not in self._adapters:
            adapter_class = self.SUPPORTED_PLUGINS.get(plugin_name)
            if adapter_class:
                self._adapters[plugin_name] = adapter_class()
        
        return self._adapters.get(plugin_name)
    
    def adapt_test(self, test_func: Callable, plugin_name: Optional[str] = None) -> Callable:
        """Adapta un test según los plugins detectados."""
        
        adapted = test_func
        
        # Si se especifica un plugin específico
        if plugin_name:
            adapter = self.get_adapter(plugin_name)
            if adapter:
                if hasattr(adapter, 'adapt_async_test'):
                    adapted = adapter.adapt_async_test(adapted)
                elif hasattr(adapter, 'adapt_test'):
                    adapted = adapter.adapt_test(adapted)
            return adapted
        
        # Auto-detectar y aplicar todos los plugins relevantes
        for detected_plugin in self._detected_plugins:
            adapter = self.get_adapter(detected_plugin)
            if adapter:
                if inspect.iscoroutinefunction(adapted) and hasattr(adapter, 'adapt_async_test'):
                    adapted = adapter.adapt_async_test(adapted)
                elif hasattr(adapter, 'adapt_test'):
                    adapted = adapter.adapt_test(adapted)
        
        return adapted


# Instancia global del plugin manager
_plugin_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """Obtiene la instancia global del plugin manager."""
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager()
        _plugin_manager.detect_plugins()
    return _plugin_manager
