# BUILD — BIO-DIPROMA (DIPROMA)

Guía paso a paso para compilar `zkteco.exe` portable en Windows.

> **Audiencia**: dev/IT responsable de producir un binario redistribuible
> para DIPROMA.
> **Desarrollado por**: Grupo Olympo.
> **Plataforma de compilación**: Windows 10 1803+ o Windows 11 (64-bit).
> **Output**: `dist\zkteco-portable-v0.1.0.zip` (~70–90 MB) listo para
> descomprimir y correr en cualquier PC Windows objetivo.

---

## 1. Prerrequisitos

| Requisito | Versión | Cómo obtenerlo |
|-----------|---------|----------------|
| Windows 64-bit | 10 1803+ o 11 | — |
| Python | 3.11.x o 3.12.x | https://www.python.org/downloads/windows/ — marcar **"Add Python to PATH"** durante la instalación. |
| Visual C++ Build Tools | 2019 o 2022 | https://visualstudio.microsoft.com/visual-cpp-build-tools/ — cargar el workload "Desktop development with C++". Necesario por algunas wheels nativas (bcrypt, etc.). |
| Git | cualquiera reciente | https://git-scm.com/download/win |
| Espacio en disco | ~2 GB libres | venv + cache pip + dist + zip |

Verificar que Python esté en PATH:

```powershell
python --version
# Esperado: Python 3.11.x  (o 3.12.x)
```

> Si `python` no se reconoce, reabrir la terminal después de instalar.
> Si tras reiniciar sigue sin reconocerse, agregar manualmente la ruta
> `C:\Users\<tu-usuario>\AppData\Local\Programs\Python\Python311\` y su
> subcarpeta `Scripts\` al PATH del sistema.

---

## 2. Primera compilación

Clonar el repo y entrar al directorio:

```powershell
git clone <url-del-repo> BIOMETRICO-DIPROMA
cd BIOMETRICO-DIPROMA
```

Correr el script de build (PowerShell):

```powershell
.\packaging\build.ps1
```

O en CMD:

```cmd
packaging\build.bat
```

El script hará automáticamente:

1. Validar que estás en la raíz del repo.
2. Crear el venv en `.\venv\` si no existe.
3. Activar el venv.
4. Instalar `requirements-dev.txt` (incluye PyInstaller).
5. Borrar `build\` y `dist\` previos.
6. Compilar con `pyinstaller packaging\zkteco.spec --noconfirm`.
7. Leer la versión desde `config.APP_VERSION`.
8. Empaquetar `dist\zkteco\` en `dist\zkteco-portable-v<VERSION>.zip`.

Tiempo esperado en la primera corrida: **5–10 min** (depende de la red al
instalar dependencias). Compilaciones siguientes: **1–3 min**.

---

## 3. Resultado

Al terminar, la carpeta `dist\` tendrá:

```
dist\
├── zkteco\
│   ├── zkteco.exe          <- ejecutable principal
│   ├── _internal\          <- DLLs, recursos, Python embebido
│   └── data\               <- (se crea en runtime al correr el .exe)
└── zkteco-portable-v0.1.0.zip   <- distribuible
```

El `.zip` es lo que se entrega al cliente. Descomprimir en cualquier
carpeta de la PC objetivo y ejecutar `zkteco.exe`.

---

## 4. Verificación post-build

Antes de distribuir, validar manualmente:

1. Doble click en `dist\zkteco\zkteco.exe`. Debe abrir la ventana de login
   sin mostrar consola CMD adicional (por `--windowed`).
2. Clic derecho sobre `zkteco.exe` → **Propiedades** → pestaña **Detalles**.
   Debe mostrar:
   - **Descripción del archivo**: BIO-DIPROMA — Sistema de Asistencia Biométrica
   - **Versión del producto**: 0.1.0.0
   - **Empresa**: DIPROMA
3. En el primer arranque, verificar que se cree `data\zkteco_app.db` junto
   al `.exe` (modo portable de Sub-6.1).
4. Pasar el `.zip` por VirusTotal (https://www.virustotal.com/) para
   confirmar que ningún AV mayor lo marque como falso positivo.

---

## 5. Distribución

Entregar al cliente únicamente `zkteco-portable-v<VERSION>.zip`. El
flujo de instalación en el equipo destino:

1. Copiar el `.zip` a la PC (USB, OneDrive, GitHub Release, etc.).
2. Clic derecho → **Propiedades** → marcar **"Desbloquear"** si Windows
   adjuntó la marca de zona descargada.
3. Extraer en una carpeta sin permisos de admin requeridos
   (recomendado: `C:\Olympo\zkteco\`).
4. Crear acceso directo a `zkteco.exe` en el escritorio.
5. En el primer arranque, el Setup Wizard pedirá crear el SUPERADMIN.

> **No** instalar dentro de `C:\Program Files\` — esa ruta requiere
> permisos de admin para escribir el archivo `data\zkteco_app.db`, y el
> modo portable está pensado para correr sin elevación.

---

## 6. Troubleshooting

### `.\packaging\build.ps1 : No se puede cargar el archivo … porque la ejecución de scripts está deshabilitada en este sistema`

Política de PowerShell bloqueando scripts no firmados. Dos opciones:

```powershell
# (a) Solo para esta sesión:
powershell -ExecutionPolicy Bypass -File .\packaging\build.ps1

# (b) Permanente para tu usuario:
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### `pyinstaller : El término 'pyinstaller' no se reconoce`

El venv no está activo o `requirements-dev.txt` no se instaló. El script
debería haberlo hecho — re-correr `.\packaging\build.ps1` desde la raíz
del repo. Si persiste, activar manualmente:

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

### Antivirus marca `zkteco.exe` como amenaza

Falso positivo común con binarios PyInstaller no firmados. Mitigaciones:

1. Confirmar en VirusTotal que solo motores menores lo marcan.
2. Agregar la carpeta `C:\Olympo\zkteco\` a la lista de exclusiones
   del AV institucional.
3. A futuro (Sub-6.4+): firmar el `.exe` con un certificado de code-signing
   para reducir falsos positivos.

UPX está deshabilitado intencionalmente en `zkteco.spec` justamente para
evitar agravar este problema.

### El `.exe` se cierra en silencio sin abrir ventana

`--windowed` oculta los traceback de Python. Para diagnosticar, correr
desde una terminal CMD:

```cmd
cd dist\zkteco
zkteco.exe
```

Si no aparece nada, recompilar temporalmente con `console=True` en
`packaging\zkteco.spec` (línea ~109) y volver a correr el build para ver
el traceback completo. **Restaurar `console=False` antes de hacer commit.**

### Build muy lento (> 15 min)

Causas frecuentes:

- Antivirus escaneando cada archivo dentro de `build\`. Excluir el repo
  de los escaneos en tiempo real.
- Disco mecánico o saturado. Mover el repo a SSD si es posible.
- Red lenta al descargar wheels la primera vez. Las siguientes corridas
  reutilizan el cache de pip y son más rápidas.

### `ImportError: No module named X` al correr el `.exe`

Algún import dinámico no fue capturado por `collect_submodules` en el
spec. Agregar el paquete al bloque `hidden` en `packaging\zkteco.spec`
y recompilar. Reportar el caso para incorporar el fix permanentemente.

---

## 7. Compilaciones siguientes

Tras la primera corrida, el venv y el cache de pip ya están listos. El
flujo se reduce a:

```powershell
git pull
.\packaging\build.ps1
```

El script reutiliza el venv existente, pero siempre limpia `build\` y
`dist\` para evitar artefactos contaminados.

---

## 8. Subir la versión

Cuando se libere una nueva versión, actualizar **los dos** lugares y
hacer un commit con el formato establecido:

1. `config.py` → `APP_VERSION = "X.Y.Z"`.
2. `packaging\version_info.txt` → actualizar `filevers`, `prodvers`,
   `FileVersion` y `ProductVersion` a `(X, Y, Z, 0)` y `"X.Y.Z.0"`.
3. Commit:

   ```
   chore(release): bump version to X.Y.Z
   ```

4. Tag opcional para distribuir:

   ```powershell
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push --tags
   ```

5. Recompilar — el script empaquetará automáticamente
   `dist\zkteco-portable-vX.Y.Z.zip`.

---

## 9. Soporte

Reportar fallas de build con:

- Versión exacta de Windows y Python (`python --version`).
- Log completo de la consola (PowerShell o CMD).
- Hash del commit que se intentó compilar (`git rev-parse HEAD`).

Contacto: Grupo Olympo — info@olympohn.com
