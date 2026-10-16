"""Iconos Lucide (SVG) coloreados según el tema activo.

El color se resuelve en el momento de pintar, así que al cambiar de tema
basta con repintar: no hay que regenerar los iconos.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

ICONS_DIR = theme.RESOURCES / "icons"
STROKE_WIDTH = "1.75"

ROLE_DEFAULT = "default"
ROLE_MUTED = "muted"
ROLE_ACCENT = "accent"
ROLE_ON_ACCENT = "on-accent"
ROLE_TEXT = "text"
ROLE_SUCCESS = "success"
ROLE_DANGER = "danger"
ROLE_WARNING = "warning"


@lru_cache(maxsize=256)
def _svg_source(name: str) -> str:
    path = ICONS_DIR / f"{name}.svg"
    if not path.exists():
        path = ICONS_DIR / "file-text.svg"
    text = path.read_text(encoding="utf-8")
    if text.startswith("<!--"):
        text = text[text.index("-->") + 3 :]
    return text.replace('stroke-width="2"', f'stroke-width="{STROKE_WIDTH}"')


@lru_cache(maxsize=1024)
def _renderer(name: str, color: str) -> QSvgRenderer:
    svg = _svg_source(name).replace("currentColor", color)
    return QSvgRenderer(QByteArray(svg.encode("utf-8")))


def role_color(role: str, mode: QIcon.Mode = QIcon.Mode.Normal, state: QIcon.State = QIcon.State.Off) -> QColor:
    p = theme.current()
    if mode == QIcon.Mode.Disabled:
        return p.qcolor("text_faint")
    if role == ROLE_ON_ACCENT:
        return p.qcolor("accent_text")
    if role == ROLE_ACCENT or state == QIcon.State.On or mode == QIcon.Mode.Selected:
        return p.qcolor("accent")
    if role == ROLE_SUCCESS:
        return p.qcolor("success")
    if role == ROLE_DANGER:
        return p.qcolor("danger")
    if role == ROLE_WARNING:
        return p.qcolor("warning")
    if role == ROLE_MUTED:
        return p.qcolor("text_muted") if mode != QIcon.Mode.Active else p.qcolor("text")
    if role == ROLE_TEXT or mode == QIcon.Mode.Active:
        return p.qcolor("text")
    return p.qcolor("text_secondary")


class LucideIconEngine(QIconEngine):
    def __init__(self, name: str, role: str = ROLE_DEFAULT, color: QColor | None = None):
        super().__init__()
        self._name = name
        self._role = role
        self._color = QColor(color) if color is not None else None

    def _color_for(self, mode, state) -> str:
        if self._color is not None and mode != QIcon.Mode.Disabled:
            return self._color.name(QColor.NameFormat.HexArgb)
        return role_color(self._role, mode, state).name(QColor.NameFormat.HexArgb)

    def paint(self, painter: QPainter, rect: QRect, mode, state) -> None:
        renderer = _renderer(self._name, _css_color(self._color_for(mode, state)))
        side = min(rect.width(), rect.height())
        target = QRectF(rect.x() + (rect.width() - side) / 2, rect.y() + (rect.height() - side) / 2, side, side)
        renderer.render(painter, target)

    def pixmap(self, size: QSize, mode, state) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size: QSize, mode, state, scale: float) -> QPixmap:
        scale = max(scale, 1.0)
        width = max(1, round(size.width() * scale))
        height = max(1, round(size.height() * scale))
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint(painter, QRect(QPoint(0, 0), QSize(width, height)), mode, state)
        painter.end()
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(scale)
        return pixmap

    def actualSize(self, size: QSize, mode, state) -> QSize:
        return size

    def clone(self) -> "LucideIconEngine":
        return LucideIconEngine(self._name, self._role, self._color)

    def key(self) -> str:
        return "lucide"


def _css_color(argb_hex: str) -> str:
    """#AARRGGBB -> rgba(...) para el SVG."""
    color = QColor(argb_hex)
    if color.alpha() == 255:
        return color.name(QColor.NameFormat.HexRgb)
    return f"rgba({color.red()},{color.green()},{color.blue()},{color.alphaF():.3f})"


def icon(name: str, role: str = ROLE_DEFAULT, color: QColor | None = None) -> QIcon:
    return QIcon(LucideIconEngine(name, role, color))


def pixmap(name: str, size: int, role: str = ROLE_DEFAULT, dpr: float = 1.0, color: QColor | None = None) -> QPixmap:
    engine = LucideIconEngine(name, role, color)
    return engine.scaledPixmap(QSize(size, size), QIcon.Mode.Normal, QIcon.State.Off, dpr)


def exists(name: str) -> bool:
    return (ICONS_DIR / f"{name}.svg").exists()


APP_ICON = theme.RESOURCES / "app" / "blackpdf.svg"


def app_icon() -> QIcon:
    result = QIcon()
    folder = theme.RESOURCES / "app"
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        png = folder / f"blackpdf-{size}.png"
        if png.exists():
            result.addFile(str(png), QSize(size, size))
    if result.isNull() and APP_ICON.exists():
        result = QIcon(str(APP_ICON))
    return result


def app_logo(size: int, dpr: float = 1.0) -> QPixmap:
    renderer = QSvgRenderer(str(APP_ICON)) if APP_ICON.exists() else None
    side = max(1, round(size * dpr))
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    if renderer is not None and renderer.isValid():
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, side, side))
        painter.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(dpr)
    return result


def resource_path(*parts: str) -> Path:
    return theme.RESOURCES.joinpath(*parts)
