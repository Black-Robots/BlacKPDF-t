"""Preferencias persistentes del usuario (QSettings en formato INI)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, QStandardPaths

from .. import APP_NAME, ORGANIZATION

MAX_RECENTS = 12
MAX_SIGNATURES = 3


class Settings:
    def __init__(self):
        self._qs = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, ORGANIZATION, APP_NAME)

    # ---------- genéricos ----------
    def value(self, key: str, default=None):
        return self._qs.value(key, default)

    def set_value(self, key: str, value) -> None:
        self._qs.setValue(key, value)

    def _bool(self, key: str, default: bool) -> bool:
        value = self._qs.value(key, default)
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes")
        return bool(value)

    def _json(self, key: str, default):
        raw = self._qs.value(key, "")
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return default

    def _set_json(self, key: str, value) -> None:
        self._qs.setValue(key, json.dumps(value))

    def sync(self) -> None:
        self._qs.sync()

    # ---------- apariencia ----------
    @property
    def theme(self) -> str:
        value = str(self._qs.value("appearance/theme", "dark"))
        return value if value in ("dark", "light", "system") else "dark"

    @theme.setter
    def theme(self, value: str) -> None:
        self._qs.setValue("appearance/theme", value)

    @property
    def night_mode(self) -> bool:
        return self._bool("viewer/night", False)

    @night_mode.setter
    def night_mode(self, value: bool) -> None:
        self._qs.setValue("viewer/night", bool(value))

    @property
    def sidebar_visible(self) -> bool:
        return self._bool("viewer/sidebar", True)

    @sidebar_visible.setter
    def sidebar_visible(self, value: bool) -> None:
        self._qs.setValue("viewer/sidebar", bool(value))

    @property
    def annotation_bar_visible(self) -> bool:
        return self._bool("viewer/annotationBar", True)

    @annotation_bar_visible.setter
    def annotation_bar_visible(self, value: bool) -> None:
        self._qs.setValue("viewer/annotationBar", bool(value))

    @property
    def sidebar_width(self) -> int:
        try:
            return max(160, min(480, int(self._qs.value("viewer/sidebarWidth", 216))))
        except (TypeError, ValueError):
            return 216

    @sidebar_width.setter
    def sidebar_width(self, value: int) -> None:
        self._qs.setValue("viewer/sidebarWidth", int(value))

    # ---------- ventana ----------
    def window_geometry(self) -> QByteArray | None:
        value = self._qs.value("window/geometry")
        return value if isinstance(value, QByteArray) else None

    def set_window_geometry(self, value: QByteArray) -> None:
        self._qs.setValue("window/geometry", value)

    # ---------- recientes ----------
    def recent_files(self) -> list[str]:
        items = self._json("files/recent", [])
        return [p for p in items if isinstance(p, str)][:MAX_RECENTS]

    def add_recent(self, path: str | os.PathLike) -> None:
        path = str(Path(path).resolve())
        items = [p for p in self.recent_files() if os.path.normcase(p) != os.path.normcase(path)]
        items.insert(0, path)
        self._set_json("files/recent", items[:MAX_RECENTS])

    def remove_recent(self, path: str) -> None:
        self._set_json("files/recent", [p for p in self.recent_files() if p != path])

    def clear_recents(self) -> None:
        self._set_json("files/recent", [])

    @property
    def last_directory(self) -> str:
        default = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        value = str(self._qs.value("files/lastDirectory", default) or default)
        return value if Path(value).is_dir() else default

    @last_directory.setter
    def last_directory(self, value: str | os.PathLike) -> None:
        folder = Path(value)
        if folder.is_file():
            folder = folder.parent
        self._qs.setValue("files/lastDirectory", str(folder))

    # ---------- posición de lectura ----------
    def reading_position(self, path: str) -> dict | None:
        data = self._json("files/positions", {})
        entry = data.get(os.path.normcase(str(Path(path).resolve()))) if isinstance(data, dict) else None
        return entry if isinstance(entry, dict) else None

    def set_reading_position(self, path: str, page: int, zoom: float, fit: str) -> None:
        data = self._json("files/positions", {})
        if not isinstance(data, dict):
            data = {}
        key = os.path.normcase(str(Path(path).resolve()))
        data.pop(key, None)
        data[key] = {"page": int(page), "zoom": round(float(zoom), 4), "fit": fit}
        while len(data) > 200:
            data.pop(next(iter(data)))
        self._set_json("files/positions", data)

    # ---------- anotaciones ----------
    def tool_style(self, tool: str) -> dict:
        return self._json(f"annotate/{tool}", {})

    def set_tool_style(self, tool: str, style: dict) -> None:
        self._set_json(f"annotate/{tool}", style)

    def signatures(self) -> list[list[list[list[float]]]]:
        items = self._json("annotate/signatures", [])
        return [s for s in items if isinstance(s, list) and s][:MAX_SIGNATURES]

    def set_signatures(self, items: list) -> None:
        self._set_json("annotate/signatures", items[:MAX_SIGNATURES])

    @property
    def custom_colors(self) -> list[str]:
        return [c for c in self._json("annotate/customColors", []) if isinstance(c, str)][:8]

    @custom_colors.setter
    def custom_colors(self, values: list[str]) -> None:
        self._set_json("annotate/customColors", values[:8])


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
