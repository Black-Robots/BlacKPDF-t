"""Errores previstos de las operaciones PDF.

Cada error lleva los datos necesarios para que la interfaz construya un
mensaje claro y traducido; nunca se muestra la traza al usuario.
"""

from __future__ import annotations


class BlackPdfError(Exception):
    """Base de todos los errores previstos."""

    def __init__(self, detail: str = "", **params):
        super().__init__(detail)
        self.detail = detail
        self.params = params


class FileOpenError(BlackPdfError):
    """No se pudo abrir o leer un archivo (params: path)."""


class NotPdfError(BlackPdfError):
    """El archivo no es un PDF ni un formato convertible (params: path)."""


class PasswordRequiredError(BlackPdfError):
    """El documento está cifrado y hace falta contraseña (params: path)."""


class WrongPasswordError(BlackPdfError):
    """La contraseña indicada no es correcta (params: path)."""


class InvalidRangeError(BlackPdfError):
    """El texto de rangos de páginas no es válido (params: spec, page_count)."""


class EmptyInputError(BlackPdfError):
    """No se ha indicado ningún archivo o página sobre la que trabajar."""


class InvalidOptionError(BlackPdfError):
    """Una opción tiene un valor fuera de rango (params: option)."""


class LastPageError(BlackPdfError):
    """Se intentó eliminar todas las páginas de un documento."""


class WriteError(BlackPdfError):
    """No se pudo escribir el archivo de salida (params: path)."""


class OcrUnavailableError(BlackPdfError):
    """Tesseract no está instalado o no se encuentran sus idiomas."""


class OperationCancelled(Exception):
    """El usuario canceló la operación en curso."""


class Model3DError(BlackPdfError):
    """El modelo 3D no se puede leer: formato desconocido o datos dañados (params: path)."""


class CadUnavailableError(BlackPdfError):
    """Falta el componente que lee STEP e IGES (gmsh) (params: path)."""


class Model3DEmptyError(BlackPdfError):
    """El modelo 3D se ha leído pero no contiene geometría visible (params: path)."""


class UpdateCheckError(BlackPdfError):
    """No se pudo consultar si hay versiones nuevas (sin conexión o GitHub no responde)."""


class UpdateDownloadError(BlackPdfError):
    """No se pudo descargar la versión nueva."""


class UpdateVerifyError(BlackPdfError):
    """La descarga no coincide con la suma SHA-256 publicada (o no hay suma con la que comprobarla)."""
