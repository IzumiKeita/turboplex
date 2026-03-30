**Texto listo para pasarle al otro IDE (para integrar en 0.2.5)**

**Contexto / problema**
- En Windows, con `turboplex==0.2.4`, el servidor MCP por stdio funciona para `initialize`, `tools/list` y `tools/call ping`, pero `tools/call discover` y `tools/call run` pueden quedarse colgados (sin respuesta JSON-RPC).
- El síntoma aparece incluso contra un proyecto pytest mínimo (un solo `test_smoke.py` con `assert 1==1`), así que no es específico de ERP3.
- Hipótesis: en la ruta compat se invoca pytest con `sys.executable`, pero cuando el proceso actual es un “launcher” (`tpx.exe`) o un wrapper, `sys.executable` puede no apuntar a un `python.exe` real, generando recursión/estado inválido o bloqueo. Además, no hay `timeout` en `subprocess.run`.

---

## Objetivo (0.2.5)
- Hacer que `discover/run` (compat=true) **siempre respondan** por MCP stdio: éxito o error estructurado, pero **nunca cuelguen**.
- Evitar problemas de encoding en Windows (stdout/stderr) y asegurar que stdout del servidor MCP se mantenga limpio (JSON-RPC únicamente).

---

## Repro mínimo (PowerShell, determinístico)
Ejecutar esto en una máquina Windows con el venv donde esté instalado `turboplex==0.2.4+`:

```powershell
$tmp = Join-Path $env:TEMP ("tpx-mcp-min-" + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

@"
def test_ok():
    assert 1 == 1
"@ | Set-Content -Encoding UTF8 -Path (Join-Path $tmp "test_smoke.py")

# Si ya tienen un smoke client MCP similar, setear:
#   TPX_CWD=$tmp
#   TPX_SMOKE_PATH="test_smoke.py"
#   TPX_SMOKE_NODEID="test_smoke.py::test_ok"
#   TPX_SMOKE_DEEP=1
# y correr el cliente.

# Alternativa: correr manualmente el server (para observar si responde)
Push-Location $tmp
tpx mcp
Pop-Location
```

**Esperado**
- `discover` y `run` deben responder (o fallar con error controlado) en un tiempo acotado.
- No se debe observar “hang” indefinido.

---

## Parche propuesto (0.2.5) — `turboplex_py/mcp_server.py`
Aplicar a la implementación MCP (la ruta “compat” que usa pytest). El diff está pensado para el archivo equivalente al que hoy contiene `_pytest_collect/_pytest_run`.

```diff
diff --git a/turboplex_py/mcp_server.py b/turboplex_py/mcp_server.py
index 0000000..1111111 100644
--- a/turboplex_py/mcp_server.py
+++ b/turboplex_py/mcp_server.py
@@
 import sys
+import os
+import shutil
+import subprocess

+def _resolve_python_executable() -> str:
+    override = os.environ.get("TPX_PYTHON_EXE")
+    if override:
+        return override
+
+    base = getattr(sys, "_base_executable", None)
+    if base and os.path.basename(base).lower().startswith("python"):
+        return base
+
+    exe = sys.executable
+    if exe and os.path.basename(exe).lower().startswith("python"):
+        return exe
+
+    found = shutil.which("python") or shutil.which("py")
+    return found or exe
+
+def _subprocess_env() -> dict[str, str]:
+    env = os.environ.copy()
+    env.setdefault("PYTHONUTF8", "1")
+    env.setdefault("PYTHONIOENCODING", "utf-8")
+    return env
+
@@
 def _pytest_collect(paths):
-    import os
-    import subprocess
-
-    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", *paths]
-    out = subprocess.run(
-        cmd,
-        capture_output=True,
-        text=True,
-        cwd=os.getcwd(),
-        env=os.environ.copy(),
-    )
+    cmd = [_resolve_python_executable(), "-m", "pytest", "--collect-only", "-q", *paths]
+    try:
+        out = subprocess.run(
+            cmd,
+            capture_output=True,
+            text=True,
+            encoding="utf-8",
+            errors="replace",
+            cwd=os.getcwd(),
+            env=_subprocess_env(),
+            timeout=float(os.environ.get("TPX_PYTEST_COLLECT_TIMEOUT_S") or "60"),
+        )
+    except subprocess.TimeoutExpired as e:
+        raise RuntimeError(f"pytest collect timeout: {e.timeout}s") from e
     if out.returncode != 0:
         raise RuntimeError(out.stderr.strip() or "pytest collect failed")
@@
 def _pytest_run(nodeid):
-    import os
-    import subprocess
-    import time
+    import time
@@
-    cmd = [sys.executable, "-m", "pytest", "-q", nodeid]
-    out = subprocess.run(
-        cmd,
-        capture_output=True,
-        text=True,
-        cwd=os.getcwd(),
-        env=os.environ.copy(),
-    )
+    cmd = [_resolve_python_executable(), "-m", "pytest", "-q", nodeid]
+    try:
+        out = subprocess.run(
+            cmd,
+            capture_output=True,
+            text=True,
+            encoding="utf-8",
+            errors="replace",
+            cwd=os.getcwd(),
+            env=_subprocess_env(),
+            timeout=float(os.environ.get("TPX_PYTEST_RUN_TIMEOUT_S") or "120"),
+        )
+    except subprocess.TimeoutExpired as e:
+        return {"passed": False, "duration_ms": int((time.perf_counter() - t0) * 1000), "error": f"pytest run timeout: {e.timeout}s"}
     dt = int((time.perf_counter() - t0) * 1000)
     passed = out.returncode == 0
     err = None
```

**Notas del parche**
- `TPX_PYTHON_EXE` permite forzar explícitamente el intérprete (útil en CI o setups raros).
- `timeout` garantiza que el tool-call siempre responde (si vence, devuelve error en vez de colgar).
- `encoding/errors` evita `UnicodeDecodeError` en Windows y normaliza salida.

---

## Criterios de aceptación (checklist)
- MCP stdio:
  - `initialize` OK
  - `tools/list` OK
  - `tools/call ping` OK
- Compat tools:
  - `tools/call discover (compat=true)` **no cuelga**: responde con items o error controlado antes del timeout.
  - `tools/call run (compat=true)` **no cuelga**: responde con passed/error antes del timeout.
- Windows robustness:
  - No `UnicodeDecodeError`.
  - stdout del server no se contamina (solo JSON-RPC).
- Configurabilidad:
  - `TPX_PYTHON_EXE`, `TPX_PYTEST_COLLECT_TIMEOUT_S`, `TPX_PYTEST_RUN_TIMEOUT_S` funcionan.

---

## Mensaje de cierre sugerido (para release notes 0.2.5)
- “MCP stdio: `discover/run` (compat=true) ya no se quedan colgados en Windows; se usa un Python ejecutable real y se agregan timeouts + manejo robusto de encoding.”

Si quieres, también te redacto el texto como “issue” (título + descripción + pasos + expected/actual) listo para pegar en GitHub/GitLab.