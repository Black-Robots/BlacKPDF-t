"""Comprimir PDF: recompresión de imágenes, subconjuntos de fuentes y limpieza."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .document import PdfSource, open_pdf, source_size
from .errors import WriteError
from .files import atomic_write
from .progress import ProgressCallback, report


class CompressionLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class _Profile:
    dpi_threshold: int | None
    dpi_target: int
    quality: int


_PROFILES = {
    # Solo limpieza estructural: sin pérdida de calidad.
    CompressionLevel.LOW: _Profile(dpi_threshold=None, dpi_target=0, quality=0),
    # Imágenes a 150 ppp y calidad JPEG 75: equilibrio para pantalla e impresión.
    CompressionLevel.MEDIUM: _Profile(dpi_threshold=180, dpi_target=150, quality=75),
    # Imágenes a 96 ppp y calidad 50: lo más pequeño posible para enviar.
    CompressionLevel.HIGH: _Profile(dpi_threshold=110, dpi_target=96, quality=50),
}


@dataclass(frozen=True)
class CompressionResult:
    path: Path
    original_size: int
    compressed_size: int

    @property
    def saved_ratio(self) -> float:
        """Fracción ahorrada (0.35 = 35 % más pequeño)."""
        if self.original_size <= 0:
            return 0.0
        return max(0.0, 1.0 - self.compressed_size / self.original_size)


def compress_bytes(
    source: PdfSource,
    level: CompressionLevel | str = CompressionLevel.MEDIUM,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> bytes:
    level = CompressionLevel(level)
    profile = _PROFILES[level]
    doc = open_pdf(source, password)
    try:
        report(progress, 0, 4)
        if profile.dpi_threshold is not None:
            try:
                doc.rewrite_images(
                    dpi_threshold=profile.dpi_threshold,
                    dpi_target=profile.dpi_target,
                    quality=profile.quality,
                    lossy=True,
                    lossless=True,
                    bitonal=False,
                    color=True,
                    gray=True,
                )
            except Exception:
                # Algunas imágenes exóticas no se pueden recodificar: se
                # mantiene el resto de la compresión.
                pass
        report(progress, 1, 4)
        try:
            doc.subset_fonts()
        except Exception:
            pass
        report(progress, 2, 4)
        data = doc.tobytes(
            garbage=4,
            clean=True,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
            use_objstms=1,
        )
        report(progress, 3, 4)
        return data
    finally:
        doc.close()


def compress_pdf(
    source: PdfSource,
    output: str | os.PathLike,
    level: CompressionLevel | str = CompressionLevel.MEDIUM,
    *,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> CompressionResult:
    """Comprime y guarda. Si el resultado no es menor, se conserva el original."""
    original_size = source_size(source)
    data = compress_bytes(source, level, password, progress)
    if original_size and len(data) >= original_size and password is None:
        original = _original_bytes(source)
        if original is not None:
            data = original
    try:
        path = atomic_write(output, lambda tmp: Path(tmp).write_bytes(data))
    except OSError as exc:
        raise WriteError(str(exc), path=str(output)) from exc
    report(progress, 4, 4)
    return CompressionResult(path=path, original_size=original_size, compressed_size=len(data))


def _original_bytes(source: PdfSource) -> bytes | None:
    data: bytes | None = None
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
    elif isinstance(source, (str, os.PathLike)):
        try:
            data = Path(source).read_bytes()
        except OSError:
            return None
    if data is not None and b"%PDF" in data[:1024]:
        return data
    return None
