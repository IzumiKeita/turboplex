# Release Notes – TurboPlex v0.3.1 (ERP3 build)

Este build de TurboPlex v0.3.1 se generó desde `C:\GitHub\ERP3\vendor\turboplex` y fue validado contra el proyecto ERP3.

## Fix: selección de intérprete (TPX_PYTHON_EXE)

- `TPX_PYTHON_EXE` tiene prioridad real sobre la autodetección de `.venv`.
- Esto permite correr `tpx` dentro de un proyecto que tiene `.venv` presente, pero forzar el runner a usar un intérprete distinto (por ejemplo `.venv_test2`).

Ejemplo:

```powershell
$env:TPX_PYTHON_EXE = (Resolve-Path .\.venv_test2\Scripts\python.exe).Path
.\.venv_test2\Scripts\tpx.exe --path tests/test_simple.py --workers 1
```

Salida esperada (resumen):

- `Using TPX_PYTHON_EXE: ...\.venv_test2\Scripts\python.exe`

## Fix: pytest.skip en runner nativo

- El runner nativo (`turboplex_py/runner.py`) ahora reconoce `pytest.skip(...)` correctamente.
- Los tests saltados se reportan como `SKIP` (no como `FAIL`) e incluyen `skip_reason` en el JSON.
- El resumen de consola muestra el contador de `skipped`.

## Artefactos (local)

- Wheel: `vendor/turboplex/dist/turboplex-0.3.1-py3-none-win_amd64.whl`
- Sdist: `vendor/turboplex/dist/turboplex-0.3.1.tar.gz`
