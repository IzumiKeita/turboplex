@echo off
setlocal

echo [1/3] Extrayendo token de .env de forma segura...
:: Usamos PowerShell desde el BAT para parsear el .env sin mostrar el token
for /f "usebackq tokens=*" %%a in (`powershell -NoProfile -Command "(Get-Content .env | Where-Object { $_ -match '^\s*pypi_api_key\s*=' } | ForEach-Object { ($_ -split '=',2)[1].Trim() } | Select-Object -First 1)"`) do set "PYPI_TOKEN=%%a"

if "%PYPI_TOKEN%"=="" (
    echo [ERROR] No se pudo encontrar pypi_api_key en el archivo .env
    pause
    exit /b 1
)

echo [2/3] Configurando variables de entorno para Maturin...
set "MATURIN_USERNAME=__token__"
set "MATURIN_PASSWORD=%PYPI_TOKEN%"

echo [3/3] Publicando version 0.3.0 en PyPI...
python -m maturin publish --skip-existing

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [EXITO] TurboPlex v0.3.0 ha sido publicado correctamente.
) else (
    echo.
    echo [ERROR] Hubo un problema al publicar. Revisa los logs arriba.
)

pause
