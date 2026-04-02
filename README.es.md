# TurboPlex (tpx) - El Motor de Orquestación de Pruebas para la Era de la IA

[English](README.md) | **Español**

<p align="center">
  <img src="https://img.shields.io/badge/Rust-DEA584?style=for-the-badge&logo=rust&logoColor=white" alt="Rust">
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License">
</p>

## Índice
- [¿Por qué TurboPlex?](#es-por-que)
- [Instalación](#es-instalacion)
- [Uso Rápido](#es-uso-rapido)
- [Benchmarks](#es-benchmarks)
- [Configuración](#es-configuracion)
- [API para Agentes IA](#es-api-agentes-ia)
- [Comandos](#es-comandos)
- [Arquitectura](#es-arquitectura)
- [Archivos Excluidos del Repositorio (.gitignore)](#es-gitignore)
- [License](#es-license)
- [Autores](#es-autores)

<a id="es-por-que"></a>
## ¿Por qué TurboPlex?

> **4x más rápido que Pytest** en ejecución de pruebas. El motor de orquestación diseñado para la era de la IA.

### Propuesta de Valor

| Característica | Descripción |
|----------------|-------------|
| ⚡ **4x Velocidad** | Ejecución en paralelo con cacheo inteligente (12s → 3s) |
| 🦀 **Núcleo Rust** | Análisis estático y gestión de memoria sin overhead |
| 🤖 **Protocolo M2M** | Genera `.tplex_report.json` con errores procesables por IA |
| 🧠 **Análisis IA** | Comando `--analyze` categoriza fallos para debugging a escala |
| 👁️ **Watch Mode** | Recarga automática cuando guardas archivos `.py` |
| 🎯 **Orquestación Inteligente** | Auto-detecta tier de hardware (móvil/desktop/servidor) |
| 🔧 **Soporte Parametrize** | Compatibilidad total con `@pytest.mark.parametrize` y `@mock.patch` |
| 📈 **De-duplicación** | Elimina errores duplicados para debugging más rápido |
| 🗂️ **Rutas Normalizadas** | Rutas estandarizadas para compatibilidad cross-platform |

<a id="es-instalacion"></a>
## Instalación

```bash
# Clona el repositorio
cd turboplex

# Instala en modo desarrollo
pip install -e .

# Verifica la instalación
tpx --help
```

<a id="es-uso-rapido"></a>
## Uso Rápido

### Ejecución Básica

```bash
# Un solo test
tpx --path tests/test_simple.py

# Múltiples directorios
tpx --path tests/ --path tests/integration/

# Auto-descubrimiento de tests
tpx
```

### Modo Watch (Desarrollo TDD)

```bash
# Ejecuta y observa cambios en tiempo real
tpx --watch --path tests/
```

### Integración con Agentes IA

```bash
# INSTRUCCIÓN PARA TU AGENTE IA:
# Si detectas un archivo .tplex_report.json en el proyecto,
# léelo inmediatamente y propón una solución automática.

# El archivo contiene:
# - Error exacto
# - Línea del fallo
# - Contexto del código (5 líneas antes y después)
```

<a id="es-benchmarks"></a>
## Benchmarks

### Análisis de Tests con IA (`--analyze`)

Para suites de tests grandes con cientos de fallos, TurboPlex proporciona categorización automática de errores y recomendaciones accionables. Testeado en suites de producción reales (~200 tests, ~3.2MB de datos de fallo).

```bash
# Ejecutar tests con generación de reporte JSONL
tpx --path tests/

# Analizar el reporte generado
tpx --analyze
```

**Salida de Ejemplo:**
```
════════════════════════════════════════════════════════════
          TurboPlex Analysis Report
════════════════════════════════════════════════════════════

📊 Resumen
   Total:  199
   Exitosos: 35
   Fallidos: 164
   Tasa:   17.6%

🚨 Issues Críticos
   • 45 tests tienen problemas de conectividad a base de datos
   • 12 tests tienen errores de importación - pueden faltar dependencias

📋 Categorías de Error
   [45] AuthError: Expected 200 got 403 - Fallos de autorización
   [32] DatabaseError: Unique constraint violation
   [28] FixtureError: Fixture setup failed
   [20] AssertionError: Creation status mismatch
   [15] ImportError: Missing module

💡 Top Recomendaciones
   1. Prioridad 45: Verificar fixtures de autenticación - asegurar credenciales válidas
   2. Prioridad 32: Implementar limpieza de DB entre tests o usar identificadores únicos
   3. Prioridad 28: Revisar dependencias de fixtures y asegurar setup/teardown correcto
```

**Archivos Generados:**
- `turboplex_full_report.json` — Reporte JSONL completo con contexto de errores (traceback, locals, diff, parametrize call_spec)
- Categorización automática: Errores de DB, fallos de Auth, issues de import, problemas de fixtures
- Salida JSON lista para IA para pipelines de debugging automatizado
- De-duplicación de errores con fingerprinting y conteo de ocurrencias
- Normalización cross-platform de rutas (forward slashes para compatibilidad universal)

### Speedrun: Suite de Producción (~200 tests)

| Herramienta | Tiempo | por test |
|-------------|--------|----------|
| **pytest** | ~340s | ~1.7s |
| **tpx (cold)** | ~180s | ~0.9s |
| **tpx (cached)** | **~25s** | **~0.13s** |

```
pytest (cold):  ████████████████████████████████████████ 340s
tpx (cold):     ██████████████████████ 180s (2x faster)
tpx (cached):   ███ 25s (14x faster, 82% cache hit)
```

🖥️ **Testeado en:**

- CPU: Ryzen 7 5700X3D (8C/16T)
- RAM: 16GB DDR4 @ 3600MHz
- Storage: Crucial P3 NVMe Gen3 (1TB)

### Comparativa por Test

| Métrica | pytest | tpx |
|---------|--------|-----|
| Tiempo por test | ~6s | ~1.5s |
| Cacheo | No | Sí (SHA-256) |
| M2M Report | No | Sí (.tplex_report.json) |

<a id="es-configuracion"></a>
## Configuración

### Archivo `turbo_config.toml`

```toml
[execution]
max_workers = 8
default_timeout_ms = 30000
cache_enabled = true

[python]
enabled = true
interpreter = "python"
module = "turboplex_py"
test_paths = ["tests"]
project_path = "."
```

### Caché

El caché se almacena en `.turboplex_cache/` y se invalida automáticamente cuando los archivos de test cambian (hash SHA-256).

<a id="es-api-agentes-ia"></a>
## API para Agentes IA

### Formato `.tplex_report.json`

```json
{
  "timestamp": "2026-03-28 14:17:49",
  "total_tests": 1,
  "failed_count": 1,
  "failures": [
    {
      "test": "test_fiscal_year_close_logic",
      "file": "tests/test_accounting_close.py",
      "line": 42,
      "error": "parameter 'db' has no @fixture and no default",
      "context": [
        "    38: def test_fiscal_year_close_logic(db):",
        "    39:     # Arrange",
        "    40:     year = 2024",
        ">>> 41:     result = close_year(db, year)",
        "    42:     assert result.success"
      ]
  ],
  "unique_failures": 1
}
```

### MCP para IDE/Agentes (`tpx mcp`)

- Contrato estable por herramienta (`discover`, `run`, `get_report`): `schemaVersion`, `tool`, `ok`, `runId`, `mode`, `summary`, `logs`, `data`.
- En errores (`ok=false`), `data.error` usa:
  - `code`: `timeout | subprocess_failed | invalid_input | not_found | internal_error`
  - `message`: texto legible
  - `details`: opcional (`phase`, `returncode`, `timeout_s`, etc.)
- `run.summary` incluye métricas operativas: `workers_used`, `timeouts`, `subprocess_failures`.
- Extensiones DB-first:
  - `data.results[].db_metrics.write_count`
  - `data.results[].db_dirty`
  - `data.results[].db_dirty_summary`
  - `run.summary.db_write_count_total`
  - `run.summary.db_dirty_tests`

Cobertura de integración agregada hoy:
- `tests/test_mcp_db_integration.py` valida `run` de MCP con escrituras reales en SQLite.
- Política strict dirty validada:
  - `TPX_DB_STRICT_DIRTY=0` -> la corrida puede pasar reportando `db_dirty`.
  - `TPX_DB_STRICT_DIRTY=1` -> la corrida falla con `db_error.code=db_dirty_state`.
- Se añadió variante subprocess-only con `xfail` en Windows por crash nativo ocasional `0xC0000005` (Access Violation).

Variables de entorno MCP más comunes:
- `TPX_PYTHON_EXE`
- `TPX_MCP_LIGHT_COLLECT=1`
- `TPX_MCP_DEBUG=1`
- `TPX_MCP_STDOUT_MODE=redirect|failfast`
- `TPX_MCP_TEST_TIMEOUT_S` (default 120)
- `TPX_MCP_TURBOPLEX_COLLECT_TIMEOUT_S` (default 120)
- `TPX_MCP_TURBOPLEX_RUN_TIMEOUT_S` (default 60)
- `TPX_MCP_PYTEST_COLLECT_TIMEOUT_S` / `TPX_PYTEST_COLLECT_TIMEOUT_S` (default 120)
- `TPX_MCP_PYTEST_RUN_TIMEOUT_S` / `TPX_PYTEST_RUN_TIMEOUT_S` (default 60)
- `TPX_MCP_HEARTBEAT_S` (default 1)
- `TPX_MCP_TERMINATE_GRACE_S` (default 2)
- `TPX_MCP_DRAIN_MAX_CHARS` (default 2000000)
- `TPX_MCP_LOGS_MAX_CHARS` (default 20000)

Variables de hardening DB:
- `TPX_DB_STRICT_DIRTY=0|1` (default `0`, solo falla si está en `1`)
- `TPX_DB_METRICS_ENABLED=0|1` (default `1`)
- `TPX_DB_ISOLATION_MODE=auto|schema|database|transaction` (default `auto`)
- `TPX_DB_WORKER_PREFIX=tpx_w`
- `TPX_DB_DIRTY_TRACK_MAX_TABLES=12`

<a id="es-comandos"></a>
## Comandos

| Comando | Descripción |
|---------|-------------|
| `tpx` | Auto-descubrir y ejecutar tests |
| `tpx --path ./tests` | Ejecutar tests en directorio |
| `tpx --watch` | Modo watch con auto-reload |
| `tpx --compat` | Delegar discovery/ejecución a pytest para suites con muchos fixtures |
| `tpx --compat --light` | Modo discovery rápido (saltea carga de conftest.py) |
| `tpx --analyze` | Analizar fallos de tests y categorizar errores (requiere `turboplex_full_report.json`) |
| `tpx mcp` | Iniciar servidor MCP sobre stdio para integración IDE |
| `tpx --help` | Mostrar ayuda |

<a id="es-arquitectura"></a>
## Arquitectura

```
┌─────────────────────────────────────────────────────┐
│                    tpx (Rust)                        │
├─────────────────────────────────────────────────────┤
│  • Descubrimiento de tests                          │
│  • Cacheo SHA-256                                  │
│  • Ejecución paralela (Rayon)                      │
│  • Watch mode (notify)                             │
│  • Reporte M2M (.tplex_report.json)                │
│  • Reportes JSONL (turboplex_full_report.json)     │
│  • Análisis IA (--analyze)                         │
└─────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────┐
│              turboplex_py (Python)                  │
├─────────────────────────────────────────────────────┤
│  • collector.py - Descubridor de tests             │
│  • runner.py - Ejecutor de tests                   │
│  • fixtures.py - Sistema de fixtures @fixture      │
│  • markers.py - skip, skipif                       │
└─────────────────────────────────────────────────────┘
```

<a id="es-gitignore"></a>
## Archivos Excluidos del Repositorio (.gitignore)

Este proyecto ignora archivos generados y configuración local para mantener el repositorio liviano, reproducible y libre de datos sensibles.

- Artefactos de compilación y cachés (por ejemplo `target/`, `**/target/`, `.cache/`)
- Archivos temporales y logs (`*.tmp`, `*.log`, `*.swp`)
- Configuración local del IDE/SO (por ejemplo `.vscode/`, `.idea/`, `Thumbs.db`, `.DS_Store`)
- Entornos y metadatos locales de Python (por ejemplo `.venv/`, `__pycache__/`, `*.egg-info/`)
- Archivos de entorno con secretos o configuración local (`.env`, `.env.*`)
- Dependencias y salidas de tooling web si aplican (`node_modules/`, `dist/`, `build/`)
- Cachés y reportes generados por TurboPlex (`.turboplex_cache/`, `.tplex_report.json`, `turboplex_full_report.json`)

<a id="es-license"></a>
## License

MIT License - Ver archivo `LICENSE`

<a id="es-autores"></a>
## Autores

**Versión TurboPlex:** 0.3.1 - **TurboPlex Team** - [@turbo plexus](https://github.com/turboplex)

---

<p align="center">
  🚀 <em>El futuro de los tests está aquí</em>
</p>
