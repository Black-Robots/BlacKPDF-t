"""Traducciones de la interfaz: completas, compiladas y cargadas en ejecución."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QLocale
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location("i18n_tool", ROOT / "scripts" / "i18n.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["i18n_tool"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return _tool()


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance
    from blackpdf.ui import i18n

    i18n.install("es")  # los demás tests esperan los textos originales


def test_texts_are_extracted_with_their_runtime_context(tool):
    messages, problems = tool.extract()
    assert not problems, problems
    contexts = {(m.context, m.source) for m in messages}
    assert ("Tools", "Unir PDF") in contexts  # función auxiliar _tr de módulo
    assert ("Keys", "Mayús") in contexts  # QT_TRANSLATE_NOOP
    assert ("MainWindow", "Idioma") in contexts  # self.tr()
    assert ("ShortcutsDialog", "Archivo") in contexts  # auxiliar que devuelve QT_TRANSLATE_NOOP


def test_every_text_is_translated_with_the_same_placeholders(tool):
    messages, _ = tool.extract()
    assert tool.check(messages) == []


def test_compiled_translations_are_up_to_date(tool, tmp_path):
    tool.build(tmp_path)
    for language in tool.LANGUAGES:
        built = (tmp_path / f"blackpdf_{language}.qm").read_bytes()
        shipped = (tool.QM_DIR / f"blackpdf_{language}.qm").read_bytes()
        assert built == shipped, f"ejecuta «python scripts/i18n.py build» ({language})"


def test_system_language_falls_back_to_english():
    from blackpdf.ui import i18n

    assert i18n.system_language(["pt-BR", "en-US"]) == "pt"
    assert i18n.system_language(["fr_CA"]) == "fr"
    assert i18n.system_language(["de-DE", "es-MX"]) == "es"
    assert i18n.system_language(["de-DE", "ja-JP"]) == "en"
    assert i18n.resolve("auto") == i18n.system_language()
    assert i18n.resolve("fr") == "fr"


@pytest.mark.parametrize("language, merge, menu, shift", [
    ("en", "Merge PDF", "Language", "Shift"),
    ("pt", "Juntar PDF", "Idioma", "Shift"),
    ("fr", "Fusionner des PDF", "Langue", "Maj"),
])
def test_translations_load_at_runtime(app, language, merge, menu, shift):
    from blackpdf.ui import i18n
    from blackpdf.ui.keys import shortcut_text
    from blackpdf.ui.tools import spec

    assert i18n.install(language) == language
    assert spec("merge").title == merge
    assert QCoreApplication.translate("MainWindow", "Idioma") == menu
    assert shortcut_text("Ctrl+Shift+S") == f"Ctrl+{shift}+S"
    assert QLocale().name().startswith(language)


def test_spanish_is_the_source(app):
    from blackpdf.ui import formatting, i18n

    i18n.install("es")
    assert QCoreApplication.translate("Tools", "Unir PDF") == "Unir PDF"
    assert formatting.percent(125) == "125 %"
    i18n.install("en")
    assert formatting.percent(125) == "125%"
    assert formatting.size(1536) == "1.5 KB"
