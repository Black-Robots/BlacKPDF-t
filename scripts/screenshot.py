"""Capturas de la interfaz sin pantalla, para revisar el diseño.

Uso: python scripts/screenshot.py SALIDA_DIR [PDF] [--light]
Genera inicio.png, documento.png y herramienta.png.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    pdf = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
    light = "--light" in sys.argv

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from blackpdf.ui import icons, theme
    from blackpdf.ui.main_window import MainWindow

    theme.load_fonts()
    theme.manager().apply("light" if light else "dark")
    app.setWindowIcon(icons.app_icon())
    window = MainWindow()
    window.resize(1400, 880)
    window.show()

    def pump(ms: int = 600) -> None:
        end = QTimer()
        end.setSingleShot(True)
        loop_done = []
        end.timeout.connect(lambda: loop_done.append(True))
        end.start(ms)
        while not loop_done:
            app.processEvents()

    pump()
    suffix = "_claro" if light else ""
    window.grab().save(str(out / f"inicio{suffix}.png"))
    if pdf:
        window.open_files([pdf])
        pump(1500)
        window.grab().save(str(out / f"documento{suffix}.png"))
        tab = window.current_tab()
        tab.set_tool("pen")
        pump(300)
        tab.find()
        tab.search_bar.edit.setText("PDF")
        pump(900)
        window.grab().save(str(out / f"busqueda{suffix}.png"))
        from blackpdf.ui import tools

        dialog_class = tools.dialog_class("watermark")
        dialog = dialog_class(window, window._context_for(tab), {})
        dialog.show()
        pump(900)
        dialog.grab().save(str(out / f"marca_agua{suffix}.png"))
        dialog.close()
        dialog = tools.dialog_class("compress")(window, window._context_for(tab), {})
        dialog.show()
        pump(400)
        dialog.grab().save(str(out / f"comprimir{suffix}.png"))
        dialog.close()
    window.palette_overlay.open(window._commands())
    pump(400)
    window.grab().save(str(out / f"paleta{suffix}.png"))
    for tab in window.document_tabs():
        tab.document._modified = False
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
