"""Firma digital con certificado: firmar, verificar y detectar manipulaciones."""

import datetime as dt
import shutil
import subprocess

import pymupdf
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from blackpdf.core.errors import InvalidOptionError
from blackpdf.core.signatures import (
    CertificateError,
    CertificatePasswordError,
    SignOptions,
    build_appearance,
    has_signatures,
    list_signatures,
    load_certificate,
    sign_pdf,
    stamp_rect,
)

from .conftest import build_pdf

PASSWORD = "clave segura"


def make_certificate(path, *, days_before=1, days_after=365, name="GARCÍA LÓPEZ DIEGO - 12345678Z"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "ES"),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, "IDCES-12345678Z"),
        x509.NameAttribute(NameOID.COMMON_NAME, name),
    ])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=days_before))
        .not_valid_after(now + dt.timedelta(days=days_after))
        .add_extension(x509.KeyUsage(True, True, False, False, False, False, False, False, False), critical=True)
        .sign(key, hashes.SHA256())
    )
    data = pkcs12.serialize_key_and_certificates(
        b"prueba", key, cert, None, serialization.BestAvailableEncryption(PASSWORD.encode())
    )
    path.write_bytes(data)
    return path


@pytest.fixture
def certificate(tmp_path):
    return make_certificate(tmp_path / "certificado.p12")


@pytest.fixture
def document(tmp_path):
    path = tmp_path / "contrato.pdf"
    build_pdf(3).save(path)
    return path


def test_load_certificate(certificate):
    info = load_certificate(certificate, PASSWORD)
    assert info.name == "GARCÍA LÓPEZ DIEGO - 12345678Z"
    assert info.serial == "IDCES-12345678Z"
    assert not info.expired and not info.not_yet_valid
    with pytest.raises(CertificatePasswordError):
        load_certificate(certificate, "otra")


def test_visible_signature_is_valid_and_covers_whole_file(certificate, document, tmp_path):
    output = tmp_path / "firmado.pdf"
    sign_pdf(document, output, certificate, PASSWORD, SignOptions(reason="Conforme", location="Madrid"))
    statuses = list_signatures(output)
    assert len(statuses) == 1
    status = statuses[0]
    assert status.intact and status.valid
    assert status.covers_whole_file, "nada después de la firma: ningún lector lo verá como modificado"
    assert status.signer == "GARCÍA LÓPEZ DIEGO - 12345678Z"
    assert status.reason == "Conforme" and status.location == "Madrid"
    assert not status.trusted and status.level == "warning", "certificado de prueba: no es de una CA de confianza"
    assert status.page == 2, "por defecto en la última página"
    page = pymupdf.open(output)[2]
    expected = stamp_rect(page, "bottom-right")
    assert status.rect[0] == pytest.approx(expected.x0, abs=1) and status.rect[3] == pytest.approx(expected.y1, abs=1)
    assert has_signatures(output.read_bytes())


@pytest.mark.skipif(shutil.which("pdfsig") is None, reason="Poppler no instalado")
def test_independent_validator_agrees(certificate, document, tmp_path):
    """Poppler (otra implementación) también considera la firma correcta y el documento intacto."""
    output = tmp_path / "firmado.pdf"
    sign_pdf(document, output, certificate, PASSWORD, SignOptions(visible=False))
    result = subprocess.run(["pdfsig", str(output)], capture_output=True, text=True)
    assert "Signature Validation: Signature is Valid." in result.stdout
    assert "Total document signed" in result.stdout or "total document signed" in result.stdout.lower()


def test_invisible_signature(certificate, document, tmp_path):
    output = tmp_path / "invisible.pdf"
    sign_pdf(document, output, certificate, PASSWORD, SignOptions(visible=False))
    status = list_signatures(output)[0]
    assert status.intact and status.valid and status.rect is None


def test_tampering_is_detected(certificate, document, tmp_path):
    output = tmp_path / "firmado.pdf"
    sign_pdf(document, output, certificate, PASSWORD)
    doc = pymupdf.open(output)
    doc[0].insert_text((72, 400), "Cláusula añadida después", fontsize=12)
    tampered = doc.tobytes()
    status = list_signatures(tampered)[0]
    assert not status.intact and status.level == "invalid"


def test_second_signature_keeps_first_valid(certificate, document, tmp_path):
    first = tmp_path / "una.pdf"
    second = tmp_path / "dos.pdf"
    sign_pdf(document, first, certificate, PASSWORD, SignOptions(position="bottom-left"))
    other = make_certificate(tmp_path / "otro.p12", name="ANA MARTÍN")
    sign_pdf(first, second, other, PASSWORD, SignOptions(position="bottom-right"))
    a, b = list_signatures(second)
    assert a.intact and a.valid and not a.covers_whole_file and a.changes_allowed
    assert b.intact and b.valid and b.covers_whole_file
    assert {a.signer, b.signer} == {"GARCÍA LÓPEZ DIEGO - 12345678Z", "ANA MARTÍN"}


def test_signature_on_rotated_and_cropped_page(certificate, tmp_path):
    doc = build_pdf(1)
    doc[0].set_cropbox(pymupdf.Rect(20, 30, 560, 800))
    doc[0].set_rotation(90)
    source = tmp_path / "girado.pdf"
    doc.save(source)
    output = tmp_path / "firmado.pdf"
    wanted = (100.0, 60.0, 330.0, 134.0)
    sign_pdf(source, output, certificate, PASSWORD, SignOptions(rect=wanted, page=0))
    status = list_signatures(output)[0]
    assert status.intact and status.valid
    for got, expected in zip(status.rect, wanted):
        assert got == pytest.approx(expected, abs=1.5)


def test_handwritten_appearance_and_unicode():
    data = build_appearance(230, 74, "Íñigo Muñoz", dt.datetime.now(dt.timezone.utc), "Conforme", "Logroño",
                           handwritten=[[(0, 0), (1, 0.4), (2, 0)]])
    page = pymupdf.open("pdf", data)[0]
    text = page.get_text()
    assert "Íñigo Muñoz" in text and "Logroño" in text
    assert page.get_drawings(), "incluye los trazos de la firma manuscrita"


def test_expired_certificate_is_refused(tmp_path, document):
    expired = make_certificate(tmp_path / "caducado.p12", days_before=400, days_after=-10)
    with pytest.raises(CertificateError):
        sign_pdf(document, tmp_path / "x.pdf", expired, PASSWORD)


def test_bad_options(certificate, document, tmp_path):
    with pytest.raises(InvalidOptionError):
        sign_pdf(document, tmp_path / "x.pdf", certificate, PASSWORD, SignOptions(page=9))
    with pytest.raises(InvalidOptionError):
        sign_pdf(document, tmp_path / "x.pdf", certificate, PASSWORD, SignOptions(rect=(0, 0, 5, 5)))


def test_unsigned_document(document):
    assert not has_signatures(document)
    assert list_signatures(document) == []


def test_signing_encrypted_pdf(certificate, tmp_path):
    from blackpdf.core.security import protect_pdf

    locked = tmp_path / "protegido.pdf"
    plain = tmp_path / "p.pdf"
    build_pdf(1).save(plain)
    protect_pdf(plain, locked, "abrir")
    output = tmp_path / "firmado.pdf"
    sign_pdf(locked, output, certificate, PASSWORD, password="abrir")
    assert list_signatures(output)[0].intact
