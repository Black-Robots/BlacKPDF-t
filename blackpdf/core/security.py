"""Proteger con contraseña (AES-256) y desbloquear."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .document import PdfSource, open_pdf, read_file, save_pdf
from .errors import InvalidOptionError, NotPdfError, PasswordRequiredError, WrongPasswordError
from .progress import ProgressCallback, report


@dataclass(frozen=True)
class Permissions:
    print: bool = True
    copy: bool = True
    modify: bool = True
    annotate: bool = True
    forms: bool = True
    assemble: bool = True

    def is_unrestricted(self) -> bool:
        return all((self.print, self.copy, self.modify, self.annotate, self.forms, self.assemble))

    def to_flags(self) -> int:
        flags = pymupdf.PDF_PERM_ACCESSIBILITY
        if self.print:
            flags |= pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ
        if self.copy:
            flags |= pymupdf.PDF_PERM_COPY
        if self.modify:
            flags |= pymupdf.PDF_PERM_MODIFY
        if self.annotate:
            flags |= pymupdf.PDF_PERM_ANNOTATE
        if self.forms:
            flags |= pymupdf.PDF_PERM_FORM
        if self.assemble:
            flags |= pymupdf.PDF_PERM_ASSEMBLE
        return flags

    @classmethod
    def from_flags(cls, flags: int) -> "Permissions":
        return cls(
            print=bool(flags & pymupdf.PDF_PERM_PRINT),
            copy=bool(flags & pymupdf.PDF_PERM_COPY),
            modify=bool(flags & pymupdf.PDF_PERM_MODIFY),
            annotate=bool(flags & pymupdf.PDF_PERM_ANNOTATE),
            forms=bool(flags & pymupdf.PDF_PERM_FORM),
            assemble=bool(flags & pymupdf.PDF_PERM_ASSEMBLE),
        )


MIN_PASSWORD_LENGTH = 1


def encryption_options(
    user_password: str,
    owner_password: str = "",
    permissions: Permissions | None = None,
) -> dict:
    """Opciones de `Document.save` para cifrar con AES-256."""
    if len(user_password) < MIN_PASSWORD_LENGTH:
        raise InvalidOptionError("contraseña vacía", option="password")
    permissions = permissions or Permissions()
    if not owner_password:
        # Sin contraseña de propietario explícita: si hay restricciones se
        # genera una aleatoria para que no se puedan saltar con la de apertura.
        owner_password = user_password if permissions.is_unrestricted() else secrets.token_urlsafe(24)
    return {
        "encryption": pymupdf.PDF_ENCRYPT_AES_256,
        "user_pw": user_password,
        "owner_pw": owner_password,
        "permissions": permissions.to_flags(),
    }


def protect_pdf(
    source: PdfSource,
    output: str | os.PathLike,
    user_password: str,
    owner_password: str = "",
    permissions: Permissions | None = None,
    *,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    options = encryption_options(user_password, owner_password, permissions)
    doc = open_pdf(source, password)
    try:
        report(progress, 0, 1)
        path = save_pdf(doc, output, garbage=3, **options)
        report(progress, 1, 1)
        return path
    finally:
        doc.close()


def is_encrypted(source: str | os.PathLike | bytes) -> bool:
    data = read_file(source) if isinstance(source, (str, os.PathLike)) else bytes(source)
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            return bool(doc.is_encrypted or doc.needs_pass or doc.metadata.get("encryption"))
    except Exception as exc:
        raise NotPdfError(str(exc), path=str(source) if not isinstance(source, bytes) else "") from exc


def unlock_pdf(
    source: str | os.PathLike | bytes,
    output: str | os.PathLike,
    password: str = "",
    *,
    progress: ProgressCallback | None = None,
) -> Path:
    """Guarda una copia sin cifrado ni restricciones.

    Hace falta la contraseña si el PDF la pide para abrirse; los PDF que solo
    tienen restricciones (contraseña de propietario) se liberan sin ella.
    """
    label = str(source) if not isinstance(source, (bytes, bytearray)) else ""
    data = read_file(source) if isinstance(source, (str, os.PathLike)) else bytes(source)
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise NotPdfError(str(exc), path=label) from exc
    try:
        report(progress, 0, 1)
        if doc.needs_pass:
            if not password:
                raise PasswordRequiredError(path=label)
            if not doc.authenticate(password):
                raise WrongPasswordError(path=label)
        path = save_pdf(doc, output, garbage=3, encryption=pymupdf.PDF_ENCRYPT_NONE)
        report(progress, 1, 1)
        return path
    finally:
        doc.close()
