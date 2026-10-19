"""Compila la biblioteca nativa blackprc y la copia a blackpdf/native.

Uso:
    python native/prc/build.py                 # plataforma actual
    python native/prc/build.py --windows       # DLL de Windows con mingw-w64
    python native/prc/build.py --nanoprc DIR   # usar una copia local de nanoPRC
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUTPUT = ROOT / "blackpdf" / "native"

MINGW_TOOLCHAIN = """\
set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR x86_64)
set(CMAKE_C_COMPILER x86_64-w64-mingw32-gcc)
set(CMAKE_RC_COMPILER x86_64-w64-mingw32-windres)
set(CMAKE_FIND_ROOT_PATH /usr/x86_64-w64-mingw32)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--windows", action="store_true", help="compilar la DLL de Windows con mingw-w64")
    parser.add_argument("--nanoprc", type=Path, help="copia local de nanoPRC")
    parser.add_argument("--zlib", type=Path, help="copia local de zlib")
    parser.add_argument("--build-dir", type=Path, help="carpeta de compilación")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="carpeta de destino")
    args = parser.parse_args()

    if shutil.which("cmake") is None:
        print("Hace falta CMake 3.24 o posterior.", file=sys.stderr)
        return 1

    target = "windows" if args.windows or sys.platform == "win32" else sys.platform
    build = args.build_dir or ROOT / "build" / f"native-prc-{target}"
    build.mkdir(parents=True, exist_ok=True)

    configure = ["cmake", "-S", str(HERE), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release"]
    if args.nanoprc:
        configure.append(f"-DFETCHCONTENT_SOURCE_DIR_NANOPRC={args.nanoprc.resolve()}")
    if args.zlib:
        configure.append(f"-DFETCHCONTENT_SOURCE_DIR_ZLIB={args.zlib.resolve()}")
    if args.windows and sys.platform != "win32":
        toolchain = build / "mingw-w64.cmake"
        toolchain.write_text(MINGW_TOOLCHAIN, encoding="utf-8")
        configure.append(f"-DCMAKE_TOOLCHAIN_FILE={toolchain}")

    subprocess.run(configure, check=True)
    subprocess.run(["cmake", "--build", str(build), "--config", "Release", "--parallel"], check=True)

    names = ("blackprc.dll",) if target == "windows" else ("libblackprc.dylib",) if target == "darwin" else ("libblackprc.so",)
    built = [path for name in names for path in build.rglob(name)]
    if not built:
        print("No se encontró la biblioteca compilada.", file=sys.stderr)
        return 1
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / built[0].name
    shutil.copy2(built[0], destination)
    print(f"Biblioteca nativa: {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
