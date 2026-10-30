"""Native-widget dialog for previewing a recorded day's A4 pages."""
import re
from io import BytesIO
from datetime import datetime
from pathlib import Path

from PIL import Image
from PySide6.QtCore import (
    QPoint,
    QBuffer,
    QIODevice,
    QMarginsF,
    QSize,
    QStandardPaths,
    QTimer,
    Qt,
)
from PySide6.QtGui import (
    QPageLayout,
    QPageSize,
    QPainter,
    QPixmap,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import (
    QPrinter,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from busy_overlay import BusyOverlay, start_background_task
from print_preview import (
    paint_widget_pages,
    show_application_print_preview,
)


PAGE_WIDTH = 820
PAGE_HEIGHT = round(PAGE_WIDTH * 297 / 210)
PAGE_MARGIN = 6
PAGE_CONTENT_HEIGHT = PAGE_HEIGHT - 2 * PAGE_MARGIN - 2
PREVIEW_EXPORT_DPI = 600
COLORS = (
    ("#2563EB", "#EFF6FF", "#BFDBFE"),
    ("#059669", "#ECFDF5", "#A7F3D0"),
    ("#D97706", "#FFFBEB", "#FDE68A"),
    ("#7C3AED", "#F5F3FF", "#DDD6FE"),
    ("#DB2777", "#FDF2F8", "#FBCFE8"),
    ("#0891B2", "#ECFEFF", "#A5F3FC"),
    ("#DC2626", "#FEF2F2", "#FECACA"),
    ("#4F46E5", "#EEF2FF", "#C7D2FE"),
)


def _day_date_parts(date_text):
    date_value = datetime.strptime(date_text, "%Y-%m-%d")
    weekdays = (
        "الإثنين", "الثلاثاء", "الأربعاء", "الخميس",
        "الجمعة", "السبت", "الأحد",
    )
    months = (
        "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
        "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
    )
    return (
        weekdays[date_value.weekday()],
        str(date_value.day),
        months[date_value.month - 1],
        str(date_value.year),
    )


def _label(text, style, parent=None):
    label = QLabel(str(text))
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setStyleSheet(style)
    if parent is not None:
        label.setParent(parent)
    return label


class HeightAwareCard(QFrame):
    """Card sized from its measured content height."""

    def __init__(self, object_name, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self._content_min_height = 0

    def set_content_heights(self, min_height, preferred_height):
        self._content_min_height = int(min_height)
        self.setMinimumHeight(self._content_min_height)
        self.setMaximumHeight(16777215)
        self.resize(self.width(), int(preferred_height))
        self.setMaximumHeight(self.height())


class PageContentComposer:
    """Position page widgets using measured heights instead of a page layout."""

    def __init__(self, page):
        self.page = page
        self._spacing = 5
        self._next_offset = 0
        self.components = []

    def spacing(self):
        return self._spacing

    def place(self, widget, vertical_offset):
        widget.setParent(self.page)
        widget.move(
            PAGE_MARGIN,
            PAGE_MARGIN + int(vertical_offset),
        )
        widget.show()
        self.components.append(widget)
        self._next_offset = int(vertical_offset) + widget.height() + self._spacing

    def addWidget(self, widget):
        self.place(widget, self._next_offset)


def _calculate_min_height(widget, width):
    widget.setFixedWidth(width)
    widget.ensurePolished()
    layouts = []

    def collect_layouts(layout):
        if layout is None:
            return
        margins = layout.contentsMargins()
        layouts.append(
            (layout, margins.left(), margins.top(), margins.right(),
             margins.bottom(), layout.spacing())
        )
        for index in range(layout.count()):
            item = layout.itemAt(index)
            child_layout = item.layout()
            if child_layout is not None:
                collect_layouts(child_layout)

    def measure_height():
        layout = widget.layout()
        if layout is not None:
            layout.activate()
        widget.ensurePolished()
        if widget.hasHeightForWidth():
            return max(
                widget.minimumSizeHint().height(),
                widget.heightForWidth(width),
            )
        return max(
            widget.sizeHint().height(),
            widget.minimumSizeHint().height(),
        )

    collect_layouts(widget.layout())
    preferred_height = measure_height()
    try:
        for layout_data in layouts:
            layout = layout_data[0]
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
        compact_height = measure_height()
    finally:
        for layout, left, top, right, bottom, spacing in layouts:
            layout.setContentsMargins(left, top, right, bottom)
            layout.setSpacing(spacing)
        layout = widget.layout()
        if layout is not None:
            layout.activate()
    return compact_height, preferred_height


def _size_component(widget, width):
    min_height, preferred_height = _calculate_min_height(widget, width)
    if isinstance(widget, HeightAwareCard):
        widget.set_content_heights(min_height, preferred_height)
    else:
        widget.setFixedHeight(preferred_height)
    widget.setSizePolicy(
        QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
    )
    return preferred_height


def _render_page_pixmap(page, dpi=None):
    if dpi is None:
        dpi = PREVIEW_EXPORT_DPI
    if dpi <= 0:
        raise ValueError("يجب أن تكون دقة الصورة أكبر من صفر.")
    pixel_width = round(210 * dpi / 25.4)
    pixel_height = round(297 * dpi / 25.4)
    pixmap = QPixmap(pixel_width, pixel_height)
    if pixmap.isNull():
        raise OSError("تعذر إنشاء Pixmap بالحجم المطلوب.")
    pixmap.fill(Qt.GlobalColor.white)

    painter = QPainter(pixmap)
    if not painter.isActive():
        raise OSError("تعذر بدء الرسم على Pixmap.")
    painter.setRenderHints(
        QPainter.RenderHint.Antialiasing
        | QPainter.RenderHint.TextAntialiasing
        | QPainter.RenderHint.SmoothPixmapTransform
    )
    painter.scale(pixel_width / page.width(), pixel_height / page.height())
    page.ensurePolished()
    if page.layout() is not None:
        page.layout().activate()
    try:
        page.render(painter, QPoint())
    finally:
        painter.end()

    return pixmap


def _pdf_page_dimensions(dpi):
    if dpi <= 0:
        raise ValueError("يجب أن تكون دقة الصورة أكبر من صفر.")
    return round(210 * dpi / 25.4), round(297 * dpi / 25.4)


def _validate_exported_pdf(pdf_path, expected_page_count):
    pdf_data = Path(pdf_path).read_bytes()
    number = rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    media_boxes = re.findall(
        rb"/MediaBox\s*\[\s*("
        + number
        + rb")\s+("
        + number
        + rb")\s+("
        + number
        + rb")\s+("
        + number
        + rb")\s*\]",
        pdf_data,
    )
    if len(media_boxes) != expected_page_count:
        raise RuntimeError("تعذر التحقق من أبعاد صفحات ملف PDF.")
    for page_number, box in enumerate(media_boxes):
        left, bottom, right, top = (float(value) for value in box)
        if right <= left or top <= bottom:
            raise RuntimeError(
                f"أبعاد صفحة PDF رقم {page_number + 1} غير صالحة."
            )

    document = QPdfDocument(None)
    try:
        if document.load(str(pdf_path)) != QPdfDocument.Error.None_:
            raise RuntimeError("تعذر فتح ملف PDF بعد إنشائه.")
        if document.pageCount() != expected_page_count:
            raise RuntimeError("عدد صفحات ملف PDF لا يطابق صفحات المعاينة.")

        for page_number in range(document.pageCount()):
            page_size = document.pagePointSize(page_number)
            if page_size.width() <= 0 or page_size.height() <= 0:
                raise RuntimeError(
                    f"أبعاد صفحة PDF رقم {page_number + 1} غير صالحة."
                )
            image_size = QSize(
                240,
                round(240 * page_size.height() / page_size.width()),
            )
            image = document.render(
                page_number,
                image_size,
                fillColor=Qt.GlobalColor.white,
            )
            if image.isNull():
                raise RuntimeError(
                    f"تعذر قراءة محتوى صفحة PDF رقم {page_number + 1}."
                )
            has_content = any(
                (color := image.pixelColor(x, y)).alpha() > 0
                and (
                    color.red() < 255
                    or color.green() < 255
                    or color.blue() < 255
                )
                for y in range(0, image.height(), 4)
                for x in range(0, image.width(), 4)
            )
            if not has_content:
                raise RuntimeError(
                    f"صفحة PDF رقم {page_number + 1} فارغة."
                )
    finally:
        document.close()


def export_day_preview_to_pdf(payload, output_path, dpi=None):
    if not isinstance(payload, dict):
        raise TypeError("بيانات اليوم يجب أن تكون قاموسًا.")
    if not output_path or not str(output_path).strip():
        raise ValueError("يجب تحديد مسار ملف PDF.")
    if dpi is None:
        dpi = PREVIEW_EXPORT_DPI
    if dpi <= 0:
        raise ValueError("يجب أن تكون دقة الصورة أكبر من صفر.")

    pdf_path = Path(output_path)
    if pdf_path.suffix.lower() != ".pdf":
        pdf_path = pdf_path.with_suffix(".pdf")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    dialog = DayPdfPreviewDialog(payload)
    images = []
    try:
        for page, _, _ in dialog._pages:
            pixmap = _render_page_pixmap(page, dpi)
            images.append(
                Image.fromqimage(pixmap.toImage()).convert("RGB")
            )
        images[0].save(
            pdf_path,
            format="PDF",
            save_all=True,
            append_images=images[1:],
            resolution=dpi,
        )
    finally:
        for image in images:
            image.close()

    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"لم يتم إنشاء ملف PDF: {pdf_path}")
    _validate_exported_pdf(pdf_path, len(dialog._pages))
    return pdf_path


def export_day_preview_to_documents(payload, dpi=None):
    if not isinstance(payload, dict) or not payload.get("date"):
        raise ValueError("يجب تحديد تاريخ اليوم قبل التصدير.")
    documents_path = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    if not documents_path:
        raise OSError("تعذر تحديد مجلد Documents الخاص بالنظام.")
    output_directory = Path(documents_path)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / f"school_day_{payload['date']}.pdf"
    return export_day_preview_to_pdf(payload, output_path, dpi)


def _documents_export_path(date_text):
    documents_path = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    if not documents_path:
        raise OSError("تعذر تحديد مجلد Documents الخاص بالنظام.")
    output_directory = Path(documents_path)
    output_directory.mkdir(parents=True, exist_ok=True)
    return output_directory / f"school_day_{date_text}.pdf"


def _save_rendered_preview_pdf(
    png_pages, output_path, dpi, expected_page_count
):
    pdf_path = Path(output_path)
    images = []
    try:
        for png_page in png_pages:
            with Image.open(BytesIO(png_page)) as source:
                images.append(source.convert("RGB"))
        images[0].save(
            pdf_path,
            format="PDF",
            save_all=True,
            append_images=images[1:],
            resolution=dpi,
        )
    finally:
        for image in images:
            image.close()
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"لم يتم إنشاء ملف PDF: {pdf_path}")
    _validate_exported_pdf(pdf_path, expected_page_count)
    return pdf_path


def _date_card(date_text, width):
    card = QFrame()
    card.setObjectName("dayPreviewDateCard")
    card.setStyleSheet(
        "QFrame#dayPreviewDateCard { background:#F8FAFC;"
        "border:1px solid #E2E8F0; border-right:5px solid #2563EB;"
        "border-radius:12px; }"
    )
    layout = QHBoxLayout(card)
    layout.setContentsMargins(14, 7, 14, 7)
    layout.setSpacing(24)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    weekday, day, month, year = _day_date_parts(date_text)
    weekday_label = _label(
        f"يوم {weekday}",
        "color:#475569; font-size:10pt; font-weight:600;"
        "background:transparent; border:none;",
    )
    day_label = _label(
        day,
        "color:#1D4ED8; background:#DBEAFE; border-radius:8px;"
        "font-size:16pt; font-weight:700; padding:2px 10px;",
    )
    month_year_label = _label(
        f"{month} {year}",
        "color:#1E3A8A; font-size:12pt; font-weight:700;"
        "background:transparent; border:none;",
    )
    weekday_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    day_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    month_year_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(weekday_label)
    layout.addWidget(day_label)
    layout.addWidget(month_year_label)
    card.setFixedWidth(width)
    card.setFixedHeight(60)
    return card


def _lesson_card(lesson, index, width):
    accent, tint, border = COLORS[index % len(COLORS)]
    card = HeightAwareCard("dayLessonCard")
    card.setStyleSheet(
        "QFrame#dayLessonCard { background:#FFFFFF;"
        f"border:1px solid {border}; border-right:5px solid {accent};"
        "border-radius:12px; }"
    )
    layout = QVBoxLayout(card)
    layout.setContentsMargins(6, 4, 6, 4)
    layout.setSpacing(3)

    title_text = str(lesson.get("classe") or "—")
    title = _label(
        title_text,
        f"color:{accent}; font-size:12pt; font-weight:700;"
        "background:transparent; border:none;",
    )
    layout.addWidget(title)

    metadata = (
        ("المجال", lesson.get("majal")),
        ("ساعة التدريس", lesson.get("heure")),
        ("التوقيت", lesson.get("time")),
        ("الوحدة", lesson.get("wehda_number")),
        ("الحصة", lesson.get("hessa_number")),
        ("نمط التعلم", lesson.get("namat_taallom")),
    )
    for row_start in (0, 3):
        row = QHBoxLayout()
        row.setSpacing(3)
        for field_name, field_value in metadata[row_start:row_start + 3]:
            chip = _label(
                f"{field_name}: {field_value or '—'}",
                f"color:#334155; background:{tint}; border:1px solid {border};"
                "border-radius:6px; padding:2px 4px;",
            )
            chip.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            row.addWidget(chip, 1)
        layout.addLayout(row)

    _size_component(card, width)
    return card


def _activity_card(activity, index, width, details):
    accent, tint, border = COLORS[index % len(COLORS)]
    card = HeightAwareCard("dayActivityCard")
    card.setStyleSheet(
        "QFrame#dayActivityCard {"
        f"background:{tint}; border:1px solid {border};"
        "border-radius:9px; }"
    )
    layout = QVBoxLayout(card)
    layout.setContentsMargins(10, 8, 10, 8)
    layout.setSpacing(5)

    heading_text = str(activity.get("title") or "نشاط دون عنوان")
    if activity.get("time"):
        heading_text += f"  ·  {activity['time']}"
    if activity.get("classe"):
        heading_text += f"  ·  {activity['classe']}"
    heading = _label(
        heading_text,
        f"color:{accent}; font-weight:700;"
        "background:transparent; border:none;",
    )
    layout.addWidget(heading)

    for detail in details:
        name = str(detail.get("key_name") or "—").strip()
        if name.startswith("العنصر:"):
            name = name[len("العنصر:"):].strip()
        value = str(detail.get("value_text") or "—").strip()
        detail_label = _label(
            f"{name}: {value}",
            "color:#334155; background:transparent; border:none;",
        )
        detail_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(detail_label)

    _size_component(card, width)
    return card


def _activity_grid_row(cards, width):
    row = QWidget()
    row.setObjectName("dayActivityGridRow")
    row.setFixedWidth(width)
    row_height = max(card.height() for card in cards)
    row.setFixedHeight(row_height)
    row.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    layout = QGridLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setHorizontalSpacing(5)
    layout.setVerticalSpacing(0)
    for column, card in enumerate(cards):
        card.setFixedHeight(row_height)
        layout.addWidget(card, 0, column)
        layout.setColumnStretch(column, card.width())
    return row


def _balance_activity_card_widths(cards, row_width, gap_width):
    if len(cards) < 2:
        return

    available_width = row_width - gap_width
    base_width, remainder = divmod(available_width, len(cards))
    minimum_width = max(100, base_width // 2)
    widths = [base_width] * len(cards)
    for index in range(remainder):
        widths[index] += 1

    measured_heights = [{} for _ in cards]

    def measure(card_index, width):
        cache = measured_heights[card_index]
        if width not in cache:
            cache[width] = _size_component(cards[card_index], width)
        return cache[width]

    initial_height = max(
        measure(index, width) for index, width in enumerate(widths)
    )
    maximum_width = available_width - minimum_width * (len(cards) - 1)

    def widths_for_height(target_height):
        required_widths = []
        for index in range(len(cards)):
            low = minimum_width
            high = maximum_width
            if measure(index, high) > target_height:
                return None
            while low < high:
                middle = (low + high) // 2
                if measure(index, middle) <= target_height:
                    high = middle
                else:
                    low = middle + 1
            required_widths.append(low)
        if sum(required_widths) > available_width:
            return None
        return required_widths

    low_height = 0
    high_height = initial_height
    best_widths = widths
    while low_height < high_height:
        target_height = (low_height + high_height) // 2
        candidate_widths = widths_for_height(target_height)
        if candidate_widths is None:
            low_height = target_height + 1
        else:
            high_height = target_height
            best_widths = candidate_widths

    best_widths = widths_for_height(high_height) or best_widths
    extra_width = available_width - sum(best_widths)
    for index in range(len(best_widths)):
        share = extra_width // (len(best_widths) - index)
        best_widths[index] += share
        extra_width -= share

    for card, width in zip(cards, best_widths):
        _size_component(card, width)


class DayPdfPreviewDialog(QDialog):
    """Show paginated A4 pages built from native Qt card widgets."""

    def __init__(self, payload, parent=None):
        super().__init__(parent)
        if not isinstance(payload, dict) or not payload.get("date"):
            raise ValueError("بيانات اليوم غير صالحة للمعاينة.")
        if not isinstance(payload.get("lessons"), list) or not payload["lessons"]:
            raise ValueError("لا توجد حصص مسجلة لهذا اليوم.")

        self.setWindowTitle("معاينة صفحات اليوم")
        self.resize(960, 760)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._payload = payload
        self._pages = []

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        pages_container = QWidget()
        self.pages_layout = QVBoxLayout(pages_container)
        self.pages_layout.setContentsMargins(18, 18, 18, 18)
        self.pages_layout.setSpacing(22)
        self._build_pages(payload)
        scroll_area.setWidget(pages_container)

        layout = QVBoxLayout(self)
        buttons_layout = QHBoxLayout()
        self.export_pdf_button = QPushButton("تصدير PDF", self)
        self.export_pdf_button.setObjectName("dayPreviewExportPdfButton")
        self.export_pdf_button.setProperty("actionType", "export")
        self.export_pdf_button.clicked.connect(self.export_pdf)
        self.print_button = QPushButton("طباعة", self)
        self.print_button.setObjectName("dayPreviewPrintButton")
        self.print_button.setProperty("actionType", "print")
        self.print_button.clicked.connect(self.print_pages)
        buttons_layout.addWidget(self.export_pdf_button)
        buttons_layout.addWidget(self.print_button)
        buttons_layout.addStretch(1)
        layout.addLayout(buttons_layout)
        layout.addWidget(scroll_area)

    def export_pdf(self):
        try:
            output_path = _documents_export_path(self._payload["date"])
            self._export_pages_async(
                output_path,
                lambda pdf_path: QMessageBox.information(
                    self,
                    "نجاح التصدير",
                    f"تم تصدير اليوم إلى:\n{pdf_path}",
                ),
                lambda error: QMessageBox.critical(
                    self, "تعذر تصدير PDF",
                    f"حدث خطأ أثناء تصدير اليوم:\n{error}",
                ),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(
                self, "تعذر تصدير PDF", f"حدث خطأ أثناء تصدير اليوم:\n{exc}"
            )

    def _export_pages_async(self, output_path, on_success, on_error):
        if getattr(self, "_day_export_pending", False):
            return
        self._day_export_pending = True
        self.export_pdf_button.setEnabled(False)
        overlay = BusyOverlay(self)
        png_pages = []
        page_index = 0
        expected_page_count = len(self._pages)

        def finish_with_error(message):
            self._day_export_pending = False
            self.export_pdf_button.setEnabled(True)
            overlay.close()
            on_error(message)

        def render_next_page():
            nonlocal page_index
            try:
                if page_index < len(self._pages):
                    page, _, _ = self._pages[page_index]
                    pixmap = _render_page_pixmap(
                        page, PREVIEW_EXPORT_DPI
                    )
                    buffer = QBuffer()
                    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                    if not pixmap.toImage().save(buffer, "PNG"):
                        raise RuntimeError(
                            "تعذر تجهيز صفحة PDF للتصدير."
                        )
                    png_pages.append(bytes(buffer.data()))
                    buffer.close()
                    page_index += 1
                    QTimer.singleShot(0, render_next_page)
                    return

                started = start_background_task(
                    self,
                    lambda: _save_rendered_preview_pdf(
                        png_pages,
                        output_path,
                        PREVIEW_EXPORT_DPI,
                        expected_page_count,
                    ),
                    lambda path: self._complete_day_export(
                        path, on_success
                    ),
                    lambda error: self._fail_day_export(
                        error, on_error
                    ),
                    overlay=overlay,
                )
                if not started:
                    finish_with_error(
                        "توجد مهمة أخرى قيد التنفيذ في نافذة المعاينة."
                    )
            except (OSError, ValueError, RuntimeError) as exc:
                finish_with_error(str(exc))

        QTimer.singleShot(0, render_next_page)

    def _complete_day_export(self, path, on_success):
        self._day_export_pending = False
        self.export_pdf_button.setEnabled(True)
        on_success(path)

    def _fail_day_export(self, error, on_error):
        self._day_export_pending = False
        self.export_pdf_button.setEnabled(True)
        on_error(error)

    def print_pages(self):
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageLayout(
            QPageLayout(
                QPageSize(QPageSize.PageSizeId.A4),
                QPageLayout.Orientation.Portrait,
                QMarginsF(8, 8, 8, 8),
                QPageLayout.Unit.Millimeter,
            )
        )
        try:
            first_page = self._pages[0][0]
            orientation = (
                QPageLayout.Orientation.Landscape
                if first_page.width() > first_page.height()
                else QPageLayout.Orientation.Portrait
            )
            show_application_print_preview(
                self,
                printer,
                len(self._pages),
                lambda target_printer, page_numbers: paint_widget_pages(
                    target_printer, self._pages, page_numbers
                ),
                orientation,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(
                self,
                "تعذرت معاينة الطباعة",
                f"حدث خطأ أثناء تجهيز المعاينة:\n{exc}",
            )

    def _new_page(self, date_text, include_date=False):
        page = QFrame()
        page.setObjectName("dayPreviewPage")
        page.setFixedSize(PAGE_WIDTH, PAGE_HEIGHT)
        page.setStyleSheet(
            "QFrame#dayPreviewPage { background:#FFFFFF;"
            "border:1px solid #CBD5E1; }"
        )
        page_content = PageContentComposer(page)
        date_card = None
        if include_date:
            date_card = _date_card(
                date_text, PAGE_WIDTH - 2 * PAGE_MARGIN
            )
            page_content.place(date_card, 0)
        self.pages_layout.addWidget(page, 0, Qt.AlignmentFlag.AlignHCenter)
        self._pages.append((page, page_content, date_card))
        return page_content, date_card

    def _build_pages(self, payload):
        date_text = payload["date"]
        page_content, date_card = self._new_page(
            date_text, include_date=True
        )
        spacing = page_content.spacing()
        used_height = date_card.height() + spacing
        content_width = PAGE_WIDTH - 2 * PAGE_MARGIN

        def place_activity_row(cards):
            nonlocal page_content, used_height
            row = _activity_grid_row(cards, content_width)
            row_height = row.height()
            if (
                used_height + row_height + spacing
                > PAGE_CONTENT_HEIGHT
            ):
                page_content, _ = self._new_page(date_text)
                used_height = 0
            page_content.addWidget(row)
            used_height += row_height + spacing

        def place_full_width_activity(
            activity, lesson_index, force_fragment=False
        ):
            nonlocal page_content, used_height
            details = list(activity.get("details", []))
            card = _activity_card(
                activity, lesson_index, content_width, details
            )
            if card.height() <= PAGE_CONTENT_HEIGHT and not force_fragment:
                place_activity_row([card])
                return
            if not details:
                raise ValueError(
                    "عنوان النشاط أكبر من المساحة المتاحة في صفحة A4."
                )

            remaining_details = details
            while remaining_details:
                available_height = (
                    PAGE_CONTENT_HEIGHT - used_height - spacing
                )
                chunk = []
                for detail in remaining_details:
                    candidate = _activity_card(
                        activity,
                        lesson_index,
                        content_width,
                        chunk + [detail],
                    )
                    if candidate.height() > available_height:
                        break
                    chunk.append(detail)

                if not chunk:
                    if used_height:
                        page_content, _ = self._new_page(date_text)
                        used_height = 0
                        continue
                    raise ValueError(
                        "أحد تفاصيل النشاط أكبر من صفحة المعاينة."
                    )

                fragment = _activity_card(
                    activity,
                    lesson_index,
                    content_width,
                    chunk,
                )
                place_activity_row([fragment])
                remaining_details = remaining_details[len(chunk):]
                if remaining_details:
                    page_content, _ = self._new_page(date_text)
                    used_height = 0

        for lesson_index, lesson in enumerate(payload["lessons"]):
            header = _lesson_card(lesson, lesson_index, content_width)
            header_height = _size_component(header, content_width)
            if header_height > PAGE_CONTENT_HEIGHT:
                raise ValueError("بطاقة الحصة أكبر من المساحة المتاحة في صفحة A4.")

            activities = lesson.get("activities", [])
            activity_groups = [
                activities[start:start + 4]
                for start in range(0, len(activities), 4)
            ]
            keep_first_group = False
            force_fragment_first_activity = False
            first_activity_height = 0
            if activity_groups:
                first_group = activity_groups[0]
                columns = len(first_group)
                gap_width = 5 * (columns - 1)
                card_width = (content_width - gap_width) // columns
                first_group_cards = [
                    _activity_card(
                        activity,
                        lesson_index,
                        card_width,
                        activity.get("details", []),
                    )
                    for activity in first_group
                ]
                _balance_activity_card_widths(
                    first_group_cards, content_width, gap_width
                )
                first_group_height = max(
                    card.height() for card in first_group_cards
                )
                keep_first_group = (
                    first_group_height <= PAGE_CONTENT_HEIGHT
                    and header_height + spacing
                    + first_group_height + spacing
                    <= PAGE_CONTENT_HEIGHT
                )
                if not keep_first_group:
                    first_activity = first_group[0]
                    full_width_card = _activity_card(
                        first_activity,
                        lesson_index,
                        content_width,
                        first_activity.get("details", []),
                    )
                    first_activity_height = full_width_card.height()
                    if (
                        header_height + spacing
                        + first_activity_height + spacing
                        > PAGE_CONTENT_HEIGHT
                    ):
                        details = first_activity.get("details", [])
                        if not details:
                            raise ValueError(
                                "تعذر وضع بطاقة الحصة وأول نشاط في صفحة واحدة."
                            )
                        force_fragment_first_activity = True
                        first_activity_height = _activity_card(
                            first_activity,
                            lesson_index,
                            content_width,
                            details[:1],
                        ).height()
                    if (
                        header_height + spacing
                        + first_activity_height + spacing
                        > PAGE_CONTENT_HEIGHT
                    ):
                        raise ValueError(
                            "تعذر وضع بطاقة الحصة وأول نشاط في صفحة واحدة."
                        )
                    activity_groups = [
                        [first_group[0]],
                        first_group[1:],
                        *activity_groups[1:],
                    ]
                    activity_groups = [
                        group for group in activity_groups if group
                    ]

            required_height = header_height + spacing
            if keep_first_group:
                required_height += (
                    first_group_height + spacing
                )
            elif activities:
                required_height += (
                    first_activity_height + spacing
                )
            if used_height + required_height > PAGE_CONTENT_HEIGHT:
                page_content, date_card = self._new_page(date_text)
                used_height = 0
            page_content.addWidget(header)
            used_height += header_height + spacing

            if not activities:
                empty = _label(
                    "لا توجد أنشطة مسجلة لهذه الحصة.",
                    "color:#64748B; padding:6px;"
                    "background:#F8FAFC; border-radius:8px;",
                )
                _size_component(empty, content_width - 20)
                if (
                    used_height + empty.height() + spacing
                    > PAGE_CONTENT_HEIGHT
                ):
                    page_content, date_card = self._new_page(date_text)
                    used_height = 0
                page_content.addWidget(empty)
                used_height += empty.height() + spacing
                continue
            for group_index, activity_group in enumerate(activity_groups):
                if group_index == 0 and not keep_first_group:
                    place_full_width_activity(
                        activity_group[0],
                        lesson_index,
                        force_fragment_first_activity,
                    )
                    continue
                columns = len(activity_group)
                gap_width = 5 * (columns - 1)
                activity_card_width = (content_width - gap_width) // columns
                cards = [
                    _activity_card(
                        activity,
                        lesson_index,
                        activity_card_width,
                        activity.get("details", []),
                    )
                    for activity in activity_group
                ]
                _balance_activity_card_widths(
                    cards, content_width, gap_width
                )
                if all(card.height() <= PAGE_CONTENT_HEIGHT for card in cards):
                    place_activity_row(cards)
                else:
                    for activity in activity_group:
                        place_full_width_activity(activity, lesson_index)

        self.pages_layout.addStretch(1)
