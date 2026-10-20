"""Idioma de la interfaz.

Los textos del código están en español; las traducciones se compilan en
``resources/i18n/blackpdf_<idioma>.qm`` (ver ``scripts/i18n.py``). Por
defecto se usa el idioma del sistema si está disponible y, si no, el inglés.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

SOURCE_LANGUAGE = "es"
LANGUAGES: tuple[tuple[str, str], ...] = (
    ("es", "Español"),
    ("en", "English"),
    ("pt", "Português"),
    ("fr", "Français"),
)
"""Código ISO 639-1 y nombre del idioma en ese mismo idioma."""

FALLBACK = "en"
TRANSLATIONS = Path(__file__).resolve().parent.parent / "resources" / "i18n"

_current = SOURCE_LANGUAGE
_installed: list[QTranslator] = []


def codes() -> list[str]:
    return [code for code, _ in LANGUAGES]


def system_language(ui_languages: list[str] | None = None) -> str:
    """Primer idioma del sistema que BlackPDF tiene traducido; si no, inglés."""
    available = codes()
    for name in ui_languages if ui_languages is not None else QLocale.system().uiLanguages():
        code = name.replace("_", "-").split("-")[0].lower()
        if code in available:
            return code
    return FALLBACK


def resolve(choice: str | None) -> str:
    """Idioma efectivo para una preferencia («auto» o un código)."""
    if choice in codes():
        return choice
    return system_language()


def current() -> str:
    return _current


def install(choice: str | None = None) -> str:
    """Carga las traducciones de BlackPDF y de Qt. Devuelve el código elegido."""
    global _current
    app = QCoreApplication.instance()
    for translator in _installed:
        app.removeTranslator(translator)
    _installed.clear()
    code = resolve(choice)
    if code != SOURCE_LANGUAGE:
        translator = QTranslator(app)
        if translator.load(str(TRANSLATIONS / f"blackpdf_{code}.qm")):
            app.installTranslator(translator)
            _installed.append(translator)
        else:
            code = SOURCE_LANGUAGE
    # Números y fechas: la variante regional del sistema si habla ese idioma
    # (es-MX, pt-PT, en-GB...); si no, la más extendida.
    system = QLocale.system()
    if system.name().split("_")[0] == code:
        locale = system
    else:
        locale = QLocale({"es": "es_ES", "en": "en_US", "pt": "pt_BR", "fr": "fr_FR"}.get(code, code))
    QLocale.setDefault(locale)
    # Textos propios de Qt (botones estándar, diálogos de archivo...). Qt
    # solo trae algunas variantes (pt_BR): se prueba la región y luego el idioma.
    qt = QTranslator(app)
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    for name in (locale.name(), {"pt": "pt_BR"}.get(code, code)):
        if qt.load(f"qtbase_{name}", folder):
            app.installTranslator(qt)
            _installed.append(qt)
            break
    _current = code
    return code


def language_name(code: str) -> str:
    return dict(LANGUAGES).get(code, code)
