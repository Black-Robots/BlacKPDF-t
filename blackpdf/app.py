"""Punto de entrada de BlackPDF."""

from __future__ import annotations

import multiprocessing
import os
import sys


def _arguments(argv: list[str]) -> list[str]:
    paths = []
    for arg in argv:
        if arg.startswith("-"):
            continue
        if arg.startswith("file://"):
            from urllib.parse import unquote, urlparse

            arg = unquote(urlparse(arg).path)
            if os.name == "nt" and arg.startswith("/") and len(arg) > 2 and arg[2] == ":":
                arg = arg[1:]
        paths.append(os.path.abspath(arg))
    return paths


def main() -> int:
    # Imprescindible en Windows y con PyInstaller: los subprocesos de trabajo
    # arrancan con este mismo ejecutable.
    multiprocessing.freeze_support()

    for arg in sys.argv[1:]:
        if arg == "--self-test" or arg.startswith("--self-test="):
            from .selftest import run

            return run(arg.partition("=")[2] or None)

    from PySide6.QtCore import QLibraryInfo, QLocale, Qt, QTranslator
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from . import APP_ID, APP_NAME, ORGANIZATION, __version__

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(ORGANIZATION)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(APP_ID)
    app.setStyle("Fusion")

    translator = QTranslator(app)
    if translator.load(QLocale("es_ES"), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)

    from .ui import icons, theme
    from .ui.errors import install_excepthook, setup_logging
    from .ui.platform import set_app_user_model_id
    from .ui.settings import settings
    from .ui.single_instance import SingleInstance

    setup_logging()
    install_excepthook()

    paths = _arguments(sys.argv[1:])
    instance = SingleInstance()
    standalone = "--new-window" in sys.argv
    if not standalone:
        if instance.send_to_running(paths):
            return 0
        instance.listen()

    set_app_user_model_id()
    from .core.annotations import set_default_author

    set_default_author(settings().author_name)
    theme.load_fonts()
    theme.manager().apply(settings().theme)
    app.setWindowIcon(icons.app_icon())

    from .ui.main_window import MainWindow

    window = MainWindow()
    instance.filesReceived.connect(window.open_and_raise)
    window.show()
    if paths:
        window.open_files(paths)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
