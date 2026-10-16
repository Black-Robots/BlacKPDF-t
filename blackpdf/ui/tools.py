"""Catálogo de herramientas: tarjetas de inicio, menús y paleta de comandos."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication


def _tr(text: str) -> str:
    return QCoreApplication.translate("Tools", text)


@dataclass(frozen=True)
class ToolSpec:
    id: str
    title: str
    description: str
    icon: str
    category: str
    editor: bool = False
    """True si la herramienta trabaja dentro del editor (abre el documento)."""


def categories() -> list[tuple[str, str]]:
    return [
        ("organize", _tr("Organizar")),
        ("optimize", _tr("Optimizar")),
        ("convert", _tr("Convertir")),
        ("edit", _tr("Editar y firmar")),
        ("security", _tr("Seguridad")),
    ]


def tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec("merge", _tr("Unir PDF"), _tr("Combina varios PDF en uno, en el orden que quieras."), "combine", "organize"),
        ToolSpec("split", _tr("Dividir PDF"), _tr("Separa por rangos, cada N páginas o página a página."), "scissors", "organize"),
        ToolSpec("organize", _tr("Organizar páginas"), _tr("Reordena, gira, duplica o elimina páginas arrastrando."), "layout-grid", "organize", True),
        ToolSpec("alternate", _tr("Alternar páginas"), _tr("Intercala dos PDF. Ideal para escaneos a doble cara."), "shuffle", "organize"),
        ToolSpec("extract", _tr("Extraer páginas"), _tr("Crea un PDF nuevo solo con las páginas elegidas."), "file-output", "organize"),
        ToolSpec("rotate", _tr("Rotar PDF"), _tr("Gira todas las páginas o solo algunas."), "rotate-cw", "organize"),
        ToolSpec("compress", _tr("Comprimir PDF"), _tr("Reduce el tamaño manteniendo la mejor calidad posible."), "shrink", "optimize"),
        ToolSpec("ocr", _tr("Reconocer texto (OCR)"), _tr("Hace buscable y copiable el texto de documentos escaneados."), "scan-text", "optimize"),
        ToolSpec("to_images", _tr("PDF a imagen"), _tr("Convierte cada página en PNG o JPG."), "image", "convert"),
        ToolSpec("from_images", _tr("Imagen a PDF"), _tr("Convierte fotos y escaneos en un PDF."), "images", "convert"),
        ToolSpec("annotate", _tr("Anotar y dibujar"), _tr("Lápiz, resaltador, formas, texto y notas."), "pen-line", "edit", True),
        ToolSpec("sign", _tr("Firmar PDF"), _tr("Dibuja tu firma una vez y estámpala donde quieras."), "signature", "edit", True),
        ToolSpec("forms", _tr("Rellenar formulario"), _tr("Completa formularios PDF y, si quieres, aplánalos."), "text-cursor-input", "edit", True),
        ToolSpec("watermark", _tr("Marca de agua"), _tr("Estampa texto o una imagen con opacidad y giro."), "stamp", "edit"),
        ToolSpec("page_numbers", _tr("Números de página"), _tr("Numera las páginas con el formato que elijas."), "hash", "edit"),
        ToolSpec("crop", _tr("Recortar márgenes"), _tr("Elimina márgenes blancos de forma automática o manual."), "crop", "edit"),
        ToolSpec("resize", _tr("Cambiar tamaño"), _tr("Ajusta las páginas a A4, Carta y otros tamaños."), "scaling", "edit"),
        ToolSpec("flatten", _tr("Aplanar PDF"), _tr("Fija anotaciones y formularios en el contenido."), "layers", "edit"),
        ToolSpec("protect", _tr("Proteger PDF"), _tr("Cifra con contraseña AES de 256 bits."), "lock", "security"),
        ToolSpec("unlock", _tr("Desbloquear PDF"), _tr("Quita la contraseña y las restricciones."), "lock-open", "security"),
    ]


def spec(tool_id: str) -> ToolSpec | None:
    return next((t for t in tool_specs() if t.id == tool_id), None)


def dialog_class(tool_id: str):
    from .dialogs import convert, edit, ocr, organize, security

    return {
        "merge": organize.MergeDialog,
        "split": organize.SplitDialog,
        "alternate": organize.AlternateDialog,
        "extract": organize.ExtractDialog,
        "rotate": organize.RotateDialog,
        "compress": convert.CompressDialog,
        "to_images": convert.PdfToImagesDialog,
        "from_images": convert.ImagesToPdfDialog,
        "watermark": edit.WatermarkDialog,
        "page_numbers": edit.PageNumbersDialog,
        "crop": edit.CropDialog,
        "resize": edit.ResizeDialog,
        "flatten": edit.FlattenDialog,
        "protect": security.ProtectDialog,
        "unlock": security.UnlockDialog,
        "ocr": ocr.OcrDialog,
    }.get(tool_id)


def document_menu_entries() -> list[tuple[str, str, str]]:
    """Herramientas aplicables al documento abierto, para su menú."""
    order = ["compress", "ocr", "-", "watermark", "page_numbers", "crop", "resize", "flatten", "forms", "-",
             "split", "extract", "rotate", "merge", "alternate", "-", "to_images", "-", "protect", "unlock"]
    specs = {t.id: t for t in tool_specs()}
    entries = []
    for tool_id in order:
        if tool_id == "-":
            entries.append(("-", "", ""))
        else:
            item = specs[tool_id]
            entries.append((item.id, item.icon, item.title))
    return entries
