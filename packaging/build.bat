@echo off
REM ============================================================================
REM build.bat - Compila zkteco.exe portable para Windows (CMD equivalente)
REM ============================================================================
REM
REM Uso (desde la raiz del repo, en CMD):
REM
REM     packaging\build.bat
REM
REM Equivalente CMD de packaging\build.ps1. Mismo flujo, mismas salidas.
REM Pensado para entornos con PowerShell restringido o build servers minimos.
REM
REM Resultado:
REM     dist\zkteco\               carpeta --onedir lista para correr
REM     dist\zkteco\zkteco.exe     ejecutable principal
REM     dist\zkteco-portable-vX.Y.Z.zip   ZIP portable para distribuir
REM
REM Requiere Windows 10 1803+ (por el `tar` integrado para crear el zip).
REM Ver BUILD.md en la raiz del repo para troubleshooting completo.
REM ============================================================================

setlocal enabledelayedexpansion

REM -- 0. Validar directorio de trabajo --------------------------------------
REM El .bat puede invocarse desde cualquier carpeta. Saltamos a la raiz del
REM repo (un nivel arriba de packaging\) para que las rutas relativas del
REM spec resuelvan bien.
cd /d "%~dp0.."

if not exist "config.py" (
    echo [ERROR] No se encontro config.py en %CD%.
    echo         Ejecuta este script desde la raiz del repo.
    exit /b 1
)
if not exist "main.py" (
    echo [ERROR] No se encontro main.py en %CD%.
    exit /b 1
)

echo [INFO] Repo root: %CD%

REM -- 1. Verificar / crear venv ---------------------------------------------
if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Creando venv en .\venv\ ...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] python -m venv fallo. Verifica que Python 3.11+ este en PATH.
        exit /b 1
    )
) else (
    echo [INFO] Reutilizando venv existente.
)

REM -- 2. Activar venv -------------------------------------------------------
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] No se pudo activar el venv.
    exit /b 1
)

REM -- 3. Instalar dependencias ----------------------------------------------
echo [INFO] Instalando dependencias (requirements-dev.txt) ...
python -m pip install --upgrade pip --quiet
if errorlevel 1 exit /b 1
python -m pip install -r requirements-dev.txt --quiet
if errorlevel 1 (
    echo [ERROR] pip install fallo. Revisa la conexion y los logs.
    exit /b 1
)

REM -- 4. Limpiar artefactos previos -----------------------------------------
if exist "build" (
    echo [INFO] Borrando build\ ...
    rmdir /s /q build
)
if exist "dist" (
    echo [INFO] Borrando dist\ ...
    rmdir /s /q dist
)

REM -- 5. Ejecutar PyInstaller -----------------------------------------------
echo [INFO] Compilando con PyInstaller ...
pyinstaller packaging\zkteco.spec --noconfirm
if errorlevel 1 (
    echo [ERROR] PyInstaller fallo. Revisa el log de arriba.
    exit /b 1
)

if not exist "dist\zkteco\zkteco.exe" (
    echo [ERROR] dist\zkteco\zkteco.exe no se genero.
    exit /b 1
)

REM -- 6. Leer version desde config.APP_VERSION ------------------------------
REM Capturamos la salida de python en una variable. for /f con `delims=`
REM preserva la linea completa (sin recortes en espacios).
for /f "delims=" %%v in ('python -c "from config import APP_VERSION; print(APP_VERSION)"') do set "VERSION=%%v"

if "%VERSION%"=="" (
    echo [ERROR] No se pudo leer APP_VERSION desde config.py.
    exit /b 1
)
echo [INFO] Version detectada: %VERSION%

REM -- 7. Empaquetar ZIP portable --------------------------------------------
REM Windows 10 1803+ trae `tar` (bsdtar) integrado en system32. La opcion
REM -a -c -f genera un .zip estandar a partir de `dist\zkteco\`.
set "ZIP_NAME=zkteco-portable-v%VERSION%.zip"
set "ZIP_PATH=dist\%ZIP_NAME%"

if exist "%ZIP_PATH%" del /q "%ZIP_PATH%"

echo [INFO] Generando %ZIP_PATH% ...
pushd dist
tar -a -c -f "%ZIP_NAME%" zkteco
if errorlevel 1 (
    popd
    echo [ERROR] tar fallo al crear el ZIP.
    exit /b 1
)
popd

REM -- 8. Resumen ------------------------------------------------------------
echo.
echo --- BUILD COMPLETADO -----------------------------------------------
echo   Carpeta:     dist\zkteco\
echo   Ejecutable:  dist\zkteco\zkteco.exe
echo   ZIP:         %ZIP_PATH%
echo --------------------------------------------------------------------

endlocal
exit /b 0
