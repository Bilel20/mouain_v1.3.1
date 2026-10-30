# -*- coding: utf-8 -*-
"""Teacher timetable page with automatic and editable weekly schedules."""
import hashlib
import html
import json
import math
import sqlite3
import uuid

from PySide6.QtCore import QEvent, QMimeData, QRect, QTime, Qt, Signal
from PySide6.QtGui import QDrag, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from app_config import (
    PLANS_NAMES,
    set_dialog_button_action_types,
)
from class_assignment_ui import add_assignment_editor


WEEKDAYS = (
    "الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت",
)
CARD_COLORS = (
    ("#2563EB", "#EFF6FF"),
    ("#059669", "#ECFDF5"),
    ("#D97706", "#FFFBEB"),
    ("#7C3AED", "#F5F3FF"),
    ("#DB2777", "#FDF2F8"),
    ("#0891B2", "#ECFEFF"),
    ("#4F46E5", "#EEF2FF"),
    ("#65A30D", "#F7FEE7"),
)
CARD_MIME_TYPE = "application/x-teacher-timetable-card"


class TimetableHourHeader(QHeaderView):
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._hour_labels = []
        self.setSectionsClickable(False)
        self.setMinimumHeight(44)

    def set_hour_labels(self, labels):
        self._hour_labels = list(labels)
        self.updateGeometry()
        self.viewport().update()

    def paintEvent(self, _event):
        painter = QPainter(self.viewport())
        painter.setFont(self.font())
        for hour_index, label in enumerate(self._hour_labels):
            first_column = hour_index * 2
            second_column = first_column + 1
            if second_column >= self.count():
                break
            first_x = self.sectionViewportPosition(first_column)
            second_x = self.sectionViewportPosition(second_column)
            left = min(first_x, second_x)
            right = max(
                first_x + self.sectionSize(first_column),
                second_x + self.sectionSize(second_column),
            )
            rect = QRect(left + 2, 2, right - left - 4, self.height() - 4)
            painter.setPen(QPen(self.palette().mid().color(), 1))
            painter.setBrush(self.palette().button())
            painter.drawRoundedRect(rect, 7, 7)
            painter.setPen(QPen(self.palette().text().color(), 1))
            painter.drawText(
                rect.adjusted(4, 0, -4, 0),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )
            painter.setPen(QPen(self.palette().midlight().color(), 1))
            painter.drawLine(
                rect.left() + rect.width() // 2,
                rect.top() + 2,
                rect.left() + rect.width() // 2,
                rect.bottom() - 2,
            )


def _card_color(card):
    key = f"{card.get('class_id')}:{card.get('lesson_name')}"
    index = int.from_bytes(
        hashlib.sha256(key.encode("utf-8")).digest()[:4], "big"
    ) % len(CARD_COLORS)
    return CARD_COLORS[index]


def _duration_text(duration):
    duration = float(duration)
    return {
        0.5: "نصف ساعة",
        1: "ساعة",
        1.5: "ساعة ونصف",
        2: "ساعتان",
    }.get(duration, f"{duration:g} ساعة")


class TimetableLessonDialog(QDialog):
    def __init__(self, db, parent=None, card=None):
        super().__init__(parent)
        self.db = db
        self.card = card
        self.setWindowTitle(
            "تعديل حصة الجدول" if card else "إضافة حصة إلى الجدول"
        )
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(390)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.level_combo = QComboBox()
        self.level_combo.addItem("اختر المستوى", None)
        for level_id, label in self.db.teaching_assignment_levels():
            self.level_combo.addItem(label, level_id)

        self.class_combo = QComboBox()
        self.class_combo.addItem("اختر المستوى أولاً", None)
        self.class_combo.setEnabled(False)
        self.level_combo.currentIndexChanged.connect(self.load_classes)
        self.class_combo.currentIndexChanged.connect(
            self._update_assigned_lessons
        )

        self.lesson_combo = QComboBox()
        self.domain_label = QLabel()
        self.lesson_combo.currentTextChanged.connect(
            self.update_domain_label
        )
        self.duration_combo = QComboBox()
        self.duration_combo.addItem("نصف ساعة", 0.5)
        self.duration_combo.addItem("ساعة واحدة", 1)
        self.duration_combo.addItem("ساعة ونصف", 1.5)
        self.duration_combo.addItem("ساعتان", 2)

        if card:
            level_id = card.get("level_id")
            if level_id is None and card.get("class_group_key"):
                level_id = f"group:{card['class_group_key']}"
            elif level_id is None:
                level_id = next(
                    (
                        class_level_id
                        for class_id, _name, class_level_id
                        in self.db.classes()
                        if class_id == card.get("class_id")
                    ),
                    None,
                )
            self.level_combo.setCurrentIndex(
                self.level_combo.findData(level_id)
            )
            self.class_combo.setCurrentIndex(
                self.class_combo.findData(card.get("class_id"))
            )
            lesson_index = self.lesson_combo.findData(card.get("lesson_name"))
            if lesson_index >= 0:
                self.lesson_combo.setCurrentIndex(lesson_index)
            duration_index = self.duration_combo.findData(
                card.get("duration_hours", 1)
            )
            if duration_index >= 0:
                self.duration_combo.setCurrentIndex(duration_index)
            self.level_combo.setEnabled(False)
            self.class_combo.setEnabled(False)
            self.lesson_combo.setEnabled(False)
            self.duration_combo.setEnabled(True)

        form.addRow("المستوى:", self.level_combo)
        form.addRow(
            "القسم:",
            add_assignment_editor(
                self.class_combo,
                self.db,
                self,
                self._refresh_assigned_options,
            ),
        )
        form.addRow("اسم الحصة:", self.lesson_combo)
        form.addRow("المجال:", self.domain_label)
        form.addRow("المدة:", self.duration_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox()
        buttons.addButton(
            "حفظ التعديل" if card else "إضافة",
            QDialogButtonBox.AcceptRole,
        )
        buttons.addButton("إلغاء", QDialogButtonBox.RejectRole)
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_domain_label()
        self._update_assigned_lessons()

    def load_classes(self, *_args):
        level_id = self.level_combo.currentData()
        selected_class = self.class_combo.currentData()
        self.class_combo.clear()
        if level_id is None:
            self.class_combo.addItem("اختر المستوى أولاً", None)
            self.class_combo.setEnabled(False)
            return
        self.class_combo.addItem("اختر القسم", None)
        classes = [
            option
            for option in self.db.teaching_assignment_classes(
                include_groups=True
            )
            if option["level_id"] == level_id
        ]
        for option in classes:
            self.class_combo.addItem(option["name"], option["id"])
        selected_index = self.class_combo.findData(selected_class)
        if selected_index >= 0:
            self.class_combo.setCurrentIndex(selected_index)
        self.class_combo.setEnabled(bool(classes))
        self._update_assigned_lessons()

    def _update_assigned_lessons(self, *_args):
        class_id = self.class_combo.currentData()
        assignment = next(
            (
                option
                for option in self.db.teaching_assignment_classes(
                    include_groups=True
                )
                if option["id"] == class_id
            ),
            None,
        )
        selected_lesson = self.lesson_combo.currentData()
        allowed_lessons = (
            assignment["subjects"] if assignment is not None else []
        )
        self.lesson_combo.blockSignals(True)
        self.lesson_combo.clear()
        for lesson_name in allowed_lessons:
            if lesson_name in PLANS_NAMES:
                self.lesson_combo.addItem(lesson_name, lesson_name)
        selected_index = self.lesson_combo.findData(selected_lesson)
        if selected_index >= 0:
            self.lesson_combo.setCurrentIndex(selected_index)
        self.lesson_combo.blockSignals(False)
        self.update_domain_label()

    def _refresh_assigned_options(self):
        selected_level = self.level_combo.currentData()
        self.level_combo.blockSignals(True)
        self.level_combo.clear()
        self.level_combo.addItem("اختر المستوى", None)
        for level_id, label in self.db.teaching_assignment_levels():
            self.level_combo.addItem(label, level_id)
        index = self.level_combo.findData(selected_level)
        self.level_combo.setCurrentIndex(index if index >= 0 else 0)
        self.level_combo.blockSignals(False)
        self.load_classes()

    def update_domain_label(self, *_args):
        details = PLANS_NAMES.get(self.lesson_combo.currentText(), {})
        self.domain_label.setText(str(details.get("المجال") or ""))

    def accept(self):
        if self.level_combo.currentData() is None:
            QMessageBox.warning(self, "بيانات ناقصة", "يرجى اختيار المستوى.")
            return
        if self.class_combo.currentData() is None:
            QMessageBox.warning(self, "بيانات ناقصة", "يرجى اختيار القسم.")
            return
        if self.lesson_combo.currentData() is None:
            QMessageBox.warning(
                self, "مادة التدريس مطلوبة",
                "لا توجد مادة تدريس مسجلة للقسم المحدد.",
            )
            return
        super().accept()

    def card_values(self):
        class_name = self.class_combo.currentText().strip()
        selected_id = self.class_combo.currentData()
        groups = self.db.class_groups()
        group_key = None
        class_id = None
        class_ids = []
        if isinstance(selected_id, str) and selected_id.startswith("group:"):
            candidate_group_key = selected_id[len("group:"):]
            if (
                candidate_group_key in groups
                and class_name == self.class_combo.itemText(
                    self.class_combo.currentIndex()
                )
            ):
                group_key = candidate_group_key
                class_id = selected_id
                class_ids = [
                    member["id"]
                    for member in groups[group_key]["classes"]
                ]
        elif isinstance(selected_id, int):
            class_id = selected_id
            class_ids = [selected_id]
        if class_id is None:
            matching_class = next(
                (
                    known_id
                    for known_id, known_name, known_level_id
                    in self.db.classes()
                    if known_level_id == self.level_combo.currentData()
                    and known_name.strip().casefold() == class_name.casefold()
                ),
                None,
            )
            if matching_class is not None:
                class_id = matching_class
                class_ids = [matching_class]
            else:
                class_id = (
                    f"custom:{self.level_combo.currentData()}:"
                    f"{class_name.casefold()}"
                )
        duration_hours = self.duration_combo.currentData()
        duration_value = float(duration_hours) if duration_hours is not None else 1.0
        return {
            "id": self.card["id"] if self.card else uuid.uuid4().hex,
            "level_id": self.level_combo.currentData(),
            "level_name": self.level_combo.currentText(),
            "class_id": class_id,
            "class_group_key": group_key,
            "class_ids": class_ids,
            "class_name": class_name,
            "lesson_name": self.lesson_combo.currentText(),
            "domain": str(
                PLANS_NAMES[self.lesson_combo.currentText()].get("المجال") or ""
            ),
            "duration_hours": duration_value,
            "assignment_id": (
                self.card.get("assignment_id") if self.card else None
            ),
        }


class TimetableDurationDialog(QDialog):
    def __init__(self, current_duration, parent=None):
        super().__init__(parent)
        self.setWindowTitle("تغيير مدة الحصة")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(240)
        layout = QVBoxLayout(self)
        self.duration_combo = QComboBox()
        self.duration_combo.addItem("نصف ساعة", 0.5)
        self.duration_combo.addItem("ساعة", 1)
        self.duration_combo.addItem("ساعة ونصف", 1.5)
        self.duration_combo.addItem("ساعتان", 2)
        current_index = self.duration_combo.findData(
            float(current_duration)
        )
        if current_index >= 0:
            self.duration_combo.setCurrentIndex(current_index)
        layout.addWidget(self.duration_combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_duration(self):
        return float(self.duration_combo.currentData())


class TimetableHoursDialog(QDialog):
    def __init__(self, start_time, end_time, parent=None):
        super().__init__(parent)
        self.setWindowTitle("تحديد ساعة الانطلاق والانتهاء")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.start_time_edit = QTimeEdit(
            QTime.fromString(start_time, "HH:mm")
        )
        self.start_time_edit.setLayoutDirection(Qt.LeftToRight)
        self.start_time_edit.setDisplayFormat("HH:mm")
        self.start_time_edit.setTimeRange(QTime(0, 0), QTime(22, 0))
        self.end_time_edit = QTimeEdit(
            QTime.fromString(end_time, "HH:mm")
        )
        self.end_time_edit.setLayoutDirection(Qt.LeftToRight)
        self.end_time_edit.setDisplayFormat("HH:mm")
        self.end_time_edit.setTimeRange(QTime(1, 0), QTime(23, 0))
        form.addRow("ساعة الانطلاق:", self.start_time_edit)
        form.addRow("ساعة الانتهاء:", self.end_time_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return self.start_time_edit.time(), self.end_time_edit.time()

    def accept(self):
        start_time, end_time = self.values()
        if end_time <= start_time:
            QMessageBox.warning(
                self,
                "ساعات العمل",
                "يجب أن تكون ساعة الانتهاء بعد ساعة الانطلاق.",
            )
            return
        super().accept()


class TimetableSettingsDialog(QDialog):
    def __init__(self, days, start_time, end_time, parent=None):
        super().__init__(parent)
        self.setWindowTitle(
            "إنشاء الجدول اليدوي" if not days else "تعديل إعدادات الجدول"
        )
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        days_widget = QWidget()
        days_layout = QHBoxLayout(days_widget)
        days_layout.setContentsMargins(0, 0, 0, 0)
        self.day_checks = []
        for index, day_name in enumerate(WEEKDAYS):
            check = QCheckBox(day_name)
            check.setChecked(index in days)
            self.day_checks.append(check)
            days_layout.addWidget(check)
        form.addRow("أيام العمل:", days_widget)

        self.start_time_edit = QTimeEdit(
            QTime.fromString(start_time, "HH:mm")
        )
        self.start_time_edit.setDisplayFormat("HH:mm")
        self.start_time_edit.setTimeRange(QTime(0, 0), QTime(22, 0))
        self.end_time_edit = QTimeEdit(
            QTime.fromString(end_time, "HH:mm")
        )
        self.end_time_edit.setDisplayFormat("HH:mm")
        self.end_time_edit.setTimeRange(QTime(1, 0), QTime(23, 0))
        form.addRow("ساعة الانطلاق:", self.start_time_edit)
        form.addRow("ساعة الانتهاء:", self.end_time_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox()
        buttons.addButton("حفظ", QDialogButtonBox.AcceptRole)
        buttons.addButton("إلغاء", QDialogButtonBox.RejectRole)
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return (
            [
                index for index, check in enumerate(self.day_checks)
                if check.isChecked()
            ],
            self.start_time_edit.time(),
            self.end_time_edit.time(),
        )

    def accept(self):
        days, start_time, end_time = self.values()
        if not days:
            QMessageBox.warning(
                self, "أيام العمل", "يرجى اختيار يوم عمل واحد على الأقل."
            )
            return
        if start_time.minute() or end_time.minute():
            QMessageBox.warning(
                self, "ساعات العمل", "يرجى تحديد ساعات كاملة دون دقائق."
            )
            return
        if end_time <= start_time:
            QMessageBox.warning(
                self,
                "ساعات العمل",
                "يجب أن تكون ساعة الانتهاء بعد ساعة الانطلاق.",
            )
            return
        super().accept()


class TimetableEntryCell(QFrame):
    removeRequested = Signal(int, str)

    def __init__(
        self, card, weekday, start_time, parent=None, duration_hours=None
    ):
        super().__init__(parent)
        self.card_id = card["id"]
        self._drag_position = None
        self._drag_label = None
        self._card_frame = QFrame(self)
        self._card_frame.setObjectName("timetableEntryCell")
        self._card_frame.setLayoutDirection(Qt.LeftToRight)
        self._card_frame.setCursor(Qt.OpenHandCursor)
        self._card_frame.installEventFilter(self)
        accent, background = _card_color(card)
        self._card_frame.setStyleSheet(
            "QFrame#timetableEntryCell {"
            f"background-color: {background}; border: 1px solid {accent}; "
            f"border-radius: 6px; }}"
        )
        layout = QGridLayout(self._card_frame)
        layout.setContentsMargins(3, 2, 3, 3)
        layout.setSpacing(0)
        label = QLabel(
            f"{card['class_name']} — {card.get('level_name', '')}\n"
            f"{card['lesson_name']}"
        )
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setCursor(Qt.OpenHandCursor)
        label.installEventFilter(self)
        self._drag_label = label
        label.setStyleSheet(
            "color: #0F172A; border: none; background: transparent;"
        )
        layout.addWidget(label, 0, 0, 2, 2)
        remove_button = QToolButton(self)
        remove_button.setObjectName("timetableEntryRemoveButton")
        remove_button.setText("×")
        remove_button.setToolTip("إزالة الحصة من الجدول")
        remove_button.setCursor(Qt.PointingHandCursor)
        remove_button.clicked.connect(
            lambda: self.removeRequested.emit(weekday, start_time)
        )
        layout.addWidget(remove_button, 0, 1, Qt.AlignTop | Qt.AlignRight)
        entry_duration = (
            duration_hours if duration_hours is not None
            else card.get("duration_hours", 1)
        )
        self.setToolTip(
            f"{card['class_name']} — {card['lesson_name']}\n"
            f"المجال: {card['domain']}\n"
            f"المدة: {float(entry_duration):g} ساعة"
        )

    def eventFilter(self, watched, event):
        if watched not in (self._card_frame, self._drag_label):
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_position = event.globalPosition().toPoint()
        elif event.type() == QEvent.Type.MouseMove:
            if (
                self._drag_position is not None
                and event.buttons() & Qt.MouseButton.LeftButton
                and (
                    event.globalPosition().toPoint() - self._drag_position
                ).manhattanLength() >= QApplication.startDragDistance()
            ):
                self._drag_position = None
                self._start_copy_drag()
                return True
        elif event.type() == QEvent.Type.MouseButtonRelease:
            self._drag_position = None
        return super().eventFilter(watched, event)

    def _start_copy_drag(self):
        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setData(
            CARD_MIME_TYPE, self.card_id.encode("utf-8")
        )
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.CopyAction)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._card_frame.setGeometry(self.rect())


class AutomaticTimetableCell(QFrame):
    def __init__(self, text, count, parent=None):
        super().__init__(parent)
        class_name, separator, domain = text.partition(":")
        color_key = {
            "class_id": class_name,
            "lesson_name": domain if separator else class_name,
        }
        accent, background = _card_color(color_key)
        self.setObjectName("automaticTimetableCell")
        self.setStyleSheet(
            "QFrame#automaticTimetableCell {"
            f"background-color: {background}; border: 1px solid {accent}; "
            f"border-right: 4px solid {accent}; border-radius: 6px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 4, 5, 4)
        label = QLabel(
            f"{class_name}\n{domain}" if separator else class_name
        )
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(
            "color: #0F172A; border: none; background: transparent;"
        )
        layout.addWidget(label)
        self.setToolTip(f"{text}\nتكرّرت {count} مرة")


class TimetableLessonCard(QFrame):
    selected = Signal(str)
    durationChangeRequested = Signal(str)

    def __init__(self, card, parent=None):
        super().__init__(parent)
        self.card = card
        self._drag_position = None
        self.setObjectName("teacherTimetableLessonCard")
        accent, background = _card_color(card)
        self.setStyleSheet(
            "QFrame#teacherTimetableLessonCard {"
            f"background-color: {background}; border: 1px solid {accent}; "
            f"border-right: 4px solid {accent}; border-radius: 8px;"
            f"}} QFrame#teacherTimetableLessonCard[selected=\"true\"] "
            f"{{border: 3px solid {accent};}}"
        )
        self.setCursor(Qt.OpenHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(2)
        title = QLabel(
            f"{card.get('class_name', '')} — "
            f"{card.get('level_name', '')}\n"
            f"{card.get('lesson_name', '')}"
        )
        title.setWordWrap(True)
        title.setStyleSheet(
            "font-weight: bold; color: #0F172A; border: none; "
            "background: transparent;"
        )
        layout.addWidget(title)
        duration = float(card.get("duration_hours", 1))
        duration_text = _duration_text(duration)
        details = QLabel(
            f"{html.escape(str(card.get('domain', '')))} · "
            f"<a href=\"change-duration\">"
            f"{html.escape(duration_text)}</a>"
        )
        details.setTextFormat(Qt.TextFormat.RichText)
        details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        details.setOpenExternalLinks(False)
        details.setCursor(Qt.PointingHandCursor)
        details.linkActivated.connect(
            lambda _link: self.durationChangeRequested.emit(
                self.card["id"]
            )
        )
        details.setToolTip("اضغط لتغيير مدة الحصة")
        details.setStyleSheet(
            "font-size: 9pt; color: #475569; border: none; "
            "background: transparent;"
        )
        layout.addWidget(details)
        self.setFixedSize(140, 140)
        self.setToolTip(
            f"المستوى: {card.get('level_name', '')}\n"
            f"القسم: {card.get('class_name', '')}\n"
            f"الحصة: {card.get('lesson_name', '')}\n"
            f"المجال: {card.get('domain', '')}\n"
            f"المدة: {_duration_text(card.get('duration_hours', 1))}\n"
            "اسحب البطاقة إلى خانة اليوم والساعة."
        )

    def mousePressEvent(self, event):
        self._drag_position = event.position().toPoint()
        self.selected.emit(self.card["id"])
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_position is None
            or not event.buttons() & Qt.LeftButton
            or (event.position().toPoint() - self._drag_position)
            .manhattanLength() < QApplication.startDragDistance()
        ):
            return
        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setData(CARD_MIME_TYPE, self.card["id"].encode("utf-8"))
        drag.setMimeData(mime_data)
        drag.exec(Qt.CopyAction)


class FloatingLessonPalette(QDialog):
    cardSelected = Signal(str)
    durationChangeRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint,
        )
        self.setObjectName("teacherTimetableLessonPalette")
        self.setWindowTitle("حصص الجدول اليدوي")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(520, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.cards_scroll = QScrollArea(self)
        self.cards_scroll.setWidgetResizable(True)
        self.cards_container = QWidget()
        self.cards_layout = QGridLayout(self.cards_container)
        self.cards_layout.setAlignment(Qt.AlignTop)
        self.cards_layout.setContentsMargins(6, 6, 6, 6)
        self.cards_layout.setSpacing(8)
        self.cards_scroll.setWidget(self.cards_container)
        layout.addWidget(self.cards_scroll)
        self.set_cards([])

    def set_cards(self, cards):
        filtered_cards = [
            card for card in cards
            if isinstance(card, dict) and card.get("assignment_id") is not None
        ]
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        if not filtered_cards:
            empty_label = QLabel(
                "أضف مواد التدريس من نافذة تعيين الأقسام والأنشطة."
            )
            empty_label.setWordWrap(True)
            empty_label.setAlignment(Qt.AlignCenter)
            self.cards_layout.addWidget(empty_label, 0, 0, 1, 2)
            return
        activity_counts = {}
        counted_lessons = set()
        for card in filtered_cards:
            domain_key = (
                card.get("class_id"),
                card.get("domain", ""),
            )
            lesson_key = (*domain_key, card.get("lesson_name", ""))
            if lesson_key in counted_lessons:
                continue
            counted_lessons.add(lesson_key)
            activities = PLANS_NAMES.get(
                card.get("lesson_name", ""), {}
            ).get("الأنشطة", [])
            activity_counts[domain_key] = (
                activity_counts.get(domain_key, 0) + len(activities)
            )
        sorted_cards = sorted(
            filtered_cards,
            key=lambda card: (
                str(card.get("class_name", "")).strip().casefold(),
                -activity_counts.get(
                    (card.get("class_id"), card.get("domain", "")), 0
                ),
                str(card.get("domain", "")).strip().casefold(),
                str(card.get("lesson_name", "")).strip().casefold(),
            ),
        )
        card_widgets = []
        for card in sorted_cards:
            card_widget = TimetableLessonCard(card, self.cards_container)
            card_widget.selected.connect(self.cardSelected)
            card_widget.durationChangeRequested.connect(
                self.durationChangeRequested
            )
            card_widget.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )
            card_widgets.append(card_widget)
        self._palette_card_widgets = card_widgets
        self._relayout_cards()
        QApplication.processEvents()
        self._relayout_cards()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_cards()

    def _relayout_cards(self):
        if not hasattr(self, "cards_layout") or not hasattr(self, "_palette_card_widgets"):
            return
        while self.cards_layout.count():
            self.cards_layout.takeAt(0)
        if not self._palette_card_widgets:
            return
        available_width = max(280, self.width() - 16)
        if available_width >= 420:
            columns = min(3, len(self._palette_card_widgets))
        else:
            columns = min(
                max(1, available_width // 150),
                len(self._palette_card_widgets),
            )
        total_rows = (
            len(self._palette_card_widgets) + columns - 1
        ) // columns
        for row in range(total_rows):
            row_widgets = self._palette_card_widgets[
                row * columns:(row + 1) * columns
            ]
            offset = (columns - len(row_widgets)) // 2
            for column, card_widget in enumerate(row_widgets):
                self.cards_layout.addWidget(
                    card_widget, row, column + offset
                )
        self.cards_layout.setAlignment(
            Qt.AlignHCenter | Qt.AlignTop
        )


class TeacherTimetableGrid(QTableWidget):
    entryDropped = Signal(int, int, str)
    entryDragMoved = Signal(int, int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setShowGrid(True)
        self._drag_start_guide = QFrame(self.viewport())
        self._drag_start_guide.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._drag_start_guide.setStyleSheet(
            "background-color: #2563EB; border: none;"
        )
        self._drag_start_guide.hide()
        self._drag_boundary_lines = []
        self._drag_target_frame = QFrame(self.viewport())
        self._drag_target_frame.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._drag_target_frame.setStyleSheet(
            "background-color: rgba(37, 99, 235, 24); "
            "border: 2px solid #2563EB;"
        )
        self._drag_target_frame.hide()
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setWordWrap(True)
        self.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(CARD_MIME_TYPE):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(CARD_MIME_TYPE):
            point = event.position().toPoint()
            row = self.rowAt(point.y())
            column = self.columnAt(point.x())
            card_id = bytes(
                event.mimeData().data(CARD_MIME_TYPE)
            ).decode("utf-8")
            if row >= 0 and column >= 0:
                self.entryDragMoved.emit(row, column, card_id)
            else:
                self.hide_drag_preview()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.hide_drag_preview()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.hide_drag_preview()
        if not event.mimeData().hasFormat(CARD_MIME_TYPE):
            event.ignore()
            return
        point = event.position().toPoint()
        row = self.rowAt(point.y())
        column = self.columnAt(point.x())
        card_id = bytes(
            event.mimeData().data(CARD_MIME_TYPE)
        ).decode("utf-8")
        if row < 0 or column < 0:
            event.ignore()
            return
        self.entryDropped.emit(row, column, card_id)
        event.acceptProposedAction()

    def show_drag_preview(self, row, column, span):
        if (
            row < 0
            or row >= self.rowCount()
            or column < 0
            or column >= self.columnCount()
        ):
            self.hide_drag_preview()
            return
        row_y = self.rowViewportPosition(row)
        row_height = self.rowHeight(row)
        boundary_count = max(0, self.columnCount() - 1)
        while len(self._drag_boundary_lines) < boundary_count:
            line = QFrame(self.viewport())
            line.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )
            line.setStyleSheet(
                "background-color: #94A3B8; border: none;"
            )
            self._drag_boundary_lines.append(line)
        for boundary_index in range(boundary_count):
            first_x = self.columnViewportPosition(boundary_index)
            second_x = self.columnViewportPosition(boundary_index + 1)
            x = max(first_x, second_x)
            line = self._drag_boundary_lines[boundary_index]
            line.setGeometry(QRect(x, row_y, 1, row_height))
            line.show()
            line.raise_()

        last_column = min(
            column + max(1, span), self.columnCount()
        ) - 1
        covered_columns = range(column, last_column + 1)
        left = min(
            self.columnViewportPosition(index)
            for index in covered_columns
        )
        right = max(
            self.columnViewportPosition(index) + self.columnWidth(index)
            for index in range(column, last_column + 1)
        )
        start_x = (
            right - 1
            if self.layoutDirection() == Qt.RightToLeft
            else left
        )
        self._drag_target_frame.setGeometry(
            QRect(left, row_y, max(1, right - left), row_height)
        )
        self._drag_target_frame.show()
        self._drag_target_frame.raise_()
        self._drag_start_guide.setGeometry(
            QRect(start_x - 1, row_y, 3, row_height)
        )
        self._drag_start_guide.show()
        self._drag_start_guide.raise_()

    def hide_drag_preview(self):
        for line in self._drag_boundary_lines:
            line.hide()
        self._drag_target_frame.hide()
        self._drag_start_guide.hide()


class TeacherTimetablePage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.year_id = db.year_id
        self.read_only = False
        self.selected_card_id = None
        self.schedule = self._default_schedule()
        self.automatic_schedule = {}
        self.automatic_first_hour = 8
        self.automatic_last_hour = 17
        self.setLayoutDirection(Qt.RightToLeft)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)
        self.lesson_palette = FloatingLessonPalette()
        self.lesson_palette.cardSelected.connect(self.select_card)
        self.lesson_palette.durationChangeRequested.connect(
            self.change_card_duration
        )
        self.lesson_palette.hide()

        self.automatic_group = QGroupBox("الجدول الآلي")
        automatic_layout = QVBoxLayout(self.automatic_group)
        self.automatic_info = QLabel(
            "يُبنى الجدول من سجل الحصص وفق آلية التوليد الحالية."
        )
        self.automatic_info.setWordWrap(True)
        automatic_layout.addWidget(self.automatic_info)
        self.automatic_table = QTableWidget()
        self.automatic_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers
        )
        self.automatic_table.setSelectionMode(
            QAbstractItemView.NoSelection
        )
        self.automatic_table.setWordWrap(True)
        self.automatic_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.automatic_table.verticalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.automatic_table.verticalHeader().setFont(
            QApplication.font()
        )
        automatic_layout.addWidget(self.automatic_table, 1)
        root.addWidget(self.automatic_group, 1)

        self.manual_group = QGroupBox("توليد يدوي")
        manual_layout = QVBoxLayout(self.manual_group)

        self.manual_info = QLabel(
            "اسحب بطاقة الحصة إلى اليوم والساعة المناسبين، أو استخدم القلم للتعديل."
        )
        self.manual_info.setWordWrap(True)
        self.manual_info.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table_toolbar = QHBoxLayout()
        self.edit_timetable_button = QToolButton(self.manual_group)
        self.edit_timetable_button.setObjectName("editTeacherTimetableButton")
        self.edit_timetable_button.setText("✎")
        self.edit_timetable_button.setToolTip(
            "تحديد الأقسام والأنشطة المناسبة لها"
        )
        self.edit_timetable_button.setAccessibleName(
            "تعديل جدول أوقات المعلم"
        )
        self.edit_timetable_button.setCursor(Qt.PointingHandCursor)
        self.edit_timetable_button.clicked.connect(self.open_editor)
        table_toolbar.addWidget(self.edit_timetable_button)

        self.edit_timetable_hours_button = QToolButton(self.manual_group)
        self.edit_timetable_hours_button.setObjectName(
            "editTeacherTimetableHoursButton"
        )
        self.edit_timetable_hours_button.setText("◷")
        self.edit_timetable_hours_button.setToolTip(
            "تحديد ساعة الانطلاق وساعة الانتهاء"
        )
        self.edit_timetable_hours_button.setAccessibleName(
            "تحديد ساعة الانطلاق وساعة الانتهاء"
        )
        self.edit_timetable_hours_button.setCursor(Qt.PointingHandCursor)
        self.edit_timetable_hours_button.clicked.connect(
            self.edit_timetable_hours
        )
        table_toolbar.addWidget(self.edit_timetable_hours_button)

        self.show_palette_button = QToolButton(self.manual_group)
        self.show_palette_button.setObjectName("showTeacherTimetablePaletteButton")
        self.show_palette_button.setText("👁")
        self.show_palette_button.setToolTip(
            "عرض/إخفاء حصص الجدول اليدوي"
        )
        self.show_palette_button.setAccessibleName(
            "عرض حصص الجدول اليدوي"
        )
        self.show_palette_button.setCursor(Qt.PointingHandCursor)
        self.show_palette_button.clicked.connect(self.toggle_lesson_palette)
        table_toolbar.addWidget(self.show_palette_button)
        table_toolbar.addWidget(self.manual_info, 1)
        manual_layout.addLayout(table_toolbar)
        self.manual_table = TeacherTimetableGrid()
        self.manual_table.setHorizontalHeader(TimetableHourHeader())
        self.manual_table.entryDropped.connect(self.place_card)
        self.manual_table.entryDragMoved.connect(
            self._preview_card_placement
        )
        self.manual_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.manual_table.verticalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.manual_table.verticalHeader().setFont(QApplication.font())
        manual_layout.addWidget(self.manual_table, 1)
        root.addWidget(self.manual_group, 1)

        self.set_school_year(self.db.year_id)
        self._update_manual_controls()

    @staticmethod
    def _default_schedule():
        return {
            "version": 1,
            "days": [],
            "start_time": "08:00",
            "end_time": "16:00",
            "cards": [],
            "entries": [],
        }

    def set_school_year(self, year_id):
        previous_year_id = self.year_id
        self.year_id = year_id
        self.selected_card_id = None
        saved = self.db.teacher_timetable(year_id)
        if saved is None:
            self.schedule = self._default_schedule()
        else:
            self.schedule = self._normalize_schedule(saved)
        self._update_manual_controls()
        self._render_cards()
        self._render_manual_table()
        self.set_read_only(self.read_only)
        if previous_year_id != year_id:
            self.automatic_schedule = {}
            self.automatic_first_hour = 8
            self.automatic_last_hour = 17
            self.automatic_table.clear()
            self.automatic_table.setRowCount(0)
            self.automatic_table.setColumnCount(0)
            self.automatic_info.setText(
                "يُبنى الجدول الآلي من سجل السنة الدراسية المحددة."
            )

    @staticmethod
    def _normalize_schedule(schedule):
        if schedule.get("version") != 1:
            raise ValueError("إصدار بيانات جدول المعلم غير مدعوم.")
        normalized = TeacherTimetablePage._default_schedule()
        normalized.update(schedule)
        if not isinstance(normalized.get("days"), list):
            raise ValueError("أيام العمل المحفوظة في جدول المعلم غير صالحة.")
        if not isinstance(normalized.get("cards"), list):
            raise ValueError("بطاقات الحصص المحفوظة في جدول المعلم غير صالحة.")
        if not isinstance(normalized.get("entries"), list):
            raise ValueError("حصص الجدول المحفوظة غير صالحة.")
        return normalized

    def _update_manual_controls(self):
        has_schedule = bool(self.schedule.get("days"))
        self.edit_timetable_button.setEnabled(not self.read_only)
        self.edit_timetable_hours_button.setEnabled(not self.read_only)
        self.show_palette_button.setEnabled(not self.read_only)
        self.manual_table.setAcceptDrops(
            not self.read_only and has_schedule
        )

    def toggle_lesson_palette(self):
        if self.read_only:
            return
        self._render_cards()
        self._position_lesson_palette_over_automatic()
        self.lesson_palette.show()
        self.lesson_palette.raise_()
        self.lesson_palette.activateWindow()

    def set_read_only(self, read_only):
        self.read_only = bool(read_only)
        self._update_manual_controls()

    def set_automatic_schedule(
        self, schedule, first_hour=8, last_hour=17
    ):
        self.automatic_schedule = schedule or {}
        self.automatic_first_hour = max(0, min(23, int(first_hour)))
        self.automatic_last_hour = max(
            self.automatic_first_hour + 1,
            min(24, int(last_hour)),
        )
        days = ("الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت")
        self.automatic_table.clear()
        self.automatic_table.setRowCount(len(days))
        self.automatic_table.setColumnCount(
            self.automatic_last_hour - self.automatic_first_hour
        )
        self.automatic_table.setVerticalHeaderLabels(days)
        self.automatic_table.setHorizontalHeaderLabels([
            f"{hour:02d}:00 - {hour + 1:02d}:00"
            for hour in range(
                self.automatic_first_hour, self.automatic_last_hour
            )
        ])
        self.automatic_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        for day, cells in schedule.items():
            for column, span, text, count in cells:
                if not 0 <= day < len(days):
                    continue
                if (
                    column < 0
                    or column >= self.automatic_table.columnCount()
                ):
                    continue
                span = min(
                    max(1, span),
                    self.automatic_table.columnCount() - column,
                )
                item = QTableWidgetItem()
                item.setTextAlignment(Qt.AlignCenter)
                item.setToolTip(f"{text}\nتكرّرت {count} مرة")
                self.automatic_table.setItem(day, column, item)
                self.automatic_table.setCellWidget(
                    day,
                    column,
                    AutomaticTimetableCell(
                        text, count, self.automatic_table
                    ),
                )
                if span > 1:
                    self.automatic_table.setSpan(day, column, 1, span)
        self.automatic_info.setText(
            "تم توليد الجدول آليًا من سجل الحصص، باختيار الحصة الأكثر تكرارًا "
            "لكل يوم وساعة. تُدمج الحصص ذات الساعتين."
            if any(schedule.values())
            else "لا توجد حصص مسجلة بعد لتوليد الجدول الآلي."
        )

    def set_automatic_generation_failed(self, message):
        self.automatic_info.setText(f"تعذر توليد الجدول الآلي: {message}")

    def refresh_lesson_palette_from_database(self):
        saved = self.db.teacher_timetable(self.year_id)
        if saved is None:
            self.schedule = self._default_schedule()
        else:
            self.schedule = self._normalize_schedule(saved)
        self._render_cards()
        QApplication.processEvents()

    def _render_cards(self):
        self.lesson_palette.set_cards(self.schedule["cards"])

    def select_card(self, card_id):
        self.selected_card_id = card_id
        for index in range(self.lesson_palette.cards_layout.count()):
            widget = self.lesson_palette.cards_layout.itemAt(index).widget()
            if isinstance(widget, TimetableLessonCard):
                widget.setProperty(
                    "selected", widget.card["id"] == card_id
                )
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()

    def change_card_duration(self, card_id):
        if self.read_only:
            return
        card = next(
            (
                item for item in self.schedule.get("cards", [])
                if item.get("id") == card_id
            ),
            None,
        )
        if card is None:
            return
        dialog = TimetableDurationDialog(
            card.get("duration_hours", 1), self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        duration = dialog.selected_duration()
        if duration == float(card.get("duration_hours", 1)):
            return
        previous_schedule = json.loads(json.dumps(self.schedule))
        card["duration_hours"] = duration
        if not self._save_schedule():
            self.schedule = previous_schedule
        self._render_cards()
        if self.selected_card_id is not None:
            self.select_card(self.selected_card_id)
        self._update_manual_controls()

    def _position_lesson_palette_over_automatic(self):
        automatic_top_left = self.automatic_table.mapToGlobal(
            self.automatic_table.rect().topLeft()
        )
        screen = QApplication.screenAt(automatic_top_left)
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            self.lesson_palette.move(automatic_top_left)
            return
        available = screen.availableGeometry()
        palette_width = self.lesson_palette.width()
        palette_height = self.lesson_palette.height()
        x = min(
            max(available.left(), automatic_top_left.x()),
            available.right() - palette_width + 1,
        )
        y = min(
            max(available.top(), automatic_top_left.y()),
            available.bottom() - palette_height + 1,
        )
        self.lesson_palette.move(x, y)

    def open_editor(self):
        if self.read_only:
            return
        from dialogs import TeachingAssignmentsDialog

        dialog = TeachingAssignmentsDialog(self.db, self.year_id, self)
        dialog.exec()
        self.refresh_lesson_palette_from_database()

    def edit_timetable_hours(self):
        if self.read_only:
            return
        dialog = TimetableHoursDialog(
            self.schedule.get("start_time", "08:00"),
            self.schedule.get("end_time", "16:00"),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        start_time, end_time = dialog.values()
        start_label = start_time.toString("HH:mm")
        end_label = end_time.toString("HH:mm")
        start_minute = start_time.hour() * 60 + start_time.minute()
        end_minute = end_time.hour() * 60 + end_time.minute()
        retained_entries = []
        for entry in self.schedule.get("entries", []):
            entry_start = QTime.fromString(
                entry.get("start_time", ""), "HH:mm"
            )
            if not entry_start.isValid():
                continue
            entry_start_minute = (
                entry_start.hour() * 60 + entry_start.minute()
            )
            entry_end_minute = entry_start_minute + int(round(
                float(entry.get("duration_hours", 1)) * 60
            ))
            if (
                entry_start_minute >= start_minute
                and entry_end_minute <= end_minute
            ):
                retained_entries.append(entry)

        removed_count = (
            len(self.schedule.get("entries", [])) - len(retained_entries)
        )
        if removed_count and QMessageBox.question(
            self,
            "تغيير ساعات الجدول",
            f"سيؤدي تغيير ساعات العمل إلى حذف {removed_count} "
            "من الحصص الموضوعة خارج النطاق الجديد. هل تريد المتابعة؟",
        ) != QMessageBox.StandardButton.Yes:
            return

        previous_schedule = json.loads(json.dumps(self.schedule))
        self.schedule["start_time"] = start_label
        self.schedule["end_time"] = end_label
        self.schedule["entries"] = retained_entries
        if not self._save_schedule():
            self.schedule = previous_schedule
            return
        self._render_manual_table()
        self._update_manual_controls()

    def remove_table_entry(self, weekday, start_time):
        if self.read_only:
            return
        previous = json.loads(json.dumps(self.schedule))
        self.schedule["entries"] = [
            entry for entry in self.schedule["entries"]
            if not (
                entry["weekday"] == weekday
                and entry["start_time"] == start_time
            )
        ]
        if len(self.schedule["entries"]) == len(previous["entries"]):
            return
        self.manual_table.clearSpans()
        if not self._save_schedule():
            self.schedule = previous
            return
        self._render_manual_table()

    def _time_slots(self, start_time=None, end_time=None):
        start_time = start_time or QTime.fromString(
            self.schedule.get("start_time", "08:00"), "HH:mm"
        )
        end_time = end_time or QTime.fromString(
            self.schedule.get("end_time", "16:00"), "HH:mm"
        )
        if start_time.minute() or end_time.minute():
            raise ValueError("يرجى تحديد ساعات كاملة دون دقائق.")
        start_hour = start_time.hour()
        end_hour = end_time.hour()
        if end_hour <= start_hour:
            raise ValueError("يجب أن تكون ساعة الانتهاء بعد ساعة الانطلاق.")
        slot_count = (end_hour - start_hour) * 2
        return [
            start_time.addSecs(index * 1800).toString("HH:mm")
            for index in range(slot_count)
        ]

    def _render_manual_table(self):
        days = self.schedule.get("days", [])
        if not days:
            self.manual_table.clear()
            self.manual_table.setRowCount(0)
            self.manual_table.setColumnCount(0)
            return
        try:
            start_time = QTime.fromString(
                self.schedule.get("start_time", "08:00"), "HH:mm"
            )
            end_time = QTime.fromString(
                self.schedule.get("end_time", "16:00"), "HH:mm"
            )
            slots = self._time_slots(start_time, end_time)
        except ValueError:
            self.manual_table.clear()
            self.manual_table.setRowCount(0)
            self.manual_table.setColumnCount(0)
            return
        self.manual_table.clearSpans()
        self.manual_table.clearContents()
        self.manual_table.clear()
        self.manual_table.setRowCount(len(days))
        self.manual_table.setColumnCount(len(slots))
        self.manual_table.setVerticalHeaderLabels([
            WEEKDAYS[day] for day in days
        ])
        self.manual_table.horizontalHeader().set_hour_labels([
            f"{slot} - "
            f"{QTime.fromString(slot, 'HH:mm').addSecs(3600).toString('HH:mm')}"
            for slot in slots[::2]
        ])
        for row in range(len(days)):
            for column in range(len(slots)):
                item = QTableWidgetItem()
                item.setToolTip("اسقط بطاقة حصة هنا")
                self.manual_table.setItem(row, column, item)
        self.manual_table.setMinimumHeight(max(220, len(days) * 54))
        self._render_entries(slots, days)

    def _render_entries(self, slots, days):
        cards = {
            card["id"]: card for card in self.schedule.get("cards", [])
        }
        for entry in self.schedule.get("entries", []):
            card = cards.get(entry.get("card_id"))
            if card is None or entry.get("weekday") not in days:
                continue
            start_time = QTime.fromString(entry.get("start_time", ""), "HH:mm")
            if not start_time.isValid():
                continue
            start_label = start_time.toString("HH:mm")
            column = slots.index(start_label) \
                if start_label in slots else -1
            row = days.index(entry["weekday"])
            duration = float(entry.get("duration_hours", 1))
            slot_span = self._duration_column_span(duration)
            if column < 0 or column + slot_span > len(slots):
                continue
            item = self.manual_table.item(row, column)
            if item is not None and item.data(Qt.UserRole):
                continue
            background_item = QTableWidgetItem()
            background_item.setData(Qt.UserRole, card["id"])
            self.manual_table.setItem(row, column, background_item)
            cell = TimetableEntryCell(
                card, entry["weekday"], entry["start_time"],
                self.manual_table, duration_hours=duration,
            )
            cell.removeRequested.connect(self.remove_table_entry)
            self.manual_table.setCellWidget(row, column, cell)
            if slot_span > 1:
                self.manual_table.setSpan(row, column, 1, slot_span)

    @staticmethod
    def _duration_column_span(duration):
        return max(1, math.ceil(float(duration) * 2))

    def _preview_card_placement(self, row, column, card_id):
        card = next(
            (
                card for card in self.schedule.get("cards", [])
                if card.get("id") == card_id
            ),
            None,
        )
        if card is None:
            self.manual_table.hide_drag_preview()
            return
        duration = float(card.get("duration_hours", 1))
        self.manual_table.show_drag_preview(
            row, column, self._duration_column_span(duration)
        )

    def duration_half_slots(self, duration):
        duration = float(duration if duration is not None else 1)
        return max(1, int(round(duration * 2)))

    def place_card(self, row, column, card_id):
        if self.read_only:
            return
        cards = {card["id"]: card for card in self.schedule["cards"]}
        card = cards.get(card_id)
        days = self.schedule.get("days", [])
        if card is None or row < 0 or row >= len(days):
            return
        try:
            slots = self._time_slots(
                QTime.fromString(self.schedule["start_time"], "HH:mm"),
                QTime.fromString(self.schedule["end_time"], "HH:mm"),
            )
        except ValueError as error:
            QMessageBox.warning(self, "الجدول اليدوي", str(error))
            return
        if column < 0 or column >= len(slots):
            return
        weekday = days[row]
        duration = float(card.get("duration_hours", 1))
        duration_slots = self.duration_half_slots(duration)
        column_span = self._duration_column_span(duration)
        if column + column_span > len(slots):
            QMessageBox.information(
                self, "مدة الحصة",
                "لا توجد ساعات كافية قبل نهاية الدوام لوضع هذه الحصة.",
            )
            return
        start_time = QTime.fromString(slots[column], "HH:mm")
        start_minute = (
            start_time.hour() * 60 + start_time.minute()
        )
        occupied_end_minute = start_minute + duration_slots * 30
        for existing_entry in self.schedule.get("entries", []):
            if existing_entry.get("weekday") != weekday:
                continue
            existing_start = QTime.fromString(existing_entry.get("start_time", ""), "HH:mm")
            if not existing_start.isValid():
                continue
            existing_start_minute = existing_start.hour() * 60 + existing_start.minute()
            existing_duration = float(existing_entry.get("duration_hours", 1))
            existing_end_minute = existing_start_minute + int(round(existing_duration * 60))
            if start_minute < existing_end_minute and occupied_end_minute > existing_start_minute:
                QMessageBox.information(
                    self, "الخانة مشغولة",
                    "هذه الساعة أو إحدى الساعات متداخلة مع حصة أخرى.",
                )
                return

        merged_start_minute = start_minute
        merged_end_minute = occupied_end_minute
        merged_entries = set()
        entries = self.schedule.get("entries", [])
        while True:
            adjacent_range = None
            for index, entry in enumerate(entries):
                if (
                    index in merged_entries
                    or entry.get("weekday") != weekday
                    or entry.get("card_id") != card_id
                ):
                    continue
                entry_start = QTime.fromString(
                    entry.get("start_time", ""), "HH:mm"
                )
                if not entry_start.isValid():
                    continue
                entry_start_minute = (
                    entry_start.hour() * 60 + entry_start.minute()
                )
                entry_end_minute = entry_start_minute + int(round(
                    float(entry.get("duration_hours", 1)) * 60
                ))
                if (
                    entry_end_minute == merged_start_minute
                    or entry_start_minute == merged_end_minute
                ):
                    adjacent_range = (
                        index, entry_start_minute, entry_end_minute
                    )
                    break
            if adjacent_range is None:
                break
            (
                adjacent_index,
                adjacent_start_minute,
                adjacent_end_minute,
            ) = adjacent_range
            merged_entries.add(adjacent_index)
            merged_start_minute = min(
                merged_start_minute, adjacent_start_minute
            )
            merged_end_minute = max(
                merged_end_minute, adjacent_end_minute
            )

        previous = json.loads(json.dumps(self.schedule))
        self.schedule["entries"] = [
            entry for index, entry in enumerate(entries)
            if index not in merged_entries
        ]
        merged_start_time = QTime(
            merged_start_minute // 60, merged_start_minute % 60
        )
        self.schedule["entries"].append({
            "weekday": weekday,
            "start_time": merged_start_time.toString("HH:mm"),
            "duration_hours": (merged_end_minute - merged_start_minute) / 60,
            "card_id": card_id,
        })
        if not self._save_schedule():
            self.schedule = previous
            return
        self._render_manual_table()

    def _save_schedule(self):
        try:
            self.db.save_teacher_timetable(
                self.schedule, year_id=self.year_id
            )
        except (ValueError, TypeError, sqlite3.Error) as error:
            QMessageBox.warning(
                self, "تعذر حفظ جدول الأوقات", str(error)
            )
            return False
        return True
