# Briefing: Módulos de Alta Fidelidad v0.3.6+

## 📋 RESUMEN DE IMPLEMENTACIÓN

Se han implementado 2 módulos de alta fidelidad en `turboplex_py/mcp/utils.py`:

| Módulo | Función Principal | Archivo |
|--------|-------------------|---------|
| **Pre-Flight Health Check** | Abortar ejecución temprano si dependencias críticas fallan | `utils.py:107-344` |
| **Autopsia Automática** | Capturar estado de variables locales en excepciones | `utils.py:347-544` |

---

## 🔬 MÓDULO 1: PRE-FLIGHT HEALTH CHECK

### APIs Exportadas

```python
from turboplex_py.mcp import (
    HealthCheckError,           # Excepción específica
    HealthCheckReport,          # Contenedor de reporte
    check_postgres_connectivity,  # Check PostgreSQL vía socket
    check_env_file,             # Validar .env
    check_dependency_versions,  # SQLAlchemy 2.0+, pytest 7.0+
    run_health_checks,          # Ejecutar todos
    preflight_guard,            # Guard que lanza si falla
    preflight_guard_decorator,  # Decorador
)
```

### Integración en Flujo MCP

#### Opción A: Guard explícito antes de subprocess (RECOMENDADO)

En `mcp/server.py`, antes de llamar a `subprocess.run()` o `Popen`:

```python
from turboplex_py.mcp import preflight_guard, HealthCheckError, payload_error, ToolError
from turboplex_py.mcp.utils import uuid_v7
import time

def discover(paths: list[str] | None = None, compat: bool = False) -> str:
    t0 = time.perf_counter()
    run_id = uuid_v7()
    
    # PRE-FLIGHT GUARD: Abortar temprano si infraestructura no está lista
    try:
        health_report = preflight_guard(
            check_postgres=True,   # Verificar PG via socket (3s timeout)
            check_env=True,        # Verificar .env existe y legible
            check_deps=True,       # SQLAlchemy>=2.0, pytest>=7.0
            env_path=".env"
        )
    except HealthCheckError as e:
        # Retornar payload_error estructurado con info del health check
        duration_ms = int((time.perf_counter() - t0) * 1000)
        return payload_error(
            tool="discover",
            run_id=run_id,
            mode=" HealthCheckFailed",
            duration_ms=duration_ms,
            error=ToolError(
                code="INFRASTRUCTURE_NOT_READY",
                message=str(e),
                details={
                    "health_check": "failed",
                    "hint": "Configure .env, PostgreSQL, o actualice dependencias"
                }
            )
        )
    
    # Si pasa el guard, continuar con operación normal...
    # ... resto del código
```

#### Opción B: Decorador en funciones MCP

```python
from turboplex_py.mcp import preflight_guard_decorator, payload_error, ToolError

@preflight_guard_decorator(check_postgres=True, check_env=True, check_deps=True)
def discover(paths=None, compat=False):
    # Si falla health check, nunca se ejecuta esta función
    # El decorador lanza HealthCheckError antes
    ...

# Manejo del error en capa superior:
try:
    result = discover(paths, compat)
except HealthCheckError as e:
    return payload_error(...)
```

### Checks Implementados

| Check | Descripción | Fallo Típico |
|-------|-------------|--------------|
| `postgres_connectivity` | Socket connect a PGHOST:PGPORT | PG no iniciado, puerto bloqueado |
| `env_file` | Existencia, legibilidad, variables críticas | .env no existe, permisos |
| `dependency_versions` | SQLAlchemy>=2.0.0, pytest>=7.0.0 | Versión obsoleta, no instalado |

### Formato de Reporte

```python
report = run_health_checks()
print(report.to_dict())
# {
#   "passed": False,
#   "checks": {
#     "postgres_connectivity": {
#       "passed": False,
#       "message": "No se puede conectar a PostgreSQL en localhost:5432 (error 10061)",
#       "details": {}
#     },
#     "env_file": {
#       "passed": True,
#       "message": ".env legible con 12 variables (3 críticas)",
#       "details": {"variables": 12, "critical_vars_found": [...]}
#     },
#     "dependency_versions": {
#       "passed": True,
#       "message": "Todas las dependencias cumplen versiones mínimas",
#       "details": {"sqlalchemy": {"version": "2.0.30", "passed": True}}
#     }
#   },
#   "summary": "2/3 checks passed"
# }
```

---

## 🧬 MÓDULO 2: AUTOPSIA AUTOMÁTICA

### APIs Exportadas

```python
from turboplex_py.mcp import (
    capture_autopsy,        # Capturar estado de excepción
    autopsy_from_dict,    # Añadir autopsy a resultado existente
    AutopsyJSONEncoder,   # Encoder JSON seguro
    _scrub_value,         # Función de limpieza de objetos
)
```

### Estrategia de Scrubbing (Limpieza de Objetos)

El scrubbing evita que objetos no serializables rompan el JSON:

| Tipo de Objeto | Estrategia | Ejemplo |
|----------------|------------|---------|
| Primitivos | Pasan directo | `int`, `str`, `bool` |
| Collections | Recursivo + límite 50 items | `list`, `dict` |
| Excepciones | Extraer type, message, args | `ValueError("...")` |
| Objetos con `__dict__` | Extraer 20 atributos no-callable | `SomeClass(...)` |
| DB Sessions/Files/etc | `<Session> (non-serializable resource)` | SQLAlchemy Session |
| Callables | `<function name>` | `def foo():` |
| Otros | `repr()` limitado a 200 chars | Cualquier otro objeto |

### Integración en Test Runner

En `mcp/collect.py`, modificar `pytest_run` para capturar autopsy:

```python
from turboplex_py.mcp import capture_autopsy, autopsy_from_dict
import traceback

def pytest_run(nodeid):
    """Run a single test using pytest with autopsy on failure."""
    t0 = time.perf_counter()
    python_exe = resolve_python_executable()
    base_cmd = [python_exe, "-m", "pytest", "-q", nodeid]
    cmd = _build_pytest_cmd(base_cmd)
    
    cfg = load_mcp_config()
    timeout_s = cfg.pytest_run_timeout_s
    
    try:
        rc, stdout, stderr = _run_pytest_with_diagnostics(cmd, "pytest_run", timeout_s)
    except ToolSubprocessError as e:
        # Capturar autopsy del error
        dt = int((time.perf_counter() - t0) * 1000)
        result = {
            "passed": False,
            "duration_ms": dt,
            "error": str(e.stderr)[:1000]
        }
        # Añadir autopsy con traceback actual
        try:
            exc = e.__cause__ or e
            result = autopsy_from_dict(result, exc)
        except Exception:
            pass  # Autopsy es best-effort
        return result
    except Exception as e:
        # Capturar cualquier otra excepción con autopsy
        dt = int((time.perf_counter() - t0) * 1000)
        result = {
            "passed": False,
            "duration_ms": dt,
            "error": str(e)[:1000]
        }
        result = autopsy_from_dict(result, e)
        return result
    
    dt = int((time.perf_counter() - t0) * 1000)
    passed = rc == 0
    err = None
    if not passed:
        err = (stderr or "").strip() or (stdout or "").strip() or "pytest failed"
        err += f"\n[Python: {python_exe}]"
    
    result = {"passed": passed, "duration_ms": dt, "error": err}
    
    # Si falló, intentar capturar autopsy del stderr si contiene traceback
    if not passed and "Traceback" in (stderr or ""):
        # Crear excepción sintética para capturar info disponible
        class SyntheticError(Exception):
            pass
        try:
            syn_exc = SyntheticError(err)
            # El traceback real está en stderr, no tenemos el objeto traceback
            # pero podemos añadir el stderr al autopsy manualmente
            result["autopsy"] = {
                "exception_type": "pytest_failure",
                "exception_message": err[:1000],
                "stderr_analysis": stderr[:2000] if stderr else None,
                "note": "Autopsy parcial: traceback en stderr"
            }
        except Exception:
            pass
    
    return result
```

### Formato de Autopsy en JSON

```json
{
  "passed": false,
  "duration_ms": 1234,
  "error": "AssertionError: expected 5 but got 3",
  "autopsy": {
    "exception_type": "AssertionError",
    "exception_message": "expected 5 but got 3",
    "frames": [
      {
        "filename": "/tests/test_api.py",
        "function": "test_user_count",
        "lineno": 42,
        "locals": {
          "expected": 5,
          "actual": 3,
          "db_session": "<Session> (non-serializable resource)",
          "users": [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
            {"id": 3, "name": "Charlie"}
          ]
        }
      },
      {
        "filename": "/app/services.py",
        "function": "get_user_count",
        "lineno": 15,
        "locals": {
          "query": "<Query> (non-serializable resource)",
          "filters": {"active": true}
        }
      }
    ]
  }
}
```

### Uso con JSON Encoder Personalizado

```python
import json
from turboplex_py.mcp import AutopsyJSONEncoder

# Usar encoder que automáticamente scrubbea objetos no serializables
result_with_autopsy = capture_autopsy(some_exception)
json_str = json.dumps(result_with_autopsy, cls=AutopsyJSONEncoder)
```

---

## 📊 DIAGRAMA DE INTEGRACIÓN

```
┌─────────────────────────────────────────────────────────────┐
│                    MCP Server Entry                         │
│  (mcp/server.py discover/run)                              │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│           PRE-FLIGHT HEALTH CHECK (Opcional)                │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐  │
│  │ PostgreSQL      │ │ .env File       │ │ Dependencies    │  │
│  │ Socket Check    │ │ Existence       │ │ Versions        │  │
│  └────────┬────────┘ └────────┬────────┘ └────────┬────────┘  │
│           │                     │                     │         │
│           └─────────────────────┼─────────────────────┘         │
│                                 ▼                                 │
│                    ┌─────────────────────┐                     │
│                    │   HealthCheckError  │ ──► payload_error   │
│                    │   (si falla)        │     estructurado    │
│                    └─────────────────────┘                     │
└─────────────────────────────────────────────────────────────┘
                     │
                     ▼ (si pasa)
┌─────────────────────────────────────────────────────────────┐
│                   Subprocess Execution                       │
│              (pytest / turboplex runner)                     │
└────────────────────┬────────────────────────────────────────┘
                     │
         ┌───────────┴───────────┐
         ▼                       ▼
┌────────────────┐    ┌─────────────────────────────┐
│   SUCCESS      │    │   FAILURE (Exception)       │
│                │    │                             │
│  passed: true  │    │  ┌─────────────────────┐   │
│                │    │  │  AUTOPSIA CAPTURE   │   │
│                │    │  │  - exception_type   │   │
│                │    │  │  - frames[]         │   │
│                │    │  │  - locals (scrubbed)│   │
│                │    │  └─────────────────────┘   │
│                │    │           │                │
│                │    │           ▼                │
│                │    │  ┌─────────────────────┐   │
│                │    │  │   Scrubbing         │   │
│                │    │  │   (safe JSON)       │   │
│                │    │  └─────────────────────┘   │
│                │    │           │                │
│                │    │           ▼                │
│                │    │  autopsy field in result   │
└────────────────┘    └─────────────────────────────┘
```

---

## 🎯 EJEMPLO COMPLETO: Función run() con ambos módulos

```python
from turboplex_py.mcp import (
    payload_ok, payload_error, ToolError,
    preflight_guard, HealthCheckError,
    autopsy_from_dict, uuid_v7
)
import time
import traceback

def run(selection: list[dict], compat: bool = False) -> str:
    t0 = time.perf_counter()
    run_id = uuid_v7()
    
    # 1. PRE-FLIGHT GUARD
    try:
        preflight_guard(check_postgres=True, check_env=True, check_deps=True)
    except HealthCheckError as e:
        return payload_error(
            tool="run",
            run_id=run_id,
            mode="error",
            duration_ms=int((time.perf_counter() - t0) * 1000),
            error=ToolError(
                code="HEALTH_CHECK_FAILED",
                message=str(e),
                details={"phase": "pre-flight"}
            )
        )
    
    # 2. EJECUCIÓN
    results = []
    for item in selection:
        try:
            result = execute_test(item)  # Tu función de ejecución
            results.append(result)
        except Exception as e:
            # 3. AUTOPSIA EN FALLA
            failed_result = {
                "passed": False,
                "error": str(e),
                "item": item
            }
            failed_result = autopsy_from_dict(failed_result, e)
            results.append(failed_result)
    
    return payload_ok(
        tool="run",
        run_id=run_id,
        mode="compat" if compat else "normal",
        summary={"total": len(results), "passed": sum(1 for r in results if r.get("passed"))},
        data={"results": results}
    )
```

---

## ✅ CHECKLIST DE IMPLEMENTACIÓN

- [x] `HealthCheckError` - Excepción específica
- [x] `HealthCheckReport` - Contenedor de reportes
- [x] `check_postgres_connectivity()` - Check vía socket
- [x] `check_env_file()` - Validación .env
- [x] `check_dependency_versions()` - Versiones mínimas
- [x] `run_health_checks()` - Orquestador
- [x] `preflight_guard()` - Guard con raise
- [x] `preflight_guard_decorator()` - Decorador
- [x] `_scrub_value()` - Limpieza recursiva de objetos
- [x] `capture_autopsy()` - Captura de frames
- [x] `autopsy_from_dict()` - Integración en resultados
- [x] `AutopsyJSONEncoder` - Encoder JSON seguro
- [x] Exports en `__init__.py`

---

## 🚀 PRÓXIMOS PASOS SUGERIDOS

1. **Integrar en `mcp/server.py`**: Añadir `preflight_guard()` al inicio de `discover()` y `run()`
2. **Integrar en `mcp/collect.py`**: Añadir `autopsy_from_dict()` en el manejo de excepciones
3. **Tests**: Crear tests unitarios para health checks y scrubbing
4. **Documentación**: Añadir ejemplos al README del proyecto

---

**Implementado por:** Cascade AI  
**Versión:** v0.3.6+  
**Fecha:** 2026-04-04
