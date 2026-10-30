# -*- coding: utf-8 -*-
"""واجهة إدارة المخططات التعليمية."""
import json
import html
import hashlib
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path
from PySide6.QtCore import (
    QDate, QEvent, QMarginsF, QMimeData, QStandardPaths, Signal,
    QStringListModel, QTimer, Qt,
)
from PySide6.QtGui import (
    QColor, QDrag, QFont, QPageLayout, QPageSize,
    QStandardItem, QStandardItemModel, QTextCharFormat, QTextCursor,
    QTextDocument, QTextFormat, QTextOption,
)
from PySide6.QtPrintSupport import QPrinter
from print_preview import (
    paint_text_document_pages,
    show_application_print_preview,
)
from app_config import (
    DETAIL_KEYS_BY_NAMAT,
    LEVEL_FIRAKS,
    PLANS_NAMES,
    load_custom_font,
)
from busy_overlay import start_background_task
from database import PLAN_ACTIVITY_ALIASES
from class_assignment_ui import add_assignment_editor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QBoxLayout,
    QCalendarWidget,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QFileDialog,
    QPlainTextEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)


WEEKDAYS = (
    ("الإثنين", 0), ("الثلاثاء", 1), ("الأربعاء", 2),
    ("الخميس", 3), ("الجمعة", 4), ("السبت", 5), ("الأحد", 6),
)

PDF_ACTIVITY_CARD_COLORS = (
    ("#2563EB", "#EFF6FF"),
    ("#059669", "#ECFDF5"),
    ("#D97706", "#FFFBEB"),
    ("#7C3AED", "#F5F3FF"),
    ("#DB2777", "#FDF2F8"),
    ("#0891B2", "#ECFEFF"),
    ("#4F46E5", "#EEF2FF"),
    ("#65A30D", "#F7FEE7"),
)
PDF_LESSON_NUMBER_COLORS = {
    "منهجي": ("#1D4ED8", "#DBEAFE"),
    "إدماج": ("#059669", "#ECFDF5"),
    "تقييم": ("#D97706", "#FFFBEB"),
    "دعم وعلاج": ("#7C3AED", "#F5F3FF"),
}
PDF_WIDGET_SPACING = "5"


def _pdf_activity_card_colors(activity_name):
    normalized_name = str(activity_name or "").strip().casefold()
    digest = hashlib.sha256(normalized_name.encode("utf-8")).digest()
    color_index = int.from_bytes(digest[:4], "big") % len(
        PDF_ACTIVITY_CARD_COLORS
    )
    return PDF_ACTIVITY_CARD_COLORS[color_index]


def _keep_activity_widgets_on_same_page(document, page_size):
    page_width = page_size.width()
    page_height = page_size.height()
    if page_width <= 0 or page_height <= 0:
        return

    layout = document.documentLayout()
    always_before = QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore

    for _ in range(document.blockCount() + 1):
        layout.documentSize()
        activity_tables = []

        def collect_tables(frame):
            for child in frame.childFrames():
                if child.__class__.__name__ == "QTextTable":
                    rect = layout.frameBoundingRect(child)
                    is_widget_lesson_row = (
                        child.rows() == 1
                        and child.columns() >= 3
                        and child.columns() % 2 == 1
                        and rect.width() >= page_width * 0.8
                    )
                    is_activity_cards = (
                        child.rows() == 1
                        and bool(child.childFrames())
                        and rect.width() >= page_width * 0.7
                    )
                    if is_widget_lesson_row or is_activity_cards:
                        activity_tables.append((child, rect))
                collect_tables(child)

        collect_tables(document.rootFrame())
        moved_table = False
        for table, rect in activity_tables:
            if rect.height() >= page_height:
                continue
            offset_in_page = rect.y() % page_height
            if (
                offset_in_page + rect.height() > page_height + 0.1
                and not table.format().pageBreakPolicy() & always_before
            ):
                table_format = table.format()
                table_format.setPageBreakPolicy(always_before)
                table.setFormat(table_format)
                moved_table = True
                break
        if not moved_table:
            break


def _pdf_text(value):
    return html.escape(str(value or "").strip()).replace("\n", "<br/>")


def _pdf_inline_text(value):
    return html.escape(" ".join(str(value or "").split()))


def _pdf_rounded_box_html(
    content, border_color, background_color, text_align, width="100%"
):
    return (
        f"<table border='1' style='border-color:{border_color}; "
        "border-collapse:collapse;' align='right' "
        f"width='{width}' cellspacing='0' cellpadding='0' "
        f"bgcolor='{background_color}'>"
        "<tr>"
        f"<td bgcolor='{background_color}' dir='rtl' "
        f"align='{text_align}' style='padding:4px; "
        f"text-align:{text_align};'>{content}</td>"
        "</tr></table>"
    )


def _pdf_detail_text(detail, text_key, name_key):
    return str(detail.get(text_key) or detail.get(name_key) or "").strip()


def _plan_item_display_name(activity_name, scope_class_name=""):
    activity_name = str(activity_name or "").strip()
    scope_class_name = str(scope_class_name or "").strip()
    if scope_class_name:
        return f"{scope_class_name} — {activity_name}"
    return activity_name


def _teaching_plan_name(snapshot):
    return str(snapshot["plan"].get("title") or "المخطط").strip()


def _teaching_plan_class_label(plan):
    class_name = str(plan.get("class_name") or "")
    shared_group_key = str(plan.get("shared_group_key") or "").strip()
    if shared_group_key:
        return (
            f"{shared_group_key} (مشترك؛ الكفاية: {class_name})"
        )
    return class_name


def _teaching_plan_class_ids(plan):
    if len(plan) > 20 and plan[20]:
        return set(plan[20])
    return {plan[2]}


def _suggested_teaching_plan_number(db, snapshot, school_year=None):
    plan = snapshot["plan"]
    title = str(plan.get("title") or "").strip().casefold()
    class_name = str(plan.get("class_name") or "").strip().casefold()
    school_year = (
        school_year or db.school_year_name_for_date()
    )
    used_numbers = {
        row[11] for row in db.teaching_plans()
        if str(row[1] or "").strip().casefold() == title
        and str(row[3] or "").strip().casefold() == class_name
        and row[11] is not None
        and db.school_year_name_for_date(
            date.fromisoformat(str(row[8]))
        ) == school_year
    }
    try:
        source_number = int(plan.get("plan_number") or 0)
    except (TypeError, ValueError):
        source_number = 0
    if source_number > 0 and source_number not in used_numbers:
        return source_number
    return max(used_numbers, default=0) + 1


def _teaching_plan_pdf_html(snapshot, page_width_px):
    plan = snapshot["plan"]
    plan_number = plan.get("plan_number")
    class_name = _teaching_plan_class_label(plan)
    title = (
        f"مخطط {_teaching_plan_name(snapshot)} - عدد "
        f"{plan_number if plan_number is not None else 'غير محدد'} "
        f"القسم - {class_name or ''}"
    )
    content = [
        "<html><head><meta charset='utf-8'/><style>",
        "html, body { direction: rtl; text-align: right; color: #172033; "
        "font-size: 7pt; }",
        "h1 { color: #123B67; font-size: 10pt; text-align: center; "
        "margin: 0 0 8pt 0; }",
        "h2 { color: #123B67; font-size: 8pt; text-align: center; "
        "margin: 10pt 0 5pt 0; }",
        "p { margin: 3pt 0; }",
        "table { width: 100%; border-collapse: collapse; direction: rtl; "
        "text-align: right; }",
        "th, td { border: 0.5pt solid #8795A5; padding: 4pt; "
        "vertical-align: top; direction: rtl; }",
        "th { background-color: #E8F1FA; text-align: right; }",
        ".plan-data th { width: 20%; }",
        ".plan-data td { text-align: center; }",
        ".lesson-table { border: none; border-collapse: collapse; "
        "direction: rtl; }",
        ".activity-widget-lesson-table { border-collapse: collapse; }",
        ".activity-widget-lesson-layout { width: 100%; "
        "border-collapse: collapse; direction: rtl; }",
        ".lesson-number { width: 10%; white-space: nowrap; "
        "text-align: right; vertical-align: middle; font-weight: bold; "
        "color: #1E3A8A; padding: 0; border: none; }",
        ".lesson-content { text-align: right; vertical-align: top; "
        "padding: 0; border: none; }",
        ".activity-widget-lesson-number { width: 60px; white-space: nowrap; "
        "text-align: center; "
        "vertical-align: middle; color: #1D4ED8; }",
        ".activity-cards { border: none; border-collapse: separate; "
        "direction: rtl; }",
        ".symbol { font-weight: bold; color: #123B67; }",
        "</style></head><body dir='rtl' lang='ar'>",
        f"<h2 style='text-align: center; font-size: 8pt;'>{_pdf_text(title)}</h2>",
    ]

    metadata = (
        ("المجال", plan.get("domain_name")),
        ("كفاية المجال", plan.get("domain_competence")),
        ("تاريخ الانطلاق", plan.get("start_date")),
        ("عدد الحصص", plan.get("lesson_count")),
    )
    items = snapshot.get("items", [])
    component_texts = []
    for item in items:
        for detail in item.get("details", []):
            component = _pdf_detail_text(
                detail, "component_text", "component_name"
            )
            if component and component not in component_texts:
                component_texts.append(component)
    component_symbols = {
        component: f"[م{index}]"
        for index, component in enumerate(component_texts, 1)
    }
    content.append("<table width='100%'><tr><td style='width: 33%; align: center;'>")
    for prop in snapshot.get("properties", []):
        key = str(prop.get("key") or "").strip()
        value = str(prop.get("value") or "").strip()
        if key and value and key not in {"تقديم الوحدة", "وضعية الاستكشاف"}:
            metadata += ((key, value),)
    metadata_rows = [
        f"<tr><td>{_pdf_text(value)}</td><th>{_pdf_text(label)}</th></tr>"
        for label, value in metadata if value not in (None, "")
    ]
    if metadata_rows:
        content.extend((
            "<table class='plan-data' dir='rtl'>",
            *metadata_rows,
            "</table>",
        ))
    content.append("</td><td style='width: 34%; align: center;'>")
    if component_symbols:
        content.append(
            "<table class='plan-data' dir='rtl'>"
            "<tr><th colspan='2' style='text-align:center;'>"
            "مفتاح مكونات الكفاية</th></tr>"
        )
        for component, symbol in component_symbols.items():
            content.append(
                f"<tr><td class='symbol' align='center'>"
                f"{_pdf_text(component)}</td>"
                f"<td>{_pdf_text(symbol)}</td></tr>"
            )
        content.append("</table>")
    content.append("</td><td style='width: 33%; align: center;'>")
    content.append(
        "<table width='100%' class='plan-data' dir='rtl'>"
        "<tr><th style='text-align:center;'>الكفاية</th>"
        "<th style='text-align:center;'>النشاط</th></tr>"
    )
    for item in items:
        activity_display_name = _plan_item_display_name(
            item.get("activity_name"),
            item.get("scope_class_name"),
        )
        content.append(
            f"<tr><td align='center'>"
            f"{_pdf_text(item.get('competence_text'))}</td>"
            f"<td align='center'>{_pdf_text(activity_display_name)}</td></tr>"
        )
    content.append("</table>")
    content.append("</td></tr></table>")

    plan_properties = {
        str(prop.get("key") or "").strip():
        str(prop.get("value") or "").strip()
        for prop in snapshot.get("properties", [])
    }
    unit_introduction = plan_properties.get("تقديم الوحدة", "")
    if unit_introduction:
        content.extend((
            "<table class='plan-data' dir='rtl'><tr>"
            "<td dir='rtl' style='text-align:right;'>"
            "<h3 style='text-align:center; font-size:8pt;'>تقديم الوحدة</h3>"
            f"<p dir='rtl' style='text-align:right;'>{_pdf_text(unit_introduction)}</p>"
            "</td></tr></table>",
        ))

    items_by_id = {
        item.get("source_id"): item for item in items
        if item.get("source_id") is not None
    }
    activities_by_mode = defaultdict(list)
    for activity in snapshot.get("activities", []):
        activities_by_mode[activity.get("learning_mode")].append(activity)
    mode_settings = {
        setting.get("learning_mode"): setting
        for setting in snapshot.get("mode_settings", [])
    }
    configured_activities = {
        "إدماج": {
            activity.get("activity_name"): activity
            for activity in snapshot.get("integration_activities", [])
        },
        "تقييم": {
            activity.get("activity_name"): activity
            for activity in snapshot.get("evaluation_activities", [])
        },
        "دعم وعلاج": {
            activity.get("activity_name"): activity
            for activity in snapshot.get("support_activities", [])
        },
    }
    mode_sections = (
        ("منهجي", "حصص التعلم المنهجي"),
        ("إدماج", "حصص التعلم الإدماجي"),
        ("تقييم", "حصص التعلم التقييمي"),
        ("دعم وعلاج", "حصص الدعم والعلاج"),
    )
    mode_counts = plan.get("mode_lesson_counts", {})
    lesson_number_offset = 0
    for mode, heading in mode_sections:
        uses_activity_widget_layout = mode in {
            "منهجي", "إدماج", "تقييم", "دعم وعلاج"
        }
        try:
            lesson_count = max(0, int(mode_counts.get(mode, 0) or 0))
        except (TypeError, ValueError):
            lesson_count = 0
        lesson_number_offset += lesson_count
        content.append(
            f"<h3 style='text-align: center; font-size: 8pt;'>{_pdf_text(heading)} "
            f"<b>(عدد الحصص: {lesson_count})</b></h3>"
        )
        if not lesson_count:
            continue
        table_class = (
            "lesson-table activity-widget-lesson-table"
            if uses_activity_widget_layout else "lesson-table"
        )
        content.append(
            f"<table class='{table_class}' dir='rtl' border='0' "
            "width='100%' "
            f"cellspacing='{0 if uses_activity_widget_layout else PDF_WIDGET_SPACING}' "
            "cellpadding='0'>"
        )
        for mode_lesson_number in range(1, lesson_count + 1):
            lesson_number = (
                lesson_number_offset - lesson_count + mode_lesson_number
            )
            lesson_activities = [
                activity for activity in activities_by_mode.get(mode, [])
                if activity.get("lesson_number") == mode_lesson_number
            ]
            activity_cards = []
            if uses_activity_widget_layout and lesson_activities:
                page_width_points = page_width_px * 72 / 96
                available_width = max(
                    0,
                    page_width_points - 60
                    - int(PDF_WIDGET_SPACING)
                    * (len(lesson_activities) + 2),
                )
                card_width = (
                    available_width / len(lesson_activities)
                )
            else:
                card_width = (
                    100 / len(lesson_activities)
                    if lesson_activities else 100
                )
            for activity in lesson_activities:
                item = items_by_id.get(activity.get("item_source_id"))
                activity_display_name = _plan_item_display_name(
                    activity.get("activity_name") or "نشاط",
                    item.get("scope_class_name") if item else "",
                )
                border_color, background_color = (
                    _pdf_activity_card_colors(activity.get("activity_name"))
                )
                activity_name = _pdf_text(activity_display_name)
                line_parts = []
                if mode == "منهجي":
                    details = item.get("details", []) if item else []
                    components = list(dict.fromkeys(
                        component_symbols.get(
                            _pdf_detail_text(
                                detail, "component_text", "component_name"
                            )
                        )
                        for detail in details
                        if _pdf_detail_text(
                            detail, "component_text", "component_name"
                        )
                    ))
                    if components:
                        line_parts.append(
                            f"مكوّن الكفاية: {', '.join(components)}"
                        )
                    specific_objective = str(
                        activity.get("specific_objective") or ""
                    ).strip()
                    objectives = (
                        [specific_objective] if specific_objective else []
                    )
                    if objectives:
                        line_parts.append(
                            f"الهدف المميز: {', '.join(objectives)}"
                        )
                    activity_content = str(
                        activity.get("content") or ""
                    ).strip()
                    if activity_content:
                        line_parts.append(
                            f"المحتوى: {activity_content}"
                        )
                    for key, label in (
                        ("lesson_goal", "هدف الحصة"),
                        ("target_competencies", "الكفايات المستهدفة"),
                        ("evaluation", "التقييم"),
                        ("expected_performance", "الأداء المنتظر"),
                        ("target_criteria", "المعايير المستهدفة"),
                        ("demographic_concept", "المفهوم السكاني"),
                        ("health_concept", "المفهوم الصحي"),
                        ("life_skills", "المهارات الحياتية"),
                    ):
                        value = activity.get(key)
                        if value not in (None, ""):
                            line_parts.append(f"{label}: {value}")
                    for prop in activity.get("properties", []):
                        key = str(prop.get("key") or "").strip()
                        value = str(prop.get("value") or "").strip()
                        if key and value:
                            line_parts.append(f"{key}: {value}")
                else:
                    configured = configured_activities.get(mode, {}).get(
                        activity.get("activity_name"), {}
                    )
                    if mode == "إدماج":
                        label = "الكفايات المستهدفة"
                        keys = (
                            "target_criteria", "target_competencies",
                            "target_text",
                        )
                    elif mode == "تقييم":
                        label = "الأداء المنتظر"
                        keys = ("expected_performance", "target_text")
                    else:
                        label = "المعايير المستهدفة"
                        keys = ("target_criteria",)
                    value = next(
                        (
                            activity.get(key) or configured.get(key)
                            for key in keys
                            if activity.get(key) or configured.get(key)
                        ),
                        "",
                    )
                    if mode == "تقييم" and not value:
                        value = mode_settings.get(mode, {}).get(
                            "expected_performance", ""
                        )
                    if value:
                        line_parts.append(f"{label}: {value}")
                if uses_activity_widget_layout:
                    line = (
                        "<p align='center' "
                        "style='margin-top:0; margin-bottom:3pt;'>"
                        f"<b>{activity_name}: "
                        f"{activity.get('duration_minutes')} دقيقة</b></p>"
                    )
                    if line_parts:
                        line += (
                            "<p align='center' dir='rtl' "
                            "style='margin-top:0; margin-bottom:3pt; "
                            "color:#334155;'>"
                            f"{_pdf_inline_text(' _ '.join(line_parts))}</p>"
                        )
                    card_alignment = "center"
                else:
                    line = (
                        f"<font color='{border_color}'>"
                        f"<b>{activity_name}</b></font>"
                    )
                    if line_parts:
                        line += "".join(
                            f"<p style='margin:4pt 0 0 0; color:#334155;'>"
                            f"{_pdf_text(part)}</p>"
                            for part in line_parts
                        )
                    card_alignment = "right"
                if uses_activity_widget_layout:
                    activity_cards.append(
                        f"<td width='{card_width:.2f}' "
                        f"bgcolor='{background_color}' dir='rtl' "
                        f"align='{card_alignment}' "
                        f"style='border:1px solid {border_color}; "
                        "padding:4px 4px 0 4px; direction:rtl;'>"
                        f"{line}</td>"
                    )
                else:
                    card_html = _pdf_rounded_box_html(
                        line, border_color, background_color,
                        card_alignment
                    )
                    activity_cards.append(
                        f"<td width='{card_width:.2f}%' valign='top' "
                        f"style='direction:rtl;'>{card_html}</td>"
                    )
            activity_table = ""
            if activity_cards:
                if uses_activity_widget_layout:
                    spacer = (
                        f"<td width='{PDF_WIDGET_SPACING}' "
                        "style='border:0; padding:0;'></td>"
                    )
                    activity_table = (
                        spacer
                        + spacer.join(reversed(activity_cards))
                        + spacer
                    )
                else:
                    activity_table = (
                        "<table class='activity-cards' dir='rtl' "
                        "width='100%' border='0' "
                        f"cellspacing='{PDF_WIDGET_SPACING}' "
                        "cellpadding='0'><tr>"
                        f"{''.join(reversed(activity_cards))}</tr></table>"
                    )
            elif uses_activity_widget_layout:
                page_width_points = page_width_px * 72 / 96
                activity_table = (
                    f"<td width='{max(0, page_width_points - 65):.2f}' "
                    "style='border:0; padding:0;'>"
                    "<span style='font-size:1pt;'>&nbsp;</span></td>"
                )
            number_border, number_background = PDF_LESSON_NUMBER_COLORS[
                mode
            ]
            if uses_activity_widget_layout:
                lesson_number_cell = (
                    f"<td class='lesson-number activity-widget-lesson-number' "
                    "width='60' bgcolor='"
                    f"{number_background}' style='width:60px; "
                    f"border:1px solid {number_border}; "
                    "vertical-align:middle; text-align:center;'>"
                    f"<b><font color='{number_border}'>"
                    f"{_pdf_text(lesson_number)}</font></b></td>"
                )
            else:
                lesson_number_cell = (
                    f"<td class='lesson-number' width='40px' "
                    "style='vertical-align:middle;'>"
                    "<table border='1' cellspacing='0' cellpadding='0' "
                    f"width='100%' bgcolor='{number_background}' "
                    f"style='border-color:{number_border}; "
                    "border-collapse:collapse;'>"
                    f"<tr><td align='center' dir='rtl' "
                    f"bgcolor='{number_background}' "
                    f"style='border-color:{number_border}; "
                    "padding:4px;'>"
                    f"<b><font color='{number_border}'>"
                    f"{_pdf_text(lesson_number)}</font></b></td></tr>"
                    "</table></td>"
                )
            content.append(
                "<tr>"
                + (
                    "<td class='activity-widget-lesson-row' "
                    "style='padding:0; border:0;'>"
                    "<table class='activity-widget-lesson-layout' dir='rtl' "
                    "width='100%' border='0' "
                    "cellspacing='0' cellpadding='0'>"
                    "<tr>"
                    f"{activity_table}{lesson_number_cell}"
                    "<td width='"
                    f"{PDF_WIDGET_SPACING}' "
                    "style='border:0; padding:0;'></td>"
                    "</tr></table></td>"
                    if uses_activity_widget_layout
                    else (
                        f"<td class='lesson-content'>{activity_table}</td>"
                        f"{lesson_number_cell}"
                    )
                )
                + "</tr>"
            )
            if uses_activity_widget_layout:
                content.append(
                    "<tr><td style='border:0; padding:0;'>"
                    "<p style='margin:0; font-size:1pt;'>&nbsp;</p>"
                    "<p style='margin:0; font-size:1pt;'>&nbsp;</p>"
                    "</td></tr>"
                )
        content.append("</table>")

    exploration = plan_properties.get("وضعية الاستكشاف", "")
    if exploration:
        content.extend((
            "<table class='plan-data' dir='rtl'><tr>"
            "<td dir='rtl' style='text-align:right;'>"
            "<h3 style='text-align:center; font-size:8pt;'>وضعية الاستكشاف</h3>"
            f"<p dir='rtl' style='text-align:center;'>{_pdf_text(exploration)}</p>"
            "</td></tr></table>",
        ))
    content.append("</body></html>")
    return "".join(content)


def _teaching_plan_document(snapshot, page_size):
    if not isinstance(snapshot, dict) or not isinstance(
        snapshot.get("plan"), dict
    ):
        raise ValueError("بيانات المخطط غير صالحة لتصدير PDF.")
    document = QTextDocument()
    document.setDefaultFont(QFont(load_custom_font(), 9))
    document.setDocumentMargin(0)
    text_option = QTextOption()
    text_option.setTextDirection(Qt.RightToLeft)
    document.setDefaultTextOption(text_option)
    document.setPageSize(page_size)
    page_width_px = page_size.width() * 96 / 72
    document.setHtml(
        _teaching_plan_pdf_html(snapshot, page_width_px)
    )
    _keep_activity_widgets_on_same_page(document, page_size)
    return document


def _teaching_plan_printer():
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setPageOrientation(QPageLayout.Orientation.Landscape)
    printer.setPageMargins(
        QMarginsF(5.0, 5.0, 5.0, 5.0),
        QPageLayout.Unit.Millimeter,
    )
    if printer.pageLayout().orientation() != QPageLayout.Orientation.Landscape:
        raise OSError("تعذر ضبط اتجاه صفحات المخطط إلى أفقي.")
    return printer


def print_teaching_plan(snapshot, parent=None):
    printer = _teaching_plan_printer()
    page_size = printer.pageLayout().paintRect(
        QPageLayout.Unit.Point
    ).size()
    document = _teaching_plan_document(snapshot, page_size)
    page_count = document.pageCount()
    if page_count < 1:
        raise ValueError("لا توجد صفحات جاهزة للطباعة.")

    def paint_document(target_printer, page_numbers):
        paint_text_document_pages(
            target_printer, document, page_numbers
        )

    show_application_print_preview(
        parent,
        printer,
        page_count,
        paint_document,
        QPageLayout.Orientation.Landscape,
        "معاينة طباعة المخطط",
    )


def export_teaching_plan_pdf(snapshot, output_path):
    printer = _teaching_plan_printer()
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(output_path))
    page_size = printer.pageLayout().paintRect(
        QPageLayout.Unit.Point
    ).size()
    document = _teaching_plan_document(snapshot, page_size)
    document.print_(printer)
    path = Path(output_path)
    if (
        printer.printerState() == QPrinter.PrinterState.Error
        or not path.is_file()
        or path.stat().st_size < 1000
    ):
        raise OSError("لم يتم إنشاء ملف PDF صالح.")
    return path


class TeachingPlanImportOptionsDialog(QDialog):
    def __init__(
        self, db, snapshot, suggested_number, parent=None,
        choose_class=False,
    ):
        super().__init__(parent)
        self.db = db
        self.snapshot = snapshot
        self.setWindowTitle("إعداد المخطط المستورد")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(560)

        plan = snapshot["plan"]
        self.choose_class = choose_class
        layout = QVBoxLayout(self)
        form = QFormLayout()

        if choose_class:
            level_name = str(plan.get("level_name") or "").strip()
            level_id = next(
                (
                    candidate_id
                    for candidate_id, candidate_name in db.levels()
                    if str(candidate_name).strip().casefold()
                    == level_name.casefold()
                ),
                None,
            )
            registered_classes = [
                (class_id, class_name)
                for class_id, class_name, class_level_id in db.classes()
                if class_level_id == level_id
            ] if level_id is not None else []
            if not registered_classes:
                raise ValueError(
                    "لا توجد أقسام مسجلة من مستوى المخطط لاختيار القسم."
                )
            self.class_combo = QComboBox()
            self.class_combo.addItem("اختر القسم", None)
            for class_id, class_name in registered_classes:
                self.class_combo.addItem(class_name, class_id)
            source_class_index = self.class_combo.findText(
                str(plan.get("class_name") or "").strip(),
                Qt.MatchFlag.MatchFixedString,
            )
            if source_class_index >= 0:
                self.class_combo.setCurrentIndex(source_class_index)
            form.addRow("القسم:", self.class_combo)

        self.school_year_combo = QComboBox()
        self.school_year_combo.setEditable(True)
        years = {
            str(year_name) for _year_id, year_name in db.school_years()
        }
        current_year = db.school_year_name_for_date()
        years.add(current_year)
        years.add(str(plan.get("school_year") or ""))
        years.discard("")
        for year_name in sorted(years, reverse=True):
            self.school_year_combo.addItem(year_name, year_name)
        self.school_year_combo.setCurrentText(current_year)
        form.addRow("السنة الدراسية:", self.school_year_combo)

        self.plan_number_spin = QSpinBox()
        self.plan_number_spin.setRange(1, 99999)
        self.plan_number_spin.setValue(suggested_number)
        form.addRow("عدد المخطط:", self.plan_number_spin)
        self.school_year_combo.currentTextChanged.connect(
            self.update_suggested_number_for_class
        )
        if choose_class:
            self.class_combo.currentTextChanged.connect(
                self.update_suggested_number_for_class
            )

        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        source_date = QDate.fromString(
            str(plan.get("start_date") or ""), "yyyy-MM-dd"
        )
        self.start_date_edit.setDate(
            source_date if source_date.isValid() else QDate.currentDate()
        )
        self.school_year_combo.currentTextChanged.connect(
            self.adjust_start_date_for_school_year
        )
        self.adjust_start_date_for_school_year(current_year)
        form.addRow("تاريخ الانطلاق:", self.start_date_edit)
        layout.addLayout(form)

        weekdays_group = QGroupBox("أيام التدريس وعدد الحصص اليومية")
        weekdays_grid = QGridLayout(weekdays_group)
        weekdays_grid.addWidget(QLabel("اليوم"), 0, 0)
        weekdays_grid.addWidget(QLabel("تدريس"), 0, 1)
        weekdays_grid.addWidget(QLabel("عدد الحصص"), 0, 2)
        source_weekdays = plan.get("weekdays", [])
        source_daily_counts = plan.get("lessons_per_day")
        if not isinstance(source_weekdays, list):
            raise ValueError("أيام التدريس في المخطط المستورد غير صالحة.")
        if isinstance(source_daily_counts, dict):
            try:
                daily_counts = {
                    int(day): int(count)
                    for day, count in source_daily_counts.items()
                }
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "أعداد الحصص اليومية في المخطط المستورد غير صالحة."
                ) from error
        else:
            try:
                daily_counts = {int(day): 1 for day in source_weekdays}
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "أيام التدريس في المخطط المستورد غير صالحة."
                ) from error
        if any(
            day < 0 or day > 6 or count < 1
            for day, count in daily_counts.items()
        ):
            raise ValueError(
                "أيام التدريس أو أعداد الحصص اليومية في المخطط المستورد غير صالحة."
            )
        self.weekday_rows = {}
        for row_index, (day_name, day_number) in enumerate(WEEKDAYS, 1):
            day_label = QLabel(day_name)
            check = QCheckBox()
            spin = QSpinBox()
            spin.setRange(1, 999)
            spin.setValue(max(1, daily_counts.get(day_number, 1)))
            check.setChecked(day_number in daily_counts)
            spin.setEnabled(check.isChecked())
            check.toggled.connect(spin.setEnabled)
            weekdays_grid.addWidget(day_label, row_index, 0)
            weekdays_grid.addWidget(check, row_index, 1)
            weekdays_grid.addWidget(spin, row_index, 2)
            self.weekday_rows[day_number] = (check, spin)
        layout.addWidget(weekdays_group)

        buttons = QHBoxLayout()
        save_button = QPushButton("متابعة")
        _set_action_type(save_button, "save")
        _style_primary_button(save_button)
        save_button.clicked.connect(self.accept)
        cancel_button = QPushButton("إلغاء")
        _set_action_type(cancel_button, "cancel")
        cancel_button.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

    def adjust_start_date_for_school_year(self, school_year):
        try:
            start_year_text, end_year_text = school_year.strip().split("-", 1)
            start_year = int(start_year_text)
            end_year = int(end_year_text)
        except (AttributeError, TypeError, ValueError):
            return
        if end_year != start_year + 1:
            return
        current = self.start_date_edit.date()
        current_year = (
            current.year() if current.month() >= 7 else current.year() - 1
        )
        if current_year == start_year:
            return
        target_year = (
            start_year if current.month() >= 7 else start_year + 1
        )
        target = QDate(target_year, current.month(), current.day())
        if not target.isValid():
            target = QDate(target_year, current.month(), 1).addMonths(1).addDays(-1)
        self.start_date_edit.setDate(target)

    def update_suggested_number_for_class(self, *_args):
        if not hasattr(self, "plan_number_spin"):
            return
        snapshot = dict(self.snapshot)
        plan = dict(snapshot["plan"])
        if self.choose_class:
            class_name = self.class_combo.currentText().strip()
            if not class_name:
                return
            plan["class_name"] = class_name
        snapshot["plan"] = plan
        self.plan_number_spin.setValue(
            _suggested_teaching_plan_number(
                self.db,
                snapshot,
                self.school_year_combo.currentText().strip(),
            )
        )

    def selected_class_name(self):
        if not self.choose_class:
            return None
        class_name = self.class_combo.currentText().strip()
        if self.class_combo.currentData() is None or not class_name:
            raise ValueError("اختر القسم المسجل الذي سيُنسخ إليه المخطط.")
        return class_name

    def values(self):
        year_name = self.school_year_combo.currentText().strip()
        try:
            start_year_text, end_year_text = year_name.split("-", 1)
            start_year = int(start_year_text)
            end_year = int(end_year_text)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("اسم السنة الدراسية يجب أن يكون مثل 2026-2027.") from error
        if end_year != start_year + 1:
            raise ValueError("اسم السنة الدراسية غير صالح.")
        start_date = self.start_date_edit.date()
        actual_start_year = (
            start_date.year() if start_date.month() >= 7
            else start_date.year() - 1
        )
        if actual_start_year != start_year:
            raise ValueError(
                "يجب أن يقع تاريخ الانطلاق ضمن السنة الدراسية المختارة."
            )
        weekly_schedule = {
            day: spin.value()
            for day, (check, spin) in self.weekday_rows.items()
            if check.isChecked()
        }
        if not weekly_schedule:
            raise ValueError("حدد يوماً واحداً على الأقل للتدريس.")
        return (
            year_name,
            self.plan_number_spin.value(),
            start_date.toString("yyyy-MM-dd"),
            weekly_schedule,
        )


class TeachingPlanViewPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setAcceptDrops(True)
        self.setLayoutDirection(Qt.RightToLeft)

        layout = QVBoxLayout(self)
        selectors = QHBoxLayout()
        selectors.setSpacing(12)
        selectors.addStretch(1)
        self.school_year_combo = QComboBox()
        self.class_combo = QComboBox()
        self.domain_combo = QComboBox()
        self.name_combo = QComboBox()
        self.unit_number_combo = QComboBox()
        selector_fields = (
            ("السنة الدراسية", self.school_year_combo, 200),
            ("القسم", self.class_combo, 380),
            ("المجال", self.domain_combo, 230),
            ("اسم المخطط", self.name_combo, 250),
            ("عدد الوحدة", self.unit_number_combo, 180),
        )
        self.selector_fields = []
        for label, combo, maximum_width in selector_fields:
            combo.setMinimumWidth(min(180, maximum_width))
            combo.setMaximumWidth(maximum_width)
            combo.setLayoutDirection(Qt.RightToLeft)
            combo.currentIndexChanged.connect(self.update_plan_filters)
            field = QWidget()
            field.setMaximumWidth(maximum_width)
            field.setSizePolicy(
                QSizePolicy.Policy.Maximum,
                QSizePolicy.Policy.Preferred,
            )
            field_layout = QVBoxLayout(field)
            field_layout.setContentsMargins(0, 0, 0, 0)
            caption = QLabel(label)
            caption.setStyleSheet(
                "font-weight: bold; color: #334155;"
            )
            field_layout.addWidget(caption)
            if combo is self.class_combo:
                assignment_editor = add_assignment_editor(
                    combo,
                    self.db,
                    self,
                    self.refresh_plans,
                )
                assignment_editor.setMaximumWidth(maximum_width)
                field_layout.addWidget(assignment_editor)
            else:
                field_layout.addWidget(combo)
            selectors.addWidget(field)
            self.selector_fields.append(field)
        selectors.addStretch(1)
        layout.addLayout(selectors)

        actions = QHBoxLayout()
        self.export_json_button = QPushButton("تصدير JSON")
        _set_action_type(self.export_json_button, "export")
        self.export_json_button.clicked.connect(self.export_selected_json)
        actions.addWidget(self.export_json_button)

        self.export_pdf_button = QPushButton("تصدير PDF")
        _set_action_type(self.export_pdf_button, "export")
        self.export_pdf_button.clicked.connect(self.export_selected_pdf)
        actions.addWidget(self.export_pdf_button)

        self.print_plan_button = QPushButton("طباعة المخطط")
        _set_action_type(self.print_plan_button, "print")
        self.print_plan_button.clicked.connect(self.print_selected_plan)
        actions.addWidget(self.print_plan_button)

        self.import_json_button = QPushButton("استيراد JSON")
        _set_action_type(self.import_json_button, "browse")
        self.import_json_button.clicked.connect(self.import_plan_file)
        actions.addWidget(self.import_json_button)

        self.copy_plan_button = QPushButton("نسخ")
        _set_action_type(self.copy_plan_button, "copy_paste")
        self.copy_plan_button.clicked.connect(self.copy_selected_plan)
        actions.addWidget(self.copy_plan_button)

        self.paste_plan_button = QPushButton("لصق")
        _set_action_type(self.paste_plan_button, "copy_paste")
        self.paste_plan_button.clicked.connect(self.paste_plan)
        actions.addWidget(self.paste_plan_button)

        self.delete_plan_button = QPushButton("مسح المخطط")
        _set_action_type(self.delete_plan_button, "delete")
        self.delete_plan_button.clicked.connect(self.delete_selected_plan)
        actions.addWidget(self.delete_plan_button)
        actions.addStretch()
        layout.addLayout(actions)

        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setAcceptDrops(False)
        self.content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content_container = QWidget()
        self.content_container.setAcceptDrops(False)
        self.content_container.setLayoutDirection(Qt.RightToLeft)
        self.content_layout = QVBoxLayout(self.content_container)
        self.content_layout.setContentsMargins(12, 12, 12, 20)
        self.content_layout.setSpacing(14)
        self.content_scroll.setWidget(self.content_container)
        layout.addWidget(self.content_scroll, 1)
        self.school_year_combo.currentIndexChanged.connect(
            self.update_plan_filters
        )
        QApplication.clipboard().dataChanged.connect(
            self.update_action_states
        )
        self.refresh_plans()

    @staticmethod
    def _is_json_file_drop(event):
        mime_data = event.mimeData()
        if not mime_data.hasUrls():
            return False
        urls = mime_data.urls()
        return (
            len(urls) == 1
            and urls[0].isLocalFile()
            and urls[0].toLocalFile().lower().endswith(".json")
        )

    def dragEnterEvent(self, event):
        if self._is_json_file_drop(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._is_json_file_drop(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not self._is_json_file_drop(event):
            event.ignore()
            return
        event.acceptProposedAction()
        self.import_plan_file(
            event.mimeData().urls()[0].toLocalFile()
        )

    def refresh_plans(self, selected_plan_id=None):
        selected_ids = (
            self.school_year_combo.currentData(),
            self.class_combo.currentData(),
            self.domain_combo.currentData(),
            self.name_combo.currentData(),
            self.unit_number_combo.currentData(),
        )
        self._plan_rows = self.db.teaching_plans()
        if selected_plan_id is not None:
            selected_plan = next(
                (
                    row for row in self._plan_rows
                    if row[0] == selected_plan_id
                ),
                None,
            )
            if selected_plan is not None:
                selected_ids = (
                    self.db.school_year_name_for_date(
                        date.fromisoformat(str(selected_plan[8]))
                    ),
                    (
                        f"group:{selected_plan[19]}"
                        if len(selected_plan) > 19 and selected_plan[19]
                        else selected_plan[2]
                    ),
                    selected_plan[4],
                    selected_plan[1],
                    selected_plan[11] if selected_plan[11] is not None else -1,
                )
        years = {
            str(year_name)
            for _year_id, year_name in self.db.school_years()
        }
        years.update(
            self.db.school_year_name_for_date(
                date.fromisoformat(str(row[8]))
            )
            for row in self._plan_rows
        )
        current_year = self.db.school_year_name_for_date()
        years.add(current_year)
        self._set_combo_options(
            self.school_year_combo,
            "السنة الدراسية",
            [(year, year) for year in sorted(years, reverse=True)],
            selected_ids[0] or current_year,
        )
        selected_ids = (
            self.school_year_combo.currentData(),
            *selected_ids[1:],
        )
        self.update_plan_filters(
            selected_ids=selected_ids
        )

    @staticmethod
    def _set_combo_options(combo, placeholder, options, selected=None):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(placeholder, None)
        for value, label in options:
            combo.addItem(str(label), value)
        index = combo.findData(selected)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def update_plan_filters(self, *_args, selected_ids=None):
        if not hasattr(self, "_plan_rows"):
            return
        if selected_ids is None:
            selected_ids = (
                self.school_year_combo.currentData(),
                self.class_combo.currentData(),
                self.domain_combo.currentData(),
                self.name_combo.currentData(),
                self.unit_number_combo.currentData(),
            )
        school_year, class_id, domain_id, plan_name, unit_number = selected_ids
        year_rows = [
            row for row in self._plan_rows
            if school_year is None
            or self.db.school_year_name_for_date(
                date.fromisoformat(str(row[8]))
            ) == school_year
        ]
        year_id_by_name = {
            str(year_name): year_id
            for year_id, year_name in self.db.school_years()
        }
        selected_year_id = year_id_by_name.get(
            str(school_year), self.db.year_id
        )
        groups = self.db.class_groups()
        self._class_labels = {}
        for option in self.db.teaching_assignment_classes(
            selected_year_id, expand_groups=True
        ):
            label = f"{option['name']} — {option['level_name']}"
            if option["group_key"] in groups:
                label = f"{label} ({groups[option['group_key']]['level_label']})"
            self._class_labels[option["id"]] = label
        for group_key in dict.fromkeys(
            row[19] for row in year_rows
            if len(row) > 19 and row[19]
        ):
            self._class_labels[f"group:{group_key}"] = (
                f"{group_key} (مشترك بين القسمين)"
            )
        class_options = self._class_filter_options(
            year_rows, self._class_labels
        )
        self._set_combo_options(
            self.class_combo,
            "اختر القسم",
            class_options,
            class_id,
        )
        class_id = self.class_combo.currentData()
        class_rows = [
            row for row in year_rows
            if TeachingPlanViewPage._plan_matches_class_filter(
                row, class_id
            )
        ]
        domains = list(dict.fromkeys(
            (row[4], row[5]) for row in class_rows
        ))
        self._set_combo_options(
            self.domain_combo, "اختر المجال", domains,
            domain_id,
        )
        domain_id = self.domain_combo.currentData()

        domain_rows = [
            row for row in class_rows
            if domain_id is None or row[4] == domain_id
        ]
        names = list(dict.fromkeys((row[1], row[1]) for row in domain_rows))
        self._set_combo_options(
            self.name_combo, "اختر اسم المخطط", names,
            plan_name,
        )
        plan_name = self.name_combo.currentData()

        name_rows = [
            row for row in domain_rows
            if plan_name is None or row[1] == plan_name
        ]
        numbers = list(dict.fromkeys(
            (
                row[11] if row[11] is not None else -1,
                f"عدد {row[11]}" if row[11] is not None else "غير محدد",
            )
            for row in name_rows
        ))
        self._set_combo_options(
            self.unit_number_combo, "اختر عدد الوحدة", numbers,
            unit_number,
        )
        self.display_selected_plan()

    def matching_plan(self):
        school_year = self.school_year_combo.currentData()
        class_id = self.class_combo.currentData()
        domain_id = self.domain_combo.currentData()
        plan_name = self.name_combo.currentData()
        unit_number = self.unit_number_combo.currentData()
        if (
            school_year is None or class_id is None or domain_id is None
            or plan_name is None or unit_number is None
        ):
            return None
        return next(
            (
                row for row in self._plan_rows
                if TeachingPlanViewPage._plan_matches_class_filter(
                    row, class_id
                )
                and row[4] == domain_id
                and row[1] == plan_name
                and self.db.school_year_name_for_date(
                    date.fromisoformat(str(row[8]))
                ) == school_year
                and (row[11] if row[11] is not None else -1) == unit_number
            ),
            None,
        )

    @staticmethod
    def _class_filter_options(rows, class_labels):
        options = [
            (class_id, class_labels[class_id])
            for class_id in dict.fromkeys(
                class_id
                for row in rows
                for class_id in _teaching_plan_class_ids(row)
                if class_id in class_labels
            )
        ]
        options.extend(
            (selection, class_labels[selection])
            for selection in class_labels
            if isinstance(selection, str)
            and selection.startswith("group:")
        )
        return options

    @staticmethod
    def _plan_matches_class_filter(row, class_selection):
        if class_selection is None:
            return True
        if (
            isinstance(class_selection, str)
            and class_selection.startswith("group:")
        ):
            return (
                len(row) > 19
                and row[19] == class_selection[len("group:"):]
            )
        return class_selection in _teaching_plan_class_ids(row)

    @staticmethod
    def _is_teaching_plan_snapshot(text):
        try:
            snapshot = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return False
        return (
            isinstance(snapshot, dict)
            and snapshot.get("format") == "jrnl_teaching_plan"
            and snapshot.get("version") == 1
            and isinstance(snapshot.get("plan"), dict)
        )

    def update_action_states(self, *_args):
        selected = self.matching_plan() is not None
        for button in (
            self.export_json_button,
            self.export_pdf_button,
            self.print_plan_button,
            self.copy_plan_button,
            self.delete_plan_button,
        ):
            button.setEnabled(selected)
        self.paste_plan_button.setEnabled(
            self._is_teaching_plan_snapshot(
                QApplication.clipboard().text()
            )
        )

    def display_selected_plan(self, *_args):
        matching_plan = self.matching_plan()
        if matching_plan is None:
            self._clear_content()
            prompt = QLabel(
                "اختر السنة الدراسية والقسم والمجال واسم المخطط وعدد الوحدة لعرضه."
            )
            prompt.setAlignment(Qt.AlignCenter)
            prompt.setStyleSheet("color: #64748B; padding: 36px;")
            self.content_layout.addWidget(prompt)
            self.update_action_states()
            return
        plan_id = matching_plan[0]
        self._clear_content()
        try:
            snapshot = self.db.export_teaching_plan(int(plan_id))
            self._render_snapshot(snapshot)
        except (ValueError, sqlite3.Error) as error:
            QMessageBox.warning(
                self, "تعذر عرض المخطط", str(error)
            )
        self.update_action_states()

    def selected_snapshot(self):
        plan = self.matching_plan()
        if plan is None:
            QMessageBox.information(
                self, "اختيار مطلوب", "حدد مخططاً أولاً."
            )
            return None
        try:
            return self.db.export_teaching_plan(plan[0])
        except (ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "تعذر تحميل المخطط", str(error))
            return None

    def export_selected_json(self):
        snapshot = self.selected_snapshot()
        if snapshot is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "تصدير المخطط", "", "ملفات JSON (*.json)"
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        def write_snapshot():
            with open(path, "w", encoding="utf-8") as file:
                json.dump(snapshot, file, ensure_ascii=False, indent=2)

        start_background_task(
            self,
            write_snapshot,
            lambda _result: QMessageBox.information(
                self, "اكتمل التصدير", "تم تصدير المخطط بنجاح."
            ),
            lambda error: QMessageBox.warning(
                self, "تعذر تصدير المخطط", error
            ),
        )

    def export_selected_pdf(self):
        snapshot = self.selected_snapshot()
        if snapshot is None:
            return
        suggested_path = (
            f"{_teaching_plan_name(snapshot)} - "
            f"{snapshot['plan'].get('class_name') or ''}.pdf"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "تصدير المخطط PDF", suggested_path, "ملفات PDF (*.pdf)"
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        start_background_task(
            self,
            lambda: export_teaching_plan_pdf(snapshot, path),
            lambda _result: QMessageBox.information(
                self, "اكتمل التصدير", f"تم تصدير المخطط إلى:\n{path}"
            ),
            lambda error: QMessageBox.warning(
                self, "تعذر تصدير المخطط", error
            ),
        )

    def print_selected_plan(self):
        matching_plan = self.matching_plan()
        if matching_plan is None:
            QMessageBox.information(
                self, "اختيار مطلوب", "حدد مخططاً أولاً."
            )
            return
        plan_id = matching_plan[0]
        start_background_task(
            self,
            lambda: self.db.export_teaching_plan(plan_id),
            lambda snapshot: self._show_print_preview(snapshot),
            lambda error: QMessageBox.warning(
                self, "تعذرت طباعة المخطط", error
            ),
            keep_overlay_until_window=True,
        )

    def _show_print_preview(self, snapshot):
        try:
            print_teaching_plan(snapshot, self)
        except (OSError, ValueError, sqlite3.Error, RuntimeError) as error:
            QMessageBox.warning(self, "تعذرت طباعة المخطط", str(error))

    def copy_selected_plan(self):
        snapshot = self.selected_snapshot()
        if snapshot is None:
            return
        QApplication.clipboard().setText(
            json.dumps(snapshot, ensure_ascii=False, indent=2)
        )
        QMessageBox.information(
            self, "اكتمل النسخ", "تم نسخ بيانات المخطط إلى الحافظة."
        )

    def delete_selected_plan(self):
        matching_plan = self.matching_plan()
        if matching_plan is None:
            return
        answer = QMessageBox.question(
            self,
            "تأكيد مسح المخطط",
            "سيُحذف المخطط المحدد وحصصه وخصائصه الإضافية نهائياً. هل تريد المتابعة؟",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.db.delete_teaching_plan(int(matching_plan[0]))
        except (ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "تعذر مسح المخطط", str(error))
            return
        self.refresh_plans()

    def paste_plan(self):
        text = QApplication.clipboard().text()
        if not self._is_teaching_plan_snapshot(text):
            QMessageBox.information(
                self, "لا يوجد مخطط منسوخ",
                "انسخ مخططاً أو JSON صالحاً إلى الحافظة أولاً.",
            )
            return
        try:
            snapshot = json.loads(text)
        except json.JSONDecodeError as error:
            QMessageBox.warning(self, "تعذر قراءة المخطط", str(error))
            return
        self.import_snapshot(snapshot, choose_class=True)

    def import_plan_file(self, path=None):
        if not isinstance(path, str):
            path, _ = QFileDialog.getOpenFileName(
                self, "استيراد مخطط", "", "ملفات JSON (*.json)"
            )
        if not path:
            return
        if not path.lower().endswith(".json"):
            QMessageBox.warning(
                self, "ملف غير صالح", "اختر ملفاً بامتداد JSON."
            )
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as file:
                snapshot = json.load(file)
        except (OSError, json.JSONDecodeError) as error:
            QMessageBox.warning(self, "تعذر قراءة ملف المخطط", str(error))
            return
        self.import_snapshot(snapshot)

    def suggested_import_number(self, snapshot):
        return _suggested_teaching_plan_number(self.db, snapshot)

    def import_snapshot(self, snapshot, choose_class=False):
        try:
            valid_snapshot = self._is_teaching_plan_snapshot(
                json.dumps(snapshot, ensure_ascii=False)
            )
        except (TypeError, ValueError):
            valid_snapshot = False
        if not valid_snapshot:
            QMessageBox.warning(
                self, "ملف غير صالح",
                "صيغة ملف JSON غير مدعومة للمخططات التعليمية.",
            )
            return
        try:
            dialog = TeachingPlanImportOptionsDialog(
                self.db,
                snapshot,
                self.suggested_import_number(snapshot),
                self,
                choose_class=choose_class,
            )
        except (KeyError, TypeError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(
                self, "ملف غير صالح", str(error)
            )
            return
        if not dialog.exec():
            return
        try:
            school_year, plan_number, start_date, weekly_schedule = (
                dialog.values()
            )
            imported_snapshot = dict(snapshot)
            imported_plan = dict(snapshot["plan"])
            imported_plan["school_year"] = school_year
            imported_plan["plan_number"] = plan_number
            imported_plan["start_date"] = start_date
            selected_class = dialog.selected_class_name()
            if selected_class is not None:
                imported_plan["class_name"] = selected_class
            imported_plan["weekdays"] = sorted(weekly_schedule)
            imported_plan["lessons_per_day"] = {
                str(day): count for day, count in weekly_schedule.items()
            }
            imported_snapshot["plan"] = imported_plan
            imported_id = self.db.import_teaching_plan(imported_snapshot)
            self.refresh_plans(selected_plan_id=imported_id)
        except (ValueError, TypeError, sqlite3.Error) as error:
            QMessageBox.warning(
                self, "تعذر استيراد المخطط", str(error)
            )
            return
        actual_number = self.db.teaching_plan_number(imported_id)
        message = "تم استيراد المخطط بنجاح."
        if actual_number != plan_number:
            message += (
                f"\nتم استخدام العدد {actual_number} لأن العدد المطلوب "
                "مستخدم في مخطط آخر."
            )
        QMessageBox.information(self, "اكتمل الاستيراد", message)

    def _clear_content(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _table(headers, rows, column_widths=None):
        table = QTableWidget(len(rows), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setLayoutDirection(Qt.RightToLeft)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setFocusPolicy(Qt.NoFocus)
        table.setWordWrap(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setAlternatingRowColors(True)
        table.setStyleSheet("""
            QTableWidget {
                background: #FFFFFF;
                alternate-background-color: #F8FAFC;
                border: 1px solid #D9E2EF;
                border-radius: 10px;
                gridline-color: #E2E8F0;
                color: #1E293B;
            }
            QHeaderView::section {
                background: #EAF2FC;
                color: #123B67;
                font-weight: bold;
                text-align: center;
                border: none;
                border-bottom: 1px solid #CBD5E1;
                padding: 8px;
            }
        """)
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                cell = QTableWidgetItem(
                    "" if value is None else str(value)
                )
                cell.setTextAlignment(Qt.AlignCenter)
                table.setItem(row_index, column, cell)
        table.resizeRowsToContents()
        for row_index in range(table.rowCount()):
            table.setRowHeight(
                row_index, max(38, table.rowHeight(row_index))
            )
        if column_widths:
            for column, width in enumerate(column_widths):
                table.setColumnWidth(column, width)
        table.setMinimumHeight(max(64, table.horizontalHeader().sizeHint().height()
                                   + sum(table.rowHeight(i)
                                         for i in range(table.rowCount())) + 4))
        return table

    @staticmethod
    def _panel(title, accent="#2563EB"):
        panel = QFrame()
        panel.setObjectName("planViewPanel")
        panel.setStyleSheet(
            "QFrame#planViewPanel { background: #FFFFFF; "
            f"border: 1px solid #D9E2EF; border-top: 3px solid {accent}; "
            "border-radius: 12px; }"
        )
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 10, 12, 12)
        panel_layout.setSpacing(8)
        heading = QLabel(title)
        heading.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #123B67; "
            "border: none; background: transparent;"
        )
        heading.setAlignment(Qt.AlignCenter)
        panel_layout.addWidget(heading)
        return panel, panel_layout

    def _render_snapshot(self, snapshot):
        plan = snapshot["plan"]
        class_label = _teaching_plan_class_label(plan)
        title_text = (
            f"مخطط {_teaching_plan_name(snapshot)} — عدد "
            f"{plan.get('plan_number') if plan.get('plan_number') is not None else 'غير محدد'} "
            f"— القسم: {class_label}"
        )
        banner = QFrame()
        banner.setStyleSheet(
            "QFrame { background: #123B67; border-radius: 14px; }"
        )
        banner_layout = QVBoxLayout(banner)
        banner_layout.setContentsMargins(18, 16, 18, 16)
        title = QLabel(title_text)
        title.setWordWrap(True)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: white; "
            "border: none; background: transparent;"
        )
        banner_layout.addWidget(title)
        self.content_layout.addWidget(banner)

        metadata = [
            ("المجال", plan.get("domain_name")),
            ("كفاية المجال", plan.get("domain_competence")),
            ("تاريخ الانطلاق", plan.get("start_date")),
            ("عدد الحصص", plan.get("lesson_count")),
        ]
        properties = {
            str(prop.get("key") or "").strip():
            str(prop.get("value") or "").strip()
            for prop in snapshot.get("properties", [])
        }
        metadata.extend(
            (key, value)
            for key, value in properties.items()
            if key and value and key not in {"تقديم الوحدة", "وضعية الاستكشاف"}
        )
        metadata_rows = [
            (label, value) for label, value in metadata
            if value not in (None, "")
        ]

        items = snapshot.get("items", [])
        component_texts = []
        for item in items:
            for detail in item.get("details", []):
                component = _pdf_detail_text(
                    detail, "component_text", "component_name"
                )
                if component and component not in component_texts:
                    component_texts.append(component)
        component_symbols = {
            component: f"[م{index}]"
            for index, component in enumerate(component_texts, 1)
        }
        info_grid = QGridLayout()
        info_grid.setHorizontalSpacing(12)
        info_grid.setVerticalSpacing(12)
        info_panel, info_layout = self._panel("بيانات المخطط")
        info_layout.addWidget(self._table(("البيان", "التفاصيل"), metadata_rows))
        info_grid.addWidget(info_panel, 0, 0)

        if component_symbols:
            symbols_panel, symbols_layout = self._panel(
                "مفتاح مكونات الكفاية", "#059669"
            )
            symbols_layout.addWidget(self._table(
                ("الرمز", "مكوّن الكفاية"),
                [(symbol, component)
                 for component, symbol in component_symbols.items()],
            ))
            info_grid.addWidget(symbols_panel, 0, 1)

        items_panel, items_layout = self._panel("الأنشطة وكفاياتها", "#D97706")
        items_layout.addWidget(self._table(
            ("النشاط", "الكفاية"),
            [(
                _plan_item_display_name(
                    item.get("activity_name"),
                    item.get("scope_class_name"),
                ),
                item.get("competence_text"),
            )
             for item in items],
        ))
        info_grid.addWidget(items_panel, 0, 2)
        self.content_layout.addLayout(info_grid)

        unit_introduction = properties.get("تقديم الوحدة", "")
        if unit_introduction:
            self.content_layout.addWidget(
                self._text_panel("تقديم الوحدة", unit_introduction, "#0891B2")
            )

        items_by_id = {
            item.get("source_id"): item for item in items
            if item.get("source_id") is not None
        }
        activities_by_mode = defaultdict(list)
        for activity in snapshot.get("activities", []):
            activities_by_mode[activity.get("learning_mode")].append(activity)
        mode_settings = {
            setting.get("learning_mode"): setting
            for setting in snapshot.get("mode_settings", [])
        }
        configured_activities = {
            "إدماج": {
                activity.get("activity_name"): activity
                for activity in snapshot.get("integration_activities", [])
            },
            "تقييم": {
                activity.get("activity_name"): activity
                for activity in snapshot.get("evaluation_activities", [])
            },
            "دعم وعلاج": {
                activity.get("activity_name"): activity
                for activity in snapshot.get("support_activities", [])
            },
        }
        mode_sections = (
            ("منهجي", "حصص التعلم المنهجي"),
            ("إدماج", "حصص التعلم الإدماجي"),
            ("تقييم", "حصص التعلم التقييمي"),
            ("دعم وعلاج", "حصص الدعم والعلاج"),
        )
        mode_counts = plan.get("mode_lesson_counts", {})
        number_offset = 0
        for mode, heading in mode_sections:
            try:
                lesson_count = max(0, int(mode_counts.get(mode, 0) or 0))
            except (TypeError, ValueError):
                lesson_count = 0
            number_offset += lesson_count
            color, background = PDF_LESSON_NUMBER_COLORS[mode]
            section, section_layout = self._panel(
                f"{heading} — عدد الحصص: {lesson_count}", color
            )
            if lesson_count:
                for mode_lesson_number in range(1, lesson_count + 1):
                    lesson_number = (
                        number_offset - lesson_count + mode_lesson_number
                    )
                    lesson_activities = [
                        activity
                        for activity in activities_by_mode.get(mode, [])
                        if activity.get("lesson_number") == mode_lesson_number
                    ]
                    section_layout.addWidget(self._lesson_card(
                        mode, mode_lesson_number, lesson_number,
                        lesson_activities, items_by_id,
                        component_symbols, configured_activities,
                        mode_settings,
                    ))
            else:
                empty = QLabel("لا توجد حصص محددة لهذا النمط.")
                empty.setAlignment(Qt.AlignCenter)
                empty.setStyleSheet("color: #64748B; padding: 8px;")
                section_layout.addWidget(empty)
            self.content_layout.addWidget(section)

        exploration = properties.get("وضعية الاستكشاف", "")
        if exploration:
            self.content_layout.addWidget(
                self._text_panel("وضعية الاستكشاف", exploration, "#7C3AED")
            )
        self.content_layout.addStretch(1)

    def _text_panel(self, title, text, accent):
        panel, panel_layout = self._panel(title, accent)
        body = QLabel(str(text))
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignCenter)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setStyleSheet(
            "color: #334155; line-height: 1.5; border: none; "
            "background: transparent; padding: 6px;"
        )
        panel_layout.addWidget(body)
        return panel

    def _lesson_card(
        self, mode, mode_lesson_number, lesson_number, activities,
        items_by_id, component_symbols, configured_activities, mode_settings,
    ):
        lesson = QFrame()
        lesson.setStyleSheet(
            "QFrame { background: #F8FAFC; border: 1px solid #E2E8F0; "
            "border-radius: 12px; }"
        )
        lesson_layout = QHBoxLayout(lesson)
        lesson_layout.setContentsMargins(10, 10, 10, 10)
        lesson_layout.setSpacing(10)
        number = QLabel(f"الحصة\n{lesson_number}")
        number.setAlignment(Qt.AlignCenter)
        number.setMinimumSize(64, 54)
        color, background = PDF_LESSON_NUMBER_COLORS[mode]
        number.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {color}; "
            f"background: {background}; border: 1px solid {color}; "
            "border-radius: 10px; padding: 6px;"
        )
        lesson_layout.addWidget(number, 0, Qt.AlignTop)
        cards = QGridLayout()
        cards.setHorizontalSpacing(8)
        cards.setVerticalSpacing(8)
        if activities:
            for index, activity in enumerate(activities):
                card = self._activity_card(
                    mode, activity, items_by_id, component_symbols,
                    configured_activities, mode_settings,
                )
                cards.addWidget(card, index // 2, index % 2)
            lesson_layout.addLayout(cards, 1)
        else:
            empty = QLabel("لم تُوزّع أنشطة على هذه الحصة بعد.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                "color: #64748B; border: none; background: transparent;"
            )
            lesson_layout.addWidget(empty, 1)
        return lesson

    def _activity_card(
        self, mode, activity, items_by_id, component_symbols,
        configured_activities, mode_settings,
    ):
        border, background = _pdf_activity_card_colors(
            activity.get("activity_name")
        )
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {background}; border: 1px solid {border}; "
            "border-radius: 11px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(5)
        item = items_by_id.get(activity.get("item_source_id"))
        activity_display_name = _plan_item_display_name(
            activity.get("activity_name") or "نشاط",
            item.get("scope_class_name") if item else "",
        )
        name = QLabel(
            f"{activity_display_name}"
            f" — {activity.get('duration_minutes') or ''} دقيقة"
        )
        name.setWordWrap(True)
        name.setAlignment(Qt.AlignCenter)
        name.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {border}; "
            "border: none; background: transparent;"
        )
        card_layout.addWidget(name)

        details = []
        if mode == "منهجي":
            item_details = item.get("details", []) if item else []
            components = list(dict.fromkeys(
                component_symbols.get(
                    _pdf_detail_text(
                        detail, "component_text", "component_name"
                    )
                )
                for detail in item_details
                if _pdf_detail_text(
                    detail, "component_text", "component_name"
                )
            ))
            if components:
                details.append(("مكوّن الكفاية", "، ".join(components)))
            specific_objective = str(
                activity.get("specific_objective") or ""
            ).strip()
            objectives = [specific_objective] if specific_objective else []
            if objectives:
                details.append(("الهدف المميز", "، ".join(objectives)))
            activity_content = str(activity.get("content") or "").strip()
            if activity_content:
                details.append(("المحتوى", activity_content))
            for key, label in (
                ("lesson_goal", "هدف الحصة"),
                ("target_competencies", "الكفايات المستهدفة"),
                ("evaluation", "التقييم"),
                ("expected_performance", "الأداء المنتظر"),
                ("target_criteria", "المعايير المستهدفة"),
                ("demographic_concept", "المفهوم السكاني"),
                ("health_concept", "المفهوم الصحي"),
                ("life_skills", "المهارات الحياتية"),
            ):
                value = activity.get(key)
                if value not in (None, ""):
                    details.append((label, value))
            details.extend(
                (str(prop.get("key") or "").strip(),
                 str(prop.get("value") or "").strip())
                for prop in activity.get("properties", [])
                if str(prop.get("key") or "").strip()
                and str(prop.get("value") or "").strip()
            )
        else:
            configured = configured_activities.get(mode, {}).get(
                activity.get("activity_name"), {}
            )
            if mode == "إدماج":
                label = "الكفايات المستهدفة"
                keys = ("target_criteria", "target_competencies", "target_text")
            elif mode == "تقييم":
                label = "الأداء المنتظر"
                keys = ("expected_performance", "target_text")
            else:
                label = "المعايير المستهدفة"
                keys = ("target_criteria",)
            value = next(
                (
                    activity.get(key) or configured.get(key)
                    for key in keys
                    if activity.get(key) or configured.get(key)
                ),
                "",
            )
            if mode == "تقييم" and not value:
                value = mode_settings.get(mode, {}).get(
                    "expected_performance", ""
                )
            if value:
                details.append((label, value))
        for label, value in details:
            field = QLabel(f"{label}: {value}")
            field.setWordWrap(True)
            field.setAlignment(Qt.AlignCenter)
            field.setTextInteractionFlags(Qt.TextSelectableByMouse)
            field.setStyleSheet(
                "color: #334155; border: none; background: transparent;"
            )
            card_layout.addWidget(field)
        return card

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_plans()


def _style_primary_button(button):
    button.setProperty("primary", "true")
    button.style().unpolish(button)
    button.style().polish(button)


def _set_action_type(button, action_type):
    button.setProperty("actionType", action_type)
    button.style().unpolish(button)
    button.style().polish(button)


class PlanPropertiesTable(QTableWidget):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setColumnWidth(0, int(self.viewport().width() * 0.25))


class CheckableComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setModel(QStandardItemModel(self))
        self.view().pressed.connect(self.toggle_item)
        self.lineEdit().returnPressed.connect(self.add_typed_item)
        self.setPlaceholderText("اختر هدفاً أو أضف هدفاً جديداً")

    def add_checkable_item(self, text, checked=False):
        item = QStandardItem(str(text))
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        self.model().appendRow(item)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.refresh_text()

    def toggle_item(self, index):
        item = self.model().itemFromIndex(index)
        if item is None:
            return
        item.setCheckState(
            Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
        )
        self.refresh_text()

    def add_typed_item(self):
        text = self.lineEdit().text().strip()
        if not text:
            self.refresh_text()
            return
        matching_items = self.model().findItems(text, Qt.MatchFixedString)
        item = matching_items[0] if matching_items else None
        if item is None:
            self.add_checkable_item(text, checked=True)
        else:
            item.setCheckState(Qt.Checked)
            self.refresh_text()

    def set_checked_texts(self, texts):
        selected = {str(text).strip() for text in texts if str(text).strip()}
        for index in range(self.count()):
            item = self.model().item(index)
            item.setCheckState(
                Qt.Checked if item.text() in selected else Qt.Unchecked
            )
        self.refresh_text()

    def checked_texts(self):
        return [
            self.model().item(index).text()
            for index in range(self.count())
            if self.model().item(index).checkState() == Qt.Checked
        ]

    def refresh_text(self):
        self.lineEdit().setText("، ".join(self.checked_texts()))


class AutocompletePlainTextEdit(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.completion_model = QStringListModel(self)
        self.completer = QCompleter(self.completion_model, self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.activated.connect(self.insert_completion)

    def set_suggestions(self, suggestions):
        self.completion_model.setStringList(list(dict.fromkeys(suggestions)))

    def insert_completion(self, completion):
        self.setPlainText(completion)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        prefix = self.textCursor().block().text().strip()
        if len(prefix) < 2:
            self.completer.popup().hide()
            return
        self.completer.setCompletionPrefix(prefix)
        self.completer.complete(self.cursorRect())


class TeachingPlanDialog(QDialog):
    PROPERTY_PRESETS = (
        "مدار الاهتمام",
        "مشروع الوحدة",
        "تقديم الوحدة",
        "وضعية الاستكشاف",
    )
    INTEREST_AREAS = (
        "الصحّة والرفاه",
        "المبادرة وبناء المشروع",
        "المحيط والاستهلاك",
        "وسائل الإعلام والاتصال",
        "العيش معاً والمواطنة",
    )

    def __init__(self, db, parent=None, plan=None):
        super().__init__(parent)
        self.db = db
        self.plan = plan
        self._initializing = True
        self.setWindowTitle("بيانات المخطط")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1120, 720)

        layout = QVBoxLayout(self)
        content_scroll = QScrollArea()
        content_scroll.setWidgetResizable(True)
        content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        content_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        body = QWidget()
        body.setLayoutDirection(Qt.LeftToRight)
        columns = QHBoxLayout(body)
        columns.setDirection(QBoxLayout.LeftToRight)
        columns.setContentsMargins(0, 0, 0, 0)
        left_column = QWidget()
        left_column.setLayoutDirection(Qt.RightToLeft)
        left_layout = QVBoxLayout(left_column)
        right_column = QWidget()
        right_column.setLayoutDirection(Qt.RightToLeft)
        right_layout = QVBoxLayout(right_column)
        columns.addWidget(left_column, 1)
        columns.addWidget(right_column, 1)
        content_scroll.setWidget(body)
        layout.addWidget(content_scroll, 1)

        form_group = QGroupBox("بيانات المخطط")
        form_group.setLayoutDirection(Qt.RightToLeft)
        form = QFormLayout(form_group)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.majal_combo = QComboBox()
        for majal_id, name in db.planning_majals():
            self.majal_combo.addItem(name, majal_id)
        form.addRow("المجال:", self.majal_combo)

        self.plan_name_combo = QComboBox()
        self.plan_name_combo.addItem("اختر اسم المخطط", None)
        form.addRow("اسم المخطط:", self.plan_name_combo)

        self.classe_combo = QComboBox()
        self._assigned_class_options = []
        self._populate_assigned_classes()
        self.competence_combo = QComboBox()
        self.majal_combo.currentIndexChanged.connect(
            self.update_plan_name_options
        )
        self.majal_combo.currentIndexChanged.connect(self.load_competences)
        self.update_plan_name_options()
        form.addRow(
            "القسم:",
            add_assignment_editor(
                self.classe_combo,
                db,
                self,
                self._refresh_assigned_classes,
            ),
        )
        self.group_plan_panel = QWidget()
        group_plan_form = QFormLayout(self.group_plan_panel)
        group_plan_form.setContentsMargins(0, 0, 0, 0)
        self.group_plan_scope_combo = QComboBox()
        self.group_plan_scope_combo.addItem("اختر نطاق المخطط", None)
        self.group_plan_scope_combo.addItem(
            "مخطط خاص بقسم واحد", "individual"
        )
        self.group_plan_scope_combo.addItem(
            "مخطط مشترك للفوج", "shared"
        )
        group_plan_form.addRow("نطاق المخطط:", self.group_plan_scope_combo)
        self.group_competence_class_combo = QComboBox()
        self.group_competence_class_combo.addItem("اختر قسم الكفاية", None)
        group_plan_form.addRow(
            "القسم:", self.group_competence_class_combo
        )
        self.shared_competence_panel = QWidget()
        shared_competence_form = QFormLayout(self.shared_competence_panel)
        shared_competence_form.setContentsMargins(0, 0, 0, 0)
        self.shared_competence_combos = []
        self.shared_competence_class_ids = []
        self.shared_competence_resolution_combo = QComboBox()
        self.shared_competence_resolution_combo.addItem(
            "اختر الكفاية الأصح", None
        )
        self.shared_competence_status = QLabel()
        self.shared_competence_status.setWordWrap(True)
        self.shared_competence_status.setStyleSheet("color: #475569;")
        for index in range(2):
            competence_combo = QComboBox()
            self.shared_competence_combos.append(competence_combo)
            shared_competence_form.addRow(
                f"كفاية القسم {index + 1}:", competence_combo
            )
        shared_competence_form.addRow(
            "الكفاية المعتمدة:",
            self.shared_competence_resolution_combo,
        )
        shared_competence_form.addRow("", self.shared_competence_status)
        self.shared_competence_panel.hide()
        group_plan_form.addRow("", self.shared_competence_panel)
        self.shared_competence_owners = {}
        self.group_competence_owner_label = group_plan_form.labelForField(
            self.group_competence_class_combo
        )
        self.group_plan_panel.hide()
        form.addRow("", self.group_plan_panel)
        self.class_group_hint = QLabel(
            "يمكن إعداد مخطط مستقل لكل قسم من الفوج، أو مخطط مشترك "
            "مع تحديد القسم صاحب الكفاية."
        )
        self.class_group_hint.setWordWrap(True)
        self.class_group_hint.setStyleSheet("color: #475569;")
        form.addRow("", self.class_group_hint)
        form.addRow("كفاية المجال:", self.competence_combo)
        self.competence_label = form.labelForField(self.competence_combo)
        self.classe_combo.currentIndexChanged.connect(
            self._handle_class_selection_changed
        )
        self.group_plan_scope_combo.currentIndexChanged.connect(
            self._handle_group_plan_context_changed
        )
        self.group_competence_class_combo.currentIndexChanged.connect(
            self._handle_group_plan_context_changed
        )
        for combo in self.shared_competence_combos:
            combo.currentIndexChanged.connect(
                self._handle_shared_competences_changed
            )
        self.shared_competence_resolution_combo.currentIndexChanged.connect(
            self._handle_group_plan_context_changed
        )

        self.start_date = QDateEdit(QDate.currentDate())
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("yyyy-MM-dd")
        form.addRow("تاريخ الانطلاق:", self.start_date)
        self._plan_number_touched = False
        self._updating_suggested_plan_number = False
        self.plan_number_spin = QSpinBox()
        self.plan_number_spin.setRange(1, 2_147_483_647)
        self.plan_number_spin.setValue(
            plan[8] if plan and plan[8] is not None else 1
        )
        form.addRow("عدد المخطط:", self.plan_number_spin)
        self.lesson_count = QSpinBox()
        self.lesson_count.setRange(0, 4000)
        self.lesson_count.setValue(0)
        self.lesson_count.setReadOnly(True)
        self.lesson_count.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        form.addRow("عدد الحصص:", self.lesson_count)
        counts_group = QGroupBox("عدد الحصص حسب نمط التعلم")
        counts_layout = QFormLayout(counts_group)
        counts_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.mode_lesson_counts = {}
        for mode_name in PlanActivityDialog.MODES:
            count_spin = QSpinBox()
            count_spin.setRange(0, 1000)
            self.mode_lesson_counts[mode_name] = count_spin
            counts_layout.addRow(f"حصص {mode_name}:", count_spin)
        form.addRow(counts_group)
        self.mode_ratio_warning = QLabel()
        self.mode_ratio_warning.setWordWrap(True)
        form.addRow("", self.mode_ratio_warning)
        self.lesson_duration_combo = QComboBox()
        for minutes in (30, 60, 120):
            self.lesson_duration_combo.addItem(
                f"{minutes} دقيقة", minutes
            )
        form.addRow("الزمن المخصص لكل حصة:", self.lesson_duration_combo)
        self.lesson_duration_suggestion = QLabel()
        self.lesson_duration_suggestion.setStyleSheet("color: #475569;")
        form.addRow("", self.lesson_duration_suggestion)
        right_layout.addWidget(form_group)

        properties_group = QGroupBox("خاصيات يحددها المستخدم لهذا المخطط")
        properties_group.setToolTip(
            "أضف اسماً وقيمة لكل خاصية تريد حفظها مع هذا المخطط، "
            "مثل مدار الاهتمام أو تقديم الوحدة. مرّر المؤشر هنا لعرض هذا التوضيح."
        )
        properties_group.setFont(QApplication.font())
        properties_layout = QVBoxLayout(properties_group)
        self.plan_properties = []
        self.editing_property_row = None

        property_form = QGroupBox("إدخال الخاصية")
        property_form.setObjectName("planPropertyForm")
        property_form.setLayoutDirection(Qt.RightToLeft)
        property_form_layout = QFormLayout(property_form)
        property_form_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.property_name_combo = QComboBox()
        self.property_name_combo.setEditable(True)
        self.property_name_combo.addItems(self.PROPERTY_PRESETS)
        self.property_name_combo.setFont(QApplication.font())
        self.property_name_combo.setToolTip(
            "اختر اسماً مقترحاً أو اكتب اسم الخاصية بنفسك."
        )
        property_form_layout.addRow("اسم الخاصية:", self.property_name_combo)
        self.property_value_container = QWidget()
        self.property_value_layout = QVBoxLayout(self.property_value_container)
        self.property_value_layout.setContentsMargins(0, 0, 0, 0)
        property_form_layout.addRow("القيمة:", self.property_value_container)
        self.property_value_editor = None

        form_actions = QHBoxLayout()
        self.save_property_button = QPushButton("إضافة الخاصية")
        _set_action_type(self.save_property_button, "add")
        self.save_property_button.setFont(QApplication.font())
        self.save_property_button.clicked.connect(self.save_property_form)
        self.cancel_property_edit_button = QPushButton("إلغاء التعديل")
        _set_action_type(self.cancel_property_edit_button, "cancel")
        self.cancel_property_edit_button.setFont(QApplication.font())
        self.cancel_property_edit_button.clicked.connect(
            self.clear_property_form
        )
        self.cancel_property_edit_button.hide()
        form_actions.addWidget(self.save_property_button)
        form_actions.addWidget(self.cancel_property_edit_button)
        property_form_layout.addRow(form_actions)
        properties_layout.addWidget(property_form, 1)

        property_table_panel = QWidget()
        property_table_panel.setObjectName("planPropertyTablePanel")
        property_table_layout = QVBoxLayout(property_table_panel)
        property_actions = QHBoxLayout()
        property_actions.addStretch()
        self.edit_property_button = QPushButton("تعديل الخاصية المحددة")
        _set_action_type(self.edit_property_button, "edit")
        self.edit_property_button.setFont(QApplication.font())
        self.edit_property_button.clicked.connect(self.edit_selected_property)
        self.edit_property_button.setEnabled(False)
        self.delete_property_button = QPushButton("حذف الخاصية المحددة")
        _set_action_type(self.delete_property_button, "delete")
        self.delete_property_button.setFont(QApplication.font())
        self.delete_property_button.clicked.connect(
            self.remove_property_row
        )
        self.delete_property_button.setEnabled(False)
        property_actions.addWidget(self.edit_property_button)
        property_actions.addWidget(self.delete_property_button)
        property_table_layout.addLayout(property_actions)

        self.properties_table = PlanPropertiesTable(0, 2)
        self.properties_table.setHorizontalHeaderLabels(["اسم الخاصية", "القيمة"])
        self.properties_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Interactive
        )
        self.properties_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self.properties_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.properties_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.properties_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.properties_table.setWordWrap(True)
        self.properties_table.setMinimumHeight(310)
        self.properties_table.setFont(QApplication.font())
        self.properties_table.setToolTip(
            "هذا الجدول للعرض فقط. استخدم زري التعديل والحذف أعلاه، "
            "ويظهر نموذج الإدخال فوقه."
        )
        self.properties_table.itemSelectionChanged.connect(
            self.update_property_actions
        )
        property_table_layout.addWidget(self.properties_table, 1)
        properties_layout.addWidget(property_table_panel, 1)
        left_layout.addWidget(properties_group, 1)
        self.property_name_combo.currentTextChanged.connect(
            self.change_property_value_editor
        )
        self.change_property_value_editor(
            self.property_name_combo.currentText()
        )

        days_box = QGroupBox("أيام التدريس الأسبوعية وعدد الحصص")
        days_layout = QGridLayout(days_box)
        self.day_checks = {}
        self.day_lesson_counts = {}
        days_layout.addWidget(QLabel("اليوم"), 0, 0)
        days_layout.addWidget(QLabel("عدد الحصص"), 0, 1)
        for row, (name, value) in enumerate(WEEKDAYS, start=1):
            checkbox = QCheckBox(name)
            checkbox.setChecked(value < 6)
            daily_count = QSpinBox()
            daily_count.setRange(1, 20)
            daily_count.setValue(1)
            daily_count.setEnabled(checkbox.isChecked())
            checkbox.toggled.connect(
                lambda checked, spin=daily_count: spin.setEnabled(checked)
            )
            self.day_checks[value] = checkbox
            self.day_lesson_counts[value] = daily_count
            days_layout.addWidget(checkbox, row, 0)
            days_layout.addWidget(daily_count, row, 1)
        left_layout.addWidget(days_box)

        self.date_preview = QLabel()
        self.date_preview.setStyleSheet("color: #475569;")
        left_layout.addWidget(self.date_preview)
        self.autosave_status_label = QLabel("تُحفظ التغييرات آلياً.")
        self.autosave_status_label.setStyleSheet("color: #475569;")
        right_layout.addWidget(self.autosave_status_label)
        right_layout.addStretch()
        self.start_date.dateChanged.connect(self.update_date_preview)
        self.lesson_count.valueChanged.connect(self.update_date_preview)
        for day, checkbox in self.day_checks.items():
            checkbox.toggled.connect(self.update_date_preview)
            checkbox.toggled.connect(self.schedule_autosave)
            self.day_lesson_counts[day].valueChanged.connect(
                self.update_date_preview
            )
            self.day_lesson_counts[day].valueChanged.connect(
                self.schedule_autosave
            )
        self.competence_combo.currentIndexChanged.connect(self.update_date_preview)
        self.classe_combo.currentIndexChanged.connect(
            self.update_duration_suggestion
        )
        self.majal_combo.currentIndexChanged.connect(
            self.update_duration_suggestion
        )
        if not plan:
            self.update_duration_suggestion()

        buttons = QHBoxLayout()
        close_button = QPushButton("إغلاق")
        _set_action_type(close_button, "close")
        close_button.clicked.connect(self.close_dialog)
        buttons.addStretch()
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        if plan:
            self._set_combo_data(self.majal_combo, plan[3])
            self.update_plan_name_options(selected_name=plan[1])
            shared_group_key = plan[14] if len(plan) > 14 else None
            if shared_group_key:
                self._set_combo_data(
                    self.classe_combo, f"group:{shared_group_key}"
                )
                self._update_group_plan_controls()
                self._set_combo_data(self.group_plan_scope_combo, "shared")
            else:
                self._set_combo_data(self.classe_combo, plan[2])
            self.load_competences()
            if shared_group_key:
                owner_index = next(
                    (
                        index for index, class_id in enumerate(
                            self.shared_competence_class_ids
                        )
                        if class_id == plan[2]
                    ),
                    None,
                )
                if owner_index is not None:
                    self._set_combo_data(
                        self.shared_competence_combos[owner_index],
                        plan[4],
                    )
                    other_index = 1 - owner_index
                    saved_text = (
                        self.shared_competence_combos[
                            owner_index
                        ].currentText().strip().casefold()
                    )
                    other_combo = self.shared_competence_combos[other_index]
                    matching_index = next(
                        (
                            index for index in range(1, other_combo.count())
                            if other_combo.itemText(index).strip().casefold()
                            == saved_text
                        ),
                        -1,
                    )
                    if matching_index >= 0:
                        other_combo.setCurrentIndex(matching_index)
                    self._update_shared_competence_match()
                    if (
                        self._selected_shared_competence()
                        != plan[4]
                    ):
                        self._set_combo_data(
                            self.shared_competence_resolution_combo,
                            plan[4],
                        )
            else:
                self._set_combo_data(self.competence_combo, plan[4])
            self.start_date.setDate(QDate.fromString(plan[5], "yyyy-MM-dd"))
            duration_index = self.lesson_duration_combo.findData(plan[9])
            if duration_index >= 0:
                self.lesson_duration_combo.setCurrentIndex(duration_index)
            saved_counts = plan[10:14]
            for mode_name, count in zip(
                PlanActivityDialog.MODES, saved_counts
            ):
                self.mode_lesson_counts[mode_name].setValue(count)
            self.lesson_duration_suggestion.setText(
                "مدة الحصة محفوظة مع هذا المخطط، ويمكن تعديلها."
            )
            for _property_id, key, value in db.teaching_plan_properties(plan[0]):
                self.plan_properties.append((key, value))
            self.refresh_properties_table()
            selected_days = {int(day) for day in plan[7].split(",") if day}
            saved_daily_counts = db.teaching_plan_weekday_counts(plan[0])
            for day, checkbox in self.day_checks.items():
                checkbox.setChecked(day in selected_days)
                if day in saved_daily_counts:
                    self.day_lesson_counts[day].setValue(
                        saved_daily_counts[day]
                    )
                self.day_lesson_counts[day].setEnabled(
                    checkbox.isChecked()
                )
        else:
            self.load_competences()
        for widget in (
            self.plan_name_combo,
            self.classe_combo,
            self.majal_combo,
            self.competence_combo,
            self.start_date,
            self.lesson_count,
            self.lesson_duration_combo,
            self.property_name_combo,
        ):
            widget.setLayoutDirection(Qt.RightToLeft)
        self.start_date.setAlignment(Qt.AlignRight)
        self.lesson_count.setAlignment(Qt.AlignRight)
        for count_spin in self.mode_lesson_counts.values():
            count_spin.setLayoutDirection(Qt.RightToLeft)
            count_spin.setAlignment(Qt.AlignRight)
        for combo in (
            self.classe_combo,
            self.majal_combo,
            self.competence_combo,
            self.lesson_duration_combo,
            self.property_name_combo,
        ):
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.setLayoutDirection(Qt.RightToLeft)
                line_edit.setAlignment(Qt.AlignRight)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(500)
        self.autosave_timer.timeout.connect(self.save)
        self.plan_name_combo.currentIndexChanged.connect(
            self.schedule_autosave
        )
        self.plan_name_combo.currentIndexChanged.connect(
            self.update_suggested_plan_number
        )
        self.classe_combo.currentIndexChanged.connect(
            self.schedule_autosave
        )
        self.classe_combo.currentIndexChanged.connect(
            self.update_suggested_plan_number
        )
        self.majal_combo.currentIndexChanged.connect(
            self.schedule_autosave
        )
        self.competence_combo.currentIndexChanged.connect(
            self.schedule_autosave
        )
        self.start_date.dateChanged.connect(self.schedule_autosave)
        self.start_date.dateChanged.connect(
            self.update_suggested_plan_number
        )
        self.plan_number_spin.valueChanged.connect(
            self.plan_number_changed
        )
        self.lesson_duration_combo.currentIndexChanged.connect(
            self.schedule_autosave
        )
        for count_spin in self.mode_lesson_counts.values():
            count_spin.valueChanged.connect(self.update_lesson_counts)
        for checkbox in self.day_checks.values():
            checkbox.toggled.connect(self.schedule_autosave)
        self.update_lesson_counts()
        self.update_date_preview()
        self.update_suggested_plan_number()
        self._initializing = False

    @staticmethod
    def _set_combo_data(combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def update_plan_name_options(self, *_args, selected_name=None):
        if selected_name is None:
            selected_name = self.plan_name_combo.currentData()
        majal_name = self.majal_combo.currentText().strip()
        self.plan_name_combo.blockSignals(True)
        self.plan_name_combo.clear()
        self.plan_name_combo.addItem("اختر اسم المخطط", None)
        for plan_name, plan_data in PLANS_NAMES.items():
            if plan_data.get("المجال") == majal_name:
                self.plan_name_combo.addItem(plan_name, plan_name)
        self._set_combo_data(self.plan_name_combo, selected_name)
        self.plan_name_combo.blockSignals(False)

    def refresh_properties_table(self):
        self.properties_table.setRowCount(len(self.plan_properties))
        for row, (key, value) in enumerate(self.plan_properties):
            for column, text in enumerate((key, value)):
                item = QTableWidgetItem(text)
                if column == 1:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.properties_table.setItem(row, column, item)
        self.update_property_actions()

    def update_property_actions(self):
        has_selection = self.properties_table.currentRow() >= 0
        self.edit_property_button.setEnabled(has_selection)
        self.delete_property_button.setEnabled(has_selection)

    def save_property_form(self):
        key = self.property_name_combo.currentText().strip()
        if not key:
            QMessageBox.warning(self, "اسم مطلوب", "أدخل اسم الخاصية.")
            return
        if isinstance(self.property_value_editor, QComboBox):
            value = self.property_value_editor.currentText().strip()
        else:
            value = self.property_value_editor.toPlainText().strip()

        if self.editing_property_row is None:
            self.plan_properties.append((key, value))
        else:
            self.plan_properties[self.editing_property_row] = (key, value)
        self.refresh_properties_table()
        self.clear_property_form()
        self.schedule_autosave()

    def edit_selected_property(self):
        row = self.properties_table.currentRow()
        if not 0 <= row < len(self.plan_properties):
            return
        self.editing_property_row = row
        key, value = self.plan_properties[row]
        self.property_name_combo.setCurrentText(key)
        self.set_property_value(value)
        self.save_property_button.setText("حفظ التعديل")
        _set_action_type(self.save_property_button, "edit")
        self.cancel_property_edit_button.show()

    def remove_property_row(self):
        row = self.properties_table.currentRow()
        if not 0 <= row < len(self.plan_properties):
            return
        self.plan_properties.pop(row)
        if self.editing_property_row == row:
            self.clear_property_form()
        elif (
            self.editing_property_row is not None
            and self.editing_property_row > row
        ):
            self.editing_property_row -= 1
        self.refresh_properties_table()
        self.schedule_autosave()

    def clear_property_form(self):
        self.editing_property_row = None
        self.property_name_combo.setCurrentText(self.PROPERTY_PRESETS[0])
        self.set_property_value("")
        self.save_property_button.setText("إضافة الخاصية")
        _set_action_type(self.save_property_button, "add")
        self.cancel_property_edit_button.hide()

    def create_property_value_editor(self, key, value=""):
        if key.strip() == "مدار الاهتمام":
            editor = QComboBox()
            editor.setEditable(True)
            editor.addItems(self.INTEREST_AREAS)
            editor.setCurrentText(value)
            editor.setFont(QApplication.font())
            editor.setLayoutDirection(Qt.RightToLeft)
            editor.lineEdit().setAlignment(Qt.AlignRight)
            editor.setToolTip("اختر مجال اهتمام أو اكتب قيمة أخرى.")
            return editor

        editor = QPlainTextEdit()
        editor.setPlainText(value)
        editor.setFont(QApplication.font())
        editor.setLayoutDirection(Qt.RightToLeft)
        text_option = editor.document().defaultTextOption()
        text_option.setAlignment(Qt.AlignRight)
        editor.document().setDefaultTextOption(text_option)
        editor.setMinimumHeight(editor.fontMetrics().lineSpacing() * 5 + 16)
        editor.setPlaceholderText("اكتب المحتوى هنا...")
        editor.setToolTip("أدخل محتوى الخاصية في مساحة متعددة الأسطر.")
        return editor

    def current_property_value(self):
        if isinstance(self.property_value_editor, QComboBox):
            return self.property_value_editor.currentText()
        if isinstance(self.property_value_editor, QPlainTextEdit):
            return self.property_value_editor.toPlainText()
        return ""

    def set_property_value(self, value):
        if isinstance(self.property_value_editor, QComboBox):
            self.property_value_editor.setCurrentText(value)
        elif isinstance(self.property_value_editor, QPlainTextEdit):
            self.property_value_editor.setPlainText(value)

    def change_property_value_editor(self, key):
        wants_interest_combo = key.strip() == "مدار الاهتمام"
        if (
            self.property_value_editor is not None
            and isinstance(self.property_value_editor, QComboBox)
            == wants_interest_combo
        ):
            return
        previous_value = self.current_property_value()
        if self.property_value_editor is not None:
            self.property_value_layout.removeWidget(self.property_value_editor)
            self.property_value_editor.deleteLater()
        self.property_value_editor = self.create_property_value_editor(
            key, previous_value
        )
        self.property_value_layout.addWidget(self.property_value_editor)

    def _populate_assigned_classes(self, selected_id=None):
        if not hasattr(self, "classe_combo"):
            return
        if selected_id is None:
            selected_id = self.classe_combo.currentData()
        self.classe_combo.blockSignals(True)
        self.classe_combo.clear()
        self.classe_combo.addItem("اختر القسم", None)
        options = self.db.teaching_assignment_classes(
            include_groups=True,
            expand_groups=True,
        )
        self._assigned_class_options = list(options)
        ordered_options = [
            option for option in options
            if not (
                isinstance(option.get("id"), str)
                and option["id"].startswith("group:")
            )
        ] + [
            option for option in options
            if (
                isinstance(option.get("id"), str)
                and option["id"].startswith("group:")
            )
        ]
        for option in ordered_options:
            is_group = (
                isinstance(option.get("id"), str)
                and option["id"].startswith("group:")
            )
            label = (
                f"{option['name']} — قسم مدمج"
                if is_group else
                f"{option['name']} — {option['level_name']}"
            )
            self.classe_combo.addItem(label, option["id"])
        index = self.classe_combo.findData(selected_id)
        self.classe_combo.setCurrentIndex(index if index >= 0 else 0)
        self.classe_combo.blockSignals(False)

    def _refresh_assigned_classes(self):
        self._populate_assigned_classes()
        self._update_group_plan_controls()
        self.load_competences()

    def _selected_plan_class_id(self):
        selected_id = self.classe_combo.currentData()
        if (
            isinstance(selected_id, str)
            and selected_id.startswith("group:")
            and self.group_plan_scope_combo.currentData() == "shared"
        ):
            return self._selected_shared_competence_owner()
        if isinstance(selected_id, str) and selected_id.startswith("group:"):
            return self.group_competence_class_combo.currentData()
        return selected_id

    def _selected_shared_competence(self):
        selected_id = self.classe_combo.currentData()
        if not (
            isinstance(selected_id, str)
            and selected_id.startswith("group:")
            and self.group_plan_scope_combo.currentData() == "shared"
        ):
            return None
        first_id = self.shared_competence_combos[0].currentData()
        second_id = self.shared_competence_combos[1].currentData()
        if first_id is None or second_id is None:
            return None
        first_text = self.shared_competence_combos[0].currentText().strip()
        second_text = self.shared_competence_combos[1].currentText().strip()
        if first_text.casefold() == second_text.casefold():
            return first_id
        return self.shared_competence_resolution_combo.currentData()

    def _selected_shared_competence_owner(self):
        competence_id = self._selected_shared_competence()
        if competence_id is None:
            return None
        return self.shared_competence_owners.get(competence_id)

    def _selected_competence_id(self):
        selected_id = self.classe_combo.currentData()
        if (
            isinstance(selected_id, str)
            and selected_id.startswith("group:")
            and self.group_plan_scope_combo.currentData() == "shared"
        ):
            return self._selected_shared_competence()
        return self.competence_combo.currentData()

    def _selected_shared_group_key(self):
        selected_id = self.classe_combo.currentData()
        if (
            isinstance(selected_id, str)
            and selected_id.startswith("group:")
            and self.group_plan_scope_combo.currentData() == "shared"
        ):
            return selected_id[len("group:"):]
        return None

    def _update_group_plan_controls(self):
        selected_id = self.classe_combo.currentData()
        if not (
            isinstance(selected_id, str)
            and selected_id.startswith("group:")
        ):
            self._active_plan_group_key = None
            self.group_plan_panel.hide()
            return

        group_key = selected_id[len("group:"):]
        previous_group_key = getattr(self, "_active_plan_group_key", None)
        previous_scope = (
            self.group_plan_scope_combo.currentData()
            if previous_group_key == group_key else None
        )
        previous_class_id = (
            self.group_competence_class_combo.currentData()
            if previous_group_key == group_key else None
        )
        self.group_plan_scope_combo.blockSignals(True)
        self.group_plan_scope_combo.setCurrentIndex(0)
        if previous_scope is not None:
            self._set_combo_data(self.group_plan_scope_combo, previous_scope)
        self.group_plan_scope_combo.blockSignals(False)
        self.group_competence_class_combo.blockSignals(True)
        self.group_competence_class_combo.clear()
        self.group_competence_class_combo.addItem(
            "اختر قسم الكفاية", None
        )
        group = LEVEL_FIRAKS.get(group_key)
        if group is not None:
            levels_by_id = {
                level_id: str(level_name).strip()
                for level_id, level_name in self.db.levels()
            }
            registered_classes = {
                (
                    str(class_name).strip().casefold(),
                    levels_by_id.get(level_id, "").casefold(),
                ): (class_id, str(class_name).strip(), levels_by_id.get(
                    level_id, ""
                ))
                for class_id, class_name, level_id in self.db.classes()
            }
            added_class_ids = set()
            for member in group.get("الأقسام", ()):
                class_name = str(member.get("القسم") or "").strip()
                level_name = str(member.get("المستوى") or "").strip()
                registered = registered_classes.get(
                    (class_name.casefold(), level_name.casefold())
                )
                if registered is None or registered[0] in added_class_ids:
                    continue
                class_id, registered_name, registered_level = registered
                self.group_competence_class_combo.addItem(
                    f"{registered_name} — {registered_level}",
                    class_id,
                )
                added_class_ids.add(class_id)
        if previous_class_id is not None:
            self._set_combo_data(
                self.group_competence_class_combo, previous_class_id
            )
        self.group_competence_class_combo.blockSignals(False)
        self._active_plan_group_key = group_key
        self._update_group_scope_visibility()
        self.group_plan_panel.show()

    def _update_group_scope_visibility(self):
        shared = self.group_plan_scope_combo.currentData() == "shared"
        individual = self.group_plan_scope_combo.currentData() == "individual"
        self.group_competence_class_combo.setVisible(individual)
        if self.group_competence_owner_label is not None:
            self.group_competence_owner_label.setVisible(individual)
        self.shared_competence_panel.setVisible(shared)
        if self.competence_label is not None:
            self.competence_label.setVisible(not shared)
        self.competence_combo.setVisible(not shared)

    def _populate_shared_competences(self):
        selected_id = self.classe_combo.currentData()
        if not (
            isinstance(selected_id, str)
            and selected_id.startswith("group:")
        ):
            return
        group_key = selected_id[len("group:"):]
        group = LEVEL_FIRAKS.get(group_key)
        if group is None:
            self.shared_competence_class_ids = []
            return

        levels_by_id = {
            level_id: str(level_name).strip()
            for level_id, level_name in self.db.levels()
        }
        registered_classes = {
            (
                str(class_name).strip().casefold(),
                levels_by_id.get(level_id, "").casefold(),
            ): (class_id, str(class_name).strip(), levels_by_id.get(
                level_id, ""
            ))
            for class_id, class_name, level_id in self.db.classes()
        }
        members = []
        for member in group.get("الأقسام", ()):
            key = (
                str(member.get("القسم") or "").strip().casefold(),
                str(member.get("المستوى") or "").strip().casefold(),
            )
            registered = registered_classes.get(key)
            if registered is not None:
                members.append(registered)
        if len(members) != 2:
            raise ValueError(
                f"يجب تسجيل قسمي الفوج «{group_key}» قبل إعداد مخطط مشترك."
            )

        self._shared_competence_members = members
        self.shared_competence_class_ids = [
            member[0] for member in members
        ]
        self.shared_competence_owners.clear()
        majal_id = self.majal_combo.currentData()
        for index, (class_id, class_name, _level_name) in enumerate(members):
            combo = self.shared_competence_combos[index]
            previous_id = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(f"اختر كفاية {class_name}", None)
            if majal_id is not None:
                for competence_id, text in (
                    self.db.planning_competences_majal(class_id, majal_id)
                ):
                    combo.addItem(text, competence_id)
                    self.shared_competence_owners[competence_id] = class_id
            self._set_combo_data(combo, previous_id)
            combo.blockSignals(False)
            label = self.shared_competence_panel.layout().labelForField(
                combo
            )
            if label is not None:
                label.setText(f"كفاية {class_name}:")

        self.shared_competence_resolution_combo.blockSignals(True)
        self.shared_competence_resolution_combo.clear()
        self.shared_competence_resolution_combo.addItem(
            "اختر الكفاية الأصح", None
        )
        self.shared_competence_resolution_combo.blockSignals(False)
        self._update_shared_competence_match()

    def _update_shared_competence_match(self):
        first_combo, second_combo = self.shared_competence_combos
        first_id = first_combo.currentData()
        second_id = second_combo.currentData()
        resolution = self.shared_competence_resolution_combo
        previous_resolution_id = resolution.currentData()
        resolution.blockSignals(True)
        resolution.clear()
        resolution.addItem("اختر الكفاية الأصح", None)
        if first_id is None or second_id is None:
            self.shared_competence_status.setText(
                "اختر كفاية المجال لكل قسم لمطابقتهما."
            )
            resolution.setVisible(False)
        else:
            first_text = first_combo.currentText().strip()
            second_text = second_combo.currentText().strip()
            if first_text.casefold() == second_text.casefold():
                self.shared_competence_status.setText(
                    "الكفايتان متطابقتان؛ ستُعتمد كفاية واحدة للقسمين."
                )
                resolution.setVisible(False)
            else:
                self.shared_competence_status.setText(
                    "الكفايتان مختلفتان؛ اختر الكفاية الأصح لاعتمادها."
                )
                choices = {}
                for class_id, combo in zip(
                    self.shared_competence_class_ids,
                    self.shared_competence_combos,
                ):
                    competence_id = combo.currentData()
                    text = combo.currentText().strip()
                    if competence_id is not None:
                        choices.setdefault(
                            text.casefold(),
                            (class_id, competence_id, text),
                        )
                for class_id, competence_id, text in choices.values():
                    class_name = next(
                        name for identifier, name, _level
                        in self._shared_competence_members
                        if identifier == class_id
                    )
                    resolution.addItem(
                        f"{class_name} — {text}", competence_id
                    )
                self._set_combo_data(
                    resolution, previous_resolution_id
                )
                resolution.setVisible(True)
        resolution.blockSignals(False)

    def _handle_class_selection_changed(self, *_args):
        self._update_group_plan_controls()
        self.load_competences()
        self.update_duration_suggestion()

    def _handle_group_plan_context_changed(self, *_args):
        self._update_group_scope_visibility()
        self.load_competences()
        self.update_duration_suggestion()
        if hasattr(self, "autosave_timer"):
            self.schedule_autosave()

    def _handle_shared_competences_changed(self, *_args):
        self._update_shared_competence_match()
        self.update_date_preview()
        self.update_duration_suggestion()
        if hasattr(self, "autosave_timer"):
            self.schedule_autosave()

    def load_competences(self, *_args):
        current_id = self.competence_combo.currentData()
        self.competence_combo.clear()
        selected_id = self.classe_combo.currentData()
        if (
            isinstance(selected_id, str)
            and selected_id.startswith("group:")
            and self.group_plan_scope_combo.currentData() == "shared"
        ):
            try:
                self._populate_shared_competences()
            except ValueError as exc:
                self.shared_competence_status.setText(str(exc))
            self.update_date_preview()
            return
        classe_id = self._selected_plan_class_id()
        majal_id = self.majal_combo.currentData()
        if classe_id is not None and majal_id is not None:
            for competence_id, text in self.db.planning_competences_majal(
                classe_id, majal_id
            ):
                self.competence_combo.addItem(text, competence_id)
        self._set_combo_data(self.competence_combo, current_id)
        self.update_date_preview()

    def update_date_preview(self, *_args):
        if self._selected_competence_id() is None:
            selected_id = self.classe_combo.currentData()
            is_shared_group = (
                isinstance(selected_id, str)
                and selected_id.startswith("group:")
                and self.group_plan_scope_combo.currentData() == "shared"
            )
            if is_shared_group:
                self.date_preview.setText(
                    "اختر كفاية المجال لكل قسم، وطابق بينهما أو حدد "
                    "الكفاية الأصح عند الاختلاف."
                )
            else:
                self.date_preview.setText(
                    "لا توجد كفاية مجال مسجلة لهذا القسم والمجال."
                )
            return
        if self.lesson_count.value() < 1:
            self.date_preview.setText("حدد حصصاً واحدة على الأقل ضمن أنماط التعلم.")
            return
        weekly_schedule = {
            day: spin.value()
            for day, spin in self.day_lesson_counts.items()
            if self.day_checks[day].isChecked()
        }
        if not weekly_schedule:
            self.date_preview.setText(
                "اختر أيام التدريس وحدد عدد الحصص في كل يوم."
            )
            return
        start = self.start_date.date().toString("yyyy-MM-dd")
        try:
            end = self.db.calculate_teaching_plan_end_date(
                start, self.lesson_count.value(), weekly_schedule
            )
        except (ValueError, OverflowError) as exc:
            self.date_preview.setText(f"تعذر حساب تاريخ النهاية: {exc}")
            return
        self.date_preview.setText(f"تاريخ النهاية المتوقع بعد استبعاد العطل: {end}")

    def update_lesson_counts(self, *_args):
        counts = {
            mode: spin.value()
            for mode, spin in self.mode_lesson_counts.items()
        }
        total = sum(counts.values())
        self.lesson_count.setValue(total)
        methodical_ratio = (
            counts["منهجي"] * 100 / total if total else 0
        )
        if not total:
            self.mode_ratio_warning.setText(
                "حدد عدد الحصص لكل نمط؛ مجموعها يحدد عدد حصص المخطط."
            )
            self.mode_ratio_warning.setStyleSheet("color: #475569;")
        elif not 65 <= methodical_ratio <= 85:
            self.mode_ratio_warning.setText(
                f"تحذير: نسبة حصص المنهجي {methodical_ratio:.1f}%، "
                "ويُستحسن أن تكون بين 65% و85%."
            )
            self.mode_ratio_warning.setStyleSheet(
                "color: #B45309; font-weight: bold;"
            )
        else:
            self.mode_ratio_warning.setText(
                f"نسبة حصص المنهجي {methodical_ratio:.1f}% — ضمن النطاق المقترح."
            )
            self.mode_ratio_warning.setStyleSheet("color: #15803D;")
        self.update_date_preview()
        if hasattr(self, "autosave_timer"):
            self.schedule_autosave()

    def schedule_autosave(self, *_args):
        if self._initializing:
            return
        self.autosave_status_label.setText("تُحفظ التغييرات آلياً...")
        self.autosave_timer.start()

    def plan_number_changed(self, *_args):
        if self._initializing or self._updating_suggested_plan_number:
            return
        self._plan_number_touched = True
        self.schedule_autosave()

    def update_suggested_plan_number(self, *_args):
        if self._initializing or self._plan_number_touched:
            return
        title = self.plan_name_combo.currentData()
        classe_id = self._selected_plan_class_id()
        if title is None or classe_id is None:
            return
        start_date = self.start_date.date().toString("yyyy-MM-dd")
        plan_id = self.plan[0] if self.plan else None
        number = None
        if self.plan:
            previous_start = date.fromisoformat(str(self.plan[5]))
            current_start = date.fromisoformat(start_date)
            previous_school_year = (
                previous_start.year
                if previous_start.month >= 7
                else previous_start.year - 1
            )
            current_school_year = (
                current_start.year
                if current_start.month >= 7
                else current_start.year - 1
            )
            if (
                title == self.plan[1]
                and classe_id == self.plan[2]
                and current_school_year == previous_school_year
                and self.plan[8] is not None
            ):
                number = self.plan[8]
        if number is None:
            try:
                number = self.db.next_teaching_plan_number(
                    title, classe_id, plan_id, start_date=start_date
                )
            except (ValueError, sqlite3.Error) as exc:
                QMessageBox.warning(
                    self, "تعذر تحديد عدد المخطط تلقائياً", str(exc)
                )
                return
        self._updating_suggested_plan_number = True
        try:
            self.plan_number_spin.setValue(number)
        finally:
            self._updating_suggested_plan_number = False

    def update_duration_suggestion(self, *_args):
        classe_id = self._selected_plan_class_id()
        majal_id = self.majal_combo.currentData()
        if classe_id is None or majal_id is None:
            self.lesson_duration_suggestion.setText(
                "لا توجد ملاحظات سابقة؛ اختر المدة المناسبة."
            )
            return
        duration, source = self.db.suggest_teaching_plan_duration(
            classe_id, majal_id
        )
        if duration is not None:
            index = self.lesson_duration_combo.findData(duration)
            if index >= 0:
                self.lesson_duration_combo.setCurrentIndex(index)
        else:
            self.lesson_duration_combo.setCurrentIndex(
                self.lesson_duration_combo.findData(60)
            )
        self.lesson_duration_suggestion.setText(source)

    def save(self):
        title = self.plan_name_combo.currentData()
        selected_class_id = self._selected_plan_class_id()
        group_selected = (
            isinstance(self.classe_combo.currentData(), str)
            and self.classe_combo.currentData().startswith("group:")
        )
        weekly_schedule = {
            day: spin.value()
            for day, spin in self.day_lesson_counts.items()
            if self.day_checks[day].isChecked()
        }
        lesson_count = sum(
            spin.value() for spin in self.mode_lesson_counts.values()
        )
        if group_selected and (
            self.group_plan_scope_combo.currentData() is None
        ):
            self.autosave_status_label.setText(
                "اختر نطاق المخطط لهذا الفوج."
            )
            self.autosave_status_label.setStyleSheet("color: #B45309;")
            return False
        if group_selected and self.group_plan_scope_combo.currentData() == "shared":
            first_competence = self.shared_competence_combos[0].currentData()
            second_competence = self.shared_competence_combos[1].currentData()
            if first_competence is None or second_competence is None:
                self.autosave_status_label.setText(
                    "اختر كفاية المجال لكل قسم في الفوج المشترك."
                )
                self.autosave_status_label.setStyleSheet("color: #B45309;")
                return False
            if (
                self.shared_competence_combos[0].currentText().strip().casefold()
                != self.shared_competence_combos[1].currentText().strip().casefold()
                and self.shared_competence_resolution_combo.currentData()
                is None
            ):
                self.autosave_status_label.setText(
                    "الكفايتان مختلفتان؛ اختر الكفاية الأصح لاعتمادها."
                )
                self.autosave_status_label.setStyleSheet("color: #B45309;")
                return False
        if (
            title not in PLANS_NAMES
            or selected_class_id is None
            or self._selected_competence_id() is None
            or not weekly_schedule or lesson_count < 1
        ):
            self.autosave_status_label.setText(
                "اختر اسم المخطط من القائمة وأكمل الكفاية وأيام التدريس "
                "وعدد الحصص لكل يوم، وحدد حصة واحدة على الأقل لكل نمط مطلوب."
            )
            self.autosave_status_label.setStyleSheet("color: #B45309;")
            return False
        mode_counts = {
            mode: spin.value()
            for mode, spin in self.mode_lesson_counts.items()
        }
        try:
            saved_plan_id = self.db.save_teaching_plan(
                title,
                selected_class_id,
                self.majal_combo.currentData(),
                self._selected_competence_id(),
                self.start_date.date().toString("yyyy-MM-dd"),
                lesson_count,
                weekly_schedule,
                self.plan[0] if self.plan else None,
                self.plan_properties,
                self.lesson_duration_combo.currentData(),
                mode_counts,
                self._selected_shared_group_key(),
            )
        except (ValueError, TypeError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "تعذر حفظ المخطط", str(exc))
            self.autosave_status_label.setText("تعذر الحفظ الآلي.")
            self.autosave_status_label.setStyleSheet("color: #B91C1C;")
            return False
        try:
            self.db.save_teaching_plan_number(
                saved_plan_id, self.plan_number_spin.value()
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "تعذر حفظ عدد المخطط", str(exc))
            self.autosave_status_label.setText("تعذر حفظ عدد المخطط.")
            self.autosave_status_label.setStyleSheet("color: #B91C1C;")
            return False
        self.plan = self.db.teaching_plan(saved_plan_id)
        self._plan_number_touched = False
        self.autosave_status_label.setText("تم الحفظ آلياً.")
        self.autosave_status_label.setStyleSheet("color: #15803D;")
        return True

    def close_dialog(self):
        self.autosave_timer.stop()
        if self.plan_name_combo.currentData() is not None or self.plan is not None:
            if not self.save():
                if self.plan is not None:
                    return
                answer = QMessageBox.question(
                    self,
                    "مخطط غير مكتمل",
                    "لم يُحفظ المخطط لأن بياناته الأساسية غير مكتملة. هل تريد الإغلاق؟",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
        self.accept()

    def closeEvent(self, event):
        self.autosave_timer.stop()
        if self.plan_name_combo.currentData() is not None or self.plan is not None:
            if not self.save():
                if self.plan is not None:
                    event.ignore()
                    return
                answer = QMessageBox.question(
                    self,
                    "مخطط غير مكتمل",
                    "لم يُحفظ المخطط لأن بياناته الأساسية غير مكتملة. هل تريد الإغلاق؟",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    event.ignore()
                    return
        event.accept()


class PlanItemsDialog(QDialog):
    def __init__(self, db, plan, parent=None):
        super().__init__(parent)
        self.db = db
        self.plan = plan
        self.details = []
        self.item_id = None
        self.editing_detail_index = None
        self.loading_form = False
        self.detail_autosave_timer = QTimer(self)
        self.detail_autosave_timer.setSingleShot(True)
        self.detail_autosave_timer.setInterval(450)
        self.detail_autosave_timer.timeout.connect(self.autosave_detail)
        self.setWindowTitle("تحديد أنشطة المخطط وكفاياتها")
        self.setLayoutDirection(Qt.RightToLeft)
        available_geometry = self.screen().availableGeometry()
        self.setFixedSize(
            min(1250, available_geometry.width()),
            min(760, available_geometry.height()),
        )

        layout = QVBoxLayout(self)
        self.scope_classes = db.teaching_plan_item_scope_classes(plan[0])
        scope_layout = QHBoxLayout()
        scope_label = QLabel("نطاق الأنشطة:")
        scope_layout.addWidget(scope_label)
        self.activity_scope_combo = QComboBox()
        self.activity_scope_combo.addItem("مشترك بين القسمين", None)
        self.activity_scope_combo.addItem("خاص بقسم", "class")
        scope_layout.addWidget(self.activity_scope_combo)
        self.activity_scope_class_label = QLabel("القسم:")
        scope_layout.addWidget(self.activity_scope_class_label)
        self.activity_scope_class_combo = QComboBox()
        for class_id, class_name in self.scope_classes:
            self.activity_scope_class_combo.addItem(class_name, class_id)
        owner_class_id = plan[2] if len(plan) > 2 else None
        owner_index = self.activity_scope_class_combo.findData(owner_class_id)
        if owner_index >= 0:
            self.activity_scope_class_combo.setCurrentIndex(owner_index)
        scope_layout.addWidget(self.activity_scope_class_combo)
        scope_layout.addStretch()
        layout.addLayout(scope_layout)
        self.activity_scope_combo.setVisible(bool(self.scope_classes))
        self.activity_scope_class_combo.setVisible(False)
        scope_label.setVisible(bool(self.scope_classes))
        self.activity_scope_class_label.setVisible(False)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(PlanActivityDialog.MODES)
        self.mode_combo.hide()

        self.mode_tabs = QTabWidget()
        self.mode_tabs.setObjectName("planLearningModeTabs")
        self.mode_tab_layouts = {}
        self.mode_tab_pages = {}
        for mode_name in PlanActivityDialog.MODES:
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_area.setFrameShape(QFrame.Shape.NoFrame)
            content = QWidget()
            content_layout = (
                QHBoxLayout(content)
                if mode_name == "منهجي"
                else QVBoxLayout(content)
            )
            scroll_area.setWidget(content)
            self.mode_tabs.addTab(scroll_area, mode_name)
            self.mode_tab_layouts[mode_name] = content_layout
            self.mode_tab_pages[mode_name] = scroll_area

        layout.addWidget(self.mode_tabs, 1)
        self.mode_tabs.currentChanged.connect(
            self.select_learning_mode_tab
        )
        self.pending_mode_selections = {}
        self.pending_mode_performance = {}
        self.pending_mode_targets = {}
        self.mode_objective_drafts = {}
        self.mode_activity_id = None
        self.mode_activity_mode = self.mode_combo.currentText()
        self.loading_mode_activity = False
        form_box = QGroupBox("بيانات النشاط ومكوّناته")
        self.form_box = form_box
        form_layout = QVBoxLayout(form_box)
        methodical_layout = self.mode_tab_layouts["منهجي"]
        form = QFormLayout()

        self.activity_combo = QComboBox()
        self.activity_combo.setEditable(True)
        self.activity_names = db.teaching_plan_activity_names(plan[0])
        self.activity_combo.addItems(self.activity_names)
        form.addRow("المادة / النشاط:", self.activity_combo)

        self.competence_combo = QComboBox()
        self.competence_combo.setEditable(True)
        form.addRow("كفاية المادة:", self.competence_combo)

        self.component_combo = QComboBox()
        self.component_combo.setEditable(True)
        form.addRow("مكوّن الكفاية:", self.component_combo)
        self.objective_combo = QComboBox()
        self.objective_combo.setEditable(True)
        form.addRow("الهدف المميز (اختياري):", self.objective_combo)
        self.content_combo = QComboBox()
        self.content_combo.setEditable(True)
        form.addRow("المحتوى:", self.content_combo)
        for combo in (
            self.activity_combo, self.competence_combo,
            self.component_combo, self.objective_combo, self.content_combo,
        ):
            completer = combo.completer()
            if completer is not None:
                completer.setCaseSensitivity(Qt.CaseInsensitive)
                completer.setFilterMode(Qt.MatchContains)
        form_layout.addLayout(form)

        detail_buttons = QHBoxLayout()
        self.save_detail_button = QPushButton("إضافة المكوّن والمحتوى")
        _set_action_type(self.save_detail_button, "add")
        _style_primary_button(self.save_detail_button)
        self.save_detail_button.clicked.connect(self.save_detail)
        edit_detail = QPushButton("تعديل السطر المحدد")
        _set_action_type(edit_detail, "edit")
        edit_detail.clicked.connect(self.edit_detail)
        remove_detail = QPushButton("حذف السطر المحدد")
        _set_action_type(remove_detail, "delete")
        remove_detail.clicked.connect(self.remove_detail)
        detail_buttons.addWidget(self.save_detail_button)
        detail_buttons.addWidget(edit_detail)
        detail_buttons.addWidget(remove_detail)
        detail_buttons.addStretch()
        form_layout.addLayout(detail_buttons)

        self.details_table = QTableWidget(0, 3)
        self.details_table.setHorizontalHeaderLabels([
            "مكوّن الكفاية", "الهدف المميز", "المحتوى",
        ])
        self.details_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.details_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.details_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.details_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        form_layout.addWidget(self.details_table, 1)

        form_actions = QHBoxLayout()
        clear_form = QPushButton("نشاط جديد")
        _set_action_type(clear_form, "add")
        clear_form.clicked.connect(self.clear_form)
        form_actions.addWidget(clear_form)
        form_actions.addStretch()
        form_layout.addLayout(form_actions)
        methodical_layout.addWidget(form_box, 1)

        saved_box = QGroupBox("الأنشطة المختارة للمخطط")
        saved_layout = QVBoxLayout(saved_box)
        self.items_table = QTableWidget(0, 5)
        self.items_table.setHorizontalHeaderLabels([
            "النشاط", "كفاية المادة", "المكونات", "المحتويات", "النطاق",
        ])
        self.items_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.items_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.items_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.items_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.items_table.itemSelectionChanged.connect(self.load_selected_item)
        saved_layout.addWidget(self.items_table, 1)
        self.mode_objectives_tree = QTreeWidget()
        self.mode_objectives_tree.setHeaderHidden(True)
        self.mode_objectives_tree.setMaximumHeight(360)
        self.mode_objectives_tree.itemChanged.connect(
            self.update_mode_tree_check_state
        )
        saved_layout.addWidget(self.mode_objectives_tree, 1)
        self.mode_activity_combo = QComboBox()
        saved_layout.addWidget(QLabel("النشاط المحدد للنمط:"))
        saved_layout.addWidget(self.mode_activity_combo)
        self.mode_objective_combo = CheckableComboBox()
        self.mode_objective_label = QLabel("الأهداف المميزة:")
        saved_layout.addWidget(self.mode_objective_label)
        saved_layout.addWidget(self.mode_objective_combo)
        self.target_competencies_edit = QPlainTextEdit()
        self.target_competencies_edit.setFixedHeight(
            self.target_competencies_edit.fontMetrics().lineSpacing() * 4 + 20
        )
        self.target_competencies_edit.setLayoutDirection(Qt.RightToLeft)
        self.target_competencies_label = QLabel("الكفايات المستهدفة:")
        saved_layout.addWidget(self.target_competencies_label)
        saved_layout.addWidget(self.target_competencies_edit)
        self.toggle_mode_activity_button = QPushButton("إضافة النشاط المحدد")
        _set_action_type(self.toggle_mode_activity_button, "add")
        self.toggle_mode_activity_button.clicked.connect(
            self.toggle_mode_activity
        )
        saved_layout.addWidget(self.toggle_mode_activity_button)
        self.expected_performance_edit = QPlainTextEdit()
        self.expected_performance_edit.setFixedHeight(
            self.expected_performance_edit.fontMetrics().lineSpacing() * 4 + 20
        )
        self.expected_performance_edit.setLayoutDirection(Qt.RightToLeft)
        self.expected_performance_label = QLabel("الأداء المنتظر:")
        saved_layout.addWidget(self.expected_performance_label)
        saved_layout.addWidget(self.expected_performance_edit)

        self.integration_panel = QGroupBox("أنشطة الإدماج ومحتوياتها")
        integration_layout = QHBoxLayout(self.integration_panel)
        integration_form_layout = QVBoxLayout()
        self.integration_activity_combo = QComboBox()
        self.integration_activity_combo.addItems(self.activity_names)
        integration_form_layout.addWidget(QLabel("النشاط:"))
        integration_form_layout.addWidget(self.integration_activity_combo)
        integration_form_layout.addWidget(
            QLabel("وصف النشاط (مع الإكمال التلقائي):")
        )
        self.integration_prompt_edit = AutocompletePlainTextEdit()
        self.integration_prompt_edit.setFixedHeight(
            self.integration_prompt_edit.fontMetrics().lineSpacing() * 2 + 16
        )
        self.integration_prompt_edit.setLayoutDirection(Qt.RightToLeft)
        integration_form_layout.addWidget(self.integration_prompt_edit)
        integration_form_layout.addWidget(QLabel("صيغة وضعية الإدماج:"))
        self.integration_template_combo = QComboBox()
        self.integration_template_combo.setEditable(True)
        self.integration_template_combo.addItems((
            "حل وضعيات مشكل دالة يتطلب حلها توظيف الكفايات التالية:",
            "قراءة نص سردي والإجابة شفويا وكتابيا عن أسئلة متصلة تتطلب الكفايات والمهارات التالية:",
            "إنتاج نص سردي يتطلب توظيف الكفايات والمهارات التالية:",
        ))
        integration_form_layout.addWidget(self.integration_template_combo)
        integration_form_layout.addWidget(QLabel("المحتويات المتاحة:"))
        self.integration_contents_table = QTableWidget(0, 1)
        self.integration_contents_table.setHorizontalHeaderLabels(
            ["المحتوى — اختر واحداً أو أكثر"]
        )
        self.integration_contents_table.setSelectionMode(
            QAbstractItemView.NoSelection
        )
        self.integration_contents_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.integration_contents_table.setMaximumHeight(150)
        integration_form_layout.addWidget(self.integration_contents_table)
        self.integration_target_edit = QPlainTextEdit()
        self.integration_target_edit.setFixedHeight(
            self.integration_target_edit.fontMetrics().lineSpacing() * 6 + 20
        )
        self.integration_target_edit.setLayoutDirection(Qt.RightToLeft)
        integration_buttons = QHBoxLayout()
        self.add_integration_activity_button = QPushButton("إضافة النشاط")
        _set_action_type(self.add_integration_activity_button, "add")
        _style_primary_button(self.add_integration_activity_button)
        self.add_integration_activity_button.clicked.connect(
            self.add_integration_activity
        )
        self.new_integration_activity_button = QPushButton("إضافة نشاط جديد")
        _set_action_type(self.new_integration_activity_button, "add")
        self.new_integration_activity_button.clicked.connect(
            self.new_integration_activity
        )
        integration_buttons.addWidget(self.add_integration_activity_button)
        integration_buttons.addWidget(self.new_integration_activity_button)
        integration_panel_layout = QVBoxLayout()
        integration_panel_layout.addWidget(
            QLabel("نص النشاط والكفايات المستهدفة:")
        )
        integration_panel_layout.addWidget(self.integration_target_edit)
        integration_panel_layout.addLayout(integration_buttons)
        integration_panel_layout.addWidget(
            QLabel("الأنشطة المضافة للإدماج:")
        )
        self.integration_table = QTableWidget(0, 3)
        self.integration_table.setHorizontalHeaderLabels([
            "النشاط", "صيغة الوضعية", "المحتويات المختارة",
        ])
        self.integration_table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )
        self.integration_table.setSelectionMode(
            QAbstractItemView.SingleSelection
        )
        self.integration_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers
        )
        self.integration_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.integration_table.itemSelectionChanged.connect(
            self.update_integration_table_actions
        )
        integration_panel_layout.addWidget(self.integration_table, 1)
        integration_table_buttons = QHBoxLayout()
        self.edit_integration_activity_button = QPushButton(
            "تعديل النشاط المحدد"
        )
        _set_action_type(self.edit_integration_activity_button, "edit")
        self.edit_integration_activity_button.clicked.connect(
            self.edit_integration_activity
        )
        self.delete_integration_activity_button = QPushButton(
            "حذف النشاط المحدد"
        )
        _set_action_type(self.delete_integration_activity_button, "delete")
        self.delete_integration_activity_button.clicked.connect(
            self.delete_integration_activity
        )
        integration_table_buttons.addWidget(
            self.edit_integration_activity_button
        )
        integration_table_buttons.addWidget(
            self.delete_integration_activity_button
        )
        integration_panel_layout.addLayout(integration_table_buttons)
        integration_layout.addLayout(integration_form_layout, 1)
        integration_layout.addLayout(integration_panel_layout, 1)
        self.mode_tab_layouts["إدماج"].addWidget(
            self.integration_panel, 1
        )
        self.integration_data_loading = False
        self.editing_integration_activity = None
        self.integration_selected_contents = set()
        self.last_integration_template = ""
        self.integration_autosave_timer = QTimer(self)
        self.integration_autosave_timer.setSingleShot(True)
        self.integration_autosave_timer.setInterval(350)
        self.integration_autosave_timer.timeout.connect(
            self.autosave_integration_draft
        )
        self.integration_activity_combo.currentIndexChanged.connect(
            self.load_integration_contents
        )
        self.integration_prompt_edit.textChanged.connect(
            self.on_integration_prompt_changed
        )
        self.integration_template_combo.currentTextChanged.connect(
            self.update_integration_template
        )
        self.integration_contents_table.itemChanged.connect(
            self.update_integration_content_selection
        )
        self.integration_target_edit.textChanged.connect(
            self.schedule_integration_autosave
        )
        self.integration_activity_combo.setCurrentIndex(
            0 if self.integration_activity_combo.count() else -1
        )

        self.evaluation_panel = QGroupBox("أنشطة التقييم ومحتوياتها")
        evaluation_layout = QHBoxLayout(self.evaluation_panel)
        evaluation_form_layout = QVBoxLayout()
        self.evaluation_activity_combo = QComboBox()
        self.evaluation_activity_combo.addItems(self.activity_names)
        evaluation_form_layout.addWidget(QLabel("النشاط:"))
        evaluation_form_layout.addWidget(self.evaluation_activity_combo)
        evaluation_form_layout.addWidget(
            QLabel("وصف النشاط (مع الإكمال التلقائي):")
        )
        self.evaluation_prompt_edit = AutocompletePlainTextEdit()
        self.evaluation_prompt_edit.setFixedHeight(
            self.evaluation_prompt_edit.fontMetrics().lineSpacing() * 2 + 16
        )
        self.evaluation_prompt_edit.setLayoutDirection(Qt.RightToLeft)
        evaluation_form_layout.addWidget(self.evaluation_prompt_edit)
        evaluation_form_layout.addWidget(QLabel("النصوص المقترحة:"))
        self.evaluation_template_combo = QComboBox()
        self.evaluation_template_combo.setEditable(True)
        self.evaluation_template_combo.addItems((
            "أن يكون المتعلم قادرا على قراءة نص سردي قراءة صامتة وسريعة "
            "ويجيب شفويا وكتابيا أن أسئلة متصلة بالمهارات والكفايات التالية:",
            "أن يكون المتعلم قادرا على إنتاج نص سردي يوظف فيه المهارات "
            "والكفايات التالية:",
            "أن يكون المتعلم قادرا على حل وضعيات مشكل دالة تتطلب توظيف "
            "المهارات والكفايات التالية:",
        ))
        evaluation_form_layout.addWidget(self.evaluation_template_combo)
        evaluation_form_layout.addWidget(QLabel("المحتويات المتاحة:"))
        self.evaluation_contents_table = QTableWidget(0, 1)
        self.evaluation_contents_table.setHorizontalHeaderLabels(
            ["المحتوى — اختر واحداً أو أكثر"]
        )
        self.evaluation_contents_table.setSelectionMode(
            QAbstractItemView.NoSelection
        )
        self.evaluation_contents_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.evaluation_contents_table.setMaximumHeight(150)
        evaluation_form_layout.addWidget(self.evaluation_contents_table)
        self.evaluation_target_edit = QPlainTextEdit()
        self.evaluation_target_edit.setFixedHeight(
            self.evaluation_target_edit.fontMetrics().lineSpacing() * 6 + 20
        )
        self.evaluation_target_edit.setLayoutDirection(Qt.RightToLeft)
        evaluation_buttons = QHBoxLayout()
        self.add_evaluation_activity_button = QPushButton("إضافة النشاط")
        _set_action_type(self.add_evaluation_activity_button, "add")
        _style_primary_button(self.add_evaluation_activity_button)
        self.add_evaluation_activity_button.clicked.connect(
            self.add_evaluation_activity
        )
        self.new_evaluation_activity_button = QPushButton("إضافة نشاط جديد")
        _set_action_type(self.new_evaluation_activity_button, "add")
        self.new_evaluation_activity_button.clicked.connect(
            self.new_evaluation_activity
        )
        evaluation_buttons.addWidget(self.add_evaluation_activity_button)
        evaluation_buttons.addWidget(self.new_evaluation_activity_button)
        evaluation_panel_layout = QVBoxLayout()
        evaluation_panel_layout.addWidget(
            QLabel("نص النشاط والأداء المنتظر:")
        )
        evaluation_panel_layout.addWidget(self.evaluation_target_edit)
        evaluation_panel_layout.addLayout(evaluation_buttons)
        evaluation_panel_layout.addWidget(
            QLabel("الأنشطة المضافة للتقييم:")
        )
        self.evaluation_table = QTableWidget(0, 3)
        self.evaluation_table.setHorizontalHeaderLabels([
            "النشاط", "النص المقترح", "المحتويات المختارة",
        ])
        self.evaluation_table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )
        self.evaluation_table.setSelectionMode(
            QAbstractItemView.SingleSelection
        )
        self.evaluation_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers
        )
        self.evaluation_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.evaluation_table.itemSelectionChanged.connect(
            self.update_evaluation_table_actions
        )
        evaluation_panel_layout.addWidget(self.evaluation_table, 1)
        evaluation_table_buttons = QHBoxLayout()
        self.edit_evaluation_activity_button = QPushButton(
            "تعديل النشاط المحدد"
        )
        _set_action_type(self.edit_evaluation_activity_button, "edit")
        self.edit_evaluation_activity_button.clicked.connect(
            self.edit_evaluation_activity
        )
        self.delete_evaluation_activity_button = QPushButton(
            "حذف النشاط المحدد"
        )
        _set_action_type(self.delete_evaluation_activity_button, "delete")
        self.delete_evaluation_activity_button.clicked.connect(
            self.delete_evaluation_activity
        )
        evaluation_table_buttons.addWidget(
            self.edit_evaluation_activity_button
        )
        evaluation_table_buttons.addWidget(
            self.delete_evaluation_activity_button
        )
        evaluation_panel_layout.addLayout(evaluation_table_buttons)
        evaluation_layout.addLayout(evaluation_form_layout, 1)
        evaluation_layout.addLayout(evaluation_panel_layout, 1)
        self.mode_tab_layouts["تقييم"].addWidget(
            self.evaluation_panel, 1
        )
        self.evaluation_data_loading = False
        self.editing_evaluation_activity = None
        self.evaluation_selected_contents = set()
        self.last_evaluation_template = ""
        self.evaluation_autosave_timer = QTimer(self)
        self.evaluation_autosave_timer.setSingleShot(True)
        self.evaluation_autosave_timer.setInterval(350)
        self.evaluation_autosave_timer.timeout.connect(
            self.autosave_evaluation_draft
        )
        self.evaluation_activity_combo.currentIndexChanged.connect(
            self.load_evaluation_contents
        )
        self.evaluation_prompt_edit.textChanged.connect(
            self.on_evaluation_prompt_changed
        )
        self.evaluation_template_combo.currentTextChanged.connect(
            self.update_evaluation_template
        )
        self.evaluation_contents_table.itemChanged.connect(
            self.update_evaluation_content_selection
        )
        self.evaluation_target_edit.textChanged.connect(
            self.schedule_evaluation_autosave
        )
        self.evaluation_activity_combo.setCurrentIndex(
            0 if self.evaluation_activity_combo.count() else -1
        )

        self.support_panel = QGroupBox("أنشطة الدعم والعلاج")
        support_panel_layout = QVBoxLayout(self.support_panel)
        self.support_activity_combo = QComboBox()
        self.support_activity_combo.addItems(self.activity_names)
        support_panel_layout.addWidget(QLabel("النشاط:"))
        support_panel_layout.addWidget(self.support_activity_combo)
        support_panel_layout.addWidget(QLabel("المعايير المستهدفة:"))
        self.support_target_criteria_text = (
            "أن يكون المتعلم قادرا على تجاوز الصعوبات التي ستحدد على "
            "ضوء نتائج تقييم نهاية الوحدة / الفترة"
        )
        self.support_target_criteria_edit = QPlainTextEdit()
        self.support_target_criteria_edit.setFixedHeight(
            self.support_target_criteria_edit.fontMetrics().lineSpacing() * 2
            + 20
        )
        self.support_target_criteria_edit.setLayoutDirection(Qt.RightToLeft)
        self.support_target_criteria_edit.setReadOnly(True)
        self.support_target_criteria_edit.setPlainText(
            self.support_target_criteria_text
        )
        support_panel_layout.addWidget(self.support_target_criteria_edit)
        support_buttons = QHBoxLayout()
        self.add_support_activity_button = QPushButton("إضافة النشاط")
        _set_action_type(self.add_support_activity_button, "add")
        _style_primary_button(self.add_support_activity_button)
        self.add_support_activity_button.clicked.connect(
            self.add_support_activity
        )
        self.new_support_activity_button = QPushButton("إضافة نشاط جديد")
        _set_action_type(self.new_support_activity_button, "add")
        self.new_support_activity_button.clicked.connect(
            self.new_support_activity
        )
        support_buttons.addWidget(self.add_support_activity_button)
        support_buttons.addWidget(self.new_support_activity_button)
        support_panel_layout.addLayout(support_buttons)
        support_panel_layout.addWidget(
            QLabel("الأنشطة المضافة للدعم والعلاج:")
        )
        self.support_table = QTableWidget(0, 2)
        self.support_table.setHorizontalHeaderLabels([
            "النشاط", "المعايير المستهدفة",
        ])
        self.support_table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )
        self.support_table.setSelectionMode(
            QAbstractItemView.SingleSelection
        )
        self.support_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers
        )
        self.support_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.support_table.itemSelectionChanged.connect(
            self.update_support_table_actions
        )
        support_panel_layout.addWidget(self.support_table, 1)
        support_table_buttons = QHBoxLayout()
        self.edit_support_activity_button = QPushButton(
            "تعديل النشاط المحدد"
        )
        _set_action_type(self.edit_support_activity_button, "edit")
        self.edit_support_activity_button.clicked.connect(
            self.edit_support_activity
        )
        self.delete_support_activity_button = QPushButton(
            "حذف النشاط المحدد"
        )
        _set_action_type(self.delete_support_activity_button, "delete")
        self.delete_support_activity_button.clicked.connect(
            self.delete_support_activity
        )
        support_table_buttons.addWidget(self.edit_support_activity_button)
        support_table_buttons.addWidget(self.delete_support_activity_button)
        support_panel_layout.addLayout(support_table_buttons)
        self.mode_tab_layouts["دعم وعلاج"].addWidget(
            self.support_panel, 1
        )
        self.editing_support_activity = None
        self.support_autosave_timer = QTimer(self)
        self.support_autosave_timer.setSingleShot(True)
        self.support_autosave_timer.setInterval(350)
        self.support_autosave_timer.timeout.connect(
            self.autosave_support_activity
        )
        self.support_activity_combo.currentIndexChanged.connect(
            self.load_support_activity
        )
        self.refresh_support_table()
        self.support_activity_combo.setCurrentIndex(
            0 if self.support_activity_combo.count() else -1
        )

        item_buttons = QHBoxLayout()
        delete_item = QPushButton("حذف النشاط المحدد")
        _set_action_type(delete_item, "delete")
        delete_item.clicked.connect(self.delete_selected_item)
        self.delete_item_button = delete_item
        finish = QPushButton("إنهاء")
        _set_action_type(finish, "close")
        _style_primary_button(finish)
        finish.clicked.connect(self.accept)
        item_buttons.addWidget(delete_item)
        item_buttons.addStretch()
        item_buttons.addWidget(finish)
        saved_layout.addLayout(item_buttons)
        methodical_layout.addWidget(saved_box, 1)

        for combo in (
            self.activity_combo, self.competence_combo,
            self.component_combo, self.objective_combo, self.content_combo,
            self.integration_template_combo, self.evaluation_template_combo,
        ):
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.setLayoutDirection(Qt.RightToLeft)
                line_edit.setAlignment(Qt.AlignRight)
        for editor in (
            self.target_competencies_edit,
            self.expected_performance_edit,
            self.integration_prompt_edit,
            self.integration_target_edit,
            self.evaluation_prompt_edit,
            self.evaluation_target_edit,
            self.support_target_criteria_edit,
        ):
            editor.setLayoutDirection(Qt.RightToLeft)
            text_option = editor.document().defaultTextOption()
            text_option.setAlignment(Qt.AlignRight)
            editor.document().setDefaultTextOption(text_option)

        self.activity_combo.currentTextChanged.connect(
            self.load_competence_suggestions
        )
        self.activity_scope_combo.currentIndexChanged.connect(
            self.update_activity_scope
        )
        self.activity_scope_class_combo.currentIndexChanged.connect(
            self.update_activity_scope
        )
        self.content_combo.activated.connect(self.schedule_detail_autosave)
        content_line_edit = self.content_combo.lineEdit()
        if content_line_edit is not None:
            content_line_edit.editingFinished.connect(
                self.schedule_detail_autosave
            )
        self.competence_combo.currentTextChanged.connect(
            self.load_component_suggestions
        )
        self.component_combo.currentTextChanged.connect(
            self.load_objective_suggestions
        )
        self.objective_combo.currentTextChanged.connect(
            self.load_content_suggestions
        )
        self.activity_combo.setInsertPolicy(QComboBox.NoInsert)
        self.competence_combo.setInsertPolicy(QComboBox.NoInsert)
        self.saved_mode_settings = db.teaching_plan_mode_settings(plan[0])
        self.pending_mode_selections = {
            mode: {
                **{
                    item_id: set(objectives)
                    for item_id, objectives in setting[
                        "selected_objectives"
                    ].items()
                },
                **{
                    item_id: set()
                    for item_id in setting["selected_items"]
                    if item_id not in setting["selected_objectives"]
                },
                **{
                    activity_name: set(objectives)
                    for activity_name, objectives in setting.get(
                        "selected_activities", {}
                    ).items()
                },
            }
            for mode, setting in self.saved_mode_settings["modes"].items()
        }
        self.mode_combo.setCurrentText(
            self.saved_mode_settings["default_mode"]
        )
        self.mode_tabs.setCurrentIndex(
            self.mode_tabs.indexOf(
                self.mode_tab_pages[
                    self.saved_mode_settings["default_mode"]
                ]
            )
        )
        self.last_mode = self.mode_combo.currentText()
        self.mode_combo.currentTextChanged.connect(self.update_mode_setup)
        self.mode_activity_combo.currentIndexChanged.connect(
            self.load_mode_activity_options
        )
        self.mode_settings_autosave_timer = QTimer(self)
        self.mode_settings_autosave_timer.setSingleShot(True)
        self.mode_settings_autosave_timer.setInterval(350)
        self.mode_settings_autosave_timer.timeout.connect(
            self.autosave_mode_settings
        )
        self.mode_objective_combo.model().itemChanged.connect(
            self.on_mode_objective_changed
        )
        self.target_competencies_edit.textChanged.connect(
            self.cache_mode_target_text
        )
        self.expected_performance_edit.textChanged.connect(
            self.schedule_mode_settings_autosave
        )
        self.load_items()
        self.update_mode_setup()
        self.load_competence_suggestions(self.activity_combo.currentText())
        self.loading_plan_number = False

    def select_learning_mode_tab(self, index):
        mode = self.mode_tabs.tabText(index)
        if mode and mode != self.mode_combo.currentText():
            self.mode_combo.setCurrentText(mode)

    def update_mode_setup(self, mode=None):
        previous_mode = self.last_mode
        if previous_mode and previous_mode != mode:
            self.pending_mode_selections[previous_mode] = (
                self.collect_mode_selections(previous_mode)
            )
            self.cache_mode_activity_state(previous_mode)
            self.pending_mode_performance[previous_mode] = (
                self.expected_performance_edit.toPlainText()
            )
            self.mode_settings_autosave_timer.stop()
            if previous_mode == "إدماج":
                self.autosave_integration_draft()
            elif previous_mode == "تقييم":
                self.autosave_evaluation_draft()
            elif previous_mode == "دعم وعلاج":
                self.autosave_support_activity()
            else:
                self.autosave_mode_settings(previous_mode)
        mode = mode or self.mode_combo.currentText()
        self.last_mode = mode
        methodical = mode == "منهجي"
        integration = mode == "إدماج"
        evaluation = mode == "تقييم"
        support = mode == "دعم وعلاج"
        self.form_box.setVisible(methodical)
        self.items_table.setVisible(methodical)
        self.mode_objectives_tree.hide()
        self.mode_activity_combo.setVisible(
            not methodical and not integration and not evaluation and not support
        )
        self.mode_objective_combo.hide()
        self.mode_objective_label.hide()
        self.target_competencies_edit.hide()
        self.target_competencies_label.hide()
        self.toggle_mode_activity_button.setVisible(
            not methodical and not integration and not evaluation and not support
        )
        self.expected_performance_label.hide()
        self.expected_performance_edit.hide()
        self.integration_panel.setVisible(integration)
        self.evaluation_panel.setVisible(evaluation)
        self.support_panel.setVisible(support)
        self.delete_item_button.setVisible(methodical)
        if integration:
            self.load_integration_contents()
        elif evaluation:
            self.load_evaluation_contents()
        elif support:
            self.refresh_support_table()
            self.load_support_activity()
        else:
            self.populate_mode_activity_combo()
            self.load_mode_activity_options()
        self.expected_performance_edit.setPlainText(
            self.pending_mode_performance.get(
                mode,
                self.saved_mode_settings["modes"].get(
                    mode, {}
                ).get("expected_performance", ""),
            )
        )

    def populate_mode_activity_combo(self):
        current_id = self.mode_activity_combo.currentData()
        self.mode_activity_combo.blockSignals(True)
        self.mode_activity_combo.clear()
        plan_items = self.db.teaching_plan_items(self.plan[0])
        activity_names = list(self.activity_names)
        known_names = {name.strip().casefold() for name in activity_names}
        for plan_item, _details in plan_items:
            name = str(plan_item[1] or "").strip()
            if name.casefold() not in known_names:
                activity_names.append(name)
                known_names.add(name.casefold())
        configured_names = set()
        for plan_item, _details in plan_items:
            activity_name = str(plan_item[1] or "").strip()
            label = f"{activity_name} — {plan_item[3]}"
            if plan_item[5] is not None:
                label = _plan_item_display_name(
                    activity_name, plan_item[6] or "قسم مخصص"
                ) + f" — {plan_item[3]}"
            self.mode_activity_combo.addItem(label, plan_item[0])
            self.mode_activity_combo.setItemData(
                self.mode_activity_combo.count() - 1,
                activity_name, Qt.UserRole + 1,
            )
            configured_names.add(activity_name.casefold())
        for activity_name in activity_names:
            if activity_name.strip().casefold() in configured_names:
                continue
            self.mode_activity_combo.addItem(activity_name, activity_name)
            self.mode_activity_combo.setItemData(
                self.mode_activity_combo.count() - 1,
                activity_name, Qt.UserRole + 1,
            )
        index = self.mode_activity_combo.findData(current_id)
        self.mode_activity_combo.setCurrentIndex(
            index if index >= 0 else (0 if self.mode_activity_combo.count() else -1)
        )
        self.mode_activity_combo.blockSignals(False)

    def refresh_integration_table(self):
        records = self.db.teaching_plan_integration_activities(self.plan[0])
        self.integration_table.setRowCount(len(records))
        for row, record in enumerate(records):
            activity_name, _prompt, template, _target, contents = record
            values = (
                activity_name,
                template,
                "، ".join(contents),
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value or ""))
                if column == 0:
                    cell.setData(Qt.UserRole, activity_name)
                self.integration_table.setItem(row, column, cell)
        self.update_integration_table_actions()

    def update_integration_table_actions(self):
        enabled = self.integration_table.currentRow() >= 0
        self.edit_integration_activity_button.setEnabled(enabled)
        self.delete_integration_activity_button.setEnabled(enabled)

    def load_integration_contents(self, *_args):
        activity_name = self.integration_activity_combo.currentText().strip()
        if (
            not self.integration_data_loading
            and activity_name != getattr(self, "last_integration_activity", "")
        ):
            if self.editing_integration_activity:
                self.integration_autosave_timer.stop()
                self.editing_integration_activity = None
            self.integration_selected_contents.clear()
            self.integration_data_loading = True
            self.integration_prompt_edit.clear()
            self.integration_target_edit.clear()
            self.integration_data_loading = False
        self.last_integration_activity = activity_name
        contents = self.db.teaching_plan_integration_contents(
            self.plan[0], activity_name
        )
        self.integration_prompt_edit.set_suggestions(contents)
        self.integration_contents_table.blockSignals(True)
        self.integration_contents_table.setRowCount(len(contents))
        for row, content in enumerate(contents):
            item = QTableWidgetItem(content)
            item.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsUserCheckable
            )
            item.setCheckState(
                Qt.Checked
                if content in self.integration_selected_contents
                else Qt.Unchecked
            )
            self.integration_contents_table.setItem(row, 0, item)
        self.integration_contents_table.blockSignals(False)
        self.update_integration_target()

    def update_integration_content_selection(self, item):
        if item is None or self.integration_data_loading:
            return
        content = item.text().strip()
        if item.checkState() == Qt.Checked:
            self.integration_selected_contents.add(content)
        else:
            self.integration_selected_contents.discard(content)
        self.update_integration_target()
        self.schedule_integration_autosave()

    def update_integration_template(self, template):
        if self.integration_data_loading:
            return
        previous = self.last_integration_template
        prompt = self.integration_prompt_edit.toPlainText().strip()
        if not prompt or prompt == previous:
            self.integration_data_loading = True
            self.integration_prompt_edit.setPlainText(template)
            self.integration_data_loading = False
        self.last_integration_template = template
        self.update_integration_target()
        self.schedule_integration_autosave()

    def on_integration_prompt_changed(self):
        if self.integration_data_loading:
            return
        self.update_integration_target()
        self.schedule_integration_autosave()

    def update_integration_target(self, *_args):
        prompt = self.integration_prompt_edit.toPlainText().strip()
        if not prompt:
            prompt = self.integration_template_combo.currentText().strip()
        selected_contents = [
            self.integration_contents_table.item(row, 0).text()
            for row in range(self.integration_contents_table.rowCount())
            if self.integration_contents_table.item(
                row, 0
            ).checkState() == Qt.Checked
        ]
        target_text = "\n".join(
            part for part in (prompt, *selected_contents) if part
        )
        if self.integration_target_edit.toPlainText() != target_text:
            self.integration_data_loading = True
            self.integration_target_edit.setPlainText(target_text)
            self.integration_data_loading = False

    def schedule_integration_autosave(self, *_args):
        if (
            not self.integration_data_loading
            and self.editing_integration_activity
        ):
            self.integration_autosave_timer.start()

    def autosave_integration_draft(self):
        if not self.editing_integration_activity:
            return True
        activity_name = self.integration_activity_combo.currentText().strip()
        if activity_name != self.editing_integration_activity:
            return True
        selected_contents = [
            self.integration_contents_table.item(row, 0).text()
            for row in range(self.integration_contents_table.rowCount())
            if self.integration_contents_table.item(
                row, 0
            ).checkState() == Qt.Checked
        ]
        try:
            self.db.save_teaching_plan_integration_activity(
                self.plan[0],
                activity_name,
                self.integration_prompt_edit.toPlainText(),
                self.integration_template_combo.currentText(),
                self.integration_target_edit.toPlainText(),
                selected_contents,
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(
                self, "تعذر الحفظ التلقائي لنشاط الإدماج", str(exc)
            )
            return False
        self.refresh_integration_table()
        return True

    def add_integration_activity(self):
        activity_name = self.integration_activity_combo.currentText().strip()
        if not activity_name:
            QMessageBox.warning(self, "اختيار مطلوب", "اختر نشاطاً أولاً.")
            return
        selected_contents = [
            self.integration_contents_table.item(row, 0).text()
            for row in range(self.integration_contents_table.rowCount())
            if self.integration_contents_table.item(
                row, 0
            ).checkState() == Qt.Checked
        ]
        try:
            self.db.save_teaching_plan_integration_activity(
                self.plan[0],
                activity_name,
                self.integration_prompt_edit.toPlainText(),
                self.integration_template_combo.currentText(),
                self.integration_target_edit.toPlainText(),
                selected_contents,
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "تعذر إضافة نشاط الإدماج", str(exc))
            return
        self.editing_integration_activity = activity_name
        self.integration_activity_combo.setEnabled(False)
        self.refresh_integration_table()

    def new_integration_activity(self):
        self.integration_autosave_timer.stop()
        self.editing_integration_activity = None
        self.integration_activity_combo.setEnabled(True)
        existing = {
            record[0]
            for record in self.db.teaching_plan_integration_activities(
                self.plan[0]
            )
        }
        next_index = next(
            (
                index for index in range(self.integration_activity_combo.count())
                if self.integration_activity_combo.itemText(index) not in existing
            ),
            0,
        )
        self.integration_activity_combo.setCurrentIndex(next_index)
        self.integration_data_loading = True
        self.integration_selected_contents.clear()
        self.integration_prompt_edit.clear()
        self.integration_template_combo.setCurrentIndex(0)
        self.last_integration_template = (
            self.integration_template_combo.currentText()
        )
        self.integration_data_loading = False
        self.load_integration_contents()
        self.integration_prompt_edit.setFocus()

    def edit_integration_activity(self):
        row = self.integration_table.currentRow()
        if row < 0:
            return
        activity_name = self.integration_table.item(
            row, 0
        ).data(Qt.UserRole)
        record = next(
            (
                value for value
                in self.db.teaching_plan_integration_activities(self.plan[0])
                if value[0] == activity_name
            ),
            None,
        )
        if record is None:
            return
        _, prompt, template, target, selected_contents = record
        self.integration_autosave_timer.stop()
        self.integration_data_loading = True
        self.editing_integration_activity = activity_name
        self.integration_activity_combo.setEnabled(False)
        self.integration_selected_contents = set(selected_contents)
        index = self.integration_activity_combo.findText(
            activity_name, Qt.MatchFixedString
        )
        self.integration_activity_combo.setCurrentIndex(index)
        self.integration_template_combo.setCurrentText(template)
        self.last_integration_template = template
        self.integration_prompt_edit.setPlainText(prompt)
        self.integration_target_edit.setPlainText(target)
        self.integration_data_loading = False
        self.load_integration_contents()
        self.integration_data_loading = True
        self.integration_target_edit.setPlainText(target)
        self.integration_data_loading = False

    def delete_integration_activity(self):
        row = self.integration_table.currentRow()
        if row < 0:
            return
        activity_name = self.integration_table.item(
            row, 0
        ).data(Qt.UserRole)
        try:
            self.db.delete_teaching_plan_integration_activity(
                self.plan[0], activity_name
            )
        except sqlite3.Error as exc:
            QMessageBox.warning(self, "تعذر حذف نشاط الإدماج", str(exc))
            return
        if self.editing_integration_activity == activity_name:
            self.integration_autosave_timer.stop()
            self.editing_integration_activity = None
            self.integration_activity_combo.setEnabled(True)
        self.refresh_integration_table()

    def refresh_evaluation_table(self):
        records = self.db.teaching_plan_evaluation_activities(self.plan[0])
        self.evaluation_table.setRowCount(len(records))
        for row, record in enumerate(records):
            activity_name, _prompt, template, _target, contents = record
            values = (
                activity_name,
                template,
                "، ".join(contents),
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value or ""))
                if column == 0:
                    cell.setData(Qt.UserRole, activity_name)
                self.evaluation_table.setItem(row, column, cell)
        self.update_evaluation_table_actions()

    def update_evaluation_table_actions(self):
        enabled = self.evaluation_table.currentRow() >= 0
        self.edit_evaluation_activity_button.setEnabled(enabled)
        self.delete_evaluation_activity_button.setEnabled(enabled)

    def load_evaluation_contents(self, *_args):
        activity_name = self.evaluation_activity_combo.currentText().strip()
        if (
            not self.evaluation_data_loading
            and activity_name != getattr(self, "last_evaluation_activity", "")
        ):
            if self.editing_evaluation_activity:
                self.evaluation_autosave_timer.stop()
                self.editing_evaluation_activity = None
            self.evaluation_selected_contents.clear()
            self.evaluation_data_loading = True
            self.evaluation_prompt_edit.clear()
            self.evaluation_target_edit.clear()
            self.evaluation_data_loading = False
        self.last_evaluation_activity = activity_name
        contents = self.db.teaching_plan_integration_contents(
            self.plan[0], activity_name
        )
        self.evaluation_prompt_edit.set_suggestions(contents)
        self.evaluation_contents_table.blockSignals(True)
        self.evaluation_contents_table.setRowCount(len(contents))
        for row, content in enumerate(contents):
            item = QTableWidgetItem(content)
            item.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsUserCheckable
            )
            item.setCheckState(
                Qt.Checked
                if content in self.evaluation_selected_contents
                else Qt.Unchecked
            )
            self.evaluation_contents_table.setItem(row, 0, item)
        self.evaluation_contents_table.blockSignals(False)
        self.update_evaluation_target()

    def update_evaluation_content_selection(self, item):
        if item is None or self.evaluation_data_loading:
            return
        content = item.text().strip()
        if item.checkState() == Qt.Checked:
            self.evaluation_selected_contents.add(content)
        else:
            self.evaluation_selected_contents.discard(content)
        self.update_evaluation_target()
        self.schedule_evaluation_autosave()

    def update_evaluation_template(self, template):
        if self.evaluation_data_loading:
            return
        previous = self.last_evaluation_template
        prompt = self.evaluation_prompt_edit.toPlainText().strip()
        if not prompt or prompt == previous:
            self.evaluation_data_loading = True
            self.evaluation_prompt_edit.setPlainText(template)
            self.evaluation_data_loading = False
        self.last_evaluation_template = template
        self.update_evaluation_target()
        self.schedule_evaluation_autosave()

    def on_evaluation_prompt_changed(self):
        if self.evaluation_data_loading:
            return
        self.update_evaluation_target()
        self.schedule_evaluation_autosave()

    def update_evaluation_target(self, *_args):
        prompt = self.evaluation_prompt_edit.toPlainText().strip()
        if not prompt:
            prompt = self.evaluation_template_combo.currentText().strip()
        selected_contents = [
            self.evaluation_contents_table.item(row, 0).text()
            for row in range(self.evaluation_contents_table.rowCount())
            if self.evaluation_contents_table.item(
                row, 0
            ).checkState() == Qt.Checked
        ]
        target_text = "\n".join(
            part for part in (prompt, *selected_contents) if part
        )
        if self.evaluation_target_edit.toPlainText() != target_text:
            self.evaluation_data_loading = True
            self.evaluation_target_edit.setPlainText(target_text)
            self.evaluation_data_loading = False

    def schedule_evaluation_autosave(self, *_args):
        if (
            not self.evaluation_data_loading
            and self.editing_evaluation_activity
        ):
            self.evaluation_autosave_timer.start()

    def autosave_evaluation_draft(self):
        if not self.editing_evaluation_activity:
            return True
        activity_name = self.evaluation_activity_combo.currentText().strip()
        if activity_name != self.editing_evaluation_activity:
            return True
        selected_contents = [
            self.evaluation_contents_table.item(row, 0).text()
            for row in range(self.evaluation_contents_table.rowCount())
            if self.evaluation_contents_table.item(
                row, 0
            ).checkState() == Qt.Checked
        ]
        try:
            self.db.save_teaching_plan_evaluation_activity(
                self.plan[0],
                activity_name,
                self.evaluation_prompt_edit.toPlainText(),
                self.evaluation_template_combo.currentText(),
                self.evaluation_target_edit.toPlainText(),
                selected_contents,
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(
                self, "تعذر الحفظ التلقائي لنشاط التقييم", str(exc)
            )
            return False
        self.refresh_evaluation_table()
        return True

    def add_evaluation_activity(self):
        activity_name = self.evaluation_activity_combo.currentText().strip()
        if not activity_name:
            QMessageBox.warning(self, "اختيار مطلوب", "اختر نشاطاً أولاً.")
            return
        selected_contents = [
            self.evaluation_contents_table.item(row, 0).text()
            for row in range(self.evaluation_contents_table.rowCount())
            if self.evaluation_contents_table.item(
                row, 0
            ).checkState() == Qt.Checked
        ]
        try:
            self.db.save_teaching_plan_evaluation_activity(
                self.plan[0],
                activity_name,
                self.evaluation_prompt_edit.toPlainText(),
                self.evaluation_template_combo.currentText(),
                self.evaluation_target_edit.toPlainText(),
                selected_contents,
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "تعذر إضافة نشاط التقييم", str(exc))
            return
        self.editing_evaluation_activity = activity_name
        self.evaluation_activity_combo.setEnabled(False)
        self.refresh_evaluation_table()

    def new_evaluation_activity(self):
        self.evaluation_autosave_timer.stop()
        self.editing_evaluation_activity = None
        self.evaluation_activity_combo.setEnabled(True)
        existing = {
            record[0]
            for record in self.db.teaching_plan_evaluation_activities(
                self.plan[0]
            )
        }
        next_index = next(
            (
                index
                for index in range(self.evaluation_activity_combo.count())
                if self.evaluation_activity_combo.itemText(index) not in existing
            ),
            0,
        )
        self.evaluation_activity_combo.setCurrentIndex(next_index)
        self.evaluation_data_loading = True
        self.evaluation_selected_contents.clear()
        self.evaluation_prompt_edit.clear()
        self.evaluation_template_combo.setCurrentIndex(0)
        self.last_evaluation_template = (
            self.evaluation_template_combo.currentText()
        )
        self.evaluation_data_loading = False
        self.load_evaluation_contents()
        self.evaluation_prompt_edit.setFocus()

    def edit_evaluation_activity(self):
        row = self.evaluation_table.currentRow()
        if row < 0:
            return
        activity_name = self.evaluation_table.item(
            row, 0
        ).data(Qt.UserRole)
        record = next(
            (
                value
                for value in self.db.teaching_plan_evaluation_activities(
                    self.plan[0]
                )
                if value[0] == activity_name
            ),
            None,
        )
        if record is None:
            return
        _, prompt, template, target, selected_contents = record
        self.evaluation_autosave_timer.stop()
        self.evaluation_data_loading = True
        self.editing_evaluation_activity = activity_name
        self.evaluation_activity_combo.setEnabled(False)
        self.evaluation_selected_contents = set(selected_contents)
        index = self.evaluation_activity_combo.findText(
            activity_name, Qt.MatchFixedString
        )
        self.evaluation_activity_combo.setCurrentIndex(index)
        self.evaluation_template_combo.setCurrentText(template)
        self.last_evaluation_template = template
        self.evaluation_prompt_edit.setPlainText(prompt)
        self.evaluation_target_edit.setPlainText(target)
        self.evaluation_data_loading = False
        self.load_evaluation_contents()
        self.evaluation_data_loading = True
        self.evaluation_target_edit.setPlainText(target)
        self.evaluation_data_loading = False

    def delete_evaluation_activity(self):
        row = self.evaluation_table.currentRow()
        if row < 0:
            return
        activity_name = self.evaluation_table.item(
            row, 0
        ).data(Qt.UserRole)
        try:
            self.db.delete_teaching_plan_evaluation_activity(
                self.plan[0], activity_name
            )
        except sqlite3.Error as exc:
            QMessageBox.warning(self, "تعذر حذف نشاط التقييم", str(exc))
            return
        if self.editing_evaluation_activity == activity_name:
            self.evaluation_autosave_timer.stop()
            self.editing_evaluation_activity = None
            self.evaluation_activity_combo.setEnabled(True)
        self.refresh_evaluation_table()

    def refresh_support_table(self):
        records = self.db.teaching_plan_support_activities(self.plan[0])
        self.support_table.setRowCount(len(records))
        for row, (activity_name, _target_criteria) in enumerate(records):
            target_criteria = self.support_target_criteria_text
            for column, value in enumerate((activity_name, target_criteria)):
                cell = QTableWidgetItem(str(value or ""))
                if column == 0:
                    cell.setData(Qt.UserRole, activity_name)
                self.support_table.setItem(row, column, cell)
        self.update_support_table_actions()

    def update_support_table_actions(self):
        enabled = self.support_table.currentRow() >= 0
        self.edit_support_activity_button.setEnabled(enabled)
        self.delete_support_activity_button.setEnabled(enabled)

    def load_support_activity(self, *_args):
        activity_name = self.support_activity_combo.currentText().strip()
        if self.editing_support_activity:
            if activity_name != self.editing_support_activity:
                self.support_autosave_timer.stop()
                self.editing_support_activity = None
                self.support_activity_combo.setEnabled(True)
        self.support_target_criteria_edit.blockSignals(True)
        self.support_target_criteria_edit.setPlainText(
            self.support_target_criteria_text
        )
        self.support_target_criteria_edit.blockSignals(False)

    def autosave_support_activity(self):
        activity_name = self.editing_support_activity
        if not activity_name:
            return True
        if self.support_activity_combo.currentText().strip() != activity_name:
            return True
        try:
            self.db.save_teaching_plan_support_activity(
                self.plan[0],
                activity_name,
                self.support_target_criteria_text,
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(
                self, "تعذر الحفظ التلقائي لمعايير الدعم والعلاج", str(exc)
            )
            return False
        self.refresh_support_table()
        return True

    def add_support_activity(self):
        activity_name = self.support_activity_combo.currentText().strip()
        if not activity_name:
            QMessageBox.warning(self, "اختيار مطلوب", "اختر نشاطاً أولاً.")
            return
        try:
            self.db.save_teaching_plan_support_activity(
                self.plan[0],
                activity_name,
                self.support_target_criteria_text,
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(
                self, "تعذر إضافة نشاط الدعم والعلاج", str(exc)
            )
            return
        self.editing_support_activity = activity_name
        self.support_activity_combo.setEnabled(False)
        self.refresh_support_table()

    def new_support_activity(self):
        self.support_autosave_timer.stop()
        self.editing_support_activity = None
        self.support_activity_combo.setEnabled(True)
        existing = {
            record[0]
            for record in self.db.teaching_plan_support_activities(
                self.plan[0]
            )
        }
        next_index = next(
            (
                index
                for index in range(self.support_activity_combo.count())
                if self.support_activity_combo.itemText(index) not in existing
            ),
            0,
        )
        self.support_activity_combo.setCurrentIndex(next_index)
        self.support_target_criteria_edit.setPlainText(
            self.support_target_criteria_text
        )
        self.support_target_criteria_edit.setFocus()

    def edit_support_activity(self):
        row = self.support_table.currentRow()
        if row < 0:
            return
        activity_name = self.support_table.item(
            row, 0
        ).data(Qt.UserRole)
        index = self.support_activity_combo.findText(
            activity_name, Qt.MatchFixedString
        )
        if index < 0:
            return
        self.support_activity_combo.setCurrentIndex(index)
        self.editing_support_activity = activity_name
        self.support_activity_combo.setEnabled(False)
        self.load_support_activity()
        self.support_target_criteria_edit.setFocus()

    def delete_support_activity(self):
        row = self.support_table.currentRow()
        if row < 0:
            return
        activity_name = self.support_table.item(
            row, 0
        ).data(Qt.UserRole)
        try:
            self.db.delete_teaching_plan_support_activity(
                self.plan[0], activity_name
            )
        except sqlite3.Error as exc:
            QMessageBox.warning(
                self, "تعذر حذف نشاط الدعم والعلاج", str(exc)
            )
            return
        if self.editing_support_activity == activity_name:
            self.support_autosave_timer.stop()
            self.editing_support_activity = None
            self.support_activity_combo.setEnabled(True)
        self.refresh_support_table()

    @staticmethod
    def _same_activity_name(first, second):
        first = str(first or "").strip().casefold()
        second = str(second or "").strip().casefold()
        if first == second:
            return True
        first_aliases = {
            alias.casefold()
            for alias in PLAN_ACTIVITY_ALIASES.get(first, ())
        }
        second_aliases = {
            alias.casefold()
            for alias in PLAN_ACTIVITY_ALIASES.get(second, ())
        }
        return second in first_aliases or first in second_aliases

    def cache_mode_activity_state(self, mode=None):
        mode = mode or self.mode_activity_mode
        item_id = self.mode_activity_id
        if item_id is None:
            return
        selections = self.pending_mode_selections.setdefault(mode, {})
        if mode in ("إدماج", "تقييم"):
            draft_key = (mode, item_id)
            objectives = self.current_target_objectives()
            self.mode_objective_drafts[draft_key] = objectives
            if item_id in selections:
                selections[item_id] = objectives
            self.pending_mode_targets.setdefault(mode, {})[item_id] = (
                self.target_competencies_edit.toPlainText()
            )
        elif mode == "دعم وعلاج" and item_id in selections:
            selections[item_id] = set()

    def load_mode_activity_options(self, *_args):
        self.cache_mode_activity_state()
        item_id = self.mode_activity_combo.currentData()
        mode = self.mode_combo.currentText()
        self.loading_mode_activity = True
        self.mode_activity_id = item_id
        self.mode_objective_combo.blockSignals(True)
        self.mode_objective_combo.model().blockSignals(True)
        self.mode_objective_combo.clear()
        if item_id is not None and mode in ("إدماج", "تقييم"):
            saved_mode = self.saved_mode_settings["modes"].get(mode, {})
            selected = self.mode_objective_drafts.get(
                (mode, item_id),
                self.pending_mode_selections.get(mode, {}).get(
                    item_id,
                    saved_mode.get("selected_objectives", {}).get(
                        item_id, set()
                    ),
                ),
            )
            for objective in self.mode_objectives_for_activity(item_id):
                self.mode_objective_combo.add_checkable_item(
                    objective, objective in selected
                )
            for objective in sorted(set(selected)):
                if self.mode_objective_combo.findText(
                    objective, Qt.MatchFixedString
                ) < 0:
                    self.mode_objective_combo.add_checkable_item(
                        objective, checked=True
                    )
            target_text = self.pending_mode_targets.get(mode, {}).get(
                item_id, "\n" + "\n".join(sorted(selected))
            )
            self.target_competencies_edit.setPlainText(target_text)
        else:
            self.target_competencies_edit.clear()
        self.mode_objective_combo.model().blockSignals(False)
        self.mode_objective_combo.blockSignals(False)
        self.mode_objective_combo.refresh_text()
        self.mode_activity_mode = mode
        self.loading_mode_activity = False
        selected_ids = set(self.collect_mode_selections(mode))
        self.toggle_mode_activity_button.setText(
            "إزالة النشاط المحدد"
            if item_id in selected_ids else "إضافة النشاط المحدد"
        )
        _set_action_type(
            self.toggle_mode_activity_button,
            "delete" if item_id in selected_ids else "add",
        )

    def mode_objectives_for_activity(self, item_id):
        objectives = []
        activity_name = str(
            self.mode_activity_combo.currentData(Qt.UserRole + 1) or ""
        ).strip()
        for item, details in self.db.teaching_plan_items(self.plan[0]):
            matches_item = item[0] == item_id if isinstance(
                item_id, int
            ) else self._same_activity_name(item[1], activity_name)
            if matches_item:
                for detail in details:
                    objective = str(detail[3] or "").strip()
                    if objective and objective not in objectives:
                        objectives.append(objective)
        if not objectives:
            for competence_id, _activity_name, competence_text in (
                self.db.teaching_plan_item_competences(
                    self.plan[0], activity_name
                )
            ):
                for suggestion in self.db.teaching_plan_item_suggestions(
                    self.plan[0], competence_id, competence_text
                ):
                    objective = str(suggestion[6] or "").strip()
                    if objective and objective not in objectives:
                        objectives.append(objective)
        for _item, details in self.db.teaching_plan_items(self.plan[0]):
            for detail in details:
                objective = str(detail[3] or "").strip()
                if objective and objective not in objectives:
                    objectives.append(objective)
        return objectives

    def update_mode_objective_text(self, _item):
        item_id = self.mode_activity_combo.currentData()
        if item_id is None:
            return
        mode = self.mode_activity_mode
        selected = set(self.mode_objective_combo.checked_texts())
        self.mode_objective_drafts[(mode, item_id)] = selected
        if item_id in self.pending_mode_selections.setdefault(mode, {}):
            self.pending_mode_selections[mode][item_id] = selected
        text = "\n" + "\n".join(self.mode_objective_combo.checked_texts())
        self.pending_mode_targets.setdefault(mode, {})[item_id] = text
        self.target_competencies_edit.setPlainText(text)
        self.schedule_mode_settings_autosave()

    def on_mode_objective_changed(self, item):
        self.update_mode_objective_text(item)

    def schedule_mode_settings_autosave(self, *_args):
        if not self.loading_mode_activity:
            self.mode_settings_autosave_timer.start()

    def current_target_objectives(self):
        return {
            line.strip()
            for line in self.target_competencies_edit.toPlainText().splitlines()
            if line.strip()
        }

    def cache_mode_target_text(self):
        item_id = self.mode_activity_combo.currentData()
        mode = self.mode_activity_mode
        if (
            self.loading_mode_activity or item_id is None
            or mode not in ("إدماج", "تقييم")
        ):
            return
        self.pending_mode_targets.setdefault(mode, {})[item_id] = (
            self.target_competencies_edit.toPlainText()
        )
        self.schedule_mode_settings_autosave()

    def toggle_mode_activity(self):
        item_id = self.mode_activity_combo.currentData()
        if item_id is None:
            return
        mode = self.mode_activity_mode
        selections = self.pending_mode_selections.setdefault(mode, {})
        if item_id in selections:
            del selections[item_id]
        elif mode in ("إدماج", "تقييم"):
            objectives = self.current_target_objectives()
            if not objectives:
                QMessageBox.warning(
                    self, "اختيار مطلوب",
                    "اختر هدفاً مميزاً واحداً على الأقل لهذا النشاط.",
                )
                return
            selections[item_id] = objectives
        else:
            selections[item_id] = set()
        self.load_mode_activity_options()
        self.schedule_mode_settings_autosave()

    def populate_mode_objectives_tree(self):
        mode = self.mode_combo.currentText()
        previous_selection = self.pending_mode_selections.get(mode)
        if previous_selection is None:
            previous_selection = self.saved_mode_settings["modes"].get(
                mode, {}
            ).get("selected_objectives", {})
        saved_items = self.saved_mode_settings["modes"].get(
            mode, {}
        ).get("selected_items", set())
        all_objectives = []
        for _plan_item, details in self.db.teaching_plan_items(self.plan[0]):
            for detail in details:
                objective = str(detail[3] or "").strip()
                if objective and objective not in all_objectives:
                    all_objectives.append(objective)

        self.mode_objectives_tree.blockSignals(True)
        self.mode_objectives_tree.clear()
        for plan_item, details in self.db.teaching_plan_items(self.plan[0]):
            item_id = plan_item[0]
            item_label = f"{plan_item[1]} — {plan_item[3]}"
            if plan_item[5] is not None:
                item_label += f" ({plan_item[6] or 'قسم مخصص'})"
            activity_node = QTreeWidgetItem([
                item_label
            ])
            activity_node.setData(0, Qt.UserRole, item_id)
            activity_node.setFlags(
                activity_node.flags()
                | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
            )
            activity_node.setCheckState(
                0, Qt.Checked if item_id in saved_items else Qt.Unchecked
            )
            if mode in ("إدماج", "تقييم"):
                objectives = []
                for detail in details:
                    objective = str(detail[3] or "").strip()
                    if objective and objective not in objectives:
                        objectives.append(objective)
                if not objectives:
                    objectives = all_objectives
                selected_objectives = previous_selection.get(
                    item_id, set()
                )
                for objective in objectives:
                    objective_node = QTreeWidgetItem([objective])
                    objective_node.setData(0, Qt.UserRole, objective)
                    objective_node.setFlags(
                        objective_node.flags()
                        | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                    )
                    objective_node.setCheckState(
                        0,
                        Qt.Checked
                        if objective in selected_objectives
                        else Qt.Unchecked,
                    )
                    activity_node.addChild(objective_node)
            self.mode_objectives_tree.addTopLevelItem(activity_node)
        self.mode_objectives_tree.blockSignals(False)

    def update_mode_tree_check_state(self, item, _column):
        if item.parent() is not None:
            parent = item.parent()
            selected_count = sum(
                parent.child(index).checkState(0) == Qt.Checked
                for index in range(parent.childCount())
            )
            if selected_count == 0:
                state = Qt.Unchecked
            elif selected_count == parent.childCount():
                state = Qt.Checked
            else:
                state = Qt.PartiallyChecked
            parent.setCheckState(0, state)
        else:
            state = item.checkState(0)
            for index in range(item.childCount()):
                item.child(index).setCheckState(0, state)
        self.schedule_mode_settings_autosave()

    def collect_mode_selections(self, mode=None):
        mode = mode or self.mode_combo.currentText()
        self.cache_mode_activity_state(mode)
        return {
            item_id: set(objectives)
            for item_id, objectives in self.pending_mode_selections.get(
                mode, {}
            ).items()
        }

    def autosave_mode_settings(self, mode=None):
        mode = mode or self.mode_combo.currentText()
        if mode == "إدماج":
            return True
        selections = (
            self.collect_mode_selections(mode)
            if mode != "منهجي" else {}
        )
        try:
            self.db.save_teaching_plan_mode_settings(
                self.plan[0],
                mode,
                selections,
                self.expected_performance_edit.toPlainText()
                if mode == "تقييم" else "",
            )
        except ValueError as exc:
            QMessageBox.warning(self, "تعذر الحفظ التلقائي", str(exc))
            return False
        self.saved_mode_settings = self.db.teaching_plan_mode_settings(
            self.plan[0]
        )
        setting = self.saved_mode_settings["modes"].get(mode, {})
        self.pending_mode_selections[mode] = {
            **{
                item_id: set(objectives)
                for item_id, objectives in setting.get(
                    "selected_objectives", {}
                ).items()
            },
            **{
                item_id: set()
                for item_id in setting.get("selected_items", set())
                if item_id not in setting.get("selected_objectives", {})
            },
            **{
                activity_name: set(objectives)
                for activity_name, objectives in setting.get(
                    "selected_activities", {}
                ).items()
            },
        }
        self.pending_mode_performance.pop(mode, None)
        return True

    @staticmethod
    def _prepare_suggestion_combo(combo, suggestions, selected_text=""):
        combo.blockSignals(True)
        combo.clear()
        seen = set()
        for text, record_id in suggestions:
            text = str(text or "").strip()
            if not text or (text, record_id) in seen:
                continue
            seen.add((text, record_id))
            combo.addItem(text, record_id)
        combo.setCurrentText(selected_text)
        combo.blockSignals(False)

    @staticmethod
    def _selected_record_id(combo):
        index = combo.findText(combo.currentText(), Qt.MatchExactly)
        return combo.itemData(index) if index >= 0 else None

    def selected_scope_class_id(self):
        if self.activity_scope_combo.currentData() != "class":
            return None
        return self.activity_scope_class_combo.currentData()

    def set_activity_scope(self, class_id):
        self.activity_scope_combo.blockSignals(True)
        self.activity_scope_class_combo.blockSignals(True)
        if class_id is None:
            index = self.activity_scope_combo.findData(None)
        else:
            index = self.activity_scope_combo.findData("class")
            class_index = self.activity_scope_class_combo.findData(class_id)
            if class_index >= 0:
                self.activity_scope_class_combo.setCurrentIndex(class_index)
        self.activity_scope_combo.setCurrentIndex(index)
        self.activity_scope_combo.blockSignals(False)
        self.activity_scope_class_combo.blockSignals(False)
        self.update_activity_scope()

    def update_activity_scope(self, *_args):
        class_specific = (
            self.activity_scope_combo.currentData() == "class"
            and bool(self.scope_classes)
        )
        self.activity_scope_class_label.setVisible(class_specific)
        self.activity_scope_class_combo.setVisible(class_specific)
        if not self.loading_form:
            self._refresh_activity_suggestions()
            self.load_competence_suggestions(
                self.activity_combo.currentText()
            )

    def load_competence_suggestions(self, activity_name):
        rows = self.db.teaching_plan_item_competences(
            self.plan[0], activity_name.strip(),
            self.selected_scope_class_id(),
        ) if activity_name.strip() else []
        selected_competence = rows[0][2] if rows else ""
        self._prepare_suggestion_combo(
            self.competence_combo,
            [(row[2], row[0]) for row in rows],
            selected_competence,
        )
        self.component_combo.setCurrentText("")
        self.objective_combo.setCurrentText("")
        self.content_combo.setCurrentText("")
        self.load_component_suggestions()

    def suggestion_rows(self):
        competence_text = self.competence_combo.currentText().strip()
        if not competence_text:
            return []
        competence_id = self._selected_record_id(self.competence_combo)
        return self.db.teaching_plan_item_suggestions(
            self.plan[0], competence_id,
            competence_text,
            self.selected_scope_class_id(),
        )

    def load_component_suggestions(self, *_args):
        rows = self.suggestion_rows()
        self._prepare_suggestion_combo(
            self.component_combo,
            [(row[4], row[3]) for row in rows if row[4]],
            "",
        )
        self.objective_combo.setCurrentText("")
        self.content_combo.setCurrentText("")
        self.load_objective_suggestions()

    def load_objective_suggestions(self, *_args):
        rows = self.suggestion_rows()
        component_id = self._selected_record_id(self.component_combo)
        if component_id is not None:
            rows = [row for row in rows if row[3] == component_id]
        else:
            component_text = self.component_combo.currentText().strip()
            rows = [
                row for row in rows
                if str(row[4] or "").strip() == component_text
            ] if component_text else []
        suggestions = [(row[6], row[5]) for row in rows if row[6]]
        unique_suggestions = []
        seen_texts = set()
        for text, record_id in suggestions:
            normalized = str(text or "").strip()
            if normalized and normalized not in seen_texts:
                seen_texts.add(normalized)
                unique_suggestions.append((normalized, record_id))
        self._prepare_suggestion_combo(
            self.objective_combo,
            unique_suggestions,
            "",
        )
        self.content_combo.setCurrentText("")
        self.load_content_suggestions()

    def load_content_suggestions(self, *_args):
        rows = self.suggestion_rows()
        component_id = self._selected_record_id(self.component_combo)
        objective_id = self._selected_record_id(self.objective_combo)
        if component_id is not None:
            rows = [row for row in rows if row[3] == component_id]
        else:
            component_text = self.component_combo.currentText().strip()
            rows = [
                row for row in rows
                if str(row[4] or "").strip() == component_text
            ] if component_text else []
        if objective_id is not None:
            rows = [row for row in rows if row[5] == objective_id]
        else:
            objective_text = self.objective_combo.currentText().strip()
            rows = [
                row for row in rows
                if str(row[6] or "").strip() == objective_text
            ] if objective_text else []
        if not self.competence_combo.currentText().strip():
            rows = []
        self._prepare_suggestion_combo(
            self.content_combo,
            [(row[8], row[7]) for row in rows if row[8]],
            "",
        )

    def save_detail(self):
        if (
            not self.activity_combo.currentText().strip()
            or not self.competence_combo.currentText().strip()
        ):
            QMessageBox.warning(
                self, "بيانات ناقصة",
                "حدد النشاط وكفايته قبل إضافة مكوّن أو محتوى.",
            )
            return
        component_text = self.component_combo.currentText().strip()
        objective_text = self.objective_combo.currentText().strip()
        content_text = self.content_combo.currentText().strip()
        detail = (
            self._selected_record_id(self.component_combo),
            component_text,
            self._selected_record_id(self.objective_combo),
            objective_text,
            self._selected_record_id(self.content_combo),
            content_text,
        )
        if self.editing_detail_index is None:
            self.details.append(detail)
            row = self.details_table.rowCount()
            self.details_table.insertRow(row)
        else:
            row = self.editing_detail_index
            self.details[row] = detail
        for column, text in enumerate((
            component_text, objective_text, content_text,
        )):
            self.details_table.setItem(row, column, QTableWidgetItem(text))
        self.editing_detail_index = None
        self.save_detail_button.setText("إضافة المكوّن والمحتوى")
        _set_action_type(self.save_detail_button, "add")
        self.component_combo.setCurrentText("")
        self.objective_combo.setCurrentText("")
        self.content_combo.setCurrentText("")
        self.update_locked_fields()
        self.load_objective_suggestions()
        self.persist_item()

    def schedule_detail_autosave(self, *_args):
        if self.loading_form:
            return
        if (
            self.activity_combo.currentText().strip()
            and self.competence_combo.currentText().strip()
            and self.component_combo.currentText().strip()
            and self.content_combo.currentText().strip()
        ):
            self.detail_autosave_timer.start()

    def autosave_detail(self):
        if (
            self.activity_combo.currentText().strip()
            and self.competence_combo.currentText().strip()
            and self.component_combo.currentText().strip()
            and self.content_combo.currentText().strip()
        ):
            self.save_detail()

    def edit_detail(self):
        row = self.details_table.currentRow()
        if row < 0:
            return
        detail = self.details[row]
        self.loading_form = True
        self._set_combo_value(
            self.component_combo, detail[0], detail[1]
        )
        self._set_combo_value(self.objective_combo, detail[2], detail[3])
        self._set_combo_value(self.content_combo, detail[4], detail[5])
        self.editing_detail_index = row
        self.loading_form = False
        self.save_detail_button.setText("تعديل السطر المحدد")
        _set_action_type(self.save_detail_button, "edit")

    @staticmethod
    def _set_combo_value(combo, record_id, text):
        if record_id is not None:
            index = combo.findData(record_id)
            if index >= 0:
                combo.setCurrentIndex(index)
                return
        combo.setCurrentText(text)

    def update_locked_fields(self):
        locked = bool(self.details)
        self.activity_combo.setEnabled(not locked)
        self.competence_combo.setEnabled(not locked)
        if self.scope_classes:
            self.activity_scope_combo.setEnabled(not locked)
            self.activity_scope_class_combo.setEnabled(not locked)

    def remove_detail(self):
        row = self.details_table.currentRow()
        if row >= 0:
            self.detail_autosave_timer.stop()
            removed_detail = self.details.pop(row)
            self.details_table.removeRow(row)
            if self.editing_detail_index == row:
                self.editing_detail_index = None
                self.save_detail_button.setText("إضافة المكوّن والمحتوى")
                _set_action_type(self.save_detail_button, "add")
            elif (
                self.editing_detail_index is not None
                and self.editing_detail_index > row
            ):
                self.editing_detail_index -= 1
            self.update_locked_fields()
            self.load_objective_suggestions()
            if self.details:
                self.persist_item()
            elif self.item_id is not None:
                try:
                    self.db.delete_teaching_plan_item(self.item_id)
                except sqlite3.IntegrityError:
                    QMessageBox.warning(
                        self, "تعذر حذف النشاط",
                        "هذا النشاط مستخدم في حصة مخططة؛ احذف ارتباطه بالحصة أولاً.",
                    )
                    self.details.insert(row, removed_detail)
                    self.refresh_details_table()
                    return
                self.item_id = None
                self.load_items()

    def load_items(self):
        rows = self.db.teaching_plan_items(self.plan[0])
        self._refresh_activity_suggestions()
        self.populate_mode_activity_combo()
        self.refresh_integration_table()
        self.refresh_evaluation_table()
        self.refresh_support_table()
        if (
            self.mode_combo.currentText() == "إدماج"
            and not self.editing_integration_activity
        ):
            self.load_integration_contents()
        if (
            self.mode_combo.currentText() == "تقييم"
            and not self.editing_evaluation_activity
        ):
            self.load_evaluation_contents()
        self.items_table.setRowCount(len(rows))
        for row_index, (item, details) in enumerate(rows):
            values = (
                item[1], item[3], len(details),
                "، ".join(detail[4] for detail in details),
                (
                    "مشترك"
                    if item[5] is None
                    else f"خاص بـ {item[6] or 'القسم المحدد'}"
                ),
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column == 0:
                    cell.setData(Qt.UserRole, item[0])
                self.items_table.setItem(row_index, column, cell)
        self.items_table.clearSelection()
        self.populate_mode_objectives_tree()

    def load_selected_item(self):
        row = self.items_table.currentRow()
        if row < 0:
            return
        item_id = self.items_table.item(row, 0).data(Qt.UserRole)
        record = self.db.teaching_plan_item(item_id)
        if record is None:
            return
        item, details = record
        self.item_id = item_id
        self.loading_form = True
        self.set_activity_scope(item[5])
        self._refresh_activity_suggestions(item[2])
        self.activity_combo.setCurrentText(item[2])
        self.load_competence_suggestions(item[2])
        self.competence_combo.setCurrentText(item[4])
        self.load_component_suggestions()
        self.details = [
            (
                detail[1], detail[2], detail[3], detail[4],
                detail[5], detail[6],
            )
            for detail in details
        ]
        self.refresh_details_table()
        self.load_objective_suggestions()
        self.update_locked_fields()
        self.loading_form = False

    def refresh_details_table(self):
        self.details_table.setRowCount(len(self.details))
        for row, detail in enumerate(self.details):
            for column, text in enumerate((detail[1], detail[3], detail[5])):
                self.details_table.setItem(
                    row, column, QTableWidgetItem(text)
                )

    def persist_item(self):
        activity_name = self.activity_combo.currentText().strip()
        try:
            self.item_id = self.db.save_teaching_plan_item(
                self.plan[0],
                activity_name,
                self._selected_record_id(self.competence_combo),
                self.competence_combo.currentText(),
                self.details,
                self.item_id,
                self.selected_scope_class_id(),
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "تعذر الحفظ التلقائي للنشاط", str(exc))
            return False
        self.load_items()
        if self.db.teaching_plan_number(self.plan[0]) is None:
            try:
                number = self.db.next_teaching_plan_number(
                    self.plan[1], self.plan[2], self.plan[0],
                    start_date=self.plan[5],
                )
                self.db.save_teaching_plan_number(
                    self.plan[0], number
                )
            except (ValueError, sqlite3.Error) as exc:
                QMessageBox.warning(
                    self, "تعذر تعيين عدد المخطط تلقائياً", str(exc)
                )
                return False
        return True

    def done(self, result):
        self.detail_autosave_timer.stop()
        self.mode_settings_autosave_timer.stop()
        self.integration_autosave_timer.stop()
        self.evaluation_autosave_timer.stop()
        self.support_autosave_timer.stop()
        self.autosave_detail()
        self.autosave_integration_draft()
        self.autosave_evaluation_draft()
        self.autosave_support_activity()
        if self.mode_combo.currentText() not in ("إدماج", "تقييم"):
            self.autosave_mode_settings()
        super().done(result)

    def clear_form(self):
        self.detail_autosave_timer.stop()
        self.item_id = None
        self.loading_form = True
        self._refresh_activity_suggestions("")
        self.activity_combo.setCurrentText("")
        self.competence_combo.setCurrentText("")
        self.component_combo.setCurrentText("")
        self.objective_combo.setCurrentText("")
        self.content_combo.setCurrentText("")
        self.details.clear()
        self.editing_detail_index = None
        self.refresh_details_table()
        self.update_locked_fields()
        self.loading_form = False
        self.save_detail_button.setText("إضافة المكوّن والمحتوى")
        _set_action_type(self.save_detail_button, "add")
        self.load_competence_suggestions("")

    def _refresh_activity_suggestions(self, selected_text=None):
        if selected_text is None:
            selected_text = self.activity_combo.currentText()
        selected_item_id = self.item_id
        plan_items = self.db.teaching_plan_items(self.plan[0])
        existing_names = {
            str(row[0][1] or "").strip().casefold()
            for row in plan_items
            if (
                row[0][0] != selected_item_id
                and row[0][5] == self.selected_scope_class_id()
            )
        }
        activity_names = list(self.activity_names)
        known_names = {name.strip().casefold() for name in activity_names}
        for item, _details in plan_items:
            name = str(item[1] or "").strip()
            if name and name.casefold() not in known_names:
                activity_names.append(name)
                known_names.add(name.casefold())
        self.activity_combo.blockSignals(True)
        self.activity_combo.clear()
        self.activity_combo.addItems([
            name for name in activity_names
            if name.strip().casefold() not in existing_names
        ])
        self.activity_combo.setCurrentText(selected_text)
        self.activity_combo.blockSignals(False)

    def delete_selected_item(self):
        row = self.items_table.currentRow()
        if row < 0:
            return
        item_id = self.items_table.item(row, 0).data(Qt.UserRole)
        try:
            self.db.delete_teaching_plan_item(item_id)
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self, "تعذر حذف النشاط",
                "هذا النشاط مستخدم في حصة مخططة؛ احذف ارتباطه بالحصة أولاً.",
            )
            return
        if self.item_id == item_id:
            self.clear_form()
        self.load_items()


class PlanActivityDialog(QDialog):
    FIELDS = (
        ("content", "المحتوى"),
        ("lesson_goal", "هدف الحصة"),
        ("specific_objective", "الأهداف المميزة"),
        ("target_competencies", "الكفايات المستهدفة"),
        ("evaluation", "التقييم"),
        ("expected_performance", "الأداء المنتظر"),
        ("target_criteria", "المعايير المستهدفة"),
        ("demographic_concept", "المفهوم السكاني"),
        ("health_concept", "المفهوم الصحي"),
        ("life_skills", "المهارات الحياتية"),
    )
    FIELD_INDEX = {
        key: index + 6 for index, (key, _label) in enumerate(FIELDS)
    }
    MODES = ("منهجي", "إدماج", "تقييم", "دعم وعلاج")

    def __init__(self, db, plan, parent=None, activity=None):
        super().__init__(parent)
        self.db = db
        self.plan = plan
        self.activity = activity
        self.mode_configuration = db.teaching_plan_mode_settings(plan[0])
        self.setWindowTitle("حصة مخططة")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(850, 760)

        layout = QVBoxLayout(self)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form = QFormLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(self.MODES)
        self.mode_combo.currentTextChanged.connect(self.update_mode_form)
        form.addRow("نمط التعلم:", self.mode_combo)
        self.lesson_number = QSpinBox()
        self.lesson_number.setRange(1, max(1, plan[6]))
        form.addRow("رقم الحصة:", self.lesson_number)
        self.competence_list = QListWidget()
        self.competence_items = {}
        self.plan_items = db.teaching_plan_items(plan[0])
        self.plan_item_details = {
            plan_item[0]: details for plan_item, details in self.plan_items
        }
        for plan_item, _details in self.plan_items:
            scope_label = (
                f" ({plan_item[6] or 'قسم مخصص'})"
                if plan_item[5] is not None else ""
            )
            item = QListWidgetItem(
                f"{plan_item[1]} — {plan_item[3]}{scope_label}"
            )
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, plan_item[0])
            self.competence_list.addItem(item)
            self.competence_items[plan_item[0]] = item
        self.competence_list.setMaximumHeight(125)
        form.addRow("النشاط / كفاية المادة:", self.competence_list)
        self.objective_tree = QTreeWidget()
        self.objective_tree.setHeaderHidden(True)
        self.objective_tree.setMaximumHeight(230)
        self.objective_tree.itemChanged.connect(self.update_objective_activity_state)
        self.objective_tree_mode = None
        form.addRow("الأنشطة والكفايات المستهدفة:", self.objective_tree)
        body_layout.addLayout(form)
        activity_selection = QHBoxLayout()
        select_all = QPushButton("تحديد كل الأنشطة")
        _set_action_type(select_all, "add")
        select_all.clicked.connect(self.select_all_competences)
        clear_selection = QPushButton("إلغاء التحديد")
        _set_action_type(clear_selection, "clear")
        clear_selection.clicked.connect(self.clear_competences)
        self.select_all_button = select_all
        self.clear_selection_button = clear_selection
        activity_selection.addWidget(select_all)
        activity_selection.addWidget(clear_selection)
        activity_selection.addStretch()
        body_layout.addLayout(activity_selection)

        self.text_fields = {}
        self.text_field_labels = {}
        for key, label in self.FIELDS:
            editor = QPlainTextEdit()
            editor.setMaximumHeight(58)
            if key == "expected_performance":
                editor.setFixedHeight(editor.fontMetrics().lineSpacing() * 4 + 20)
                editor.setLayoutDirection(Qt.RightToLeft)
            self.text_fields[key] = editor
            field_label = QLabel(label + ":")
            self.text_field_labels[key] = field_label
            body_layout.addWidget(field_label)
            body_layout.addWidget(editor)
        self.objective_tree_label = form.labelForField(self.objective_tree)
        self.competence_list_label = form.labelForField(self.competence_list)

        self.properties_group = QGroupBox(
            "خاصيات إضافية يحددها المستخدم"
        )
        properties_group = self.properties_group
        properties_layout = QVBoxLayout(properties_group)
        self.properties_table = QTableWidget(0, 2)
        self.properties_table.setHorizontalHeaderLabels(["اسم الخاصية", "القيمة"])
        self.properties_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.properties_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        properties_layout.addWidget(self.properties_table)
        property_buttons = QHBoxLayout()
        add_property = QPushButton("إضافة خاصية")
        _set_action_type(add_property, "add")
        add_property.clicked.connect(self.add_property_row)
        remove_property = QPushButton("حذف الخاصية المحددة")
        _set_action_type(remove_property, "delete")
        remove_property.clicked.connect(self.remove_property_row)
        property_buttons.addWidget(add_property)
        property_buttons.addWidget(remove_property)
        property_buttons.addStretch()
        properties_layout.addLayout(property_buttons)
        body_layout.addWidget(properties_group)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        save_button = QPushButton("حفظ")
        _set_action_type(save_button, "save")
        _style_primary_button(save_button)
        save_button.clicked.connect(self.save)
        cancel_button = QPushButton("إلغاء")
        _set_action_type(cancel_button, "cancel")
        cancel_button.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

        self.update_mode_form()
        if activity:
            row, properties = activity
            self.lesson_number.setValue(row[1])
            self.mode_combo.setCurrentText(row[2])
            item = self.competence_items.get(row[3])
            if item:
                item.setCheckState(Qt.Checked)
            for competence_id, other_item in self.competence_items.items():
                if competence_id != row[3]:
                    other_item.setFlags(
                        other_item.flags() & ~Qt.ItemIsEnabled
                    )
            self.select_all_button.setEnabled(False)
            self.clear_selection_button.setEnabled(False)
            if row[2] in ("إدماج", "تقييم"):
                self.restore_objective_selection(row[3], row[9])
            for index, (key, _label) in enumerate(self.FIELDS, start=6):
                self.text_fields[key].setPlainText(row[index] or "")
            for _property_id, key, value in properties:
                self.add_property_row(key, value)
        else:
            self.mode_combo.setCurrentText(
                self.mode_configuration["default_mode"]
            )
            mode_settings = self.mode_configuration["modes"].get(
                self.mode_combo.currentText(), {}
            )
            self.text_fields["expected_performance"].setPlainText(
                mode_settings.get("expected_performance", "")
            )
            self.apply_mode_activity_defaults()
            if not self.competence_items:
                QMessageBox.information(
                    self, "لم يتم إعداد أنشطة المخطط",
                    "أعدّ أنشطة المخطط وكفاياتها ومكوناتها ومحتوياتها أولاً.",
                )

    def update_mode_form(self, *_args):
        mode = self.mode_combo.currentText()
        uses_objective_tree = mode in ("إدماج", "تقييم")
        self.competence_list.setVisible(not uses_objective_tree)
        self.objective_tree.setVisible(uses_objective_tree)
        self.objective_tree_label.setVisible(uses_objective_tree)
        self.competence_list_label.setText(
            "الأنشطة:" if mode == "دعم وعلاج"
            else "النشاط / كفاية المادة:"
        )
        if mode == "إدماج":
            self.objective_tree_label.setText("الأنشطة / الكفايات المستهدفة:")
        elif mode == "تقييم":
            self.objective_tree_label.setText("الأنشطة / الكفايات:")
        self.select_all_button.setVisible(True)
        self.clear_selection_button.setVisible(True)
        self.select_all_button.setText(
            "تحديد كل الأهداف" if uses_objective_tree else "تحديد كل الأنشطة"
        )
        if uses_objective_tree:
            self.populate_objective_tree()
        else:
            self.apply_mode_activity_defaults()

        visible_fields = {
            "منهجي": set(self.text_fields),
            "إدماج": set(),
            "تقييم": {"expected_performance"},
            "دعم وعلاج": set(),
        }.get(mode, set())
        for key, editor in self.text_fields.items():
            self.text_field_labels[key].setVisible(key in visible_fields)
            editor.setVisible(key in visible_fields)
        self.properties_group.setVisible(mode == "منهجي")

    def apply_mode_activity_defaults(self):
        if self.activity:
            return
        mode_settings = self.mode_configuration["modes"].get(
            self.mode_combo.currentText(), {}
        )
        selected_items = mode_settings.get("selected_items", set())
        for plan_item_id, item in self.competence_items.items():
            item.setCheckState(
                Qt.Checked if plan_item_id in selected_items else Qt.Unchecked
            )
    def populate_objective_tree(self):
        mode = self.mode_combo.currentText()
        selected_by_activity = {}
        for index in range(self.objective_tree.topLevelItemCount()):
            activity_node = self.objective_tree.topLevelItem(index)
            selected_by_activity[activity_node.data(0, Qt.UserRole)] = {
                activity_node.child(child_index).text(0)
                for child_index in range(activity_node.childCount())
                if activity_node.child(child_index).checkState(0)
                == Qt.Checked
            }
        self.objective_tree.blockSignals(True)
        self.objective_tree.clear()
        all_objectives = []
        for plan_item, details in self.plan_items:
            for detail in details:
                objective = str(detail[3] or "").strip()
                if objective and objective not in all_objectives:
                    all_objectives.append(objective)
        for plan_item, details in self.plan_items:
            objectives = []
            for detail in details:
                objective = str(detail[3] or "").strip()
                if objective and objective not in objectives:
                    objectives.append(objective)
            if not objectives:
                objectives = list(all_objectives)
            scope_label = (
                f" ({plan_item[6] or 'قسم مخصص'})"
                if plan_item[5] is not None else ""
            )
            activity_node = QTreeWidgetItem([
                f"{plan_item[1]} — {plan_item[3]}{scope_label}"
            ])
            activity_node.setData(0, Qt.UserRole, plan_item[0])
            activity_node.setFlags(
                activity_node.flags()
                | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
            )
            if (
                self.activity
                and plan_item[0] != self.activity[0][3]
            ):
                activity_node.setFlags(
                    activity_node.flags() & ~Qt.ItemIsEnabled
                )
            activity_node.setCheckState(0, Qt.Unchecked)
            for objective in objectives:
                objective_node = QTreeWidgetItem([objective])
                objective_node.setData(0, Qt.UserRole, objective)
                objective_node.setFlags(
                    objective_node.flags()
                    | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                )
                if (
                    self.activity
                    and plan_item[0] != self.activity[0][3]
                ):
                    objective_node.setFlags(
                        objective_node.flags() & ~Qt.ItemIsEnabled
                    )
                objective_node.setCheckState(0, Qt.Unchecked)
                activity_node.addChild(objective_node)
            self.objective_tree.addTopLevelItem(activity_node)
        self.objective_tree.blockSignals(False)
        if self.objective_tree_mode == mode and selected_by_activity:
            for index in range(self.objective_tree.topLevelItemCount()):
                activity_node = self.objective_tree.topLevelItem(index)
                selected = selected_by_activity.get(
                    activity_node.data(0, Qt.UserRole), set()
                )
                for child_index in range(activity_node.childCount()):
                    objective_node = activity_node.child(child_index)
                    if objective_node.text(0) in selected:
                        objective_node.setCheckState(0, Qt.Checked)
        elif self.activity:
            row = self.activity[0]
            self.restore_objective_selection(row[3], row[9])
        else:
            selected_by_activity = (
                self.mode_configuration["modes"].get(
                    self.mode_combo.currentText(), {}
                ).get("selected_objectives", {})
            )
            for index in range(self.objective_tree.topLevelItemCount()):
                activity_node = self.objective_tree.topLevelItem(index)
                selected = selected_by_activity.get(
                    activity_node.data(0, Qt.UserRole), set()
                )
                for child_index in range(activity_node.childCount()):
                    objective_node = activity_node.child(child_index)
                    if objective_node.text(0) in selected:
                        objective_node.setCheckState(0, Qt.Checked)
        self.objective_tree_mode = mode

    def update_objective_activity_state(self, item, column):
        if item.parent() is not None:
            parent = item.parent()
            selected_count = sum(
                parent.child(index).checkState(0) == Qt.Checked
                for index in range(parent.childCount())
            )
            if selected_count == 0:
                state = Qt.Unchecked
            elif selected_count == parent.childCount():
                state = Qt.Checked
            else:
                state = Qt.PartiallyChecked
            parent.setCheckState(
                0, state
            )
        elif item.checkState(0) != Qt.PartiallyChecked:
            state = item.checkState(0)
            for index in range(item.childCount()):
                item.child(index).setCheckState(0, state)

    def restore_objective_selection(self, plan_item_id, target_competencies):
        selected = {
            text.strip()
            for text in str(target_competencies or "").splitlines()
            if text.strip()
        }
        for index in range(self.objective_tree.topLevelItemCount()):
            activity_node = self.objective_tree.topLevelItem(index)
            matches_activity = activity_node.data(0, Qt.UserRole) == plan_item_id
            for child_index in range(activity_node.childCount()):
                objective_node = activity_node.child(child_index)
                checked = matches_activity and (
                    objective_node.text(0) in selected
                )
                objective_node.setCheckState(
                    0, Qt.Checked if checked else Qt.Unchecked
                )
            if matches_activity and activity_node.childCount() == 0:
                activity_node.setCheckState(0, Qt.Checked)

    @staticmethod
    def _set_combo_data(combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def select_all_competences(self):
        if self.mode_combo.currentText() in ("إدماج", "تقييم"):
            for index in range(self.objective_tree.topLevelItemCount()):
                self.objective_tree.topLevelItem(index).setCheckState(
                    0, Qt.Checked
                )
        else:
            for item in self.competence_items.values():
                item.setCheckState(Qt.Checked)

    def clear_competences(self):
        for index in range(self.objective_tree.topLevelItemCount()):
            self.objective_tree.topLevelItem(index).setCheckState(
                0, Qt.Unchecked
            )
        for item in self.competence_items.values():
            item.setCheckState(Qt.Unchecked)

    def add_property_row(self, key="", value=""):
        row = self.properties_table.rowCount()
        self.properties_table.insertRow(row)
        self.properties_table.setItem(row, 0, QTableWidgetItem(key))
        value_item = QTableWidgetItem(value)
        value_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.properties_table.setItem(row, 1, value_item)

    def remove_property_row(self):
        row = self.properties_table.currentRow()
        if row >= 0:
            self.properties_table.removeRow(row)

    def save(self):
        mode = self.mode_combo.currentText()
        if mode in ("إدماج", "تقييم"):
            selected_activities = []
            for index in range(self.objective_tree.topLevelItemCount()):
                activity_node = self.objective_tree.topLevelItem(index)
                selected_objectives = [
                    activity_node.child(child_index).text(0)
                    for child_index in range(activity_node.childCount())
                    if activity_node.child(child_index).checkState(0)
                    == Qt.Checked
                ]
                if selected_objectives:
                    selected_activities.append((
                        int(activity_node.data(0, Qt.UserRole)),
                        selected_objectives,
                    ))
            activity_values = []
            for plan_item_id, objectives in selected_activities:
                values = dict.fromkeys(self.text_fields, "")
                if mode == "تقييم":
                    values["expected_performance"] = (
                        self.text_fields["expected_performance"].toPlainText()
                    )
                values["target_competencies"] = "\n".join(objectives)
                activity_values.append((plan_item_id, values))
        elif mode == "دعم وعلاج":
            competence_ids = [
                int(self.competence_list.item(row).data(Qt.UserRole))
                for row in range(self.competence_list.count())
                if self.competence_list.item(row).checkState() == Qt.Checked
            ]
            activity_values = [
                (plan_item_id, dict.fromkeys(self.text_fields, ""))
                for plan_item_id in competence_ids
            ]
        else:
            competence_ids = [
                int(self.competence_list.item(row).data(Qt.UserRole))
                for row in range(self.competence_list.count())
                if self.competence_list.item(row).checkState() == Qt.Checked
            ]
            activity_values = [
                (plan_item_id, {
                    key: editor.toPlainText()
                    for key, editor in self.text_fields.items()
                })
                for plan_item_id in competence_ids
            ]
        if not activity_values:
            QMessageBox.warning(self, "تعذر الحفظ", "لا توجد كفاية مادة مرتبطة بالمخطط.")
            return
        if mode == "تقييم" and any(
            not values["expected_performance"].strip()
            for _plan_item_id, values in activity_values
        ):
            QMessageBox.warning(
                self, "بيانات ناقصة", "أدخل الأداء المنتظر للتقييم."
            )
            return
        if mode == "إدماج" and any(
            not values["target_competencies"].strip()
            for _plan_item_id, values in activity_values
        ):
            QMessageBox.warning(
                self, "بيانات ناقصة",
                "اختر هدفاً مميزاً واحداً على الأقل لكل نشاط محدد.",
            )
            return
        properties = []
        for row in range(self.properties_table.rowCount()):
            key_item = self.properties_table.item(row, 0)
            value_item = self.properties_table.item(row, 1)
            properties.append((
                key_item.text() if key_item else "",
                value_item.text() if value_item else "",
            ))
        try:
            for plan_item_id, values in activity_values:
                self.db.save_teaching_plan_activity(
                    self.plan[0],
                    self.lesson_number.value(),
                    mode,
                    plan_item_id,
                    values,
                    properties,
                    self.activity[0][0] if self.activity else None,
                    self.activity[0][16] if self.activity else self.plan[9],
                )
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "تعذر حفظ الحصة", str(exc))
            return
        self.accept()


class OfficialHolidayCalendar(QCalendarWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._blocked_dates = set()
        self._last_allowed_date = self.selectedDate()
        self.selectionChanged.connect(self.restore_allowed_selection)
        self.currentPageChanged.connect(self.refresh_holiday_formats)
        self.refresh_holiday_formats()

    def set_blocked_dates(self, blocked_dates):
        self._blocked_dates = set(blocked_dates)
        selected = self.selectedDate()
        if selected in self._blocked_dates:
            available = next(
                (
                    selected.addDays(offset)
                    for offset in range(1, 367)
                    if selected.addDays(offset) not in self._blocked_dates
                ),
                QDate.currentDate(),
            )
            self.blockSignals(True)
            self.setSelectedDate(available)
            self.blockSignals(False)
            self._last_allowed_date = available
        else:
            self._last_allowed_date = selected
        self.refresh_holiday_formats()

    def restore_allowed_selection(self):
        selected = self.selectedDate()
        if selected in self._blocked_dates:
            self.blockSignals(True)
            self.setSelectedDate(self._last_allowed_date)
            self.blockSignals(False)
            return
        self._last_allowed_date = selected

    def refresh_holiday_formats(self, *_args):
        self.setDateTextFormat(QDate(), QTextCharFormat())
        month_start = QDate(self.yearShown(), self.monthShown(), 1)
        month_end = month_start.addMonths(1).addDays(-1)
        holiday_format = QTextCharFormat()
        holiday_format.setBackground(QColor("#FDE68A"))
        holiday_format.setForeground(QColor("#92400E"))
        blocked_format = QTextCharFormat()
        blocked_format.setBackground(QColor("#E2E8F0"))
        blocked_format.setForeground(QColor("#94A3B8"))
        for (
            _holiday_id, start_text, end_text, name, holiday_type
        ) in self.db.holidays():
            start = max(
                QDate.fromString(start_text, "yyyy-MM-dd"), month_start
            )
            end = min(
                QDate.fromString(end_text, "yyyy-MM-dd"), month_end
            )
            if start > end:
                continue
            holiday_label = (
                "عطلة رسمية"
                if holiday_type == "official"
                else "غياب طارئ غير رسمي"
            )
            holiday_format.setToolTip(
                f"{name or holiday_label} — {holiday_label}"
            )
            date_value = start
            while date_value <= end:
                self.setDateTextFormat(
                    date_value,
                    blocked_format
                    if date_value in self._blocked_dates
                    else holiday_format,
                )
                date_value = date_value.addDays(1)


class OfficialHolidayMonthCard(QFrame):
    dateSelected = Signal(object)
    WEEKDAY_LABELS = ("اثـ", "ثلا", "أر", "خ", "ج", "سـ", "أح")

    def __init__(self, year, month, holidays, parent=None):
        super().__init__(parent)
        self.setObjectName("officialHolidayMonthCard")
        accent, background = PDF_ACTIVITY_CARD_COLORS[
            (month - 1) % len(PDF_ACTIVITY_CARD_COLORS)
        ]
        self.setStyleSheet(
            "QFrame#officialHolidayMonthCard {"
            f"background-color: {background}; border: 1px solid {accent}; "
            f"border-top: 3px solid {accent}; border-radius: 9px;"
            "}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 9)
        layout.setSpacing(5)

        month_names = (
            "جانفي", "فيفري", "مارس", "أفريل", "ماي", "جوان",
            "جويلية", "أوت", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
        )
        title = QLabel(f"{month_names[month - 1]} {year}")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "font-size: 11pt; font-weight: bold; color: #1E3A8A; "
            "border: none; background: transparent;"
        )
        layout.addWidget(title)

        days_grid = QGridLayout()
        days_grid.setContentsMargins(0, 0, 0, 0)
        days_grid.setHorizontalSpacing(2)
        days_grid.setVerticalSpacing(2)
        for column, weekday in enumerate(self.WEEKDAY_LABELS):
            label = QLabel(weekday)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(
                "font-size: 9pt; font-weight: bold; color: #475569; "
                "border: none; background: transparent;"
            )
            days_grid.addWidget(label, 0, column)

        first_day = QDate(year, month, 1)
        for day in range(1, first_day.daysInMonth() + 1):
            current_date = QDate(year, month, day)
            holiday_name = holidays.get(
                current_date.toString("yyyy-MM-dd")
            )
            label = QPushButton(str(day))
            label.setCursor(Qt.PointingHandCursor)
            label.setMinimumHeight(24)
            label.setMaximumHeight(28)
            label.clicked.connect(
                lambda _checked=False, selected_date=current_date:
                self.dateSelected.emit(selected_date)
            )
            if holiday_name is not None:
                label.setToolTip(
                    f"{holiday_name}\n"
                    f"{current_date.toString('dd/MM/yyyy')}"
                )
                label.setStyleSheet(
                    "QPushButton {font-weight: bold; color: #9D174D; "
                    "background-color: #FCE7F3; border: 1px solid #F9A8D4; "
                    "border-radius: 5px; padding: 0; min-height: 24px;}"
                    "QPushButton:hover {background-color: #FBCFE8;}"
                )
            else:
                label.setStyleSheet(
                    "QPushButton {color: #334155; background-color: #FFFFFF; "
                    "border: 1px solid #DBEAFE; border-radius: 5px; "
                    "padding: 0; min-height: 24px;}"
                    "QPushButton:hover {background-color: #DBEAFE;}"
                )
            day_offset = first_day.dayOfWeek() - 1 + day - 1
            days_grid.addWidget(
                label, day_offset // 7 + 1, day_offset % 7
            )
        layout.addLayout(days_grid)


class OfficialHolidaysPage(QWidget):
    def __init__(self, db, parent=None, holiday_type="official"):
        super().__init__(parent)
        self.db = db
        self.holiday_type = holiday_type
        self.is_unofficial = holiday_type == "unofficial"
        self.holiday_label = (
            "غياب طارئ غير رسمي" if self.is_unofficial else "عطلة رسمية"
        )
        self.setLayoutDirection(Qt.RightToLeft)
        self.editing_holiday_id = None
        self.month_cards = []
        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        months_panel = QWidget()
        months_panel_layout = QVBoxLayout(months_panel)
        months_panel_layout.setContentsMargins(0, 0, 0, 0)
        months_panel_layout.setSpacing(6)
        months_header = QHBoxLayout()
        self.school_year_label = QLabel()
        self.school_year_label.setStyleSheet(
            "font-size: 11pt; font-weight: bold; color: #1E3A8A;"
        )
        months_header.addWidget(self.school_year_label)
        months_header.addStretch()
        holiday_legend = QLabel(
            "أيام الغياب الطارئ مميّزة"
            if self.is_unofficial else "أيام العطل مميّزة"
        )
        holiday_legend.setStyleSheet(
            "font-weight: bold; color: #9D174D; "
            "background-color: #FCE7F3; border: 1px solid #F9A8D4; "
            "border-radius: 5px; padding: 3px 7px;"
        )
        months_header.addWidget(holiday_legend)
        months_panel_layout.addLayout(months_header)

        self.months_scroll = QScrollArea()
        self.months_scroll.setWidgetResizable(True)
        self.months_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.months_container = QWidget()
        self.months_container.setLayoutDirection(Qt.RightToLeft)
        self.months_grid = QGridLayout(self.months_container)
        self.months_grid.setContentsMargins(4, 4, 8, 4)
        self.months_grid.setSpacing(8)
        self.months_scroll.setWidget(self.months_container)
        months_panel_layout.addWidget(self.months_scroll, 1)
        body.addWidget(months_panel, 4)

        management_layout = QVBoxLayout()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "المعرّف", "من تاريخ", "إلى تاريخ", "العطلة", "النوع",
        ])
        self.table.setColumnHidden(0, True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setMinimumHeight(220)
        management_layout.addWidget(self.table, 2)

        form = QFormLayout()
        form.setVerticalSpacing(4)
        self.start_date_edit = QDateEdit(QDate.currentDate())
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_calendar = OfficialHolidayCalendar(
            self.db, self.start_date_edit
        )
        self.start_date_edit.setCalendarWidget(self.start_calendar)
        self.end_date_edit = QDateEdit(QDate.currentDate())
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_calendar = OfficialHolidayCalendar(
            self.db, self.end_date_edit
        )
        self.end_date_edit.setCalendarWidget(self.end_calendar)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(
            "سبب الغياب: لقاء تكويني، عطلة مرضية، عطلة استثنائية"
            if self.is_unofficial else "اسم العطلة"
        )
        form.addRow("من تاريخ:", self.start_date_edit)
        form.addRow("إلى تاريخ:", self.end_date_edit)
        form.addRow("اسم العطلة:", self.name_edit)
        management_layout.addLayout(form)
        buttons = QHBoxLayout()
        for text, callback, action_type in (
            ("إضافة عطلة", self.add_holiday, "add"),
            ("تعديل المحددة", self.edit_holiday, "edit"),
            ("حذف المحددة", self.delete_holiday, "delete"),
        ):
            button = QPushButton(text)
            _set_action_type(button, action_type)
            if text == "إضافة عطلة":
                _style_primary_button(button)
            button.clicked.connect(callback)
            buttons.addWidget(button)
            if text == "إضافة عطلة":
                self.add_button = button
            elif text == "تعديل المحددة":
                self.edit_button = button
            elif text == "حذف المحددة":
                self.delete_button = button
        management_layout.addLayout(buttons)
        management_panel = QWidget()
        management_panel.setLayoutDirection(Qt.RightToLeft)
        management_panel.setLayout(management_layout)
        management_panel.setMinimumWidth(580)
        management_panel.setMaximumWidth(640)
        body.addWidget(management_panel, 1)
        layout.addLayout(body, 1)
        self.table.itemSelectionChanged.connect(self.fill_form)
        self.start_date_edit.dateChanged.connect(self.update_actions)
        self.end_date_edit.dateChanged.connect(self.update_actions)
        self.load()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.layout_month_cards)

    def layout_month_cards(self):
        if not hasattr(self, "months_grid"):
            return
        while self.months_grid.count():
            self.months_grid.takeAt(0)
        available_width = self.months_scroll.viewport().width()
        column_count = max(1, min(5, (available_width + 8) // 228))
        for column in range(5):
            self.months_grid.setColumnStretch(column, 0)
        for index, card in enumerate(self.month_cards):
            row, column = divmod(index, column_count)
            self.months_grid.addWidget(card, row, column)
        for column in range(column_count):
            self.months_grid.setColumnStretch(column, 1)

    def refresh_month_cards(self, holiday_rows):
        year_name = self.db.school_year_name_for_date()
        try:
            first_year = int(year_name.split("-", 1)[0])
        except (AttributeError, ValueError) as error:
            raise ValueError(
                f"اسم السنة الدراسية غير صالح: {year_name}"
            ) from error
        holidays = {}
        for (
            _holiday_id, start_text, end_text, name, holiday_type
        ) in holiday_rows:
            holiday_label = (
                "عطلة رسمية"
                if holiday_type == "official"
                else "غياب طارئ غير رسمي"
            )
            current_date = QDate.fromString(start_text, "yyyy-MM-dd")
            end_date = QDate.fromString(end_text, "yyyy-MM-dd")
            while current_date.isValid() and current_date <= end_date:
                key = current_date.toString("yyyy-MM-dd")
                holiday_names = holidays.setdefault(key, [])
                display_name = name or holiday_label
                if display_name not in holiday_names:
                    holiday_names.append(display_name)
                current_date = current_date.addDays(1)
        holidays = {
            day: "\n".join(names) for day, names in holidays.items()
        }
        for card in self.month_cards:
            card.deleteLater()
        self.month_cards = []
        for month_offset in range(10):
            month_number = 9 + month_offset
            year = first_year
            if month_number > 12:
                month_number -= 12
                year += 1
            self.month_cards.append(
                OfficialHolidayMonthCard(
                    year, month_number, holidays, self.months_container
                )
            )
            self.month_cards[-1].dateSelected.connect(
                self.select_month_date
            )
        self.school_year_label.setText(f"السنة الدراسية {year_name}")
        self.layout_month_cards()

    def blocked_dates(self):
        return set()

    def refresh_date_calendars(self):
        blocked_dates = self.blocked_dates()
        self.start_calendar.set_blocked_dates(blocked_dates)
        self.end_calendar.set_blocked_dates(blocked_dates)

    def load(self):
        rows = self.db.holidays(self.holiday_type)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                display_value = (
                    "رسمية" if value == "official"
                    else "غير رسمية" if value == "unofficial"
                    else value
                )
                self.table.setItem(
                    row_index, column, QTableWidgetItem(str(display_value))
                )
        self.refresh_month_cards(rows)
        self.refresh_date_calendars()
        self.update_actions()

    def fill_form(self):
        row = self.table.currentRow()
        if row < 0:
            self.editing_holiday_id = None
            self.refresh_date_calendars()
            self.update_actions()
            return
        self.editing_holiday_id = int(self.table.item(row, 0).text())
        date_item = self.table.item(row, 1)
        end_date_item = self.table.item(row, 2)
        name_item = self.table.item(row, 3)
        if date_item:
            self.start_date_edit.setDate(
                QDate.fromString(date_item.text(), "yyyy-MM-dd")
            )
        if end_date_item:
            self.end_date_edit.setDate(
                QDate.fromString(end_date_item.text(), "yyyy-MM-dd")
            )
        self.name_edit.setText(name_item.text() if name_item else "")
        self.refresh_date_calendars()
        self.update_actions()

    def update_actions(self):
        has_selection = self.table.currentRow() >= 0
        start = self.start_date_edit.date()
        end = self.end_date_edit.date()
        add_available = start <= end
        self.add_button.setEnabled(add_available)
        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

    def select_month_date(self, date_value):
        self.editing_holiday_id = None
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.start_date_edit.setDate(date_value)
        self.end_date_edit.setDate(date_value)
        self.name_edit.clear()
        self.refresh_date_calendars()
        self.update_actions()

    def add_holiday(self):
        self.editing_holiday_id = None
        self._save_holiday()

    def edit_holiday(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(
                self, "اختيار مطلوب", "يرجى تحديد عطلة لتعديلها."
            )
            return
        self._save_holiday(int(self.table.item(row, 0).text()))

    def _save_holiday(self, holiday_id=None):
        start = self.start_date_edit.date()
        end = self.end_date_edit.date()
        if start > end:
            QMessageBox.warning(
                self,
                "فترة غير صالحة",
                "يجب أن يكون تاريخ البداية في اليوم نفسه أو قبل تاريخ النهاية.",
            )
            return
        try:
            self.db.save_official_holiday(
                start.toString("yyyy-MM-dd"),
                end.toString("yyyy-MM-dd"),
                self.name_edit.text(),
                holiday_id,
                self.holiday_type,
            )
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "تعذر حفظ العطلة", str(exc))
            return
        self.load()
        self.table.clearSelection()
        self.name_edit.clear()
        self.editing_holiday_id = None
        self.refresh_date_calendars()

    def delete_holiday(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(
                self, "اختيار مطلوب", "يرجى تحديد عطلة لحذفها."
            )
            return
        answer = QMessageBox.question(
            self,
            "تأكيد الحذف",
            "هل تريد حذف العطلة المحددة؟",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.db.delete_official_holiday(
                int(self.table.item(row, 0).text())
            )
        except sqlite3.Error as exc:
            QMessageBox.warning(self, "تعذر حذف العطلة", str(exc))
            return
        self.load()


class PlanActivityCard(QFrame):
    def __init__(self, activity, color, duration_minutes, parent=None):
        super().__init__(parent)
        self.activity = activity
        self.drag_start_position = None
        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedSize(100, 100)
        self.setCursor(Qt.OpenHandCursor)
        activity_label = _plan_item_display_name(
            activity["activity_name"],
            activity.get("scope_class_name"),
        )
        tooltip_lines = [f"النشاط: {activity_label}"]
        if activity.get("learning_mode") == "إدماج":
            tooltip_lines.append(
                "الكفايات المستهدفة: "
                f"{activity['target_competencies']}"
            )
        elif activity.get("learning_mode") != "تقييم":
            tooltip_lines.append(f"المحتوى: {activity['content']}")
        tooltip_lines.extend((
            f"التوقيت: {duration_minutes} دقيقة",
            "اسحب البطاقة إلى الحصة المطلوبة لنسخها.",
        ))
        self.setToolTip("\n".join(tooltip_lines))
        self.setStyleSheet(
            "QFrame {"
            f"background-color: {color};"
            "border: 1px solid #94A3B8; border-radius: 10px;"
            "}"
        )
        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(4, 4, 4, 4)
        card_layout.setSpacing(2)
        name = QLabel(activity_label)
        name.setWordWrap(True)
        name.setAlignment(Qt.AlignCenter)
        name.setStyleSheet(
            "font-weight: bold; font-size: 8pt; color: #0F172A; border: none; "
            "background: transparent;"
        )
        card_layout.addWidget(name)
        if activity.get("learning_mode") not in ("تقييم", "دعم وعلاج"):
            detail_text = (
                activity["target_competencies"]
                if activity.get("learning_mode") == "إدماج"
                else activity["content"]
            )
            detail = QLabel(detail_text)
            detail.setWordWrap(True)
            detail.setAlignment(Qt.AlignCenter)
            detail.setStyleSheet(
                "font-size: 7pt; color: #334155; border: none; "
                "background: transparent;"
            )
            card_layout.addWidget(detail, 1)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start_position = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self.drag_start_position is None
            or not event.buttons() & Qt.LeftButton
            or (event.position().toPoint() - self.drag_start_position).manhattanLength()
            < QApplication.startDragDistance()
        ):
            return
        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setText(json.dumps(self.activity, ensure_ascii=False))
        drag.setMimeData(mime_data)
        drag.exec(Qt.CopyAction)


class PlanScheduledActivityButton(QPushButton):
    def __init__(self, activity_id, learning_mode, text, parent=None):
        super().__init__(text, parent)
        self.activity_id = activity_id
        self.learning_mode = learning_mode
        self.drag_start_position = None
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("اسحب النشاط إلى حصة لنسخها إليها.")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start_position = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self.drag_start_position is None
            or not event.buttons() & Qt.LeftButton
            or (
                event.position().toPoint() - self.drag_start_position
            ).manhattanLength() < QApplication.startDragDistance()
        ):
            return
        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setText(json.dumps({
            "activity_id": self.activity_id,
            "learning_mode": self.learning_mode,
        }))
        drag.setMimeData(mime_data)
        drag.exec(Qt.CopyAction)


class PlanLessonDropRow(QFrame):
    def __init__(
        self, mode, lesson_number, on_drop, parent=None,
        display_number=None,
    ):
        super().__init__(parent)
        self.mode = mode
        self.lesson_number = lesson_number
        self.display_number = display_number or lesson_number
        self.on_drop = on_drop
        self.setAcceptDrops(True)
        self.setMinimumHeight(124)
        self.setFrameShape(QFrame.StyledPanel)
        if mode == "منهجي":
            row_background = "#EFF6FF"
            row_border = "2px solid #2563EB"
        else:
            row_background = "#F8FAFC"
            row_border = "1px dashed #94A3B8"
        self.setStyleSheet(
            "PlanLessonDropRow {"
            f"background: {row_background}; "
            f"border: {row_border}; border-radius: 8px;"
            "}"
        )
        self.row_layout = QHBoxLayout(self)
        self.row_layout.setContentsMargins(10, 10, 10, 10)
        label = QLabel(str(self.display_number))
        label.setMinimumWidth(36)
        label.setStyleSheet(
            "font-weight: bold; color: #1E293B; border: none;"
        )
        self.row_layout.addWidget(label)
        self.activities_layout = QHBoxLayout()
        self.activities_layout.setAlignment(Qt.AlignCenter)
        self.row_layout.addLayout(self.activities_layout, 1)
        self.drop_hint = QLabel("أفلت النشاط هنا لإضافته إلى الحصة")
        self.drop_hint.setAlignment(Qt.AlignCenter)
        self.drop_hint.setStyleSheet("color: #64748B; border: none;")
        self.row_layout.addWidget(self.drop_hint)
        self.row_layout.addStretch()

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        try:
            activity = json.loads(event.mimeData().text())
        except (TypeError, ValueError):
            event.ignore()
            return
        if (
            not isinstance(activity, dict)
            or not {"item_id", "activity_id"}.intersection(activity)
        ):
            event.ignore()
            return
        self.on_drop(self, activity)
        event.acceptProposedAction()


class PlanDistributionEntryDialog(QDialog):
    MODE_FIELDS = {
        "منهجي": (
            ("content", "المحتوى"),
            ("lesson_goal", "هدف الحصة"),
            ("specific_objective", "الهدف المميز"),
        ),
        "إدماج": (("target_competencies", "الكفايات المستهدفة"),),
        "تقييم": (("expected_performance", "الأداء المنتظر"),),
        "دعم وعلاج": (("target_criteria", "المعايير المستهدفة"),),
    }

    def __init__(
        self, mode, activity, lesson_number, default_duration,
        existing=None, parent=None, property_suggestions=(),
    ):
        super().__init__(parent)
        self.mode = mode
        self.activity = activity
        self.existing = existing
        self.setWindowTitle(f"إضافة نشاط إلى الحصة {mode} {lesson_number}")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(520, 600)

        layout = QVBoxLayout(self)
        title_text = _plan_item_display_name(
            activity["activity_name"],
            activity.get("scope_class_name"),
        )
        if mode not in ("إدماج", "تقييم") and activity["content"]:
            title_text += f" — {activity['content']}"
        title = QLabel(title_text)
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        form = QFormLayout()
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 300)
        self.duration_spin.setSuffix(" دقيقة")
        self.duration_spin.setValue(
            existing[0][16] if existing else default_duration
        )
        form.addRow("الزمن المخصص للنشاط:", self.duration_spin)
        body_layout.addLayout(form)

        self.fields = {}
        existing_row = existing[0] if existing else None
        existing_properties = existing[1] if existing else ()
        self.property_suggestions = {
            key: values for key, values in property_suggestions
        }
        field_property_keys = {
            "content": ("المحتوى",),
            "lesson_goal": ("هدف الحصة",),
            "specific_objective": ("الهدف المميز", "الأهداف المميزة"),
            "target_competencies": ("الكفايات المستهدفة",),
            "evaluation": ("التقييم",),
            "expected_performance": ("الأداء المنتظر",),
            "target_criteria": ("المعايير المستهدفة",),
            "demographic_concept": ("المفهوم السكاني",),
            "health_concept": ("مفهوم صحي أو سكاني",),
            "life_skills": ("المهارة الحياتية", "المهارات الحياتية"),
        }
        for key, label in self.MODE_FIELDS[mode]:
            editor = QPlainTextEdit()
            editor.setLayoutDirection(Qt.RightToLeft)
            text_option = editor.document().defaultTextOption()
            text_option.setAlignment(Qt.AlignRight)
            editor.document().setDefaultTextOption(text_option)
            editor.setMinimumHeight(82)
            if key == "content":
                editor.setPlainText(
                    existing_row[6] if existing_row else activity["content"]
                )
            elif existing_row:
                value = str(
                    existing_row[PlanActivityDialog.FIELD_INDEX[key]] or ""
                )
                if (
                    not value and mode == "إدماج"
                    and key == "target_competencies"
                ):
                    value = str(
                        existing_row[
                            PlanActivityDialog.FIELD_INDEX["target_criteria"]
                        ] or ""
                    )
                if not value and key == "target_competencies":
                    value = next(
                        (
                            str(property_value or "")
                            for _property_id, property_key, property_value
                            in existing_properties
                            if property_key == label
                        ),
                        "",
                    )
                if (
                    not value and mode == "تقييم"
                    and key == "expected_performance"
                ):
                    value = str(
                        activity.get("expected_performance", "") or ""
                    )
                if (
                    not value and mode == "دعم وعلاج"
                    and key == "target_criteria"
                ):
                    value = str(activity.get("target_criteria", "") or "")
                editor.setPlainText(value)
            else:
                editor.setPlainText(activity.get(key, ""))
                historical_values = next(
                    (
                        self.property_suggestions[property_key]
                        for property_key in field_property_keys.get(key, ())
                        if self.property_suggestions.get(property_key)
                    ),
                    [],
                )
                if historical_values:
                    editor.setPlaceholderText(
                        "قيم سابقة لهذا النشاط: "
                        + "، ".join(historical_values)
                    )
            self.fields[key] = editor
            form.addRow(f"{label}:", editor)

        self.custom_properties_group = QGroupBox("خاصيات إضافية")
        custom_layout = QVBoxLayout(self.custom_properties_group)
        self.custom_rows_layout = QVBoxLayout()
        custom_layout.addLayout(self.custom_rows_layout)
        self.custom_rows = []
        field_keys = {
            label for _key, label in self.MODE_FIELDS[mode]
        }
        self.property_key_suggestions = list(dict.fromkeys(
            key for key in (
                *DETAIL_KEYS_BY_NAMAT.get(mode, ()),
                *self.property_suggestions.keys(),
            )
            if key not in field_keys
        ))
        add_property = QPushButton("إضافة خاصية")
        _set_action_type(add_property, "add")
        add_property.clicked.connect(
            lambda _checked=False: self.add_custom_property_row()
        )
        custom_layout.addWidget(add_property)
        body_layout.addWidget(self.custom_properties_group)
        loaded_keys = set()
        for _property_id, key, value in existing_properties:
            if key in field_keys:
                continue
            self.add_custom_property_row(key, value)
            loaded_keys.add(key)
        for key in self.property_key_suggestions:
            if key not in loaded_keys:
                self.add_custom_property_row(key)
                loaded_keys.add(key)
        self.add_custom_property_row()
        body_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        save_button = QPushButton("إضافة إلى الحصة")
        _set_action_type(save_button, "save")
        _style_primary_button(save_button)
        save_button.clicked.connect(self.accept)
        cancel_button = QPushButton("إلغاء")
        _set_action_type(cancel_button, "cancel")
        cancel_button.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)
    def add_custom_property_row(self, key="", value=""):
        row = QHBoxLayout()
        key_edit = QLineEdit(key)
        key_edit.setPlaceholderText("اسم الخاصية (من دفتر إعداد الدروس)")
        key_edit.setCompleter(QCompleter(self.property_key_suggestions, key_edit))
        value_edit = QLineEdit(value)
        value_edit.setPlaceholderText("القيمة")
        value_edit.setCompleter(QCompleter(
            self.property_suggestions.get(key, []), value_edit
        ))
        for editor in (key_edit, value_edit):
            editor.setLayoutDirection(Qt.RightToLeft)
            editor.setAlignment(Qt.AlignRight)
        row.addWidget(key_edit)
        row.addWidget(value_edit)
        self.custom_rows_layout.addLayout(row)
        self.custom_rows.append((key_edit, value_edit))
        key_edit.textChanged.connect(
            lambda text, editor=value_edit: editor.setCompleter(
                QCompleter(self.property_suggestions.get(text, []), editor)
            )
        )
        key_edit.textChanged.connect(self.ensure_trailing_property_row)
        value_edit.textChanged.connect(self.ensure_trailing_property_row)

    def ensure_trailing_property_row(self, *_args):
        if not self.custom_rows:
            self.add_custom_property_row()
            return
        key_edit, value_edit = self.custom_rows[-1]
        if key_edit.text().strip() or value_edit.text().strip():
            self.add_custom_property_row()

    def values(self):
        fields = {
            key: editor.toPlainText().strip()
            for key, editor in self.fields.items()
        }
        fields.setdefault("content", self.activity["content"])
        fields.setdefault("lesson_goal", "")
        fields.setdefault("specific_objective", "")
        fields.setdefault("target_competencies", "")
        fields.setdefault("evaluation", "")
        fields.setdefault("expected_performance", "")
        fields.setdefault("target_criteria", "")
        fields.setdefault("demographic_concept", "")
        fields.setdefault("health_concept", "")
        fields.setdefault("life_skills", "")
        properties = [
            (key.text().strip(), value.text().strip())
            for key, value in self.custom_rows
            if key.text().strip()
        ]
        return fields, properties, self.duration_spin.value()


class PlanDistributionPage(QWidget):
    def __init__(self, db, plan, on_back, parent=None):
        super().__init__(parent)
        self.db = db
        self.plan = plan
        self.on_back = on_back
        self.setWindowTitle(f"توزيع أنشطة المخطط: {plan[1]}")
        self.setLayoutDirection(Qt.RightToLeft)
        self.mode_rows = {}
        self.activity_card_widgets = []
        self.activity_card_columns = None

        layout = QVBoxLayout(self)
        self.title_label = QLabel(f"توزيع أنشطة المخطط على الحصص: {plan[1]}")
        self.title_label.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #1E40AF;"
        )
        layout.addWidget(self.title_label)

        self.mode_tabs = QTabWidget()
        self.mode_tabs.setObjectName("planDistributionModeTabs")
        self.mode_widgets = {}
        for mode in PlanActivityDialog.MODES:
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            activities_group = QGroupBox(
                f"أنشطة نمط {mode}"
            )
            activities_group.installEventFilter(self)
            activities_layout = QVBoxLayout(activities_group)
            activities_grid = QGridLayout()
            activities_grid.setAlignment(
                Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignTop
            )
            activities_layout.addLayout(activities_grid)
            activities_scroll = QScrollArea()
            activities_scroll.setWidgetResizable(True)
            activities_scroll.setMinimumHeight(180)
            activities_scroll.setMaximumHeight(260)
            activities_scroll.setWidget(activities_group)
            tab_layout.addWidget(activities_scroll)

            lessons_scroll = QScrollArea()
            lessons_scroll.setWidgetResizable(True)
            lesson_rows_container = QWidget()
            lesson_rows_layout = QGridLayout(lesson_rows_container)
            lesson_rows_layout.setHorizontalSpacing(8)
            lesson_rows_layout.setVerticalSpacing(8)
            for column in range(3):
                lesson_rows_layout.setColumnStretch(column, 1)
            lessons_scroll.setWidget(lesson_rows_container)
            tab_layout.addWidget(lessons_scroll, 1)

            self.mode_tabs.addTab(tab, mode)
            self.mode_widgets[mode] = {
                "activities_group": activities_group,
                "activities_grid": activities_grid,
                "activity_card_widgets": [],
                "activity_card_columns": None,
                "lesson_rows_layout": lesson_rows_layout,
                "mode_rows": {},
            }
        layout.addWidget(self.mode_tabs, 1)
        self.mode_tabs.currentChanged.connect(self.load_mode)

        actions = QHBoxLayout()
        close_button = QPushButton("العودة إلى المخططات")
        _set_action_type(close_button, "back")
        close_button.clicked.connect(self.on_back)
        actions.addStretch()
        actions.addWidget(close_button)
        layout.addLayout(actions)

        self.load_mode("منهجي")

    def eventFilter(self, watched, event):
        if (
            any(
                watched is widgets["activities_group"]
                for widgets in self.mode_widgets.values()
            )
            and event.type() == QEvent.Resize
        ):
            QTimer.singleShot(
                0,
                lambda group=watched: self.layout_activity_cards(
                    self._mode_for_activity_group(group)
                ),
            )
        return super().eventFilter(watched, event)

    def _mode_for_activity_group(self, group):
        return next(
            (
                mode for mode, widgets in self.mode_widgets.items()
                if widgets["activities_group"] is group
            ),
            None,
        )

    def layout_activity_cards(self, mode=None):
        mode = mode or self.mode_tabs.tabText(
            self.mode_tabs.currentIndex()
        )
        widgets = self.mode_widgets[mode]
        activity_card_widgets = widgets["activity_card_widgets"]
        activities_group = widgets["activities_group"]
        activities_grid = widgets["activities_grid"]
        if not activity_card_widgets:
            return
        grid_width = (
            activities_group.contentsRect().width()
            - activities_grid.contentsMargins().left()
            - activities_grid.contentsMargins().right()
        )
        spacing = activities_grid.horizontalSpacing()
        if spacing < 0:
            spacing = 6
        card_width = activity_card_widgets[0].width()
        columns = max(1, (grid_width + spacing) // (card_width + spacing))
        if columns == widgets["activity_card_columns"]:
            return
        while activities_grid.count():
            activities_grid.takeAt(0)
        widgets["activity_card_columns"] = columns
        for index, card in enumerate(activity_card_widgets):
            activities_grid.addWidget(
                card, index // columns, index % columns
            )

    @staticmethod
    def activity_colors(item_id):
        seed = (
            int(item_id)
            if isinstance(item_id, int)
            else sum(
                (index + 1) * ord(char)
                for index, char in enumerate(str(item_id))
            )
        )
        hue = (seed * 137) % 360
        return (
            QColor.fromHsv(hue, 48, 248).name(),
            QColor.fromHsv(hue, 180, 190).name(),
        )

    def display_lesson_number(self, mode, mode_lesson_number):
        mode_index = PlanActivityDialog.MODES.index(mode)
        previous_lesson_count = sum(
            int(self.plan[10 + index] or 0)
            for index in range(mode_index)
        )
        return previous_lesson_count + mode_lesson_number

    def activity_cards_for_mode(self, mode):
        plan_items = self.db.teaching_plan_items(self.plan[0])
        items_by_name = {
            self.db._normalized_plan_activity(item[1]): (item, details)
            for item, details in plan_items
        }
        cards = []
        if mode == "منهجي":
            configured = [
                (
                    item[0],
                    item[1],
                    [str(detail[4] or "").strip() for detail in details],
                    {},
                )
                for item, details in plan_items
            ]
        elif mode in ("إدماج", "تقييم"):
            configured = []
        else:
            configured = [
                (None, row[0], [], {"target_criteria": row[1]})
                for row in self.db.teaching_plan_support_activities(
                    self.plan[0]
                )
            ]

        mode_settings = self.db.teaching_plan_mode_settings(
            self.plan[0]
        )["modes"].get(mode, {})
        if mode == "إدماج":
            for record in self.db.teaching_plan_integration_activities(
                self.plan[0]
            ):
                activity_name, _prompt, _template, target_text, _contents = record
                item_data = items_by_name.get(
                    self.db._normalized_plan_activity(activity_name)
                )
                item, _details = item_data if item_data else (None, ())
                cards.append({
                    "item_id": item[0] if item else None,
                    "activity_name": activity_name,
                    "learning_mode": mode,
                    "content": "",
                    "competence_text": item[3] if item else "",
                    "target_competencies": target_text,
                    "target_criteria": target_text,
                    "expected_performance": mode_settings.get(
                        "expected_performance", ""
                    ),
                })
            return cards
        if mode == "تقييم":
            for record in self.db.teaching_plan_evaluation_activities(
                self.plan[0]
            ):
                activity_name, _prompt, _template, target_text, _contents = record
                item_data = items_by_name.get(
                    self.db._normalized_plan_activity(activity_name)
                )
                item, _details = item_data if item_data else (None, ())
                cards.append({
                    "item_id": item[0] if item else None,
                    "activity_name": activity_name,
                    "learning_mode": mode,
                    "content": "",
                    "competence_text": item[3] if item else "",
                    "target_competencies": "",
                    "expected_performance": (
                        target_text
                        or mode_settings.get("expected_performance", "")
                    ),
                })
            return cards
        if mode == "دعم وعلاج":
            for activity_name, target_criteria in (
                self.db.teaching_plan_support_activities(self.plan[0])
            ):
                item_data = items_by_name.get(
                    self.db._normalized_plan_activity(activity_name)
                )
                item, _details = item_data if item_data else (None, ())
                cards.append({
                    "item_id": item[0] if item else None,
                    "activity_name": activity_name,
                    "learning_mode": mode,
                    "content": "",
                    "competence_text": item[3] if item else "",
                    "target_criteria": target_criteria,
                })
            return cards

        for item_id, activity_name, selected_contents, default_fields in configured:
            item_data = next(
                (
                    (item, details)
                    for item, details in plan_items
                    if item[0] == item_id
                ),
                None,
            ) if item_id is not None else items_by_name.get(
                self.db._normalized_plan_activity(activity_name)
            )
            if not item_data:
                continue
            item, details = item_data
            objectives_by_content = {
                str(detail[4] or "").strip().casefold():
                str(detail[3] or "").strip()
                for detail in details
            }
            detail_contents = [
                str(detail[4] or "").strip()
                for detail in details if str(detail[4] or "").strip()
            ]
            contents = [
                str(content or "").strip()
                for content in selected_contents if str(content or "").strip()
            ] or detail_contents or [""]
            for content in dict.fromkeys(contents):
                cards.append({
                    "item_id": item[0],
                    "activity_name": item[1],
                    "scope_class_name": (
                        item[6] if item[5] is not None else ""
                    ),
                    "learning_mode": mode,
                    "content": content,
                    "competence_text": item[3],
                    **default_fields,
                    "specific_objective": objectives_by_content.get(
                        content.casefold(),
                        default_fields.get("specific_objective", ""),
                    ),
                    "expected_performance": mode_settings.get(
                        "expected_performance", ""
                    ),
                })
        return cards

    def load_mode(self, mode=None, *_args):
        if not isinstance(mode, str):
            mode = self.mode_tabs.tabText(
                self.mode_tabs.currentIndex()
            )
        if mode not in self.mode_widgets:
            return
        mode_index = PlanActivityDialog.MODES.index(mode)
        if mode_index != self.mode_tabs.currentIndex():
            self.mode_tabs.setCurrentIndex(mode_index)
            return
        widgets = self.mode_widgets[mode]
        self.activities_group = widgets["activities_group"]
        self.activities_grid = widgets["activities_grid"]
        self.activity_card_widgets = widgets["activity_card_widgets"]
        self.activity_card_columns = widgets["activity_card_columns"]
        self.lesson_rows_layout = widgets["lesson_rows_layout"]
        self.mode_rows = widgets["mode_rows"]
        self.clear_layout(self.activities_grid)
        self.activity_card_widgets = []
        widgets["activity_card_widgets"] = self.activity_card_widgets
        self.activity_card_columns = None
        widgets["activity_card_columns"] = None
        self.clear_layout(self.lesson_rows_layout)
        self.mode_rows.clear()
        cards = self.activity_cards_for_mode(mode)
        self.saved_activities = self.db.teaching_plan_activities(
            self.plan[0]
        )
        for activity in cards:
            color, border = self.activity_colors(
                activity["item_id"] or activity["activity_name"]
            )
            card = PlanActivityCard(
                activity, color, self.plan[9] or 60, self
            )
            card.setStyleSheet(
                f"QFrame {{background-color: {color}; "
                f"border: 2px solid {border}; border-radius: 10px;}}"
            )
            self.activity_card_widgets.append(card)
        if not cards:
            empty = QLabel("لا توجد أنشطة أو محتويات مسجلة لهذا النمط.")
            empty.setStyleSheet("color: #64748B;")
            self.activities_grid.addWidget(empty, 0, 0)
        else:
            self.layout_activity_cards(mode)

        mode_index = PlanActivityDialog.MODES.index(mode)
        lesson_count = int(self.plan[10 + mode_index] or 0)
        if lesson_count == 0:
            self.lesson_rows_layout.addWidget(
                QLabel("لا توجد حصص محددة لهذا النمط في إعدادات المخطط."),
                0, 0, 1, 3,
            )
        for lesson_number in range(1, lesson_count + 1):
            row = PlanLessonDropRow(
                mode,
                lesson_number,
                self.drop_activity,
                self,
                display_number=self.display_lesson_number(
                    mode, lesson_number
                ),
            )
            self.mode_rows[(mode, lesson_number)] = row
            row_index, column_index = divmod(lesson_number - 1, 3)
            self.lesson_rows_layout.addWidget(
                row, row_index, column_index
            )
            self.populate_lesson_row(row)

    def load(self):
        self.title_label.setText(
            f"توزيع أنشطة المخطط على الحصص: {self.plan[1]}"
        )
        self.load_mode(
            self.mode_tabs.tabText(self.mode_tabs.currentIndex())
        )

    @staticmethod
    def clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            nested = item.layout()
            if nested is not None:
                PlanDistributionPage.clear_layout(nested)

    def populate_lesson_row(self, row):
        for activity, _properties in self.saved_activities:
            if activity[1] != row.lesson_number or activity[2] != row.mode:
                continue
            card_data = {
                "item_id": activity[3],
                "activity_name": activity[4],
                "scope_class_name": activity[17],
                "learning_mode": activity[2],
                "content": activity[6],
                "competence_text": activity[5],
                "specific_objective": activity[8],
                "target_competencies": activity[9],
            }
            color, border = self.activity_colors(
                activity[3] or activity[4]
            )
            activity_label = _plan_item_display_name(
                activity[4], activity[17]
            )
            button = PlanScheduledActivityButton(
                activity[0],
                activity[2],
                f"{activity_label}\n{activity[6]}\n{activity[16]} دقيقة"
            )
            button.setFixedSize(125, 100)
            button.setToolTip(
                f"النشاط: {activity_label}\n"
                f"المحتوى: {activity[6]}\n"
                f"التوقيت: {activity[16]} دقيقة\n"
                "اسحب النشاط إلى حصة أخرى لنسخه إليها."
            )
            button.setStyleSheet(
                f"QPushButton {{background-color: {color}; "
                f"border: 1px solid {border}; border-radius: 8px; "
                "padding: 3px; font-size: 7pt; text-align: center;}"
            )
            button.clicked.connect(
                lambda _checked=False, row=row, card=card_data, activity=activity:
                self.open_entry_dialog(row, card, activity)
            )
            entry_widget = QWidget()
            entry_widget.setFixedSize(125, 100)
            entry_layout = QGridLayout(entry_widget)
            entry_layout.setContentsMargins(0, 0, 0, 0)
            entry_layout.setSpacing(0)
            entry_layout.addWidget(button, 0, 0)
            delete_button = QToolButton()
            delete_button.setText("×")
            delete_button.setToolTip("حذف النشاط من الحصة")
            delete_button.setFixedSize(20, 20)
            _set_action_type(delete_button, "delete")
            delete_button.setStyleSheet(
                "QToolButton {font-size: 14pt; font-weight: bold; "
                "padding: 0; border-radius: 7px;}"
            )
            delete_button.clicked.connect(
                lambda _checked=False, activity_id=activity[0]:
                self.delete_activity(activity_id)
            )
            entry_layout.addWidget(
                delete_button, 0, 0, Qt.AlignLeft | Qt.AlignTop
            )
            row.activities_layout.addWidget(entry_widget)
        row.drop_hint.setVisible(row.activities_layout.count() == 0)

    def open_entry_dialog(self, row, activity_card, activity_row=None):
        default_duration = self.plan[9] or 60
        existing = None
        if activity_row is not None:
            existing = next(
                item for item in self.db.teaching_plan_activities(self.plan[0])
                if item[0][0] == activity_row[0]
            )
        property_suggestions = self.db.teaching_diary_activity_properties(
            activity_card["activity_name"], row.mode
        )
        dialog = PlanDistributionEntryDialog(
            row.mode,
            activity_card,
            row.display_number,
            default_duration,
            existing,
            self,
            property_suggestions=property_suggestions,
        )
        if not dialog.exec():
            return
        fields, properties, duration = dialog.values()
        try:
            self.db.save_teaching_plan_activity(
                self.plan[0],
                row.lesson_number,
                row.mode,
                activity_card["item_id"],
                fields,
                properties,
                activity_row[0] if activity_row else None,
                duration,
                activity_name=activity_card["activity_name"],
            )
        except (ValueError, TypeError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "تعذر حفظ توزيع النشاط", str(exc))
            return
        self.load_mode(row.mode)

    def drop_activity(self, row, activity):
        if "activity_id" in activity:
            self.copy_scheduled_activity(row, activity)
            return
        if activity not in self.activity_cards_for_mode(row.mode):
            QMessageBox.warning(
                self, "نشاط غير متاح",
                "لا يمكن توزيع هذا النشاط ضمن نمط التعلم المحدد.",
            )
            return
        self.open_entry_dialog(row, activity)

    def copy_scheduled_activity(self, target_row, payload):
        if payload.get("learning_mode") != target_row.mode:
            QMessageBox.warning(
                self, "نمط تعلم مختلف",
                "يمكن نسخ النشاط إلى حصص النمط المحدد فقط.",
            )
            return
        activity_id = payload.get("activity_id")
        activity = next(
            (
                saved for saved in self.saved_activities
                if saved[0][0] == activity_id
            ),
            None,
        )
        if activity is None:
            QMessageBox.warning(
                self, "تعذر نسخ النشاط",
                "لم يعد هذا التوزيع موجوداً في المخطط.",
            )
            return
        row, properties = activity
        fields = {
            key: str(row[index] or "")
            for key, index in PlanActivityDialog.FIELD_INDEX.items()
        }
        try:
            self.db.save_teaching_plan_activity(
                self.plan[0],
                target_row.lesson_number,
                target_row.mode,
                row[3],
                fields,
                [(key, value) for _property_id, key, value in properties],
                duration_minutes=row[16],
                activity_name=row[4],
            )
        except (ValueError, TypeError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "تعذر نسخ النشاط", str(exc))
            return
        self.load_mode(target_row.mode)

    def delete_activity(self, activity_id):
        answer = QMessageBox.question(
            self, "تأكيد الحذف", "هل تريد حذف هذا النشاط من الحصة؟"
        )
        if answer == QMessageBox.Yes:
            self.db.delete_teaching_plan_activity(activity_id)
            self.load_mode(
                self.mode_tabs.tabText(self.mode_tabs.currentIndex())
            )


class TeachingPlanCard(QFrame):
    selected = Signal(int)
    double_clicked = Signal(int)
    action_requested = Signal(str, int)

    def __init__(self, plan, weekday_counts, items=(), parent=None):
        super().__init__(parent)
        self.plan_id = int(plan[0])
        self._selected = False
        self.setObjectName("teachingPlanCard")
        self.setMinimumWidth(360)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QFrame#teachingPlanCard {
                background: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 14px;
            }
            QFrame#teachingPlanCard[selected="true"] {
                background: #F8FBFF;
                border: 2px solid #2563EB;
            }
        """)
        self.setProperty("selected", "false")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        self.action_buttons = {}
        for object_name, tooltip, action, standard_icon in (
            (
                "teachingPlanCardEditButton",
                "تعديل المخطط",
                "edit",
                QStyle.StandardPixmap.SP_FileDialogDetailedView,
            ),
            (
                "teachingPlanCardDeleteButton",
                "حذف المخطط",
                "delete",
                QStyle.StandardPixmap.SP_TrashIcon,
            ),
            (
                "teachingPlanCardConfigureButton",
                "إعداد أنشطة المخطط",
                "configure",
                QStyle.StandardPixmap.SP_FileDialogContentsView,
            ),
            (
                "teachingPlanCardDistributeButton",
                "توزيع أنشطة المخطط على الحصص",
                "distribute",
                QStyle.StandardPixmap.SP_ArrowRight,
            ),
            (
                "teachingPlanCardExportJsonButton",
                "تصدير المخطط بصيغة JSON",
                "export_json",
                QStyle.StandardPixmap.SP_DialogSaveButton,
            ),
            (
                "teachingPlanCardExportPdfButton",
                "تصدير المخطط بصيغة PDF",
                "export_pdf",
                QStyle.StandardPixmap.SP_FileIcon,
            ),
            (
                "teachingPlanCardPrintButton",
                "طباعة المخطط",
                "print",
                QStyle.StandardPixmap.SP_DialogApplyButton,
            ),
        ):
            button = QToolButton(self)
            button.setObjectName(object_name)
            button.setIcon(self.style().standardIcon(standard_icon))
            button.setFixedSize(28, 28)
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.setStyleSheet(
                "QToolButton { background: transparent; border: none; "
                "padding: 3px; }"
                "QToolButton:hover { background: transparent; border: none; }"
            )
            button.clicked.connect(
                lambda _checked=False, key=action: (
                    self.action_requested.emit(key, self.plan_id)
                )
            )
            self.action_buttons[action] = button
            button.raise_()

        header = QHBoxLayout()
        title = QLabel(str(plan[1] or "").strip() or "مخطط دون اسم")
        title.setWordWrap(True)
        title.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #123B67; "
            "border: none; background: transparent;"
        )
        header.addWidget(title, 1)
        number_text = (
            f"عدد {plan[11]}" if plan[11] is not None else "دون رقم"
        )
        number = QLabel(number_text)
        number.setAlignment(Qt.AlignCenter)
        number.setStyleSheet(
            "font-weight: bold; color: #1D4ED8; background: #DBEAFE; "
            "border: none; border-radius: 10px; padding: 5px 9px;"
        )
        self.number_badge = number
        header.addWidget(number)
        layout.addLayout(header)

        shared_group_key = plan[19] if len(plan) > 19 else None
        class_label_text = (
            f"القسم: {shared_group_key} (مشترك؛ الكفاية: {plan[3]})"
            if shared_group_key else f"القسم: {plan[3]}"
        )
        class_label = QLabel(class_label_text)
        class_label.setStyleSheet(
            "font-weight: bold; color: #334155; border: none; "
            "background: transparent;"
        )
        layout.addWidget(class_label)

        info = QGridLayout()
        info.setHorizontalSpacing(10)
        info.setVerticalSpacing(5)
        self._add_info(info, 0, 0, "المجال", plan[5])
        self._add_info(info, 0, 1, "تاريخ الانطلاق", plan[8])
        self._add_info(info, 1, 0, "مدة الحصة", f"{plan[12]} دقيقة")
        self._add_info(info, 1, 1, "النهاية المتوقعة", plan[17])
        layout.addLayout(info)

        competence = QLabel(f"كفاية المجال: {plan[7] or ''}")
        competence.setWordWrap(True)
        competence.setStyleSheet(
            "color: #334155; border: none; border-radius: 8px; "
            "background: #F1F5F9; padding: 8px;"
        )
        layout.addWidget(competence)

        if items:
            items_heading = QLabel("الأنشطة وكفاياتها")
            items_heading.setStyleSheet(
                "font-weight: bold; color: #123B67; border: none; "
                "background: transparent;"
            )
            layout.addWidget(items_heading)
            for item, _details in items[:4]:
                item_name = _plan_item_display_name(
                    item[1], item[6] if item[5] is not None else ""
                )
                item_competence = str(item[3] or "").strip()
                item_label = QLabel(
                    f"{item_name} — {item_competence}"
                    if item_competence else item_name
                )
                item_label.setWordWrap(True)
                item_label.setStyleSheet(
                    "color: #334155; border: none; border-radius: 7px; "
                    "background: #F8FAFC; padding: 5px;"
                )
                layout.addWidget(item_label)
            if len(items) > 4:
                more_items = QLabel(f"و{len(items) - 4} أنشطة أخرى")
                more_items.setStyleSheet(
                    "font-size: 10px; color: #64748B; border: none; "
                    "background: transparent;"
                )
                layout.addWidget(more_items)

        days = {day: name for name, day in WEEKDAYS}
        schedule_text = "، ".join(
            f"{days.get(day, str(day))}: {count}"
            for day, count in sorted(weekday_counts.items())
        ) or "غير محددة"
        schedule = QLabel(f"الحصص الأسبوعية: {schedule_text}")
        schedule.setWordWrap(True)
        schedule.setStyleSheet(
            "color: #475569; border: none; background: transparent;"
        )
        layout.addWidget(schedule)

        mode_names = ("منهجي", "إدماج", "تقييم", "دعم وعلاج")
        mode_colors = {
            "منهجي": ("#1D4ED8", "#DBEAFE"),
            "إدماج": ("#059669", "#ECFDF5"),
            "تقييم": ("#D97706", "#FFFBEB"),
            "دعم وعلاج": ("#7C3AED", "#F5F3FF"),
        }
        modes = QHBoxLayout()
        modes.setSpacing(6)
        for index, mode in enumerate(mode_names, start=13):
            color, background = mode_colors[mode]
            label = QLabel(f"{mode}: {plan[index]}")
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(
                f"font-size: 11px; color: {color}; background: {background}; "
                "border: none; border-radius: 8px; padding: 5px;"
            )
            modes.addWidget(label, 1)
        layout.addLayout(modes)

        lesson_total = QLabel(f"إجمالي الحصص: {plan[9]}")
        lesson_total.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lesson_total.setStyleSheet(
            "font-weight: bold; color: #123B67; border: none; "
            "background: transparent;"
        )
        layout.addWidget(lesson_total)

        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        spacing = 2
        buttons = tuple(self.action_buttons.values())
        total_width = sum(button.width() for button in buttons)
        total_width += spacing * max(0, len(buttons) - 1)
        x = (self.width() - total_width) // 2
        for button in self.action_buttons.values():
            button.move(x, 8)
            button.raise_()
            x += button.width() + spacing

    @staticmethod
    def _add_info(layout, row, column, title, value):
        block = QWidget()
        block.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        block_layout = QVBoxLayout(block)
        block_layout.setContentsMargins(0, 0, 0, 0)
        block_layout.setSpacing(2)
        caption = QLabel(title)
        caption.setStyleSheet(
            "font-size: 10px; color: #64748B; border: none; "
            "background: transparent;"
        )
        text = QLabel(str(value or ""))
        text.setWordWrap(True)
        text.setStyleSheet(
            "font-weight: bold; color: #334155; border: none; "
            "background: transparent;"
        )
        block_layout.addWidget(caption)
        block_layout.addWidget(text)
        layout.addWidget(block, row, column)

    def set_selected(self, selected):
        self._selected = bool(selected)
        self.setProperty("selected", "true" if self._selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.plan_id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit(self.plan_id)
        super().mouseDoubleClickEvent(event)


class TeachingPlansPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._distribution_pages = {}
        self._plan_cards = {}
        self._selected_plan_id = None
        self.setAcceptDrops(True)
        self.setLayoutDirection(Qt.RightToLeft)
        layout = QVBoxLayout(self)
        guidance = QLabel(
            "النمط المنهجي توجيهي بنسبة تقريبية 75% من التعلمات، ولا يفرض التطبيق توزيعاً آلياً."
        )
        guidance.setStyleSheet("color: #475569;")
        layout.addWidget(guidance)

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("البحث في المخططات:"))
        self.plan_search = QLineEdit()
        self.plan_search.setPlaceholderText(
            "ابحث عن مخطط سابق بالاسم أو القسم أو المجال أو الكفاية..."
        )
        self.plan_search.setLayoutDirection(Qt.RightToLeft)
        self.plan_search.setAlignment(Qt.AlignRight)
        self.plan_search.setAcceptDrops(False)
        self.plan_search.textChanged.connect(self.load_plans)
        search_layout.addWidget(self.plan_search, 1)
        layout.addLayout(search_layout)

        plan_buttons = QHBoxLayout()
        for text, callback, action_type in (
            ("مخطط جديد", self.add_plan, "add"),
        ):
            button = QPushButton(text)
            _set_action_type(button, action_type)
            _style_primary_button(button)
            button.clicked.connect(callback)
            plan_buttons.addWidget(button)
        self.import_json_button = QPushButton("استيراد JSON")
        _set_action_type(self.import_json_button, "browse")
        self.import_json_button.clicked.connect(self.import_plan_file)
        plan_buttons.addWidget(self.import_json_button)
        plan_buttons.addStretch()
        layout.addLayout(plan_buttons)

        self.plans_scroll = QScrollArea()
        self.plans_scroll.setWidgetResizable(True)
        self.plans_scroll.setAcceptDrops(False)
        self.plans_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.plans_container = QWidget()
        self.plans_container.setLayoutDirection(Qt.RightToLeft)
        self.plans_grid = QGridLayout(self.plans_container)
        self.plans_grid.setContentsMargins(8, 8, 8, 8)
        self.plans_grid.setHorizontalSpacing(12)
        self.plans_grid.setVerticalSpacing(12)
        self.plans_scroll.setWidget(self.plans_container)
        layout.addWidget(self.plans_scroll, 1)
        self.load_plans()

    @staticmethod
    def _configure_table(table):
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

    @staticmethod
    def _is_json_file_drop(event):
        mime_data = event.mimeData()
        if not mime_data.hasUrls():
            return False
        urls = mime_data.urls()
        return (
            len(urls) == 1
            and urls[0].isLocalFile()
            and urls[0].toLocalFile().lower().endswith(".json")
        )

    def dragEnterEvent(self, event):
        if self._is_json_file_drop(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        if self._is_json_file_drop(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        if not self._is_json_file_drop(event):
            event.ignore()
            return
        event.acceptProposedAction()
        self.import_plan_file(
            event.mimeData().urls()[0].toLocalFile()
        )

    def export_selected_plan(self):
        plan_id = self.selected_plan()
        if plan_id is None:
            QMessageBox.information(
                self, "اختيار مطلوب", "حدد مخططاً لتصديره."
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "تصدير المخطط", "", "ملفات JSON (*.json)"
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        def export_snapshot():
            snapshot = self.db.export_teaching_plan(plan_id)
            with open(path, "w", encoding="utf-8") as file:
                json.dump(snapshot, file, ensure_ascii=False, indent=2)

        start_background_task(
            self,
            export_snapshot,
            lambda _result: QMessageBox.information(
                self, "اكتمل التصدير", "تم تصدير المخطط بنجاح."
            ),
            lambda error: QMessageBox.warning(
                self, "تعذر تصدير المخطط", error
            ),
        )

    def export_selected_plan_pdf(self):
        plan_id = self.selected_plan()
        if plan_id is None:
            QMessageBox.information(
                self, "اختيار مطلوب", "حدد مخططاً لتصديره."
            )
            return
        def export_pdf():
            snapshot = self.db.export_teaching_plan(plan_id)
            documents_path = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DocumentsLocation
            )
            if not documents_path:
                raise OSError("تعذر تحديد مجلد Documents الخاص بالنظام.")
            documents = Path(documents_path)
            documents.mkdir(parents=True, exist_ok=True)

            plan = snapshot["plan"]
            plan_name = _teaching_plan_name(snapshot)
            plan_number = (
                plan.get("plan_number")
                if plan.get("plan_number") is not None else "غير محدد"
            )
            filename = (
                f"مخطط {plan_name} - عدد {plan_number} "
                f"القسم - {_teaching_plan_class_label(plan)}"
            )
            filename = "".join(
                "_" if character in '<>:"/\\|?*' or ord(character) < 32
                else character
                for character in filename
            ).strip(" .")
            output_path = documents / f"{filename[:180] or 'مخطط'}.pdf"
            export_teaching_plan_pdf(snapshot, output_path)

        start_background_task(
            self,
            export_pdf,
            lambda _result: QMessageBox.information(
                self,
                "اكتمل التصدير",
                "تم تصدير المخطط إلى مجلد المستندات.",
            ),
            lambda error: QMessageBox.warning(
                self, "تعذر تصدير المخطط", error
            ),
        )

    def print_selected_plan(self):
        plan_id = self.selected_plan()
        if plan_id is None:
            QMessageBox.information(
                self, "اختيار مطلوب", "حدد مخططاً لطباعته."
            )
            return
        start_background_task(
            self,
            lambda: self.db.export_teaching_plan(plan_id),
            lambda snapshot: self._show_print_preview(snapshot),
            lambda error: QMessageBox.warning(
                self, "تعذرت طباعة المخطط", error
            ),
            keep_overlay_until_window=True,
        )

    def _show_print_preview(self, snapshot):
        try:
            print_teaching_plan(snapshot, self)
        except (OSError, ValueError, sqlite3.Error, RuntimeError) as error:
            QMessageBox.warning(self, "تعذرت طباعة المخطط", str(error))

    def import_plan_file(self, path=None):
        if not isinstance(path, str):
            path, _ = QFileDialog.getOpenFileName(
                self, "استيراد مخطط", "", "ملفات JSON (*.json)"
            )
        if not path:
            return
        if not path.lower().endswith(".json"):
            QMessageBox.warning(
                self, "ملف غير صالح", "اختر ملفاً بامتداد JSON."
            )
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as file:
                snapshot = json.load(file)
            if not isinstance(snapshot, dict):
                raise ValueError("يجب أن يحتوي ملف JSON على كائن مخطط.")
            if (
                snapshot.get("format") != "jrnl_teaching_plan"
                or snapshot.get("version") != 1
            ):
                raise ValueError(
                    "صيغة ملف JSON غير مدعومة للمخططات التعليمية."
                )
            plan_data = snapshot.get("plan")
            if not isinstance(plan_data, dict):
                raise ValueError("بيانات المخطط غير صالحة في ملف JSON.")
            dialog = TeachingPlanImportOptionsDialog(
                self.db,
                snapshot,
                _suggested_teaching_plan_number(self.db, snapshot),
                self,
            )
            if not dialog.exec():
                return
            school_year, plan_number, start_date, weekly_schedule = (
                dialog.values()
            )
            snapshot = dict(snapshot)
            plan_data = dict(plan_data)
            plan_data["school_year"] = school_year
            plan_data["plan_number"] = plan_number
            plan_data["start_date"] = start_date
            plan_data["weekdays"] = sorted(weekly_schedule)
            plan_data["lessons_per_day"] = {
                str(day): count for day, count in weekly_schedule.items()
            }
            snapshot["plan"] = plan_data
            duplicate_rows = [
                row for row in self.db.teaching_plans()
                if (
                    str(row[1]).strip().casefold()
                    == str(plan_data.get("title") or "").strip().casefold()
                    and str(row[3]).strip().casefold()
                    == str(plan_data.get("class_name") or "").strip().casefold()
                    and str(row[5]).strip().casefold()
                    == str(plan_data.get("domain_name") or "").strip().casefold()
                    and str(row[8]).strip()
                    == str(plan_data.get("start_date") or "").strip()
                )
            ]
            replace_plan_id = None
            if duplicate_rows:
                target = duplicate_rows[0]
                if len(duplicate_rows) > 1:
                    choices = [
                        f"{row[1]} — {row[3]} — {row[8]} (#{row[0]})"
                        for row in duplicate_rows
                    ]
                    selected, accepted = QInputDialog.getItem(
                        self, "اختيار المخطط المطابق",
                        "توجد عدة مخططات مطابقة؛ اختر المخطط المراد استبداله:",
                        choices, 0, False,
                    )
                    if not accepted:
                        return
                    target = duplicate_rows[choices.index(selected)]

                prompt = QMessageBox(self)
                prompt.setWindowTitle("مخطط مكرر")
                prompt.setText(
                    "يوجد مخطط بالقسم والمجال والاسم وتاريخ الانطلاق نفسها."
                )
                copy_button = prompt.addButton(
                    "استيراد كنسخة جديدة", QMessageBox.AcceptRole
                )
                replace_button = prompt.addButton(
                    "استبدال المخطط المطابق", QMessageBox.DestructiveRole
                )
                copy_button.setProperty("actionType", "add")
                replace_button.setProperty("actionType", "edit")
                cancel_button = prompt.addButton(
                    "إلغاء", QMessageBox.RejectRole
                )
                cancel_button.setProperty("actionType", "cancel")
                prompt.exec()
                if prompt.clickedButton() is replace_button:
                    replace_plan_id = target[0]
                elif prompt.clickedButton() is not copy_button:
                    return

            imported_id = self.db.import_teaching_plan(
                snapshot, replace_plan_id=replace_plan_id
            )
            if replace_plan_id is not None:
                self._distribution_pages.pop(replace_plan_id, None)
            self.load_plans(include_plan_id=imported_id)
        except (
            OSError, json.JSONDecodeError, TypeError, ValueError, sqlite3.Error
        ) as error:
            QMessageBox.warning(
                self, "تعذر استيراد المخطط", str(error)
            )
            return
        message = "تم استيراد المخطط بنجاح."
        actual_number = self.db.teaching_plan_number(imported_id)
        if actual_number != plan_number:
            message += (
                f"\nتم استخدام العدد {actual_number} لأن العدد المطلوب "
                "مستخدم في مخطط آخر."
            )
        QMessageBox.information(self, "اكتمل الاستيراد", message)

    def selected_plan(self):
        if self._selected_plan_id in self._plan_cards:
            return self._selected_plan_id
        return None

    def load_plans(self, include_plan_id=None):
        previous_plan_id = self._selected_plan_id
        all_rows = self.db.teaching_plans()
        query = self.plan_search.text().strip().casefold()
        if query:
            rows = [
                row for row in all_rows
                if any(
                    query in str(value or "").casefold()
                    for value in (row[1], row[3], row[5], row[7], row[8])
                )
            ]
        else:
            today = QDate.currentDate().toString("yyyy-MM-dd")
            rows = [
                row for row in all_rows
                if row[8] <= today <= row[17]
            ]
        if include_plan_id is not None:
            included = next(
                (row for row in all_rows if row[0] == include_plan_id),
                None,
            )
            if included is not None and all(
                row[0] != include_plan_id for row in rows
            ):
                rows.append(included)
            rows.sort(key=lambda row: (row[8], row[0]), reverse=True)
        self._clear_plan_cards()
        selected_plan_id = None
        if rows:
            visible_ids = {row[0] for row in rows}
            if include_plan_id in visible_ids:
                selected_plan_id = include_plan_id
            elif previous_plan_id in visible_ids:
                selected_plan_id = previous_plan_id
            else:
                selected_plan_id = rows[0][0]
            for index, row in enumerate(rows):
                plan_id = row[0]
                try:
                    weekday_counts = self.db.teaching_plan_weekday_counts(
                        plan_id
                    )
                except sqlite3.Error as error:
                    QMessageBox.warning(
                        self, "تعذر تحميل أيام المخطط", str(error)
                    )
                    weekday_counts = {}
                try:
                    items = self.db.teaching_plan_items(plan_id)
                except sqlite3.Error as error:
                    QMessageBox.warning(
                        self, "تعذر تحميل أنشطة المخطط", str(error)
                    )
                    items = []
                card = TeachingPlanCard(row, weekday_counts, items)
                card.selected.connect(self.select_plan)
                card.double_clicked.connect(self.edit_plan_by_id)
                card.action_requested.connect(
                    self.handle_plan_card_action
                )
                self._plan_cards[plan_id] = card
                self.plans_grid.addWidget(card, index // 2, index % 2)
                card.set_selected(plan_id == selected_plan_id)
            self.plans_grid.setRowStretch((len(rows) + 1) // 2, 1)
        else:
            empty = QLabel("لا توجد مخططات مطابقة للبحث.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color: #64748B; padding: 24px;")
            self.plans_grid.addWidget(empty, 0, 0, 1, 2)
        self._selected_plan_id = selected_plan_id

    def _clear_plan_cards(self):
        self._plan_cards.clear()
        while self.plans_grid.count():
            item = self.plans_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def select_plan(self, plan_id):
        if plan_id not in self._plan_cards:
            return
        self._selected_plan_id = plan_id
        for card_id, card in self._plan_cards.items():
            card.set_selected(card_id == plan_id)

    def handle_plan_card_action(self, action, plan_id):
        if plan_id not in self._plan_cards:
            return
        self.select_plan(plan_id)
        handlers = {
            "edit": self.edit_plan,
            "delete": self.delete_plan,
            "configure": self.configure_items,
            "distribute": self.distribute_activities,
            "export_json": self.export_selected_plan,
            "export_pdf": self.export_selected_plan_pdf,
            "print": self.print_selected_plan,
        }
        handler = handlers.get(action)
        if handler is not None:
            handler()

    def add_plan(self):
        dialog = TeachingPlanDialog(self.db, self)
        dialog.exec()
        self.load_plans()

    def edit_plan(self):
        plan_id = self.selected_plan()
        if plan_id is None:
            QMessageBox.information(self, "اختيار مطلوب", "حدد مخططاً لتعديله.")
            return
        dialog = TeachingPlanDialog(self.db, self, self.db.teaching_plan(plan_id))
        dialog.exec()
        self.load_plans()

    def delete_plan(self):
        plan_id = self.selected_plan()
        if plan_id is None:
            QMessageBox.information(self, "اختيار مطلوب", "حدد مخططاً لحذفه.")
            return
        answer = QMessageBox.question(
            self, "تأكيد الحذف",
            "سيُحذف المخطط وحصصه وخصائصه الإضافية. هل تريد المتابعة؟",
        )
        if answer == QMessageBox.Yes:
            self.db.delete_teaching_plan(plan_id)
            self.load_plans()

    def configure_items(self):
        plan_id = self.selected_plan()
        if plan_id is None:
            QMessageBox.information(self, "اختيار مطلوب", "حدد مخططاً أولاً.")
            return
        dialog = PlanItemsDialog(
            self.db, self.db.teaching_plan(plan_id), self
        )
        dialog.exec()
        self.load_plans(include_plan_id=plan_id)

    def edit_plan_by_id(self, plan_id):
        if plan_id not in self._plan_cards:
            return
        self.select_plan(plan_id)
        self.edit_plan()

    def distribute_activities(self):
        plan_id = self.selected_plan()
        if plan_id is None:
            QMessageBox.information(self, "اختيار مطلوب", "حدد مخططاً أولاً.")
            return
        plan = self.db.teaching_plan(plan_id)
        error = self.distribution_validation_error(plan)
        if error:
            QMessageBox.warning(self, "المخطط غير جاهز للتوزيع", error)
            return

        window = self.window()
        pages_stack = getattr(window, "pages_stack", None)
        if pages_stack is None:
            QMessageBox.warning(
                self,
                "تعذر فتح صفحة التوزيع",
                "تعذر الوصول إلى مكدس صفحات التطبيق.",
            )
            return

        page = self._distribution_pages.get(plan_id)
        if page is None:
            page = PlanDistributionPage(
                self.db,
                plan,
                lambda: self.return_from_distribution(pages_stack),
            )
            self._distribution_pages[plan_id] = page
            pages_stack.addWidget(page)
        else:
            page.plan = plan
            page.load()
        pages_stack.setCurrentWidget(page)

    def distribution_validation_error(self, plan):
        if plan is None:
            return "تعذر تحميل بيانات المخطط."
        if not str(plan[1] or "").strip():
            return "أدخل اسم المخطط واحفظه أولاً."
        if any(value is None for value in plan[2:5]):
            return "تحقق من القسم والمجال وكفاية المجال في إعدادات المخطط."
        start_date = QDate.fromString(str(plan[5]), "yyyy-MM-dd")
        if not start_date.isValid():
            return "تاريخ انطلاق المخطط غير صالح."
        try:
            weekdays = [int(day) for day in str(plan[7]).split(",") if day]
        except ValueError:
            return "أيام التدريس المسجلة في المخطط غير صالحة."
        if not weekdays or any(day < 0 or day > 6 for day in weekdays):
            return "حدد أيام تدريس صالحة في إعدادات المخطط."
        if plan[9] not in (30, 60, 120):
            return "حدد زمناً صالحاً للحصة: 30 أو 60 أو 120 دقيقة."

        modes = PlanActivityDialog.MODES
        try:
            mode_counts = tuple(int(count) for count in plan[10:14])
            lesson_count = int(plan[6])
        except (TypeError, ValueError):
            return "أعداد الحصص حسب نمط التعلم غير صالحة."
        if (
            len(mode_counts) != len(modes)
            or any(count < 1 for count in mode_counts)
            or sum(mode_counts) != lesson_count
        ):
            return (
                "حدد عدداً صحيحاً لا يقل عن حصة واحدة لكل نمط تعلم، "
                "وتأكد من أن مجموع الأعداد يساوي إجمالي حصص المخطط."
            )
        return None

    def return_from_distribution(self, pages_stack):
        pages_stack.setCurrentWidget(self)
