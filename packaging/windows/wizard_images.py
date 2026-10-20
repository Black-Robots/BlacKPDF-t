"""Imágenes del asistente de instalación (Inno Setup 6.6 o posterior).

- large-*: panel lateral de las páginas de bienvenida y final (logo, nombre y autor).
- small-*: icono de la esquina de las demás páginas.
- back-dark-* / back-light-*: fondo de todas las páginas, con los colores de la aplicación.

Se generan en los tamaños exactos que Inno Setup usa de 100 % a 250 % de escala de Windows,
para que se vean nítidas. Uso: python packaging/windows/wizard_images.py
"""

from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "wizard"
FONTS = ROOT / "blackpdf" / "resources" / "fonts"
LOGO = ROOT / "blackpdf" / "resources" / "app" / "blackpdf.svg"

# Tamaños del área de cada imagen según la documentación de Inno Setup 6.7 (escala: ancho, alto).
LARGE = {100: (202, 386), 125: (269, 515), 150: (336, 643), 175: (403, 772), 200: (430, 824),
         225: (498, 953), 250: (534, 1022)}
SMALL = {100: 58, 125: 77, 150: 97, 175: 116, 200: 124, 225: 143, 250: 159}
BACK = {100: (596, 432), 125: (796, 576), 150: (994, 720), 175: (1193, 864), 200: (1272, 922),
        225: (1471, 1066), 250: (1630, 1148)}

RED = "#E5484D"
# Superficies de blackpdf/ui/theme.py (oscuro y claro).
DARK_SURFACE = "#16161A"
LIGHT_SURFACE = "#FFFFFF"
FOLIO_PATH = ("M92 50 H146 L178 82 V194 a12 12 0 0 1 -12 12 H92 a12 12 0 0 1 -12 -12 V62 "
              "a12 12 0 0 1 12 -12 Z")


def rgb(value: str) -> tuple[float, float, float]:
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))


def mix(base: str, color: str, amount: float) -> str:
    """`color` sobre `base` con la opacidad `amount` (0-1)."""
    a, b = rgb(base), rgb(color)
    return "#" + "".join(f"{round((x + (y - x) * amount) * 255):02X}" for x, y in zip(a, b))


def folio(color: str, width: float = 12) -> pymupdf.Document:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">'
           f'<path d="{FOLIO_PATH}" fill="none" stroke="{color}" stroke-width="{width}" '
           f'stroke-linejoin="round" stroke-linecap="round"/></svg>')
    return pymupdf.open("pdf", pymupdf.open("svg", svg.encode()).convert_to_pdf())


def centered(page, font, text: str, size: float, y: float, color: str) -> None:
    writer = pymupdf.TextWriter(page.rect)
    writer.append(((page.rect.width - font.text_length(text, fontsize=size)) / 2, y), text, font=font, fontsize=size)
    writer.write_text(page, color=rgb(color))


def save(page, base: tuple[float, float], size: tuple[int, int], path: Path, alpha: bool = False) -> Path:
    matrix = pymupdf.Matrix(size[0] / base[0], size[1] / base[1])
    page.get_pixmap(matrix=matrix, alpha=alpha).save(path)
    return path


def large(scale: int) -> Path:
    """Panel lateral: negro con un degradado suave, el folio rojo, el nombre y el autor."""
    width, height = LARGE[100]
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    bands = 96
    for i in range(bands):
        top = height * i / bands
        page.draw_rect(pymupdf.Rect(0, top, width, top + height / bands + 0.6), color=None,
                       fill=rgb(mix("#0A0A0B", "#1A1A1F", i / (bands - 1))))
    page.draw_rect(pymupdf.Rect(0, 0, width, 3), color=None, fill=rgb(RED))
    side = 100
    top = 104
    page.show_pdf_page(pymupdf.Rect((width - side) / 2, top, (width + side) / 2, top + side), folio(RED), 0)
    semibold = pymupdf.Font(fontfile=str(FONTS / "Inter-SemiBold.ttf"))
    regular = pymupdf.Font(fontfile=str(FONTS / "Inter-Regular.ttf"))
    centered(page, semibold, "BlackPDF", 25, top + side + 38, "#FFFFFF")
    page.draw_rect(pymupdf.Rect(width / 2 - 12, top + side + 54, width / 2 + 12, top + side + 56.5), color=None,
                   fill=rgb(RED))
    centered(page, regular, "By Diego Rayado Zumel", 10, height - 26, "#8E8E99")
    return save(page, (width, height), LARGE[scale], OUT / f"large-{scale}.png")


def small(scale: int) -> Path:
    """Icono de la esquina: el de la aplicación, con margen y fondo transparente."""
    side = SMALL[100]
    doc = pymupdf.open()
    page = doc.new_page(width=side, height=side)
    margin = side * 0.08
    logo = pymupdf.open("pdf", pymupdf.open("svg", LOGO.read_bytes()).convert_to_pdf())
    page.show_pdf_page(pymupdf.Rect(margin, margin, side - margin, side - margin), logo, 0)
    return save(page, (side, side), (SMALL[scale], SMALL[scale]), OUT / f"small-{scale}.png", alpha=True)


def back(scale: int, dark: bool) -> Path:
    """Fondo de las páginas: el color de la aplicación, la línea roja arriba y un folio muy tenue."""
    width, height = BACK[100]
    surface = DARK_SURFACE if dark else LIGHT_SURFACE
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.draw_rect(page.rect, color=None, fill=rgb(surface))
    page.draw_rect(pymupdf.Rect(0, 0, width, 2.5), color=None, fill=rgb(RED))
    # Folio grande asomando por la esquina inferior izquierda, casi invisible: no estorba al texto.
    watermark = folio(mix(surface, RED, 0.08 if dark else 0.05), width=7)
    side = 330
    page.show_pdf_page(pymupdf.Rect(-96, height - side * 0.62, -96 + side, height + side * 0.38), watermark, 0)
    name = "dark" if dark else "light"
    return save(page, (width, height), BACK[scale], OUT / f"back-{name}-{scale}.png")


def file_list(prefix: str) -> str:
    """Valor para el .iss: todos los tamaños de una imagen separados por comas."""
    return ",".join(f"wizard\\{prefix}-{scale}.png" for scale in sorted(LARGE))


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    for scale in sorted(LARGE):
        large(scale)
        small(scale)
        back(scale, dark=True)
        back(scale, dark=False)
    for prefix in ("large", "small", "back-dark", "back-light"):
        print(file_list(prefix))
