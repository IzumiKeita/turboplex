# Cache Hardening v0.3.4 (The Strict Era)

Fecha: 2026-04-03
Version: v0.3.4

## Objetivo
Endurecer el cache de TurboPlex para entornos profesionales:
- Invalidar cache si cambia la configuracion de DB (TPX_DB_*).
- Marcar calentamiento de OS (page cache) para diagnostico.
- Garantizar integridad: resultados cacheados siempre con duracion cero y warning en debug si no.

## Cambios aplicados

### A) Ancla de Base de Datos (alta prioridad)
Se incorporo el hash de todas las variables `TPX_DB_*` en el fingerprint del runtime.
Esto invalida el cache cuando cambian host, usuario, puerto o cualquier flag DB.

- Codigo: `compute_db_env_fingerprint()`
- Ubicacion: `src/main/part1.rs`
- Efecto: `env.fingerprint` ahora incluye `db_env`.

### B) Deteccion de OS_WARM (prioridad media)
Se compara el tiempo medido en Rust con el `duration_ms` reportado por Python.
Si `rust_ms - python_ms < 150`, se agrega `os_warm: true` en `enriched_data`; de lo contrario `false`.
Para resultados cacheados se fuerza `os_warm: false`.

- Codigo: `run_python_item_fixed()`
- Ubicacion: `src/test_runner/python.rs`

### C) Check de integridad del cache
Si `cached == true`, se fuerza `duration_ms = 0`.
Si el valor es mayor a 5ms, se emite warning en debug (`debug-logging`).

- Codigo: `OutputState::push()`
- Ubicacion: `src/main/output.rs`

## Impacto
- Cache mas estricto ante cambios de DB.
- Metadato explicito para distinguir calentamiento del kernel vs. ejecucion real.
- Reduce falsos negativos de cache (latencias altas reportadas como cached).

## Verificacion sugerida
1. Ejecutar un test con cache caliente.
2. Cambiar una variable `TPX_DB_*`.
3. Confirmar que el cache se invalida (no hay `cached=true`).
4. Forzar overhead y verificar `os_warm: true/false` en JSON.

## Smoke test (2 pasadas)
- Pasada 1 (cold): `tplex_smoke_os_warm_pass1.json`
- Pasada 2 (warm/cache): `tplex_smoke_os_warm_pass2.json`
- Expectativa: primera pasada `cached=false` y `os_warm` segun delta; segunda pasada `cached=true`, `os_warm=false`, `duration_ms=0`.

### D) Fingerprint en cache de part2 (fix de invalidación)
El cache de resultados (`part2/cache.rs`) ahora guarda el `fingerprint` junto con `passed`.
Al cargar, verifica que el fingerprint coincida; si cambia (ej: DB env), invalida el cache.

- Código: `save_cached_pass_result()`, `load_cached_pass_result()`
- Ubicación: `src/main/part2/cache.rs`
- Efecto: Cambios de `TPX_DB_*` invalidan el cache existente.

## Referencias
- `src/main/part1.rs`
- `src/main/part2/cache.rs`
- `src/test_runner/python.rs`
- `src/main/output.rs`
