# =============================================================================
# build.ps1 — Compila zkteco.exe portable para Windows
# =============================================================================
#
# Uso (desde la raíz del repo, en una terminal PowerShell con Python 3.11+
# y Visual C++ Build Tools instalados):
#
#     .\packaging\build.ps1
#
# Si la ExecutionPolicy bloquea el script:
#
#     powershell -ExecutionPolicy Bypass -File .\packaging\build.ps1
#
# Resultado:
#     dist\zkteco\               carpeta --onedir lista para correr
#     dist\zkteco\zkteco.exe     ejecutable principal
#     dist\zkteco-portable-vX.Y.Z.zip   ZIP portable para distribuir
#
# Diseño:
#   • Fail-fast con $ErrorActionPreference = 'Stop'.
#   • Idempotente: limpia build/ y dist/ antes de cada compilación.
#   • Reproducible: instala dependencias desde requirements-dev.txt.
#   • La versión sale de config.APP_VERSION (single source of truth).
#
# Ver BUILD.md en la raíz del repo para troubleshooting completo.
# =============================================================================

$ErrorActionPreference = 'Stop'

# ── 0. Validar directorio de trabajo ─────────────────────────────────────────
# El script se invoca desde packaging\, pero las rutas de PyInstaller son
# relativas a la raíz del repo. Forzamos cwd = raíz para que `pyinstaller
# packaging\zkteco.spec` resuelva correctamente.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir '..')
Set-Location $RepoRoot

if (-not (Test-Path 'config.py') -or -not (Test-Path 'main.py')) {
    Write-Host '[ERROR] No se encontraron config.py / main.py.' -ForegroundColor Red
    Write-Host '        Ejecuta este script desde la raiz del repo.' -ForegroundColor Red
    exit 1
}

Write-Host "[INFO] Repo root: $RepoRoot" -ForegroundColor Cyan

# ── 1. Verificar / crear venv ────────────────────────────────────────────────
# Convención: el venv vive en .\venv\. Si no existe, lo creamos. Si existe,
# lo reutilizamos (más rápido en buildss sucesivos).
if (-not (Test-Path 'venv\Scripts\Activate.ps1')) {
    Write-Host '[INFO] Creando venv en .\venv\ ...' -ForegroundColor Cyan
    python -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[ERROR] python -m venv fallo. Verifica que Python 3.11+ este en PATH.' -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host '[INFO] Reutilizando venv existente.' -ForegroundColor Cyan
}

# ── 2. Activar venv ──────────────────────────────────────────────────────────
. .\venv\Scripts\Activate.ps1

# ── 3. Instalar dependencias de desarrollo ──────────────────────────────────
# requirements-dev.txt incluye prod + pyinstaller. Usamos --upgrade para
# garantizar que estamos en las versiones lockeadas tras un `git pull`.
Write-Host '[INFO] Instalando dependencias (requirements-dev.txt) ...' -ForegroundColor Cyan
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements-dev.txt --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] pip install fallo. Revisa la conexion y los logs.' -ForegroundColor Red
    exit 1
}

# ── 4. Limpiar artefactos previos ────────────────────────────────────────────
# build\ es scratch de PyInstaller — siempre se borra completo.
# dist\ contiene el output del bundle, pero ``dist\zkteco\data\`` guarda la
# BD de producción y los logs de la instalación: borrarla en cada rebuild
# implicaría perder TODO el histórico de asistencia. Sub-3.1: preservamos
# esa subcarpeta moviéndola a un staging temporal antes del clean y
# restaurándola después de PyInstaller.
if (Test-Path 'build') {
    Write-Host '[INFO] Borrando build\ ...' -ForegroundColor Cyan
    Remove-Item -Recurse -Force 'build'
}

$DataDirRel    = 'dist\zkteco\data'
$DataStagedAt  = $null
if (Test-Path $DataDirRel) {
    # Movemos a un path temporal fuera de dist\ para que el Remove de dist\
    # no la toque. Restauramos después de que PyInstaller haya recreado
    # dist\zkteco\.
    $DataStagedAt = Join-Path $env:TEMP ("zkteco_data_backup_" + [System.Guid]::NewGuid().ToString('N'))
    Write-Host "[INFO] Preservando data\ en staging: $DataStagedAt" -ForegroundColor Cyan
    Move-Item -Path $DataDirRel -Destination $DataStagedAt
}

if (Test-Path 'dist') {
    Write-Host '[INFO] Borrando dist\ ...' -ForegroundColor Cyan
    Remove-Item -Recurse -Force 'dist'
}

# ── 5. Ejecutar PyInstaller ──────────────────────────────────────────────────
# --noconfirm: no preguntar antes de sobrescribir dist\zkteco\.
# El spec ya define modo --onedir, --windowed, icono, version_info, etc.
Write-Host '[INFO] Compilando con PyInstaller ...' -ForegroundColor Cyan
pyinstaller packaging\zkteco.spec --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] PyInstaller fallo. Revisa el log de arriba.' -ForegroundColor Red
    exit 1
}

if (-not (Test-Path 'dist\zkteco\zkteco.exe')) {
    Write-Host '[ERROR] dist\zkteco\zkteco.exe no se genero.' -ForegroundColor Red
    exit 1
}

# ── 5b. Restaurar data\ preservado ───────────────────────────────────────────
# Si en el paso 4 movimos data\ al staging, lo devolvemos ahora que dist\
# fue regenerado. Si no había data\ previa (primera build), no hay nada
# que restaurar.
if ($DataStagedAt -ne $null -and (Test-Path $DataStagedAt)) {
    Write-Host '[INFO] Restaurando data\ preservado ...' -ForegroundColor Cyan
    Move-Item -Path $DataStagedAt -Destination $DataDirRel
}

# ── 6. Leer version desde config.APP_VERSION ─────────────────────────────────
# Single source of truth: si el dev olvida actualizar version_info.txt, el
# zip al menos lleva la version correcta segun config.py.
$Version = (python -c "from config import APP_VERSION; print(APP_VERSION)").Trim()
if ([string]::IsNullOrWhiteSpace($Version)) {
    Write-Host '[ERROR] No se pudo leer APP_VERSION desde config.py.' -ForegroundColor Red
    exit 1
}
Write-Host "[INFO] Version detectada: $Version" -ForegroundColor Cyan

# ── 7. Empaquetar ZIP portable ───────────────────────────────────────────────
$ZipName = "zkteco-portable-v$Version.zip"
$ZipPath = Join-Path 'dist' $ZipName
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }

Write-Host "[INFO] Generando $ZipPath ..." -ForegroundColor Cyan
Compress-Archive -Path 'dist\zkteco\*' -DestinationPath $ZipPath -CompressionLevel Optimal

# ── 8. Resumen ───────────────────────────────────────────────────────────────
$ZipSize = [math]::Round((Get-Item $ZipPath).Length / 1MB, 2)
Write-Host ''
Write-Host '─── BUILD COMPLETADO ─────────────────────────────────────' -ForegroundColor Green
Write-Host "  Carpeta:     dist\zkteco\"                                -ForegroundColor Green
Write-Host "  Ejecutable:  dist\zkteco\zkteco.exe"                      -ForegroundColor Green
Write-Host "  ZIP:         $ZipPath ($ZipSize MB)"                      -ForegroundColor Green
Write-Host '──────────────────────────────────────────────────────────' -ForegroundColor Green
