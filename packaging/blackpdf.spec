# -*- mode: python ; coding: utf-8 -*-
# Especificación de PyInstaller para BlackPDF (Windows y Linux).
# Uso: pyinstaller --noconfirm --clean packaging/blackpdf.spec

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT))
from blackpdf import __version__  # noqa: E402

IS_WINDOWS = sys.platform.startswith("win")

datas = [(str(ROOT / "blackpdf" / "resources"), "blackpdf/resources")]
datas += collect_data_files("pyhanko") + collect_data_files("pyhanko_certvalidator") + collect_data_files("certifi")

# Las herramientas se cargan por nombre en el subproceso de trabajo.
hiddenimports = (
    collect_submodules("blackpdf")
    + collect_submodules("pyhanko")
    + collect_submodules("pyhanko_certvalidator")
)

# Biblioteca nativa para PRC (nanoPRC), compilada con native/prc/build.py.
NATIVE = ROOT / "blackpdf" / "native" / ("blackprc.dll" if IS_WINDOWS else "libblackprc.so")
if not NATIVE.is_file():
    raise SystemExit(f"Falta {NATIVE}: compílala con «python native/prc/build.py»")
binaries = [(str(NATIVE), "blackpdf/native")]

excludes = [
    "tkinter", "unittest", "pydoc_data", "test", "PIL", "numpy",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtPdf",
    "PySide6.QtWebEngineCore", "PySide6.QtMultimedia", "PySide6.Qt3DCore",
]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)
IS_LINUX = sys.platform.startswith("linux")
if IS_LINUX:
    # Pila GTK (solo la usaba el tema de plataforma gtk3: el sistema ya la tiene)
    # y plataformas Qt que BlackPDF no necesita. Se conservan xcb y wayland.
    _drop = (
        "libqgtk3", "libgtk-3", "libgdk-3", "libatk-1.0", "libatk-bridge", "libatspi", "libcairo",
        "libpango", "libgdk_pixbuf", "libepoxy", "libthai", "libdatrie", "libpixman", "libXcomposite",
        "libXdamage", "libXinerama", "libqeglfs", "libqlinuxfb", "libqminimalegl", "libqvnc",
        "libqvkkhrdisplay", "libQt6EglFS", "libQt6EglFsKms", "egldeviceintegrations",
    )
    a.binaries = [entry for entry in a.binaries if not any(name in entry[0] for name in _drop)]

pyz = PYZ(a.pure)

version_file = None
if IS_WINDOWS:
    version_file = str(ROOT / "packaging" / "windows" / "version_info.txt")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BlackPDF",
    debug=False,
    strip=IS_LINUX,
    upx=False,
    console=False,
    icon=str(ROOT / "packaging" / "windows" / "blackpdf.ico") if IS_WINDOWS else None,
    version=version_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=IS_LINUX,
    upx=False,
    name="BlackPDF",
)
