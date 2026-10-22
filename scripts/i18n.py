"""Traducciones de la interfaz de BlackPDF.

    python scripts/i18n.py update            extrae los textos y actualiza translations/*.ts
    python scripts/i18n.py pending [FILE]    lista lo que falta por traducir (y lo deja en FILE como plantilla)
    python scripts/i18n.py fill [FILE]       aplica las traducciones de FILE y reutiliza las que ya existen
    python scripts/i18n.py build             compila los .ts en blackpdf/resources/i18n/*.qm
    python scripts/i18n.py check             textos sin traducir o con marcadores distintos

FILE es un JSON {"texto original": {"en": "...", "pt": "...", "fr": "..."}}. Lo que ya
está traducido igual en otro contexto se reutiliza solo; FILE solo necesita lo nuevo
(y lo que se quiera corregir: lo que trae FILE sustituye a lo que hubiera).

Los textos se extraen del código con el analizador de Python (no con
lupdate) para usar el mismo contexto que PySide6 en ejecución: la clase en
``self.tr()``, el contexto explícito de ``QCoreApplication.translate()`` y
``QT_TRANSLATE_NOOP()``, y el de las funciones auxiliares de módulo como
``def _tr(text): return QCoreApplication.translate("Tools", text)``.

Los .ts son el formato de Qt Linguist: se pueden editar con
``pyside6-linguist`` o con cualquier editor de texto.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "blackpdf"
TS_DIR = ROOT / "translations"
QM_DIR = SOURCES / "resources" / "i18n"
LANGUAGES = ("en", "pt", "fr")
SOURCE_LANGUAGE = "es"

PLACEHOLDER = re.compile(r"\{[^{}]*\}|%\d|%[sd]|\([A-Z0-9+]+\)$")


@dataclass
class Message:
    context: str
    source: str
    locations: list[tuple[str, int]] = field(default_factory=list)


def _literal(node) -> str | None:
    """Cadena literal (también la concatenación implícita "a" "b")."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _wrappers(tree: ast.Module) -> dict[str, str]:
    """Funciones de módulo que devuelven QCoreApplication.translate("Ctx", texto) o
    QT_TRANSLATE_NOOP("Ctx", texto)."""
    result = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.args.args:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Call):
                call = inner.value
                func = call.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name in ("translate", "QT_TRANSLATE_NOOP") and len(call.args) >= 2 and _literal(call.args[0]) is not None:
                    result[node.name] = _literal(call.args[0])
    return result


class _Extractor(ast.NodeVisitor):
    def __init__(self, path: str, wrappers: dict[str, str], messages: dict, problems: list[str]):
        self.path = path
        self.wrappers = wrappers
        self.messages = messages
        self.problems = problems
        self.classes: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append(node.name)
        self.generic_visit(node)
        self.classes.pop()

    def _add(self, context: str, node, text_node) -> None:
        text = _literal(text_node)
        if text is None:
            if isinstance(text_node, ast.JoinedStr):
                self.problems.append(f"{self.path}:{node.lineno}: f-string dentro de una traducción")
            return
        key = (context, text)
        message = self.messages.get(key)
        if message is None:
            message = self.messages[key] = Message(context, text)
        message.locations.append((self.path, node.lineno))

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "tr" and isinstance(func, ast.Attribute) and node.args:
            if self.classes:
                self._add(self.classes[-1], node, node.args[0])
            elif _literal(node.args[0]) is not None:
                self.problems.append(f"{self.path}:{node.lineno}: tr() fuera de una clase (usa translate)")
        elif name in ("translate", "QT_TRANSLATE_NOOP") and len(node.args) >= 2 and _literal(node.args[0]) is not None:
            self._add(_literal(node.args[0]), node, node.args[1])
        elif isinstance(func, ast.Name) and name in self.wrappers and node.args:
            self._add(self.wrappers[name], node, node.args[0])
        elif isinstance(func, ast.Name) and name == "tr" and self.classes and node.args:
            # Alias local «tr = self.tr» dentro de un método.
            self._add(self.classes[-1], node, node.args[0])
        self.generic_visit(node)


def extract() -> tuple[list[Message], list[str]]:
    messages: dict[tuple[str, str], Message] = {}
    problems: list[str] = []
    for path in sorted(SOURCES.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("blackpdf/core/"):
            continue  # el núcleo no depende de Qt: no tiene textos traducibles
        tree = ast.parse(path.read_text(encoding="utf-8"), relative)
        _Extractor(relative, _wrappers(tree), messages, problems).visit(tree)
    ordered = sorted(messages.values(), key=lambda m: (m.context, m.locations[0]))
    return ordered, problems


# -- archivos .ts ----------------------------------------------------------------------


def ts_path(language: str) -> Path:
    return TS_DIR / f"blackpdf_{language}.ts"


def read_ts(path: Path) -> dict[tuple[str, str], tuple[str, bool]]:
    """(contexto, original) -> (traducción, terminada)."""
    if not path.exists():
        return {}
    result = {}
    root = ET.parse(path).getroot()
    for context in root.iter("context"):
        name = context.findtext("name") or ""
        for message in context.iter("message"):
            source = message.findtext("source") or ""
            translation = message.find("translation")
            text = translation.text or "" if translation is not None else ""
            unfinished = translation is not None and translation.get("type") in ("unfinished", "vanished", "obsolete")
            result[(name, source)] = (text, bool(text) and not unfinished)
    return result


def write_ts(path: Path, language: str, messages: list[Message], translations: dict) -> None:
    root = ET.Element("TS", version="2.1", language=language, sourcelanguage=SOURCE_LANGUAGE)
    contexts: dict[str, ET.Element] = {}
    for message in messages:
        context = contexts.get(message.context)
        if context is None:
            context = contexts[message.context] = ET.SubElement(root, "context")
            ET.SubElement(context, "name").text = message.context
        node = ET.SubElement(context, "message")
        for file, line in message.locations:
            ET.SubElement(node, "location", filename="../" + file, line=str(line))
        ET.SubElement(node, "source").text = message.source
        text, done = translations.get((message.context, message.source), ("", False))
        translation = ET.SubElement(node, "translation")
        if text:
            translation.text = text
        if not done:
            translation.set("type", "unfinished")
    ET.indent(root, space="    ")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = ET.tostring(root, encoding="unicode")
    path.write_text('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n' + data + "\n", encoding="utf-8")


def placeholders(text: str) -> list[str]:
    return sorted(PLACEHOLDER.findall(text))


def check(messages: list[Message]) -> list[str]:
    problems = []
    for language in LANGUAGES:
        translations = read_ts(ts_path(language))
        missing = 0
        for message in messages:
            text, done = translations.get((message.context, message.source), ("", False))
            if not done:
                missing += 1
                continue
            if placeholders(text) != placeholders(message.source):
                problems.append(f"{language}: marcadores distintos en {message.context}: {message.source!r} -> {text!r}")
        if missing:
            problems.append(f"{language}: {missing} textos sin traducir")
    return problems


def update() -> int:
    messages, problems = extract()
    for problem in problems:
        print("aviso:", problem)
    for language in LANGUAGES:
        path = ts_path(language)
        write_ts(path, language, messages, read_ts(path))
        print(f"{path.relative_to(ROOT)}: {len(messages)} textos")
    return 0


def lrelease() -> str:
    found = shutil.which("pyside6-lrelease") or shutil.which("lrelease")
    if found:
        return found
    candidate = Path(sys.executable).with_name("pyside6-lrelease")
    if candidate.exists():
        return str(candidate)
    raise SystemExit("No se encuentra pyside6-lrelease (viene con PySide6).")


def build(output: Path = QM_DIR) -> int:
    output.mkdir(parents=True, exist_ok=True)
    tool = lrelease()
    for language in LANGUAGES:
        target = output / f"blackpdf_{language}.qm"
        subprocess.run([tool, "-silent", "-nounfinished", str(ts_path(language)), "-qm", str(target)], check=True)
        print(f"{target.relative_to(ROOT) if target.is_relative_to(ROOT) else target}")
    return 0


def _memory(current: dict) -> dict[str, dict[str, set[str]]]:
    """Traducciones ya terminadas de cada texto, en cualquier contexto."""
    memory: dict[str, dict[str, set[str]]] = {language: {} for language in LANGUAGES}
    for language in LANGUAGES:
        for (_context, source), (text, done) in current[language].items():
            if done and text:
                memory[language].setdefault(source, set()).add(text)
    return memory


def _resolve(new: dict) -> tuple[list[Message], dict, list[tuple[str, str]]]:
    """Aplica ``new`` y la memoria de traducciones; devuelve los .ts resultantes y lo que sigue faltando."""
    messages, _problems = extract()
    current = {language: read_ts(ts_path(language)) for language in LANGUAGES}
    memory = _memory(current)
    missing: list[tuple[str, str]] = []
    for language in LANGUAGES:
        for message in messages:
            key = (message.context, message.source)
            text, done = current[language].get(key, ("", False))
            proposal = (new.get(message.source) or {}).get(language, "")
            if not proposal:
                if done and text:
                    continue
                options = memory[language].get(message.source, set())
                proposal = next(iter(options)) if len(options) == 1 else ""
            if not proposal:
                missing.append((language, message.source))
                continue
            if placeholders(proposal) != placeholders(message.source):
                raise SystemExit(f"{language}: marcadores distintos: {message.source!r} -> {proposal!r}")
            current[language][key] = (proposal, True)
    return messages, current, missing


def pending(output: Path | None = None) -> int:
    _messages, _current, missing = _resolve({})
    sources = sorted({source for _language, source in missing})
    for source in sources:
        print(source)
    if output is not None:
        template = {source: {language: "" for language in LANGUAGES} for source in sources}
        output.write_text(json.dumps(template, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(sources)} textos por traducir" + (f" (plantilla en {output})" if output is not None else ""))
    return 0


def fill(path: Path | None = None) -> int:
    new = json.loads(path.read_text(encoding="utf-8")) if path is not None else {}
    messages, current, missing = _resolve(new)
    for language in LANGUAGES:
        write_ts(ts_path(language), language, messages, current[language])
    if missing:
        print("Faltan traducciones:")
        for language, source in missing:
            print(f"  [{language}] {source}")
        return 1
    print("Todo traducido.")
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    argument = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if command == "update":
        return update()
    if command == "pending":
        return pending(argument)
    if command == "fill":
        return fill(argument)
    if command == "build":
        return build()
    if command == "check":
        messages, problems = extract()
        problems += check(messages)
        for problem in problems:
            print(problem)
        return 1 if problems else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
