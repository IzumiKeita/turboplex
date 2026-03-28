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
| 👀 **Watch Mode** | Recarga automática cuando guardas archivos `.py` |

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

### Speedrun: 2 Tests

| Herramienta | Tiempo |
|------------|--------|
| **pytest** | 12.69s |
| **tpx (cached)** | **~3s** |

```
pytest:     ████████████████████████████ 12.69s
tpx:        █████ 3s (cached)
```

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
    }
  ]
}
```

<a id="es-comandos"></a>
## Comandos

| Comando | Descripción |
|---------|-------------|
| `tpx` | Auto-descubrir y ejecutar tests |
| `tpx --path ./tests` | Ejecutar tests en directorio |
| `tpx --watch` | Modo watch con auto-reload |
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
- Cachés y reportes generados por TurboPlex (`.turboplex_cache/`, `.tplex_report.json`)

<a id="es-license"></a>
## License

MIT License - Ver archivo `LICENSE`

<a id="es-autores"></a>
## Autores

**TurboPlex Team** - [@turbo plexus](https://github.com/turboplex)

---

<p align="center">
  🚀 <em>El futuro de los tests está aquí</em>
</p>
