"""Render perezoso de páginas por teselas, con caché LRU.

PyMuPDF no es seguro entre hilos, así que el render ocurre en el hilo de la
interfaz pero troceado: un temporizador procesa trabajos durante unos pocos
milisegundos y devuelve el control al bucle de eventos. Solo se renderiza
lo visible; mientras tanto se muestra una miniatura escalada.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

import pymupdf
from PySide6.QtCore import QObject, QRect, QTimer, Signal
from PySide6.QtGui import QColor, QImage

from . import theme
from .document import PdfDocument

TILE = 512
THUMB_WIDTH = 240
MAX_TILE_BYTES = 320 * 1024 * 1024
MAX_DISPLAY_LISTS = 32
MAX_THUMBS = 600
SLICE_SECONDS = 0.012


@dataclass(frozen=True)
class TileKey:
    page: int
    scale: int
    """Escala * 1000 redondeada: identifica el nivel de zoom."""
    tx: int
    ty: int
    night: bool


@dataclass
class Tile:
    image: QImage
    rect: QRect
    """Posición en píxeles de dispositivo dentro de la página."""


def invert_luminance(pix: pymupdf.Pixmap) -> None:
    """Invierte la luminosidad conservando el tono (el rojo sigue siendo rojo)."""
    native = getattr(pymupdf.mupdf, "fz_invert_pixmap_luminance", None)
    if native is not None:
        try:
            native(pix.this)
            return
        except Exception:
            pass
    pix.invert_irect(pix.irect)


def _rgb_int(color: QColor) -> int:
    return (color.red() << 16) | (color.green() << 8) | color.blue()


class PageRenderer(QObject):
    tileReady = Signal(int)
    thumbReady = Signal(int)

    def __init__(self, model: PdfDocument, parent: QObject | None = None):
        super().__init__(parent)
        self._model = model
        self._tiles: OrderedDict[TileKey, Tile] = OrderedDict()
        self._tile_bytes = 0
        self._lists: OrderedDict[int, pymupdf.DisplayList] = OrderedDict()
        self._thumbs: OrderedDict[tuple[int, bool], QImage] = OrderedDict()
        self._tile_queue: list[tuple[TileKey, float]] = []
        self._thumb_queue: list[tuple[int, bool, float]] = []
        self._thumb_dpr = 1.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._work)
        model.aboutToReplace.connect(self._drop_lists)
        model.changed.connect(self.invalidate)

    # ---------- caché ----------
    def _drop_lists(self) -> None:
        self._lists.clear()

    def invalidate(self, pages=None) -> None:
        if pages is None:
            self._tiles.clear()
            self._tile_bytes = 0
            self._lists.clear()
            self._thumbs.clear()
        else:
            targets = set(pages)
            for key in [k for k in self._tiles if k.page in targets]:
                tile = self._tiles.pop(key)
                self._tile_bytes -= tile.image.sizeInBytes()
            for page in targets:
                self._lists.pop(page, None)
            for key in [k for k in self._thumbs if k[0] in targets]:
                del self._thumbs[key]
        self._tile_queue.clear()
        self._thumb_queue.clear()

    def clear_tiles(self) -> None:
        self._tiles.clear()
        self._tile_bytes = 0

    def _display_list(self, index: int) -> pymupdf.DisplayList:
        dl = self._lists.get(index)
        if dl is not None:
            self._lists.move_to_end(index)
            return dl
        dl = self._model.doc[index].get_displaylist(annots=True)
        self._lists[index] = dl
        while len(self._lists) > MAX_DISPLAY_LISTS:
            self._lists.popitem(last=False)
        return dl

    # ---------- consultas ----------
    @staticmethod
    def scale_key(scale: float) -> int:
        return int(round(scale * 1000))

    def tile(self, key: TileKey) -> Tile | None:
        tile = self._tiles.get(key)
        if tile is not None:
            self._tiles.move_to_end(key)
        return tile

    def thumbnail(self, index: int, night: bool = False) -> QImage | None:
        image = self._thumbs.get((index, night))
        if image is not None:
            self._thumbs.move_to_end((index, night))
        return image

    def set_thumbnail_dpr(self, dpr: float) -> None:
        if abs(dpr - self._thumb_dpr) > 0.01:
            self._thumb_dpr = dpr
            self._thumbs.clear()

    # ---------- peticiones ----------
    def request_tiles(self, keys: list[tuple[TileKey, float]]) -> None:
        """Sustituye la cola de teselas (ordenadas por prioridad)."""
        self._tile_queue = [(k, s) for k, s in keys if k not in self._tiles]
        self._schedule()

    def request_thumbnail(self, index: int, night: bool = False, priority: bool = False) -> None:
        if (index, night) in self._thumbs:
            return
        entry = (index, night, time.monotonic())
        self._thumb_queue = [e for e in self._thumb_queue if e[:2] != (index, night)]
        if priority:
            self._thumb_queue.insert(0, entry)
        else:
            self._thumb_queue.append(entry)
        self._schedule()

    def _schedule(self) -> None:
        if (self._tile_queue or self._thumb_queue) and not self._timer.isActive():
            self._timer.start()

    # ---------- trabajo ----------
    def _work(self) -> None:
        deadline = time.perf_counter() + SLICE_SECONDS
        done_tiles: set[int] = set()
        done_thumbs: set[int] = set()
        count = self._model.page_count
        while time.perf_counter() < deadline and (self._tile_queue or self._thumb_queue):
            if self._tile_queue:
                key, scale = self._tile_queue.pop(0)
                if key in self._tiles or key.page >= count:
                    continue
                if self._render_tile(key, scale):
                    done_tiles.add(key.page)
            else:
                index, night, _ = self._thumb_queue.pop(0)
                if (index, night) in self._thumbs or index >= count:
                    continue
                self._render_thumbnail(index, night)
                done_thumbs.add(index)
        for page in done_tiles:
            self.tileReady.emit(page)
        for page in done_thumbs:
            self.thumbReady.emit(page)
        self._schedule()

    def _pixmap_to_image(self, pix: pymupdf.Pixmap, night: bool) -> QImage:
        if night:
            p = theme.current()
            invert_luminance(pix)
            pix.tint_with(_rgb_int(p.qcolor("night_page")), _rgb_int(p.qcolor("night_ink")))
        image = QImage(pix.samples_mv, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        return image.copy()

    def _render_tile(self, key: TileKey, scale: float) -> bool:
        try:
            dl = self._display_list(key.page)
        except Exception:
            return False
        bounds = dl.rect
        x0 = key.tx * TILE / scale
        y0 = key.ty * TILE / scale
        clip = pymupdf.Rect(x0, y0, x0 + TILE / scale, y0 + TILE / scale) & bounds
        if clip.is_empty:
            return False
        try:
            pix = dl.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, clip=clip)
        except Exception:
            return False
        image = self._pixmap_to_image(pix, key.night)
        tile = Tile(image, QRect(pix.x, pix.y, pix.width, pix.height))
        self._tiles[key] = tile
        self._tile_bytes += image.sizeInBytes()
        while self._tile_bytes > MAX_TILE_BYTES and len(self._tiles) > 1:
            _, old = self._tiles.popitem(last=False)
            self._tile_bytes -= old.image.sizeInBytes()
        return True

    def _render_thumbnail(self, index: int, night: bool) -> None:
        try:
            dl = self._display_list(index)
            rect = dl.rect
            scale = THUMB_WIDTH * self._thumb_dpr / max(rect.width, rect.height, 1)
            pix = dl.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        except Exception:
            return
        image = self._pixmap_to_image(pix, night)
        image.setDevicePixelRatio(self._thumb_dpr)
        self._thumbs[(index, night)] = image
        while len(self._thumbs) > MAX_THUMBS:
            self._thumbs.popitem(last=False)
