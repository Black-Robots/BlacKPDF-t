"""Comprobación rápida de una instalación (`BlackPDF --self-test`).

Verifica que los recursos están empaquetados, que PyMuPDF renderiza y que
los subprocesos de trabajo arrancan desde el ejecutable. Devuelve 0 si todo
va bien. Pensado para validar las compilaciones de Windows y Linux.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def _report(lines: list[str], target: str | None) -> None:
    text = "\n".join(lines) + "\n"
    if target:
        Path(target).write_text(text, encoding="utf-8")
    if sys.stdout is not None:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except (OSError, ValueError):
            pass


def _signature_check(folder: Path, source: Path) -> bool:
    """Firma con un certificado de prueba y comprueba que la firma es íntegra."""
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    from .core.signatures import SignOptions, list_signatures, sign_pdf

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Prueba BlackPDF")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=2)).sign(key, hashes.SHA256()))
    p12 = folder / "prueba.p12"
    p12.write_bytes(pkcs12.serialize_key_and_certificates(
        b"prueba", key, cert, None, serialization.BestAvailableEncryption(b"x")))
    signed = folder / "firmado.pdf"
    sign_pdf(source, signed, p12, "x", SignOptions(visible=True))
    statuses = list_signatures(signed)
    return len(statuses) == 1 and statuses[0].intact and statuses[0].valid


def _gui_check() -> bool:
    """Crea la ventana principal sin pantalla, abre un PDF y renderiza una página."""
    import time

    import pymupdf
    from PySide6.QtWidgets import QApplication

    from PySide6.QtCore import QSettings

    app = QApplication.instance() or QApplication(["BlackPDF", "-platform", "offscreen"])
    from .ui import theme
    from .ui.main_window import MainWindow

    theme.load_fonts()
    theme.manager().apply("dark")
    with tempfile.TemporaryDirectory() as folder:
        # Preferencias en una carpeta temporal: la prueba no toca las del usuario.
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, folder)
        path = Path(folder) / "interfaz.pdf"
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Interfaz", fontsize=18)
        doc.save(path)
        doc.close()
        window = MainWindow()
        window.resize(1100, 760)
        window.show()
        window.open_files([str(path)])
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        tab = window.current_tab()
        ok = tab is not None and tab.renderer.thumbnail(0) is not None and not window.grab().isNull()
        if tab is not None:
            tab.document._modified = False
        window.close()
        app.processEvents()
        return ok


def run(target: str | None = None) -> int:
    lines: list[str] = []
    ok = True

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and condition
        lines.append(f"{'OK  ' if condition else 'FAIL'} {name}{(': ' + detail) if detail else ''}")

    try:
        import pymupdf

        from . import __version__
        from .core.ocr import available_languages
        from .core.text import FONTS_DIR
        from .procworker import run_blocking

        lines.append(f"BlackPDF {__version__} - PyMuPDF {pymupdf.VersionBind} - Python {sys.version.split()[0]}")
        resources = Path(__file__).resolve().parent / "resources"
        check("iconos", (resources / "icons" / "file-text.svg").exists())
        check("fuentes", (FONTS_DIR / "Inter-Regular.ttf").exists())
        check("icono de la aplicación", (resources / "app" / "blackpdf.svg").exists())
        check("idiomas OCR", "spa" in available_languages())

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "prueba.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((72, 72), "Prueba de BlackPDF", fontsize=18)
            doc.save(source)
            pix = doc[0].get_pixmap(dpi=72)
            check("render", pix.width > 0)
            output = Path(folder) / "salida.pdf"
            result = run_blocking("blackpdf.core.compress:compress_pdf", str(source), str(output), "medium")
            check("subproceso de trabajo", output.exists() and result.compressed_size > 0)
            check("firma digital", _signature_check(Path(folder), source))
    except Exception as exc:  # cualquier fallo debe quedar en el informe
        check("ejecución", False, repr(exc))
    if os.environ.get("BLACKPDF_SELFTEST_SKIP_GUI"):
        lines.append("SKIP interfaz")
    else:
        try:
            check("interfaz", _gui_check())
        except Exception as exc:
            check("interfaz", False, repr(exc))
    lines.append("RESULTADO: " + ("OK" if ok else "FALLO"))
    _report(lines, target)
    return 0 if ok else 1
