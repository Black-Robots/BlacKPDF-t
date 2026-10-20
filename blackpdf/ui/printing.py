"""Impresión con el diálogo del sistema."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QRectF, Qt
from PySide6.QtGui import QImage, QPageLayout, QPainter
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QApplication, QProgressDialog, QWidget

from .document import PdfDocument


def _tr(text: str) -> str:
    return QCoreApplication.translate("Printing", text)


MAX_PRINT_DPI = 300


def print_document(parent: QWidget, document: PdfDocument, current_page: int = 0) -> bool:
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setDocName(document.display_name)
    printer.setFromTo(1, document.page_count)
    first = document.page_size(0)
    printer.setPageOrientation(
        QPageLayout.Orientation.Landscape if first.width() > first.height() else QPageLayout.Orientation.Portrait
    )
    dialog = QPrintDialog(printer, parent)
    dialog.setWindowTitle(_tr("Imprimir"))
    dialog.setOption(QPrintDialog.PrintDialogOption.PrintPageRange, True)
    dialog.setOption(QPrintDialog.PrintDialogOption.PrintCurrentPage, True)
    dialog.setMinMax(1, document.page_count)
    if dialog.exec() != QPrintDialog.DialogCode.Accepted:
        return False

    if printer.printRange() == QPrinter.PrintRange.PageRange:
        pages = list(range(printer.fromPage() - 1, printer.toPage()))
    elif printer.printRange() == QPrinter.PrintRange.CurrentPage:
        pages = [current_page]
    else:
        pages = list(range(document.page_count))
    pages = [p for p in pages if 0 <= p < document.page_count]
    if not pages:
        return False

    dpi = min(MAX_PRINT_DPI, printer.resolution())
    progress = QProgressDialog(_tr("Preparando la impresión..."), _tr("Cancelar"), 0, len(pages), parent)
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(400)

    painter = QPainter()
    if not painter.begin(printer):
        return False
    try:
        for position, index in enumerate(pages):
            if progress.wasCanceled():
                printer.abort()
                return False
            progress.setValue(position)
            QApplication.processEvents()
            if position:
                printer.newPage()
            page = document.doc[index]
            pix = page.get_pixmap(dpi=dpi, alpha=False, annots=True)
            image = QImage(pix.samples_mv, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
            area = QRectF(painter.viewport())
            scale = min(area.width() / pix.width, area.height() / pix.height)
            w, h = pix.width * scale, pix.height * scale
            target = QRectF(area.left() + (area.width() - w) / 2, area.top() + (area.height() - h) / 2, w, h)
            painter.drawImage(target, image)
        progress.setValue(len(pages))
    finally:
        painter.end()
    return True


def page_image(document: PdfDocument, index: int, dpi: int = 150) -> QImage:
    pix = document.doc[index].get_pixmap(dpi=dpi, alpha=False, annots=True)
    return QImage(pix.samples_mv, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()

