"""Standalone, paginated PDF export for a recorded school day."""
import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMarginsF, QRectF, QSize
from PySide6.QtGui import (
    QFont,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QTextDocument,
)

from app_config import load_custom_font


PAGE_MARGIN_MM = 12
PAGE_SPACING = 10
DETAIL_FONT_SIZE = 8
LESSON_COLORS = (
    ("#2563EB", "#EFF6FF", "#BFDBFE"),
    ("#059669", "#ECFDF5", "#A7F3D0"),
    ("#D97706", "#FFFBEB", "#FDE68A"),
    ("#7C3AED", "#F5F3FF", "#DDD6FE"),
    ("#DB2777", "#FDF2F8", "#FBCFE8"),
    ("#0891B2", "#ECFEFF", "#A5F3FC"),
    ("#DC2626", "#FEF2F2", "#FECACA"),
    ("#4F46E5", "#EEF2FF", "#C7D2FE"),
)


def _escaped(value):
    return html.escape(str(value or ""), quote=True)


def _day_title(date_text):
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
        f"يوم {weekdays[date_value.weekday()]} "
        f"{date_value.day} {months[date_value.month - 1]} {date_value.year}"
    )


def _header_html(date_text):
    return (
        "<div style='background-color:#EFF6FF; border:1px solid #BFDBFE; "
        "border-radius:14px; padding:15px 18px; margin:0 0 12px 0; "
        "text-align:center; color:#1D4ED8; font-size:16pt; font-weight:bold;'>"
        f"{_escaped(_day_title(date_text))}</div>"
    )


def _lesson_html(lesson, index):
    accent, tint, border = LESSON_COLORS[index % len(LESSON_COLORS)]
    heading = _escaped(lesson.get("classe") or "—")
    metadata = (
        ("المجال", lesson.get("majal")),
        ("ساعة التدريس", lesson.get("heure")),
        ("التوقيت", lesson.get("time")),
        ("الوحدة", lesson.get("wehda_number")),
        ("الحصة", lesson.get("hessa_number")),
        ("نمط التعلم", lesson.get("namat_taallom")),
    )
    rows = []
    for row_index in range(0, len(metadata), 3):
        cells = "".join(
            "<td valign='top' style='background-color:"
            f"{tint};border:1px solid {border};border-radius:7px;"
            "padding:2px 4px;color:#334155;'>"
            f"{_escaped(label)}: {_escaped(value or '—')}</td>"
            for label, value in metadata[row_index:row_index + 3]
        )
        rows.append(f"<tr>{cells}</tr>")
    return (
        "<div style='background-color:#FFFFFF;border:1px solid "
        f"{border};border-right:5px solid {accent};border-radius:12px;"
        "padding:4px 5px;margin:0 0 5px 0;'>"
        f"<div style='color:{accent};font-size:12pt;font-weight:bold;"
        f"margin-bottom:3px;'>{heading}</div>"
        "<table dir='rtl' width='100%' cellspacing='2' cellpadding='0'>"
        f"{''.join(rows)}</table></div>"
    )


def _activity_html(
    activity,
    index,
    details,
    continued=False,
):
    accent, tint, border = LESSON_COLORS[index % len(LESSON_COLORS)]
    heading = ""
    if not continued:
        heading = (
            f"{_escaped(activity.get('title') or 'نشاط دون عنوان')}"
            + (
                f"  ·  {_escaped(activity['time'])}"
                if activity.get("time") else ""
            )
            + (
                f"  ·  {_escaped(activity['classe'])}"
                if activity.get("classe")
                else ""
            )
        )
    detail_html = "".join(
        "<div style='background-color:transparent;border:none;"
        "padding:2px 0;margin-top:3px;color:#334155;'>"
        f"{_escaped(_clean_detail_name(detail.get('key_name')))}: "
        f"{_escaped(detail.get('value_text'))}</div>"
        for detail in details
    )
    return (
        f"<div style='background-color:{tint};border:1px solid {border};"
        "border-radius:9px;padding:8px 10px;margin:0 10px 8px 10px;'>"
        + (
            f"<div style='color:{accent};font-weight:bold;margin-bottom:5px;'>"
            f"{heading}</div>"
            if heading else ""
        )
        + f"{detail_html}</div>"
    )


def _clean_detail_name(name):
    cleaned = str(name or "—").strip()
    if cleaned.startswith("العنصر:"):
        return cleaned[len("العنصر:"):].strip()
    return cleaned


def _measure_html(parts, width, font):
    document = QTextDocument()
    document.setDefaultFont(font)
    document.setHtml("".join(parts))
    document.setTextWidth(width)
    return document.size().height()


def _split_detail(
    detail, activity, index, available_height, width, font, continued=False
):
    """Split an individually oversized detail value without dropping text."""
    value = str(detail.get("value_text") or "")
    if not value:
        return [detail]

    chunks = []
    remaining = value
    while remaining:
        low, high, best = 1, len(remaining), 0
        while low <= high:
            middle = (low + high) // 2
            if middle < len(remaining):
                split_at = remaining.rfind(" ", 0, middle + 1)
                if split_at < max(1, middle // 2):
                    split_at = middle
            else:
                split_at = middle
            candidate = remaining[:split_at].rstrip()
            candidate_detail = dict(detail, value_text=candidate)
            candidate_html = _activity_html(
                activity,
                index,
                [candidate_detail],
                continued=continued or bool(chunks),
            )
            if _measure_html([candidate_html], width, font) <= available_height:
                best = split_at
                low = middle + 1
            else:
                high = middle - 1
        if best == 0:
            raise ValueError(
                "يوجد عنصر نشاط أطول من مساحة الصفحة ولا يمكن تقسيمه."
            )
        chunks.append(dict(detail, value_text=remaining[:best]))
        remaining = remaining[best:]
    return chunks


def _activity_fragments(activity, index, available_height, width, font):
    details = activity.get("details", [])
    whole = _activity_html(activity, index, details)
    if _measure_html([whole], width, font) <= available_height:
        return [whole]

    fragments = []
    current_details = []
    for detail in details:
        detail_html = _activity_html(
            activity, index, current_details + [detail],
            continued=bool(fragments),
        )
        if _measure_html([detail_html], width, font) <= available_height:
            current_details.append(detail)
            continue

        if current_details:
            fragments.append(
                _activity_html(
                    activity, index, current_details,
                    continued=bool(fragments),
                )
            )
            current_details = []

        single_html = _activity_html(
            activity, index, [detail], continued=bool(fragments)
        )
        if _measure_html([single_html], width, font) <= available_height:
            current_details.append(detail)
            continue

        split_details = _split_detail(
            detail, activity, index, available_height, width, font,
            continued=bool(fragments),
        )
        for split_detail in split_details:
            fragments.append(
                _activity_html(
                    activity, index, [split_detail],
                    continued=bool(fragments),
                )
            )

    if current_details or not fragments:
        fragments.append(
            _activity_html(
                activity, index, current_details,
                continued=bool(fragments),
            )
        )
    return fragments


def _paginate(payload, width, height, font):
    date_header = _header_html(payload["date"])
    pages = []
    current_page = [date_header]
    page_limit = height - 8

    def start_page():
        nonlocal current_page
        if current_page:
            pages.append(current_page)
        current_page = []

    for lesson_index, lesson in enumerate(payload["lessons"]):
        lesson_html = _lesson_html(lesson, lesson_index)
        lesson_height = _measure_html([lesson_html], width, font)
        if lesson_height > page_limit:
            raise ValueError("بطاقة الحصة أكبر من المساحة المتاحة في صفحة A4.")
        if _measure_html(current_page + [lesson_html], width, font) > page_limit:
            start_page()
        current_page.append(lesson_html)

        activities = lesson.get("activities", [])
        if not activities:
            empty_activity = (
                "<div style='color:#64748B;background:#F8FAFC;border-radius:8px;"
                "padding:6px;margin:0 10px 8px;'>"
                "لا توجد أنشطة مسجلة لهذه الحصة.</div>"
            )
            if _measure_html(current_page + [empty_activity], width, font) > page_limit:
                start_page()
            current_page.append(empty_activity)
            continue

        for activity in activities:
            activity_html = _activity_html(
                activity, lesson_index, activity.get("details", [])
            )
            activity_height = _measure_html(
                [activity_html], width, font
            )
            if activity_height <= page_limit:
                if (
                    _measure_html(current_page + [activity_html], width, font)
                    > page_limit
                ):
                    start_page()
                current_page.append(activity_html)
                continue

            if current_page:
                start_page()
            fragments = _activity_fragments(
                activity, lesson_index, page_limit, width, font
            )
            for fragment_index, fragment in enumerate(fragments):
                if _measure_html([fragment], width, font) > page_limit:
                    raise ValueError(
                        "جزء من النشاط أكبر من المساحة المتاحة في صفحة A4."
                    )
                current_page.append(fragment)
                if fragment_index < len(fragments) - 1:
                    start_page()

    if current_page:
        pages.append(current_page)
    return pages


def _layout_pages(payload, page_width, page_height, dpi, font):
    margin = round(PAGE_MARGIN_MM * dpi / 25.4)
    content_width = page_width - 2 * margin
    content_height = page_height - 2 * margin
    pages = _paginate(payload, content_width, content_height, font)
    return pages, margin, content_width, content_height


def _paint_page(painter, components, margin, content_width, content_height, font):
    document = QTextDocument()
    document.setDefaultFont(font)
    document.setHtml(
        "<html><head><meta charset='utf-8'></head>"
        "<body dir='rtl' style='direction:rtl;margin:0;'>"
        + "".join(components)
        + "</body></html>"
    )
    document.setTextWidth(content_width)
    painter.save()
    painter.translate(margin, margin)
    document.drawContents(
        painter, QRectF(0, 0, content_width, content_height)
    )
    painter.restore()


def render_day_preview(payload, dpi=96):
    """Render the exact A4 pages used by export_day_to_pdf as preview images."""
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dictionary.")
    if not payload.get("date"):
        raise ValueError("يجب تحديد تاريخ اليوم قبل المعاينة.")
    if not isinstance(payload.get("lessons"), list) or not payload["lessons"]:
        raise ValueError("لا توجد حصص مسجلة لهذا اليوم.")
    if dpi <= 0:
        raise ValueError("dpi must be greater than zero.")

    page_width = round(210 * dpi / 25.4)
    page_height = round(297 * dpi / 25.4)
    font = QFont(load_custom_font(), DETAIL_FONT_SIZE)
    pages, margin, content_width, content_height = _layout_pages(
        payload, page_width, page_height, dpi, font
    )

    images = []
    for components in pages:
        image = QImage(
            QSize(page_width, page_height), QImage.Format.Format_ARGB32
        )
        image.fill(0xFFFFFFFF)
        painter = QPainter()
        if not painter.begin(image):
            raise RuntimeError("تعذر إنشاء صورة معاينة صفحة PDF.")
        try:
            _paint_page(
                painter, components, margin, content_width, content_height,
                font,
            )
        finally:
            painter.end()
        images.append(image)
    return images


def _day_export_html(payload, font_family):
    date_value = datetime.strptime(payload["date"], "%Y-%m-%d")
    weekdays = (
        "الإثنين", "الثلاثاء", "الأربعاء", "الخميس",
        "الجمعة", "السبت", "الأحد",
    )
    months = (
        "جانفي", "فيفري", "مارس", "أفريل", "ماي", "جوان",
        "جويلية", "أوت", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
    )
    date_parts = (
        str(date_value.year),
        months[date_value.month - 1],
        str(date_value.day),
        weekdays[date_value.weekday()],
        "يوم",
    )
    escaped_font = html.escape(font_family, quote=True)
    parts = [
        "<html><head><meta charset='utf-8'></head>",
        "<body dir='rtl' style='direction: rtl;'>",
        f"<div style='font-family: \"{escaped_font}\"; font-size: 8pt;'>",
        "<table dir='rtl' width='100%' cellspacing='5' cellpadding='1' "
        "style='background-color: #DBEAFE; margin-bottom: 5px;'>"
        "<tr>",
    ]
    for value in date_parts:
        parts.append(
            "<td align='center' bgcolor='#DBEAFE' "
            "style='background-color: #DBEAFE;'>"
            f"{html.escape(value)}</td>"
        )
    parts.append("</tr></table>")

    for lesson in payload["lessons"]:
        lesson_text = (
            f"القسم: {lesson.get('classe') or '—'}"
            f"    |    المجال: {lesson.get('majal') or '—'}"
            f"    |    الساعة: {lesson.get('heure') or '—'}"
            f"    |    التوقيت: {lesson.get('time') or '—'}"
            f"    |    عدد الوحدة: {lesson.get('wehda_number') or '—'}"
            f"    |    الحصة: {lesson.get('hessa_number') or '—'}"
        )
        parts.append(
            "<table dir='rtl' width='100%' cellspacing='0' cellpadding='5' "
            "style='background-color: #E8F5E9; margin-bottom: 5px;'>"
            "<tr><td align='center' bgcolor='#E8F5E9' "
            "style='background-color: #E8F5E9; font-size: 8pt;'>"
            f"{html.escape(lesson_text)}</td></tr></table>"
        )
        for activity in lesson.get("activities", []):
            activity_text = (
                f"النشاط: {activity.get('title') or '—'}"
                + (
                    f"    |    التوقيت: {activity['time']}"
                    if activity.get("time") else ""
                )
            )
            if lesson.get("class_group_key") and activity.get("classe"):
                activity_text += f"    |    القسم: {activity['classe']}"
            parts.append(
                "<p dir='rtl' style='background-color: #FAFAFA; "
                "border: 1px solid #E2E8F0; border-radius: 6px; "
                "color: #010101; margin: 2px 10px; padding: 3px 8px; "
                "font-size: 8pt; font-weight: bold; text-align: center;'>"
                f"{html.escape(f'📖 {activity_text} 📖')}</p>"
            )
            for detail in activity.get("details", []):
                detail_name = str(detail.get("key_name") or "").strip()
                if detail_name.startswith("العنصر:"):
                    detail_name = detail_name[len("العنصر:"):]
                parts.append(
                    "<p dir='rtl' style='background-color: #FFFFFF; "
                    "border: 1px solid #E2E8F0; border-radius: 6px; "
                    "color: #334155; margin: 2px 10px; padding: 3px 8px; "
                    "font-size: 7pt;'>"
                    f"♣ {html.escape(detail_name)}: "
                    f"{html.escape(str(detail.get('value_text') or '').strip())}"
                    "</p>"
                )
    parts.append("</div></body></html>")
    return "".join(parts)


def export_day_to_pdf(payload, output_path):
    """Export a recorded day using the standalone QTextDocument PDF layout."""
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dictionary.")
    if not payload.get("date"):
        raise ValueError("يجب تحديد تاريخ اليوم قبل التصدير.")
    if not isinstance(payload.get("lessons"), list) or not payload["lessons"]:
        raise ValueError("لا توجد حصص مسجلة لليوم المحدد.")

    pdf_path = Path(output_path)
    if not str(output_path).strip():
        raise ValueError("A PDF output path is required.")
    if pdf_path.suffix.lower() != ".pdf":
        pdf_path = pdf_path.with_suffix(".pdf")

    writer = QPdfWriter(str(pdf_path))
    writer.setResolution(96)
    page_layout = QPageLayout(
        QPageSize(QPageSize.PageSizeId.A4),
        QPageLayout.Orientation.Portrait,
        QMarginsF(12, 12, 12, 12),
        QPageLayout.Unit.Millimeter,
    )
    if not writer.setPageLayout(page_layout):
        raise RuntimeError("تعذر ضبط حجم صفحة PDF إلى A4.")

    font_family = load_custom_font()
    document = QTextDocument()
    document.setDefaultFont(QFont(font_family, DETAIL_FONT_SIZE))
    document.setHtml(_day_export_html(payload, font_family))
    document.setPageSize(
        writer.pageLayout().paintRect(QPageLayout.Unit.Point).size()
    )
    document.print_(writer)

    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"لم يتم إنشاء ملف PDF: {pdf_path}")
    return pdf_path
