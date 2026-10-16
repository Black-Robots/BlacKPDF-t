"""Capturas de todos los diálogos de herramientas (sin pantalla).

Uso: python scripts/screenshot_dialogs.py SALIDA_DIR PDF [--light]
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
    pdf = sys.argv[2]
    light = "--light" in sys.argv
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from blackpdf.ui import theme, tools
    from blackpdf.ui.dialogs.about import AboutDialog, ShortcutsDialog
    from blackpdf.ui.dialogs.common import PasswordDialog, PropertiesDialog
    from blackpdf.ui.dialogs.forms import FormFillDialog
    from blackpdf.ui.dialogs.signature import SignatureDialog
    from blackpdf.ui.main_window import MainWindow
    from blackpdf.core.document import document_info

    theme.load_fonts()
    theme.manager().apply("light" if light else "dark")

    def pump(ms: int = 500) -> None:
        done = []
        QTimer.singleShot(ms, lambda: done.append(1))
        while not done:
            app.processEvents()

    window = MainWindow()
    window.resize(1400, 880)
    window.show()
    window.open_files([pdf])
    pump(800)
    tab = window.current_tab()
    suffix = "_claro" if light else ""
    for item in tools.tool_specs():
        cls = tools.dialog_class(item.id)
        if cls is None:
            continue
        dialog = cls(window, window._context_for(tab), {})
        dialog.show()
        pump(500)
        dialog.grab().save(str(out / f"tool_{item.id}{suffix}.png"))
        dialog.close()
    extras = {
        "about": AboutDialog(window),
        "shortcuts": ShortcutsDialog(window),
        "password": PasswordDialog("secreto.pdf", True, window),
        "properties": PropertiesDialog(document_info(tab.document.doc), tab.document.path, window),
        "forms": FormFillDialog(tab.document, window),
        "signature": SignatureDialog(window),
    }
    for name, dialog in extras.items():
        dialog.show()
        pump(300)
        dialog.grab().save(str(out / f"dialog_{name}{suffix}.png"))
        dialog.close()
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
