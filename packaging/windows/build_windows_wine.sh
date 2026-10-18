#!/usr/bin/env bash
# Compila BlackPDF para Windows desde Linux con Wine y genera el instalador.
# Requisitos: wine (64 bits) y xvfb-run. Uso: packaging/windows/build_windows_wine.sh
# En Windows nativo usa packaging/windows/build_windows.ps1 (mismo resultado).
set -euo pipefail

# Todo el proceso corre dentro de una única pantalla virtual (Xvfb).
if [ -z "${BLACKPDF_IN_XVFB:-}" ]; then
    export BLACKPDF_IN_XVFB=1
    exec xvfb-run -a -s "-screen 0 1280x1024x24" "$0" "$@"
fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
# Python 3.15 aún no funciona bajo Wine (strftime %OB); en Windows nativo se usa 3.15.
PY_VERSION="${PY_VERSION:-3.14.8}"
INNO_VERSION="${INNO_VERSION:-6.7.3}"
export WINEPREFIX="${WINEPREFIX:-$ROOT/build/wineprefix}"
export WINEARCH=win64
export WINEDEBUG=-all
# Sin Mono ni Gecko: no hacen falta y su aviso de instalación bloquearía el proceso.
export WINEDLLOVERRIDES="mscoree=;mshtml="
unset PYTHONPATH PYTHONHOME
CACHE="$ROOT/build/downloads"
mkdir -p "$CACHE" "$ROOT/packaging/out"

WINE="wine"
VERSION="$(python3 -c 'import re,sys; print(re.search(r"__version__ = \"(.+?)\"", open("blackpdf/__init__.py").read()).group(1))')"

if [ ! -f "$WINEPREFIX/system.reg" ]; then
    echo "==> Creando prefijo de Wine"
    wineboot --init
    wineserver -w
fi

PYEXE="$WINEPREFIX/drive_c/Python/python.exe"
if [ ! -f "$PYEXE" ]; then
    echo "==> Instalando Python $PY_VERSION para Windows"
    INSTALLER="$CACHE/python-$PY_VERSION-amd64.exe"
    [ -f "$INSTALLER" ] || curl -fsSL -o "$INSTALLER" "https://www.python.org/ftp/python/$PY_VERSION/python-$PY_VERSION-amd64.exe"
    $WINE "$INSTALLER" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0 Include_tcltk=0 \
        Include_doc=0 Include_launcher=0 Shortcuts=0 TargetDir='C:\Python'
    wineserver -w
fi

echo "==> Dependencias"
if ! $WINE 'C:\Python\python.exe' -m pip --version >/dev/null 2>&1; then
    $WINE 'C:\Python\python.exe' -m ensurepip --upgrade
fi
$WINE 'C:\Python\python.exe' -m pip install --disable-pip-version-check -q --upgrade pip
$WINE 'C:\Python\python.exe' -m pip install --disable-pip-version-check -q PySide6-Essentials pymupdf pyhanko pyinstaller

echo "==> PyInstaller"
if [ -n "${SKIP_PYINSTALLER:-}" ] && [ -f build/dist-win/BlackPDF/BlackPDF.exe ]; then
    echo "(se reutiliza build/dist-win)"
else
rm -rf build/dist-win build/work-win
$WINE 'C:\Python\python.exe' -m PyInstaller --noconfirm --clean \
    --distpath 'build\dist-win' --workpath 'build\work-win' 'packaging\blackpdf.spec'
wineserver -w
fi

echo "==> Autocomprobación"
# Wine no incluye icuuc.dll (ICU de Windows 10 1903+), que Qt 6 usa en Windows: la prueba
# de interfaz se hace en Windows real (GitHub Actions o build_windows.ps1).
export BLACKPDF_SELFTEST_SKIP_GUI=1
$WINE 'build\dist-win\BlackPDF\BlackPDF.exe' --self-test='build\selftest-win.txt' || true
wineserver -w
cat build/selftest-win.txt
grep -q "RESULTADO: OK" build/selftest-win.txt

ISCC="$WINEPREFIX/drive_c/InnoSetup/ISCC.exe"
if [ ! -f "$ISCC" ]; then
    echo "==> Instalando Inno Setup $INNO_VERSION"
    INNO="$CACHE/innosetup-$INNO_VERSION.exe"
    INNO_TAG="is-${INNO_VERSION//./_}"
    [ -f "$INNO" ] || curl -fsSL -o "$INNO" "https://github.com/jrsoftware/issrc/releases/download/$INNO_TAG/innosetup-$INNO_VERSION.exe"
    $WINE "$INNO" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /CURRENTUSER '/DIR=C:\InnoSetup'
    wineserver -w
fi

echo "==> Instalador"
(cd packaging/windows && $WINE "$ISCC" /Q "/DMyAppVersion=$VERSION" BlackPDF.iss)
wineserver -w
ls -lh packaging/out
