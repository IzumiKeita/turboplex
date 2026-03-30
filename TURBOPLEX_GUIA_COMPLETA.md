# TurboPlex - Guía Completa de Implementación

> **TurboPlex** es un runner de tests híbrido Rust+Python diseñado para ejecutar tests de Pytest con mayor velocidad mediante ejecución paralela, caching inteligente y compatibilidad nativa con fixtures de base de datos.

---

## Tabla de Contenidos

1. [Arquitectura de TurboPlex](#1-arquitectura-de-turboplex)
2. [Componentes Principales](#2-componentes-principales)
3. [Instalación y Setup](#3-instalación-y-setup)
4. [El Problema: Pydantic y la Fase de Collection](#4-el-problema-pydantic-y-la-fase-de-collection)
5. [La Solución: conftest.py Híbrido con Imports Lazy](#5-la-solución-conftestpy-híbrido-con-imports-lazy)
6. [Implementación Paso a Paso para ERP3](#6-implementación-paso-a-paso-para-erp3)
7. [Errores Comunes y Soluciones](#7-errores-comunes-y-soluciones)
8. [Troubleshooting Avanzado](#8-troubleshooting-avanzado)
9. [Mejores Prácticas](#9-mejores-prácticas)
10. [Comparativa: TurboPlex vs Pytest](#10-comparativa-turboplex-vs-pytest)
11. [Roadmap y Futuras Mejoras](#11-roadmap-y-futuras-mejoras)

---

## 1. Arquitectura de TurboPlex

### 1.1 Visión General

TurboPlex opera en tres fases distintas:

```
┌─────────────────────────────────────────────────────────────┐
│                    FASE 1: COLLECTION                        │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │  Rust tpx    │───→│Python Collector│───→│  JSON Cache  │   │
│  │   (main)     │    │ (turboplex_py)│    │              │   │
│  └──────────────┘    └──────────────┘    └──────────────┘   │
│         │                                              │     │
│         │         Descubre tests sin ejecutarlos       │     │
│         │         Usa AST parsing + import lazy          │     │
└─────────┼──────────────────────────────────────────────┼─────┘
          │                                              │
          ▼                                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    FASE 2: EJECUCIÓN                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │  Rust tpx    │───→│  Python Runner │───→│  subprocess  │   │
│  │  (parallel)  │    │ (turboplex_py)│    │  per test    │   │
│  └──────────────┘    └──────────────┘    └──────────────┘   │
│         │                                              │     │
│         │         Ejecuta tests en paralelo            │     │
│         │         Gestiona fixtures por test           │     │
└─────────┼──────────────────────────────────────────────┼─────┘
          │                                              │
          ▼                                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    FASE 3: REPORTING                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │  Resultados  │───→│   Cache de    │───→│  Salida JSON │   │
│  │  Agregados   │    │   resultados  │    │  o terminal  │   │
│  └──────────────┘    └──────────────┘    └──────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 Flujo de Datos

1. **Collection Phase**: El collector Python escanea archivos de test usando AST parsing para encontrar funciones de test sin ejecutar importaciones pesadas.

2. **Caching**: Los tests descubiertos se almacenan en `.turboplex_cache/collected_tests.json` con un hash del contenido de los archivos.

3. **Ejecución Paralela**: Cada test se ejecuta en un proceso Python separado, permitiendo paralelización y aislamiento.

4. **Fixture Management**: TurboPlex gestiona fixtures nativamente, soportando `scope`, `autouse`, y dependencias entre fixtures.

---

## 2. Componentes Principales

### 2.1 Rust Components (`tpx` executable)

| Archivo | Propósito |
|---------|-----------|
| `src/main/mod.rs` | Punto de entrada CLI |
| `src/main/part1.rs` | Utilidades y help |
| `src/main/part2.rs` | `get_or_collect_tests()` - lógica de collection y cache |
| `src/test_runner/python.rs` | `run_python_test()` - ejecución de tests individuales |

### 2.2 Python Components (`turboplex_py` module)

| Archivo | Propósito |
|---------|-----------|
| `turboplex_py/__main__.py` | Entry point para CLI |
| `turboplex_py/collector.py` | Descubrimiento de tests via AST |
| `turboplex_py/pytest_bridge.py` | Puente de fixtures Pytest ↔ TurboPlex |
| `turboplex_py/fixtures.py` | Decorador `@fixture` compatible con pytest |
| `turboplex_py/db_lazy_patcher.py` | Patcher para SQLAlchemy lazy loading |

### 2.3 Variables de Entorno Críticas

```bash
# Modo TurboPlex (activa el conftest híbrido)
export TURBOPLEX_MODE=1
export TURBOTEST_SUBPROCESS=1

# Configuración de Base de Datos
export TEST_DATABASE_URL="postgresql+psycopg2://user:pass@localhost/db"
export DATABASE_URL="${TEST_DATABASE_URL}"

# Python Path
export PYTHONPATH="${PROJECT_ROOT}/backend:${PYTHONPATH}"
```

---

## 3. Instalación y Setup

### 3.1 Instalación de TurboPlex

```bash
# Clonar repositorio
git clone https://github.com/your-org/turboplex.git
cd turboplex

# Compilar release
cargo build --release

# El binario estará en:
# target/release/tpx.exe (Windows)
# target/release/tpx (Linux/Mac)
```

### 3.2 Instalación del módulo Python

```bash
# Activar virtual environment del proyecto
cd your-project
source .venv/bin/activate  # o .venv\Scripts\activate en Windows

# Instalar turboplex_py en modo editable
cd ../turboplex
pip install -e .

# O copiar manualmente:
cp -r turboplex_py your-project/.venv/lib/python3.x/site-packages/
```

### 3.3 Estructura de Archivos Recomendada

```
project-root/
├── backend/
│   ├── tests/
│   │   ├── conftest.py          # ← ARCHIVO CRÍTICO (híbrido)
│   │   ├── test_simple.py       # Tests sin DB
│   │   └── test_with_db.py      # Tests con fixtures DB
│   ├── app/
│   │   ├── core/
│   │   │   ├── database.py
│   │   │   └── config.py        # ← Pydantic Settings
│   │   └── ...
│   └── .env                     # Variables de entorno
├── .turboplex_cache/            # Cache auto-generado
└── .venv/
```

---

## 4. El Problema: Pydantic y la Fase de Collection

### 4.1 El Error Clásico

```
collect: skip import /path/to/test_file.py: 
3 validation errors for Settings
DATABASE_URL
  Field required [type=missing, input_value={}, input_type=dict]
SECRET_KEY
  Field required [type=missing, input_type=dict]
EXTERNAL_API_KEY
  Field required [type=missing, input_type=dict]
```

### 4.2 Por Qué Ocurre

El problema ocurre durante la **fase de collection** cuando TurboPlex intenta descubrir tests:

1. El **collector Python** debe importar los archivos de test para encontrar funciones
2. Los tests importan `conftest.py` automáticamente (comportamiento de pytest)
3. `conftest.py` importa `from app.core.config import settings`
4. **Pydantic valida inmediatamente** y requiere variables de entorno (`DATABASE_URL`, etc.)
5. El collector corre **sin variables de entorno** configuradas
6. **Resultado**: `ValidationError` → Collection falla → 0 tests encontrados

### 4.3 Diagrama del Problema

```
┌─────────────────────────────────────────────────────────────┐
│                    FASE DE COLLECTION                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. tpx llama a turboplex_py collect                        │
│         │                                                    │
│         ▼                                                    │
│  2. collector.py intenta importar test_admin_config.py       │
│         │                                                    │
│         ▼                                                    │
│  3. Python auto-importa conftest.py (pytest behavior)       │
│         │                                                    │
│         ▼                                                    │
│  4. conftest.py: from app.core.config import settings       │
│         │                                                    │
│         ▼                                                    │
│  5. app.core.config ejecuta: Settings()                     │
│         │                                                    │
│         ▼                                                    │
│  6. Pydantic.__init__() valida DATABASE_URL, etc.         │
│         │                                                    │
│         ▼                                                    │
│  7. ❌ VARIABLES NO EXISTEN EN ENV DEL COLLECTOR            │
│         │                                                    │
│         ▼                                                    │
│  8. ❌ ValidationError: Field required                      │
│         │                                                    │
│         ▼                                                    │
│  9. ❌ Collection aborta → 0 tests → tpx no ejecuta nada    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 4.4 Por Qué Pytest No Tiene Este Problema

Pytest ejecuta desde el directorio del proyecto donde:
- `.env` está presente
- `pytest.ini` o `pyproject.toml` configuran el entorno
- El usuario ya exportó las variables necesarias

TurboPlex aísla cada fase para performance, pero esto rompe el contexto de entorno.

---

## 5. La Solución: conftest.py Híbrido con Imports Lazy

### 5.1 Estrategia de Solución

En lugar de importar y ejecutar código pesado al nivel del módulo, usamos **imports lazy** dentro de funciones y fixtures:

```python
# ❌ MAL: Ejecuta al importar el módulo
from app.core.config import settings  # ← Pydantic valida aquí
settings.DATABASE_URL = "..."         # ← Falla si no hay env

# ✅ BIEN: Solo importa cuando se necesita
def get_settings():
    from app.core.config import settings  # ← Lazy import
    return settings
```

### 5.2 Patrón del conftest.py Híbrido

```python
"""
conftest.py Híbrido - Compatible con Pytest y TurboPlex

Este archivo detecta el runtime y carga fixtures apropiados:
- Pytest mode: Carga completa con inicialización eager
- TurboPlex mode: Carga lazy, inicialización en fixtures
"""

import os

# Detectar modo de ejecución
_IS_TURBOPLEX = os.environ.get('TURBOPLEX_MODE') or os.environ.get('TURBOTEST_SUBPROCESS')

# Configuración común (ligera, no requiere DB)
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg2://user:pass@localhost/test_db"
)

if _IS_TURBOPLEX:
    # ============================================================
    # MODO TURBOPLEX - Imports lazy, inicialización en fixtures
    # ============================================================
    
    from turboplex_py.fixtures import fixture
    
    def _get_engine():
        """Lazy initialization del engine."""
        from sqlalchemy import create_engine
        return create_engine(TEST_DATABASE_URL)
    
    @fixture(scope="function")
    def setup_db():
        """Setup de DB para TurboPlex."""
        engine = _get_engine()
        from app.core.database import Base
        from sqlalchemy import text
        
        with engine.connect() as conn:
            # Crear schema si no existe
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS public"))
            Base.metadata.create_all(bind=conn)
        
        yield
    
    @fixture(scope="function")
    def db(setup_db):
        """Sesión de DB para TurboPlex."""
        from sqlalchemy.orm import Session
        from app.core.database import get_db
        # ... lógica de sesión
        yield session

else:
    # ============================================================
    # MODO PYTEST - Imports eager, inicialización al cargar
    # ============================================================
    
    import pytest
    from sqlalchemy import create_engine
    from app.core.config import settings  # ✅ Safe aquí, pytest tiene env
    
    # Inicialización inmediata (funciona con pytest)
    engine = create_engine(TEST_DATABASE_URL)
    
    @pytest.fixture(scope="function")
    def setup_db():
        """Setup de DB para Pytest."""
        with engine.connect() as conn:
            # Limpiar tablas
            pass
        yield
    
    @pytest.fixture(scope="function")
    def db(setup_db):
        """Sesión de DB para Pytest."""
        from sqlalchemy.orm import Session
        # ... lógica de sesión
        yield session
```

### 5.3 Implementación Lazy para Pytest (Solución ERP3)

Para proyectos existentes con mucha lógica de inicialización en Pytest mode, usamos **lazy initialization con variables globales**:

```python
# ============================================================================
# MODO PYTEST CON LAZY INITIALIZATION
# ============================================================================
else:
    print(">>> Inicializando conftest en MODO PYTEST")
    
    # Variables globales para lazy initialization
    _pytest_engine = None
    _pytest_session_local = None
    
    def _get_pytest_engine():
        """Lazy initialization del engine para Pytest."""
        global _pytest_engine, _pytest_session_local
        if _pytest_engine is not None:
            return _pytest_engine, _pytest_session_local
            
        # Imports LAZY - solo cuando se necesita
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker
        from app.core.database import Base
        from app.core.config import settings  # ← Safe aquí, bajo demanda
        
        # Setup completo
        os.environ["DATABASE_URL"] = TEST_DATABASE_URL
        settings.DATABASE_URL = TEST_DATABASE_URL
        
        engine = create_engine(TEST_DATABASE_URL, ...)
        SessionLocal = sessionmaker(bind=engine, ...)
        
        # Crear schema
        with engine.connect() as conn:
            Base.metadata.create_all(bind=conn)
        
        _pytest_engine = engine
        _pytest_session_local = SessionLocal
        return engine, SessionLocal
    
    try:
        # Imports mínimos que no requieren env
        import pytest
        from fastapi.testclient import TestClient
        from sqlalchemy import text, event
        from app.core.database import get_db
        
        # Model imports - no requieren DB connection
        from app.core_modules.sistema import models as sistema_models
        from app.core_modules.billing import models as billing_models
        
    except Exception as e:
        # Fallback si falla importación
        print(f"[ADVERTENCIA] Error cargando imports: {e}")
        
        @pytest.fixture(scope="function")
        def db():
            raise RuntimeError("DB no inicializada")
    
    # Fixtures usan lazy initialization
    @pytest.fixture(scope="function")
    def setup_db():
        engine, _ = _get_pytest_engine()
        # ... usar engine
        yield
    
    @pytest.fixture(scope="function")
    def db(setup_db):
        from sqlalchemy.orm import Session
        from app.core_modules.sistema import models as sistema_models
        
        engine, TestingSessionLocal = _get_pytest_engine()
        
        # Registrar event listeners aquí, no al importar
        @event.listens_for(Session, "after_flush")
        def _auto_company_subscription(session, flush_context):
            # ... lógica
            pass
        
        connection = engine.connect()
        session = TestingSessionLocal(bind=connection)
        # ... resto de lógica
        yield session
```

---

## 6. Implementación Paso a Paso para ERP3

### 6.1 Preparación del Entorno

```bash
# 1. Navegar al proyecto ERP3
cd f:\proyecto python\turboplex\ERP3

# 2. Verificar estructura
ls backend/tests/conftest.py
ls backend/app/core/config.py

# 3. Verificar variables de entorno
cat backend/.env
# Debe contener:
# TEST_DATABASE_URL=postgresql+psycopg2://erp3_user:erp3_pass_2026@localhost:5432/erp3_test
# DATABASE_URL=${TEST_DATABASE_URL}
```

### 6.2 Paso 1: Crear Backup del conftest Original

```bash
cd backend/tests
cp conftest.py conftest.py.backup.pytest
```

### 6.3 Paso 2: Implementar conftest Híbrido

Ver sección 5.3 para el código completo.

Puntos clave:
1. Agregar detección de modo `_IS_TURBOPLEX`
2. Separar modo TurboPlex y modo Pytest
3. Usar lazy initialization en modo Pytest
4. Wrappear imports pesados en try/except

### 6.4 Paso 3: Actualizar turboplex_py en el Venv

```bash
# Copiar conftest al módulo turboplex_py
cp backend/tests/conftest.py .venv/Lib/site-packages/turboplex_py/

# Verificar que el módulo está accesible
python -c "import turboplex_py; print('OK')"
```

### 6.5 Paso 4: Compilar tpx (si hay cambios en Rust)

```bash
cd f:\proyecto python\turboplex
cargo build --release
```

### 6.6 Paso 5: Verificar Collection

```bash
# Desde el directorio backend/
cd backend

# Limpiar cache
rm -rf .turboplex_cache

# Ejecutar collector directamente
python -m turboplex_py collect tests/test_admin_config.py --out-json /tmp/test.json

# Verificar salida
cat /tmp/test.json
# Debe mostrar: {"items": [{"path": "...", "qualname": "..."}, ...]}
```

### 6.7 Paso 6: Ejecutar con tpx

```bash
# IMPORTANTE: Ejecutar desde backend/
cd backend

# Limpiar cache
rm -rf .turboplex_cache

# Ejecutar tests
f:\proyecto python\turboplex\target\release\tpx.exe --path tests/test_admin_config.py

# Resultado esperado:
# [RUST DEBUG] Collected 2 tests
# Results: 0 passed, 2 failed (XXXXms)
# (Los tests pueden fallar por lógica, pero no por collection)
```

### 6.8 Paso 7: Ejecutar Múltiples Tests

```bash
cd backend

f:\proyecto python\turboplex\target\release\tpx.exe \
  --path tests/test_health.py \
  --path tests/test_main.py \
  --path tests/test_admin_config.py \
  --path tests/test_debug_config.py \
  --path tests/test_feature_flags.py

# Resultado esperado:
# [RUST DEBUG] Collected 7 tests
# Results: 2 passed, 5 failed (XXXXms)
```

### 6.9 Paso 8: Verificar Compatibilidad Pytest

```bash
cd backend

# Debe funcionar igual que antes
pytest tests/test_admin_config.py -v

# Resultado: mismo que tpx (2 passed/failed)
```

---

## 7. Errores Comunes y Soluciones

### 7.1 Error: `ValidationError` en Collection

**Síntoma:**
```
3 validation errors for Settings
DATABASE_URL Field required
SECRET_KEY Field required
EXTERNAL_API_KEY Field required
[RUST DEBUG] Collected 0 tests
```

**Causa:** `app.core.config` se importa y valida durante collection sin variables de entorno.

**Solución:**
```python
# En conftest.py, modo Pytest:
# Mover imports dentro de funciones lazy

def _get_pytest_engine():
    from app.core.config import settings  # ← Dentro de función
    settings.DATABASE_URL = TEST_DATABASE_URL
```

### 7.2 Error: `fixture() got an unexpected keyword argument 'scope'`

**Síntoma:**
```
TypeError: fixture() got an unexpected keyword argument 'scope'
```

**Causa:** El decorador `fixture` de TurboPlex no aceptaba argumentos como `scope` y `autouse`.

**Solución:** (ya implementada en el código actual)
```python
# turboplex_py/fixtures.py
def fixture(fn=None, *, scope="function", autouse=False):
    def decorator(func):
        setattr(func, "_tt_fixture", True)
        setattr(func, "_tt_fixture_scope", scope)
        setattr(func, "_tt_fixture_autouse", autouse)
        # ...
    if fn is not None:
        return decorator(fn)
    return decorator
```

### 7.3 Error: `UndefinedTable` - Tablas no existen

**Síntoma:**
```
sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedTable)
no existe la relación «empresas»
```

**Causa:** El schema no se creó antes de ejecutar consultas.

**Solución:** En fixture `setup_db`, agregar:
```python
@fixture(scope="function")
def setup_db():
    engine = _get_engine()
    from app.core.database import Base
    from sqlalchemy import text
    
    with engine.connect() as conn:
        # Verificar si hay tablas
        result = conn.execute(text("""
            SELECT COUNT(*) FROM information_schema.tables 
            WHERE table_schema = 'public'
        """))
        table_count = result.scalar()
        
        if table_count == 0:
            print(">>> [TurboPlex] Creando schema...")
            Base.metadata.create_all(bind=conn)
    
    yield
```

### 7.4 Error: `UniqueViolation` - Datos duplicados

**Síntoma:**
```
sqlalchemy.exc.IntegrityError: (psycopg2.errors.UniqueViolation)
llave duplicada viola restricción de unicidad «ix_usuarios_email»
```

**Causa:** Datos de tests anteriores persisten entre ejecuciones.

**Solución:** Implementar limpieza de tablas:
```python
def _truncate_all_tables(conn):
    """Trunca todas las tablas del schema public."""
    from sqlalchemy import text
    
    tables = conn.execute(text("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_schema='public' AND table_type='BASE TABLE'
    """)).fetchall()
    
    if tables:
        table_list = ", ".join(f'"public"."{t[0]}"' for t in tables)
        conn.execute(text(f"TRUNCATE {table_list} CASCADE;"))

@fixture(scope="function")
def setup_db():
    engine = _get_engine()
    with engine.connect() as conn:
        _truncate_all_tables(conn)  # ← Limpiar antes
    yield
```

### 7.5 Error: `_active_generators` AttributeError

**Síntoma:**
```
AttributeError: 'PytestBridge' object has no attribute '_active_generators'
```

**Causa:** Referencia incorrecta al atributo en `pytest_bridge.py`.

**Solución:**
```python
# En pytest_bridge.py
# Cambiar:
self._active_generators[name] = gen
# Por:
self.fixture_manager._active_generators[name] = gen
```

### 7.6 Error: Tests recolectados pero no ejecutados

**Síntoma:**
```
[RUST DEBUG] Collected 2 tests
Results: 0 passed, 0 failed (0ms)
```

**Causa:** El cache está corrupto o vacío.

**Solución:**
```bash
# Limpiar cache y reintentar
rm -rf .turboplex_cache

# Verificar que el archivo de cache se genera correctamente
# Debe contener: {"items": [...]}
```

### 7.7 Error: `TURBOTEST_SUBPROCESS` no detectado

**Síntoma:**
```
DEBUG: TURBOPLEX_MODE=None
DEBUG: TURBOTEST_SUBPROCESS=None
DEBUG: _IS_TURBOPLEX=None
```

**Causa:** La variable no se propaga al proceso Python.

**Solución:** Verificar en `turboplex_py/__main__.py`:
```python
if len(sys.argv) > 1 and sys.argv[1] in ("collect", "run"):
    os.environ["TURBOTEST_SUBPROCESS"] = "1"  # ← Debe estar aquí
```

---

## 8. Troubleshooting Avanzado

### 8.1 Debug Mode

Activar logging detallado:

```python
# En conftest.py
import os
print(f"DEBUG: TURBOPLEX_MODE={os.environ.get('TURBOPLEX_MODE')}")
print(f"DEBUG: TURBOTEST_SUBPROCESS={os.environ.get('TURBOTEST_SUBPROCESS')}")
print(f"DEBUG: _IS_TURBOPLEX={_IS_TURBOPLEX}")
```

```rust
// En Rust (ya implementado en part2.rs)
eprintln!("[RUST DEBUG] Starting Python collector");
eprintln!("[RUST DEBUG] Interpreter: {}", env.interpreter);
eprintln!("[RUST DEBUG] Paths: {:?}", paths);
```

### 8.2 Verificar flujo completo

```bash
# 1. Verificar collector standalone
cd backend
python -m turboplex_py collect tests/test_simple.py --out-json /tmp/out.json
cat /tmp/out.json

# 2. Verificar con debug print en conftest
# Agregar en conftest.py:
print(">>> [CONFDEBUG] _IS_TURBOPLEX=", _IS_TURBOPLEX)

# 3. Ejecutar tpx y ver salida
tpx --path tests/test_simple.py 2>&1 | grep -E "(CONFDEBUG|DEBUG|Collected)"
```

### 8.3 Inspeccionar Cache

```bash
# Ver contenido del cache
cat .turboplex_cache/collected_tests.json | python -m json.tool

# Si está vacío o corrupto:
rm -rf .turboplex_cache
```

### 8.4 Verificar PYTHONPATH

```bash
# Desde backend/
python -c "import sys; print('\n'.join(sys.path))"

# Debe incluir:
# f:\proyecto python\turboplex\ERP3\backend
```

### 8.5 Test Aislado

```python
# test_isolated.py
import os
print(f"ENV TURBOPLEX_MODE: {os.environ.get('TURBOPLEX_MODE')}")
print(f"ENV TURBOTEST_SUBPROCESS: {os.environ.get('TURBOTEST_SUBPROCESS')}")

def test_simple():
    assert True
```

```bash
cd backend
python -m turboplex_py collect tests/test_isolated.py --out-json /tmp/out.json
```

---

## 9. Mejores Prácticas

### 9.1 Organización de Fixtures

```python
# ✅ BIEN: Fixtures simples y enfocados
@fixture(scope="function")
def db(setup_db):
    """Proporciona sesión de DB limpia."""
    from sqlalchemy.orm import Session
    session = Session(bind=engine)
    yield session
    session.close()

# ❌ EVITAR: Fixtures que hacen demasiado
@fixture(scope="function")
def everything():
    """Crea DB, carga datos, configura auth, etc."""
    # ... 100 líneas de código
```

### 9.2 Aislamiento de Tests

```python
# ✅ BIEN: Cada test limpia después de sí mismo
def test_create_user(db):
    user = User(name="Test")
    db.add(user)
    db.commit()
    
    # El fixture db hace rollback automático

# ❌ EVITAR: Dejar datos residuales
def test_create_user_bad(db):
    user = User(name="Test")
    db.add(user)
    db.commit()
    # No hay cleanup → contamina otros tests
```

### 9.3 Uso de Scope

```python
# ✅ BIEN: Scope apropiado
@fixture(scope="session")  # Una vez por suite
def app_config():
    return load_config()

@fixture(scope="module")   # Una vez por archivo de test
def module_data():
    return expensive_computation()

@fixture(scope="function") # Una vez por test (default)
def db_session():
    return create_session()
```

### 9.4 Detección de Modo

```python
# ✅ BIEN: Usar función helper
import os

def is_turboplex_mode():
    return os.environ.get('TURBOPLEX_MODE') or \
           os.environ.get('TURBOTEST_SUBPROCESS')

if is_turboplex_mode():
    # Código TurboPlex
else:
    # Código Pytest
```

### 9.5 Documentación de Fixtures

```python
@fixture(scope="function")
def admin_client(db, auth_token):
    """
    Cliente HTTP autenticado como administrador.
    
    Args:
        db: Sesión de base de datos
        auth_token: Token JWT válido
    
    Yields:
        TestClient: Cliente configurado con headers de auth
    
    Example:
        def test_admin_endpoint(admin_client):
            resp = admin_client.get("/admin/users")
            assert resp.status_code == 200
    """
    from fastapi.testclient import TestClient
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {auth_token}"
    yield client
```

---

## 10. Comparativa: TurboPlex vs Pytest

| Característica | TurboPlex | Pytest |
|----------------|-----------|---------|
| **Velocidad** | ⚡ Paralelo | 🐢 Secuencial (por defecto) |
| **Caching** | ✅ Collection + Results | ❌ No cache nativo |
| **Fixtures DB** | ✅ Soporte nativo | ✅ Soporte nativo |
| **Overhead** | Bajo (Rust) | Alto (Python) |
| **Compatibilidad** | Requiere ajustes | Universal |
| **Plugins** | Limitado | Extenso |
| **Reportes** | JSON simple | Múltiples formatos |
| **Debugging** | Logs básicos | pdb, rich tracebacks |

### Cuándo Usar TurboPlex

- ✅ Suite de tests grande (>100 tests)
- ✅ Tests independientes (pueden paralelizarse)
- ✅ CI/CD donde el tiempo importa
- ✅ Tests con DB que necesitan aislamiento

### Cuándo Usar Pytest

- ✅ Debugging de tests fallidos
- ✅ Uso extensivo de plugins
- ✅ Tests que requieren orden específico
- ✅ Desarrollo iterativo

---

## 11. Roadmap y Futuras Mejoras

### 11.1 Corto Plazo

1. **Mejor manejo de errores**: Capturar y reportar errores de collection
2. **Watch mode**: Re-ejecutar tests automáticamente en cambios
3. **Filtrado**: `--filter` para ejecutar subset de tests

### 11.2 Mediano Plazo

1. **Coverage**: Integración con coverage.py
2. **Snapshots**: Soporte para snapshot testing
3. **Parametrización**: Soporte completo para `@pytest.mark.parametrize`

### 11.3 Largo Plazo

1. **Distributed**: Ejecución distribuida en múltiples máquinas
2. **IDE Integration**: Plugins para VSCode, PyCharm
3. **Remote Cache**: Cache compartido entre equipo

---

## Apéndice A: Configuración de Referencia

### A.1 conftest.py Completo (ERP3)

Ver archivo: `f:\proyecto python\turboplex\ERP3\backend\tests\conftest.py`

### A.2 .env de Referencia

```bash
# Database
TEST_DATABASE_URL=postgresql+psycopg2://erp3_user:erp3_pass_2026@localhost:5432/erp3_test
DATABASE_URL=${TEST_DATABASE_URL}

# Security
SECRET_KEY=your-secret-key-here
EXTERNAL_API_KEY=your-api-key-here

# App
DEBUG=true
ENVIRONMENT=testing
```

### A.3 Script de Setup

```bash
#!/bin/bash
# setup_turboplex.sh

set -e

echo "=== TurboPlex Setup ==="

# 1. Compilar
cd /path/to/turboplex
cargo build --release

# 2. Instalar Python module
cd /path/to/your-project
source .venv/bin/activate
pip install -e /path/to/turboplex

# 3. Copiar conftest
cp /path/to/turboplex/examples/conftest_hybrid.py backend/tests/conftest.py

echo "=== Setup Complete ==="
echo "Run: cd backend && /path/to/tpx --path tests/"
```

---

## Apéndice B: FAQ

### Q: ¿Puedo usar TurboPlex con Django?
**A:** Sí, pero necesitarás adaptar el conftest para usar `django.test.TestCase` o `pytest-django`.

### Q: ¿Funciona con async/await?
**A:** Soporte parcial. Los fixtures async requieren manejo especial en `pytest_bridge.py`.

### Q: ¿Puedo mezclar tests con y sin DB?
**A:** Sí, el conftest híbrido detecta automáticamente qué fixtures necesita cada test.

### Q: ¿Cómo debuggeo un test que falla solo en TurboPlex?
**A:** Ejecuta el test individualmente con pytest, luego compara el entorno:
```bash
pytest tests/test_failing.py -v --tb=long
tpx --path tests/test_failing.py 2>&1 | head -100
```

### Q: ¿Dónde reporto bugs?
**A:** GitHub Issues del repositorio TurboPlex con:
- Versión de tpx
- Salida completa con `RUST DEBUG`
- Contenido del conftest (sin datos sensibles)
- Comando exacto usado

---

**Documentación creada:** Marzo 2026  
**Versión de TurboPlex:** Compatible con commit actual  
**Autor:** Sistema de Documentación Automática
