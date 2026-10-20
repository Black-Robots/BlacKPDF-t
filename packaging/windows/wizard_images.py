"""Imágenes del asistente de instalación (Inno Setup): panel lateral con el logo e icono pequeño.

Se generan en varios tamaños (100 % a 250 % de escala de Windows) para que se vean nítidas.
Uso: python packaging/windows/wizard_images.py
"""

from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "wizard"
FONT = ROOT / "blackpdf" / "resources" / "fonts" / "Inter-SemiBold.ttf"
LOGO = ROOT / "blackpdf" / "resources" / "app" / "blackpdf.svg"

SCALES = (100, 125, 150, 175, 200, 250)
LARGE = (164, 314)
SMALL = 55

BACKGROUND = (10 / 255, 10 / 255, 11 / 255)
RED = (229 / 255, 72 / 255, 77 / 255)
FOLIO = """<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <path d="M92 50 H146 L178 82 V194 a12 12 0 0 1 -12 12 H92 a12 12 0 0 1 -12 -12 V62 a12 12 0 0 1 12 -12 Z"
        fill="none" stroke="#E5484D" stroke-width="12" stroke-linejoin="round" stroke-linecap="round"/>
</svg>"""


def _pdf_from_svg(source: bytes) -> pymupdf.Document:
    svg = pymupdf.open("svg", source)
    return pymupdf.open("pdf", svg.convert_to_pdf())


def large(scale: int) -> Path:
    """Panel lateral de las páginas de bienvenida y final: negro, folio rojo y el nombre."""
    width, height = LARGE
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.draw_rect(page.rect, color=None, fill=BACKGROUND)
    page.draw_rect(pymupdf.Rect(0, 0, width, 3), color=None, fill=RED)
    side = 92
    top = 92
    page.show_pdf_page(pymupdf.Rect((width - side) / 2, top, (width + side) / 2, top + side), _pdf_from_svg(FOLIO.encode()), 0)
    font = pymupdf.Font(fontfile=str(FONT))
    size = 22
    text = "BlackPDF"
    writer = pymupdf.TextWriter(page.rect)
    writer.append(((width - font.text_length(text, fontsize=size)) / 2, top + side + 34), text, font=font, fontsize=size)
    writer.write_text(page, color=(1, 1, 1))
    page.draw_rect(pymupdf.Rect(width / 2 - 12, top + side + 50, width / 2 + 12, top + side + 52.5), color=None, fill=RED)
    path = OUT / f"large-{scale}.png"
    page.get_pixmap(matrix=pymupdf.Matrix(scale / 100, scale / 100)).save(path)
    return path


def small(scale: int) -> Path:
    """Icono de la cabecera de las demás páginas: el icono de la aplicación con fondo transparente."""
    doc = pymupdf.open()
    page = doc.new_page(width=SMALL, height=SMALL)
    # Con margen: si no, el icono queda pegado al borde de la ventana.
    margin = SMALL * 0.09
    page.show_pdf_page(pymupdf.Rect(margin, margin, SMALL - margin, SMALL - margin), _pdf_from_svg(LOGO.read_bytes()), 0)
    path = OUT / f"small-{scale}.png"
    page.get_pixmap(matrix=pymupdf.Matrix(scale / 100, scale / 100), alpha=True).save(path)
    return path


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for scale in SCALES:
        print(large(scale), small(scale))
