"""Comprobación rápida de una instalación (`BlackPDF --self-test`).

Verifica que los recursos están empaquetados, que PyMuPDF renderiza y que
los subprocesos de trabajo arrancan desde el ejecutable. Devuelve 0 si todo
va bien. Pensado para validar las compilaciones de Windows y Linux.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def _report(lines: list[str], target: str | None) -> None:
    text = "\n".join(lines) + "\n"
    if target:
        Path(target).write_text(text, encoding="utf-8")
    if sys.stdout is not None:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except (OSError, ValueError):
            pass


def run(target: str | None = None) -> int:
    lines: list[str] = []
    ok = True

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and condition
        lines.append(f"{'OK  ' if condition else 'FAIL'} {name}{(': ' + detail) if detail else ''}")

    try:
        import pymupdf

        from . import __version__
        from .core.ocr import available_languages
        from .core.text import FONTS_DIR
        from .procworker import run_blocking

        lines.append(f"BlackPDF {__version__} - PyMuPDF {pymupdf.VersionBind} - Python {sys.version.split()[0]}")
        resources = Path(__file__).resolve().parent / "resources"
        check("iconos", (resources / "icons" / "file-text.svg").exists())
        check("fuentes", (FONTS_DIR / "Inter-Regular.ttf").exists())
        check("icono de la aplicación", (resources / "app" / "blackpdf.svg").exists())
        check("idiomas OCR", "spa" in available_languages())

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "prueba.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((72, 72), "Prueba de BlackPDF", fontsize=18)
            doc.save(source)
            pix = doc[0].get_pixmap(dpi=72)
            check("render", pix.width > 0)
            output = Path(folder) / "salida.pdf"
            result = run_blocking("blackpdf.core.compress:compress_pdf", str(source), str(output), "medium")
            check("subproceso de trabajo", output.exists() and result.compressed_size > 0)
    except Exception as exc:  # cualquier fallo debe quedar en el informe
        check("ejecución", False, repr(exc))
    lines.append("RESULTADO: " + ("OK" if ok else "FALLO"))
    _report(lines, target)
    return 0 if ok else 1
