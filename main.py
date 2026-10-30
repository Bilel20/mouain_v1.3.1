# -*- coding: utf-8 -*-
"""الواجهة الرئيسية (SchoolPlanner) — بقية الكود مقسم إلى:
app_config, database, completion, dialogs, widgets.
"""
from app_config import (
    APP_DIR,
    DB_PATH,
    ICON_PATH,
    LEVEL_FIRAKS,
    SCHOOL_INFO_COLUMNS,
    ArabicDialogTranslator,
    level_firak_label,
    load_custom_font,
)
from database import (
    Database, activity_completion_issues,
    duration_minutes, lesson_completion_issues,
    required_activity_properties,
)
from dialogs import (ActivityEntryDialog, BulkLessonEntryDialog, CompetenceManagerDialog,
                     ConceptManagerDialog,
                     CustomCalendarWidget, DaySelectionDialog,
                     DetailEntryDialog, EntryDialog, LessonEntryDialog,
                     LessonScheduleEditDialog,
                     RepeatActivitiesSelectionDialog,
                     PreviousSchoolYearsDialog, SchoolYearSelectionDialog,
                     TeachingAssignmentsDialog, TimetableDialog,
                     YearImportDialog)
from widgets import (CompetencesCbaBrowsePage, DayLessonsBrowsePage,
                     OfficialProgramsBrowsePage, OfficialProgramsWidget)
from teacher_timetable import TeacherTimetablePage
from plans import (
    OfficialHolidaysPage, TeachingPlanViewPage, TeachingPlansPage,
)
from lesson_book_settings import LessonBookSettingsPage
from assessment_file_management import AssessmentFileManagementPage
from busy_overlay import start_background_task

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
import numpy as np

import json
import secrets
from datetime import datetime
from pathlib import Path
import pandas as pd
from database_connection import connect_database
import sqlite3
from PySide6.QtCore import (
    Qt, QDate, QLocale, QObject, QThread, QTimer, Signal, Slot, QSize,
    QMimeData,
)
from PySide6.QtGui import QAction, QIcon, QFont, QColor, QDrag
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QGridLayout, QSplitter, QGroupBox, QDialog,
    QDialogButtonBox,
    QLabel, QLineEdit, QDateEdit,
    QTableWidget, QTableWidgetItem, QPushButton, QMessageBox,
    QHeaderView, QAbstractItemView, QMenu, QToolButton, QScrollArea,
    QSizePolicy, QWidgetAction, QTextEdit, QSpinBox, QProgressBar, QListWidget,
    QListWidgetItem, QListView,
    QStyle,
)
import sys


SCHOOL_INFO_DIALOG_FIELDS = [
    ("السنة الدراسية", "text", ""),
    ("المؤسسة", "text", ""),
    ("المعلم", "text", ""),
    ("المتفقد", "text", ""),
    ("اسم المساعد البيداغوجي", "text", ""),
    ("الرتبة", "text", ""),
    ("الشهادة العلمية", "text", ""),
    ("تاريخ الانتداب", "date", ""),
    ("تاريخ الترسيم", "date", ""),
    ("تاريخ الزيارة الأخيرة", "date", ""),
    ("العدد البيداغوجي المسند", "text", ""),
    ("خلاصة تقرير الزيارة", "plaintext", ""),
    ("اللغة", "text", ""),
    ("الهاتف", "text", ""),
    ("البريد الإلكتروني", "text", ""),
    ("الحالة العائلية", "text", ""),
    ("عدد الأطفال", "nonnegative_int", 0),
]

SCHEDULED_LESSONS_MIME = "application/x-schoolplanner-scheduled-lessons+json"
CARD_ACTION_BUTTON_STYLE = (
    "QPushButton { background: #F1F5F9; color: #334155;"
    "border: 1px solid #CBD5E1; border-radius: 9px;"
    "font-size: 11pt; font-weight: 700; padding: 0px; }"
    "QPushButton:hover { background: #DBEAFE;"
    "color: #1D4ED8; border-color: #60A5FA; }"
    "QPushButton[actionType=\"delete\"]:hover {"
    "background: #FEE2E2; color: #B91C1C;"
    "border-color: #FCA5A5; }"
)


def _create_card_action_button(
    parent, icon, tooltip, action, action_type, callback, item_id,
    enabled=True,
):
    button = QPushButton(icon, parent)
    button.setFixedSize(25, 25)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setProperty("actionType", action_type)
    button.setStyleSheet(CARD_ACTION_BUTTON_STYLE)
    button.setEnabled(enabled)
    button.clicked.connect(
        lambda _checked=False: callback(action, item_id)
    )
    return button


def _configure_icon_button(button, standard_icon, accessible_name):
    button.setText("")
    button.setIcon(QApplication.style().standardIcon(standard_icon))
    button.setToolTip(accessible_name)
    button.setAccessibleName(accessible_name)


def _parse_hour_interval(value):
    cleaned = (
        str(value or "")
        .replace("ـ", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace(" ", "")
    )
    parts = cleaned.split("-")
    if len(parts) != 2:
        raise ValueError("صيغة ساعة التدريس غير صالحة.")
    try:
        start = datetime.strptime(parts[0], "%H:%M")
        end = datetime.strptime(parts[1], "%H:%M")
    except ValueError as exc:
        raise ValueError("صيغة ساعة التدريس غير صالحة.") from exc
    start_minute = start.hour * 60 + start.minute
    end_minute = end.hour * 60 + end.minute
    if end_minute <= start_minute:
        raise ValueError("وقت نهاية الحصة يجب أن يكون بعد بدايتها.")
    return start_minute, end_minute


def _default_detail_key(namat, details):
    if str(namat or "").strip() != "منهجي":
        return None
    if any(
        str(detail[1] or "").strip() == "هدف الحصة"
        for detail in details
    ):
        return None
    return "هدف الحصة"


def _has_unoccupied_work_time(
    lessons, work_start, work_end, excluded_lesson_id=None
):
    return bool(_unoccupied_work_intervals(
        lessons, work_start, work_end, excluded_lesson_id
    ))


def _unoccupied_work_intervals(
    lessons, work_start, work_end, excluded_lesson_id=None
):
    intervals = []
    for lesson in lessons:
        if excluded_lesson_id is not None and lesson[0] == excluded_lesson_id:
            continue
        start, end = _parse_hour_interval(lesson[4])
        start = max(start, work_start)
        end = min(end, work_end)
        if start < end:
            intervals.append((start, end))

    intervals.sort()
    cursor = work_start
    available = []
    for start, end in intervals:
        if start > cursor:
            available.append((cursor, start))
        cursor = max(cursor, end)
        if cursor >= work_end:
            break
    if cursor < work_end:
        available.append((cursor, work_end))
    return available


def _hour_interval_is_available(
    hour_interval, lessons, work_bounds=None, excluded_lesson_id=None
):
    start, end = _parse_hour_interval(hour_interval)
    if work_bounds is not None:
        work_start, work_end = work_bounds
        if start < work_start or end > work_end:
            return False
    for lesson in lessons:
        if excluded_lesson_id is not None and lesson[0] == excluded_lesson_id:
            continue
        occupied_start, occupied_end = _parse_hour_interval(lesson[4])
        if start < occupied_end and occupied_start < end:
            return False
    return True


def _manual_lesson_activity_names(recorded_lessons, scheduled_lessons):
    activities_by_slot = {}
    for lesson in scheduled_lessons:
        if len(lesson) <= 5 or not str(lesson[5] or "").strip():
            continue
        slot = (
            str(lesson[0] or "").strip(),
            str(lesson[1] or "").strip(),
            _parse_hour_interval(lesson[2]),
        )
        activities_by_slot[slot] = str(lesson[5]).strip()

    activity_names = {}
    for lesson in recorded_lessons:
        slot = (
            str(lesson[2] or "").strip(),
            str(lesson[3] or "").strip(),
            _parse_hour_interval(lesson[4]),
        )
        activity_name = activities_by_slot.get(slot)
        if activity_name:
            activity_names[int(lesson[0])] = activity_name
    return activity_names


def _scheduled_lesson_is_recorded(scheduled_lesson, recorded_lessons):
    try:
        scheduled_slot = (
            str(scheduled_lesson[0] or "").strip(),
            str(scheduled_lesson[1] or "").strip(),
            _parse_hour_interval(scheduled_lesson[2]),
        )
    except (IndexError, ValueError):
        return False

    for recorded_lesson in recorded_lessons:
        try:
            recorded_slot = (
                str(recorded_lesson[2] or "").strip(),
                str(recorded_lesson[3] or "").strip(),
                _parse_hour_interval(recorded_lesson[4]),
            )
        except (IndexError, ValueError):
            continue
        if recorded_slot == scheduled_slot:
            return True
    return False


def _new_clear_confirmation_code():
    return "-".join(
        f"{secrets.randbelow(10000):04d}" for _ in range(4)
    )


def _clear_confirmation_code_matches(entered_code, expected_code):
    entered = str(entered_code or "").replace("-", "").replace(" ", "")
    expected = str(expected_code or "").replace("-", "").replace(" ", "")
    return entered == expected


class ClearAllConfirmationDialog(QDialog):
    def __init__(self, confirmation_code, parent=None):
        super().__init__(parent)
        self.setWindowTitle("تأكيد مسح جميع الحصص")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        layout = QVBoxLayout(self)
        instruction = QLabel(
            "هذا الإجراء سيمسح جميع الحصص والأنشطة والخصائص من قاعدة البيانات.\n"
            "اكتب الرمز التسلسلي التالي للتأكيد:"
        )
        instruction.setWordWrap(True)
        layout.addWidget(instruction)

        code_label = QLabel(confirmation_code)
        code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        code_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        code_label.setStyleSheet(
            "font-size: 16pt; font-weight: 700; color: #B91C1C;"
            "padding: 8px;"
        )
        layout.addWidget(code_label)

        self.code_edit = QLineEdit()
        self.code_edit.setObjectName("clearAllConfirmationCodeEdit")
        self.code_edit.setInputMask("0000-0000-0000-0000;_")
        self.code_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_edit.setPlaceholderText("0000-0000-0000-0000")
        layout.addWidget(self.code_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(
            QDialogButtonBox.StandardButton.Ok
        ).setText("تأكيد المسح")
        buttons.button(
            QDialogButtonBox.StandardButton.Ok
        ).setProperty("actionType", "clear")
        buttons.button(
            QDialogButtonBox.StandardButton.Ok
        ).setEnabled(False)
        buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        ).setText("إلغاء")
        self.code_edit.textChanged.connect(
            lambda _text: buttons.button(
                QDialogButtonBox.StandardButton.Ok
            ).setEnabled(self.code_edit.hasAcceptableInput())
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ScheduledLessonsListWidget(QListWidget):
    emptyCardActivated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("scheduledLessonCards")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setSpacing(7)
        self.itemDoubleClicked.connect(self._activate_empty_card)
        self.setStyleSheet(
            "QListWidget { border: none; background: transparent; }"
            "QListWidget::item {"
            "background: #FFFFFF; border: 1px solid #CBD5E1;"
            "border-right: 4px solid #2563EB; border-radius: 9px;"
            "padding: 9px; color: #0F172A; }"
            "QListWidget::item:selected {"
            "background: #DBEAFE; border: 2px solid #2563EB; }"
        )

    @staticmethod
    def _lesson_sort_key(entry):
        hour_text = str(entry[2] or "").strip().replace("ـ", "-")
        start_text = hour_text.replace(" ", "").split("-", 1)[0]
        try:
            return 0, datetime.strptime(start_text, "%H:%M").time()
        except ValueError:
            return 1, datetime.max.time()

    def set_lessons(
        self, lessons, disabled_intervals=(), allow_manual_add=True,
        recorded_lessons=(),
    ):
        self.clear()
        disabled_intervals = set(disabled_intervals)
        recorded_lessons = list(recorded_lessons)
        ordered_lessons = sorted(lessons, key=self._lesson_sort_key)
        for lesson in ordered_lessons:
            class_name, domain, hours, duration = (
                str(value or "").strip() for value in lesson[:4]
            )
            lesson_text = (
                f"{class_name} · {domain}\n"
                f"{hours or 'التوقيت غير محدد'}  |  "
                f"{duration or 'المدة غير محددة'}"
            )
            item = QListWidgetItem()
            item.setData(Qt.UserRole, tuple(lesson))
            item.setSizeHint(QSize(220, 66))
            tooltip = (
                f"القسم: {class_name}\nالمجال: {domain}\n"
                f"ساعة العمل: {hours}\nالمدة: {duration}\n"
                "اسحب البطاقة لإضافتها إلى دفتر اليوم."
            )
            item.setToolTip(tooltip)
            self.addItem(item)
            card = QWidget()
            card.setLayoutDirection(Qt.RightToLeft)
            card.setStyleSheet("background: transparent;")
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(4, 2, 4, 2)
            card_layout.setSpacing(8)
            if recorded_lessons and not _scheduled_lesson_is_recorded(
                lesson, recorded_lessons
            ):
                indicator = LessonStatusIndicator(
                    ["هذه الحصة المجدولة لم تُضف إلى حصص العمل."],
                    card,
                )
                indicator.setAccessibleName(
                    "تنبيه: الحصة المجدولة غير مضافة إلى حصص العمل"
                )
                card_layout.addWidget(indicator, 0, Qt.AlignVCenter)
            text_label = QLabel(lesson_text, card)
            text_label.setWordWrap(True)
            text_label.setToolTip(tooltip)
            text_label.setStyleSheet(
                "color: #0F172A; background: transparent; border: none;"
            )
            card_layout.addWidget(text_label, 1)
            self.setItemWidget(item, card)
            if _parse_hour_interval(hours) in disabled_intervals:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                item.setToolTip("هذا التوقيت مشغول بحصة أخرى.")

        empty_item = QListWidgetItem("＋ حصة غير مبرمجة\nانقر مرتين لإدخالها يدويًا")
        empty_item.setData(Qt.UserRole, None)
        empty_item.setSizeHint(QSize(220, 58))
        empty_item.setForeground(QColor("#475569"))
        empty_item.setToolTip("إضافة حصة يدويًا خارج جدول الأوقات.")
        if not allow_manual_add:
            empty_item.setFlags(empty_item.flags() & ~Qt.ItemIsEnabled)
            empty_item.setToolTip("لا توجد فترة عمل شاغرة لإضافة حصة جديدة.")
        self.addItem(empty_item)

    def selected_lessons(self):
        return [
            item.data(Qt.UserRole)
            for item in self.selectedItems()
            if item.data(Qt.UserRole) is not None
        ]

    def _activate_empty_card(self, item):
        if item.data(Qt.UserRole) is None:
            self.emptyCardActivated.emit()

    def startDrag(self, supported_actions):
        lessons = self.selected_lessons()
        if not lessons:
            return
        mime_data = QMimeData()
        mime_data.setData(
            SCHEDULED_LESSONS_MIME,
            json.dumps(lessons, ensure_ascii=False).encode("utf-8"),
        )
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec(Qt.CopyAction)


class RecordedLessonCardsWidget(QListWidget):
    actionRequested = Signal(str, int)
    lessonsDropped = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("recordedLessonCards")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setGridSize(QSize(404, 176))
        self.setSpacing(4)
        self.setStyleSheet(
            "QListWidget { border: none; background: transparent; }"
            "QListWidget::item { border: 1px solid transparent;"
            "border-radius: 12px; padding: 2px; }"
            "QListWidget::item:selected { background: #DBEAFE;"
            "border: 2px solid #60A5FA; }"
        )

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.itemAt(event.position().toPoint()) is None
        ):
            event.accept()
            return
        super().mousePressEvent(event)

    def set_lessons(
        self, lessons, heading_names=None, completion_issues=None,
        plan_progress=None, repeat_lesson_ids=None,
        repeat_lesson_statuses=None,
    ):
        self.clear()
        heading_names = heading_names or {}
        completion_issues = completion_issues or {}
        plan_progress = plan_progress or {}
        repeat_lesson_ids = set(int(lesson_id) for lesson_id in (repeat_lesson_ids or []))
        repeat_lesson_statuses = {
            int(lesson_id): status
            for lesson_id, status in (repeat_lesson_statuses or {}).items()
        }
        card_colors = (
            ("#EFF6FF", "#3B82F6", "#DBEAFE"),
            ("#ECFDF5", "#10B981", "#D1FAE5"),
            ("#FFFBEB", "#F59E0B", "#FEF3C7"),
            ("#F5F3FF", "#8B5CF6", "#EDE9FE"),
            ("#FDF2F8", "#EC4899", "#FCE7F3"),
            ("#ECFEFF", "#06B6D4", "#CFFAFE"),
            ("#FFF7ED", "#F97316", "#FFEDD5"),
            ("#F0FDF4", "#22C55E", "#DCFCE7"),
        )
        for index, lesson in enumerate(lessons):
            lesson_id, lesson_number, class_name, domain, hours, duration, unit, mode = lesson
            background, accent, property_background = card_colors[
                index % len(card_colors)
            ]
            item = QListWidgetItem()
            item.setData(Qt.UserRole, int(lesson_id))
            item.setSizeHint(QSize(396, 168))
            card = QFrame()
            card.setObjectName("recordedLessonCard")
            card.setFixedSize(388, 160)
            card.setStyleSheet(
                "QFrame#recordedLessonCard {"
                f"background: {background}; border: 1px solid {accent};"
                f"border-right: 5px solid {accent}; border-radius: 11px;"
                "}"
                "QLabel { border: none; background: transparent; }"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(9, 7, 9, 8)
            card_layout.setSpacing(5)
            heading_row = QHBoxLayout()
            heading_row.setSpacing(3)
            status_indicator = LessonStatusIndicator(
                completion_issues.get(int(lesson_id), []),
                card,
                repeat_requested=(
                    int(lesson_id) in repeat_lesson_ids
                    or int(lesson_id) in repeat_lesson_statuses
                ),
            )
            status_indicator.set_issues(
                completion_issues.get(int(lesson_id), []),
                repeat_applied=(
                    repeat_lesson_statuses.get(int(lesson_id)) == "applied"
                ),
            )
            heading_row.addWidget(status_indicator)
            heading_name = heading_names.get(int(lesson_id), domain)
            heading = QLabel(
                f"{class_name or '—'} · {heading_name or domain or '—'}"
            )
            heading.setStyleSheet(
                f"font-size: 10pt; font-weight: 700; color: {accent};"
            )
            heading.setWordWrap(True)
            heading_row.addWidget(heading, 1)
            progress = plan_progress.get(int(lesson_id))
            if progress:
                progress_lesson, total_lessons = progress
                percentage = min(
                    100, round(progress_lesson * 100 / total_lessons)
                )
                progress_bar = QProgressBar(card)
                progress_bar.setObjectName("recordedLessonPlanProgress")
                progress_bar.setRange(0, 100)
                progress_bar.setValue(percentage)
                progress_bar.setTextVisible(False)
                progress_bar.setFixedSize(72, 9)
                progress_bar.setAccessibleName(
                    f"تقدم المخطط: الحصة {progress_lesson} من "
                    f"{total_lessons}"
                )
                progress_bar.setToolTip(
                    f"الحصة {progress_lesson} من {total_lessons} "
                    f"({percentage}٪)"
                )
                progress_bar.setStyleSheet(
                    "QProgressBar { background: #DCFCE7;"
                    "border: 1px solid #86EFAC; border-radius: 4px;"
                    "padding: 0px; }"
                    "QProgressBar::chunk { background: #16A34A;"
                    "border-radius: 3px; }"
                )
                heading_row.addWidget(progress_bar)
            for icon, tooltip, action, action_type in (
                ("✎", "تعديل الحصة", "edit", "edit"),
                ("▣", "نسخ الحصة", "copy", "copy_paste"),
                ("×", "حذف الحصة", "delete", "delete"),
            ):
                button = _create_card_action_button(
                    card,
                    icon,
                    tooltip,
                    action,
                    action_type,
                    self.actionRequested.emit,
                    int(lesson_id),
                )
                heading_row.addWidget(button)
            card_layout.addLayout(heading_row)

            properties = QGridLayout()
            properties.setHorizontalSpacing(7)
            properties.setVerticalSpacing(4)
            for index, (label, value) in enumerate((
                ("المجال", domain),
                ("ساعة التدريس", hours),
                ("التوقيت", duration),
                ("الوحدة", unit),
                ("عدد الحصة", lesson_number),
                ("نمط التعلم", mode),
            )):
                property_label = QLabel(
                    f"{label}: {value if value not in (None, '') else '—'}"
                )
                property_label.setWordWrap(False)
                property_label.setAlignment(Qt.AlignCenter)
                property_label.setMinimumHeight(34)
                property_label.setStyleSheet(
                    "color: #334155; "
                    f"background: {property_background};"
                    "border-radius: 5px; padding: 2px 5px; font-size: 9pt;"
                )
                properties.addWidget(property_label, index // 2, index % 2)
            card_layout.addLayout(properties)
            self.addItem(item)
            self.setItemWidget(item, card)

    def select_lesson(self, lesson_id):
        self.clearSelection()
        if lesson_id is None:
            self.setCurrentItem(None)
            return
        for row in range(self.count()):
            item = self.item(row)
            if item.data(Qt.UserRole) == int(lesson_id):
                item.setSelected(True)
                self.setCurrentItem(item)
                self.scrollToItem(item)
                return

    def selected_lesson_ids(self):
        return [
            int(item.data(Qt.UserRole))
            for item in self.selectedItems()
            if item.data(Qt.UserRole) is not None
        ]

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(SCHEDULED_LESSONS_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(SCHEDULED_LESSONS_MIME):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(SCHEDULED_LESSONS_MIME):
            super().dropEvent(event)
            return
        try:
            lessons = json.loads(
                bytes(event.mimeData().data(SCHEDULED_LESSONS_MIME))
                .decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            event.ignore()
            return
        if (
            not isinstance(lessons, list)
            or any(
                not isinstance(lesson, list) or len(lesson) < 4
                for lesson in lessons
            )
        ):
            event.ignore()
            return
        self.lessonsDropped.emit(lessons)
        event.acceptProposedAction()


class LessonStatusIndicator(QLabel):
    def __init__(self, issues, parent=None, item_type="الحصة", repeat_requested=False):
        super().__init__(parent)
        self.item_type = item_type
        self.issues = list(issues)
        self.repeat_requested = bool(repeat_requested)
        self.repeat_applied = False
        self._warning_visible = True
        self.setFixedSize(22, 22)
        self.setObjectName("lessonStatusIndicator")
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("border: none; background: transparent;")
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._toggle_warning)
        self.set_issues(issues)

    def _status_text(self, complete):
        if self.item_type == "النشاط":
            return "النشاط مكتمل" if complete else "تنبيه: النشاط غير مكتمل"
        if self.repeat_requested:
            if self.repeat_applied:
                return "تمت إعادة الحصة"
            return "الحصة ستُعاد في المرة القادمة"
        return "الحصة مكتملة" if complete else "تنبيه: توجد مشكلة في الحصة"

    def _status_tooltip(self, complete):
        if self.repeat_requested:
            if self.repeat_applied:
                return "تمت إعادة الحصة."
            if complete:
                return "تمت الموافقة على إعادة هذه الحصة في المرة القادمة."
            return (
                "تمت الموافقة على إعادة هذه الحصة في المرة القادمة، "
                "وتوجد مشكلات تحتاج إلى استكمال:\n• "
                + "\n• ".join(self.issues)
            )
        if complete:
            if self.item_type == "النشاط":
                return "النشاط مكتمل: البيانات المطلوبة موجودة."
            return (
                "الحصة سليمة: مدد الأنشطة مكتملة والبيانات المطلوبة موجودة."
            )
        subject = "النشاط يحتاج إلى استكمال" if self.item_type == "النشاط" else "الحصة تحتاج إلى استكمال"
        return subject + ":\n• " + "\n• ".join(self.issues)

    def set_issues(self, issues, repeat_requested=None, repeat_applied=None):
        if repeat_requested is not None:
            self.repeat_requested = bool(repeat_requested)
        if repeat_applied is not None:
            self.repeat_applied = bool(repeat_applied)
        self.issues = list(issues)
        if self.repeat_requested and self.item_type == "الحصة":
            self._blink_timer.stop()
            self._warning_visible = True
            self.setText("↻")
            self.setToolTip(self._status_tooltip(not self.issues))
            self.setAccessibleName(self._status_text(not self.issues))
            self.setStyleSheet(
                "QLabel { color: white; background: "
                f"{'#16A34A' if self.repeat_applied else '#2563EB'};"
                f"border: 1px solid {'#15803D' if self.repeat_applied else '#1D4ED8'};"
                "border-radius: 11px;"
                "font-weight: 700; font-size: 13px; }"
            )
            return
        if self.issues:
            self.setText("!")
            self.setToolTip(self._status_tooltip(False))
            self.setAccessibleName(self._status_text(False))
            self._warning_visible = True
            self._apply_warning_style()
            if not self._blink_timer.isActive():
                self._blink_timer.start()
            return

        self._blink_timer.stop()
        self._warning_visible = True
        self.setText("✓")
        self.setToolTip(self._status_tooltip(True))
        self.setAccessibleName(self._status_text(True))
        self.setStyleSheet(
            "QLabel { color: white; background: #16A34A;"
            "border: 1px solid #15803D; border-radius: 11px;"
            "font-weight: 700; font-size: 13px; }"
        )

    def _apply_warning_style(self):
        background = "#F97316" if self._warning_visible else "#FFEDD5"
        foreground = "white" if self._warning_visible else "#C2410C"
        self.setStyleSheet(
            "QLabel {"
            f"color: {foreground}; background: {background};"
            "border: 1px solid #EA580C; border-radius: 11px;"
            "font-weight: 700; font-size: 14px;"
            "}"
        )

    def _toggle_warning(self):
        self._warning_visible = not self._warning_visible
        self._apply_warning_style()


def _school_info_to_dialog_values(values):
    return list(values[:4]) + [values[5], values[4]] + list(values[6:])


def _school_info_from_dialog_values(values):
    return list(values[:4]) + [values[5], values[4]] + list(values[6:])


class ActivityCardsWidget(QListWidget):
    actionRequested = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._read_only = False
        self.setObjectName("lessonActivityCards")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setGridSize(QSize(162, 140))
        self.setSpacing(6)
        self.setStyleSheet(
            "QListWidget { border: none; background: transparent; }"
            "QListWidget::item { border: 1px solid transparent;"
            "border-radius: 10px; padding: 1px; }"
        )
        self.itemSelectionChanged.connect(self._sync_selected_cards)

    def set_activities(self, activities, completion_issues=None, select_id=None):
        self.clear()
        completion_issues = completion_issues or {}
        colors = (
            ("#EFF6FF", "#2563EB", "#BFDBFE"),
            ("#ECFDF5", "#059669", "#A7F3D0"),
            ("#FFFBEB", "#D97706", "#FDE68A"),
            ("#FDF2F8", "#DB2777", "#FBCFE8"),
            ("#ECFEFF", "#0891B2", "#A5F3FC"),
            ("#FFF7ED", "#EA580C", "#FED7AA"),
            ("#F5F3FF", "#7C3AED", "#DDD6FE"),
        )
        selected_item = None
        for index, (activity_id, title, duration) in enumerate(activities):
            background, accent, border = colors[index % len(colors)]
            item = QListWidgetItem()
            item.setData(Qt.UserRole, int(activity_id))
            item.setSizeHint(QSize(160, 138))
            self.addItem(item)

            card = QFrame(self)
            card.setObjectName("lessonActivityCard")
            card.setFixedSize(150, 130)
            card.setProperty("selected", False)
            card.setStyleSheet(
                "QFrame#lessonActivityCard {"
                f"background: {background}; border: 1px solid {border};"
                f"border-right: 5px solid {accent}; border-radius: 9px;"
                "}"
                "QFrame#lessonActivityCard[selected=\"true\"] {"
                f"border: 2px solid {accent}; border-right: 5px solid {accent};"
                "}"
                "QLabel { border: none; background: transparent; }"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(7, 6, 7, 6)
            card_layout.setSpacing(4)
            heading_layout = QHBoxLayout()
            heading_layout.setSpacing(5)
            status = LessonStatusIndicator(
                completion_issues.get(int(activity_id), []),
                card,
                item_type="النشاط",
            )
            heading_layout.addWidget(status, 0, Qt.AlignTop)
            title_label = QLabel(str(title or "نشاط دون عنوان"), card)
            title_label.setObjectName("lessonActivityTitle")
            title_label.setWordWrap(True)
            title_label.setStyleSheet(
                f"color: {accent}; font-weight: 700; font-size: 10pt;"
            )
            heading_layout.addWidget(title_label, 1)
            card_layout.addLayout(heading_layout, 1)
            duration_label = QLabel(f"المدة: {duration or '—'}", card)
            duration_label.setObjectName("lessonActivityDuration")
            duration_label.setStyleSheet("color: #475569; font-size: 9pt;")
            duration_label.setWordWrap(True)
            duration_label.setAlignment(Qt.AlignCenter)
            card_layout.addWidget(duration_label)
            actions_layout = QHBoxLayout()
            actions_layout.setSpacing(4)
            actions_layout.addStretch(1)
            for icon, tooltip, action, action_type in (
                ("✎", "تعديل النشاط", "edit", "edit"),
                ("×", "حذف النشاط", "delete", "delete"),
            ):
                button = _create_card_action_button(
                    card,
                    icon,
                    tooltip,
                    action,
                    action_type,
                    self.actionRequested.emit,
                    int(activity_id),
                    enabled=not self._read_only,
                )
                actions_layout.addWidget(button)
            actions_layout.addStretch(1)
            card_layout.addLayout(actions_layout)
            self.setItemWidget(item, card)
            if select_id is not None and int(activity_id) == int(select_id):
                selected_item = item

        if selected_item is None and self.count():
            selected_item = self.item(0)
        if selected_item is not None:
            self.setCurrentItem(selected_item)
        self._sync_selected_cards()

    def set_read_only(self, read_only):
        self._read_only = bool(read_only)
        for row in range(self.count()):
            card = self.itemWidget(self.item(row))
            if card is None:
                continue
            for button in card.findChildren(QPushButton):
                button.setEnabled(not self._read_only)

    def _sync_selected_cards(self):
        for row in range(self.count()):
            item = self.item(row)
            card = self.itemWidget(item)
            if card is None:
                continue
            card.setProperty("selected", item.isSelected())
            card.style().unpolish(card)
            card.style().polish(card)


class DetailCardsWidget(QListWidget):
    actionRequested = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._read_only = False
        self.setObjectName("lessonDetailCards")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSpacing(6)
        self.setStyleSheet(
            "QListWidget { border: none; background: transparent; }"
            "QListWidget::item { border: 1px solid transparent;"
            "border-radius: 9px; padding: 1px; }"
        )
        self.itemSelectionChanged.connect(self._sync_selected_cards)

    def set_details(self, details, select_id=None):
        self.clear()
        colors = (
            ("#EFF6FF", "#2563EB", "#BFDBFE"),
            ("#ECFDF5", "#059669", "#A7F3D0"),
            ("#FFFBEB", "#D97706", "#FDE68A"),
            ("#FDF2F8", "#DB2777", "#FBCFE8"),
            ("#ECFEFF", "#0891B2", "#A5F3FC"),
            ("#FFF7ED", "#EA580C", "#FED7AA"),
        )
        selected_item = None
        for index, (detail_id, key_name, value_text) in enumerate(details):
            background, accent, border = colors[index % len(colors)]
            item = QListWidgetItem()
            item.setData(Qt.UserRole, int(detail_id))
            item.setSizeHint(QSize(340, 88))
            self.addItem(item)

            card = QFrame(self)
            card.setObjectName("lessonDetailCard")
            card.setProperty("selected", False)
            card.setStyleSheet(
                "QFrame#lessonDetailCard {"
                f"background: {background}; border: 1px solid {border};"
                f"border-right: 5px solid {accent}; border-radius: 9px;"
                "}"
                "QFrame#lessonDetailCard[selected=\"true\"] {"
                f"border: 2px solid {accent}; border-right: 5px solid {accent};"
                "}"
                "QLabel { border: none; background: transparent; }"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 7, 10, 7)
            card_layout.setSpacing(3)
            heading_layout = QHBoxLayout()
            heading_layout.setSpacing(4)
            key_label = QLabel(str(key_name or "خاصية دون اسم"), card)
            key_label.setObjectName("lessonDetailKey")
            key_label.setWordWrap(True)
            key_label.setStyleSheet(
                f"color: {accent}; font-weight: 700; font-size: 10pt;"
            )
            heading_layout.addWidget(key_label, 1)
            for icon, tooltip, action, action_type in (
                ("✎", "تعديل الخاصية", "edit", "edit"),
                ("×", "حذف الخاصية", "delete", "delete"),
            ):
                button = _create_card_action_button(
                    card,
                    icon,
                    tooltip,
                    action,
                    action_type,
                    self.actionRequested.emit,
                    int(detail_id),
                    enabled=not self._read_only,
                )
                heading_layout.addWidget(button)
            card_layout.addLayout(heading_layout)
            value_label = QLabel(str(value_text or "—"), card)
            value_label.setObjectName("lessonDetailValue")
            value_label.setWordWrap(True)
            value_label.setStyleSheet("color: #334155; font-size: 9pt;")
            card_layout.addWidget(value_label)
            self.setItemWidget(item, card)
            if select_id is not None and int(detail_id) == int(select_id):
                selected_item = item

        if selected_item is None and self.count():
            selected_item = self.item(0)
        if selected_item is not None:
            self.setCurrentItem(selected_item)
        self._sync_selected_cards()

    def set_read_only(self, read_only):
        self._read_only = bool(read_only)
        for row in range(self.count()):
            card = self.itemWidget(self.item(row))
            if card is None:
                continue
            for button in card.findChildren(QPushButton):
                button.setEnabled(not self._read_only)

    def _sync_selected_cards(self):
        for row in range(self.count()):
            item = self.item(row)
            card = self.itemWidget(item)
            if card is None:
                continue
            card.setProperty("selected", item.isSelected())
            card.style().unpolish(card)
            card.style().polish(card)


def _insert_menu_action_button(menu, original_action):
    item_button = QToolButton(menu)
    item_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    item_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    item_button.setMinimumHeight(42)
    item_button.setMinimumWidth(180)
    item_button.setCursor(Qt.PointingHandCursor)
    item_button.setObjectName("centeredNavigationMenuItem")
    widget_action = QWidgetAction(menu)

    def sync_action():
        text = original_action.text().replace("&&", "\0")
        item_button.setText(text.replace("&", "").replace("\0", "&"))
        item_button.setEnabled(original_action.isEnabled())
        item_button.setVisible(original_action.isVisible())
        item_button.setCheckable(original_action.isCheckable())
        item_button.setChecked(original_action.isChecked())
        widget_action.setEnabled(original_action.isEnabled())
        widget_action.setVisible(original_action.isVisible())

        menu_width = max(menu.minimumWidth(), item_button.sizeHint().width() + 12)
        item_button.setFixedWidth(menu_width - 12)
        menu.setMinimumWidth(menu_width)

    widget_action.setDefaultWidget(item_button)
    item_button.clicked.connect(original_action.trigger)
    item_button.clicked.connect(menu.close)
    original_action.changed.connect(sync_action)
    sync_action()
    menu.insertAction(original_action, widget_action)
    menu.removeAction(original_action)


def center_menu_items(menu):
    menu.setLayoutDirection(Qt.RightToLeft)
    menu.setMinimumWidth(230)
    for action in tuple(menu.actions()):
        if not action.isSeparator():
            _insert_menu_action_button(menu, action)


class TeacherInfoPage(QWidget):
    saveRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("teacherInfoPage")
        self.setLayoutDirection(Qt.RightToLeft)
        self.inputs = []
        self._loading_values = False
        self._read_only = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(700)
        self._autosave_timer.timeout.connect(self.saveRequested)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("teacherInfoScrollArea")
        self.scroll_area.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(18, 16, 18, 18)
        content_layout.setSpacing(14)

        banner = QFrame()
        banner.setObjectName("teacherInfoBanner")
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(22, 18, 22, 18)
        icon = QLabel("♙")
        icon.setObjectName("teacherInfoIcon")
        banner_layout.addWidget(icon)
        banner_text = QVBoxLayout()
        description = QLabel(
            "نظّم بيانات المؤسسة والمسار المهني ووسائل التواصل "
            "للسنة الدراسية المحددة."
        )
        description.setObjectName("teacherInfoBannerDescription")
        description.setWordWrap(True)
        banner_text.addWidget(description)
        banner_layout.addLayout(banner_text, 1)
        content_layout.addWidget(banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        cards_container = QWidget()
        cards_layout = QVBoxLayout(cards_container)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(12)
        self._add_field_card(
            cards_layout,
            "البيانات الأساسية",
            SCHOOL_INFO_DIALOG_FIELDS[:7],
        )
        self._add_field_card(
            cards_layout,
            "المسار المهني",
            SCHOOL_INFO_DIALOG_FIELDS[7:11],
        )
        self._add_field_card(
            cards_layout,
            "ملاحظات الزيارة",
            SCHOOL_INFO_DIALOG_FIELDS[11:12],
        )
        self._add_field_card(
            cards_layout,
            "التواصل والبيانات الشخصية",
            SCHOOL_INFO_DIALOG_FIELDS[12:],
        )
        cards_layout.addStretch(1)
        scroll.setWidget(cards_container)
        content_layout.addWidget(scroll, 1)

        self.scroll_area.setWidget(content)
        root.addWidget(self.scroll_area)

    def _add_field_card(self, parent_layout, title, fields):
        card = QGroupBox(title)
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 20, 16, 16)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        for field_index, (label_text, kind, default) in enumerate(fields):
            if kind == "nonnegative_int":
                editor = QSpinBox()
                editor.setRange(0, 999)
                editor.setValue(int(default or 0))
                editor.lineEdit().setAlignment(Qt.AlignCenter)
            elif kind == "plaintext":
                editor = QTextEdit()
                editor.setAcceptRichText(False)
                editor.setMinimumHeight(110)
            else:
                editor = QLineEdit(str(default or ""))
                editor.setAlignment(Qt.AlignCenter)
                if kind == "date":
                    editor.setPlaceholderText("YYYY-MM-DD")

            label = QLabel(label_text)
            label.setObjectName("teacherInfoFieldLabel")
            label.setAlignment(Qt.AlignCenter)
            if kind == "plaintext":
                grid.addWidget(label, 0, 0, 1, 3)
                grid.addWidget(editor, 1, 0, 1, 3)
            else:
                column = field_index % 3
                row = (field_index // 3) * 2
                grid.addWidget(label, row, column)
                grid.addWidget(editor, row + 1, column)
            self.inputs.append(editor)
            if isinstance(editor, QSpinBox):
                editor.valueChanged.connect(self._schedule_autosave)
            elif isinstance(editor, QTextEdit):
                editor.textChanged.connect(self._schedule_autosave)
            else:
                editor.textChanged.connect(self._schedule_autosave)
        parent_layout.addWidget(card)

    def set_values(self, values):
        self._loading_values = True
        self._autosave_timer.stop()
        try:
            for editor, value in zip(self.inputs, values):
                if isinstance(editor, QSpinBox):
                    editor.setValue(int(value or 0))
                elif isinstance(editor, QTextEdit):
                    editor.setPlainText(str(value or ""))
                else:
                    editor.setText(str(value or ""))
        finally:
            self._loading_values = False

    def _schedule_autosave(self, *_):
        if not self._loading_values and not self._read_only:
            self._autosave_timer.start()

    def values(self):
        result = []
        for editor in self.inputs:
            if isinstance(editor, QSpinBox):
                result.append(editor.value())
            elif isinstance(editor, QTextEdit):
                result.append(editor.toPlainText().strip())
            else:
                result.append(editor.text().strip())
        return result

    def set_read_only(self, read_only):
        self._read_only = bool(read_only)
        if self._read_only:
            self._autosave_timer.stop()
        for editor in self.inputs:
            if isinstance(editor, (QLineEdit, QTextEdit)):
                editor.setReadOnly(self._read_only)
            else:
                editor.setEnabled(not self._read_only)


class StatisticsLoadWorker(QObject):
    result_ready = Signal(object, int, bool)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, database_path, force_refresh=False):
        super().__init__()
        self.database_path = database_path
        self.cache_path = f"{database_path}.analytics.sqlite3"
        self.force_refresh = force_refresh

    @Slot()
    def run(self):
        try:
            with connect_database(self.database_path, timeout=10) as con:
                con.execute("BEGIN")
                revision_row = con.execute(
                    "SELECT revision FROM app_data_revision WHERE id=1"
                ).fetchone()
                revision = int(revision_row[0]) if revision_row else 0

                with connect_database(self.cache_path, timeout=10) as cache:
                    cache.execute("""
                        CREATE TABLE IF NOT EXISTS analytics_cache (
                            source_path TEXT PRIMARY KEY,
                            source_revision INTEGER NOT NULL,
                            payload TEXT NOT NULL,
                            refreshed_at TEXT NOT NULL
                        )
                    """)
                    cached = cache.execute(
                        """SELECT source_revision, payload
                           FROM analytics_cache WHERE source_path=?""",
                        (str(Path(self.database_path).resolve()),),
                    ).fetchone()
                if (
                    not self.force_refresh and cached
                    and int(cached[0]) == revision
                ):
                    con.rollback()
                    self.result_ready.emit(
                        json.loads(cached[1]), revision, True
                    )
                    return

                source = pd.read_sql_query("""
                    SELECT h.id AS lesson_id, d.date,
                        COALESCE(g.group_key, c.name) AS class_name,
                        m.name AS subject, lm.name AS learning_mode,
                        h.time AS lesson_time,
                        n.id AS activity_id, n.title AS activity_title,
                        n.time AS activity_time,
                        nd.id AS detail_id, nd.key_name,
                        nd.value_text
                    FROM hessa h
                    JOIN day_data d ON d.id = h.day_data_id
                    JOIN classe c ON c.id = h.classe_id
                    LEFT JOIN class_group g ON g.id=h.class_group_id
                    LEFT JOIN majal m ON m.id = h.majal_id
                    LEFT JOIN learning_mode lm ON lm.id=h.learning_mode_id
                    LEFT JOIN nachat n ON n.hessa_id = h.id
                    LEFT JOIN nachat_detail nd ON nd.nachat_id = n.id
                    ORDER BY d.date, h.id, n.id, nd.id
                """, con)
                con.commit()

            group_labels = {
                group_key: level_firak_label(group_key)
                for group_key in LEVEL_FIRAKS
            }
            source["class_name"] = source["class_name"].map(
                lambda name: (
                    f"{name} — {group_labels[name]}"
                    if name in group_labels else name
                )
            )
            payload = self._build_analytics(source)
            with connect_database(self.cache_path, timeout=10) as cache:
                cache.execute("""
                    INSERT INTO analytics_cache(
                        source_path, source_revision, payload, refreshed_at
                    ) VALUES(?, ?, ?, datetime('now', 'localtime'))
                    ON CONFLICT(source_path) DO UPDATE SET
                        source_revision=excluded.source_revision,
                        payload=excluded.payload,
                        refreshed_at=excluded.refreshed_at
                """, (
                    str(Path(self.database_path).resolve()), revision,
                    json.dumps(payload, ensure_ascii=False),
                ))
            self.result_ready.emit(payload, revision, False)
        except (sqlite3.Error, pd.errors.DatabaseError, ValueError, TypeError) as exc:
            self.failed.emit(f"تعذر تحميل الإحصائيات من قاعدة البيانات: {exc}")
        finally:
            self.finished.emit()

    @staticmethod
    def _build_analytics(source):
        mode_properties = {
            "منهجي": ("المحتوى", "هدف الحصة"),
            "إدماج": ("الكفايات المستهدفة",),
            "تقييم": ("الأداء المنتظر",),
            "دعم وعلاج": ("المعايير المستهدفة",),
        }
        if source.empty:
            lessons = pd.DataFrame(columns=[
                "lesson_id", "date", "class_name", "subject",
                "learning_mode", "lesson_time",
            ])
            activities = pd.DataFrame(columns=[
                "activity_id", "lesson_id", "activity_title",
                "activity_time", "learning_mode",
            ])
            details = pd.DataFrame(columns=[
                "detail_id", "activity_id", "activity_title",
                "key_name", "value_text", "learning_mode",
            ])
        else:
            lessons = source.drop_duplicates("lesson_id")[
                ["lesson_id", "date", "class_name", "subject",
                 "learning_mode", "lesson_time"]
            ].copy()
            activities = source.dropna(subset=["activity_id"]).drop_duplicates(
                "activity_id"
            )[
                ["activity_id", "lesson_id", "activity_title",
                 "activity_time", "learning_mode"]
            ].copy()
            details = source.dropna(subset=["detail_id"])[
                ["detail_id", "activity_id", "activity_title", "key_name",
                 "value_text", "learning_mode"]
            ].copy()

        lesson_count = int(len(lessons))
        activity_count = int(len(activities))
        detail_count = int(len(details))
        classes_count = int(lessons["class_name"].nunique())
        dates = pd.to_datetime(lessons["date"], errors="coerce")
        recorded_days = int(dates.nunique())

        activity_counts = (
            activities.groupby("lesson_id").size()
            if not activities.empty else pd.Series(dtype="int64")
        )
        metrics = {
            "lessons": lesson_count,
            "days": recorded_days,
            "classes": classes_count,
            "activities": activity_count,
            "properties": detail_count,
            "average_activities": round(
                activity_count / lesson_count, 1
            ) if lesson_count else 0,
            "average_properties": round(
                detail_count / activity_count, 1
            ) if activity_count else 0,
        }

        lesson_minutes = pd.to_numeric(
            lessons["lesson_time"].astype(str).str.extract(r"(\d+)")[0],
            errors="coerce",
        )
        lesson_minutes.index = lessons["lesson_id"]
        if not activities.empty:
            activities["duration_minutes"] = pd.to_numeric(
                activities["activity_time"].astype(str).str.extract(
                    r"(\d+)"
                )[0],
                errors="coerce",
            )
            activity_minutes = activities.groupby("lesson_id")[
                "duration_minutes"
            ].sum(min_count=1)
            has_valid_activity_times = activities.groupby(
                "lesson_id"
            )["duration_minutes"].count()
            actual_activity_count = activities.groupby("lesson_id").size()
        else:
            activity_minutes = pd.Series(dtype="float64")
            has_valid_activity_times = pd.Series(dtype="int64")
            actual_activity_count = pd.Series(dtype="int64")

        filled_keys = {}
        if not details.empty:
            valid_details = details[
                details["value_text"].fillna("").astype(str).str.strip() != ""
            ]
            filled_keys = (
                valid_details.groupby("activity_id")["key_name"]
                .agg(lambda values: set(str(value).strip() for value in values))
                .to_dict()
            )
        activity_complete = {}
        for activity in activities.itertuples(index=False):
            required = mode_properties.get(
                str(activity.learning_mode or "").strip(), ()
            )
            present = filled_keys.get(activity.activity_id, set())
            activity_complete[activity.activity_id] = all(
                key in present for key in required
            )
        complete_activity_counts = (
            activities.assign(
                is_complete=activities["activity_id"].map(activity_complete)
            ).groupby("lesson_id")["is_complete"].all()
            if not activities.empty else pd.Series(dtype=bool)
        )
        has_activities = activity_counts.reindex(
            lessons["lesson_id"], fill_value=0
        ) > 0
        minutes_match = (
            activity_minutes.reindex(lessons["lesson_id"]).to_numpy()
            == lesson_minutes.reindex(lessons["lesson_id"]).to_numpy()
        )
        valid_times = (
            has_valid_activity_times.reindex(
                lessons["lesson_id"], fill_value=0
            ).to_numpy()
            == actual_activity_count.reindex(
                lessons["lesson_id"], fill_value=0
            ).to_numpy()
        )
        all_activities_complete = complete_activity_counts.reindex(
            lessons["lesson_id"], fill_value=False
        ).to_numpy()
        complete_lessons = int(
            (has_activities.to_numpy() & minutes_match & valid_times
             & all_activities_complete).sum()
        )
        metrics["complete_lessons"] = complete_lessons
        metrics["incomplete_lessons"] = lesson_count - complete_lessons
        metrics["completion_rate"] = round(
            complete_lessons / lesson_count * 100, 1
        ) if lesson_count else 0

        subject_counts = (
            lessons.assign(subject=lessons["subject"].fillna("غير محدد"))
            .groupby("subject").size().sort_values(ascending=False)
        )
        mode_counts = (
            lessons.assign(
                learning_mode=lessons["learning_mode"].fillna("غير محدد")
            ).groupby("learning_mode").size().sort_values(ascending=False)
        )
        activity_title_counts = (
            activities.assign(
                activity_title=activities["activity_title"].fillna("غير محدد")
            ).groupby("activity_title").size().sort_values(ascending=False)
        )
        if details.empty:
            property_usage = []
        else:
            used_details = details[
                details["value_text"].fillna("").astype(str).str.strip() != ""
            ].copy()
            used_details["activity_title"] = (
                used_details["activity_title"].fillna("غير محدد")
            )
            used_details["key_name"] = used_details["key_name"].fillna(
                "غير محدد"
            )
            grouped = used_details.groupby(
                ["activity_title", "key_name"]
            ).agg(
                uses=("detail_id", "count"),
                activities=("activity_id", "nunique"),
            ).sort_values(
                ["uses", "activities"], ascending=False
            ).head(40)
            property_usage = [
                {
                    "activity": str(activity),
                    "property": str(key),
                    "uses": int(row["uses"]),
                    "activities": int(row["activities"]),
                }
                for (activity, key), row in grouped.iterrows()
            ]

        weekly = []
        forecast = []
        valid_dates = dates.dropna()
        if not valid_dates.empty:
            lesson_dates = lessons.loc[valid_dates.index, ["lesson_id"]].copy()
            lesson_dates["week"] = valid_dates.dt.to_period("W").astype(str)
            weekly_series = lesson_dates.groupby("week")["lesson_id"].nunique()
            last_period = valid_dates.max().to_period("W")
            periods = pd.period_range(
                end=last_period, periods=12, freq="W"
            )
            weekly_series = weekly_series.reindex(
                periods.astype(str), fill_value=0
            )
            weekly = [
                {"week": str(period), "lessons": int(weekly_series.loc[str(period)])}
                for period in periods
            ]
            baseline = weekly_series.tail(8).astype(float)
            if len(baseline) >= 2 and baseline.sum() > 0:
                x_values = list(range(len(baseline)))
                slope, intercept = np.polyfit(
                    x_values, baseline.to_numpy(), 1
                )
                predicted = [
                    max(0, int(round(slope * index + intercept)))
                    for index in range(len(baseline), len(baseline) + 4)
                ]
            else:
                average = float(baseline.mean()) if len(baseline) else 0
                predicted = [max(0, int(round(average)))] * 4
            future_periods = pd.period_range(
                start=last_period + 1, periods=4, freq="W"
            )
            forecast = [
                {"week": str(period), "lessons": predicted[index]}
                for index, period in enumerate(future_periods)
            ]

        return {
            "metrics": metrics,
            "subjects": [
                {"name": str(name), "count": int(count)}
                for name, count in subject_counts.items()
            ],
            "learning_modes": [
                {"name": str(name), "count": int(count)}
                for name, count in mode_counts.items()
            ],
            "activities": [
                {"name": str(name), "count": int(count)}
                for name, count in activity_title_counts.head(12).items()
            ],
            "property_usage": property_usage,
            "weekly": weekly,
            "forecast": forecast,
        }

class LessonHistoryLoadWorker(QObject):
    result_ready = Signal(object, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self, database_path, weekday_num, existing_count,
        generate_defaults=True,
    ):
        super().__init__()
        self.database_path = database_path
        self.weekday_num = weekday_num
        self.existing_count = existing_count
        self.generate_defaults = generate_defaults

    @Slot()
    def run(self):
        try:
            with connect_database(self.database_path, timeout=10) as con:
                history = pd.read_sql_query("""
                    SELECT COALESCE(g.group_key, c.name) AS classe,
                        m.name AS majal, h.heure, h.time,
                        h.wehda_number, h.hessa_number,
                        lm.name AS namat_taallom, d.date
                    FROM hessa h
                    JOIN day_data d ON h.day_data_id = d.id
                    JOIN classe c ON c.id = h.classe_id
                    LEFT JOIN class_group g ON g.id=h.class_group_id
                    LEFT JOIN majal m ON m.id=h.majal_id
                    LEFT JOIN learning_mode lm ON lm.id=h.learning_mode_id
                """, con)

            defaults = None
            if self.generate_defaults and not history.empty:
                history["date"] = pd.to_datetime(
                    history["date"], errors="coerce"
                )
                same_weekday = history[
                    history["date"].dt.dayofweek == self.weekday_num
                ]
                if self.existing_count < len(same_weekday) and not same_weekday.empty:
                    def most_common(column, fallback):
                        values = same_weekday[column].mode()
                        return values.iloc[0] if not values.empty else fallback

                    defaults = {
                        "classe": most_common("classe", ""),
                        "majal": most_common("majal", ""),
                        "heure": most_common("heure", "08:00-09:00"),
                        "time": most_common("time", "60 دقيقة"),
                        "namat": most_common("namat_taallom", "منهجي"),
                    }
            self.result_ready.emit(history, defaults)
        except (sqlite3.Error, pd.errors.DatabaseError) as exc:
            self.failed.emit(f"تعذر تحميل سجل الحصص السابقة: {exc}")
        finally:
            self.finished.emit()


class TimetableLoadWorker(QObject):
    result_ready = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, database_path, year_id):
        super().__init__()
        self.database_path = database_path
        self.year_id = year_id

    @Slot()
    def run(self):
        try:
            with connect_database(self.database_path, timeout=10) as con:
                history = pd.read_sql_query("""
                    SELECT COALESCE(g.group_key, c.name) AS classe,
                        m.name AS majal, h.heure, h.time,
                        h.wehda_number, h.hessa_number,
                        lm.name AS namat_taallom, d.date
                    FROM hessa h
                    JOIN day_data d ON h.day_data_id = d.id
                    JOIN classe c ON c.id = h.classe_id
                    LEFT JOIN class_group g ON g.id=h.class_group_id
                    LEFT JOIN majal m ON m.id=h.majal_id
                    LEFT JOIN learning_mode lm ON lm.id=h.learning_mode_id
                    WHERE d.ann_scolaire_id = ?
                """, con, params=(self.year_id,))
            group_labels = {
                group_key: level_firak_label(group_key)
                for group_key in LEVEL_FIRAKS
            }
            history["classe"] = history["classe"].map(
                lambda name: (
                    f"{name} — {group_labels[name]}"
                    if name in group_labels else name
                )
            )
            first_hour, last_hour = TimetableDialog.schedule_time_bounds(
                history
            )
            schedule = TimetableDialog.build_schedule(
                history, first_hour, last_hour
            )
            self.result_ready.emit(
                (schedule, first_hour, last_hour)
            )
        except (sqlite3.Error, pd.errors.DatabaseError, ValueError, TypeError) as exc:
            self.failed.emit(f"تعذر توليد جدول الأوقات: {exc}")
        finally:
            self.finished.emit()


class SchoolPlanner(QMainWindow):
    def __init__(self, db=None):
        super().__init__()
        self.db = db if db is not None else Database(DB_PATH)
        self.day_id = None
        self.selected_lesson_id = None
        self.selected_activity_id = None
        self.copied_details = []
        self.copied_lessons = []
        self.is_school_year_read_only = False
        
        self.setWindowTitle("المعين للتخطيط اليومي للحصص")
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        available_geometry = self.screen().availableGeometry()
        self.setMinimumSize(
            min(900, max(320, available_geometry.width() - 48)),
            min(600, max(240, available_geometry.height() - 80)),
        )
        self.resize(1250, 800)
        self.setLayoutDirection(Qt.RightToLeft)
        self.build_ui()
        self.load_school_info()

        self.db.cleanup_empty_days()
        workdays = self.db.recorded_workdays()
        if workdays:
            day_id, latest_date_str = workdays[0]
            qd = QDate.fromString(latest_date_str, "yyyy-MM-dd")
            if qd.isValid():
                self.date_edit.setDate(qd)
        
        self.load_day()
        if workdays:
            self.day_lessons_page.set_day(day_id, latest_date_str)
            self.pages_stack.setCurrentWidget(self.day_lessons_page)
        else:
            self.pages_stack.setCurrentWidget(self.lesson_page)
        self.load_statistics()
        self.setStyleSheet("QLineEdit { qproperty-alignment: AlignCenter; }")
        self.showMaximized()
        if not workdays:
            QTimer.singleShot(
                0,
                lambda: QMessageBox.information(
                    self,
                    "لا يوجد يوم عمل مسجل",
                    "لا يوجد يوم عمل مسجل بعد. تم فتح صفحة تصفح دفتر إعداد الدروس.",
                ),
            )
        self.setAcceptDrops(True)


    def build_ui(self):
        self._year_scoped_mutation_widgets = []
        self._year_scoped_mutation_actions = []
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        from PySide6.QtGui import QKeySequence, QShortcut
        self.competence_shortcut = QShortcut(QKeySequence("Ctrl+Shift+E"), self)
        self.competence_shortcut.activated.connect(self.open_competence_manager)
        self.concept_shortcut = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
        self.concept_shortcut.activated.connect(self.open_concept_manager)

        self.teacher_info_page = TeacherInfoPage(self)
        self.teacher_info_page.saveRequested.connect(self.save_school_info)

        agenda = QGroupBox("اليوم الدراسي")
        agenda_layout = QVBoxLayout(agenda)
        agenda_row = QHBoxLayout()
        agenda_layout.addLayout(agenda_row)
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dddd، dd/MM/yyyy")
        self.date_edit.setLocale(QLocale(QLocale.Arabic, QLocale.Tunisia))
        self.date_edit.setMinimumWidth(130)

        self.custom_calendar = CustomCalendarWidget(self.db, self.date_edit)
        self.date_edit.setCalendarWidget(self.custom_calendar)
        self.set_lesson_date_range()
        self.custom_calendar.set_holiday_date_disabling_enabled(True)
        if self.date_edit.date() in self.custom_calendar._disabled_dates:
            self.date_edit.setDate(self.custom_calendar._last_enabled_date)
        self._last_enabled_agenda_date = self.date_edit.date()
        self.date_edit.dateChanged.connect(self.on_agenda_date_changed)
        agenda_row.addWidget(self.date_edit)
        self.day_status = QLabel()
        self.day_status.setWordWrap(True)
        agenda_layout.addWidget(self.day_status)
        self.prev_btn = QPushButton("◀ اليوم السابق")
        self.prev_btn.setProperty("actionType", "browse")
        _configure_icon_button(
            self.prev_btn,
            QStyle.StandardPixmap.SP_ArrowLeft,
            "اليوم الدراسي السابق",
        )
        self.view_day_btn = QPushButton("عرض اليوم")
        self.view_day_btn.setObjectName("viewDayButton")
        self.view_day_btn.setProperty("actionType", "browse")
        _configure_icon_button(
            self.view_day_btn,
            QStyle.StandardPixmap.SP_FileDialogContentsView,
            "عرض اليوم المسجل",
        )
        self.view_day_btn.setEnabled(False)
        self.view_day_btn.clicked.connect(self.open_day_lessons_view)
        self.next_btn = QPushButton("اليوم التالي ▶")
        self.next_btn.setProperty("actionType", "browse")
        _configure_icon_button(
            self.next_btn,
            QStyle.StandardPixmap.SP_ArrowRight,
            "اليوم الدراسي التالي",
        )
        self.prev_btn.clicked.connect(self.show_previous_recorded_workday)
        self.next_btn.clicked.connect(self.show_next_recorded_workday)
        for button in (self.prev_btn, self.view_day_btn, self.next_btn):
            button.setFixedWidth(42)
            agenda_row.addWidget(button)

        self.lesson_loading_label = QLabel()
        self.lesson_loading_label.setAlignment(Qt.AlignCenter)
        self.lesson_loading_label.setStyleSheet(
            "color: #1E3A8A; background-color: #EFF6FF; "
            "border: 1px solid #BFDBFE; border-radius: 8px; "
            "padding: 6px; font-weight: bold;"
        )
        self.lesson_loading_label.hide()
        self.lesson_table = self.make_table(
            ["المعرّف", " الحصة", "القسم", "المجال ", "التوقيت",
             "المدة", " الوحدة", "نمط التعلم"]
        )
        self.lesson_table.setParent(self)
        self.lesson_table.setColumnHidden(0, True)
        self.lesson_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.lesson_table.itemSelectionChanged.connect(self.on_lesson_selected)
        self.lesson_table.hide()

        lower = QSplitter(Qt.Horizontal)

        act_box = QGroupBox("أنشطة الحصة المحددة")
        act_layout = QVBoxLayout(act_box)
        act_buttons = QHBoxLayout()

        for label, callback, act_type in [
            ("＋ إضافة نشاط", self.add_activity, "add"),
            ("🗑 مسح كل الأنشطة", self.clear_all_activities_for_lesson, "clear")
        ]:
            b = QPushButton(label)
            b.clicked.connect(callback)
            b.setProperty("actionType", act_type)
            act_buttons.addWidget(b)
            if act_type in {"add", "edit", "delete", "copy_paste", "clear"}:
                self._year_scoped_mutation_widgets.append(b)
            if label == "＋ إضافة نشاط":
                self.add_activity_button = b
                b.setEnabled(False)

        act_layout.addLayout(act_buttons)
        self.activity_cards = ActivityCardsWidget()
        self.activity_cards.actionRequested.connect(
            self.on_activity_card_action
        )
        self.activity_cards.itemSelectionChanged.connect(
            self.on_activity_selected
        )
        act_layout.addWidget(self.activity_cards, 1)

        lower.addWidget(act_box)

        detail_box = QGroupBox("خصائص النشاط المحدد (اسم الخاصية / قيمتها)")
        detail_layout = QVBoxLayout(detail_box)
        detail_buttons = QHBoxLayout()

        for label, callback, act_type in [
            ("＋ إضافة خاصية", self.add_detail, "add"),
            ("📋 نسخ الخصائص", self.copy_activity_details, "copy_paste"),
            ("📌 لصق الخصائص", self.paste_activity_details, "copy_paste"),
            ("🗑 مسح كل الخصائص", self.clear_all_details_for_activity, "clear")
        ]:
            b = QPushButton(label)
            b.clicked.connect(callback)
            b.setProperty("actionType", act_type)
            detail_buttons.addWidget(b)
            if act_type in {"add", "edit", "delete", "copy_paste", "clear"}:
                self._year_scoped_mutation_widgets.append(b)

        detail_layout.addLayout(detail_buttons)
        self.detail_cards = DetailCardsWidget()
        self.detail_cards.actionRequested.connect(
            self.on_detail_card_action
        )
        detail_layout.addWidget(self.detail_cards, 1)

        lower.addWidget(detail_box)
        lower.setStretchFactor(0, 1)
        lower.setStretchFactor(1, 1)
        lower.setSizes([300, 600])

        self.schedule_group = QGroupBox("حصص اليوم حسب جدول الأوقات")
        schedule_layout = QVBoxLayout(self.schedule_group)
        self.schedule_source_status = QLabel()
        self.schedule_source_status.setWordWrap(True)
        self.schedule_source_status.setStyleSheet("color: #64748B;")
        schedule_layout.addWidget(self.schedule_source_status)
        self.scheduled_lessons_list = ScheduledLessonsListWidget()
        self.scheduled_lessons_list.emptyCardActivated.connect(self.add_lesson)
        schedule_layout.addWidget(self.scheduled_lessons_list, 1)
        schedule_actions = QHBoxLayout()
        self.add_all_lessons_button = QPushButton("＋ إضافة كل الحصص")
        self.add_all_lessons_button.setProperty("actionType", "add")
        self.add_all_lessons_button.clicked.connect(self.add_all_lessons)
        self._year_scoped_mutation_widgets.append(
            self.add_all_lessons_button
        )
        schedule_actions.addWidget(self.add_all_lessons_button)

        self.paste_lessons_button = QPushButton("📌 لصق الحصة")
        self.paste_lessons_button.setToolTip("لصق الحصص المنسوخة في اليوم المحدد.")
        self.paste_lessons_button.setProperty("actionType", "copy_paste")
        self.paste_lessons_button.clicked.connect(self.paste_lessons)
        self.paste_lessons_button.setEnabled(bool(self.copied_lessons))
        self._year_scoped_mutation_widgets.append(
            self.paste_lessons_button
        )
        schedule_actions.addWidget(self.paste_lessons_button)

        self.clear_day_lessons_button = QPushButton("🗑 مسح اليوم")
        self.clear_day_lessons_button.setToolTip("مسح حصص اليوم المحدد.")
        self.clear_day_lessons_button.setProperty("actionType", "clear")
        self.clear_day_lessons_button.clicked.connect(
            self.clear_current_day_lessons
        )
        self._year_scoped_mutation_widgets.append(
            self.clear_day_lessons_button
        )
        schedule_actions.addWidget(self.clear_day_lessons_button)
        schedule_layout.addLayout(schedule_actions)

        schedule_column = QWidget()
        schedule_column_layout = QVBoxLayout(schedule_column)
        schedule_column_layout.setContentsMargins(0, 0, 0, 0)
        schedule_column_layout.addWidget(agenda)
        schedule_column_layout.addWidget(self.schedule_group, 1)

        current_section = QGroupBox("أنشطة الحصة وخصائص النشاط")
        current_section_layout = QVBoxLayout(current_section)
        current_section_layout.addWidget(self.lesson_loading_label)
        lesson_cards_hint = QLabel(
            "حصص اليوم — اسحب الجدول إلى هذه المساحة لإضافتها"
        )
        lesson_cards_hint.setStyleSheet(
            "color: #475569; font-weight: 600; padding: 3px;"
        )
        current_section_layout.addWidget(lesson_cards_hint)
        self.recorded_lesson_cards = RecordedLessonCardsWidget()
        self.recorded_lesson_cards.actionRequested.connect(
            self.on_lesson_card_action
        )
        self.recorded_lesson_cards.lessonsDropped.connect(
            self.add_scheduled_lessons
        )
        self.recorded_lesson_cards.itemSelectionChanged.connect(
            self.on_lesson_card_selection_changed
        )
        current_section_layout.addWidget(self.recorded_lesson_cards, 1)
        current_section_layout.addWidget(lower, 2)

        workspace = QSplitter(Qt.Horizontal)
        workspace.setLayoutDirection(Qt.RightToLeft)
        workspace.addWidget(schedule_column)
        workspace.addWidget(current_section)
        workspace.setStretchFactor(0, 1)
        workspace.setStretchFactor(1, 1)
        workspace.setSizes([300, 900])

        self.stats_page = QWidget()
        stats_page_layout = QVBoxLayout(self.stats_page)
        stats_page_layout.setContentsMargins(0, 0, 0, 0)
        self.stats_scroll_area = QScrollArea()
        self.stats_scroll_area.setWidgetResizable(True)
        self.stats_content = QWidget()
        stats_layout = QVBoxLayout(self.stats_content)
        stats_layout.setContentsMargins(24, 20, 24, 24)
        stats_layout.setSpacing(18)
        self.stats_scroll_area.setWidget(self.stats_content)
        stats_page_layout.addWidget(self.stats_scroll_area)

        stats_header_layout = QHBoxLayout()
        stats_title = QLabel("لوحة الإحصائيات والتوقعات")
        stats_title.setStyleSheet(
            "font-size: 22px; font-weight: 800; color: #0F172A;"
        )
        self.refresh_stats_btn = QPushButton("تحديث التحليلات")
        self.refresh_stats_btn.setProperty("primary", "true")
        self.refresh_stats_btn.clicked.connect(
            lambda: self.load_statistics(force_refresh=True)
        )
        stats_header_layout.addWidget(stats_title)
        stats_header_layout.addStretch()
        stats_header_layout.addWidget(self.refresh_stats_btn)
        stats_layout.addLayout(stats_header_layout)

        self.stats_loading_label = QLabel()
        self.stats_loading_label.setObjectName("statsLoadingLabel")
        self.stats_loading_label.setAlignment(Qt.AlignCenter)
        self.stats_loading_label.hide()
        stats_layout.addWidget(self.stats_loading_label)

        cards_grid = QGridLayout()
        cards_grid.setHorizontalSpacing(12)
        cards_grid.setVerticalSpacing(12)
        self.stats_cards = {}
        card_definitions = [
            ("lessons", "إجمالي الحصص", "0", "#2563EB"),
            ("days", "الأيام المسجلة", "0", "#0F766E"),
            ("classes", "الأقسام", "0", "#7C3AED"),
            ("activities", "الأنشطة", "0", "#D97706"),
            ("properties", "الخصائص المسجلة", "0", "#DB2777"),
            ("completion_rate", "اكتمال الحصص", "0%", "#059669"),
            ("average_activities", "متوسط الأنشطة للحصة", "0", "#0891B2"),
            ("average_properties", "متوسط الخصائص للنشاط", "0", "#9333EA"),
            ("forecast_total", "توقع الحصص للأربعة أسابيع", "0", "#EA580C"),
        ]
        for index, (key, title, value, accent) in enumerate(card_definitions):
            card = QFrame()
            card.setObjectName("statisticsCard")
            card.setStyleSheet(
                "QFrame#statisticsCard { background: #FFFFFF; "
                "border: 1px solid #E2E8F0; border-top: 4px solid "
                f"{accent}; border-radius: 12px; }}"
            )
            card_layout = QVBoxLayout(card)
            card_title = QLabel(title)
            card_title.setStyleSheet(
                "color: #64748B; font-size: 11pt; background: transparent;"
            )
            value_label = QLabel(value)
            value_label.setStyleSheet(
                f"color: {accent}; font-size: 23pt; font-weight: 800; "
                "background: transparent;"
            )
            card_layout.addWidget(card_title)
            card_layout.addWidget(value_label)
            self.stats_cards[key] = value_label
            cards_grid.addWidget(card, index // 3, index % 3)
        stats_layout.addLayout(cards_grid)

        charts_grid = QGridLayout()
        charts_grid.setHorizontalSpacing(14)
        charts_grid.setVerticalSpacing(14)
        self.stats_charts = {}
        chart_definitions = [
            ("subjects", "الحصص حسب المجال"),
            ("modes", "أنماط التعلّم"),
            ("properties", "استخدام الخصائص حسب النشاط"),
            ("weekly", "نشاط الحصص والتوقع للأربعة أسابيع القادمة"),
        ]
        for index, (key, title) in enumerate(chart_definitions):
            chart_box = QGroupBox(title)
            chart_box.setObjectName("statisticsChartCard")
            chart_layout = QVBoxLayout(chart_box)
            figure, axis = plt.subplots(figsize=(6.2, 3.6))
            figure.patch.set_facecolor("#FFFFFF")
            canvas = FigureCanvas(figure)
            canvas.setMinimumHeight(310)
            chart_layout.addWidget(canvas)
            self.stats_charts[key] = (figure, axis, canvas)
            charts_grid.addWidget(chart_box, index // 2, index % 2)
        stats_layout.addLayout(charts_grid)

        tables_grid = QGridLayout()
        self.stats_table = self.make_table(
            ["المجال", "الحصص", "النسبة"]
        )
        self.stats_activity_table = self.make_table(
            ["النشاط", "عدد مرات التسجيل"]
        )
        self.stats_property_table = self.make_table(
            ["النشاط", "اسم الخاصية", "الاستخدام", "أنشطة مختلفة"]
        )
        for table in (
            self.stats_table, self.stats_activity_table,
            self.stats_property_table,
        ):
            table.setMinimumHeight(240)
        tables_grid.addWidget(self._statistics_table_group(
            "توزيع المجالات", self.stats_table
        ), 0, 0)
        tables_grid.addWidget(self._statistics_table_group(
            "الأنشطة الأكثر استخداماً", self.stats_activity_table
        ), 0, 1)
        tables_grid.addWidget(self._statistics_table_group(
            "استعمال أسماء الخصائص حسب الأنشطة", self.stats_property_table
        ), 1, 0, 1, 2)
        stats_layout.addLayout(tables_grid)
        self.stats_cache_label = QLabel()
        self.stats_cache_label.setStyleSheet("color: #64748B;")
        stats_layout.addWidget(self.stats_cache_label)
        self.stats_revision_timer = QTimer(self)
        self.stats_revision_timer.setInterval(3000)
        self.stats_revision_timer.timeout.connect(
            self._check_statistics_revision
        )
        self.stats_revision_timer.start()

        self.official_programs_page = OfficialProgramsWidget(self.db, self)
        self.official_programs_browse_page = OfficialProgramsBrowsePage(self.db, self)

        self.competences_cba_page = CompetencesCbaBrowsePage(self.db, self)
        self.day_lessons_page = DayLessonsBrowsePage(
            self.db, parent=self, on_back=self.return_to_lesson_book
        )

        self.pages_stack = QStackedWidget()

        self.lesson_page = QWidget()
        lesson_page_layout = QVBoxLayout(self.lesson_page)
        lesson_page_layout.addWidget(workspace, 1)

        self.pages_stack.addWidget(self.lesson_page)
        self.pages_stack.addWidget(self.stats_page)
        self.pages_stack.addWidget(self.teacher_info_page)
        self.pages_stack.addWidget(self.official_programs_page)
        self.pages_stack.addWidget(self.official_programs_browse_page)
        self.pages_stack.addWidget(self.competences_cba_page)
        self.pages_stack.addWidget(self.day_lessons_page)

        self.timetable_page = TeacherTimetablePage(self.db, self)
        self.pages_stack.addWidget(self.timetable_page)

        self.plans_page = TeachingPlansPage(self.db, self)
        self.pages_stack.addWidget(self.plans_page)

        self.plan_view_page = TeachingPlanViewPage(self.db, self)
        self.pages_stack.addWidget(self.plan_view_page)

        self.official_holidays_page = OfficialHolidaysPage(self.db, self)
        self.pages_stack.addWidget(self.official_holidays_page)

        self.unofficial_holidays_page = OfficialHolidaysPage(
            self.db, self, holiday_type="unofficial"
        )
        self.pages_stack.addWidget(self.unofficial_holidays_page)

        self.assessment_file_management_page = (
            AssessmentFileManagementPage(self.db, self)
        )
        self.pages_stack.addWidget(
            self.assessment_file_management_page
        )

        self.lesson_book_settings_page = LessonBookSettingsPage(
            self.db,
            self.open_teacher_timetable_page,
            self.open_teaching_assignments,
            self.toggle_enseig_info,
            parent=self,
        )
        self.pages_stack.addWidget(self.lesson_book_settings_page)

        self._page_descriptions = {
            self.lesson_page: (
                "دفتر إعداد الدروس",
                "اختر يوماً لإضافة الحصص ومتابعة الأنشطة المسجلة.",
            ),
            self.stats_page: (
                "الإحصائيات",
                "ملخص استعمال الحصص والأنشطة والخصائص المسجلة.",
            ),
            self.teacher_info_page: (
                "بيانات المعلم",
                "بيانات المؤسسة والمسار المهني والتواصل للسنة الدراسية المحددة.",
            ),
            self.official_programs_page: (
                "إدارة البرامج الرسمية",
                "إدارة البرامج والكفايات المعتمدة في المؤسسة.",
            ),
            self.official_programs_browse_page: (
                "تصفح البرامج الرسمية",
                "ابحث في البرامج الرسمية حسب المستوى والمجال.",
            ),
            self.competences_cba_page: (
                "الكفايات والمفاهيم",
                "تصفح المفاهيم المرتبطة بالمقاربة بالكفايات.",
            ),
            self.day_lessons_page: (
                "الأيام المسجلة",
                "استعرض دفتر إعداد الدروس حسب اليوم الدراسي.",
            ),
            self.timetable_page: (
                "جدول أوقات المعلم",
                "أنشئ جدولًا آليًا من سجل العمل أو رتّب الحصص يدويًا.",
            ),
            self.plans_page: (
                "المخططات",
                "أنشئ المخططات التعليمية وتابع تقدم إنجازها.",
            ),
            self.plan_view_page: (
                "عرض المخطط",
                "تصفح محتوى المخطط بالتنسيق نفسه المستخدم في ملف PDF.",
            ),
            self.official_holidays_page: (
                "العطل الرسمية",
                "أدر فترات العطل المعتمدة في السنة الدراسية.",
            ),
            self.unofficial_holidays_page: (
                "إدارة العطل غير الرسمية",
                "سجّل غياب المعلم الطارئ بسبب لقاء تكويني أو عطلة مرضية أو استثنائية.",
            ),
            self.assessment_file_management_page: (
                "إدارة ملف التقييم والمتابعة",
                "إعداد وإدارة ملف التقييم والمتابعة.",
            ),
            self.lesson_book_settings_page: (
                "إعداد دفتر إعداد الدروس",
                "اضبط صفحات إعداد الدفتر والخصائص التي تظهر عند طباعة كل نمط تعلم.",
            ),
        }
        page_header = QFrame()
        page_header.setObjectName("pageHeader")
        page_header_layout = QVBoxLayout(page_header)
        page_header_layout.setContentsMargins(16, 10, 16, 10)
        page_header_layout.setSpacing(2)
        self.page_title_label = QLabel()
        self.page_title_label.setObjectName("pageTitle")
        self.page_description_label = QLabel()
        self.page_description_label.setObjectName("pageDescription")
        page_header_layout.addWidget(self.page_title_label)
        page_header_layout.addWidget(self.page_description_label)
        root.addWidget(page_header)
        root.addWidget(self.pages_stack, 1)

        footer = QHBoxLayout()
        self.export_hint = QLabel("تُحفظ التغييرات مباشرة في قاعدة البيانات.")
        self.export_hint.setObjectName("saveHint")
        footer.addWidget(self.export_hint, 1)
        root.addLayout(footer)

        self.statusBar().showMessage("تم الاتصال بقاعدة البيانات بنجاح.")
        toolbar = self.addToolBar("التنقل")
        toolbar.setObjectName("mainNavigationBar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        toolbar.setLayoutDirection(Qt.RightToLeft)
        toolbar.setToolTip("التنقل بين صفحات التطبيق")
        self._page_navigation_actions = []
        self.pages_stack.currentChanged.connect(self.update_page_navigation_actions)
        self.pages_stack.currentChanged.connect(self.update_page_header)

        navigation_spacer = QWidget(toolbar)
        navigation_spacer.setObjectName("navigationToolbarSpacer")
        navigation_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        toolbar.addWidget(navigation_spacer)

        lesson_book_menu = QMenu("دفتر إعداد الدروس", self)
        lesson_book_menu.setObjectName("appNavigationMenu")
        open_lesson_action = QAction("التصرف في الدفتر", self)
        open_lesson_action.setProperty("actionType", "browse")
        open_lesson_action.triggered.connect(lambda: self.pages_stack.setCurrentWidget(self.lesson_page))
        self.register_page_navigation_action(open_lesson_action, self.lesson_page)
        lesson_book_menu.addAction(open_lesson_action)
        view_day_action = QAction("عرض الأيام المسجلة بالدفتر", self)
        view_day_action.setProperty("actionType", "browse")
        view_day_action.triggered.connect(self.open_day_lessons_from_menu)
        self.register_page_navigation_action(view_day_action, self.day_lessons_page)
        lesson_book_menu.addAction(view_day_action)
        lesson_book_menu.addSeparator()

        toggle_action = QAction("بيانات المعلم", self)
        toggle_action.triggered.connect(self.toggle_enseig_info)
        self.register_page_navigation_action(
            toggle_action, self.teacher_info_page
        )

        export_pdf_action = QAction("تصدير إلى ملف PDF", self)
        export_pdf_action.triggered.connect(self.export_pdf)

        export_json_action = QAction("تصدير يوم بصيغة JSON", self)
        export_json_action.triggered.connect(self.export_day_to_json)

        import_json_action = QAction("استيراد يوم من JSON", self)
        import_json_action.triggered.connect(self.import_day_from_json)
        self._year_scoped_mutation_actions.append(import_json_action)

        export_year_action = QAction("تصدير سنة دراسية كاملة (JSON)", self)
        export_year_action.triggered.connect(self.export_all_days_for_year)

        import_year_action = QAction("استيراد سنة دراسية كاملة (JSON)", self)
        import_year_action.triggered.connect(self.import_all_years_from_json)
        self._year_scoped_mutation_actions.append(import_year_action)

        self.timetable_action = QAction("جدول أوقات المعلم", self)
        self.timetable_action.triggered.connect(
            self.open_teacher_timetable_page
        )
        self.register_page_navigation_action(
            self.timetable_action, self.timetable_page
        )

        lesson_book_menu.addAction(export_pdf_action)
        lesson_book_menu.addSeparator()
        lesson_book_menu.addAction(export_json_action)
        lesson_book_menu.addAction(import_json_action)
        lesson_book_menu.addSeparator()
        lesson_book_menu.addAction(export_year_action)
        lesson_book_menu.addAction(import_year_action)
        lesson_book_menu.addSeparator()
        clear_all_lessons_action = QAction(
            "مسح كل الحصص والأنشطة والخصائص", self
        )
        clear_all_lessons_action.setToolTip(
            "يتطلب إدخال رمز تحقق قبل مسح جميع البيانات."
        )
        clear_all_lessons_action.setProperty("actionType", "clear")
        clear_all_lessons_action.triggered.connect(
            self.clear_all_lessons_in_db
        )
        self.clear_all_lessons_action = clear_all_lessons_action
        self._year_scoped_mutation_actions.append(clear_all_lessons_action)
        lesson_book_menu.addAction(clear_all_lessons_action)

        center_menu_items(lesson_book_menu)
        lesson_book_button = QToolButton(self)
        lesson_book_button.setText("دفتر إعداد الدروس")
        lesson_book_button.setMenu(lesson_book_menu)
        lesson_book_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(lesson_book_button)

        school_year_menu = QMenu("السنة الدراسية", self)
        school_year_menu.setObjectName("appNavigationMenu")
        school_year_menu.addAction(toggle_action)
        school_year_menu.addAction(self.timetable_action)
        teaching_assignments_action = QAction(
            "تعيين الأقسام وأنشطة التدريس", self
        )
        teaching_assignments_action.triggered.connect(
            self.open_teaching_assignments
        )
        self._year_scoped_mutation_actions.append(
            teaching_assignments_action
        )
        school_year_menu.addAction(teaching_assignments_action)
        official_holidays_action = QAction("إدارة العطل الرسمية", self)
        official_holidays_action.setProperty("actionType", "browse")
        official_holidays_action.triggered.connect(
            lambda: self.pages_stack.setCurrentWidget(
                self.official_holidays_page
            )
        )
        self.register_page_navigation_action(
            official_holidays_action, self.official_holidays_page
        )
        school_year_menu.addAction(official_holidays_action)
        unofficial_holidays_action = QAction(
            "إدارة العطل غير الرسمية", self
        )
        unofficial_holidays_action.setProperty("actionType", "browse")
        unofficial_holidays_action.triggered.connect(
            lambda: self.pages_stack.setCurrentWidget(
                self.unofficial_holidays_page
            )
        )
        self.register_page_navigation_action(
            unofficial_holidays_action, self.unofficial_holidays_page
        )
        school_year_menu.addAction(unofficial_holidays_action)
        self.add_school_year_action = QAction("إضافة سنة دراسية جديدة", self)
        self.add_school_year_action.triggered.connect(self.add_school_year)
        self._year_scoped_mutation_actions.append(self.add_school_year_action)
        self.previous_school_years_action = QAction(
            "عرض السنوات الدراسية السابقة", self
        )
        self.previous_school_years_action.triggered.connect(
            self.show_previous_school_years
        )
        school_year_menu.addSeparator()
        school_year_menu.addAction(self.previous_school_years_action)
        school_year_menu.addAction(self.add_school_year_action)
        school_year_menu.aboutToShow.connect(
            self.update_add_school_year_action_state
        )
        center_menu_items(school_year_menu)
        school_year_button = QToolButton(self)
        school_year_button.setText("السنة الدراسية")
        school_year_button.setMenu(school_year_menu)
        school_year_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(school_year_button)
        self.update_add_school_year_action_state()

        prog_menu = QMenu("البرامج الرسمية", self)
        prog_menu.setObjectName("appNavigationMenu")
        browse_prog_action = QAction("تصفح البرامج الرسمية", self)
        browse_prog_action.setProperty("actionType", "browse")
        browse_prog_action.triggered.connect(self.open_official_programs_browser)
        self.register_page_navigation_action(
            browse_prog_action, self.official_programs_browse_page
        )
        prog_menu.addAction(browse_prog_action)
        open_prog_action = QAction("فتح إدارة البرامج الرسمية", self)
        open_prog_action.triggered.connect(lambda: self.pages_stack.setCurrentWidget(self.official_programs_page))
        self.register_page_navigation_action(open_prog_action, self.official_programs_page)
        prog_menu.addAction(open_prog_action)
        prog_menu.addSeparator()

        browse_cba_action = QAction("تصفح مفاهيم متصلة بالمقاربة بالكفايات", self)
        browse_cba_action.setProperty("actionType", "browse")
        browse_cba_action.triggered.connect(self.open_competences_cba_browser)
        self.register_page_navigation_action(browse_cba_action, self.competences_cba_page)
        prog_menu.addAction(browse_cba_action)

        prog_menu.addSeparator()

        self.export_prog_action = QAction("تصدير البرامج الرسمية كاملة (JSON)", self)
        self.export_prog_action.triggered.connect(self.export_programs_json)
        prog_menu.addAction(self.export_prog_action)

        self.import_prog_action = QAction("استيراد البرامج الرسمية من JSON", self)
        self.import_prog_action.triggered.connect(self.import_programs_json)
        prog_menu.addAction(self.import_prog_action)

        prog_menu.aboutToShow.connect(self.update_prog_actions_state)

        center_menu_items(prog_menu)
        prog_button = QToolButton(self)
        prog_button.setText("البرامج الرسمية")
        prog_button.setMenu(prog_menu)
        prog_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(prog_button)

        toolbar.addSeparator()
        stats_action = QAction("فتح لوحة الإحصائيات", self)
        stats_action.setText("الإحصائيات")
        stats_action.setStatusTip("عرض الإحصائيات والتوقعات")
        stats_action.triggered.connect(self.open_statistics_page)
        self.register_page_navigation_action(stats_action, self.stats_page)
        stats_button = QToolButton(self)
        stats_button.setObjectName("statisticsNavigationButton")
        stats_button.setDefaultAction(stats_action)
        toolbar.addWidget(stats_button)

        plans_action = QAction("فتح المخططات", self)
        plans_action.setText("إدارة المخططات")
        plans_action.setStatusTip("عرض المخططات التعليمية")
        plans_action.triggered.connect(lambda: self.pages_stack.setCurrentWidget(self.plans_page))
        self.register_page_navigation_action(plans_action, self.plans_page)
        plans_button = QToolButton(self)
        plans_button.setObjectName("plansNavigationButton")
        plans_button.setText("المخططات")
        plans_menu = QMenu("المخططات", self)
        plans_menu.setObjectName("appNavigationMenu")
        plans_menu.addAction(plans_action)
        view_plan_action = QAction("عرض المخطط", self)
        view_plan_action.setProperty("actionType", "browse")
        view_plan_action.setStatusTip("عرض المخطط بتنسيق PDF داخل التطبيق")
        view_plan_action.triggered.connect(
            lambda: self.pages_stack.setCurrentWidget(self.plan_view_page)
        )
        self.register_page_navigation_action(
            view_plan_action, self.plan_view_page
        )
        plans_menu.addAction(view_plan_action)
        center_menu_items(plans_menu)
        plans_button.setMenu(plans_menu)
        plans_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        toolbar.addWidget(plans_button)

        assessment_management_action = QAction(
            "إدارة ملف التقييم والمتابعة", self
        )
        assessment_management_action.setProperty("actionType", "browse")
        assessment_management_action.setStatusTip(
            "فتح صفحة إدارة ملف التقييم والمتابعة"
        )
        assessment_management_action.triggered.connect(
            lambda: self.pages_stack.setCurrentWidget(
                self.assessment_file_management_page
            )
        )
        self.register_page_navigation_action(
            assessment_management_action,
            self.assessment_file_management_page,
        )
        assessment_button = QToolButton(self)
        assessment_button.setObjectName("assessmentNavigationButton")
        assessment_button.setText("التقييم والمتابعة")
        assessment_menu = QMenu("التقييم والمتابعة", self)
        assessment_menu.setObjectName("appNavigationMenu")
        assessment_menu.addAction(assessment_management_action)
        center_menu_items(assessment_menu)
        assessment_button.setMenu(assessment_menu)
        assessment_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        toolbar.addWidget(assessment_button)

        settings_menu = QMenu("الإعدادات", self)
        settings_menu.setObjectName("appNavigationMenu")
        lesson_book_settings_action = QAction(
            "إعداد دفتر إعداد الدروس", self
        )
        lesson_book_settings_action.setProperty("actionType", "browse")
        lesson_book_settings_action.triggered.connect(
            lambda: self.pages_stack.setCurrentWidget(
                self.lesson_book_settings_page
            )
        )
        self.register_page_navigation_action(
            lesson_book_settings_action, self.lesson_book_settings_page
        )
        settings_menu.addAction(lesson_book_settings_action)
        center_menu_items(settings_menu)
        settings_button = QToolButton(self)
        settings_button.setText("الإعدادات")
        settings_button.setMenu(settings_menu)
        settings_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        toolbar.addWidget(settings_button)

        toolbar.addSeparator()
        about_menu = QMenu("حول التطبيق", self)
        about_menu.setObjectName("appNavigationMenu")
        about_action = QAction("معلومات التطبيق", self)
        about_action.setProperty("actionType", "browse")
        about_action.triggered.connect(self.show_about_dialog)
        about_menu.addAction(about_action)
        center_menu_items(about_menu)
        about_button = QToolButton(self)
        about_button.setText("حول التطبيق")
        about_button.setMenu(about_menu)
        about_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(about_button)

        navigation_spacer = QWidget(toolbar)
        navigation_spacer.setObjectName("navigationToolbarSpacer")
        navigation_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        toolbar.addWidget(navigation_spacer)

        self.update_page_header(self.pages_stack.currentIndex())

    @staticmethod
    def _statistics_table_group(title, table):
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.addWidget(table)
        return group

    def show_about_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("حول التطبيق")
        dialog.setObjectName("aboutApplicationDialog")
        dialog.setLayoutDirection(Qt.RightToLeft)
        dialog.setMinimumWidth(460)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 22, 22, 18)
        layout.setSpacing(14)

        header = QFrame()
        header.setStyleSheet(
            "QFrame { background-color: #EFF6FF; border: 1px solid #BFDBFE; "
            "border-radius: 14px; }"
        )
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(18, 18, 18, 18)
        title = QLabel("المعين للتخطيط اليومي للحصص")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "font-size: 19pt; font-weight: 800; color: #1E3A8A; "
            "background: transparent; border: none;"
        )
        description = QLabel("البرنامج المساعد التربوي للمعلم")
        description.setAlignment(Qt.AlignCenter)
        description.setStyleSheet(
            "font-size: 11pt; color: #475569; background: transparent; "
            "border: none;"
        )
        version = QLabel("الإصدار v 1.2")
        version.setAlignment(Qt.AlignCenter)
        version.setStyleSheet(
            "font-size: 10pt; font-weight: 700; color: #2563EB; "
            "background: transparent; border: none;"
        )
        header_layout.addWidget(title)
        header_layout.addWidget(description)
        header_layout.addWidget(version)
        layout.addWidget(header)

        developer = QLabel(
            "<b>المطور</b><br>بلال المزوغي"
        )
        phone = QLabel(
            '<b>الهاتف</b><br><a href="tel:20971271">20971271</a>'
        )
        email = QLabel(
            '<b>البريد الإلكتروني</b><br>'
            '<a href="mailto:mzbi9620@gmail.com">mzbi9620@gmail.com</a>'
        )
        for label in (developer, phone, email):
            label.setTextFormat(Qt.RichText)
            label.setTextInteractionFlags(Qt.TextBrowserInteraction)
            label.setOpenExternalLinks(True)
            label.setStyleSheet(
                "padding: 8px 12px; color: #334155; background: #FFFFFF; "
                "border: 1px solid #E2E8F0; border-radius: 8px;"
            )
            layout.addWidget(label)

        close_button = QPushButton("إغلاق")
        close_button.setProperty("actionType", "close")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)

        if self.styleSheet():
            dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_timetable_dialog(self):
        if getattr(self, "_timetable_thread", None) is not None:
            return
        self.timetable_action.setEnabled(False)
        self.statusBar().showMessage("في انتظار توليد البيانات ...")
        self._timetable_loading_year_id = self.db.year_id
        self._timetable_thread = QThread(self)
        self._timetable_worker = TimetableLoadWorker(
            self.db.path, self._timetable_loading_year_id
        )
        self._timetable_worker.moveToThread(self._timetable_thread)
        self._timetable_thread.started.connect(
            self._timetable_worker.run, Qt.QueuedConnection
        )
        self._timetable_worker.result_ready.connect(
            self._open_timetable_with_schedule
        )
        self._timetable_worker.failed.connect(self._timetable_load_failed)
        self._timetable_worker.finished.connect(self._timetable_thread.quit)
        self._timetable_worker.finished.connect(
            self._timetable_worker.deleteLater
        )
        self._timetable_thread.finished.connect(
            self._timetable_thread_finished
        )
        self._timetable_thread.start()

    @Slot(object)
    def _open_timetable_with_schedule(self, result):
        self.statusBar().clearMessage()
        if self._timetable_loading_year_id != self.db.year_id:
            return
        if getattr(self, "_timetable_refresh_pending", False):
            return
        schedule, first_hour, last_hour = result
        self.timetable_page.set_automatic_schedule(
            schedule, first_hour, last_hour
        )

    def open_teacher_timetable_page(self):
        self.timetable_page.set_school_year(self.db.year_id)
        self.timetable_page.set_read_only(self.is_school_year_read_only)
        self.pages_stack.setCurrentWidget(self.timetable_page)
        if getattr(self, "_timetable_thread", None) is None:
            self.open_timetable_dialog()

    def open_teaching_assignments(self):
        if not self.ensure_school_year_writable():
            return
        try:
            dialog = TeachingAssignmentsDialog(
                self.db, self.db.year_id, self
            )
        except (ValueError, sqlite3.Error) as error:
            QMessageBox.critical(
                self,
                "تعذر تحميل تعيينات التدريس",
                str(error),
            )
            return
        dialog.assignmentsChanged.connect(
            self.timetable_page.refresh_lesson_palette_from_database
        )
        dialog.exec()

    @Slot(str)
    def _timetable_load_failed(self, message):
        if getattr(self, "_timetable_refresh_pending", False):
            return
        if self._timetable_loading_year_id != self.db.year_id:
            return
        self.timetable_page.set_automatic_generation_failed(message)
        self.statusBar().showMessage(message)
        QMessageBox.critical(self, "تعذر توليد جدول الأوقات", message)

    @Slot()
    def _timetable_thread_finished(self):
        thread = self._timetable_thread
        loaded_year_id = self._timetable_loading_year_id
        self._timetable_loading_year_id = None
        self._timetable_worker = None
        self._timetable_thread = None
        if thread is not None:
            thread.deleteLater()
        self.timetable_action.setEnabled(True)
        if getattr(self, "_timetable_refresh_pending", False):
            self._timetable_refresh_pending = False
            self.open_timetable_dialog()
            return
        if (
            loaded_year_id != self.db.year_id
            and self.pages_stack.currentWidget() is self.timetable_page
        ):
            self.open_timetable_dialog()

    def open_official_programs_browser(self):
        self.official_programs_browse_page.load_levels()
        self.pages_stack.setCurrentWidget(self.official_programs_browse_page)

    def open_competences_cba_browser(self):
        self.pages_stack.setCurrentWidget(self.competences_cba_page)

    def register_page_navigation_action(self, action, page):
        self._page_navigation_actions.append((action, page))
        action.setEnabled(self.pages_stack.currentWidget() is not page)

    def open_statistics_page(self):
        self.pages_stack.setCurrentWidget(self.stats_page)
        self.load_statistics()

    def update_page_navigation_actions(self, current_index):
        current_page = self.pages_stack.widget(current_index)
        for action, page in self._page_navigation_actions:
            action.setEnabled(current_page is not page)

    def update_page_header(self, current_index):
        page = self.pages_stack.widget(current_index)
        title, description = self._page_descriptions.get(
            page, ("المعين للتخطيط اليومي للحصص", "")
        )
        self.page_title_label.setText(title)
        self.page_description_label.setText(description)

    def open_competence_manager(self):
        dlg = CompetenceManagerDialog(db=self.db, parent=self)
        dlg.setLayoutDirection(self.layoutDirection())
        if self.styleSheet():
            dlg.setStyleSheet(self.styleSheet())
        dlg.exec()

    def open_concept_manager(self):
        dlg = ConceptManagerDialog(db=self.db, parent=self)
        dlg.setLayoutDirection(self.layoutDirection())
        if self.styleSheet():
            dlg.setStyleSheet(self.styleSheet())
        dlg.exec()

    def load_statistics(self, force_refresh=False):
        if getattr(self, "_statistics_thread", None) is not None:
            if force_refresh:
                self._statistics_refresh_pending = True
            return

        waiting_text = (
            "جارٍ تحديث التحليلات..."
            if force_refresh else "جارٍ تحميل التحليلات..."
        )
        self.stats_loading_label.setText(waiting_text)
        self.stats_loading_label.show()
        self.refresh_stats_btn.setEnabled(False)
        self.stats_cache_label.setText(waiting_text)

        self._statistics_thread = QThread(self)
        self._statistics_worker = StatisticsLoadWorker(
            self.db.path, force_refresh=force_refresh
        )
        self._statistics_worker.moveToThread(self._statistics_thread)
        self._statistics_thread.started.connect(
            self._statistics_worker.run, Qt.QueuedConnection
        )
        self._statistics_worker.result_ready.connect(self._display_statistics)
        self._statistics_worker.failed.connect(self._statistics_load_failed)
        self._statistics_worker.finished.connect(self._statistics_thread.quit)
        self._statistics_worker.finished.connect(
            self._statistics_worker.deleteLater
        )
        self._statistics_thread.finished.connect(
            self._statistics_thread_finished
        )
        self._statistics_thread.start()

    @Slot(object, int, bool)
    def _display_statistics(self, payload, revision, from_cache):
        self.stats_loading_label.hide()
        self._statistics_source_revision = revision
        metrics = payload["metrics"]
        self.stats_cards["lessons"].setText(
            f"{metrics['lessons']:,}"
        )
        self.stats_cards["days"].setText(f"{metrics['days']:,}")
        self.stats_cards["classes"].setText(f"{metrics['classes']:,}")
        self.stats_cards["activities"].setText(
            f"{metrics['activities']:,}"
        )
        self.stats_cards["properties"].setText(
            f"{metrics['properties']:,}"
        )
        self.stats_cards["completion_rate"].setText(
            f"{metrics['completion_rate']:.1f}%"
        )
        self.stats_cards["average_activities"].setText(
            str(metrics["average_activities"])
        )
        self.stats_cards["average_properties"].setText(
            str(metrics["average_properties"])
        )
        forecast_total = sum(
            item["lessons"] for item in payload["forecast"]
        )
        self.stats_cards["forecast_total"].setText(str(forecast_total))
        cache_state = "من قاعدة التحليلات المحلية" if from_cache else "تم تحديثها الآن"
        self.stats_cache_label.setText(
            f"{cache_state} — نسخة البيانات {revision} — "
            f"نسبة اكتمال الحصص {metrics['completion_rate']:.1f}%"
        )

        self._fill_statistics_table(
            self.stats_table,
            ["المجال", "الحصص", "النسبة"],
            [
                (
                    item["name"], item["count"],
                    f"{item['count'] / metrics['lessons'] * 100:.1f}%"
                    if metrics["lessons"] else "0%",
                )
                for item in payload["subjects"]
            ],
        )
        self._fill_statistics_table(
            self.stats_activity_table,
            ["النشاط", "عدد مرات التسجيل"],
            [(item["name"], item["count"]) for item in payload["activities"]],
        )
        self._fill_statistics_table(
            self.stats_property_table,
            ["النشاط", "اسم الخاصية", "الاستخدام", "أنشطة مختلفة"],
            [
                (
                    item["activity"], item["property"], item["uses"],
                    item["activities"],
                )
                for item in payload["property_usage"]
            ],
        )
        self._draw_statistics_charts(payload)

    @staticmethod
    def _fill_statistics_table(table, headers, rows):
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row_index, column, item)
        table.resizeRowsToContents()

    def _draw_statistics_charts(self, payload):
        font = load_custom_font()
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [
            font, "Noto Naskh Arabic", "Arial", "DejaVu Sans"
        ]
        palette = [
            "#2563EB", "#0F766E", "#7C3AED", "#D97706",
            "#DB2777", "#0891B2", "#65A30D", "#EA580C",
        ]

        figure, axis, canvas = self.stats_charts["subjects"]
        axis.clear()
        subjects = payload["subjects"][:10]
        if subjects:
            names = [item["name"] for item in reversed(subjects)]
            counts = [item["count"] for item in reversed(subjects)]
            axis.barh(names, counts, color=palette[:len(names)])
            axis.set_xlabel("عدد الحصص")
            axis.grid(axis="x", alpha=0.2)
        else:
            axis.text(0.5, 0.5, "لا توجد بيانات بعد", ha="center", va="center")
        axis.spines[["top", "right", "left"]].set_visible(False)
        figure.tight_layout()
        canvas.draw_idle()

        figure, axis, canvas = self.stats_charts["modes"]
        axis.clear()
        modes = payload["learning_modes"]
        if modes and sum(item["count"] for item in modes):
            axis.pie(
                [item["count"] for item in modes],
                labels=[item["name"] for item in modes],
                autopct="%1.0f%%", startangle=90,
                colors=palette[:len(modes)],
                wedgeprops={"width": 0.42, "edgecolor": "#FFFFFF"},
            )
            axis.axis("equal")
        else:
            axis.text(0.5, 0.5, "لا توجد بيانات بعد", ha="center", va="center")
        figure.tight_layout()
        canvas.draw_idle()

        figure, axis, canvas = self.stats_charts["properties"]
        axis.clear()
        property_usage = payload["property_usage"][:10]
        if property_usage:
            labels = [
                f"{item['activity']} — {item['property']}"
                for item in reversed(property_usage)
            ]
            counts = [
                item["uses"] for item in reversed(property_usage)
            ]
            axis.barh(labels, counts, color="#DB2777")
            axis.set_xlabel("عدد القيم المسجلة")
            axis.grid(axis="x", alpha=0.2)
            axis.tick_params(axis="y", labelsize=8)
        else:
            axis.text(0.5, 0.5, "لا توجد خصائص مسجلة", ha="center", va="center")
        axis.spines[["top", "right", "left"]].set_visible(False)
        figure.tight_layout()
        canvas.draw_idle()

        figure, axis, canvas = self.stats_charts["weekly"]
        axis.clear()
        weekly = payload["weekly"]
        forecast = payload["forecast"]
        if weekly:
            historical_labels = [item["week"] for item in weekly]
            historical_counts = [item["lessons"] for item in weekly]
            axis.plot(
                historical_labels, historical_counts,
                color="#2563EB", marker="o", linewidth=2,
                label="الحصص المسجلة",
            )
            if forecast:
                forecast_labels = [item["week"] for item in forecast]
                forecast_counts = [item["lessons"] for item in forecast]
                axis.plot(
                    [historical_labels[-1], *forecast_labels],
                    [historical_counts[-1], *forecast_counts],
                    color="#EA580C", marker="o", linestyle="--",
                    linewidth=2, label="التوقع",
                )
            axis.tick_params(axis="x", labelrotation=35, labelsize=8)
            axis.grid(axis="y", alpha=0.2)
            axis.legend(frameon=False)
        else:
            axis.text(0.5, 0.5, "لا توجد بيانات زمنية", ha="center", va="center")
        axis.spines[["top", "right"]].set_visible(False)
        figure.tight_layout()
        canvas.draw_idle()

    @Slot(str)
    def _statistics_load_failed(self, message):
        self.stats_loading_label.setText(message)
        self.stats_loading_label.show()
        self.stats_cache_label.setText("فشل تحديث التحليلات")
        QMessageBox.critical(self, "تعذر تحميل الإحصائيات", message)

    @Slot()
    def _statistics_thread_finished(self):
        thread = self._statistics_thread
        self._statistics_worker = None
        self._statistics_thread = None
        if thread is not None:
            thread.deleteLater()
        self.refresh_stats_btn.setEnabled(True)
        if getattr(self, "_statistics_refresh_pending", False):
            self._statistics_refresh_pending = False
            self.load_statistics(force_refresh=True)

    def _check_statistics_revision(self):
        if self.pages_stack.currentWidget() is not self.stats_page:
            return
        if getattr(self, "_statistics_thread", None) is not None:
            return
        try:
            revision = self.db.analytics_revision()
        except sqlite3.Error as exc:
            self.statusBar().showMessage(
                f"تعذر فحص تحديثات الإحصائيات: {exc}", 5000
            )
            return
        if revision != getattr(self, "_statistics_source_revision", None):
            self.load_statistics()

    def toggle_enseig_info(self):
        self.load_school_info()
        self.pages_stack.setCurrentWidget(self.teacher_info_page)

    def update_add_school_year_action_state(self):
        if not hasattr(self, "add_school_year_action"):
            return
        self.add_school_year_action.setEnabled(
            not self.is_school_year_read_only
            and self.db.year_id != self.db.current_school_year_id()
        )

    def ensure_school_year_writable(self):
        if not self.is_school_year_read_only:
            return True
        QMessageBox.information(
            self, "السنة الدراسية للعرض فقط",
            "السنة الدراسية المحددة سابقة؛ يمكنك تصفحها وتصدير بياناتها فقط."
        )
        return False

    def set_lesson_date_range(self, school_year_name=None):
        if school_year_name is None:
            school_info = self.db.school_info()
            if not school_info:
                raise ValueError("بيانات السنة الدراسية المحددة غير موجودة.")
            school_year_name = school_info[0]
        try:
            start_year_text, end_year_text = str(school_year_name).split("-", 1)
            start_year = int(start_year_text)
            end_year = int(end_year_text)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"اسم السنة الدراسية غير صالح: {school_year_name}"
            ) from error
        if end_year != start_year + 1:
            raise ValueError(
                f"اسم السنة الدراسية غير صالح: {school_year_name}"
            )
        first_day = QDate(start_year, 9, 1)
        last_day = QDate(end_year, 6, 30)
        self.date_edit.setDateRange(first_day, last_day)
        self.custom_calendar.setDateRange(first_day, last_day)
        if getattr(
            self.custom_calendar, "_holiday_date_disabling_enabled", False
        ):
            self.custom_calendar.refresh_holiday_dates()

    def on_agenda_date_changed(self, *_args):
        selected_date = self.date_edit.date()
        if selected_date in self.custom_calendar._disabled_dates:
            self.custom_calendar.refresh_holiday_dates()
            if (
                self._last_enabled_agenda_date
                in self.custom_calendar._disabled_dates
            ):
                self._last_enabled_agenda_date = (
                    self.custom_calendar._nearest_enabled_date(selected_date)
                )
            self.date_edit.blockSignals(True)
            self.date_edit.setDate(self._last_enabled_agenda_date)
            self.date_edit.blockSignals(False)
            self.custom_calendar.setSelectedDate(
                self._last_enabled_agenda_date
            )
            self.statusBar().showMessage(
                "لا يمكن اختيار يوم عطلة رسمية.", 3000
            )
            return
        self._last_enabled_agenda_date = selected_date
        self.load_day()

    def set_school_year_read_only(self, read_only):
        self.is_school_year_read_only = bool(read_only)
        for widget in self._year_scoped_mutation_widgets:
            widget.setEnabled(not self.is_school_year_read_only)
        self.update_paste_lessons_button_state()
        for action in self._year_scoped_mutation_actions:
            action.setEnabled(not self.is_school_year_read_only)
        if hasattr(self, "teacher_info_page"):
            self.teacher_info_page.set_read_only(
                self.is_school_year_read_only
            )
        if hasattr(self, "detail_cards"):
            self.detail_cards.set_read_only(self.is_school_year_read_only)
        if hasattr(self, "activity_cards"):
            self.activity_cards.set_read_only(self.is_school_year_read_only)
        self.update_add_school_year_action_state()
        self._update_add_activity_button()
        if hasattr(self, "timetable_page"):
            self.timetable_page.set_read_only(
                self.is_school_year_read_only
            )

    def show_previous_school_years(self):
        if getattr(self, "_lesson_history_thread", None) is not None:
            return
        dlg = PreviousSchoolYearsDialog(
            self.db, selected_year_id=self.db.year_id, parent=self
        )
        if not dlg.exec():
            return
        year_id, year_name = dlg.selected_year()
        if not year_id:
            QMessageBox.warning(self, "السنة الدراسية", "لم يتم اختيار سنة دراسية.")
            return

        workdays = self.db.recorded_workdays(year_id)
        if workdays:
            day_id, day_text = workdays[0]
        else:
            try:
                start_year = int(str(year_name).split("-", 1)[0])
            except (TypeError, ValueError):
                QMessageBox.warning(
                    self, "السنة الدراسية غير صالحة",
                    f"تعذر تحديد تاريخ بداية السنة الدراسية: {year_name}"
                )
                return
            day_id = None
            day_text = f"{start_year:04d}-09-01"

        selected_date = QDate.fromString(day_text, "yyyy-MM-dd")
        if not selected_date.isValid():
            QMessageBox.warning(
                self, "تاريخ السنة الدراسية غير صالح",
                f"تعذر عرض التاريخ: {day_text}"
            )
            return

        self.db.year_id = year_id
        self.set_lesson_date_range(year_name)
        self.timetable_page.set_school_year(year_id)
        self.set_school_year_read_only(
            year_id != self.db.current_school_year_id()
        )
        self.load_school_info()
        self.day_lessons_page.calendar.year_id = year_id
        self.day_lessons_page.calendar.refresh_recorded_dates()
        self.date_edit.setDate(selected_date)
        self.load_day()
        self.day_lessons_page.set_day(day_id, day_text)
        self.pages_stack.setCurrentWidget(self.day_lessons_page)
        self.statusBar().showMessage(
            f"السنة الدراسية {year_name}"
            + (" — للعرض والتصدير فقط" if self.is_school_year_read_only else ""),
            5000,
        )

    def add_school_year(self):
        if not self.ensure_school_year_writable():
            return
        new_year_name = self.db.school_year_name_for_date()
        school_info = self.db.school_info()
        if not school_info:
            QMessageBox.warning(
                self, "تعذر إضافة السنة الدراسية",
                "لم يتم العثور على بيانات السنة الدراسية الحالية."
            )
            return
        if str(school_info[0] or "").strip() == new_year_name:
            self.update_add_school_year_action_state()
            return

        dialog_values = _school_info_to_dialog_values(list(school_info))
        dialog_values[0] = new_year_name
        dlg = EntryDialog(
            "إضافة سنة دراسية جديدة", SCHOOL_INFO_DIALOG_FIELDS,
            values=dialog_values, parent=self
        )
        dlg.widgets[0].setReadOnly(True)
        if not dlg.exec():
            return

        values = _school_info_from_dialog_values(dlg.values())
        values[0] = new_year_name
        try:
            new_year_id = self.db.create_school_year(values)
        except ValueError as exc:
            QMessageBox.warning(self, "تعذر إضافة السنة الدراسية", str(exc))
            self.update_add_school_year_action_state()
            return
        except sqlite3.Error as exc:
            QMessageBox.critical(
                self, "تعذر إضافة السنة الدراسية",
                f"حدث خطأ أثناء حفظ بيانات السنة الجديدة:\n{exc}"
            )
            return

        self.db.year_id = new_year_id
        self.timetable_page.set_school_year(new_year_id)
        self.set_lesson_date_range(new_year_name)
        self.load_school_info()

        start_year = int(new_year_name.split("-", 1)[0])
        first_school_day = QDate(start_year, 9, 1)
        self.date_edit.setDate(first_school_day)
        self.load_day()
        self.day_lessons_page.set_day(
            None, first_school_day.toString("yyyy-MM-dd")
        )
        self.pages_stack.setCurrentWidget(self.lesson_page)
        self.update_add_school_year_action_state()
        self.statusBar().showMessage(
            f"تمت إضافة السنة الدراسية {new_year_name} مع الإبقاء على بيانات السنة السابقة.",
            5000,
        )
        QMessageBox.information(
            self, "تمت إضافة السنة الدراسية",
            f"أُضيفت السنة الدراسية {new_year_name}، "
            "ونُسخت إليها بيانات المعلم دون تغيير بيانات السنة السابقة."
        )

    def make_table(self, headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setMinimumHeight(120)
        return table

    def load_school_info(self):
        row = self.db.school_info()
        values = list(row) if row else [
            self.db.school_year_name_for_date(),
            "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", 0,
        ]
        self.teacher_info_page.set_values(
            _school_info_to_dialog_values(values)
        )

    def save_school_info(self):
        if not self.ensure_school_year_writable():
            return
        values = _school_info_from_dialog_values(
            self.teacher_info_page.values()
        )
        if not values[0]:
            self.statusBar().showMessage(
                "أدخل السنة الدراسية ليتم حفظ بيانات المعلم.", 3000
            )
            return
        try:
            self.db.update_school_info(values)
        except (sqlite3.Error, ValueError, TypeError) as exc:
            QMessageBox.critical(
                self,
                "تعذر حفظ بيانات المعلم",
                f"حدث خطأ أثناء حفظ البيانات:\n{exc}",
            )
            return
        self.statusBar().showMessage(
            "تم حفظ بيانات المعلم والمؤسسة تلقائيا.", 3000
        )

    def load_day(self, *_):
        qd = self.date_edit.date()
        day = qd.toString("yyyy-MM-dd")
        if self.is_school_year_read_only:
            self.day_id = self.db.recorded_day_id(day)
        else:
            self.day_id = self.db.get_day_id(day)
        self.selected_lesson_id = None
        self.selected_activity_id = None

        if hasattr(self, 'custom_calendar'):
            self.custom_calendar.refresh_recorded_dates()
            self.custom_calendar.update()
        
        self.activity_cards.clear()
        self.detail_cards.clear()
        self.refresh_lessons()
        self.day_status.setText(f"التاريخ: {day} — عدد الحصص: {self.lesson_table.rowCount()}")

    def fill_table(self, table, rows):
        table.setUpdatesEnabled(False)
        try:
            table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                for column, value in enumerate(row):
                    item = QTableWidgetItem(
                        "" if value is None else str(value)
                    )
                    if column == 0:
                        item.setData(Qt.UserRole, value)
                    table.setItem(row_index, column, item)
            table.resizeRowsToContents()
        finally:
            table.setUpdatesEnabled(True)
            table.viewport().update()

    def current_id(self, table):
        if isinstance(table, QListWidget):
            item = table.currentItem()
            value = item.data(Qt.UserRole) if item is not None else None
            return int(value) if value is not None else None
        row = table.currentRow()
        if row < 0:
            return None
        item = table.item(row, 0)
        return int(item.text()) if item else None

    def refresh_lessons(self, select_id=None, select_first=False):
        rows = self.db.lessons(self.day_id)
        group_labels = {
            group_key: group["level_label"]
            for group_key, group in self.db.class_groups().items()
        }
        display_rows = [
            (
                *row[:2],
                (
                    f"{row[2]} — {group_labels[row[2]]}"
                    if row[2] in group_labels else row[2]
                ),
                *row[3:],
            )
            for row in rows
        ]
        self.fill_table(self.lesson_table, display_rows)
        manual_heading_names = self.refresh_scheduled_lessons(rows)
        plan_progress = {
            int(row[0]): progress
            for row in rows
            if (
                progress := self.db.lesson_plan_progress(row[0])
            ) is not None
        }
        repeat_lesson_statuses = self.db.repeat_request_statuses_for_day(
            self.day_id
        )
        self.recorded_lesson_cards.set_lessons(
            rows,
            heading_names=manual_heading_names,
            plan_progress=plan_progress,
            repeat_lesson_statuses=repeat_lesson_statuses,
        )
        self._update_workday_navigation_buttons()
        self.view_day_btn.setEnabled(bool(rows))
        self.day_status.setText(f"التاريخ: {self.date_edit.date().toString('yyyy-MM-dd')} — عدد الحصص: {len(rows)}")
        
        selected_row = -1
        if select_id:
            for r in range(self.lesson_table.rowCount()):
                if int(self.lesson_table.item(r, 0).text()) == select_id:
                    selected_row = r
                    break
        elif select_first and self.lesson_table.rowCount() > 0:
            selected_row = 0

        self.lesson_table.blockSignals(True)
        self.lesson_table.clearSelection()
        if selected_row >= 0:
            self.lesson_table.setCurrentCell(selected_row, 0)
            self.lesson_table.selectRow(selected_row)
        else:
            self.lesson_table.setCurrentCell(-1, -1)
        self.lesson_table.blockSignals(False)
        self.recorded_lesson_cards.blockSignals(True)
        self.recorded_lesson_cards.select_lesson(
            rows[selected_row][0] if selected_row >= 0 else None
        )
        self.recorded_lesson_cards.blockSignals(False)
        self.on_lesson_selected()
        self._refresh_lesson_completion_colors()

    def refresh_scheduled_lessons(self, day_lessons=None):
        weekday_num = self.date_edit.date().dayOfWeek() - 1
        target_day = self.date_edit.date().toString("yyyy-MM-dd")
        try:
            if day_lessons is None:
                day_lessons = (
                    self.db.lessons(self.day_id) if self.day_id else []
                )
            work_bounds = self._teacher_work_bounds()
            has_free_time = (
                True if work_bounds is None
                else _has_unoccupied_work_time(
                    day_lessons, *work_bounds
                )
            )
            manual_lessons = (
                self.db.manual_timetable_lessons_for_weekday(weekday_num)
            )
            if manual_lessons:
                lessons = manual_lessons
                source_label = "الجدول اليدوي"
                manual_heading_names = _manual_lesson_activity_names(
                    day_lessons, lessons
                )
            else:
                lessons = self.db.lessons_for_weekday(
                    weekday_num, target_day
                )
                source_label = "الجدول الآلي من سجل الحصص"
                manual_heading_names = {}
            occupied_intervals = set()
            disabled_candidate_intervals = set()
            for lesson in day_lessons:
                occupied_intervals.add(_parse_hour_interval(lesson[4]))
            for lesson in lessons:
                interval = _parse_hour_interval(lesson[2])
                if (
                    interval in occupied_intervals
                    or not _hour_interval_is_available(
                        lesson[2], day_lessons, work_bounds
                    )
                ):
                    disabled_candidate_intervals.add(interval)
            self.scheduled_lessons_list.set_lessons(
                lessons,
                disabled_intervals=disabled_candidate_intervals,
                allow_manual_add=(
                    has_free_time and not self.is_school_year_read_only
                ),
                recorded_lessons=day_lessons,
            )
            available_scheduled_lessons = any(
                _hour_interval_is_available(
                    lesson[2], day_lessons, work_bounds
                )
                for lesson in lessons
            )
            self.add_all_lessons_button.setEnabled(
                available_scheduled_lessons
                and not self.is_school_year_read_only
            )
            self.schedule_source_status.setText(
                (
                    f"المصدر: {source_label}"
                    if has_free_time else
                    "امتلأت ساعات العمل المحددة في جدول المعلم."
                ) if lessons
                else (
                    "لا توجد حصص في الجدول لهذا اليوم؛ " +
                    (
                        "يمكنك إضافة حصة يدويًا من البطاقة الأخيرة."
                        if has_free_time else
                        "لا توجد فترة عمل شاغرة لإضافة حصة."
                    )
                )
            )
            return manual_heading_names
        except (sqlite3.Error, ValueError) as exc:
            self.scheduled_lessons_list.set_lessons([])
            self.add_all_lessons_button.setEnabled(False)
            self.schedule_source_status.setText(
                "تعذر تحميل حصص جدول الأوقات."
            )
            QMessageBox.critical(
                self,
                "تعذر تحميل جدول الأوقات",
                f"حدث خطأ أثناء تحميل حصص اليوم:\n{exc}",
            )
            return {}

    def _teacher_work_bounds(self):
        schedule = self.db.teacher_timetable()
        if schedule is None:
            return 7 * 60, 17 * 60

        try:
            start, end = _parse_hour_interval(
                f"{schedule.get('start_time', '')}-"
                f"{schedule.get('end_time', '')}"
            )
        except ValueError as exc:
            raise ValueError(
                "نطاق ساعات العمل في جدول المعلم غير صالح."
            ) from exc
        return start, end


    def _has_free_timetable_time(self):
        work_bounds = self._teacher_work_bounds()
        if work_bounds is None:
            return True
        lessons = self.db.lessons(self.day_id) if self.day_id else []
        return _has_unoccupied_work_time(lessons, *work_bounds)

    def _validate_new_lesson_intervals(self, lessons, existing_lessons):
        work_bounds = self._teacher_work_bounds()
        occupied = list(existing_lessons)
        for lesson in lessons:
            start, end = _parse_hour_interval(lesson[2])
            lesson_duration = duration_minutes(lesson[3])
            if (
                lesson_duration is not None
                and end - start != lesson_duration
            ):
                return False
            if not _hour_interval_is_available(
                lesson[2], occupied, work_bounds
            ):
                return False
            occupied.append((None, None, None, None, lesson[2]))
        return True

    def _update_workday_navigation_buttons(self):
        selected_day = self.date_edit.date().toString("yyyy-MM-dd")
        recorded_days = [
            day for _, day in self.db.recorded_workdays()
            if day
        ]
        self.prev_btn.setEnabled(any(day < selected_day for day in recorded_days))
        self.next_btn.setEnabled(any(day > selected_day for day in recorded_days))

    def _navigate_recorded_workday(self, direction):
        selected_day = self.date_edit.date().toString("yyyy-MM-dd")
        recorded_days = sorted(
            day for _, day in self.db.recorded_workdays() if day
        )
        if direction < 0:
            candidates = [day for day in recorded_days if day < selected_day]
            target_day = candidates[-1] if candidates else None
        else:
            candidates = [day for day in recorded_days if day > selected_day]
            target_day = candidates[0] if candidates else None

        if target_day is not None:
            self.date_edit.setDate(QDate.fromString(target_day, "yyyy-MM-dd"))
        self._update_workday_navigation_buttons()

    def show_previous_recorded_workday(self):
        self._navigate_recorded_workday(-1)

    def show_next_recorded_workday(self):
        self._navigate_recorded_workday(1)

    def open_day_lessons_view(self):
        try:
            if not self.day_id or not self.db.lessons(self.day_id):
                self.view_day_btn.setEnabled(False)
                return

            day_text = self.date_edit.date().toString("yyyy-MM-dd")
            self.day_lessons_page.set_day(self.day_id, day_text)
            self.pages_stack.setCurrentWidget(self.day_lessons_page)
        except (sqlite3.Error, RuntimeError) as exc:
            QMessageBox.critical(
                self, "تعذر عرض حصص اليوم",
                f"حدث خطأ أثناء تحميل بيانات اليوم:\n{exc}"
            )

    def return_to_lesson_book(self):
        selected_date = QDate.fromString(
            self.day_lessons_page.day_text, "yyyy-MM-dd"
        )
        if not selected_date.isValid():
            QMessageBox.warning(
                self,
                "تاريخ غير صالح",
                "تعذر الرجوع إلى دفتر الدروس لأن تاريخ اليوم المعروض غير صالح.",
            )
            return
        self.date_edit.setDate(selected_date)
        self.load_day()
        self.pages_stack.setCurrentWidget(self.lesson_page)

    def open_day_lessons_from_menu(self):
        day_text = self.date_edit.date().toString("yyyy-MM-dd")
        try:
            day_id = self.db.recorded_day_id(day_text)
            self.day_lessons_page.set_day(day_id, day_text)
            self.pages_stack.setCurrentWidget(self.day_lessons_page)
        except (sqlite3.Error, RuntimeError) as exc:
            QMessageBox.critical(
                self, "تعذر عرض حصص اليوم",
                f"حدث خطأ أثناء تحميل بيانات اليوم:\n{exc}"
            )

    def refresh_activities(self, select_id=None):
        if not self.selected_lesson_id:
            self.activity_cards.clear()
            self.selected_activity_id = None
            self.detail_cards.clear()
            self._update_add_activity_button()
            self._refresh_lesson_completion_colors()
            return
        self.db.ensure_lesson_activities(self.selected_lesson_id)
        activities = self.db.activities(self.selected_lesson_id)
        class_names = {
            class_id: class_name
            for class_id, class_name, _level_name
            in self.db.lesson_classes(self.selected_lesson_id)
        }
        activity_scopes = self.db.activity_scopes(self.selected_lesson_id)
        displayed_activities = []
        for activity_id, title, duration in activities:
            class_id = activity_scopes.get(activity_id)
            display_title = (
                f"{title} — {class_names[class_id]}"
                if class_id in class_names else title
            )
            displayed_activities.append(
                (activity_id, display_title, duration)
            )
        completion_data = self.db.activity_completion_data(
            (self.selected_lesson_id,)
        ).get(self.selected_lesson_id, {})
        learning_mode = completion_data.get("learning_mode", "")
        details_by_id = completion_data.get("activities", {})
        issues_by_id = {
            activity[0]: self._activity_completion_issues(
                details_by_id.get(activity[0], {}).get("details", []),
                learning_mode,
            )
            for activity in activities
        }
        self.activity_cards.blockSignals(True)
        self.activity_cards.set_activities(
            displayed_activities, issues_by_id, select_id
        )
        self.activity_cards.blockSignals(False)
        self.selected_activity_id = self.current_id(self.activity_cards)
        self._refresh_lesson_completion_colors()
        self._update_add_activity_button()
        self.refresh_details()

    @staticmethod
    def _duration_minutes(value):
        return duration_minutes(value)

    @staticmethod
    def _required_activity_properties(learning_mode):
        return required_activity_properties(learning_mode)

    def _activity_completion_issues(self, details, learning_mode):
        return activity_completion_issues(details, learning_mode)

    @staticmethod
    def _set_completion_row_style(table, row, issues, item_type):
        if issues:
            background = QColor("#FEE2E2")
            foreground = QColor("#991B1B")
            state_text = "غير مكتملة"
            tooltip = f"{state_text}: " + "؛ ".join(issues)
        else:
            background = QColor("#DCFCE7")
            foreground = QColor("#166534")
            state_text = "مكتملة"
            tooltip = f"{item_type} {state_text} وفق الشروط المطلوبة."

        for column in range(table.columnCount()):
            item = table.item(row, column)
            if item is not None:
                item.setBackground(background)
                item.setForeground(foreground)
                item.setToolTip(tooltip)

    def _refresh_activity_completion_colors(self, activities=None):
        if not self.selected_lesson_id:
            return
        if activities is None:
            activities = self.db.activities(self.selected_lesson_id)
        completion_data = self.db.activity_completion_data(
            (self.selected_lesson_id,)
        ).get(self.selected_lesson_id, {})
        learning_mode = completion_data.get("learning_mode", "")
        details_by_id = completion_data.get("activities", {})
        issues_by_id = {
            activity[0]: self._activity_completion_issues(
                details_by_id.get(activity[0], {}).get("details", []),
                learning_mode,
            )
            for activity in activities
        }
        for row in range(self.activity_cards.count()):
            item = self.activity_cards.item(row)
            card = self.activity_cards.itemWidget(item)
            if card is None:
                continue
            indicator = card.findChild(
                LessonStatusIndicator, "lessonStatusIndicator"
            )
            if indicator is not None:
                indicator.set_issues(
                    issues_by_id.get(int(item.data(Qt.UserRole)), [])
                )

    def _refresh_lesson_completion_colors(self):
        lesson_ids = [
            int(self.lesson_table.item(row, 0).text())
            for row in range(self.lesson_table.rowCount())
            if self.lesson_table.item(row, 0) is not None
        ]
        completion_data = self.db.activity_completion_data(lesson_ids)
        self._refresh_lesson_card_completion_indicators(completion_data)
        for row in range(self.lesson_table.rowCount()):
            id_item = self.lesson_table.item(row, 0)
            if id_item is None:
                continue
            lesson_id = int(id_item.text())
            lesson = completion_data.get(lesson_id, {})
            self._set_completion_row_style(
                self.lesson_table, row, lesson_completion_issues(lesson),
                "الحصة"
            )

    def _refresh_lesson_card_completion_indicators(
        self, completion_data=None
    ):
        lesson_ids = [
            int(self.recorded_lesson_cards.item(row).data(Qt.UserRole))
            for row in range(self.recorded_lesson_cards.count())
            if self.recorded_lesson_cards.item(row).data(Qt.UserRole) is not None
        ]
        if completion_data is None:
            completion_data = self.db.activity_completion_data(lesson_ids)
        for row, lesson_id in enumerate(lesson_ids):
            item = self.recorded_lesson_cards.item(row)
            card = self.recorded_lesson_cards.itemWidget(item)
            if card is None:
                continue
            indicator = card.findChild(
                LessonStatusIndicator, "lessonStatusIndicator"
            )
            if indicator is not None:
                indicator.set_issues(
                    lesson_completion_issues(
                        completion_data.get(lesson_id, {})
                    )
                )

    def _activity_time_totals(self, exclude_activity_id=None):
        if not self.selected_lesson_id:
            return None, None, {}

        lesson = self.db.lesson(self.selected_lesson_id)
        lesson_minutes = self._duration_minutes(lesson[4]) if lesson else None
        if lesson_minutes is None:
            return None, None, {}

        members = self.db.lesson_classes(self.selected_lesson_id)
        class_ids = [row[0] for row in members]
        if not class_ids:
            class_ids = [None]
        class_names = {
            row[0]: row[1] for row in members
        }
        minutes_by_class = {member_id: 0 for member_id in class_ids}
        activity_scopes = self.db.activity_scopes(
            self.selected_lesson_id
        )
        for activity in self.db.activities(self.selected_lesson_id):
            if activity[0] == exclude_activity_id:
                continue
            minutes = self._duration_minutes(activity[2])
            if minutes is None:
                return lesson_minutes, None, class_names
            scope_id = activity_scopes.get(activity[0])
            affected_classes = (
                class_ids if scope_id is None else [scope_id]
            )
            for affected_class in affected_classes:
                if affected_class in minutes_by_class:
                    minutes_by_class[affected_class] += minutes
        return lesson_minutes, minutes_by_class, class_names

    def _activity_time_budget(
        self, exclude_activity_id=None, class_id=None
    ):
        lesson_minutes, minutes_by_class, _class_names = (
            self._activity_time_totals(exclude_activity_id)
        )
        if minutes_by_class is None or lesson_minutes is None:
            return lesson_minutes, None
        if class_id is not None:
            activity_minutes = minutes_by_class.get(class_id, 0)
        else:
            activity_minutes = max(minutes_by_class.values(), default=0)
        return lesson_minutes, activity_minutes

    def _show_shared_activity_time_status(self):
        lesson_minutes, minutes_by_class, class_names = (
            self._activity_time_totals()
        )
        if (
            lesson_minutes is None or minutes_by_class is None
            or len(minutes_by_class) < 2
        ):
            return

        summaries = []
        all_complete = True
        for class_id, minutes in minutes_by_class.items():
            name = class_names.get(class_id, str(class_id))
            if minutes == lesson_minutes:
                state = "مكتمل"
            elif minutes < lesson_minutes:
                remaining = lesson_minutes - minutes
                state = f"متبقي {remaining} دقيقة"
                all_complete = False
            else:
                excess = minutes - lesson_minutes
                state = f"تجاوز {excess} دقيقة"
                all_complete = False
            summaries.append(f"«{name}»: {minutes}/{lesson_minutes} دقيقة ({state})")

        message = (
            "اكتملت مدة الأنشطة لكل قسم: "
            if all_complete else "توزيع مدد الأنشطة لكل قسم: "
        ) + "؛ ".join(summaries)
        self.statusBar().showMessage(message, 10000)

    def _update_add_activity_button(self):
        if self.is_school_year_read_only:
            self.add_activity_button.setEnabled(False)
            self.add_activity_button.setToolTip(
                "السنة الدراسية السابقة متاحة للتصفح والتصدير فقط."
            )
            return
        if not self.selected_lesson_id:
            self.add_activity_button.setEnabled(False)
            self.add_activity_button.setToolTip("اختر حصة لإضافة نشاط.")
            return

        lesson_minutes, minutes_by_class, class_names = (
            self._activity_time_totals()
        )
        if lesson_minutes is None:
            self.add_activity_button.setEnabled(False)
            self.add_activity_button.setToolTip(
                "حدد مدة الحصة بالدقائق قبل إضافة الأنشطة."
            )
        elif minutes_by_class is None:
            self.add_activity_button.setEnabled(False)
            self.add_activity_button.setToolTip(
                "تعذر حساب المدد؛ صحح مدد الأنشطة لتكون بالدقائق."
            )
        elif all(
            minutes >= lesson_minutes
            for minutes in minutes_by_class.values()
        ):
            self.add_activity_button.setEnabled(False)
            self.add_activity_button.setToolTip(
                "اكتملت مدة الأنشطة لكل قسم؛ عدّل الأنشطة أو مدة الحصة."
            )
        else:
            self.add_activity_button.setEnabled(True)
            remaining_by_class = [
                f"«{class_names.get(class_id, str(class_id))}»: "
                f"{max(lesson_minutes - minutes, 0)} دقيقة"
                for class_id, minutes in minutes_by_class.items()
                if minutes < lesson_minutes
            ]
            self.add_activity_button.setToolTip(
                "الوقت المتبقي لإكمال مدة الحصة لكل قسم: "
                + "؛ ".join(remaining_by_class)
            )

    def on_lesson_selected(self):
        self.selected_lesson_id = self.current_id(self.lesson_table)
        self.selected_activity_id = None
        self.detail_cards.clear()
        self.refresh_activities()

    def on_lesson_card_selection_changed(self):
        selected_ids = self.recorded_lesson_cards.selected_lesson_ids()
        current_item = self.recorded_lesson_cards.currentItem()
        candidate_id = (
            int(current_item.data(Qt.UserRole))
            if current_item is not None
            and current_item.data(Qt.UserRole) is not None
            else None
        )
        current_id = (
            candidate_id if candidate_id in selected_ids
            else (selected_ids[-1] if selected_ids else None)
        )
        row_by_id = {
            int(self.lesson_table.item(row, 0).text()): row
            for row in range(self.lesson_table.rowCount())
            if self.lesson_table.item(row, 0) is not None
        }
        self.lesson_table.blockSignals(True)
        self.lesson_table.clearSelection()
        for lesson_id in selected_ids:
            row = row_by_id.get(lesson_id)
            if row is not None:
                self.lesson_table.selectRow(row)
        current_row = row_by_id.get(current_id)
        if current_row is not None:
            self.lesson_table.setCurrentCell(current_row, 0)
        else:
            self.lesson_table.setCurrentCell(-1, -1)
        self.lesson_table.blockSignals(False)
        self.on_lesson_selected()

    def on_lesson_card_action(self, action, lesson_id):
        lesson_row = next(
            (
                row for row in range(self.lesson_table.rowCount())
                if self.lesson_table.item(row, 0) is not None
                and int(self.lesson_table.item(row, 0).text()) == lesson_id
            ),
            None,
        )
        if lesson_row is None:
            QMessageBox.warning(
                self, "الحصة غير موجودة",
                "تعذر العثور على الحصة لتنفيذ الإجراء المطلوب.",
            )
            return

        self.lesson_table.blockSignals(True)
        self.lesson_table.clearSelection()
        self.lesson_table.setCurrentCell(lesson_row, 0)
        self.lesson_table.selectRow(lesson_row)
        self.lesson_table.blockSignals(False)
        self.recorded_lesson_cards.blockSignals(True)
        self.recorded_lesson_cards.select_lesson(lesson_id)
        self.recorded_lesson_cards.blockSignals(False)
        self.on_lesson_selected()

        if action == "edit":
            self.edit_lesson(lesson_id)
        elif action == "delete":
            self.delete_lesson(lesson_id)
        elif action == "copy":
            self.copy_selected_lessons([lesson_id])

    def on_activity_selected(self):
        self.selected_activity_id = self.current_id(self.activity_cards)
        self.refresh_details()

    def on_activity_card_action(self, action, activity_id):
        for row in range(self.activity_cards.count()):
            item = self.activity_cards.item(row)
            if int(item.data(Qt.UserRole)) != activity_id:
                continue
            self.activity_cards.setCurrentItem(item)
            break
        if action == "edit":
            self.edit_activity(activity_id)
        elif action == "delete":
            self.delete_activity(activity_id)

    def on_detail_card_action(self, action, detail_id):
        for row in range(self.detail_cards.count()):
            item = self.detail_cards.item(row)
            if int(item.data(Qt.UserRole)) != detail_id:
                continue
            self.detail_cards.setCurrentItem(item)
            break
        if action == "edit":
            self.edit_detail(detail_id)
        elif action == "delete":
            self.delete_detail(detail_id)

    def refresh_details(self, select_id=None):
        if not self.selected_activity_id:
            self.detail_cards.clear()
            self._refresh_completion_colors()
            return
        self.detail_cards.set_details(
            self.db.details(self.selected_activity_id), select_id
        )
        self._refresh_completion_colors()

    def _refresh_completion_colors(self):
        self._refresh_activity_completion_colors()
        self._refresh_lesson_completion_colors()

    def _prepare_repeat_selection(self, lesson, used_request_ids):
        lesson_data = {
            "classe": lesson[0],
            "majal": lesson[1],
            "heure": lesson[2],
            "time": lesson[3],
        }
        requests = self.db.lesson_repeat_requests_for_match(
            lesson_data, self.day_id
        )
        request = next(
            (
                candidate for candidate in requests
                if candidate["id"] not in used_request_ids
            ),
            None,
        )
        if request is None:
            return None
        used_request_ids.add(request["id"])

        target_minutes = duration_minutes(lesson[3])
        source_minutes = request["duration_minutes"]
        activities = request["activities"]
        if target_minutes is None or source_minutes is None or not activities:
            QMessageBox.warning(
                self,
                "تعذر إعداد إعادة الحصة",
                "تعذر تحديد مدة الحصة أو أنشطتها؛ أضيفت الحصة دون نسخ "
                "أنشطة الإعادة.",
            )
            return None

        activity_minutes = [
            duration_minutes(activity[2]) for activity in activities
        ]
        if target_minutes == source_minutes and all(
            minutes is not None for minutes in activity_minutes
        ) and sum(activity_minutes) <= target_minutes:
            activity_names = "\n".join(
                f"• {title} ({duration})"
                for _activity_id, title, duration in activities
            )
            answer = QMessageBox.question(
                self,
                "تأكيد إعادة الحصة كاملة",
                "مدة الحصة القادمة مساوية لمدة الحصة المطلوب إعادتها. "
                "هل توافق على إعادة جميع أنشطتها؟\n\n"
                + activity_names,
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            selected_ids = (
                [activity[0] for activity in activities]
                if answer == QMessageBox.StandardButton.Yes else []
            )
        elif target_minutes > source_minutes:
            dialog = RepeatActivitiesSelectionDialog(
                activities, target_minutes, self
            )
            if not dialog.exec():
                return None
            selected_ids = dialog.selected_activity_ids()
        else:
            selected_ids = []
            selected_minutes = 0
            for activity, minutes in zip(activities, activity_minutes):
                if minutes is None or selected_minutes + minutes > target_minutes:
                    break
                selected_ids.append(activity[0])
                selected_minutes += minutes
            if not selected_ids:
                QMessageBox.information(
                    self,
                    "تعذر إعادة نشاط",
                    "لا توجد أنشطة متتالية من الحصة الأصلية تلائم مدة "
                    "الحصة القادمة؛ بقيت أنشطة الإعادة معلقة.",
                )
                return None
            remaining_count = len(activities) - len(selected_ids)
            answer = QMessageBox.question(
                self,
                "تأكيد إعادة جزء من الحصة",
                f"ستُنسخ الأنشطة الأولى التي تلائم مدة الحصة "
                f"({selected_minutes} من {target_minutes} دقيقة)، "
                f"وتبقى {remaining_count} من الأنشطة لإكمالها لاحقًا. "
                "هل توافق؟",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None

        if not selected_ids:
            return None
        return {
            "request_id": request["id"],
            "activity_ids": selected_ids,
        }

    def add_lesson(self):
        if not self.ensure_school_year_writable():
            return
        if getattr(self, "_lesson_history_thread", None) is not None:
            return
        try:
            if not self._has_free_timetable_time():
                QMessageBox.information(
                    self, "لا توجد فترة شاغرة",
                    "امتلأت ساعات العمل المحددة في جدول المعلم؛ "
                    "لا يمكن إضافة حصة جديدة.",
                )
                return
        except (sqlite3.Error, ValueError) as exc:
            QMessageBox.critical(
                self, "تعذر التحقق من ساعات العمل",
                f"تعذر تحديد إمكانية إضافة حصة:\n{exc}",
            )
            return

        self._start_lesson_history_load(
            self._open_lesson_entry_dialog,
            self.lesson_table.rowCount(),
        )

    def _start_lesson_history_load(
        self, result_handler, existing_count, generate_defaults=True
    ):
        if getattr(self, "_lesson_history_thread", None) is not None:
            return
        waiting_text = "في انتظار توليد البيانات ..."
        self.lesson_loading_label.setText(waiting_text)
        self.lesson_loading_label.show()
        self.scheduled_lessons_list.setEnabled(False)
        self.date_edit.setEnabled(False)

        self._lesson_history_thread = QThread(self)
        self._lesson_history_worker = LessonHistoryLoadWorker(
            self.db.path,
            self.date_edit.date().dayOfWeek() - 1,
            existing_count,
            generate_defaults,
        )
        self._lesson_history_worker.moveToThread(self._lesson_history_thread)
        self._lesson_history_thread.started.connect(
            self._lesson_history_worker.run, Qt.QueuedConnection
        )
        self._lesson_history_worker.result_ready.connect(result_handler)
        self._lesson_history_worker.failed.connect(self._lesson_history_failed)
        self._lesson_history_worker.finished.connect(
            self._lesson_history_thread.quit
        )
        self._lesson_history_worker.finished.connect(
            self._lesson_history_worker.deleteLater
        )
        self._lesson_history_thread.finished.connect(
            self._lesson_history_thread_finished
        )
        self._lesson_history_thread.start()

    @Slot(object, object)
    def _open_lesson_entry_dialog(self, df_history, predicted):
        if not self.ensure_school_year_writable():
            return
        def_hessa_num = max(1, self.lesson_table.rowCount() + 1)
        default_vals = ["", "", "08:00-09:00", "60 دقيقة", 1, def_hessa_num, "منهجي"]
        if predicted:
            default_vals[0] = predicted.get("classe", "")
            default_vals[1] = predicted.get("majal", "")
            default_vals[2] = predicted.get("heure", "08:00-09:00")
            default_vals[3] = predicted.get("time", "60 دقيقة")
            default_vals[4] = predicted.get("wehda", 1)
            default_vals[5] = def_hessa_num
            default_vals[6] = predicted.get("namat", "منهجي")
        try:
            existing_lessons = (
                self.db.lessons(self.day_id) if self.day_id else []
            )
            work_bounds = self._teacher_work_bounds()
        except (sqlite3.Error, ValueError) as error:
            QMessageBox.critical(
                self,
                "تعذر قراءة جدول الأوقات",
                f"تعذر تحديد فترة شاغرة من جدول الأوقات:\n{error}",
            )
            return

        try:
            free_intervals = _unoccupied_work_intervals(
                existing_lessons, *work_bounds
            )
        except ValueError as error:
            QMessageBox.critical(
                self,
                "توقيت حصة غير صالح",
                f"تعذر تحديد الفترات المتاحة:\n{error}",
            )
            return
        available_hours = []
        for gap_start, gap_end in free_intervals:
            first_full_hour = ((gap_start + 59) // 60) * 60
            for start_minute in range(first_full_hour, gap_end, 60):
                end_minute = start_minute + 60
                if end_minute > gap_end:
                    continue
                start_text = (
                    f"{start_minute // 60:02d}:"
                    f"{start_minute % 60:02d}"
                )
                end_text = (
                    f"{end_minute // 60:02d}:"
                    f"{end_minute % 60:02d}"
                )
                available_hours.append(
                    f"{start_text}-{end_text}"
                )
        if not available_hours:
            QMessageBox.information(
                self,
                "لا توجد فترة شاغرة",
                "لا توجد فترة متصلة مدتها 30 دقيقة على الأقل لإضافة حصة.",
            )
            return
        default_vals[2] = available_hours[0]
        start, end = _parse_hour_interval(default_vals[2])
        default_vals[3] = f"{end - start} دقيقة"

        dlg = LessonEntryDialog(
            "إضافة حصة", df_history, default_values=default_vals,
            parent=self,
            db=self.db,
            available_hours=available_hours,
            reserved_hours=[],
            work_bounds=work_bounds,
        )
        if dlg.exec():
            v = dlg.values()
            if not v[0] or not v[1]:
                QMessageBox.warning(self, "بيانات ناقصة", "يرجى إدخال القسم والمجال / المادة.")
                return
            try:
                existing_lessons = self.db.lessons(self.day_id)
                if not self._validate_new_lesson_intervals(
                    [v], existing_lessons
                ):
                    QMessageBox.warning(
                        self, "التوقيت غير متاح",
                        "التوقيت يتداخل مع حصة أخرى أو يقع خارج ساعات العمل "
                        "المتاحة في جدول المعلم.",
                    )
                    return
                repeat_selection = self._prepare_repeat_selection(v, set())
                new_id = self.db.save_lesson(
                    v,
                    self.day_id,
                    repeat_selection=repeat_selection,
                )
            except (ValueError, sqlite3.Error) as exc:
                QMessageBox.critical(
                    self, "تعذر حفظ الحصة", f"حدث خطأ أثناء حفظ الحصة:\n{exc}"
                )
                return
            self.refresh_lessons(select_first=False)

    @Slot(str)
    def _lesson_history_failed(self, message):
        self.lesson_loading_label.setText(message)
        self.lesson_loading_label.show()
        QMessageBox.critical(self, "تعذر تحميل الحصص السابقة", message)

    @Slot()
    def _lesson_history_thread_finished(self):
        thread = self._lesson_history_thread
        self._lesson_history_worker = None
        self._lesson_history_thread = None
        if thread is not None:
            thread.deleteLater()
        self.date_edit.setEnabled(True)
        if not self.lesson_loading_label.text().startswith("تعذر"):
            self.lesson_loading_label.hide()
        self.scheduled_lessons_list.setEnabled(
            not self.is_school_year_read_only
        )

    def add_all_lessons(self):
        if not self.ensure_school_year_writable():
            return
        target_day = self.date_edit.date().toString("yyyy-MM-dd")
        weekday_num = self.date_edit.date().dayOfWeek() - 1
        try:
            lessons = self.db.lessons_for_weekday(weekday_num, target_day)
        except (sqlite3.Error, ValueError) as e:
            QMessageBox.critical(
                self,
                "خطأ",
                f"تعذر تحميل حصص هذا اليوم من الجدول اليدوي أو الآلي: {e}",
            )
            return

        if not lessons:
            QMessageBox.information(
                self, "لا توجد حصص مسجلة",
                "لا توجد حصص في جدول الأوقات لهذا اليوم."
            )
            return
        self.add_scheduled_lessons(lessons)

    def add_scheduled_lessons(self, lessons):
        if not self.ensure_school_year_writable():
            return
        existing_rows = self.db.lessons(self.day_id)
        existing_ids = {row[0] for row in existing_rows}
        existing_keys = {
            (str(row[2] or "").strip(), str(row[3] or "").strip(),
             str(row[4] or "").strip(), str(row[5] or "").strip())
            for row in existing_rows
        }
        seen_keys = set()
        new_lessons = []
        for lesson in lessons:
            key = tuple(
                str(value or "").strip() for value in lesson[:4]
            )
            if key not in existing_keys and key not in seen_keys:
                new_lessons.append(lesson)
                seen_keys.add(key)

        if not new_lessons:
            QMessageBox.information(
                self, "الحصص موجودة",
                "جميع الحصص المسجلة لهذا اليوم مضافة بالفعل."
            )
            return

        try:
            if not self._validate_new_lesson_intervals(
                new_lessons, existing_rows
            ):
                QMessageBox.warning(
                    self, "التوقيت غير متاح",
                    "توجد حصة في توقيت محجوز أو خارج ساعات العمل، "
                    "ولا يمكن إضافة الحصص المحددة.",
                )
                return
        except ValueError as exc:
            QMessageBox.critical(
                self, "توقيت غير صالح",
                f"تعذر التحقق من توقيت الحصة:\n{exc}",
            )
            return

        target_date = self.date_edit.date().toString("yyyy-MM-dd")
        future_number_keys = {}
        try:
            for lesson in new_lessons:
                key = (
                    str(lesson[0] or "").strip(),
                    str(lesson[1] or "").strip(),
                )
                if key not in future_number_keys:
                    future_number_keys[key] = self.db.future_lesson_dates(
                        *key, target_date
                    )
        except (sqlite3.Error, ValueError) as e:
            QMessageBox.critical(
                self,
                "تعذر التحقق من الحصص المستقبلية",
                f"تعذر التحقق من وجود حصص لاحقة:\n{e}",
            )
            return

        future_number_keys = {
            key: dates for key, dates in future_number_keys.items() if dates
        }
        if future_number_keys:
            future_descriptions = "\n".join(
                f"• {classe} — {majal}: {', '.join(dates)}"
                for (classe, majal), dates in future_number_keys.items()
            )
            QMessageBox.warning(
                self,
                "توجد حصص بتاريخ لاحق",
                "توجد حصص مسجلة لهذا القسم والمجال في تواريخ لاحقة:\n"
                f"{future_descriptions}\n\n"
                "لن يتم احتساب رقم الوحدة أو رقم الحصة تلقائيًا "
                "لهذه الحصص. يرجى إدخالهما يدويًا.",
            )

        number_defaults = {}
        repeat_number_defaults = {}
        try:
            number_request_ids = set()
            for lesson in new_lessons:
                classe, majal = lesson[:2]
                key = (str(classe or "").strip(), str(majal or "").strip())
                if key not in number_defaults:
                    number_defaults[key] = (
                        (1, 1)
                        if key in future_number_keys
                        else self.db.lesson_number_defaults(*key)
                    )
                requests = self.db.lesson_repeat_requests_for_match(
                    {
                        "classe": lesson[0],
                        "majal": lesson[1],
                        "heure": lesson[2],
                        "time": lesson[3],
                    },
                    self.day_id,
                )
                request = next(
                    (
                        candidate for candidate in requests
                        if candidate["id"] not in number_request_ids
                    ),
                    None,
                )
                if request is not None:
                    number_request_ids.add(request["id"])
                    repeat_number_defaults[
                        tuple(str(value or "").strip() for value in lesson[:3])
                    ] = (
                        request["unit_number"], request["lesson_number"]
                    )
        except (sqlite3.Error, ValueError) as e:
            QMessageBox.critical(
                self, "خطأ",
                f"تعذر تحميل أرقام الحصص أو طلبات الإعادة: {e}"
            )
            return

        dlg = BulkLessonEntryDialog(
            new_lessons,
            number_defaults,
            self,
            repeat_number_defaults=repeat_number_defaults,
            learning_mode_resolver=lambda lesson, unit, number: (
                self.db.teaching_plan_learning_mode(
                    lesson[0], lesson[1], unit, number, self.day_id
                )
            ),
            plan_lesson_count_resolver=lambda lesson, unit: (
                self.db.teaching_plan_lesson_count(
                    lesson[0], lesson[1], unit, self.day_id
                )
            ),
            assigned_activity_resolver=lambda lesson: (
                self.db.teaching_assignment_activities(
                    lesson[0], lesson[1]
                )
            ),
            manual_number_keys=future_number_keys.keys(),
        )
        if not dlg.exec():
            return

        values = dlg.values()
        if any(not lesson[0] or not lesson[1] for lesson in values):
            QMessageBox.warning(self, "بيانات ناقصة", "تعذر إضافة حصة لا تحتوي على القسم والمجال.")
            return

        try:
            if not self._validate_new_lesson_intervals(
                values, existing_rows
            ):
                QMessageBox.warning(
                    self, "التوقيت غير متاح",
                    "تغيّر التوقيت إلى فترة محجوزة أو خارج ساعات العمل.",
                )
                return
            used_request_ids = set()
            repeat_selections = {}
            for lesson_index, lesson in enumerate(values):
                selection = self._prepare_repeat_selection(
                    lesson, used_request_ids
                )
                if selection is not None:
                    repeat_selections[lesson_index] = selection
            self.db.save_lessons(
                values,
                self.day_id,
                repeat_selections=repeat_selections,
            )
        except (sqlite3.Error, ValueError) as e:
            QMessageBox.critical(self, "خطأ", f"حدث خطأ أثناء إضافة الحصص: {e}")
            return

        self.refresh_lessons(select_first=False)
        self.statusBar().showMessage(f"تمت إضافة {len(values)} حصة بنجاح.", 3000)

    def edit_lesson(self, lesson_id=None):
        if not self.ensure_school_year_writable():
            return
        if getattr(self, "_lesson_history_thread", None) is not None:
            return
        lid = lesson_id or self.current_id(self.lesson_table)
        if not lid:
            return self.warn_select("حصة")
        try:
            row = self.db.lesson(lid)
        except sqlite3.Error as exc:
            QMessageBox.critical(self, "تعذر تحميل الحصة", str(exc))
            return
        if row is None:
            QMessageBox.warning(self, "تعذر تحميل الحصة", "لم تعد الحصة موجودة.")
            return

        existing_request = self.db.lesson_repeat_request(lid)
        repeat_requested = bool(
            existing_request
            and existing_request.get("repeat_for_next_session")
            and existing_request.get("confirmed_by_user")
            and not existing_request.get("is_applied")
        )
        dialog = LessonScheduleEditDialog(
            row[3], row[5], row[6], self,
            repeat_reason=(existing_request or {}).get("reason", ""),
            repeat_requested=repeat_requested,
        )
        if not dialog.exec():
            return
        hour_interval, unit_number, lesson_number, repeat_requested, repeat_reason = dialog.values()
        try:
            start, end = _parse_hour_interval(hour_interval)
            original_duration = duration_minutes(row[4])
            if (
                original_duration is not None
                and end - start != original_duration
            ):
                QMessageBox.warning(
                    self, "مدة غير مطابقة",
                    "يجب أن يحافظ التوقيت الجديد على مدة الحصة الأصلية.",
                )
                return
            existing_lessons = self.db.lessons(self.day_id)
            work_bounds = self._teacher_work_bounds()
            if not _hour_interval_is_available(
                hour_interval,
                existing_lessons,
                work_bounds,
                excluded_lesson_id=lid,
            ):
                QMessageBox.warning(
                    self, "التوقيت محجوز",
                    "لا يمكن نقل الحصة إلى فترة تتداخل مع حصة أخرى "
                    "أو تقع خارج ساعات العمل.",
                )
                return
            self.db.update_lesson_schedule(
                lid, hour_interval, unit_number, lesson_number
            )
            if repeat_requested:
                self.db.set_lesson_repeat_request(
                    lid,
                    repeat_reason,
                    repeat_for_next_session=True,
                    confirmed_by_user=True,
                    matched_interval=hour_interval,
                    duration_minutes=original_duration,
                )
            else:
                self.db.cancel_lesson_repeat_request(lid)
        except (sqlite3.Error, ValueError) as exc:
            QMessageBox.critical(
                self, "تعذر تعديل الحصة",
                f"حدث خطأ أثناء تعديل توقيت الحصة أو أرقامها:\n{exc}",
            )
            return
        self.refresh_lessons(lid)

    def delete_lesson(self, lesson_id=None):
        if not self.ensure_school_year_writable():
            return
        lid = lesson_id or self.current_id(self.lesson_table)
        if not lid:
            return self.warn_select("حصة")
        if self.confirm("حذف الحصة المحددة وكل أنشطتها وخصائصها؟"):
            self.db.delete_lesson(lid)
            self.selected_lesson_id = None
            self.refresh_lessons()
            self.activity_cards.clear()
            self.detail_cards.clear()

    def copy_selected_lessons(self, lesson_ids=None):
        if lesson_ids is None:
            lesson_ids = [
                int(self.lesson_table.item(index.row(), 0).text())
                for index in self.lesson_table.selectionModel().selectedRows()
                if self.lesson_table.item(index.row(), 0) is not None
            ]
        if not lesson_ids:
            return self.warn_select("حصة واحدة على الأقل لنسخها")
        
        self.copied_lessons = []
        for lesson_id in lesson_ids:
            lesson_row = self.db.lesson(lesson_id)
            if not lesson_row:
                continue
            
            lesson_data = {
                "classe": lesson_row[1],
                "classe_level_id": lesson_row[9],
                "majal": lesson_row[2],
                "heure": lesson_row[3],
                "time": lesson_row[4],
                "wehda_number": lesson_row[5],
                "hessa_number": lesson_row[6],
                "namat_taallom": lesson_row[7],
                "activities": []
            }
            
            for act in self.db.activities(lesson_id):
                act_id, act_title, act_time = act[0], act[1], act[2]
                details = self.db.details(act_id)
                act_data = {
                    "title": act_title,
                    "time": act_time,
                    "classe": next(
                        (
                            class_name
                            for class_id, class_name, _level
                            in self.db.lesson_classes(lesson_id)
                            if class_id
                            == self.db.activity_class_id(act_id)
                        ),
                        "",
                    ),
                    "details": [(d[1], d[2]) for d in details]
                }
                lesson_data["activities"].append(act_data)
                
            self.copied_lessons.append(lesson_data)
        self.update_paste_lessons_button_state()
        self.statusBar().showMessage(f"تم نسخ {len(self.copied_lessons)} حصة بنجاح.", 3000)

    def update_paste_lessons_button_state(self):
        button = getattr(self, "paste_lessons_button", None)
        if button is not None:
            button.setEnabled(
                bool(self.copied_lessons)
                and not self.is_school_year_read_only
            )

    def paste_lessons(self):
        if not self.ensure_school_year_writable():
            return
        if not self.copied_lessons:
            QMessageBox.warning(self, "تنبيه", "لا توجد حصص منسوخة حالياً.")
            return
        if not self.day_id:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد اليوم المراد لصق الحصص فيه.")
            return

        try:
            existing_lessons = self.db.lessons(self.day_id)
            pasted_lessons = [
                (
                    lesson["classe"],
                    lesson["majal"],
                    lesson["heure"],
                    lesson["time"],
                )
                for lesson in self.copied_lessons
            ]
            if not self._validate_new_lesson_intervals(
                pasted_lessons, existing_lessons
            ):
                QMessageBox.warning(
                    self, "التوقيت غير متاح",
                    "لا يمكن لصق الحصص لأن أحد توقيتاتها يتداخل مع حصة "
                    "أخرى أو يقع خارج ساعات العمل.",
                )
                return
        except (sqlite3.Error, ValueError, KeyError) as exc:
            QMessageBox.critical(
                self, "تعذر التحقق من توقيت الحصص",
                f"حدث خطأ أثناء التحقق من الحصص المراد لصقها:\n{exc}",
            )
            return

        try:
            with self.db.connect() as con:
                cursor = con.cursor()
                for les in self.copied_lessons:
                    classe_id, class_group_id, class_ids = (
                        self.db._resolve_lesson_class(
                        con, les["classe"], les["classe_level_id"]
                        )
                    )
                    majal_id, learning_mode_id = (
                        self.db.resolve_lesson_lookup_ids(
                            con, les["majal"], les["namat_taallom"]
                        )
                    )
                    cursor.execute("""
                        INSERT INTO hessa(
                            classe_id, majal_id, heure, time, wehda_number,
                            hessa_number, learning_mode_id, day_data_id,
                            class_group_id
                        )
                        VALUES(?,?,?,?,?,?,?,?,?)
                    """, (
                        classe_id, majal_id, les["heure"], les["time"],
                        les["wehda_number"], les["hessa_number"],
                        learning_mode_id, self.day_id, class_group_id
                    ))
                    new_hessa_id = cursor.lastrowid
                    self.db._replace_hessa_classes(
                        con, new_hessa_id, class_ids
                    )
                    
                    for act in les["activities"]:
                        activity_class_id = self.db.resolve_activity_class(
                            con, new_hessa_id, act.get("classe")
                        )
                        cursor.execute("""
                            INSERT INTO nachat(
                                title, time, hessa_id, classe_id
                            ) VALUES(?, ?, ?, ?)
                        """, (
                            act["title"], act["time"], new_hessa_id,
                            activity_class_id,
                        ))
                        new_nachat_id = cursor.lastrowid
                        
                        for detail in act["details"]:
                            cursor.execute("""
                                INSERT INTO nachat_detail(key_name, value_text, nachat_id) VALUES(?,?,?)
                            """, (detail[0], detail[1], new_nachat_id))
                con.commit()
                
            self.refresh_lessons()
            self.statusBar().showMessage("تم لصق الحصص المنسوخة بنجاح.", 3000)
        except (sqlite3.Error, ValueError, KeyError, TypeError) as e:
            QMessageBox.critical(self, "خطأ", f"حدث خطأ أثناء لصق الحصص: {e}")

    def _current_lesson_context(self):
        if not self.selected_lesson_id:
            return "", "", None
        row = self.db.lesson(self.selected_lesson_id)
        if not row:
            return "", "", None
        return str(row[2] or ""), str(row[7] or ""), row[8]

    def add_activity(self):
        if not self.ensure_school_year_writable():
            return
        if not self.selected_lesson_id:
            return self.warn_select("حصة أولاً")
        majal, _namat, _day_data_id = self._current_lesson_context()
        weekday = self.date_edit.date().dayOfWeek() - 1
        dlg = ActivityEntryDialog(
            "إضافة نشاط", majal, self.db, weekday,
            parent=self, lesson_id=self.selected_lesson_id,
        )
        while dlg.exec():
            title, duration, class_id = dlg.values()
            if not title:
                return QMessageBox.warning(self, "بيانات ناقصة", "عنوان النشاط مطلوب.")
            duration_minutes = self._duration_minutes(duration)
            if duration_minutes is None:
                return QMessageBox.warning(
                    self, "مدة غير صالحة", "يرجى تحديد مدة النشاط بالدقائق."
                )
            lesson_minutes, activity_minutes = self._activity_time_budget(
                class_id=class_id
            )
            if lesson_minutes is None:
                return QMessageBox.warning(
                    self, "مدة الحصة غير صالحة",
                    "حدد مدة الحصة بالدقائق قبل إضافة نشاط."
                )
            if activity_minutes is None:
                return QMessageBox.warning(
                    self, "مدد أنشطة غير صالحة",
                    "تعذر حساب مجموع مدد الأنشطة؛ صحح مددها لتكون بالدقائق."
                )
            if activity_minutes + duration_minutes > lesson_minutes:
                remaining_minutes = max(lesson_minutes - activity_minutes, 0)
                QMessageBox.warning(
                    self, "تجاوز مدة الحصة",
                    f"مجموع مدد الأنشطة سيتجاوز مدة الحصة "
                    f"({lesson_minutes} دقيقة). غيّر مدة هذا النشاط إلى "
                    f"{remaining_minutes} دقيقة أو أقل."
                )
                dlg.time_combo.setFocus()
                continue
            aid = self.db.save_activity(
                title, duration, self.selected_lesson_id,
                class_id=class_id,
            )
            self.refresh_activities(aid)
            self._show_shared_activity_time_status()
            break

    def edit_activity(self, activity_id=None):
        if not self.ensure_school_year_writable():
            return
        aid = activity_id or self.current_id(self.activity_cards)
        if not aid:
            return self.warn_select("نشاط")
        majal, _namat, _day_data_id = self._current_lesson_context()
        row = next((x for x in self.db.activities(self.selected_lesson_id) if x[0] == aid), None)
        if not row:
            return
        weekday = self.date_edit.date().dayOfWeek() - 1
        dlg = ActivityEntryDialog("تعديل نشاط", majal, self.db, weekday,
                                  values=[
                                      row[1], row[2],
                                      self.db.activity_class_id(aid),
                                  ],
                                  parent=self,
                                  lesson_id=self.selected_lesson_id)
        while dlg.exec():
            title, duration, class_id = dlg.values()
            if not title:
                return QMessageBox.warning(self, "بيانات ناقصة", "عنوان النشاط مطلوب.")
            duration_minutes = self._duration_minutes(duration)
            if duration_minutes is None:
                return QMessageBox.warning(
                    self, "مدة غير صالحة", "يرجى تحديد مدة النشاط بالدقائق."
                )
            lesson_minutes, activity_minutes = self._activity_time_budget(
                exclude_activity_id=aid,
                class_id=class_id,
            )
            if lesson_minutes is None:
                return QMessageBox.warning(
                    self, "مدة الحصة غير صالحة",
                    "حدد مدة الحصة بالدقائق قبل تعديل النشاط."
                )
            if activity_minutes is None:
                return QMessageBox.warning(
                    self, "مدد أنشطة غير صالحة",
                    "تعذر حساب مجموع مدد الأنشطة؛ صحح مددها لتكون بالدقائق."
                )
            if activity_minutes + duration_minutes > lesson_minutes:
                remaining_minutes = max(lesson_minutes - activity_minutes, 0)
                QMessageBox.warning(
                    self, "تجاوز مدة الحصة",
                    f"مجموع مدد الأنشطة سيتجاوز مدة الحصة "
                    f"({lesson_minutes} دقيقة). غيّر مدة هذا النشاط إلى "
                    f"{remaining_minutes} دقيقة أو أقل."
                )
                dlg.time_combo.setFocus()
                continue
            self.db.save_activity(
                title, duration, self.selected_lesson_id, aid,
                class_id=class_id,
            )
            self.refresh_activities(aid)
            self._show_shared_activity_time_status()
            break

    def delete_activity(self, activity_id=None):
        if not self.ensure_school_year_writable():
            return
        aid = activity_id or self.current_id(self.activity_cards)
        if not aid:
            return self.warn_select("نشاط")
        if self.confirm("حذف النشاط المحدد وكل خصائصه؟"):
            self.db.delete_activity(aid)
            self.selected_activity_id = None
            self.refresh_activities()
            self._show_shared_activity_time_status()
            self.detail_cards.clear()

    def _detail_dialog_context(self):
        majal, namat, _day_data_id = self._current_lesson_context()
        activity_row = next((x for x in self.db.activities(self.selected_lesson_id or -1)
                             if x[0] == self.selected_activity_id), None)
        activity_title = activity_row[1] if activity_row else ""
        return namat, activity_title

    def add_detail(self):
        if not self.ensure_school_year_writable():
            return
        if not self.selected_activity_id:
            return self.warn_select("نشاط أولاً")
        namat, activity_title = self._detail_dialog_context()
        current_details = self.db.details(self.selected_activity_id)
        current_count = len(current_details)
        contextual_content = next(
            (
                str(detail[2] or "")
                for detail in current_details
                if str(detail[1] or "").strip() == "المحتوى"
            ),
            None,
        )
        dlg = DetailEntryDialog("إضافة خاصية للنشاط", namat, activity_title,
                                self.db, current_count=current_count,
                                parent=self,
                                contextual_content=contextual_content,
                                default_key=_default_detail_key(
                                    namat, current_details
                                ))
        if dlg.exec():
            key, value = dlg.values()
            if not key:
                return QMessageBox.warning(self, "بيانات ناقصة", "يرجى اختيار اسم الخاصية.")
            if any(d[1] == key for d in current_details):
                reply = QMessageBox.question(
                    self,
                    "تنبيه: خاصية موجودة مسبقاً",
                    f"اسم الخاصية '{key}' موجود مسبقاً لهذا النشاط.\nهل تريد متابعة الإضافة رغم ذلك؟",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if reply == QMessageBox.No:
                    return
            did = self.db.save_detail(key, value, self.selected_activity_id)
            self.refresh_details(did)

    def edit_detail(self, detail_id=None):
        if not self.ensure_school_year_writable():
            return
        did = detail_id or self.current_id(self.detail_cards)
        if not did:
            return self.warn_select("خاصية")
        current_details = self.db.details(self.selected_activity_id)
        row = next((x for x in current_details if x[0] == did), None)
        if not row:
            return
        namat, activity_title = self._detail_dialog_context()
        contextual_content = next(
            (
                str(detail[2] or "")
                for detail in current_details
                if str(detail[1] or "").strip() == "المحتوى"
            ),
            None,
        )
        dlg = DetailEntryDialog("تعديل خاصية", namat, activity_title, self.db,
                                values=[row[1], row[2]], parent=self,
                                contextual_content=contextual_content)
        if dlg.exec():
            key, value = dlg.values()
            if not key:
                return QMessageBox.warning(self, "بيانات ناقصة", "يرجى اختيار اسم الخاصية.")
            self.db.save_detail(key, value, self.selected_activity_id, did)
            self.refresh_details(did)

    def delete_detail(self, detail_id=None):
        if not self.ensure_school_year_writable():
            return
        did = detail_id or self.current_id(self.detail_cards)
        if not did:
            return self.warn_select("خاصية")
        if self.confirm("هل تريد حذف الخاصية المحددة؟"):
            self.db.delete_detail(did)
            self.refresh_details()

    def warn_select(self, what):
        QMessageBox.information(self, "تحديد مطلوب", f"يرجى تحديد {what} أولاً.")

    def confirm(self, message):
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("تأكيد العملية")
        msg_box.setText(message)
        msg_box.setIcon(QMessageBox.Question)
        btn_yes = msg_box.addButton("نعم", QMessageBox.AcceptRole)
        btn_yes.setProperty("actionType", "save")
        btn_no = msg_box.addButton("إلغاء", QMessageBox.RejectRole)
        btn_no.setProperty("actionType", "cancel")
        msg_box.exec()
        return msg_box.clickedButton() == btn_yes

    def export_pdf(self):
        selection_dialog = DaySelectionDialog(self.db, parent=self)
        if not selection_dialog.exec():
            return

        selected_date_text = selection_dialog.get_selected_date_str()
        try:
            day_id = self.db.recorded_day_id(selected_date_text)
            if not day_id:
                QMessageBox.information(
                    self,
                    "تنبيه",
                    f"لا توجد حصص مسجلة للتاريخ: {selected_date_text}",
                )
                return

            self.day_lessons_page.set_day(day_id, selected_date_text)
            self.day_lessons_page.export_pdf()
        except (sqlite3.Error, RuntimeError) as exc:
            QMessageBox.critical(
                self,
                "تعذر تصدير PDF",
                f"حدث خطأ أثناء تحميل بيانات اليوم:\n{exc}",
            )

    def export_day_to_json(self):
        dlg = DaySelectionDialog(self.db, parent=self)
        if not dlg.exec():
            return

        selected_date_text = dlg.get_selected_date_str()
        try:
            day_id = self.db.recorded_day_id(selected_date_text)
        except sqlite3.Error as error:
            QMessageBox.critical(
                self, "تعذر تصدير اليوم",
                f"تعذر تحميل بيانات اليوم:\n{error}",
            )
            return
        if not day_id:
            QMessageBox.information(
                self, "تنبيه",
                f"لا توجد بيانات مسجلة للتاريخ: {selected_date_text}",
            )
            return

        database_path = self.db.path
        year_id = self.db.year_id

        def export_day():
            import json

            payload = DayLessonsBrowsePage._build_day_payload(
                database_path, year_id, day_id
            )
            documents = Path.home() / "Documents"
            documents.mkdir(parents=True, exist_ok=True)
            output_path = documents / f"school_day_{payload['date']}.json"
            with output_path.open("w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, indent=4)
            return output_path

        start_background_task(
            self,
            export_day,
            lambda output_path: QMessageBox.information(
                self,
                "نجاح التصدير",
                f"تم تصدير بيانات اليوم بنجاح إلى مجلد المستندات:\n{output_path}",
            ),
            lambda error: QMessageBox.critical(
                self, "تعذر تصدير اليوم",
                f"حدث خطأ أثناء تصدير بيانات اليوم:\n{error}",
            ),
        )

    def import_day_from_json(self):
        if not self.ensure_school_year_writable():
            return
        import json
        from PySide6.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getOpenFileName(self, "اختيار ملف JSON للاستيراد", str(APP_DIR), "ملفات JSON (*.json);;كل الملفات (*)")
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                day_data = json.load(f)
            target_date = day_data.get("date")
            if not target_date:
                QMessageBox.warning(self, "خطأ في الملف", "ملف JSON لا يحتوي على حقل التاريخ (date).")
                return
            with self.db.connect() as con:
                cursor = con.cursor()
                cursor.execute("SELECT id FROM day_data WHERE date = ? AND ann_scolaire_id = ?", (target_date, self.db.year_id))
                existing_day = cursor.fetchone()
                if existing_day:
                    old_day_id = existing_day[0]
                    cursor.execute("DELETE FROM day_data WHERE id = ?", (old_day_id,))
                cursor.execute("INSERT INTO day_data (date, ann_scolaire_id) VALUES (?, ?)", (target_date, self.db.year_id))
                new_day_id = cursor.lastrowid
                for lesson in day_data.get("lessons", []):
                    group_key = str(
                        lesson.get("class_group_key") or ""
                    ).strip()
                    class_name = group_key or lesson.get("classe", "")
                    class_level = (
                        f"group:{group_key}"
                        if group_key else lesson.get("classe_level")
                    )
                    classe_id, class_group_id, class_ids = (
                        self.db._resolve_lesson_class(
                            con, class_name, class_level
                        )
                    )
                    majal_id, learning_mode_id = (
                        self.db.resolve_lesson_lookup_ids(
                            con, lesson.get("majal", ""),
                            lesson.get("namat_taallom", ""),
                        )
                    )
                    cursor.execute("""
                        INSERT INTO hessa (
                            classe_id, majal_id, heure, time, wehda_number,
                            hessa_number, learning_mode_id, day_data_id,
                            class_group_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        classe_id, majal_id,
                        lesson.get("heure", ""), lesson.get("time", ""),
                        lesson.get("wehda_number", 1),
                        lesson.get("hessa_number", 1),
                        learning_mode_id, new_day_id, class_group_id,
                    ))
                    new_hessa_id = cursor.lastrowid
                    self.db._replace_hessa_classes(
                        con, new_hessa_id, class_ids
                    )
                    for activity in lesson.get("activities", []):
                        activity_class_id = self.db.resolve_activity_class(
                            con, new_hessa_id, activity.get("classe")
                        )
                        cursor.execute("""
                            INSERT INTO nachat(
                                title, time, hessa_id, classe_id
                            ) VALUES (?, ?, ?, ?)
                        """, (
                            activity.get("title", ""),
                            activity.get("time", ""),
                            new_hessa_id,
                            activity_class_id,
                        ))
                        new_nachat_id = cursor.lastrowid
                        for detail in activity.get("details", []):
                            cursor.execute("INSERT INTO nachat_detail (key_name, value_text, nachat_id) VALUES (?, ?, ?)", (detail.get("key_name", ""), detail.get("value_text", ""), new_nachat_id))
                con.commit()
            qd = QDate.fromString(target_date, "yyyy-MM-dd")
            if qd.isValid():
                self.date_edit.setDate(qd)
            self.load_day()
            QMessageBox.information(self, "نجاح الاستيراد", f"تم استيراد بيانات يوم {target_date} بنجاح وتسجيلها في التطبيق.")
        except Exception as e:
            QMessageBox.critical(self, "خطأ في الاستيراد", f"حدث خطأ أثناء قراءة أو استيراد ملف JSON:\n{e}")

    @staticmethod
    def _single_json_drop_path(event):
        """أرجع مسار ملف JSON واحد فقط، وتجاهل السحب الداخلي والعناصر الأخرى."""
        mime = event.mimeData()
        if not mime.hasUrls():
            return None
        urls = mime.urls()
        if len(urls) != 1:
            return None
        path = urls[0].toLocalFile()
        if not path or Path(path).suffix.lower() != ".json":
            return None
        return path

    @staticmethod
    def _classify_json_payload(data):
        """تصنيف ملفات التطبيق حتى لا تختلط نسخة البرامج الرسمية بملفات الأيام."""
        if not isinstance(data, dict):
            return None
        if data.get("format") == "almo3in_official_programs":
            return "official_full"
        if isinstance(data.get("programs"), list) and isinstance(data.get("levels"), list) and isinstance(data.get("majals"), list):
            return "official_full"
        if "moukawan_kifaya" in data and "matiere" in data:
            return "official_tree"
        if isinstance(data.get("days"), list) or ("school_info" in data and "date" not in data):
            return "school_year"
        if "date" in data and isinstance(data.get("lessons"), list):
            return "school_day"
        return None

    def dragEnterEvent(self, event):
        # نقبل الملفات الخارجية بصيغة JSON فقط؛ السحب الداخلي بين عناصر الواجهة لا يُلتقط هنا.
        path = self._single_json_drop_path(event)
        if path:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        file_path = self._single_json_drop_path(event)
        if not file_path:
            event.ignore()
            return

        try:
            with open(file_path, "r", encoding="utf-8-sig") as source:
                data = json.load(source)
            payload_type = self._classify_json_payload(data)
            if payload_type in ("official_full", "official_tree"):
                self.pages_stack.setCurrentWidget(self.official_programs_page)
                self.import_programs_json(file_path)
            elif payload_type in ("school_year", "school_day"):
                self.process_json_import(file_path)
            else:
                QMessageBox.warning(
                    self,
                    "نوع ملف غير معروف",
                    "لم يتعرّف التطبيق على محتوى الملف. اسحب ملف JSON صادرًا عن التطبيق أو عن صفحة البرامج الرسمية.",
                )
                event.ignore()
                return
            event.acceptProposedAction()
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            QMessageBox.critical(self, "تعذّر قراءة الملف", f"لم نتمكّن من قراءة ملف JSON:\n{exc}")
            event.ignore()
        except Exception as exc:
            QMessageBox.critical(self, "خطأ أثناء الاستيراد", f"تعذّر استيراد الملف:\n{exc}")
            event.ignore()

    def import_all_years_from_json(self):
        if not self.ensure_school_year_writable():
            return
        from PySide6.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getOpenFileName(self, "اختيار ملف JSON السنوي للاستيراد", str(Path.home() / "Documents"), "ملفات JSON (*.json);;كل الملفات (*)")
        if not file_path:
            return
        self.process_json_import(file_path)

    def process_json_import(self, file_path):
        if not self.ensure_school_year_writable():
            return
        import json
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            if "days" in data:
                school_info = data.get("school_info", {})
                default_year_name = school_info.get("nom_annee", "2026-2027")
                
                dlg = YearImportDialog(self.db, default_year_name=default_year_name, parent=self)
                if not dlg.exec():
                    return
                target_year_name = dlg.get_selected_year_name()
                if not target_year_name:
                    QMessageBox.warning(self, "تنبيه", "اسم السنة الدراسية مطلوب.")
                    return

                with self.db.connect() as con:
                    cursor = con.cursor()
                    cursor.execute("SELECT id FROM ann_scolaire WHERE nom_annee = ?", (target_year_name,))
                    row = cursor.fetchone()
                    legacy_profile_columns = SCHOOL_INFO_COLUMNS[1:5]
                    extended_profile_columns = SCHOOL_INFO_COLUMNS[5:]
                    if row:
                        target_year_id = row[0]
                        assignments = [
                            f"{column}=?" for column in legacy_profile_columns
                        ] + [
                            f"{column}=COALESCE(?, {column})"
                            for column in extended_profile_columns
                        ]
                        cursor.execute(
                            f"UPDATE ann_scolaire SET {', '.join(assignments)} "
                            "WHERE id=?",
                            tuple(
                                school_info.get(column, "")
                                for column in legacy_profile_columns
                            )
                            + tuple(
                                school_info.get(column)
                                for column in extended_profile_columns
                            )
                            + (target_year_id,)
                        )
                    else:
                        insert_values = [target_year_name]
                        for column in SCHOOL_INFO_COLUMNS[1:]:
                            default_value = 0 if column == "nombre_enfants" else ""
                            insert_values.append(
                                school_info.get(column, default_value)
                            )
                        placeholders = ", ".join("?" for _ in SCHOOL_INFO_COLUMNS)
                        cursor.execute(
                            f"INSERT INTO ann_scolaire "
                            f"({', '.join(SCHOOL_INFO_COLUMNS)}) "
                            f"VALUES ({placeholders})",
                            insert_values
                        )
                        target_year_id = cursor.lastrowid

                    imported_days_count = 0
                    for day_item in data.get("days", []):
                        target_date = day_item.get("date")
                        if not target_date:
                            continue
                        
                        cursor.execute("SELECT id FROM day_data WHERE date = ? AND ann_scolaire_id = ?", (target_date, target_year_id))
                        existing_day = cursor.fetchone()
                        if existing_day:
                            cursor.execute("DELETE FROM day_data WHERE id = ?", (existing_day[0],))
                        
                        cursor.execute("INSERT INTO day_data (date, ann_scolaire_id) VALUES (?, ?)", (target_date, target_year_id))
                        new_day_id = cursor.lastrowid
                        
                        for lesson in day_item.get("lessons", []):
                            group_key = str(
                                lesson.get("class_group_key") or ""
                            ).strip()
                            classe_id, class_group_id, class_ids = (
                                self.db._resolve_lesson_class(
                                    con,
                                    group_key or lesson.get("classe", ""),
                                    (
                                        f"group:{group_key}"
                                        if group_key
                                        else lesson.get("classe_level")
                                    ),
                                )
                            )
                            majal_id, learning_mode_id = (
                                self.db.resolve_lesson_lookup_ids(
                                    con, lesson.get("majal", ""),
                                    lesson.get("namat_taallom", ""),
                                )
                            )
                            cursor.execute("""
                                INSERT INTO hessa (
                                    classe_id, majal_id, heure, time,
                                    wehda_number, hessa_number,
                                    learning_mode_id, day_data_id,
                                    class_group_id
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                classe_id, majal_id,
                                lesson.get("heure", ""), lesson.get("time", ""),
                                lesson.get("wehda_number", 1),
                                lesson.get("hessa_number", 1),
                                learning_mode_id, new_day_id, class_group_id,
                            ))
                            new_hessa_id = cursor.lastrowid
                            self.db._replace_hessa_classes(
                                con, new_hessa_id, class_ids
                            )
                            
                            for activity in lesson.get("activities", []):
                                activity_class_id = (
                                    self.db.resolve_activity_class(
                                        con, new_hessa_id,
                                        activity.get("classe"),
                                    )
                                )
                                cursor.execute("""
                                    INSERT INTO nachat(
                                        title, time, hessa_id, classe_id
                                    ) VALUES (?, ?, ?, ?)
                                """, (
                                    activity.get("title", ""),
                                    activity.get("time", ""),
                                    new_hessa_id,
                                    activity_class_id,
                                ))
                                new_nachat_id = cursor.lastrowid
                                
                                for detail in activity.get("details", []):
                                    cursor.execute("INSERT INTO nachat_detail (key_name, value_text, nachat_id) VALUES (?, ?, ?)", (detail.get("key_name", ""), detail.get("value_text", ""), new_nachat_id))
                        
                        imported_days_count += 1
                    con.commit()

                self.load_school_info()
                self.load_day()
                QMessageBox.information(self, "نجاح الاستيراد الشامل", f"تم استيراد السنة الدراسية بكافة أيامها ({imported_days_count} يوم) بنجاح!")
            else:
                target_date = data.get("date")
                if not target_date:
                    QMessageBox.warning(self, "خطأ في الملف", "ملف JSON لا يحتوي على حقل التاريخ (date) أو بيانات صحيحة.")
                    return
                with self.db.connect() as con:
                    cursor = con.cursor()
                    cursor.execute("SELECT id FROM day_data WHERE date = ? AND ann_scolaire_id = ?", (target_date, self.db.year_id))
                    existing_day = cursor.fetchone()
                    if existing_day:
                        cursor.execute("DELETE FROM day_data WHERE id = ?", (existing_day[0],))
                    cursor.execute("INSERT INTO day_data (date, ann_scolaire_id) VALUES (?, ?)", (target_date, self.db.year_id))
                    new_day_id = cursor.lastrowid
                    for lesson in data.get("lessons", []):
                        group_key = str(
                            lesson.get("class_group_key") or ""
                        ).strip()
                        classe_id, class_group_id, class_ids = (
                            self.db._resolve_lesson_class(
                                con,
                                group_key or lesson.get("classe", ""),
                                (
                                    f"group:{group_key}"
                                    if group_key
                                    else lesson.get("classe_level")
                                ),
                            )
                        )
                        majal_id, learning_mode_id = (
                            self.db.resolve_lesson_lookup_ids(
                                con, lesson.get("majal", ""),
                                lesson.get("namat_taallom", ""),
                            )
                        )
                        cursor.execute("""
                            INSERT INTO hessa (
                                classe_id, majal_id, heure, time, wehda_number,
                                hessa_number, learning_mode_id, day_data_id,
                                class_group_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            classe_id, majal_id,
                            lesson.get("heure", ""), lesson.get("time", ""),
                            lesson.get("wehda_number", 1),
                            lesson.get("hessa_number", 1),
                            learning_mode_id, new_day_id, class_group_id,
                        ))
                        new_hessa_id = cursor.lastrowid
                        self.db._replace_hessa_classes(
                            con, new_hessa_id, class_ids
                        )
                        for activity in lesson.get("activities", []):
                            activity_class_id = self.db.resolve_activity_class(
                                con, new_hessa_id, activity.get("classe")
                            )
                            cursor.execute("""
                                INSERT INTO nachat(
                                    title, time, hessa_id, classe_id
                                ) VALUES (?, ?, ?, ?)
                            """, (
                                activity.get("title", ""),
                                activity.get("time", ""),
                                new_hessa_id,
                                activity_class_id,
                            ))
                            new_nachat_id = cursor.lastrowid
                            for detail in activity.get("details", []):
                                cursor.execute("INSERT INTO nachat_detail (key_name, value_text, nachat_id) VALUES (?, ?, ?)", (detail.get("key_name", ""), detail.get("value_text", ""), new_nachat_id))
                    con.commit()
                qd = QDate.fromString(target_date, "yyyy-MM-dd")
                if qd.isValid():
                    self.date_edit.setDate(qd)
                self.load_day()
                QMessageBox.information(self, "نجاح الاستيراد", f"تم استيراد بيانات يوم {target_date} بنجاح وتسجيلها في التطبيق.")
        except Exception as e:
            QMessageBox.critical(self, "خطأ في الاستيراد", f"حدث خطأ أثناء قراءة أو استيراد ملف JSON:\n{e}")

    def clear_current_day_lessons(self):
        if not self.ensure_school_year_writable():
            return
        if not self.day_id:
            return
        if self.confirm("هل أنت متأكد من مسح جميع حصص اليوم الحالي مع أنشطتها وخصائصها؟"):
            try:
                with self.db.connect() as con:
                    cursor = con.cursor()
                    cursor.execute("DELETE FROM hessa WHERE day_data_id = ?", (self.day_id,))
                    con.commit()
                self.selected_lesson_id = None
                self.selected_activity_id = None
                self.load_day()
                self.statusBar().showMessage("تم مسح جميع حصص اليوم بنجاح.", 3000)
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"حدث خطأ أثناء مسح حصص اليوم: {e}")

    def refresh_pages_after_clearing_lessons(self):
        day_text = self.date_edit.date().toString("yyyy-MM-dd")
        self.load_day()
        self.day_lessons_page.calendar.year_id = self.db.year_id
        self.day_lessons_page.set_day(
            self.db.recorded_day_id(day_text), day_text
        )
        self.load_statistics(force_refresh=True)

        timetable_page = self.timetable_page
        timetable_page.set_automatic_schedule(
            {},
            timetable_page.automatic_first_hour,
            timetable_page.automatic_last_hour,
        )
        if getattr(self, "_timetable_thread", None) is not None:
            self._timetable_refresh_pending = True
        else:
            self.open_timetable_dialog()

    def clear_all_lessons_in_db(self):
        if not self.ensure_school_year_writable():
            return
        confirmation_code = _new_clear_confirmation_code()
        dialog = ClearAllConfirmationDialog(confirmation_code, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not _clear_confirmation_code_matches(
            dialog.code_edit.text(), confirmation_code
        ):
            QMessageBox.warning(
                self,
                "رمز التحقق غير صحيح",
                "لم يتم مسح أي بيانات لأن الرمز المدخل لا يطابق الرمز المعروض.",
            )
            return
        try:
            with self.db.connect() as con:
                cursor = con.cursor()
                cursor.execute("DELETE FROM nachat_detail")
                cursor.execute("DELETE FROM nachat")
                cursor.execute("DELETE FROM hessa")
                con.commit()
            self.selected_lesson_id = None
            self.selected_activity_id = None
            self.refresh_pages_after_clearing_lessons()
            self.statusBar().showMessage(
                "تم مسح جميع الحصص في قاعدة البيانات بالكامل.", 3000
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "تعذر مسح الحصص",
                f"حدث خطأ أثناء مسح جميع الحصص:\n{error}",
            )

    def clear_all_activities_for_lesson(self):
        if not self.ensure_school_year_writable():
            return
        if not self.selected_lesson_id:
            return self.warn_select("حصة أولاً")
        if self.confirm("هل أنت متأكد من مسح جميع أنشطة هذه الحصة وخصائصها؟"):
            try:
                with self.db.connect() as con:
                    cursor = con.cursor()
                    cursor.execute("DELETE FROM nachat WHERE hessa_id = ?", (self.selected_lesson_id,))
                    con.commit()
                self.selected_activity_id = None
                self.refresh_activities()
                self.detail_cards.clear()
                self.statusBar().showMessage("تم مسح جميع أنشطة الحصة بنجاح.", 3000)
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"حدث خطأ أثناء مسح أنشطة الحصة: {e}")

    def clear_all_details_for_activity(self):
        if not self.ensure_school_year_writable():
            return
        if not self.selected_activity_id:
            return self.warn_select("نشاط أولاً")
        if self.confirm("هل أنت متأكد من مسح جميع خصائص هذا النشاط؟"):
            try:
                with self.db.connect() as con:
                    cursor = con.cursor()
                    cursor.execute("DELETE FROM nachat_detail WHERE nachat_id = ?", (self.selected_activity_id,))
                    con.commit()
                self.refresh_details()
                self.statusBar().showMessage("تم مسح جميع خصائص النشاط بنجاح.", 3000)
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"حدث خطأ أثناء مسح خصائص النشاط: {e}")

    def copy_activity_details(self):
        if not self.selected_activity_id:
            return self.warn_select("نشاط أولاً لنسخ خصائصه")
        details = self.db.details(self.selected_activity_id)
        if not details:
            QMessageBox.information(self, "تنبيه", "النشاط المحدد لا يحتوي على خصائص لنسخها.")
            return
        self.copied_details = [(d[1], d[2]) for d in details]
        self.statusBar().showMessage(f"تم نسخ {len(self.copied_details)} خاصية بنجاح.", 3000)

    def paste_activity_details(self):
        if not self.ensure_school_year_writable():
            return
        if not self.selected_activity_id:
            return self.warn_select("نشاط أولاً لصق الخصائص فيه")
        if not self.copied_details:
            QMessageBox.warning(self, "تنبيه", "لا توجد خصائص منسوخة حالياً.")
            return
        try:
            with self.db.connect() as con:
                cursor = con.cursor()
                for key_name, value_text in self.copied_details:
                    cursor.execute("INSERT INTO nachat_detail (key_name, value_text, nachat_id) VALUES (?, ?, ?)", (key_name, value_text, self.selected_activity_id))
                con.commit()
            self.refresh_details()
            self.statusBar().showMessage("تم لصق الخصائص بنجاح.", 3000)
        except Exception as e:
            QMessageBox.critical(self, "خطأ", f"حدث خطأ أثناء لصق الخصائص: {e}")

    def export_all_days_for_year(self):
        dlg = SchoolYearSelectionDialog(self.db, parent=self)
        if not dlg.exec():
            return

        year_id, year_name = dlg.get_selected_year_info()
        if not year_id:
            QMessageBox.warning(self, "تنبيه", "لم يتم تحديد سنة دراسية.")
            return

        database_path = self.db.path

        def export_year():
            import json

            columns = ", ".join(SCHOOL_INFO_COLUMNS)
            with connect_database(database_path) as connection:
                school_info_row = connection.execute(
                    f"SELECT {columns} FROM ann_scolaire WHERE id = ?",
                    (year_id,),
                ).fetchone()
                if school_info_row is None:
                    raise ValueError("السنة الدراسية المحددة غير موجودة.")
                school_info = dict(zip(SCHOOL_INFO_COLUMNS, school_info_row))
                days = connection.execute(
                    """SELECT d.id, d.date,
                              EXISTS (
                                  SELECT 1 FROM hessa h
                                  WHERE h.day_data_id=d.id
                              )
                       FROM day_data d
                       WHERE d.ann_scolaire_id=?
                       ORDER BY d.date ASC""",
                    (year_id,),
                ).fetchall()
            if not days:
                raise ValueError(
                    f"لا توجد أيام مسجلة للسنة الدراسية: {year_name}"
                )

            all_days_data = {"school_info": school_info, "days": []}
            for day_id, day_date, has_lessons in days:
                lessons = (
                    DayLessonsBrowsePage._build_day_payload(
                        database_path, year_id, day_id
                    )["lessons"]
                    if has_lessons else []
                )
                all_days_data["days"].append({
                    "date": day_date,
                    "lessons": lessons,
                })

            documents = Path.home() / "Documents"
            documents.mkdir(parents=True, exist_ok=True)
            safe_year_name = str(year_name).replace("/", "-")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = documents / (
                f"school_year_{safe_year_name}_export_{timestamp}.json"
            )
            with output_path.open("w", encoding="utf-8") as output:
                json.dump(all_days_data, output, ensure_ascii=False, indent=4)
            return output_path

        start_background_task(
            self,
            export_year,
            lambda output_path: QMessageBox.information(
                self,
                "نجاح التصدير الشامل",
                f"تم تصدير جميع أيام السنة الدراسية ({year_name}) بنجاح "
                f"إلى مجلد المستندات:\n{output_path}",
            ),
            lambda error: QMessageBox.critical(
                self,
                "خطأ في التصدير",
                f"حدث خطأ أثناء تصدير بيانات السنة الدراسية:\n{error}",
            ),
        )

    def update_prog_actions_state(self):
        """التصدير والاستيراد الشاملان لا يتطلبان تحديد مادة بعينها."""
        self.export_prog_action.setEnabled(True)
        self.import_prog_action.setEnabled(True)

    def export_programs_json(self):
        """تصدير كامل البرامج الرسمية، من المستويات إلى المحتويات، في ملف JSON واحد."""
        from PySide6.QtWidgets import QFileDialog

        documents_dir = Path.home() / "Documents"
        start_dir = documents_dir if documents_dir.exists() else Path.home()
        default_name = start_dir / f"البرامج_الرسمية_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "تصدير البرامج الرسمية كاملة",
            str(default_name),
            "ملفات JSON (*.json);;كل الملفات (*)",
        )
        if not file_path:
            return
        if Path(file_path).suffix.lower() != ".json":
            file_path += ".json"
        database_path = self.db.path

        def export_programs():
            with connect_database(database_path) as con:
                levels = [row[0] for row in con.execute("SELECT name FROM level ORDER BY id").fetchall()]
                majals = [row[0] for row in con.execute("SELECT name FROM majal ORDER BY id").fetchall()]

                majal_comp_rows = con.execute(
                    """SELECT cmj.id, cmj.competence, m.name, l.name
                       FROM competence_majal cmj
                       JOIN majal m ON m.id = cmj.id_majal
                       JOIN level l ON l.id = cmj.id_level
                       ORDER BY l.id, m.id, cmj.id"""
                ).fetchall()
                matiere_rows = con.execute(
                    """SELECT id, matiere, competence, id_competence_majal
                       FROM competence_matiere ORDER BY id"""
                ).fetchall()
                mouk_rows = con.execute(
                    """SELECT id, competence, id_competence_matiere
                       FROM moukawan_kifaya ORDER BY id"""
                ).fetchall()
                objectif_rows = con.execute(
                    """SELECT id, objectif_specifique, description, id_moukawa_kifaya
                       FROM objectif_specifique ORDER BY id"""
                ).fetchall()
                content_rows = con.execute(
                    """SELECT content, description, id_objectif_specifique
                       FROM content ORDER BY id"""
                ).fetchall()

            contents_by_objectif = {}
            for content_text, description, objectif_id in content_rows:
                contents_by_objectif.setdefault(objectif_id, []).append({
                    "content": content_text or "", "description": description or ""
                })

            objectifs_by_mouk = {}
            for objectif_id, objectif_text, description, mouk_id in objectif_rows:
                objectifs_by_mouk.setdefault(mouk_id, []).append({
                    "objectif_specifique": objectif_text or "",
                    "description": description or "",
                    "content": contents_by_objectif.get(objectif_id, []),
                })

            mouk_by_matiere = {}
            for mouk_id, competence, matiere_id in mouk_rows:
                mouk_by_matiere.setdefault(matiere_id, []).append({
                    "competence": competence or "",
                    "objectif_specifique": objectifs_by_mouk.get(mouk_id, []),
                })

            matieres_by_competence_majal = {}
            for matiere_id, matiere, competence, competence_majal_id in matiere_rows:
                matieres_by_competence_majal.setdefault(competence_majal_id, []).append({
                    "matiere": matiere or "",
                    "competence_matiere": competence or "",
                    "moukawan_kifaya": mouk_by_matiere.get(matiere_id, []),
                })

            programs = []
            for cmj_id, competence_majal, majal_name, level_name in majal_comp_rows:
                programs.append({
                    "level": level_name,
                    "majal": majal_name,
                    "competence_majal": competence_majal or "",
                    "matieres": matieres_by_competence_majal.get(cmj_id, []),
                })

            payload = {
                "format": "almo3in_official_programs",
                "version": 1,
                "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "levels": levels,
                "majals": majals,
                "programs": programs,
            }

            target = Path(file_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, indent=2)
                output.write("\n")
            return target, len(levels), len(majals), len(programs)

        def show_export_success(result):
            target, level_count, majal_count, program_count = result
            QMessageBox.information(
                self, "تمّ التصدير",
                "تمّ تصدير البرامج الرسمية كاملة بنجاح.\n"
                f"عدد المستويات: {level_count}\n"
                f"عدد المجالات: {majal_count}\n"
                f"عدد كفايات المجالات: {program_count}\n"
                f"الملف: {target}",
            )

        start_background_task(
            self,
            export_programs,
            show_export_success,
            lambda error: QMessageBox.critical(
                self,
                "تعذّر التصدير",
                f"حدث خطأ أثناء تصدير البرامج الرسمية:\n{error}",
            ),
        )

    def import_programs_json(self, file_path=None):
        """استيراد نسخة كاملة، مع دعم ملفات الشجرة القديمة للحفاظ على التوافق."""
        from PySide6.QtWidgets import QFileDialog

        if not file_path:
            documents_dir = Path.home() / "Documents"
            start_dir = documents_dir if documents_dir.exists() else Path.home()
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "استيراد البرامج الرسمية من ملف JSON",
                str(start_dir),
                "ملفات JSON (*.json);;كل الملفات (*)",
            )
            if not file_path:
                return

        try:
            with open(file_path, "r", encoding="utf-8-sig") as source:
                data = json.load(source)
            payload_type = self._classify_json_payload(data)

            if payload_type == "official_full":
                answer = QMessageBox.question(
                    self,
                    "تأكيد استبدال البرامج الرسمية",
                    "سيُستبدل محتوى البرامج الرسمية الحالي بالكامل بالمحتوى الموجود في الملف، بما يشمل المستويات والمجالات والكفايات والأهداف والمحتويات.\n\nيُنصح بتصدير نسخة احتياطية قبل المتابعة. هل تريد مواصلة الاستيراد؟",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
                counts = self._restore_full_official_programs(data)
                self.pages_stack.setCurrentWidget(self.official_programs_page)
                self.official_programs_page.load_levels_and_majals()
                QMessageBox.information(
                    self, "تمّ الاستيراد",
                    "تمّ استيراد البرامج الرسمية بنجاح.\n"
                    f"المستويات: {counts['levels']}، المجالات: {counts['majals']}، "
                    f"كفايات المجالات: {counts['competences_majal']}، المواد: {counts['matieres']}، "
                    f"مكوّنات الكفاية: {counts['moukawan']}، الأهداف: {counts['objectifs']}، المحتويات: {counts['contents']}."
                )
                return

            if payload_type == "official_tree":
                page = self.official_programs_page
                matiere_id = page.selected_competence_matiere_id
                if not matiere_id:
                    QMessageBox.warning(
                        self, "لم تُحدّد المادة",
                        "هذا الملف يحتوي على شجرة كفاية واحدة. افتح البرامج الرسمية وحدّد المستوى والمجال والمادة المستهدفة، ثم أعد الاستيراد.",
                    )
                    return
                imported = self._import_single_competence_tree(data, matiere_id)
                if not imported:
                    return
                page.load_moukawan_data()
                QMessageBox.information(self, "تمّ الاستيراد", "تمّ استيراد شجرة الكفاية المحددة بنجاح.")
                return

            QMessageBox.warning(
                self, "ملف غير صالح",
                "لا يحتوي الملف على نسخة برامج رسمية معروفة أو شجرة كفاية قابلة للاستيراد.",
            )
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            QMessageBox.critical(self, "تعذّر قراءة الملف", f"لم نتمكّن من قراءة ملف JSON:\n{exc}")
        except Exception as exc:
            QMessageBox.critical(self, "تعذّر الاستيراد", f"حدث خطأ أثناء استيراد البرامج الرسمية:\n{exc}")

    def _restore_full_official_programs(self, data):
        """استعادة نسخة كاملة داخل معاملة واحدة؛ يُلغى التغيير كله عند وقوع خطأ."""
        if not isinstance(data, dict):
            raise ValueError("يجب أن تكون بيانات ملف النسخة الاحتياطية كائن JSON.")
        if data.get("format") not in (None, "almo3in_official_programs"):
            raise ValueError("صيغة ملف البرامج الرسمية غير مدعومة.")
        if data.get("format") == "almo3in_official_programs" and data.get("version") != 1:
            raise ValueError("إصدار ملف البرامج الرسمية غير مدعوم.")
        if not all(key in data for key in ("levels", "majals", "programs")):
            raise ValueError("ملف النسخة الاحتياطية ناقص؛ لم يقع تغيير قاعدة البيانات.")
        levels_data = data["levels"]
        majals_data = data["majals"]
        programs = data["programs"]
        if not all(isinstance(value, list) for value in (levels_data, majals_data, programs)):
            raise ValueError("بنية ملف النسخة الاحتياطية غير سليمة.")

        def get_name(value):
            if isinstance(value, str):
                return value.strip()
            if isinstance(value, dict):
                return str(value.get("name", "")).strip()
            return ""

        level_names = list(dict.fromkeys(name for name in map(get_name, levels_data) if name))
        majal_names = list(dict.fromkeys(name for name in map(get_name, majals_data) if name))
        for program in programs:
            if not isinstance(program, dict):
                raise ValueError("توجد كفاية مجال بصيغة غير سليمة داخل الملف.")
            level_name = str(program.get("level", "")).strip()
            majal_name = str(program.get("majal", "")).strip()
            if level_name and level_name not in level_names:
                level_names.append(level_name)
            if majal_name and majal_name not in majal_names:
                majal_names.append(majal_name)
            if not isinstance(program.get("matieres", []), list):
                raise ValueError("قائمة المواد في الملف غير سليمة.")

        counts = {
            "levels": 0, "majals": 0, "competences_majal": 0, "matieres": 0,
            "moukawan": 0, "objectifs": 0, "contents": 0,
        }
        with self.db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            # حذف مرتب لتبقى العلاقات سليمة حتى عند تعطيل بعض قيود الحماية خارج التطبيق.
            con.execute("DELETE FROM content")
            con.execute("DELETE FROM objectif_specifique")
            con.execute("DELETE FROM moukawan_kifaya")
            con.execute("DELETE FROM competence_matiere")
            con.execute("DELETE FROM competence_majal")
            con.execute("""
                DELETE FROM level
                WHERE id NOT IN (SELECT level_id FROM classe)
            """)
            con.execute("""
                DELETE FROM majal
                WHERE id NOT IN (
                    SELECT majal_id FROM hessa WHERE majal_id IS NOT NULL
                )
                AND id NOT IN (SELECT majal_id FROM teaching_plan)
            """)

            level_ids = {}
            for name in level_names:
                con.execute("INSERT OR IGNORE INTO level(name) VALUES(?)", (name,))
                level_ids[name] = con.execute("SELECT id FROM level WHERE name=?", (name,)).fetchone()[0]
            counts["levels"] = len(level_ids)

            majal_ids = {}
            for name in majal_names:
                con.execute("INSERT OR IGNORE INTO majal(name) VALUES(?)", (name,))
                majal_ids[name] = con.execute("SELECT id FROM majal WHERE name=?", (name,)).fetchone()[0]
            counts["majals"] = len(majal_ids)

            for program in programs:
                level_name = str(program.get("level", "")).strip()
                majal_name = str(program.get("majal", "")).strip()
                competence_majal = str(program.get("competence_majal", "")).strip()
                if not level_name or not majal_name or not competence_majal:
                    # تجاهل كفايات المجال غير المكتملة بدلاً من إنشاء سجلات عديمة الفائدة.
                    continue
                level_id = level_ids[level_name]
                majal_id = majal_ids[majal_name]
                con.execute(
                    "INSERT OR IGNORE INTO competence_majal(competence, id_majal, id_level) VALUES(?,?,?)",
                    (competence_majal, majal_id, level_id),
                )
                cmj_id = con.execute(
                    "SELECT id FROM competence_majal WHERE competence=? AND id_majal=? AND id_level=?",
                    (competence_majal, majal_id, level_id),
                ).fetchone()[0]
                counts["competences_majal"] += 1

                for matiere in program.get("matieres", []):
                    if not isinstance(matiere, dict):
                        raise ValueError("توجد مادة بصيغة غير سليمة داخل الملف.")
                    matiere_name = str(matiere.get("matiere", "")).strip()
                    competence_matiere = str(matiere.get("competence_matiere", "")).strip()
                    if not matiere_name or not competence_matiere:
                        continue
                    con.execute(
                        "INSERT OR IGNORE INTO competence_matiere(matiere, competence, id_competence_majal) VALUES(?,?,?)",
                        (matiere_name, competence_matiere, cmj_id),
                    )
                    matiere_id = con.execute(
                        "SELECT id FROM competence_matiere WHERE matiere=? AND competence=? AND id_competence_majal=?",
                        (matiere_name, competence_matiere, cmj_id),
                    ).fetchone()[0]
                    counts["matieres"] += 1

                    mouk_list = matiere.get("moukawan_kifaya", [])
                    if not isinstance(mouk_list, list):
                        raise ValueError("قائمة مكوّنات الكفاية غير سليمة.")
                    for mouk in mouk_list:
                        if isinstance(mouk, str):
                            mouk_text, objectifs = mouk.strip(), []
                        elif isinstance(mouk, dict):
                            mouk_text = str(mouk.get("competence", "")).strip()
                            objectifs = mouk.get("objectif_specifique", [])
                            if not isinstance(objectifs, list):
                                raise ValueError("قائمة الأهداف المميزة غير سليمة.")
                        else:
                            raise ValueError("يوجد مكوّن كفاية بصيغة غير سليمة.")
                        if not mouk_text:
                            continue
                        con.execute(
                            "INSERT OR IGNORE INTO moukawan_kifaya(competence, id_competence_matiere) VALUES(?,?)",
                            (mouk_text, matiere_id),
                        )
                        mouk_id = con.execute(
                            "SELECT id FROM moukawan_kifaya WHERE competence=? AND id_competence_matiere=?",
                            (mouk_text, matiere_id),
                        ).fetchone()[0]
                        counts["moukawan"] += 1

                        for objectif in objectifs:
                            if isinstance(objectif, str):
                                objectif_text, description, contents = objectif.strip(), "", []
                            elif isinstance(objectif, dict):
                                objectif_text = str(objectif.get("objectif_specifique", "")).strip()
                                description = str(objectif.get("description", "") or "")
                                contents = objectif.get("content", [])
                                if not isinstance(contents, list):
                                    raise ValueError("قائمة المحتويات غير سليمة.")
                            else:
                                raise ValueError("يوجد هدف مميز بصيغة غير سليمة.")
                            if not objectif_text:
                                continue
                            con.execute(
                                "INSERT OR IGNORE INTO objectif_specifique(objectif_specifique, id_moukawa_kifaya, description) VALUES(?,?,?)",
                                (objectif_text, mouk_id, description),
                            )
                            objectif_id = con.execute(
                                "SELECT id FROM objectif_specifique WHERE objectif_specifique=? AND id_moukawa_kifaya=?",
                                (objectif_text, mouk_id),
                            ).fetchone()[0]
                            counts["objectifs"] += 1

                            for content in contents:
                                if isinstance(content, str):
                                    content_text, content_description = content.strip(), ""
                                elif isinstance(content, dict):
                                    content_text = str(content.get("content", "")).strip()
                                    content_description = str(content.get("description", "") or "")
                                else:
                                    raise ValueError("يوجد محتوى بصيغة غير سليمة.")
                                if not content_text:
                                    continue
                                con.execute(
                                    "INSERT OR IGNORE INTO content(content, id_objectif_specifique, description) VALUES(?,?,?)",
                                    (content_text, objectif_id, content_description),
                                )
                                counts["contents"] += 1
            con.commit()
        return counts

    def _import_single_competence_tree(self, data, matiere_id):
        """توافق رجعي مع ملفات JSON القديمة التي تصدّر شجرة مادة واحدة."""
        file_majal = str(data.get("majal", "")).strip()
        file_matiere = str(data.get("matiere", "")).strip()
        with self.db.connect() as con:
            current_info = con.execute(
                """SELECT m.name, cm.matiere
                   FROM competence_matiere cm
                   JOIN competence_majal cmj ON cmj.id = cm.id_competence_majal
                   JOIN majal m ON m.id = cmj.id_majal
                   WHERE cm.id = ?""",
                (matiere_id,),
            ).fetchone()
            if not current_info:
                raise ValueError("المادة المحددة لم تعد موجودة في قاعدة البيانات.")
            current_majal, current_matiere = current_info[0].strip(), current_info[1].strip()
            if (file_majal and file_majal != current_majal) or (file_matiere and file_matiere != current_matiere):
                raise ValueError(
                    "لا تتطابق بيانات الملف مع المادة المحددة.\n"
                    f"الملف: المجال {file_majal}، المادة {file_matiere}.\n"
                    f"التحديد الحالي: المجال {current_majal}، المادة {current_matiere}."
                )
            tree = data.get("moukawan_kifaya", [])
            if not isinstance(tree, list):
                raise ValueError("قائمة مكوّنات الكفاية في الملف غير سليمة.")
            answer = QMessageBox.question(
                self,
                "تأكيد استبدال شجرة الكفاية",
                "سيتم حذف الشجرة الحالية للمادة المحددة وتعويضها بالشجرة الموجودة في الملف. هل تواصل؟",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return False

            con.execute("BEGIN IMMEDIATE")
            con.execute("DELETE FROM moukawan_kifaya WHERE id_competence_matiere = ?", (matiere_id,))
            for mouk in tree:
                if isinstance(mouk, str):
                    mouk_text, objectifs = mouk.strip(), []
                elif isinstance(mouk, dict):
                    mouk_text = str(mouk.get("competence", "")).strip()
                    objectifs = mouk.get("objectif_specifique", [])
                else:
                    raise ValueError("يوجد مكوّن كفاية بصيغة غير سليمة.")
                if not mouk_text:
                    continue
                cur = con.execute(
                    "INSERT INTO moukawan_kifaya(competence, id_competence_matiere) VALUES(?,?)",
                    (mouk_text, matiere_id),
                )
                new_mouk_id = cur.lastrowid
                if not isinstance(objectifs, list):
                    raise ValueError("قائمة الأهداف المميزة غير سليمة.")
                for obj in objectifs:
                    if isinstance(obj, str):
                        obj_text, obj_desc, contents = obj.strip(), "", []
                    elif isinstance(obj, dict):
                        obj_text = str(obj.get("objectif_specifique", "")).strip()
                        obj_desc = str(obj.get("description", "") or "")
                        contents = obj.get("content", [])
                    else:
                        raise ValueError("يوجد هدف مميز بصيغة غير سليمة.")
                    if not obj_text:
                        continue
                    if not isinstance(contents, list):
                        raise ValueError("قائمة المحتويات غير سليمة.")
                    obj_cur = con.execute(
                        "INSERT INTO objectif_specifique(objectif_specifique, id_moukawa_kifaya, description) VALUES(?,?,?)",
                        (obj_text, new_mouk_id, obj_desc),
                    )
                    new_obj_id = obj_cur.lastrowid
                    for cnt in contents:
                        if isinstance(cnt, str):
                            cnt_text, cnt_desc = cnt.strip(), ""
                        elif isinstance(cnt, dict):
                            cnt_text = str(cnt.get("content", "")).strip()
                            cnt_desc = str(cnt.get("description", "") or "")
                        else:
                            raise ValueError("يوجد محتوى بصيغة غير سليمة.")
                        if cnt_text:
                            con.execute(
                                "INSERT INTO content(content, id_objectif_specifique, description) VALUES(?,?,?)",
                                (cnt_text, new_obj_id, cnt_desc),
                            )
            con.commit()
        return True


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setLayoutDirection(Qt.RightToLeft)
    arabic_dialog_translator = ArabicDialogTranslator()
    app.installTranslator(arabic_dialog_translator)

    custom_font_family = load_custom_font()
    app.setFont(QFont(custom_font_family, 10))

    style_file = APP_DIR / "style_1.qss"
    if style_file.exists():
        with open(style_file, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())

    try:
        window = SchoolPlanner()
    except sqlite3.Error as exc:
        QMessageBox.critical(
            None,
            "تعذّر فتح قاعدة البيانات",
            f"تعذّر فتح قاعدة البيانات:\n{exc}",
        )
        sys.exit(1)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
