"""Integración con el sistema operativo (Windows y Linux)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget

IS_WINDOWS = sys.platform.startswith("win")
APP_USER_MODEL_ID = "BlackPDF.BlackPDF"


def set_app_user_model_id() -> None:
    """En Windows agrupa las ventanas en la barra de tareas con el icono propio."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def apply_title_bar_theme(widget: QWidget, dark: bool) -> None:
    """Barra de título oscura o clara en Windows 10/11 según el tema."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = wintypes.HWND(int(widget.winId()))
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (nuevo y antiguo)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
        caption = ctypes.c_int(0x0012_1010 if dark else 0x00FF_FFFF)  # COLORREF 0x00BBGGRR
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
    except Exception:
        pass


def reveal_in_folder(path: str | os.PathLike) -> None:
    """Abre la carpeta que contiene `path` y, en Windows, lo deja seleccionado."""
    target = Path(path)
    if IS_WINDOWS and target.exists():
        try:
            subprocess.Popen(["explorer", "/select,", str(target)])
            return
        except OSError:
            pass
    folder = target if target.is_dir() else target.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def open_with_system(path: str | os.PathLike) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
