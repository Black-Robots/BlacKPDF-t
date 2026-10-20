"""Utilidades de rutas y archivos de salida."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, fallback: str = "documento") -> str:
    """Devuelve un nombre de archivo válido en Windows y Linux."""
    cleaned = _INVALID_CHARS.sub("_", name).strip(" .")
    return cleaned or fallback


def unique_path(path: str | os.PathLike) -> Path:
    """Si `path` existe, añade " (2)", " (3)"... antes de la extensión."""
    path = Path(path)
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def with_suffix_name(path: str | os.PathLike, label: str, extension: str = ".pdf") -> Path:
    """`informe.pdf` + "comprimido" -> `informe_comprimido.pdf` en la misma carpeta."""
    path = Path(path)
    return path.with_name(f"{path.stem}_{label}{extension}")


def human_size(num_bytes: int, decimal_point: str = ",") -> str:
    """Tamaño legible: 1,5 MB (o 1.5 MB con ``decimal_point="."``)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}".replace(".", decimal_point)
        size /= 1024
    return f"{size:.1f} GB".replace(".", decimal_point)


def _default_mode() -> int:
    if os.name == "nt":
        return 0o666
    current = os.umask(0)
    os.umask(current)
    return 0o666 & ~current


def atomic_write(path: str | os.PathLike, writer: Callable[[str], None]) -> Path:
    """Escribe mediante `writer(ruta_temporal)` y sustituye `path` de forma atómica.

    Así nunca queda un archivo a medias si algo falla durante la escritura,
    y se conservan los permisos del archivo original cuando existe.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else _default_mode()
    fd, tmp_name = tempfile.mkstemp(prefix=".blackpdf-", suffix=path.suffix, dir=path.parent)
    os.close(fd)
    try:
        writer(tmp_name)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path
