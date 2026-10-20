"""Reconocimiento de texto (OCR) para documentos escaneados.

MuPDF incluye el motor de Tesseract; solo hacen falta los datos de idioma.
BlackPDF trae español e inglés, y usa además los de una instalación de
Tesseract si existe (TESSDATA_PREFIX o rutas habituales).
"""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf

from .document import PdfSource, open_pdf, save_pdf
from .errors import InvalidOptionError, OcrUnavailableError
from .progress import ProgressCallback, report
from .ranges import parse_page_selection

BUNDLED_TESSDATA = Path(__file__).resolve().parent.parent / "resources" / "tessdata"
_SYSTEM_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tessdata",
    r"C:\Program Files (x86)\Tesseract-OCR\tessdata",
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tessdata",
    "/usr/local/share/tessdata",
    "/opt/homebrew/share/tessdata",
)

def tessdata_folders() -> list[Path]:
    folders = []
    env = os.environ.get("TESSDATA_PREFIX")
    if env:
        folders.append(Path(env))
    folders.append(BUNDLED_TESSDATA)
    folders.extend(Path(p) for p in _SYSTEM_CANDIDATES)
    return [f for f in folders if f.is_dir()]


def available_languages() -> dict[str, Path]:
    """Idioma -> carpeta tessdata que lo contiene (la primera encontrada)."""
    found: dict[str, Path] = {}
    for folder in tessdata_folders():
        for data in folder.glob("*.traineddata"):
            code = data.stem
            if code != "osd" and code not in found:
                found[code] = folder
    return found


def ocr_available() -> bool:
    return bool(available_languages())


def _tessdata_for(languages: list[str]) -> str:
    available = available_languages()
    missing = [code for code in languages if code not in available]
    if not languages or missing:
        raise OcrUnavailableError("idiomas no disponibles", missing=missing)
    folders = {available[code] for code in languages}
    if len(folders) == 1:
        return str(folders.pop())
    # Idiomas repartidos en varias carpetas: se usa la que contiene el primero.
    return str(available[languages[0]])


def page_has_text(page: pymupdf.Page, minimum: int = 20) -> bool:
    return len(page.get_text("text").strip()) >= minimum


def ocr_pdf(
    source: PdfSource,
    output: str | os.PathLike,
    *,
    languages: list[str] | None = None,
    dpi: int = 300,
    pages: str = "",
    skip_text_pages: bool = True,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    """Añade una capa de texto invisible a las páginas escaneadas.

    Las páginas que ya tienen texto se copian tal cual (si `skip_text_pages`).
    Las demás se sustituyen por la imagen de la página con el texto reconocido
    encima, del mismo tamaño que la original.
    """
    languages = languages or ["spa"]
    if not 72 <= dpi <= 600:
        raise InvalidOptionError("resolución", option="dpi")
    tessdata = _tessdata_for(languages)
    language = "+".join(languages)
    doc = open_pdf(source, password)
    out = pymupdf.open()
    try:
        selected = set(parse_page_selection(pages, doc.page_count, allow_empty_all=True))
        total = doc.page_count
        for index in range(total):
            report(progress, index, total)
            page = doc[index]
            if index not in selected or (skip_text_pages and page_has_text(page)):
                out.insert_pdf(doc, from_page=index, to_page=index)
                continue
            pix = page.get_pixmap(dpi=dpi, alpha=False, annots=True)
            ocr_doc = pymupdf.open("pdf", pix.pdfocr_tobytes(compress=True, language=language, tessdata=tessdata))
            try:
                new_page = out.new_page(width=page.rect.width, height=page.rect.height)
                new_page.show_pdf_page(new_page.rect, ocr_doc, 0, keep_proportion=False)
            finally:
                ocr_doc.close()
        toc = doc.get_toc(simple=True)
        if toc:
            out.set_toc(toc)
        report(progress, total, total)
        return save_pdf(out, output, garbage=4)
    finally:
        out.close()
        doc.close()
