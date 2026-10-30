"""Shared application print-preview dialog and page-selection helpers."""
import re

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QPageLayout, QPainter
from PySide6.QtPrintSupport import (
    QPrintDialog,
    QPrintPreviewDialog,
    QPrintPreviewWidget,
    QPrinter,
    QPrinterInfo,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QToolBar,
    QWidget,
)
from busy_overlay import BusyOverlay


def create_print_job_printer(preview_printer):
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPrinterName(preview_printer.printerName())
    printer.setPageLayout(preview_printer.pageLayout())
    printer.setResolution(preview_printer.resolution())
    printer.setColorMode(preview_printer.colorMode())
    printer.setDuplex(preview_printer.duplex())
    printer.setPaperSource(preview_printer.paperSource())
    printer.setPageOrder(preview_printer.pageOrder())
    printer.setCopyCount(preview_printer.copyCount())
    printer.setCollateCopies(preview_printer.collateCopies())
    printer.setFullPage(preview_printer.fullPage())
    printer.setPrintRange(QPrinter.PrintRange.AllPages)
    return printer


def selected_page_numbers(page_count, selection_mode, page_range=""):
    if page_count < 1:
        raise ValueError("لا توجد صفحات جاهزة للطباعة.")
    if selection_mode == "all":
        page_numbers = list(range(1, page_count + 1))
    elif selection_mode == "range":
        normalized_range = str(page_range).translate(
            str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
        )
        parts = normalized_range.split(",")
        if not normalized_range.strip() or any(
            not part.strip() for part in parts
        ):
            raise ValueError(
                "أدخل أرقام الصفحات أو نطاقًا صالحًا، مثل 1-2,4."
            )
        page_numbers = []
        for part in parts:
            bounds = [bound.strip() for bound in part.split("-")]
            if len(bounds) == 1:
                try:
                    start = end = int(bounds[0])
                except ValueError as exc:
                    raise ValueError(
                        f"رقم الصفحة غير صالح: {bounds[0]}"
                    ) from exc
            elif len(bounds) == 2:
                try:
                    start, end = (int(bound) for bound in bounds)
                except ValueError as exc:
                    raise ValueError(
                        f"نطاق الصفحات غير صالح: {part}"
                    ) from exc
                if start > end:
                    raise ValueError(
                        f"يجب أن يبدأ النطاق من الصفحة الأصغر: {part}"
                    )
            else:
                raise ValueError(f"نطاق الصفحات غير صالح: {part}")
            if start < 1 or end > page_count:
                raise ValueError(
                    f"يجب أن تكون الصفحات بين 1 و{page_count}."
                )
            page_numbers.extend(range(start, end + 1))
        page_numbers = list(dict.fromkeys(page_numbers))
    elif selection_mode == "odd":
        page_numbers = list(range(1, page_count + 1, 2))
    elif selection_mode == "even":
        page_numbers = list(range(2, page_count + 1, 2))
    elif selection_mode == "reverse":
        page_numbers = list(range(page_count, 0, -1))
    else:
        raise ValueError("نوع تحديد الصفحات غير معروف.")

    if not page_numbers:
        raise ValueError("لا توجد صفحات مطابقة للطباعة.")
    return page_numbers


def apply_preview_zoom(preview_widget, zoom_text):
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", str(zoom_text))
    if match is None:
        return False
    zoom_factor = float(match.group(1).replace(",", ".")) / 100
    if zoom_factor <= 0:
        return False
    preview_widget.setZoomMode(
        QPrintPreviewWidget.ZoomMode.CustomZoom
    )
    preview_widget.setZoomFactor(zoom_factor)
    return True


def show_application_print_preview(
    parent,
    printer,
    page_count,
    paint_pages,
    orientation=QPageLayout.Orientation.Portrait,
    title="معاينة الطباعة",
):
    if page_count < 1:
        raise ValueError("لا توجد صفحات جاهزة للطباعة.")
    printer.setPageOrientation(orientation)
    printer.setFullPage(True)
    preview_dialog = QPrintPreviewDialog(printer, parent)
    preview_dialog.setObjectName("applicationPrintPreviewDialog")
    preview_dialog.setWindowTitle(title)
    preview_dialog.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    preview_dialog.setSizeGripEnabled(True)
    preview_dialog.setStyleSheet(
        "QDialog#applicationPrintPreviewDialog { background:#EAF0F6; }"
        "QDialog#applicationPrintPreviewDialog QToolBar {"
        "background:#FFFFFF; border:none;"
        "border-bottom:1px solid #DCE4EE; spacing:6px; padding:8px; }"
        "QDialog#applicationPrintPreviewDialog QToolButton {"
        "background:transparent; color:#334155;"
        "border:1px solid transparent; border-radius:6px;"
        "padding:5px; min-width:30px; min-height:30px; }"
        "QDialog#applicationPrintPreviewDialog QToolButton:hover {"
        "background:#EFF6FF; border-color:#BFDBFE; color:#1D4ED8; }"
        "QDialog#applicationPrintPreviewDialog QToolButton:checked {"
        "background:#DBEAFE; border-color:#93C5FD; color:#1E40AF; }"
        "QDialog#applicationPrintPreviewDialog QComboBox,"
        "QDialog#applicationPrintPreviewDialog QLineEdit {"
        "background:#FFFFFF; color:#1E293B;"
        "border:1px solid #CBD5E1; border-radius:6px;"
        "padding:4px 8px; min-height:30px; }"
        "QDialog#applicationPrintPreviewDialog QAbstractScrollArea,"
        "QDialog#applicationPrintPreviewDialog QPrintPreviewWidget {"
        "background:#CBD5E1; border:none; }"
    )
    screen = preview_dialog.screen() or QApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        width = max(1, available.width())
        height = max(1, available.height())
        preview_dialog.setMinimumSize(min(800, width), min(600, height))
        preview_dialog.resize(
            min(1200, round(width * 0.94)),
            min(850, round(height * 0.9)),
        )
        geometry = preview_dialog.geometry()
        geometry.moveCenter(available.center())
        preview_dialog.setGeometry(geometry)

    preview_widget = preview_dialog.findChild(QPrintPreviewWidget)
    preview_layout = preview_dialog.layout()
    if preview_widget is None or preview_layout is None:
        raise RuntimeError("تعذر تجهيز أدوات معاينة الطباعة.")

    printer_combo = QComboBox(preview_dialog)
    printer_combo.setObjectName("applicationPrintPreviewPrinterCombo")
    available_printers = QPrinterInfo.availablePrinters()
    if available_printers:
        for info in available_printers:
            printer_combo.addItem(info.printerName(), info.printerName())
        selected_index = printer_combo.findData(printer.printerName())
        if selected_index >= 0:
            printer_combo.setCurrentIndex(selected_index)
        elif printer.printerName():
            printer_combo.insertItem(
                0, printer.printerName(), printer.printerName()
            )
            printer_combo.setCurrentIndex(0)
    else:
        printer_combo.addItem(
            printer.printerName() or "الطابعة الافتراضية",
            printer.printerName(),
        )

    page_selection = QComboBox(preview_dialog)
    page_selection.setObjectName("applicationPrintPreviewPageSelection")
    for label, mode in (
        ("كل الصفحات", "all"),
        ("نطاق صفحات", "range"),
        ("الصفحات الفردية", "odd"),
        ("الصفحات الزوجية", "even"),
        ("عكس ترتيب الصفحات", "reverse"),
    ):
        page_selection.addItem(label, mode)
    page_range = QLineEdit(preview_dialog)
    page_range.setObjectName("applicationPrintPreviewPageRange")
    page_range.setPlaceholderText("مثال: 1-2,4")
    page_range.setToolTip(
        "أدخل صفحات منفصلة بفاصلة أو نطاقًا مثل 1-2,4."
    )
    page_range.setEnabled(False)
    page_selection.currentIndexChanged.connect(
        lambda index: page_range.setEnabled(
            page_selection.itemData(index) == "range"
        )
    )
    properties_button = QPushButton("خصائص الطابعة", preview_dialog)
    properties_button.setObjectName(
        "applicationPrintPreviewPrinterProperties"
    )
    properties_button.setProperty("actionType", "edit")
    print_button = QPushButton("طباعة", preview_dialog)
    print_button.setObjectName("applicationPrintPreviewDirectPrint")
    print_button.setProperty("actionType", "print")
    print_button.setProperty("primary", True)

    options = QWidget(preview_dialog)
    options.setObjectName("applicationPrintPreviewOptions")
    options_layout = QHBoxLayout(options)
    options_layout.setContentsMargins(10, 8, 10, 8)
    options_layout.setSpacing(8)
    options_layout.addWidget(QLabel("الطابعة:", options))
    options_layout.addWidget(printer_combo, 1)
    options_layout.addWidget(properties_button)
    options_layout.addSpacing(12)
    options_layout.addWidget(QLabel("الصفحات:", options))
    options_layout.addWidget(page_selection)
    options_layout.addWidget(page_range)
    options_layout.addWidget(QLabel(f"من {page_count} صفحة", options))
    options_layout.addWidget(print_button)
    preview_layout.insertWidget(0, options)

    for toolbar in preview_dialog.findChildren(QToolBar):
        for action in toolbar.actions():
            action_text = f"{action.text()} {action.toolTip()}".casefold()
            if (
                action.objectName() == "printAction"
                or "print" in action_text
                or "طباعة" in action_text
                or "portrait" in action_text
                or "landscape" in action_text
                or "عمودي" in action_text
                or "أفقي" in action_text
                or action.objectName() in ("portraitAction", "landscapeAction")
            ):
                toolbar.removeAction(action)

    def select_printer(index):
        printer_name = printer_combo.itemData(index)
        if printer_name:
            printer.setPrinterName(str(printer_name))
            printer.setPageOrientation(orientation)
            printer.setFullPage(True)
            preview_widget.updatePreview()

    printer_combo.currentIndexChanged.connect(select_printer)

    def show_printer_properties():
        dialog = QPrintDialog(printer, preview_dialog)
        dialog.setWindowTitle("خصائص الطابعة")
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_index = printer_combo.findData(printer.printerName())
            if selected_index >= 0:
                printer_combo.setCurrentIndex(selected_index)
            printer.setPageOrientation(orientation)
            printer.setFullPage(True)
            preview_widget.updatePreview()

    properties_button.clicked.connect(show_printer_properties)

    def direct_print():
        try:
            page_numbers = selected_page_numbers(
                page_count,
                page_selection.currentData(),
                page_range.text(),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(
                preview_dialog,
                "تعذرت الطباعة",
                f"حدث خطأ أثناء الطباعة:\n{exc}",
            )
            return

        print_button.setEnabled(False)
        overlay = BusyOverlay(preview_dialog)
        error_message = None
        try:
            print_printer = create_print_job_printer(printer)
            paint_pages(print_printer, page_numbers)
        except (OSError, ValueError, RuntimeError) as exc:
            error_message = f"حدث خطأ أثناء الطباعة:\n{exc}"
        finally:
            overlay.close()
            print_button.setEnabled(True)

        if error_message is not None:
            QMessageBox.critical(
                preview_dialog, "تعذرت الطباعة", error_message
            )

    print_button.clicked.connect(direct_print)
    for combo in preview_dialog.findChildren(QComboBox):
        if any("%" in combo.itemText(index) for index in range(combo.count())):
            combo.activated.connect(
                lambda _index, zoom_combo=combo: apply_preview_zoom(
                    preview_widget, zoom_combo.currentText()
                )
            )

    def preview_pages(target_printer):
        try:
            paint_pages(
                target_printer, list(range(1, page_count + 1))
            )
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(
                preview_dialog,
                "تعذرت معاينة الطباعة",
                f"حدث خطأ أثناء تجهيز المعاينة:\n{exc}",
            )

    preview_dialog.paintRequested.connect(preview_pages)
    preview_dialog.exec()
    return preview_dialog


def paint_widget_pages(printer, pages, page_numbers):
    first_page_entry = pages[page_numbers[0] - 1]
    first_page = (
        first_page_entry[0]
        if isinstance(first_page_entry, tuple)
        else first_page_entry
    )
    printer.setPageOrientation(
        QPageLayout.Orientation.Landscape
        if first_page.width() > first_page.height()
        else QPageLayout.Orientation.Portrait
    )
    painter = QPainter()
    if not painter.begin(printer):
        raise OSError("تعذر بدء الطباعة على الطابعة المحددة.")
    try:
        for index, page_number in enumerate(page_numbers):
            page_entry = pages[page_number - 1]
            page = page_entry[0] if isinstance(page_entry, tuple) else page_entry
            orientation = (
                QPageLayout.Orientation.Landscape
                if page.width() > page.height()
                else QPageLayout.Orientation.Portrait
            )
            if index:
                printer.setPageOrientation(orientation)
                if not printer.newPage():
                    raise OSError("تعذر إنشاء صفحة جديدة في مهمة الطباعة.")
            page_rect = printer.pageLayout().paintRectPixels(
                printer.resolution()
            )
            scale = min(
                page_rect.width() / page.width(),
                page_rect.height() / page.height(),
            )
            offset_x = page_rect.left() + (
                page_rect.width() - page.width() * scale
            ) / 2
            offset_y = page_rect.top() + (
                page_rect.height() - page.height() * scale
            ) / 2
            painter.save()
            painter.translate(offset_x, offset_y)
            painter.scale(scale, scale)
            page.render(painter, QPoint())
            painter.restore()
    finally:
        painter.end()


def paint_text_document_pages(printer, document, page_numbers):
    painter = QPainter()
    if not painter.begin(printer):
        raise OSError("تعذر بدء الطباعة على الطابعة المحددة.")
    try:
        page_size = document.pageSize()
        page_rect = printer.pageLayout().paintRectPixels(
            printer.resolution()
        )
        for index, page_number in enumerate(page_numbers):
            if index and not printer.newPage():
                raise OSError("تعذر إنشاء صفحة جديدة في مهمة الطباعة.")
            painter.save()
            painter.setViewport(
                page_rect.x(), page_rect.y(),
                page_rect.width(), page_rect.height(),
            )
            page_y = (page_number - 1) * page_size.height()
            painter.setWindow(
                0, round(page_y),
                round(page_size.width()), round(page_size.height()),
            )
            document.drawContents(
                painter,
                QRectF(
                    0,
                    page_y,
                    page_size.width(),
                    page_size.height(),
                ),
            )
            painter.restore()
    finally:
        painter.end()
