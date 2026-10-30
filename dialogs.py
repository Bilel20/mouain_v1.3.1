# -*- coding: utf-8 -*-
"""Extracted module dialogs."""
import re
import sqlite3
import uuid
from datetime import datetime, time

import pandas as pd

from PySide6.QtCore import (
    Qt, QDate, QLocale, QObject, QRunnable, Signal, QThreadPool, QTimer,
)
from PySide6.QtGui import QColor, QIntValidator, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QAbstractItemView,
    QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHeaderView, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QTextEdit, QToolButton, QVBoxLayout, QWidget, QListWidget, QScrollArea,
    QSizePolicy,
    QListWidgetItem, QStyledItemDelegate,
    QTabWidget,
)

from app_config import (
    DETAIL_KEYS_BY_NAMAT,
    PLANS_NAMES,
    level_firak_label,
    level_firak_selection_label,
)
from class_assignment_ui import add_assignment_editor
from widgets import CustomCalendarWidget

from app_config import DB_PATH, set_dialog_button_action_types
from completion import AutoCompletionTextEdit
from database import Database, duration_minutes


def _domain_for_activity_name(activity_name):
    normalized_name = str(activity_name or "").strip()
    if not normalized_name:
        return ""
    for plan_name, plan in PLANS_NAMES.items():
        domain = str(plan.get("المجال") or "").strip()
        if normalized_name == plan_name or normalized_name in plan.get(
            "الأنشطة", []
        ):
            return domain or normalized_name
        if normalized_name == domain:
            return domain
    return normalized_name


class YearImportDialog(QDialog):
    def __init__(self, db, default_year_name="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("تحديد السنة الدراسية للاستيراد")
        self.setMinimumWidth(380)
        self.db = db
        
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.combo_years = QComboBox()
        self.combo_years.setEditable(True)
        self.load_existing_years()
        if default_year_name:
            self.combo_years.setCurrentText(default_year_name)
            
        form.addRow("السنة الدراسية المستهدفة:", self.combo_years)
        layout.addLayout(form)
        
        buttons = QDialogButtonBox()
        buttons.addButton("متابعة الاستيراد", QDialogButtonBox.AcceptRole)
        buttons.addButton("إلغاء", QDialogButtonBox.RejectRole)
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def load_existing_years(self):
        try:
            with self.db.connect() as con:
                cursor = con.cursor()
                cursor.execute("SELECT id, nom_annee FROM ann_scolaire")
                rows = cursor.fetchall()
                for row in rows:
                    self.combo_years.addItem(row[1], row[0])
        except Exception as e:
            print(f"Error loading years: {e}")

    def get_selected_year_name(self):
        return self.combo_years.currentText().strip()


class SchoolYearSelectionDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setObjectName("schoolYearExportDialog")
        self.setWindowTitle("اختيار السنة الدراسية للتصدير")
        self.setMinimumWidth(350)
        self.db = db
        
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.combo_years = QComboBox()
        self.load_years()
        
        form.addRow("السنة الدراسية:", self.combo_years)
        layout.addLayout(form)

        buttons = QDialogButtonBox()
        export_button = buttons.addButton("تصدير", QDialogButtonBox.AcceptRole)
        export_button.setProperty("actionType", "export")
        cancel_button = buttons.addButton("إلغاء", QDialogButtonBox.RejectRole)
        cancel_button.setProperty("actionType", "cancel")
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def load_years(self):
        try:
            with self.db.connect() as con:
                cursor = con.cursor()
                cursor.execute("SELECT id, nom_annee FROM ann_scolaire")
                rows = cursor.fetchall()
                for row in rows:
                    self.combo_years.addItem(row[1], row[0])
        except Exception as e:
            print(f"Error loading years: {e}")

    def get_selected_year_info(self):
        year_id = self.combo_years.currentData()
        year_name = self.combo_years.currentText()
        return year_id, year_name


class PreviousSchoolYearsDialog(QDialog):
    def __init__(self, db, selected_year_id=None, parent=None):
        super().__init__(parent)
        self.setObjectName("previousSchoolYearsDialog")
        self.setWindowTitle("عرض السنوات الدراسية السابقة")
        self.setMinimumWidth(340)
        self.db = db

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.combo_years = QComboBox()
        for year_id, year_name in self.db.school_years():
            self.combo_years.addItem(str(year_name or ""), year_id)
            if year_id == selected_year_id:
                self.combo_years.setCurrentIndex(self.combo_years.count() - 1)
        form.addRow("السنة الدراسية:", self.combo_years)
        layout.addLayout(form)

        buttons = QDialogButtonBox()
        buttons.addButton("عرض", QDialogButtonBox.AcceptRole)
        buttons.addButton("إلغاء", QDialogButtonBox.RejectRole)
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_year(self):
        return self.combo_years.currentData(), self.combo_years.currentText()


class TeachingAssignmentCard(QFrame):
    changed = Signal()
    removeRequested = Signal(object)

    def __init__(self, db, assignment=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.assignment = assignment or {}
        self._loading = True
        self._previous_subjects = {}
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("تعيين قسم ومواد تدريسه")
        title.setStyleSheet("font-weight: bold;")
        remove_button = QPushButton("حذف القسم")
        remove_button.clicked.connect(
            lambda: self.removeRequested.emit(self)
        )
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(remove_button)
        layout.addLayout(header)

        sections_panel = QWidget(self)
        form = QFormLayout()
        self.level_combo = QComboBox()
        self.level_combo.addItem("اختر المستوى", None)
        for level_id, level_name in self.db.levels():
            self.level_combo.addItem(level_name, level_id)
        for group_key, group in self.db.class_groups().items():
            self.level_combo.addItem(
                level_firak_selection_label(group_key),
                f"group:{group_key}",
            )

        self.class_combo = QComboBox()
        self.class_combo.setEditable(True)
        self.class_combo.addItem("اختر القسم أو اكتب اسمه", None)
        form.addRow("المستوى:", self.level_combo)
        form.addRow("اسم القسم:", self.class_combo)
        sections_panel.setLayout(form)

        activities_panel = QWidget(self)
        activities_layout = QVBoxLayout(activities_panel)
        activities_layout.setContentsMargins(0, 0, 0, 0)
        activities_layout.addWidget(QLabel("أنشطة التدريس:"))
        self.subjects_layout = QVBoxLayout()
        self.subjects_layout.setContentsMargins(0, 0, 0, 0)
        self.subjects_layout.setSpacing(6)
        activities_layout.addLayout(self.subjects_layout)
        activities_layout.addStretch(1)

        panels = QHBoxLayout()
        panels.addWidget(sections_panel, 1)
        panels.addWidget(activities_panel, 2)
        layout.addLayout(panels)

        self.level_combo.currentIndexChanged.connect(
            self._level_changed
        )
        self.class_combo.currentTextChanged.connect(
            self._emit_changed
        )
        self._load_assignment()
        self._loading = False

    def _load_assignment(self):
        level_id = self.assignment.get("level_id")
        group_key = str(
            self.assignment.get("class_group_key") or ""
        ).strip()
        if group_key:
            level_id = f"group:{group_key}"
        if level_id is not None:
            index = self.level_combo.findData(level_id)
            if index >= 0:
                self.level_combo.setCurrentIndex(index)
        self._reload_classes(self.assignment.get("class_name", ""))
        subjects = self.assignment.get("subjects", [])
        if not isinstance(subjects, list):
            subjects = []
        for subject in subjects:
            self._add_subject_row(subject)
        self._add_subject_row()

    def _reload_classes(self, selected_name=""):
        selected_name = str(selected_name or "").strip()
        if selected_name.startswith("اختر "):
            selected_name = ""
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        self.class_combo.addItem("اختر القسم أو اكتب اسمه", None)
        level_id = self.level_combo.currentData()
        if isinstance(level_id, str) and level_id.startswith("group:"):
            group_key = level_id[len("group:"):].strip()
            if group_key in self.db.class_groups():
                self.class_combo.addItem(
                    group_key, f"group:{group_key}"
                )
                if not selected_name:
                    selected_name = group_key
        elif level_id is not None:
            for class_id, class_name, class_level_id in self.db.classes():
                if class_level_id == level_id:
                    self.class_combo.addItem(class_name, class_id)
        index = self.class_combo.findText(
            selected_name, Qt.MatchFlag.MatchFixedString
        )
        if index >= 0:
            self.class_combo.setCurrentIndex(index)
        elif selected_name:
            self.class_combo.setEditText(selected_name)
        self.class_combo.blockSignals(False)

    def _level_changed(self, *_args):
        previous_name = self.class_combo.currentText()
        self._reload_classes(previous_name)
        self._emit_changed()

    def _emit_changed(self, *_args):
        if not self._loading:
            self.changed.emit()

    def _add_subject_row(self, selected_subject=""):
        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        combo = QComboBox(row)
        combo.addItem("اختر مادة التدريس", "")
        for subject_name in PLANS_NAMES:
            combo.addItem(subject_name, subject_name)
        if selected_subject:
            index = combo.findData(selected_subject)
            if index < 0:
                combo.addItem(selected_subject, selected_subject)
                index = combo.count() - 1
            combo.setCurrentIndex(index)
        remove_button = QPushButton("×", row)
        remove_button.setFixedWidth(38)
        combo.currentIndexChanged.connect(
            lambda *_args, current_combo=combo: self._subject_changed(
                current_combo
            )
        )
        remove_button.clicked.connect(
            lambda _checked=False, current_row=row, current_combo=combo:
            self._remove_subject_row(current_row, current_combo)
        )
        row_layout.addWidget(combo, 1)
        row_layout.addWidget(remove_button)
        self.subjects_layout.addWidget(row)
        self._previous_subjects[combo] = combo.currentData()
        return combo

    def _subject_changed(self, combo):
        if self._loading:
            return
        value = combo.currentData()
        selected = [
            item.currentData()
            for item in self._subject_combos()
            if item.currentData()
        ]
        previous_value = self._previous_subjects.get(combo)
        if not value and previous_value and not selected:
            combo.blockSignals(True)
            index = combo.findData(previous_value)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)
            QMessageBox.warning(
                self,
                "مادة التدريس مطلوبة",
                "يجب اختيار مادة تدريس واحدة على الأقل للقسم.",
            )
            return
        self._previous_subjects[combo] = value
        if value and not any(
            not item.currentData() for item in self._subject_combos()
        ):
            self._add_subject_row()
        self.changed.emit()

    def _subject_combos(self):
        combos = []
        for index in range(self.subjects_layout.count()):
            row = self.subjects_layout.itemAt(index).widget()
            if row is not None:
                combo = row.findChild(QComboBox)
                if combo is not None:
                    combos.append(combo)
        return combos

    def _remove_subject_row(self, row, combo):
        if combo.currentData() and sum(
            bool(item.currentData()) for item in self._subject_combos()
        ) <= 1:
            QMessageBox.warning(
                self,
                "مادة التدريس مطلوبة",
                "يجب اختيار مادة تدريس واحدة على الأقل للقسم.",
            )
            return
        self.subjects_layout.removeWidget(row)
        self._previous_subjects.pop(combo, None)
        row.deleteLater()
        if not any(
            not item.currentData() for item in self._subject_combos()
        ):
            self._add_subject_row()
        self.changed.emit()

    def assignment_values(self):
        selected_level = self.level_combo.currentData()
        class_name = self.class_combo.currentText().strip()
        class_group_key = None
        if (
            isinstance(selected_level, str)
            and selected_level.startswith("group:")
        ):
            class_group_key = selected_level[len("group:"):].strip()
            group = self.db.class_groups().get(class_group_key)
            if group is None or not group["classes"]:
                return None
            level_ids_by_name = {
                name: level_id for level_id, name in self.db.levels()
            }
            level_id = level_ids_by_name.get(
                group["classes"][0]["level_name"]
            )
            if level_id is None:
                return None
        else:
            level_id = selected_level
        subjects = list(dict.fromkeys(
            combo.currentData()
            for combo in self._subject_combos()
            if combo.currentData()
        ))
        if level_id is None or not class_name or not subjects:
            return None
        return {
            "id": str(self.assignment.setdefault("id", uuid.uuid4().hex)),
            "level_id": level_id,
            "class_name": class_name,
            "subjects": subjects,
            "class_group_key": class_group_key,
        }


class CulturalClubCountDelegate(QStyledItemDelegate):
    def createEditor(self, parent, _option, _index):
        editor = QSpinBox(parent)
        editor.setRange(0, 100000)
        return editor

    def setEditorData(self, editor, index):
        try:
            value = int(index.data(Qt.ItemDataRole.EditRole) or 0)
        except (TypeError, ValueError):
            value = 0
        editor.setValue(value)

    def setModelData(self, editor, model, index):
        model.setData(
            index,
            str(editor.value()),
            Qt.ItemDataRole.EditRole,
        )


class CulturalClubDialog(QDialog):
    CLUB_TYPES = ("ثقافي", "ثقافي - فني", "صحي", "علمي")

    def __init__(self, club=None, parent=None):
        super().__init__(parent)
        self.club = club or {}
        self.setWindowTitle(
            "تعديل النادي" if club else "إضافة نادٍ"
        )
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(str(self.club.get("name") or ""))
        self.type_combo = QComboBox()
        self.type_combo.addItems(self.CLUB_TYPES)
        club_type = self.club.get("club_type")
        if club_type:
            self.type_combo.setCurrentText(club_type)
        self.duration_combo = QComboBox()
        self.duration_combo.addItem("60 دقيقة", 60)
        self.duration_combo.addItem("120 دقيقة", 120)
        duration_index = self.duration_combo.findData(
            self.club.get("session_duration_minutes", 60)
        )
        if duration_index >= 0:
            self.duration_combo.setCurrentIndex(duration_index)
        form.addRow("اسم النادي:", self.name_edit)
        form.addRow("نوع النادي:", self.type_combo)
        form.addRow("مدة الحصة:", self.duration_combo)
        layout.addLayout(form)

        layout.addWidget(QLabel("توزيع المشاركين حسب الأقسام:"))
        self.sections_table = QTableWidget(0, 3, self)
        self.sections_table.setHorizontalHeaderLabels(
            ("القسم", "عدد الإناث", "عدد الذكور")
        )
        self.sections_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.sections_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.sections_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.sections_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.count_delegate = CulturalClubCountDelegate(
            self.sections_table
        )
        self.sections_table.setItemDelegateForColumn(
            1, self.count_delegate
        )
        self.sections_table.setItemDelegateForColumn(
            2, self.count_delegate
        )
        layout.addWidget(self.sections_table)

        section_actions = QHBoxLayout()
        add_section = QPushButton("إضافة قسم")
        add_section.setProperty("actionType", "add")
        add_section.clicked.connect(self.add_section_row)
        remove_section = QPushButton("حذف القسم المحدد")
        remove_section.setProperty("actionType", "delete")
        remove_section.clicked.connect(self.remove_selected_section)
        section_actions.addWidget(add_section)
        section_actions.addWidget(remove_section)
        layout.addLayout(section_actions)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for section in self.club.get("sections", []):
            self.add_section_row(section)
        if not self.sections_table.rowCount():
            self.add_section_row()

    def add_section_row(self, section=None):
        section = section or {}
        row = self.sections_table.rowCount()
        self.sections_table.insertRow(row)
        self.sections_table.setItem(
            row, 0,
            QTableWidgetItem(str(section.get("section_name") or "")),
        )
        for column, key in ((1, "female_count"), (2, "male_count")):
            count_item = QTableWidgetItem(str(int(section.get(key) or 0)))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.sections_table.setItem(row, column, count_item)
        self.sections_table.setCurrentCell(row, 0)
        self.sections_table.scrollToBottom()

    def remove_selected_section(self):
        row = self.sections_table.currentRow()
        if row >= 0:
            self.sections_table.removeRow(row)

    def values(self):
        sections = []
        for row in range(self.sections_table.rowCount()):
            section_name = self.sections_table.item(row, 0)
            female_count = self.sections_table.item(row, 1)
            male_count = self.sections_table.item(row, 2)
            try:
                female_value = int(female_count.text()) if female_count else 0
                male_value = int(male_count.text()) if male_count else 0
            except ValueError:
                female_value = male_value = 0
            sections.append({
                "section_name": section_name.text() if section_name else "",
                "female_count": female_value,
                "male_count": male_value,
            })
        return {
            "name": self.name_edit.text().strip(),
            "club_type": self.type_combo.currentText(),
            "session_duration_minutes": self.duration_combo.currentData(),
            "sections": sections,
        }

    def _accept_if_valid(self):
        data = self.values()
        if not data["name"]:
            QMessageBox.warning(self, "بيانات ناقصة", "أدخل اسم النادي.")
            return
        if not data["sections"] or any(
            not section["section_name"] for section in data["sections"]
        ):
            QMessageBox.warning(
                self, "بيانات ناقصة",
                "أدخل اسم قسم لكل صف أو احذف الصف الفارغ.",
            )
            return
        seen_sections = set()
        for section in data["sections"]:
            normalized = section["section_name"].casefold()
            if normalized in seen_sections:
                QMessageBox.warning(
                    self, "قسم مكرر",
                    f"القسم «{section['section_name']}» مكرر.",
                )
                return
            seen_sections.add(normalized)
        self.accept()


class TeachingAssignmentsDialog(QDialog):
    assignmentsChanged = Signal()

    def __init__(self, db, year_id=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.year_id = db.year_id if year_id is None else year_id
        self.setObjectName("teachingAssignmentsDialog")
        self.setWindowTitle("تحديد الأقسام والأنشطة")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1220, 900)
        self.setMinimumSize(1000, 720)
        self.setStyleSheet("""
            QDialog#teachingAssignmentsDialog {
                background: #F8FAFC;
            }
            QWidget#teachingAssignmentsSidePanel {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 12px;
            }
            QWidget#teachingAssignmentsActivityPanel {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 12px;
            }
        """)
        self.assignments = [
            {
                **assignment,
                "subjects": list(assignment.get("subjects", [])),
            }
            for assignment in self.db.teacher_teaching_assignments(
                self.year_id
            )
        ]
        self.selected_assignment_id = None

        layout = QVBoxLayout(self)
        self.portals_tabs = QTabWidget(self)
        self.portals_tabs.setObjectName("teachingAssignmentsPortals")
        official_distribution_portal = QWidget(self.portals_tabs)
        official_distribution_layout = QVBoxLayout(
            official_distribution_portal
        )
        official_distribution_layout.addWidget(QLabel(
            "اختر المستوى والقسم واضغط «إضافة قسم»، ثم اختر النشاط "
            "واضغط «إضافة» لإنشاء حصة."
        ))

        sections_panel = QWidget(self)
        sections_panel.setObjectName("teachingAssignmentsSidePanel")
        sections_layout = QVBoxLayout(sections_panel)
        sections_form = QFormLayout()
        self.level_combo = QComboBox()
        self.level_combo.addItem("اختر المستوى", None)
        for level_id, level_name in self.db.levels():
            self.level_combo.addItem(level_name, level_id)
        for group_key in self.db.class_groups():
            self.level_combo.addItem(
                level_firak_selection_label(group_key),
                f"group:{group_key}",
            )
        self.class_combo = QComboBox()
        self.class_combo.setEditable(True)
        self.class_combo.addItem("اختر القسم أو اكتب اسمه", None)
        self.level_combo.currentIndexChanged.connect(self._load_classes)
        sections_form.addRow("المستوى:", self.level_combo)
        sections_form.addRow("القسم:", self.class_combo)
        sections_layout.addLayout(sections_form)
        self.add_section_button = QPushButton("إضافة قسم")
        self.add_section_button.setProperty("actionType", "add")
        self.add_section_button.clicked.connect(self.add_section)
        sections_layout.addWidget(self.add_section_button)

        self.sections_group = QGroupBox("الأقسام", sections_panel)
        self.sections_group.setObjectName("teachingAssignmentSectionsGroup")
        sections_group_layout = QVBoxLayout(self.sections_group)
        sections_group_layout.setContentsMargins(8, 12, 8, 8)
        sections_group_layout.setSpacing(6)
        self.sections_scroll = QScrollArea(self.sections_group)
        self.sections_scroll.setObjectName("teachingAssignmentSectionsScroll")
        self.sections_scroll.setWidgetResizable(True)
        self.sections_container = QWidget()
        self.sections_layout = QGridLayout(self.sections_container)
        self.sections_layout.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self.sections_layout.setContentsMargins(10, 10, 10, 10)
        self.sections_layout.setSpacing(14)
        self.sections_scroll.setWidget(self.sections_container)
        sections_group_layout.addWidget(self.sections_scroll)
        sections_layout.addWidget(self.sections_group, 1)

        activities_panel = QWidget(self)
        activities_panel.setObjectName("teachingAssignmentsActivityPanel")
        activities_layout = QVBoxLayout(activities_panel)
        activities_layout.setContentsMargins(14, 14, 14, 14)
        activities_layout.setSpacing(10)
        activities_title = QLabel("أنشطة التدريس للقسم المحدد")
        activities_title.setObjectName("teachingAssignmentPanelTitle")
        activities_layout.addWidget(activities_title)
        self.activity_combo = QComboBox()
        self.activity_combo.addItem("اختر النشاط", "")
        for subject_name in PLANS_NAMES:
            self.activity_combo.addItem(subject_name, subject_name)
        activities_layout.addWidget(self.activity_combo)
        self.add_activity_button = QPushButton("إضافة")
        self.add_activity_button.setProperty("actionType", "add")
        self.add_activity_button.clicked.connect(self.add_activity)
        activities_layout.addWidget(self.add_activity_button)

        self.subjects_group = QGroupBox("الأنشطة المضافة", activities_panel)
        self.subjects_group.setObjectName("teachingAssignmentSubjectsGroup")
        subjects_group_layout = QVBoxLayout(self.subjects_group)
        subjects_group_layout.setContentsMargins(8, 10, 8, 8)
        subjects_group_layout.setSpacing(6)

        self.subjects_scroll = QScrollArea(self.subjects_group)
        self.subjects_scroll.setObjectName("teachingAssignmentSubjectsScroll")
        self.subjects_scroll.setWidgetResizable(True)
        self.subjects_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.subjects_container = QWidget()
        self.subjects_container.setObjectName("teachingAssignmentSubjectsContainer")
        self.subjects_layout = QVBoxLayout(self.subjects_container)
        self.subjects_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.subjects_layout.setContentsMargins(2, 2, 2, 2)
        self.subjects_layout.setSpacing(10)
        self.subjects_scroll.setWidget(self.subjects_container)
        subjects_group_layout.addWidget(self.subjects_scroll)
        activities_layout.addWidget(self.subjects_group, 1)
        self._set_activity_controls_enabled(False)

        section_and_activity_panels = QHBoxLayout()
        section_and_activity_panels.addWidget(sections_panel, 1)
        section_and_activity_panels.addWidget(activities_panel, 1)
        official_distribution_layout.addLayout(
            section_and_activity_panels, 1
        )
        self.portals_tabs.addTab(
            official_distribution_portal,
            "التوزيع البيداغوجي الرسمي",
        )
        cultural_portal = QWidget(self.portals_tabs)
        cultural_portal.setObjectName("culturalActivitiesPortal")
        cultural_layout = QVBoxLayout(cultural_portal)
        cultural_layout.addWidget(QLabel(
            "أضف النوادي وحدد نوعها ومدة حصصها وتوزيع التلاميذ حسب الأقسام."
        ))
        self.add_club_button = QPushButton("إضافة نادٍ")
        self.add_club_button.setProperty("actionType", "add")
        self.add_club_button.clicked.connect(self.add_cultural_club)
        cultural_layout.addWidget(self.add_club_button)
        self.clubs_scroll = QScrollArea(cultural_portal)
        self.clubs_scroll.setWidgetResizable(True)
        self.clubs_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.clubs_container = QWidget()
        self.clubs_layout = QVBoxLayout(self.clubs_container)
        self.clubs_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.clubs_scroll.setWidget(self.clubs_container)
        cultural_layout.addWidget(self.clubs_scroll, 1)
        self.portals_tabs.addTab(
            cultural_portal,
            "النوادي الثقافية والفنية والعلمية",
        )
        self.portals_tabs.currentChanged.connect(
            self._portal_changed
        )
        layout.addWidget(self.portals_tabs, 1)

        controls = QHBoxLayout()
        self.save_status = QLabel(
            "تُحفظ التعيينات والنوادي تلقائيًا."
        )
        controls.addWidget(self.save_status, 1)
        layout.addLayout(controls)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        set_dialog_button_action_types(buttons)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("إغلاق")
        buttons.button(QDialogButtonBox.StandardButton.Close).setProperty("actionType", "close")
        layout.addWidget(buttons)

        self._load_classes()
        self._render_sections()

    def _portal_changed(self, index):
        if self.portals_tabs.widget(index) is self.portals_tabs.widget(1):
            self._load_cultural_clubs()

    def _load_cultural_clubs(self):
        try:
            clubs = self.db.cultural_clubs(self.year_id)
        except sqlite3.Error as error:
            QMessageBox.warning(
                self, "تعذر تحميل النوادي", str(error)
            )
            return
        self._render_cultural_clubs(clubs)

    def _render_cultural_clubs(self, clubs):
        while self.clubs_layout.count():
            item = self.clubs_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not clubs:
            empty_label = QLabel("لم تتم إضافة نوادٍ لهذه السنة الدراسية.")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setStyleSheet("color: #64748B; padding: 20px;")
            self.clubs_layout.addWidget(empty_label)
            return
        for club in clubs:
            card = QFrame(self.clubs_container)
            card.setObjectName("culturalClubCard")
            card.setStyleSheet(
                "QFrame#culturalClubCard { background: #FFFFFF; "
                "border: 1px solid #CBD5E1; border-radius: 10px; }"
            )
            card_layout = QVBoxLayout(card)
            header = QHBoxLayout()
            title = QLabel(club["name"])
            title.setStyleSheet("font-weight: bold; font-size: 15px;")
            header.addWidget(title)
            header.addStretch(1)
            edit_button = QPushButton("تعديل")
            edit_button.setProperty("actionType", "edit")
            edit_button.clicked.connect(
                lambda _checked=False, record=club:
                self.edit_cultural_club(record)
            )
            delete_button = QPushButton("حذف")
            delete_button.setProperty("actionType", "delete")
            delete_button.clicked.connect(
                lambda _checked=False, record_id=club["id"]:
                self.delete_cultural_club(record_id)
            )
            header.addWidget(edit_button)
            header.addWidget(delete_button)
            card_layout.addLayout(header)
            card_layout.addWidget(QLabel(
                f"النوع: {club['club_type']}    |    "
                f"مدة الحصة: {club['session_duration_minutes']} دقيقة    |    "
                f"إجمالي المشاركين: {club['participant_count']}"
            ))
            for section in club["sections"]:
                card_layout.addWidget(QLabel(
                    f"{section['section_name']}: "
                    f"{section['female_count']} إناث، "
                    f"{section['male_count']} ذكور"
                ))
            self.clubs_layout.addWidget(card)

    def add_cultural_club(self):
        dialog = CulturalClubDialog(parent=self)
        if not dialog.exec():
            return
        self._save_cultural_club(dialog.values())

    def edit_cultural_club(self, club):
        dialog = CulturalClubDialog(club, self)
        if not dialog.exec():
            return
        self._save_cultural_club(dialog.values(), club["id"])

    def _save_cultural_club(self, club_data, club_id=None):
        try:
            self.db.save_cultural_club(
                self.year_id,
                club_data["name"],
                club_data["club_type"],
                club_data["session_duration_minutes"],
                club_data["sections"],
                club_id,
            )
        except (ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "تعذر حفظ النادي", str(error))
            return
        self._load_cultural_clubs()

    def delete_cultural_club(self, club_id):
        answer = QMessageBox.question(
            self, "تأكيد الحذف", "هل تريد حذف النادي المحدد؟"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.db.delete_cultural_club(self.year_id, club_id)
        except (ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "تعذر حذف النادي", str(error))
            return
        self._load_cultural_clubs()

    def _set_activity_controls_enabled(self, enabled):
        self.activity_combo.setEnabled(enabled)
        self.add_activity_button.setEnabled(enabled)

    def add_section(self):
        level_value = self.level_combo.currentData()
        class_name = self.class_combo.currentText().strip()
        if level_value is None or not class_name:
            QMessageBox.warning(
                self, "بيانات ناقصة", "يرجى اختيار المستوى واسم القسم."
            )
            return

        group_key = None
        if isinstance(level_value, str) and level_value.startswith("group:"):
            group_key = level_value[len("group:"):].strip()
            group = self.db.class_groups().get(group_key)
            if group is None or not group["classes"]:
                QMessageBox.warning(
                    self, "مستوى غير صالح", "فوج الأقسام المحدد غير مسجل."
                )
                return
            level_ids = {
                name: identifier for identifier, name in self.db.levels()
            }
            level_id = level_ids.get(group["classes"][0]["level_name"])
            if level_id is None:
                QMessageBox.warning(
                    self, "مستوى غير صالح", "تعذر تحديد مستوى فوج الأقسام."
                )
                return
            class_key = f"group:{group_key}"
        else:
            level_id = level_value
            class_key = f"{level_id}:{class_name.casefold()}"

        existing = next(
            (
                assignment for assignment in self.assignments
                if (
                    f"group:{assignment.get('class_group_key')}"
                    if assignment.get("class_group_key")
                    else (
                        f"{assignment.get('level_id')}:"
                        f"{str(assignment.get('class_name', '')).casefold()}"
                    )
                ) == class_key
            ),
            None,
        )
        if existing is not None:
            self._select_assignment(existing["id"])
            return

        assignment = {
            "id": uuid.uuid4().hex,
            "level_id": level_id,
            "class_name": class_name,
            "subjects": [],
            "class_group_key": group_key,
        }
        self.assignments.append(assignment)
        self._render_sections()
        self._select_assignment(assignment["id"])
        self.save_status.setText(
            "أُضيفت بطاقة القسم؛ اختر نشاطًا واضغط «إضافة» لحفظه."
        )

    def _load_classes(self, *_args):
        selected_value = self.level_combo.currentData()
        selected_name = self.class_combo.currentText()
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        if selected_value is None:
            self.class_combo.addItem("اختر المستوى أولاً", None)
        elif (
            isinstance(selected_value, str)
            and selected_value.startswith("group:")
        ):
            self.class_combo.addItem("اختر القسم أو اكتب اسمه", None)
            group_key = selected_value[len("group:"):].strip()
            if group_key in self.db.class_groups():
                self.class_combo.addItem(group_key, selected_value)
                if not selected_name or selected_name.startswith("اختر "):
                    selected_name = group_key
        else:
            self.class_combo.addItem("اختر القسم أو اكتب اسمه", None)
            for class_id, name, level_id in self.db.classes():
                if level_id == selected_value:
                    self.class_combo.addItem(name, class_id)
        index = self.class_combo.findText(
            selected_name, Qt.MatchFlag.MatchFixedString
        )
        if index >= 0:
            self.class_combo.setCurrentIndex(index)
        elif selected_name and not selected_name.startswith("اختر "):
            self.class_combo.setEditText(selected_name)
        self.class_combo.blockSignals(False)

    def _render_sections(self):
        while self.sections_layout.count():
            item = self.sections_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self.assignments:
            empty_label = QLabel("أضف قسمًا ليظهر هنا.")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.sections_layout.addWidget(empty_label, 0, 0, 1, 2)
            return
        level_names = {
            level_id: level_name for level_id, level_name in self.db.levels()
        }
        group_labels = {
            key: level_firak_selection_label(key)
            for key in self.db.class_groups()
        }
        section_palettes = (
            ("#EFF6FF", "#2563EB"),
            ("#ECFDF5", "#059669"),
            ("#FFF7ED", "#EA580C"),
            ("#FDF4FF", "#C026D3"),
            ("#FEF2F2", "#DC2626"),
        )
        for index, assignment in enumerate(self.assignments):
            card = QWidget(self.sections_container)
            card.setObjectName("teachingAssignmentSectionCardContainer")
            card.setFixedSize(154, 148)
            background, border = section_palettes[index % len(section_palettes)]
            card.setStyleSheet(
                "QWidget#teachingAssignmentSectionCardContainer {"
                f"background-color: {background}; border: 2px solid {border}; "
                "border-radius: 10px;"
                "}"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 8, 8, 8)
            card_layout.setSpacing(8)

            remove_button = QToolButton(card)
            remove_button.setObjectName("teachingAssignmentRemoveButton")
            remove_button.setText("×")
            remove_button.setToolTip("حذف القسم")
            remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
            remove_button.clicked.connect(
                lambda _checked=False, assignment_id=assignment["id"]:
                self.remove_section_by_id(assignment_id)
            )
            remove_button.setFixedSize(16, 16)
            remove_button.move(130, 5)
            remove_button.raise_()

            button = QPushButton(
                f"{assignment['class_name']}\n"
                f"{group_labels.get(assignment.get('class_group_key')) or level_names.get(assignment['level_id'], '')}"
            )
            button.setObjectName("teachingAssignmentSectionCard")
            button.setCheckable(True)
            button.setChecked(
                assignment["id"] == self.selected_assignment_id
            )
            button.setFixedSize(128, 94)
            button.clicked.connect(
                lambda _checked=False, assignment_id=assignment["id"]:
                self._select_assignment(assignment_id)
            )

            card_layout.addWidget(button)
            self.sections_layout.addWidget(card, index // 3, index % 3)

    def _select_assignment(self, assignment_id):
        assignment = next(
            (
                item for item in self.assignments
                if item["id"] == assignment_id
            ),
            None,
        )
        self.selected_assignment_id = (
            assignment["id"] if assignment is not None else None
        )
        self._render_sections()
        self._render_subjects(
            assignment.get("subjects", []) if assignment else []
        )
        self._set_activity_controls_enabled(assignment is not None)

    def _render_subjects(self, subjects):
        while self.subjects_layout.count():
            item = self.subjects_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not subjects:
            empty_label = QLabel("لم يُضف أي نشاط لهذا القسم بعد.")
            empty_label.setWordWrap(True)
            self.subjects_layout.addWidget(empty_label)
            return
        activity_palettes = (
            ("#EFF6FF", "#2563EB"),
            ("#ECFDF5", "#059669"),
            ("#FFF7ED", "#EA580C"),
            ("#FDF4FF", "#C026D3"),
            ("#FEF2F2", "#DC2626"),
        )
        for index, subject in enumerate(subjects):
            row = QWidget(self)
            row.setObjectName("teachingAssignmentSubjectCard")
            background, border = activity_palettes[index % len(activity_palettes)]
            row.setStyleSheet(
                "QWidget#teachingAssignmentSubjectCard {"
                f"background-color: {background}; border: 2px solid {border}; "
                "border-radius: 10px;"
                "}"
            )
            row_layout = QGridLayout(row)
            row_layout.setContentsMargins(12, 8, 12, 8)
            row_layout.setHorizontalSpacing(8)
            row_layout.setVerticalSpacing(0)
            row_layout.setColumnStretch(0, 1)
            label = QLabel(subject)
            label.setWordWrap(True)
            label.setStyleSheet("font-weight: 600;")
            remove_button = QToolButton(row)
            remove_button.setObjectName("teachingAssignmentSubjectRemoveButton")
            remove_button.setText("×")
            remove_button.setToolTip("حذف النشاط")
            remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
            remove_button.setFixedSize(16, 16)
            remove_button.clicked.connect(
                lambda _checked=False, name=subject:
                self.remove_activity(name)
            )
            row_layout.addWidget(label, 0, 0)
            row_layout.addWidget(
                remove_button, 0, 1,
                Qt.AlignmentFlag.AlignCenter
            )
            self.subjects_layout.addWidget(row)

    def add_activity(self):
        assignment = self._selected_assignment()
        subject = self.activity_combo.currentData()
        if assignment is None:
            QMessageBox.warning(
                self, "القسم مطلوب", "أضف قسمًا واختر بطاقته أولًا."
            )
            return
        if not subject:
            QMessageBox.warning(
                self, "النشاط مطلوب", "اختر نشاط التدريس أولًا."
            )
            return
        if subject in assignment["subjects"]:
            QMessageBox.information(
                self, "النشاط مضاف", "هذا النشاط مضاف لهذا القسم بالفعل."
            )
            return
        assignment["subjects"].append(subject)
        self._save_assignments()

    def remove_activity(self, subject):
        assignment = self._selected_assignment()
        if assignment is None or subject not in assignment["subjects"]:
            return
        assignment["subjects"].remove(subject)
        if not assignment["subjects"]:
            self.assignments.remove(assignment)
            self.selected_assignment_id = None
        self._save_assignments()

    def remove_section_by_id(self, assignment_id):
        assignment = next(
            (
                item for item in self.assignments
                if item["id"] == assignment_id
            ),
            None,
        )
        if assignment is None:
            return
        self.assignments.remove(assignment)
        if self.selected_assignment_id == assignment_id:
            self.selected_assignment_id = None
        self._save_assignments()

    def remove_section(self):
        assignment = self._selected_assignment()
        if assignment is None:
            return
        self.remove_section_by_id(assignment["id"])

    def _selected_assignment(self):
        return next(
            (
                assignment for assignment in self.assignments
                if assignment["id"] == self.selected_assignment_id
            ),
            None,
        )

    def _save_assignments(self):
        assignments_to_save = [
            assignment for assignment in self.assignments
            if assignment["subjects"]
        ]
        try:
            self.db.save_teacher_teaching_assignments(
                assignments_to_save, self.year_id
            )
        except (ValueError, TypeError, sqlite3.Error) as error:
            QMessageBox.warning(
                self, "تعذر حفظ تعيينات التدريس", str(error)
            )
            self.assignments = [
                {
                    **assignment,
                    "subjects": list(assignment.get("subjects", [])),
                }
                for assignment in self.db.teacher_teaching_assignments(
                    self.year_id
                )
            ]
            self.selected_assignment_id = None
            self._set_activity_controls_enabled(False)
            self._render_sections()
            self._render_subjects([])
            return
        self.save_status.setText("تم حفظ النشاط تلقائيًا.")
        self._render_sections()
        self._select_assignment(self.selected_assignment_id)
        self._notify_assignment_change()

    def _notify_assignment_change(self):
        self.assignmentsChanged.emit()

        current_parent = self.parent()
        visited = set()
        while current_parent is not None and id(current_parent) not in visited:
            visited.add(id(current_parent))
            refresh_page = getattr(
                current_parent, "refresh_lesson_palette_from_database", None
            )
            if callable(refresh_page):
                refresh_page()
            timetable_page = getattr(current_parent, "timetable_page", None)
            refresh_page = getattr(
                timetable_page,
                "refresh_lesson_palette_from_database",
                None,
            )
            if callable(refresh_page):
                refresh_page()
            refresh_editor = getattr(
                current_parent,
                "_refresh_lesson_palette_from_assignments",
                None,
            )
            if callable(refresh_editor):
                refresh_editor()
            current_parent = current_parent.parent()
        QApplication.processEvents()

class LessonEntryDialog(QDialog):
    def __init__(
        self, title, df_history, weekday_num=None, default_values=None,
        parent=None, db=None, available_hours=None, reserved_hours=None,
        work_bounds=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.db = db
        self.weekday_num = weekday_num
        self.is_updating = False
        self._syncing_schedule = False
        self.available_hours = list(available_hours or [])
        self.reserved_hours = list(reserved_hours or [])
        self.work_bounds = work_bounds
        self.available_start_minutes = set()
        for interval in self.available_hours:
            try:
                self.available_start_minutes.add(
                    self._interval_start(interval)
                )
            except ValueError:
                continue
        
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.combo_classe = QComboBox()
        self.combo_level = QComboBox()
        self.class_level_by_name = {}
        self.group_class_names = {}
        self._refresh_assigned_class_options()

        self.combo_majal = QComboBox()
        self.combo_majal.setEditable(False)
        
        self.combo_heure = QComboBox()
        self.combo_heure.setEditable(True)
        self.combo_heure.addItems(self.available_hours)

        self.combo_time = QComboBox()
        self.combo_time.addItems(
            ["30 دقيقة", "60 دقيقة", "90 دقيقة", "120 دقيقة"]
        )
        
        self.spin_wehda = QSpinBox()
        self.spin_wehda.setRange(1, 999)
        self.spin_wehda.setValue(1)
        
        self.spin_hessa = QSpinBox()
        self.spin_hessa.setRange(1, 999)
        self.spin_hessa.setValue(1)
        
        self.combo_namat = QComboBox()
        self.combo_namat.addItems(["منهجي", "إدماج", "تقييم", "دعم وعلاج"])
        self.combo_namat.setCurrentText("منهجي")
        
        form.addRow(
            "القسم",
            add_assignment_editor(
                self.combo_classe,
                self.db,
                self,
                self._refresh_assigned_class_options,
            ),
        )
        form.addRow("المستوى الدراسي للقسم", self.combo_level)
        form.addRow("المجال", self.combo_majal)
        form.addRow("التوقيت (مثال: 08:00-09:00)", self.combo_heure)
        form.addRow("مدة الحصة", self.combo_time)
        form.addRow("الوحدة", self.spin_wehda)
        form.addRow("الحصة", self.spin_hessa)
        form.addRow("نمط التعلم", self.combo_namat)
        
        layout.addLayout(form)
        
        buttons = QDialogButtonBox()
        buttons.addButton("حفظ", QDialogButtonBox.AcceptRole)
        buttons.addButton("إلغاء", QDialogButtonBox.RejectRole)
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.combo_classe.currentTextChanged.connect(self.on_classe_changed)
        self.combo_classe.currentTextChanged.connect(
            self.on_classe_level_changed
        )
        self.combo_heure.currentTextChanged.connect(self.on_heure_changed)
        self.combo_time.currentTextChanged.connect(self.on_duration_changed)
        
        default_subject = ""
        if default_values:
            if default_values[0]:
                self.combo_classe.setCurrentText(str(default_values[0]))
            if default_values[1]:
                default_subject = str(default_values[1]).strip()
            if default_values[2]:
                default_interval = str(default_values[2]).strip()
                if self.combo_heure.findText(default_interval) >= 0:
                    self.combo_heure.setCurrentText(default_interval)
            if default_values[3]:
                self.combo_time.setCurrentText(str(default_values[3]))
            if default_values[4]:
                self.spin_wehda.setValue(int(default_values[4] or 1))
            if default_values[5]:
                self.spin_hessa.setValue(int(default_values[5] or 1))
            if default_values[6]:
                self.combo_namat.setCurrentText(str(default_values[6]))
        self.on_classe_changed(self.combo_classe.currentText())
        default_domain = _domain_for_activity_name(default_subject)
        if self.combo_majal.findText(
            default_domain, Qt.MatchFlag.MatchFixedString
        ) >= 0:
            self.combo_majal.setCurrentText(default_domain)
        if self.combo_heure.currentText().strip():
            self.on_heure_changed(self.combo_heure.currentText())
        self.on_classe_level_changed(self.combo_classe.currentText())

    def _refresh_assigned_class_options(self):
        selected_name = self.combo_classe.currentText().strip()
        selected_level = self.combo_level.currentData()
        self.class_level_by_name = {}
        self.group_class_names = {}
        self.subjects_by_class_id = {}
        self.combo_classe.blockSignals(True)
        self.combo_classe.clear()
        self.combo_classe.addItem("اختر القسم", None)
        if self.db:
            for option in self.db.teaching_assignment_classes(
                include_groups=True
            ):
                class_name = str(option["name"])
                class_id = option["id"]
                self.combo_classe.addItem(class_name, class_id)
                self.class_level_by_name[class_name] = option["level_id"]
                self.subjects_by_class_id[class_id] = list(
                    option.get("subjects", [])
                )
                if option["group_key"]:
                    self.group_class_names[class_name] = option[
                        "group_key"
                    ]
        index = self.combo_classe.findText(
            selected_name, Qt.MatchFlag.MatchFixedString
        )
        self.combo_classe.setCurrentIndex(index if index >= 0 else 0)
        self.combo_classe.blockSignals(False)

        self.combo_level.blockSignals(True)
        self.combo_level.clear()
        self.combo_level.addItem("اختر المستوى", None)
        if self.db:
            for level_id, label in self.db.teaching_assignment_levels():
                self.combo_level.addItem(label, level_id)
        level_index = self.combo_level.findData(selected_level)
        self.combo_level.setCurrentIndex(
            level_index if level_index >= 0 else 0
        )
        self.combo_level.blockSignals(False)
        self.on_classe_level_changed(self.combo_classe.currentText())
        if hasattr(self, "combo_majal"):
            self.on_classe_changed(self.combo_classe.currentText())

    def on_classe_level_changed(self, class_name):
        normalized_name = class_name.strip()
        level_id = self.class_level_by_name.get(normalized_name)
        self.combo_level.setCurrentIndex(
            self.combo_level.findData(level_id)
            if level_id is not None else 0
        )

    def on_classe_changed(self, classe_text):
        if self.is_updating:
            return
        self.is_updating = True
        try:
            activities = self.subjects_by_class_id.get(
                self.combo_classe.currentData(), []
            )
            current_subject = self.combo_majal.currentText().strip()
            current_domain = _domain_for_activity_name(current_subject)
            domains = []
            for activity in activities:
                domain = _domain_for_activity_name(activity)
                if domain and domain not in domains:
                    domains.append(domain)
            self.combo_majal.blockSignals(True)
            self.combo_majal.clear()
            self.combo_majal.addItems(domains)
            if current_domain in domains:
                self.combo_majal.setCurrentText(current_domain)
            self.combo_majal.blockSignals(False)
        finally:
            self.is_updating = False

    def on_heure_changed(self, heure_text):
        if (
            self.is_updating or self._syncing_schedule
            or not heure_text.strip()
        ):
            return
        try:
            duration = self._interval_duration(heure_text)
        except ValueError:
            return
        duration_index = self.combo_time.findText(
            f"{duration} دقيقة", Qt.MatchFlag.MatchFixedString
        )
        if duration_index >= 0:
            self._syncing_schedule = True
            self.combo_time.setCurrentIndex(duration_index)
            self._syncing_schedule = False

    @staticmethod
    def _interval_start(interval):
        cleaned = (
            str(interval or "").replace("ـ", "-").replace("–", "-")
            .replace("—", "-").replace(" ", "")
        )
        parts = cleaned.split("-")
        if len(parts) != 2:
            raise ValueError("صيغة ساعة التدريس غير صالحة.")
        start = datetime.strptime(parts[0], "%H:%M")
        return start.hour * 60 + start.minute

    @staticmethod
    def _interval_duration(interval):
        cleaned = (
            str(interval or "").replace("ـ", "-").replace("–", "-")
            .replace("—", "-").replace(" ", "")
        )
        parts = cleaned.split("-")
        if len(parts) != 2:
            raise ValueError("صيغة ساعة التدريس غير صالحة.")
        start = datetime.strptime(parts[0], "%H:%M")
        end = datetime.strptime(parts[1], "%H:%M")
        duration = int((end - start).total_seconds() // 60)
        if duration <= 0:
            raise ValueError("وقت النهاية يجب أن يكون بعد البداية.")
        return duration

    def on_duration_changed(self, duration_text):
        if self._syncing_schedule or not duration_text.strip():
            return
        try:
            duration = int(duration_text.split()[0])
            current_text = self.combo_heure.currentText().strip()
            cleaned = (
                current_text.replace("ـ", "-").replace("–", "-")
                .replace("—", "-").replace(" ", "")
            )
            start_text = cleaned.split("-", 1)[0]
            start = datetime.strptime(start_text, "%H:%M")
        except (ValueError, IndexError):
            return
        end_minutes = start.hour * 60 + start.minute + duration
        if end_minutes >= 24 * 60:
            return
        end_text = f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"
        interval = f"{start_text}-{end_text}"
        self._syncing_schedule = True
        if self.combo_heure.findText(interval) < 0:
            self.combo_heure.addItem(interval)
        self.combo_heure.setCurrentText(interval)
        self._syncing_schedule = False

    def validate_and_accept(self):
        if self.combo_level.currentData() is None:
            QMessageBox.warning(
                self, "المستوى مطلوب",
                "يرجى اختيار المستوى الدراسي للقسم."
            )
            return
        if not self.combo_majal.currentText().strip():
            QMessageBox.warning(
                self, "المجال مطلوب",
                "يرجى اختيار مجال مرتبط بالقسم المحدد.",
            )
            return
        try:
            duration = self._interval_duration(
                self.combo_heure.currentText()
            )
            start_minute = self._interval_start(
                self.combo_heure.currentText()
            )
        except ValueError:
            QMessageBox.warning(
                self, "توقيت غير صالح",
                "أدخل فترة صحيحة بصيغة 08:00-09:00.",
            )
            return
        if duration not in (30, 60, 90, 120):
            QMessageBox.warning(
                self, "مدة غير مدعومة",
                "مدة الحصة يجب أن تكون 30 أو 60 أو 90 أو 120 دقيقة.",
            )
            return
        if self.combo_time.currentText().strip() != f"{duration} دقيقة":
            QMessageBox.warning(
                self, "المدة والتوقيت غير متطابقين",
                "تحقق من تطابق مدة الحصة مع فترة البداية والنهاية.",
            )
            return
        if (
            self.available_start_minutes
            and start_minute not in self.available_start_minutes
        ):
            QMessageBox.warning(
                self, "ساعة غير متاحة",
                "اختر بداية إحدى الفترات الشاغرة المعروضة.",
            )
            return
        try:
            _start, _end = self._interval_bounds(
                self.combo_heure.currentText()
            )
            if self.work_bounds is not None and (
                _start < self.work_bounds[0]
                or _end > self.work_bounds[1]
            ):
                QMessageBox.warning(
                    self, "خارج ساعات العمل",
                    "الفترة المحددة تقع خارج ساعات عمل المعلم.",
                )
                return
            for reserved_interval in self.reserved_hours:
                reserved_start, reserved_end = self._interval_bounds(
                    reserved_interval
                )
                if (
                    start_minute != reserved_start
                    and _start < reserved_end
                    and reserved_start < _end
                ):
                    QMessageBox.warning(
                        self, "الفترة تتعارض مع الجدول",
                        "الفترة المحددة تتداخل مع حصة أخرى في جدول الأوقات.",
                    )
                    return
        except ValueError:
            QMessageBox.warning(
                self, "توقيت غير صالح",
                "تعذر التحقق من تعارض التوقيت مع جدول الأوقات.",
            )
            return
        heure_text = self.combo_heure.currentText().strip().replace(" ", "").replace("ـ", "-")
        if "-" in heure_text:
            parts = heure_text.split("-")
            if len(parts) == 2:
                try:
                    t1 = datetime.strptime(parts[0].strip(), "%H:%M").time()
                    t2 = datetime.strptime(parts[1].strip(), "%H:%M").time()
                    work_start = time(7, 0)
                    work_end = time(17, 0)
                    if not (work_start <= t1 <= work_end and work_start <= t2 <= work_end):
                        QMessageBox.warning(self, "توقيت غير مقبول", "أوقات العمل الرسمية بين 07:00 و 17:00.")
                        return
                    if t2 <= t1:
                        QMessageBox.warning(self, "خطأ في التوقيت", "وقت النهاية يجب أن يكون بعد وقت البداية.")
                        return
                except ValueError:
                    QMessageBox.warning(self, "توقيت غير صالح", "صيغة التوقيت غير مقبولة (مثال: 08:00-09:30).")
                    return
        self.accept()

    def values(self):
        class_name = self.combo_classe.currentText().strip()
        class_name = self.group_class_names.get(class_name, class_name)
        return [
            class_name,
            self.combo_majal.currentText().strip(),
            self.combo_heure.currentText().strip(),
            self.combo_time.currentText().strip(),
            self.spin_wehda.value(),
            self.spin_hessa.value(),
            self.combo_namat.currentText().strip(),
            self.combo_level.currentData(),
        ]

    @staticmethod
    def _interval_bounds(interval):
        cleaned = (
            str(interval or "").replace("ـ", "-").replace("–", "-")
            .replace("—", "-").replace(" ", "")
        )
        parts = cleaned.split("-")
        if len(parts) != 2:
            raise ValueError("صيغة ساعة التدريس غير صالحة.")
        start = datetime.strptime(parts[0], "%H:%M")
        end = datetime.strptime(parts[1], "%H:%M")
        start_minute = start.hour * 60 + start.minute
        end_minute = end.hour * 60 + end.minute
        if end_minute <= start_minute:
            raise ValueError("وقت النهاية يجب أن يكون بعد البداية.")
        return start_minute, end_minute


class LessonScheduleEditDialog(QDialog):
    def __init__(
        self,
        hour_interval,
        unit_number,
        lesson_number,
        parent=None,
        allow_repeat=True,
        repeat_reason="",
        repeat_requested=False,
    ):
        super().__init__(parent)
        self.setWindowTitle("تعديل توقيت الحصة وأرقامها")
        self.setMinimumWidth(420)
        self.setLayoutDirection(Qt.RightToLeft)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.hour_interval_edit = QLineEdit(str(hour_interval or ""))
        self.hour_interval_edit.setPlaceholderText("08:00-09:00")
        form.addRow("ساعة التدريس", self.hour_interval_edit)

        self.unit_number_spin = QSpinBox()
        self.unit_number_spin.setRange(1, 999)
        self.unit_number_spin.setValue(int(unit_number or 1))
        form.addRow("عدد الوحدة", self.unit_number_spin)

        self.lesson_number_spin = QSpinBox()
        self.lesson_number_spin.setRange(1, 999)
        self.lesson_number_spin.setValue(int(lesson_number or 1))
        form.addRow("عدد الحصة", self.lesson_number_spin)
        layout.addLayout(form)

        self.repeat_checkbox = QCheckBox("إعادة الحصة في المرة القادمة")
        self.repeat_checkbox.setVisible(allow_repeat)
        self.repeat_checkbox.toggled.connect(self._toggle_repeat_fields)
        layout.addWidget(self.repeat_checkbox)

        self.repeat_reason_edit = QLineEdit(str(repeat_reason or ""))
        self.repeat_reason_edit.setPlaceholderText("غير مكتملة / طارئ / لم تنجح")
        self.repeat_reason_edit.setVisible(False)
        self.repeat_reason_label = QLabel("سبب إعادة الحصة:")
        self.repeat_reason_label.setVisible(False)
        self.repeat_reason_row = QWidget()
        repeat_row = QHBoxLayout(self.repeat_reason_row)
        repeat_row.setContentsMargins(0, 0, 0, 0)
        repeat_row.addWidget(self.repeat_reason_label)
        repeat_row.addWidget(self.repeat_reason_edit, 1)
        layout.addWidget(self.repeat_reason_row)

        self.confirm_checkbox = QCheckBox("أوافق على إعادة الحصة في المرة القادمة")
        self.confirm_checkbox.setChecked(repeat_requested)
        layout.addWidget(self.confirm_checkbox)
        self.repeat_checkbox.setChecked(repeat_requested)

        buttons = QDialogButtonBox()
        buttons.addButton("حفظ", QDialogButtonBox.AcceptRole)
        buttons.addButton("إلغاء", QDialogButtonBox.RejectRole)
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _toggle_repeat_fields(self, checked):
        self.repeat_reason_label.setVisible(checked)
        self.repeat_reason_edit.setVisible(checked)
        self.confirm_checkbox.setVisible(checked)
        self.confirm_checkbox.setChecked(checked)

    def validate_and_accept(self):
        interval = self.hour_interval_edit.text().strip()
        parts = (
            interval.replace("ـ", "-")
            .replace("–", "-")
            .replace("—", "-")
            .replace(" ", "")
            .split("-")
        )
        if len(parts) != 2:
            QMessageBox.warning(
                self, "توقيت غير صالح",
                "أدخل الفترة بصيغة 08:00-09:00.",
            )
            return
        try:
            start = datetime.strptime(parts[0], "%H:%M")
            end = datetime.strptime(parts[1], "%H:%M")
        except ValueError:
            QMessageBox.warning(
                self, "توقيت غير صالح",
                "أدخل توقيتًا صحيحًا بصيغة 08:00-09:00.",
            )
            return
        if end <= start:
            QMessageBox.warning(
                self, "توقيت غير صالح",
                "يجب أن يكون وقت النهاية بعد وقت البداية.",
            )
            return
        if self.repeat_checkbox.isChecked() and not self.repeat_reason_edit.text().strip():
            QMessageBox.warning(
                self, "سبب مطلوب",
                "يرجى تحديد سبب إعادة الحصة في المرة القادمة.",
            )
            return
        if self.repeat_checkbox.isChecked() and not self.confirm_checkbox.isChecked():
            QMessageBox.warning(
                self, "موافقة مطلوبة",
                "يجب تأكيد إعادة الحصة في المرة القادمة قبل الحفظ.",
            )
            return
        self.hour_interval_edit.setText(
            f"{parts[0]}-{parts[1]}"
        )
        self.accept()

    def values(self):
        return (
            self.hour_interval_edit.text().strip(),
            self.unit_number_spin.value(),
            self.lesson_number_spin.value(),
            self.repeat_checkbox.isChecked(),
            self.repeat_reason_edit.text().strip(),
        )


class RepeatActivitiesSelectionDialog(QDialog):
    def __init__(self, activities, available_minutes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("اختيار أنشطة الإعادة")
        self.setMinimumWidth(420)
        self.setLayoutDirection(Qt.RightToLeft)
        self.available_minutes = int(available_minutes)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "اختر الأنشطة التي ستعاد في هذه الحصة. "
            "بقية الأنشطة ستبقى مؤجلة للحصة التالية."
        ))
        self.activity_list = QListWidget(self)
        for activity_id, title, activity_time in activities:
            item = QListWidgetItem(
                f"{title} — {activity_time or 'مدة غير محددة'}"
            )
            item.setData(Qt.UserRole, int(activity_id))
            item.setData(Qt.UserRole + 1, duration_minutes(activity_time))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.activity_list.addItem(item)
        self.activity_list.itemChanged.connect(self._selection_changed)
        layout.addWidget(self.activity_list)

        self.duration_label = QLabel()
        layout.addWidget(self.duration_label)
        buttons = QDialogButtonBox()
        self.accept_button = buttons.addButton(
            "إعادة الأنشطة المحددة", QDialogButtonBox.AcceptRole
        )
        buttons.addButton("تأجيل", QDialogButtonBox.RejectRole)
        self.accept_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        set_dialog_button_action_types(buttons)
        layout.addWidget(buttons)
        self._selection_changed()

    def _selection_changed(self, *_args):
        selected_minutes = 0
        selected_count = 0
        valid = True
        for row in range(self.activity_list.count()):
            item = self.activity_list.item(row)
            if item.checkState() != Qt.Checked:
                continue
            selected_count += 1
            activity_minutes = item.data(Qt.UserRole + 1)
            if activity_minutes is None:
                valid = False
            else:
                selected_minutes += activity_minutes
        valid = (
            valid and selected_count > 0
            and selected_minutes <= self.available_minutes
        )
        self.duration_label.setText(
            f"المدة المختارة: {selected_minutes} من "
            f"{self.available_minutes} دقيقة"
        )
        self.accept_button.setEnabled(valid)

    def selected_activity_ids(self):
        return [
            int(self.activity_list.item(row).data(Qt.UserRole))
            for row in range(self.activity_list.count())
            if self.activity_list.item(row).checkState() == Qt.Checked
        ]


class LessonNumberDelegate(QStyledItemDelegate):
    NUMBER_ROLE = Qt.ItemDataRole.UserRole
    PLAN_COUNT_ROLE = Qt.ItemDataRole.UserRole + 1

    def createEditor(self, parent, _option, _index):
        editor = QLineEdit(parent)
        editor.setValidator(QIntValidator(1, 999, editor))
        return editor

    def setEditorData(self, editor, index):
        editor.setText(str(index.data(self.NUMBER_ROLE) or 1))
        editor.selectAll()

    def setModelData(self, editor, model, index):
        try:
            lesson_number = int(editor.text().strip())
        except ValueError:
            return
        if not 1 <= lesson_number <= 999:
            return
        plan_count = index.data(self.PLAN_COUNT_ROLE)
        display_text = (
            f"{lesson_number} / {plan_count}"
            if plan_count is not None else str(lesson_number)
        )
        model.setData(index, lesson_number, self.NUMBER_ROLE)
        model.setData(index, display_text, Qt.ItemDataRole.DisplayRole)


class LearningModeDelegate(QStyledItemDelegate):
    MODES = ("منهجي", "إدماج", "تقييم", "دعم وعلاج")

    def createEditor(self, parent, _option, _index):
        editor = QComboBox(parent)
        editor.addItems(self.MODES)
        return editor

    def setEditorData(self, editor, index):
        editor.setCurrentText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class AssignedActivityDelegate(QStyledItemDelegate):
    OPTIONS_ROLE = Qt.ItemDataRole.UserRole

    def createEditor(self, parent, _option, index):
        editor = QComboBox(parent)
        editor.addItems(index.data(self.OPTIONS_ROLE) or [])
        return editor

    def setEditorData(self, editor, index):
        editor.setCurrentText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class BulkLessonEntryDialog(QDialog):
    @staticmethod
    def _duration_from_interval(hour_text):
        cleaned_text = str(hour_text or "").replace(" ", "").replace("ـ", "-")
        parts = cleaned_text.split("-")
        if len(parts) != 2:
            return None
        try:
            start = datetime.strptime(parts[0], "%H:%M")
            end = datetime.strptime(parts[1], "%H:%M")
        except ValueError:
            return None
        duration = int((end - start).total_seconds() // 60)
        return duration if duration > 0 else None

    def __init__(
        self, lessons, number_defaults, parent=None,
        repeat_number_defaults=None,
        learning_mode_resolver=None,
        plan_lesson_count_resolver=None,
        assigned_activity_resolver=None,
        manual_number_keys=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("إضافة كل الحصص")
        self.setObjectName("bulkLessonEntryDialog")
        self.setMinimumSize(1280, 520)
        self.setLayoutDirection(Qt.RightToLeft)

        def lesson_sort_key(lesson):
            hour_text = str(lesson[2] or "").strip().replace("ـ", "-").replace(" ", "")
            start_text = hour_text.split("-", 1)[0]
            try:
                return 0, datetime.strptime(start_text, "%H:%M").time(), hour_text
            except ValueError:
                return 1, time.max, hour_text

        self.lessons = []
        for lesson in sorted(lessons, key=lesson_sort_key):
            duration = self._duration_from_interval(lesson[2])
            if duration is not None:
                lesson = (
                    *lesson[:3], f"{duration} دقيقة", *lesson[4:]
                )
            self.lessons.append(lesson)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        info_label = QLabel("أدخل رقم الوحدة والحصة واختر نمط التعلم لكل حصة:")
        info_label.setObjectName("bulkLessonInfo")
        layout.addWidget(info_label)

        self.table = QTableWidget(len(self.lessons), 7)
        self.table.setHorizontalHeaderLabels([
            "القسم", "النشاط", "ساعة التدريس", "التوقيت",
            "عدد الوحدة", "عدد الحصة", "نمط التعلم"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setMinimumWidth(1230)
        self.table.verticalHeader().setDefaultSectionSize(58)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.SelectedClicked
        )
        self.lesson_number_delegate = LessonNumberDelegate(self.table)
        self.table.setItemDelegateForColumn(
            5, self.lesson_number_delegate
        )
        self.learning_mode_delegate = LearningModeDelegate(self.table)
        self.table.setItemDelegateForColumn(
            6, self.learning_mode_delegate
        )
        self.assigned_activity_delegate = AssignedActivityDelegate(self.table)
        self.table.setItemDelegateForColumn(
            1, self.assigned_activity_delegate
        )

        self.learning_mode_resolver = learning_mode_resolver
        self.plan_lesson_count_resolver = plan_lesson_count_resolver
        self.assigned_activity_resolver = assigned_activity_resolver
        self.manual_number_keys = {
            (str(classe or "").strip(), str(majal or "").strip())
            for classe, majal in (manual_number_keys or ())
        }
        next_numbers = dict(number_defaults)
        repeat_number_defaults = repeat_number_defaults or {}
        for row, lesson in enumerate(self.lessons):
            assigned_activities = []
            if self.assigned_activity_resolver is not None:
                try:
                    assigned_activities = list(
                        self.assigned_activity_resolver(lesson) or []
                    )
                except (sqlite3.Error, ValueError) as error:
                    QMessageBox.warning(
                        self,
                        "تعذر تحميل الأنشطة المسندة",
                        f"تعذر تحميل أنشطة القسم:\n{error}",
                    )
            for column, value in enumerate(lesson[:4]):
                display_value = value
                if column == 1:
                    scheduled_activity = (
                        str(lesson[5] or "").strip()
                        if len(lesson) > 5 else ""
                    )
                    display_value = (
                        scheduled_activity
                        if scheduled_activity in assigned_activities
                        else assigned_activities[0]
                        if assigned_activities else value
                    )
                if (
                    column == 0 and len(lesson) > 4
                    and str(lesson[4]).startswith("group:")
                ):
                    group_key = str(lesson[4])[len("group:"):]
                    display_value = (
                        f"{value} — {level_firak_label(group_key)}"
                    )
                item = QTableWidgetItem(str(display_value or ""))
                item.setTextAlignment(Qt.AlignCenter)
                if column == 1 and assigned_activities:
                    item.setData(
                        AssignedActivityDelegate.OPTIONS_ROLE,
                        assigned_activities,
                    )
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                elif column >= 4:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, column, item)

            default_unit, default_lesson = number_defaults.get(
                (str(lesson[0] or "").strip(), str(lesson[1] or "").strip()),
                (1, row + 1)
            )
            key = (str(lesson[0] or "").strip(), str(lesson[1] or "").strip())
            default_unit, default_lesson = next_numbers.get(key, (default_unit, default_lesson))
            repeat_numbers = repeat_number_defaults.get(
                tuple(str(value or "").strip() for value in lesson[:3])
            )
            if key in self.manual_number_keys and repeat_numbers is None:
                default_unit, default_lesson = 1, 1
            elif repeat_numbers is not None:
                default_unit, default_lesson = repeat_numbers
            self.table.setItem(
                row, 4, QTableWidgetItem(str(default_unit))
            )
            lesson_number = int(default_lesson)
            lesson_number_item = QTableWidgetItem()
            lesson_number_item.setData(
                LessonNumberDelegate.NUMBER_ROLE, lesson_number
            )
            lesson_number_item.setData(
                LessonNumberDelegate.PLAN_COUNT_ROLE, None
            )
            lesson_number_item.setText(str(lesson_number))
            self.table.setItem(row, 5, lesson_number_item)
            mode_item = QTableWidgetItem("منهجي")
            mode_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 6, mode_item)
            if key not in self.manual_number_keys:
                increment = 2 if (
                    "اللغة العربية" in key[1]
                    and re.search(r"\b120\b", str(lesson[3] or ""))
                ) else 1
                next_numbers[key] = (
                    default_unit, default_lesson + increment
                )

        self.table.cellChanged.connect(self._lesson_number_changed)
        for row in range(self.table.rowCount()):
            self._update_lesson_number_suffix(row)
            self._auto_select_learning_mode(row)

        layout.addWidget(self.table)
        buttons = QDialogButtonBox()
        save_button = buttons.addButton("إضافة الحصص", QDialogButtonBox.AcceptRole)
        save_button.setObjectName("bulkSaveButton")
        cancel_button = buttons.addButton("إلغاء", QDialogButtonBox.RejectRole)
        cancel_button.setObjectName("bulkCancelButton")
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _lesson_number_changed(self, row, column):
        if column == 4:
            self._update_lesson_number_suffix(row)
            self._auto_select_learning_mode(row)
        elif column == 5:
            self._auto_select_learning_mode(row)

    def _update_lesson_number_suffix(self, row):
        if self.plan_lesson_count_resolver is None:
            return
        lesson = self.lessons[row]
        try:
            unit_number = int(self.table.item(row, 4).text().strip())
            plan_lesson_count = self.plan_lesson_count_resolver(
                lesson, unit_number
            )
        except (sqlite3.Error, ValueError) as error:
            QMessageBox.warning(
                self,
                "تعذر قراءة المخطط",
                f"تعذر تحديد عدد حصص المخطط:\n{error}",
            )
            plan_lesson_count = None
        item = self.table.item(row, 5)
        lesson_number = item.data(LessonNumberDelegate.NUMBER_ROLE)
        item.setData(
            LessonNumberDelegate.PLAN_COUNT_ROLE,
            int(plan_lesson_count) if plan_lesson_count is not None else None,
        )
        item.setText(
            f"{lesson_number} / {int(plan_lesson_count)}"
            if plan_lesson_count is not None else str(lesson_number)
        )

    def _auto_select_learning_mode(self, row):
        if self.learning_mode_resolver is None:
            return
        lesson = self.lessons[row]
        try:
            unit_number = int(self.table.item(row, 4).text().strip())
            lesson_number = int(
                self.table.item(row, 5).data(
                    LessonNumberDelegate.NUMBER_ROLE
                )
            )
        except (AttributeError, TypeError, ValueError):
            return
        try:
            mode = self.learning_mode_resolver(
                lesson, unit_number, lesson_number
            )
        except (sqlite3.Error, ValueError) as error:
            QMessageBox.warning(
                self,
                "تعذر تحديد نمط التعلم",
                f"تعذر البحث عن نمط التعلم في المخطط:\n{error}",
            )
            return
        if mode in ("منهجي", "إدماج", "تقييم", "دعم وعلاج"):
            self.table.item(row, 6).setText(mode)

    def validate_and_accept(self):
        for row in range(self.table.rowCount()):
            for column in (4, 5):
                item = self.table.item(row, column)
                try:
                    value = int(
                        item.text().strip() if column == 4
                        else item.data(LessonNumberDelegate.NUMBER_ROLE)
                    )
                except (AttributeError, TypeError, ValueError):
                    value = 0
                if not 1 <= value <= 999:
                    QMessageBox.warning(
                        self, "قيمة غير صالحة",
                        "يجب أن يكون عدد الوحدة ورقم الحصة بين 1 و999."
                    )
                    self.table.setCurrentCell(row, column)
                    return
        self.accept()

    def values(self):
        return [
            (
                str(lesson[0] or "").strip(),
                str(lesson[1] or "").strip(),
                str(lesson[2] or "").strip(),
                str(lesson[3] or "").strip(),
                int(self.table.item(row, 4).text().strip()),
                int(
                    self.table.item(row, 5).data(
                        LessonNumberDelegate.NUMBER_ROLE
                    )
                ),
                self.table.item(row, 6).text(),
                lesson[4] if len(lesson) > 4 else None,
                self.table.item(row, 1).text().strip(),
            )
            for row, lesson in enumerate(self.lessons)
        ]


class EntryDialog(QDialog):
    def __init__(self, title, fields, values=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        if len(fields) > 10:
            self.setMinimumSize(1100, 650)
            self.resize(1250, 850)
        else:
            self.setMinimumWidth(480)
        self.widgets = []
        layout = QVBoxLayout(self)
        use_three_columns = len(fields) > 10
        form = QGridLayout() if use_three_columns else QFormLayout()
        if use_three_columns:
            for grid_column in range(3):
                form.setColumnStretch(grid_column, 1)
        row = 0
        column = 0
        
        for label, kind, default in fields:
            if kind in ("int", "nonnegative_int"):
                w = QSpinBox()
                w.setRange(0 if kind == "nonnegative_int" else 1, 999)
                initial_value = default if default is not None else (
                    0 if kind == "nonnegative_int" else 1
                )
                w.setValue(int(initial_value))
            elif kind in ["multiline", "plaintext"]:
                w = QTextEdit()
                w.setAcceptRichText(False)
                w.setPlainText(str(default or ""))
                w.setMinimumHeight(100)
                w.setAlignment(Qt.AlignCenter)
            elif kind == "date":
                w = QLineEdit(str(default or ""))
                w.setPlaceholderText("YYYY-MM-DD")
            else:
                w = QLineEdit(str(default or ""))

            if isinstance(w, QLineEdit):
                w.setAlignment(Qt.AlignCenter)
            elif isinstance(w, QSpinBox):
                w.lineEdit().setAlignment(Qt.AlignCenter)

            self.widgets.append(w)
            if use_three_columns:
                if kind == "plaintext" and column:
                    row += 1
                    column = 0

                label_widget = QLabel(label)
                label_widget.setAlignment(Qt.AlignCenter)
                cell = QWidget()
                cell_layout = QVBoxLayout(cell)
                cell_layout.setContentsMargins(0, 0, 0, 0)
                cell_layout.addWidget(label_widget)
                cell_layout.addWidget(w)
                if kind == "plaintext":
                    form.addWidget(cell, row, 0, 1, 3)
                    row += 1
                    column = 0
                else:
                    form.addWidget(cell, row, column)
                    column += 1
                    if column == 3:
                        row += 1
                        column = 0
            else:
                form.addRow(label, w)
            
        if values:
            for w, value in zip(self.widgets, values):
                if isinstance(w, QSpinBox):
                    w.setValue(int(
                        value if value is not None else
                        (0 if w.minimum() == 0 else 1)
                    ))
                elif isinstance(w, QTextEdit):
                    w.setPlainText(str(value or ""))
                else:
                    w.setText(str(value or ""))
                    
        layout.addLayout(form)
        
        buttons = QDialogButtonBox()
        buttons.addButton("حفظ", QDialogButtonBox.AcceptRole)
        buttons.addButton("إلغاء", QDialogButtonBox.RejectRole)
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        result = []
        for w in self.widgets:
            if isinstance(w, QSpinBox):
                result.append(w.value())
            elif isinstance(w, QTextEdit):
                result.append(w.toPlainText().strip())
            else:
                result.append(w.text().strip())
        return result


class DaySelectionDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setWindowTitle("اختيار يوم للتصدير")
        self.setMinimumWidth(480)
        self.setMinimumHeight(520)
        self.db = db
        
        layout = QVBoxLayout(self)
        self.info_label = QLabel(
            "اختر اليوم المطلوب تصديره من التقويم أدناه:"
        )
        self.info_label.setStyleSheet(
            "font-weight: bold; color: #2c3e50; font-size: 11px;"
        )
        layout.addWidget(self.info_label)
        
        self.calendar = CustomCalendarWidget(self.db, self)
        layout.addWidget(self.calendar)
        self.calendar.setLocale(QLocale(QLocale.Arabic, QLocale.Tunisia))
        
        buttons = QDialogButtonBox()
        self.export_btn = buttons.addButton("تصدير الملف", QDialogButtonBox.AcceptRole)
        self.export_btn.setProperty("actionType", "export")
        buttons.addButton("إلغاء", QDialogButtonBox.RejectRole)
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._configure_calendar_range()

    def get_selected_date_str(self):
        return self.calendar.selectedDate().toString("yyyy-MM-dd")

    def _configure_calendar_range(self):
        try:
            year_name = next(
                name for year_id, name in self.db.school_years()
                if year_id == self.db.year_id
            )
            match = re.fullmatch(
                r"\s*(\d{4})\s*-\s*(\d{4})\s*", str(year_name)
            )
            if match is None:
                raise ValueError(
                    f"اسم السنة الدراسية غير صالح: {year_name}"
                )
            first_year, second_year = map(int, match.groups())
            if second_year != first_year + 1:
                raise ValueError(
                    f"اسم السنة الدراسية غير متتالٍ: {year_name}"
                )
            first_date = QDate(first_year, 9, 1)
            last_date = QDate(second_year, 6, 30)
            self.calendar.setDateRange(first_date, last_date)

            workdays = {
                QDate.fromString(day_text, "yyyy-MM-dd")
                for _day_id, day_text in self.db.recorded_workdays(
                    self.db.year_id
                )
            }
            workdays = {
                day for day in workdays
                if day.isValid() and first_date <= day <= last_date
            }
            disabled_dates = set()
            current_date = first_date
            while current_date <= last_date:
                if current_date not in workdays:
                    disabled_dates.add(current_date)
                current_date = current_date.addDays(1)
            self.calendar.set_disabled_dates(
                disabled_dates,
                "لا يوجد يوم عمل مسجل لهذا التاريخ",
                background_color="#FFFFFF",
            )
            self.calendar.setEnabled(bool(workdays))
            self.export_btn.setEnabled(bool(workdays))
            if workdays:
                selected = self.calendar.selectedDate()
                if selected not in workdays:
                    self.calendar.setSelectedDate(min(workdays))
            else:
                self.info_label.setText(
                    "لا توجد أيام عمل مسجلة في هذه السنة الدراسية."
                )
        except (StopIteration, sqlite3.Error, TypeError, ValueError) as exc:
            self.calendar.setEnabled(False)
            self.export_btn.setEnabled(False)
            QMessageBox.critical(
                self,
                "تعذر إعداد تقويم التصدير",
                f"تعذر تحديد نطاق السنة الدراسية وأيام العمل:\n{exc}",
            )


class ActivityEntryDialog(QDialog):
    def __init__(
        self, title, majal, db, weekday, values=None, parent=None,
        lesson_id=None,
    ):
        super().__init__(parent)
        self.db = db
        self.majal = str(majal or "").strip()
        self.weekday = weekday
        self._activity_options_initialized = False
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        form = QFormLayout()
        layout = QVBoxLayout(self)

        self.class_scope_combo = QComboBox()
        self.class_scope_combo.addItem("مشترك بين المستويات", None)
        self.lesson_classes = db.lesson_classes(lesson_id) if lesson_id else []
        for class_id, class_name, level_name in self.lesson_classes:
            self.class_scope_combo.addItem(
                f"{class_name} — {level_name}", class_id
            )
        if len(self.lesson_classes) > 1:
            form.addRow("مستوى النشاط:", self.class_scope_combo)

        self.subjects_by_class_id = {}
        if hasattr(db, "teaching_assignment_classes"):
            for assignment in db.teaching_assignment_classes(
                include_groups=True
            ):
                subjects = list(assignment.get("subjects", []))
                class_ids = assignment.get(
                    "class_ids", [assignment.get("id")]
                )
                for class_id in class_ids:
                    self.subjects_by_class_id.setdefault(
                        class_id, []
                    ).extend(subjects)

        self.title_combo = QComboBox()
        self.title_combo.setEditable(True)
        
        self.time_combo = QComboBox()
        self.time_combo.setEditable(True)
        self.time_combo.addItems(["5 دقائق", "10 دقائق", "15 دقيقة", "20 دقيقة",
                                  "30 دقيقة", "45 دقيقة", "60 دقيقة"])
        form.addRow("عنوان النشاط", self.title_combo)
        form.addRow("المدة", self.time_combo)
        layout.addLayout(form)
        
        buttons = QDialogButtonBox()
        buttons.addButton("حفظ", QDialogButtonBox.AcceptRole)
        buttons.addButton("إلغاء", QDialogButtonBox.RejectRole)
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.class_scope_combo.currentIndexChanged.connect(
            self._refresh_activity_options
        )
        self.title_combo.currentTextChanged.connect(self.update_duration_suggestion)
        if values:
            self.time_combo.setCurrentText(str(values[1] or ""))
            if len(values) > 2:
                self.class_scope_combo.setCurrentIndex(
                    self.class_scope_combo.findData(values[2])
                )
        self._refresh_activity_options(
            preferred_title=values[0] if values else ""
        )

    def _refresh_activity_options(self, *_args, preferred_title=None):
        if preferred_title is None:
            preferred_title = self.title_combo.currentText().strip()
        class_id = self.class_scope_combo.currentData()
        class_ids = (
            [class_id]
            if class_id is not None
            else [row[0] for row in self.lesson_classes]
        )
        subjects = []
        for lesson_class_id in class_ids:
            for subject in self.subjects_by_class_id.get(
                lesson_class_id, []
            ):
                if subject not in subjects:
                    subjects.append(subject)

        activity_options = []
        for subject in subjects:
            plan = PLANS_NAMES.get(subject)
            if not isinstance(plan, dict):
                continue
            if str(plan.get("المجال") or "").strip() != self.majal:
                continue
            activities = plan.get("الأنشطة") or [subject]
            for activity in activities:
                activity = str(activity or "").strip()
                if activity and activity not in activity_options:
                    activity_options.append(activity)

        self.title_combo.blockSignals(True)
        self.title_combo.clear()
        self.title_combo.addItems(activity_options)
        if preferred_title in activity_options:
            self.title_combo.setCurrentText(preferred_title)
        elif not self._activity_options_initialized and preferred_title:
            self.title_combo.setCurrentText(preferred_title)
        else:
            self.title_combo.setCurrentIndex(-1)
        self.title_combo.blockSignals(False)
        self._activity_options_initialized = True

    def update_duration_suggestion(self, activity_title):
        if not activity_title.strip():
            return
        try:
            with self.db.connect() as con:
                df = pd.read_sql_query("""
                    SELECT n.time, d.date
                    FROM nachat n
                    JOIN hessa h ON h.id = n.hessa_id
                    JOIN majal m ON m.id=h.majal_id
                    JOIN day_data d ON d.id = h.day_data_id
                    WHERE n.title = ? AND m.name = ?
                      AND COALESCE(n.time, '') <> ''
                """, con, params=(activity_title.strip(), self.majal))
            if df.empty:
                return
            dates = pd.to_datetime(df["date"], errors="coerce")
            df = df.loc[dates.dt.dayofweek == self.weekday].copy()
            if df.empty:
                return
            modes = df["time"].astype(str).mode()
            if not modes.empty:
                suggestion = modes.iloc[0]
                old = self.time_combo.currentText()
                if not old or old not in df["time"].astype(str).unique():
                    self.time_combo.setCurrentText(suggestion)
        except Exception as exc:
            print(f"Activity duration prediction error: {exc}")

    def values(self):
        return [
            self.title_combo.currentText().strip(),
            self.time_combo.currentText().strip(),
            self.class_scope_combo.currentData(),
        ]


class DetailEntryDialog(QDialog):
    def __init__(
        self, title, namat, activity_title, db, current_count=0, values=None,
        parent=None, contextual_content=None, default_key=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(540)
        self.db = db
        self.namat = namat or ""
        self.activity_title = activity_title or ""
        self.contextual_content = contextual_content
        self._prediction_worker = None
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.key_combo = QComboBox()
        self.key_combo.setEditable(True)
        self.key_combo.addItems(DETAIL_KEYS_BY_NAMAT.get(self.namat, []))
        if default_key and not values:
            self.key_combo.setCurrentText(default_key)
        elif not values:
            self.key_combo.setCurrentIndex(-1)
            self.key_combo.lineEdit().setPlaceholderText(
                "جارٍ توقع اسم الخاصية..."
            )

        self.value_edit = AutoCompletionTextEdit(self.db, self.key_combo, self.activity_title)
        self.value_edit.setPlaceholderText("اكتب قيمة الخاصية؛ ستظهر اقتراحات مشابهة من السجل السابق…")
        font_metrics = self.value_edit.fontMetrics()
        self.value_edit.setFixedHeight(font_metrics.lineSpacing() * 5 + 12)

        self.suggestions_panel = QFrame(
            self, Qt.Tool | Qt.FramelessWindowHint
        )
        self.suggestions_panel.setObjectName("smartSuggestionsPanel")
        self.suggestions_panel.setWindowTitle("اقتراحات ذكية")
        self.suggestions_panel.setFixedSize(400, 460)
        self.suggestions_panel.setStyleSheet(
            "#smartSuggestionsPanel {"
            "background: white; border: 1px solid #94A3B8;"
            "border-radius: 8px; }"
        )
        suggestions_layout = QVBoxLayout(self.suggestions_panel)
        suggestions_layout.setContentsMargins(8, 8, 8, 8)
        suggestions_layout.addWidget(QLabel("اقتراحات ذكية"))
        self.suggestions_hint = QLabel(
            "ستظهر الاقتراحات المناسبة هنا."
        )
        self.suggestions_hint.setWordWrap(True)
        self.suggestions_hint.setAlignment(Qt.AlignCenter)
        self.suggestions_list = QListWidget()
        self.suggestions_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.suggestions_list.setToolTip("اختر اقتراحاً لإدراجه في خانة القيمة")
        suggestions_layout.addWidget(self.suggestions_list)
        suggestions_layout.addWidget(self.suggestions_hint)
        self.suggestions_panel.hide()
        self.value_edit.textChanged.connect(self.refresh_smart_suggestions)
        self.value_edit.suggestions_ready.connect(self.show_smart_suggestions)
        self.suggestions_list.itemClicked.connect(self.apply_smart_suggestion)
        self.key_combo.currentTextChanged.connect(self.refresh_smart_suggestions)
        self.key_combo.currentTextChanged.connect(self.value_edit.refresh_for_key_change)
        self.key_combo.currentTextChanged.connect(
            self._update_contextual_completion
        )
        form.addRow("اسم الخاصية", self.key_combo)
        form.addRow("القيمة", self.value_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox()
        buttons.addButton("حفظ", QDialogButtonBox.AcceptRole)
        buttons.addButton("إلغاء", QDialogButtonBox.RejectRole)
        set_dialog_button_action_types(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if values:
            old_key, old_value = values
            self.key_combo.setCurrentText(str(old_key or ""))
            self.value_edit.setPlainText(str(old_value or ""))
        elif self.activity_title:
            self._prediction_worker = DetailKeyPredictionWorker(
                self.db, 0, self.activity_title, self.namat, current_count
            )
            self._prediction_worker.signals.predicted.connect(
                self._apply_predicted_key
            )
            self._prediction_worker.signals.failed.connect(
                self._prediction_failed
            )
            QThreadPool.globalInstance().start(self._prediction_worker)
        self._update_contextual_completion()

    def _apply_predicted_key(self, _request_id, key):
        self._prediction_worker = None
        if key and not self.key_combo.currentText().strip():
            self.key_combo.setCurrentText(key)
        if not self.key_combo.currentText().strip():
            self.key_combo.lineEdit().setPlaceholderText(
                "اختر أو اكتب اسم الخاصية"
            )

    def _prediction_failed(self, _request_id, message):
        self._prediction_worker = None
        self.key_combo.lineEdit().setPlaceholderText(
            "تعذر التوقع؛ اختر أو اكتب اسم الخاصية"
        )
        QMessageBox.warning(
            self, "تعذر توقع الخاصية",
            f"تعذر تحميل ترتيب الخصائص السابقة:\n{message}"
        )

    def _update_contextual_completion(self, *_args):
        if (
            self.key_combo.currentText().strip() != "هدف الحصة"
            or self.contextual_content is None
        ):
            self.value_edit.set_contextual_completion_words(None)
            return
        content_words = str(self.contextual_content).split()[1:]
        self.value_edit.set_contextual_completion_words(
            ("المتعلم", *content_words)
        )

    def refresh_smart_suggestions(self, *_args):
        self.suggestions_list.clear()
        self.suggestions_hint.setText(
            "اكتب في خانة القيمة لعرض الاقتراحات المناسبة."
        )
        self.suggestions_hint.show()
        if self.isVisible():
            self._show_suggestions_panel()

    def show_smart_suggestions(self, suggestions):
        self.suggestions_list.clear()
        for value, score in suggestions:
            item = QListWidgetItem(value)
            item.setToolTip(f"درجة التشابه النصي: {score:.0%}")
            self.suggestions_list.addItem(item)
        has_suggestions = self.suggestions_list.count() > 0
        self.suggestions_hint.setText(
            "اكتب في خانة القيمة لعرض الاقتراحات المناسبة."
            if not self.value_edit.toPlainText().strip()
            else "لا توجد اقتراحات مناسبة لهذه القيمة."
        )
        self.suggestions_hint.setVisible(not has_suggestions)
        if self.isVisible():
            self._show_suggestions_panel()

    def _show_suggestions_panel(self):
        panel_width = self.suggestions_panel.width()
        available = self.suggestions_panel.screen().availableGeometry()
        panel_height = self.suggestions_panel.height()
        dialog_geometry = self.frameGeometry()
        gap = 8
        x = dialog_geometry.right() + gap
        if x + panel_width - 1 > available.right():
            x = dialog_geometry.left() - panel_width - gap
        x = min(
            max(x, available.left()),
            available.right() - panel_width + 1,
        )
        y = min(
            max(dialog_geometry.top(), available.top()),
            available.bottom() - panel_height + 1,
        )
        self.suggestions_panel.move(x, y)
        self.suggestions_panel.show()
        self.suggestions_panel.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self._show_suggestions_panel()
        QTimer.singleShot(0, self.value_edit.setFocus)

    def apply_smart_suggestion(self, item):
        if item is None:
            return
        self.value_edit.setPlainText(item.text())
        self.value_edit.moveCursor(QTextCursor.End)
        self.value_edit.setFocus()
        self._show_suggestions_panel()

    def hideEvent(self, event):
        self.suggestions_panel.hide()
        super().hideEvent(event)

    def values(self):
        return [self.key_combo.currentText().strip(), self.value_edit.toPlainText().strip()]


class BulkDetailSaveSignals(QObject):
    saved = Signal(int)
    failed = Signal(str)


class BulkDetailSaveWorker(QRunnable):
    def __init__(self, db, updates, inserts, deleted_ids):
        super().__init__()
        self.db = db
        self.updates = updates
        self.inserts = inserts
        self.deleted_ids = deleted_ids
        self.signals = BulkDetailSaveSignals()

    def run(self):
        try:
            saved_count = self.db.save_activity_details_bulk(
                self.updates, self.inserts, self.deleted_ids
            )
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return
        self.signals.saved.emit(saved_count)


class DetailKeyPredictionSignals(QObject):
    predicted = Signal(int, str)
    failed = Signal(int, str)


class DetailKeyPredictionWorker(QRunnable):
    def __init__(self, db, request_id, activity_title, namat, current_count):
        super().__init__()
        self.db = db
        self.request_id = request_id
        self.activity_title = activity_title
        self.namat = namat
        self.current_count = current_count
        self.signals = DetailKeyPredictionSignals()

    def run(self):
        try:
            key = self.db.predict_next_detail_key(
                self.activity_title, self.namat, self.current_count
            )
        except Exception as exc:
            self.signals.failed.emit(self.request_id, str(exc))
            return
        self.signals.predicted.emit(self.request_id, key)


class BulkActivityDetailsDialog(QDialog):
    def __init__(self, db, lesson_label, activities, namat, parent=None):
        super().__init__(parent)
        self.db = db
        self.namat = namat or ""
        self.setWindowTitle("إضافة جميع الخصائص لجميع الأنشطة")
        self.setObjectName("bulkActivityDetailsDialog")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumSize(900, 650)
        self.resize(1100, 800)
        self._saving = False
        self._save_worker = None
        self._activity_rows = []
        self._prediction_workers = {}
        self._prediction_rows = {}
        self._next_prediction_request = 0
        self._expected_keys = DETAIL_KEYS_BY_NAMAT.get(self.namat, [])

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        lesson_label_widget = QLabel(lesson_label)
        lesson_label_widget.setWordWrap(True)
        lesson_label_widget.setStyleSheet("font-weight: bold;")
        self.back_button = QPushButton("رجوع للحصة")
        self.back_button.setProperty("actionType", "back")
        self.back_button.clicked.connect(self.reject)
        header.addWidget(lesson_label_widget, 1)
        header.addWidget(self.back_button)
        layout.addLayout(header)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.activities_container = QWidget()
        self.activities_layout = QVBoxLayout(self.activities_container)
        self.activities_layout.setAlignment(Qt.AlignTop)
        self.scroll_area.setWidget(self.activities_container)
        layout.addWidget(self.scroll_area, 1)

        for activity in activities:
            activity_details = self.db.details(activity[0])
            self._add_activity_section(
                activity[0], str(activity[1] or ""), activity_details
            )

        if not activities:
            layout.addWidget(QLabel("لا توجد أنشطة في هذه الحصة. أضف نشاطاً أولاً."))

        self.status_label = QLabel()
        self.status_label.hide()
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton(
            "حفظ جميع الخصائص", QDialogButtonBox.AcceptRole
        )
        self.save_button.setProperty("actionType", "save")
        cancel_button = buttons.addButton("إلغاء", QDialogButtonBox.RejectRole)
        cancel_button.setProperty("actionType", "cancel")
        set_dialog_button_action_types(buttons)
        self.save_button.clicked.connect(self.save_details)
        cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self.save_button.setEnabled(bool(activities))

    def _add_activity_section(self, activity_id, activity_title, details):
        group = QGroupBox(f"النشاط: {activity_title}")
        group_layout = QVBoxLayout(group)
        rows_layout = QVBoxLayout()
        group_layout.addLayout(rows_layout)
        record = {
            "activity_id": activity_id,
            "title": activity_title,
            "layout": rows_layout,
            "rows": [],
            "deleted_ids": [],
            "next_prediction_index": len(details),
        }
        self._activity_rows.append(record)
        self.activities_layout.addWidget(group)

        for detail in details:
            self._add_property_row(record, detail=detail)

        self._add_property_row(
            record, prediction_index=record["next_prediction_index"]
        )
        record["next_prediction_index"] += 1
        self._refresh_contextual_completions(record)

    def _append_property_row(self, activity):
        prediction_index = activity["next_prediction_index"]
        activity["next_prediction_index"] += 1
        return self._add_property_row(
            activity, prediction_index=prediction_index
        )

    def _add_property_row(
        self, activity, detail=None, prediction_index=None
    ):
        row_widget = QWidget()
        row_layout = QVBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 6, 0, 6)
        key_layout = QHBoxLayout()
        key_combo = QComboBox()
        key_combo.setEditable(True)
        key_combo.addItems(self._expected_keys)
        if detail:
            key_combo.setCurrentText(str(detail[1] or ""))
        else:
            key_combo.clearEditText()
            key_combo.lineEdit().setPlaceholderText("جارٍ توقع اسم الخاصية...")
        remove_button = QPushButton("حذف الخاصية")
        remove_button.setProperty("actionType", "delete")
        key_layout.addWidget(QLabel("اسم الخاصية"))
        key_layout.addWidget(key_combo, 1)
        key_layout.addWidget(remove_button)
        row_layout.addLayout(key_layout)

        value_edit = AutoCompletionTextEdit(
            self.db, key_combo, activity["title"], row_widget
        )
        value_edit.setPlaceholderText(
            "اكتب قيمة الخاصية؛ ستظهر اقتراحات الإكمال الذكي من السجل السابق."
        )
        value_edit.setFixedHeight(value_edit.fontMetrics().lineSpacing() * 4 + 12)
        if detail:
            value_edit.setPlainText(str(detail[2] or ""))
        row_layout.addWidget(value_edit)

        suggestions_list = QListWidget()
        suggestions_list.setMaximumHeight(90)
        suggestions_list.setToolTip("اختر اقتراحاً لإدراجه في خانة القيمة")
        suggestions_list.hide()
        row_layout.addWidget(suggestions_list)
        value_edit.suggestions_ready.connect(
            lambda suggestions, widget=suggestions_list:
            self._show_suggestions(widget, suggestions)
        )
        suggestions_list.itemClicked.connect(
            lambda item, editor=value_edit, widget=suggestions_list:
            self._apply_suggestion(editor, widget, item)
        )
        key_combo.currentTextChanged.connect(value_edit.refresh_for_key_change)

        activity["layout"].addWidget(row_widget)
        row = {
            "widget": row_widget,
            "key_combo": key_combo,
            "value_edit": value_edit,
            "detail_id": detail[0] if detail else None,
            "original_key": str(detail[1] or "") if detail else None,
            "prediction_index": prediction_index,
            "spawned_next": False,
        }
        activity["rows"].append(row)
        remove_button.clicked.connect(
            lambda *args, target=activity, current_row=row:
            self._remove_property_row(target, current_row)
        )
        if prediction_index is not None:
            value_edit.textChanged.connect(
                lambda target=activity, current=row:
                self._on_new_property_started(target, current)
            )
            self._request_predicted_key(activity, row)
        value_edit.textChanged.connect(
            lambda target=activity:
            self._refresh_contextual_completions(target)
        )
        key_combo.currentTextChanged.connect(
            lambda _key, target=activity:
            self._refresh_contextual_completions(target)
        )
        self._refresh_contextual_completions(activity)
        return row_widget

    @staticmethod
    def _content_words(text):
        words = str(text or "").split()
        return words[1:] if words else []

    def _refresh_contextual_completions(self, activity):
        content_row = next(
            (
                row for row in activity["rows"]
                if row["key_combo"].currentText().strip() == "المحتوى"
            ),
            None,
        )
        goal_rows = [
            row for row in activity["rows"]
            if row["key_combo"].currentText().strip() == "هدف الحصة"
        ]
        if content_row is None:
            words = None
        else:
            words = (
                "المتعلم",
                *self._content_words(
                    content_row["value_edit"].toPlainText()
                ),
            )
        for row in goal_rows:
            row["value_edit"].set_contextual_completion_words(words)

    def _request_predicted_key(self, activity, row):
        self._next_prediction_request += 1
        request_id = self._next_prediction_request
        self._prediction_rows[request_id] = (activity, row)
        worker = DetailKeyPredictionWorker(
            self.db, request_id, activity["title"], self.namat,
            row["prediction_index"],
        )
        self._prediction_workers[request_id] = worker
        worker.signals.predicted.connect(self._apply_predicted_key)
        worker.signals.failed.connect(self._prediction_failed)
        QThreadPool.globalInstance().start(worker)

    def _apply_predicted_key(self, request_id, key):
        self._prediction_workers.pop(request_id, None)
        target = self._prediction_rows.pop(request_id, None)
        if target is None:
            return
        activity, row = target
        if row not in activity["rows"]:
            return
        combo = row["key_combo"]
        if key and not combo.currentText().strip():
            combo.setCurrentText(key)
        if not combo.currentText().strip():
            combo.lineEdit().setPlaceholderText("اختر أو اكتب اسم الخاصية")

    def _prediction_failed(self, request_id, message):
        self._prediction_workers.pop(request_id, None)
        target = self._prediction_rows.pop(request_id, None)
        if target is None:
            return
        activity, row = target
        if row in activity["rows"]:
            row["key_combo"].lineEdit().setPlaceholderText(
                "تعذر التوقع؛ اختر أو اكتب اسم الخاصية"
            )
        QMessageBox.warning(
            self, "تعذر توقع الخاصية",
            f"تعذر تحميل ترتيب الخصائص السابقة:\n{message}"
        )

    def _on_new_property_started(self, activity, row):
        if row["spawned_next"] or not row["value_edit"].toPlainText().strip():
            return
        row["spawned_next"] = True
        self._append_property_row(activity)

    @staticmethod
    def _show_suggestions(widget, suggestions):
        widget.clear()
        for value, score in suggestions:
            item = QListWidgetItem(value)
            item.setToolTip(f"درجة التشابه النصي: {score:.0%}")
            widget.addItem(item)
        widget.setVisible(widget.count() > 0)

    @staticmethod
    def _apply_suggestion(editor, suggestions_list, item):
        if item is None:
            return
        editor.setPlainText(item.text())
        editor.moveCursor(QTextCursor.End)
        editor.setFocus()
        suggestions_list.hide()

    @staticmethod
    def _remove_property_row(activity, row):
        if row["detail_id"] is not None:
            activity["deleted_ids"].append(row["detail_id"])
        activity["rows"].remove(row)
        row["widget"].deleteLater()

    def save_details(self):
        if self._saving:
            return
        updates = []
        inserts = []
        deleted_ids = []
        duplicate_keys = {}
        for activity in self._activity_rows:
            deleted_ids.extend(activity["deleted_ids"])
            for row in activity["rows"]:
                key_combo = row["key_combo"]
                value_edit = row["value_edit"]
                key = key_combo.currentText().strip()
                value = value_edit.toPlainText().strip()
                detail_id = row["detail_id"]
                if detail_id is None and not value:
                    continue
                if not key:
                    QMessageBox.warning(
                        self, "اسم الخاصية مطلوب",
                        "اختر أو أدخل اسم الخاصية لكل خاصية محفوظة أو قيمة مدخلة."
                    )
                    key_combo.setFocus()
                    return
                pair = (activity["activity_id"], key)
                previous_row = duplicate_keys.get(pair)
                if previous_row is not None:
                    unchanged_existing_duplicate = (
                        row["detail_id"] is not None
                        and previous_row["detail_id"] is not None
                        and row["original_key"] == key
                        and previous_row["original_key"] == key
                    )
                    if not unchanged_existing_duplicate:
                        QMessageBox.warning(
                            self, "خاصية مكررة",
                            f"أُدخلت الخاصية «{key}» أكثر من مرة للنشاط "
                            f"«{activity['title']}». احذف أو غيّر إحدى النسخ."
                        )
                        return
                else:
                    duplicate_keys[pair] = row
                if detail_id is not None:
                    updates.append((detail_id, key, value))
                else:
                    inserts.append((activity["activity_id"], key, value))

        if not updates and not inserts and not deleted_ids:
            QMessageBox.information(
                self, "لا توجد قيم للحفظ",
                "لم يتم إدخال أي قيمة أو تعديل أو حذف خاصية."
            )
            return

        self._saving = True
        self.save_button.setEnabled(False)
        self.back_button.setEnabled(False)
        self.status_label.setText("جارٍ حفظ الخصائص في الخلفية...")
        self.status_label.show()
        self._save_worker = BulkDetailSaveWorker(
            self.db, updates, inserts, deleted_ids
        )
        self._save_worker.signals.saved.connect(self._save_succeeded)
        self._save_worker.signals.failed.connect(self._save_failed)
        QThreadPool.globalInstance().start(self._save_worker)

    def _save_succeeded(self, count):
        self.status_label.setText(f"تم حفظ {count} خاصية بنجاح.")
        self.accept()

    def _save_failed(self, message):
        self._saving = False
        self.save_button.setEnabled(True)
        self.back_button.setEnabled(True)
        self.status_label.hide()
        QMessageBox.critical(
            self, "تعذر حفظ الخصائص",
            f"حدث خطأ أثناء حفظ الخصائص:\n{message}"
        )

    def reject(self):
        if self._saving:
            return
        super().reject()


class CompetenceManagerDialog(QDialog):
    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("إدارة كفايات المجالات والمواد الدراسية")
        # ⬅️ زيادة عرض وارتفاع النافذة لتكون أوسع وأكثر راحة
        self.resize(1200, 750)
        self.setLayoutDirection(Qt.RightToLeft)
        
        self.db = db if db else Database(DB_PATH)
        
        self.selected_level_id = None
        self.selected_majal_id = None
        self.selected_competence_majal_id = None
        self.copied_majal_data = None

        self.init_ui()
        self.load_levels_and_majals()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        title_label = QLabel("🎯 لوحة إدارة كفايات المجالات والمواد الدراسية")
        title_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #1E40AF; padding: 5px;")
        main_layout.addWidget(title_label)

        filters_box = QGroupBox("فلترة واختيار المستوى والمجال")
        filters_layout = QHBoxLayout(filters_box)

        self.combo_level = QComboBox()
        self.combo_level.currentIndexChanged.connect(self.on_filter_changed)

        self.combo_majal = QComboBox()
        self.combo_majal.currentIndexChanged.connect(self.on_filter_changed)

        filters_layout.addWidget(QLabel("المستوى الدراسي:"))
        filters_layout.addWidget(self.combo_level, 1)
        filters_layout.addWidget(QLabel("المجال:"))
        filters_layout.addWidget(self.combo_majal, 1)

        main_layout.addWidget(filters_box)

        splitter = QSplitter(Qt.Horizontal)

        # جدول كفاية المجال
        majal_widget = QWidget()
        majal_layout = QVBoxLayout(majal_widget)
        majal_layout.addWidget(QLabel("<b>1. كفاية المجال </b>"))
        
        majal_btns_layout = QHBoxLayout()
        for text, slot, act_type in [
            ("＋ إضافة", self.add_competence_majal, "add"), 
            ("✎ تعديل", self.edit_competence_majal, "edit"), 
            ("－ حذف", self.delete_competence_majal, "delete"),
            ("📋 نسخ الكل", self.copy_majal_with_matieres, "copy_paste"),
            ("📌 لصق وتحديث", self.paste_majal_with_matieres, "copy_paste")
        ]:
            b = QPushButton(text)
            b.setMinimumWidth(110)  # ⬅️ زيادة عرض الأزرار
            b.setProperty("actionType", act_type)
            b.clicked.connect(slot)
            majal_btns_layout.addWidget(b)
        majal_layout.addLayout(majal_btns_layout)

        self.tbl_competence_majal = self.create_clean_table(["كفاية المجال"])
        self.tbl_competence_majal.itemSelectionChanged.connect(self.on_competence_majal_selected)
        majal_layout.addWidget(self.tbl_competence_majal)
        splitter.addWidget(majal_widget)

        # جدول كفايات المواد
        matiere_widget = QWidget()
        matiere_layout = QVBoxLayout(matiere_widget)
        matiere_layout.addWidget(QLabel("<b>2. كفايات المواد المرتبطة بكفاية المجال المختارة</b>"))
        
        matiere_btns_layout = QHBoxLayout()
        for text, slot, act_type in [
            ("＋ إضافة مادة/كفاية", self.add_competence_matiere, "add"), 
            ("✎ تعديل", self.edit_competence_matiere, "edit"), 
            ("－ حذف", self.delete_competence_matiere, "delete")
        ]:
            b = QPushButton(text)
            b.setMinimumWidth(130)  # ⬅️ زيادة عرض الأزرار
            b.setProperty("actionType", act_type)
            b.clicked.connect(slot)
            matiere_btns_layout.addWidget(b)
        matiere_layout.addLayout(matiere_btns_layout)

        self.tbl_competence_matiere = self.create_clean_table(["المادة", "كفاية المادة"])
        matiere_layout.addWidget(self.tbl_competence_matiere)
        splitter.addWidget(matiere_widget)

        splitter.setSizes([520, 580])
        main_layout.addWidget(splitter, 1)

        close_btn = QPushButton("إغلاق")
        close_btn.setProperty("actionType", "close")
        close_btn.clicked.connect(self.accept)
        
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        bottom_layout.addWidget(close_btn)
        main_layout.addLayout(bottom_layout)

    def create_clean_table(self, headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        return table

    def load_levels_and_majals(self):
        try:
            with self.db.connect() as con:
                cursor = con.cursor()
                self.combo_level.blockSignals(True)
                self.combo_level.clear()
                for lid, lname in cursor.execute("SELECT id, name FROM level").fetchall():
                    self.combo_level.addItem(lname, lid)
                self.combo_level.blockSignals(False)

                self.combo_majal.blockSignals(True)
                self.combo_majal.clear()
                for mid, mname in cursor.execute("SELECT id, name FROM majal").fetchall():
                    self.combo_majal.addItem(mname, mid)
                self.combo_majal.blockSignals(False)

            self.on_filter_changed()
        except Exception as e:
            print(f"Error loading filters in manager: {e}")

    def on_filter_changed(self):
        self.selected_level_id = self.combo_level.currentData()
        self.selected_majal_id = self.combo_majal.currentData()
        self.load_competence_majal_data()

    def load_competence_majal_data(self):
        self.tbl_competence_majal.setUpdatesEnabled(False)
        self.tbl_competence_matiere.setUpdatesEnabled(False)
        try:
            self.tbl_competence_majal.setRowCount(0)
            self.tbl_competence_matiere.setRowCount(0)
            if not self.selected_level_id or not self.selected_majal_id:
                return

            with self.db.connect() as con:
                rows = con.execute(
                    """SELECT id, competence FROM competence_majal
                       WHERE id_majal = ? AND id_level = ? ORDER BY id""",
                    (self.selected_majal_id, self.selected_level_id),
                ).fetchall()

            for rec_id, competence in rows:
                row_index = self.tbl_competence_majal.rowCount()
                self.tbl_competence_majal.insertRow(row_index)
                item = QTableWidgetItem(competence)
                item.setData(Qt.UserRole, rec_id)
                self.tbl_competence_majal.setItem(row_index, 0, item)

            if self.tbl_competence_majal.rowCount() > 0:
                self.tbl_competence_majal.selectRow(0)
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", f"تعذر تحميل كفايات المجال: {exc}")
        finally:
            self.tbl_competence_majal.setUpdatesEnabled(True)
            self.tbl_competence_matiere.setUpdatesEnabled(True)

    def on_competence_majal_selected(self):
        row = self.tbl_competence_majal.currentRow()
        if row < 0:
            self.selected_competence_majal_id = None
            self.tbl_competence_matiere.setRowCount(0)
            return
        
        item = self.tbl_competence_majal.item(row, 0)
        self.selected_competence_majal_id = item.data(Qt.UserRole)
        self.load_competence_matiere_data()

    def load_competence_matiere_data(self):
        self.tbl_competence_matiere.setRowCount(0)
        if not self.selected_competence_majal_id:
            return

        try:
            with self.db.connect() as con:
                for row in con.execute("""
                    SELECT id, matiere, competence FROM competence_matiere 
                    WHERE id_competence_majal = ?
                """, (self.selected_competence_majal_id,)).fetchall():
                    r = self.tbl_competence_matiere.rowCount()
                    self.tbl_competence_matiere.insertRow(r)
                    
                    item_matiere = QTableWidgetItem(row[1])
                    item_matiere.setData(Qt.UserRole, row[0])
                    item_comp = QTableWidgetItem(row[2])
                    
                    self.tbl_competence_matiere.setItem(r, 0, item_matiere)
                    self.tbl_competence_matiere.setItem(r, 1, item_comp)
        except Exception as e:
            print(f"Error loading competence_matiere: {e}")

    def copy_majal_with_matieres(self):
        if not self.selected_competence_majal_id:
            if self.tbl_competence_majal.rowCount() > 0:
                self.tbl_competence_majal.selectRow(0)
            else:
                QMessageBox.warning(self, "تنبيه", "لا توجد كفاية مجال مسجلة في هذا المستوى والمجال لنسخها.")
                return

        row = self.tbl_competence_majal.currentRow()
        item = self.tbl_competence_majal.item(row, 0)
        majal_competence_text = item.text()

        matieres_list = []
        try:
            with self.db.connect() as con:
                for r in con.execute("""
                    SELECT matiere, competence FROM competence_matiere 
                    WHERE id_competence_majal = ?
                """, (self.selected_competence_majal_id,)).fetchall():
                    matieres_list.append({"matiere": r[0], "competence": r[1]})

            self.copied_majal_data = {
                "competence": majal_competence_text,
                "matieres": matieres_list
            }
            QMessageBox.information(self, "نجاح النسخ", f"تم نسخ كفاية المجال مع {len(matieres_list)} من كفايات المواد التابعة لها بنجاح.")
        except Exception as e:
            QMessageBox.critical(self, "خطأ", f"حدث خطأ أثناء نسخ البيانات:\n{e}")

    def paste_majal_with_matieres(self):
        if not self.copied_majal_data:
            QMessageBox.warning(self, "تنبيه", "لا توجد بيانات منسوخة حالياً في الذاكرة.")
            return
        if not self.selected_level_id or not self.selected_majal_id:
            QMessageBox.warning(self, "تنبيه", "يرجى اختيار المستوى والمجال المستهدف للصق فيه.")
            return

        try:
            with self.db.connect() as con:
                cursor = con.cursor()
                cursor.execute("""
                    SELECT id FROM competence_majal 
                    WHERE id_majal = ? AND id_level = ?
                """, (self.selected_majal_id, self.selected_level_id))
                existing_row = cursor.fetchone()

                if existing_row:
                    majal_id = existing_row[0]
                    cursor.execute("""
                        UPDATE competence_majal 
                        SET competence = ? 
                        WHERE id = ?
                    """, (self.copied_majal_data["competence"], majal_id))
                    
                    cursor.execute("""
                        DELETE FROM competence_matiere 
                        WHERE id_competence_majal = ?
                    """, (majal_id,))
                else:
                    cursor.execute("""
                        INSERT INTO competence_majal (competence, id_majal, id_level) 
                        VALUES (?, ?, ?)
                    """, (self.copied_majal_data["competence"], self.selected_majal_id, self.selected_level_id))
                    majal_id = cursor.lastrowid

                for mat in self.copied_majal_data["matieres"]:
                    cursor.execute("""
                        INSERT INTO competence_matiere (matiere, competence, id_competence_majal) 
                        VALUES (?, ?, ?)
                    """, (mat["matiere"], mat["competence"], majal_id))

                con.commit()

            self.load_competence_majal_data()
        except Exception as e:
            QMessageBox.critical(self, "خطأ", f"حدث خطأ أثناء عملية اللصق:\n{e}")

    def add_competence_majal(self):
        if not self.selected_level_id or not self.selected_majal_id:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد المستوى والمجال أولاً.")
            return

        # ⬅️ استخدام الحقل المتعدد الأسطر (multiline)
        fields = [("نص كفاية المجال:", "multiline", "")]
        dlg = EntryDialog("إضافة كفاية مجال جديدة", fields, parent=self)
        if dlg.exec():
            vals = dlg.values()
            if not vals[0]:
                return
            try:
                with self.db.connect() as con:
                    con.execute("""
                        INSERT INTO competence_majal (competence, id_majal, id_level) 
                        VALUES (?, ?, ?)
                    """, (vals[0], self.selected_majal_id, self.selected_level_id))
                    con.commit()
                self.load_competence_majal_data()
                # تم إزالة رسالة النجاح المنبثقة بناءً على طلبك
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر الإضافة (قد تكون موجودة مسبقاً):\n{e}")

    def edit_competence_majal(self):
        row = self.tbl_competence_majal.currentRow()
        if row < 0:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد كفاية المجال للتعديل.")
            return
        
        item = self.tbl_competence_majal.item(row, 0)
        rec_id = item.data(Qt.UserRole)
        current_text = item.text()

        # ⬅️ استخدام الحقل المتعدد الأسطر (multiline)
        fields = [("تعديل نص كفاية المجال:", "multiline", current_text)]
        dlg = EntryDialog("تعديل كفاية المجال", fields, parent=self)
        if dlg.exec():
            vals = dlg.values()
            if not vals[0]:
                return
            try:
                with self.db.connect() as con:
                    con.execute("UPDATE competence_majal SET competence = ? WHERE id = ?", (vals[0], rec_id))
                    con.commit()
                self.load_competence_majal_data()
                # تم إزالة رسالة النجاح المنبثقة
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"حدث خطأ أثناء التحديث:\n{e}")

    def delete_competence_majal(self):
        row = self.tbl_competence_majal.currentRow()
        if row < 0:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد كفاية المجال للحذف.")
            return
        
        item = self.tbl_competence_majal.item(row, 0)
        rec_id = item.data(Qt.UserRole)

        if QMessageBox.question(self, "تأكيد الحذف", "حذف كفاية المجال سيؤدي لحذف كافة كفايات المواد والمكونات التابعة لها. هل أنت متأكد؟", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            try:
                with self.db.connect() as con:
                    con.execute("DELETE FROM competence_majal WHERE id = ?", (rec_id,))
                    con.commit()
                self.load_competence_majal_data()
                self.tbl_competence_matiere.setRowCount(0)
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر الحذف:\n{e}")

    def add_competence_matiere(self):
        if not self.selected_competence_majal_id:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد كفاية المجال أولاً.")
            return

        # ⬅️ استخدام الحقل المتعدد الأسطر (multiline) لكفاية المادة
        fields = [
            ("اسم المادة:", "line", ""),
            ("نص كفاية المادة:", "multiline", "")
        ]
        dlg = EntryDialog("إضافة كفاية ومادة جديدة", fields, parent=self)
        if dlg.exec():
            vals = dlg.values()
            matiere, competence = vals[0], vals[1]
            if not matiere or not competence:
                QMessageBox.warning(self, "بيانات ناقصة", "اسم المادة وكفاية المادة حقلان أساسيان.")
                return
            try:
                with self.db.connect() as con:
                    con.execute("""
                        INSERT INTO competence_matiere (matiere, competence, id_competence_majal) 
                        VALUES (?, ?, ?)
                    """, (matiere, competence, self.selected_competence_majal_id))
                    con.commit()
                self.load_competence_matiere_data()
                # تم إزالة رسالة النجاح المنبثقة
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر الإضافة (قد تكون المادة مسجلة مسبقاً بنفس السياق):\n{e}")

    def edit_competence_matiere(self):
        row = self.tbl_competence_matiere.currentRow()
        if row < 0:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد السطر المراد تعديله.")
            return
        
        item_matiere = self.tbl_competence_matiere.item(row, 0)
        item_comp = self.tbl_competence_matiere.item(row, 1)
        rec_id = item_matiere.data(Qt.UserRole)
        
        # ⬅️ استخدام الحقل المتعدد الأسطر (multiline) لكفاية المادة
        fields = [
            ("اسم المادة:", "line", item_matiere.text()),
            ("نص كفاية المادة:", "multiline", item_comp.text())
        ]
        dlg = EntryDialog("تعديل المادة وكفايتها", fields, parent=self)
        if dlg.exec():
            vals = dlg.values()
            matiere, competence = vals[0], vals[1]
            if not matiere or not competence:
                QMessageBox.warning(self, "بيانات ناقصة", "جميع الحقول مطلوبة.")
                return
            try:
                with self.db.connect() as con:
                    con.execute("""
                        UPDATE competence_matiere 
                        SET matiere = ?, competence = ? 
                        WHERE id = ?
                    """, (matiere, competence, rec_id))
                    con.commit()
                self.load_competence_matiere_data()
                # تم إزالة رسالة النجاح المنبثقة
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"حدث خطأ أثناء التحديث:\n{e}")

    def delete_competence_matiere(self):
        row = self.tbl_competence_matiere.currentRow()
        if row < 0:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد المادة المراد حذفها.")
            return
        
        item = self.tbl_competence_matiere.item(row, 0)
        rec_id = item.data(Qt.UserRole)

        if QMessageBox.question(self, "تأكيد الحذف", "هل أنت متأكد من حذف هذه المادة وكفايتها؟", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            try:
                with self.db.connect() as con:
                    con.execute("DELETE FROM competence_matiere WHERE id = ?", (rec_id,))
                    con.commit()
                self.load_competence_matiere_data()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر الحذف:\n{e}")


class TimetableDialog(QDialog):
    """جدول الأوقات الأسبوعي: يُملأ آليًا بأكثر حصة تكرّرت في كل يوم وساعة (باستعمال pandas)."""

    DAYS = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"]
    FIRST_HOUR = 8
    LAST_HOUR = 17          # نهاية آخر خانة 16:00 - 17:00
    _AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

    def __init__(self, df_history, parent=None, schedule=None):
        super().__init__(parent)
        self.setWindowTitle("جدول الأوقات")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1280, 520)
        self.n_slots = self.LAST_HOUR - self.FIRST_HOUR

        layout = QVBoxLayout(self)
        title = QLabel("🗓 جدول الأوقات")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #1E40AF; padding: 4px;")
        layout.addWidget(title)

        self.table = QTableWidget(len(self.DAYS), self.n_slots)
        self.table.setHorizontalHeaderLabels(
            [f"{h:02d}:00 - {h + 1:02d}:00" for h in range(self.FIRST_HOUR, self.LAST_HOUR)]
        )
        self.table.setVerticalHeaderLabels(self.DAYS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setWordWrap(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setMinimumWidth(80)
        layout.addWidget(self.table, 1)

        self.info_label = QLabel()
        self.info_label.setStyleSheet("color: #555;")
        layout.addWidget(self.info_label)

        buttons = QDialogButtonBox()
        buttons.addButton("إغلاق", QDialogButtonBox.RejectRole)
        set_dialog_button_action_types(buttons)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        schedule = (
            schedule if schedule is not None else self.build_schedule(df_history)
        )
        self.fill_table(schedule)

    # ---------- التحليل ----------
    @classmethod
    def _parse_heure(cls, text):
        """يرجع (بداية، نهاية) بالدقائق من نص مثل '08:00-09:00'، والنهاية None إن غابت."""
        text = str(text or "").translate(cls._AR_DIGITS)
        found = re.findall(r"(\d{1,2})\s*[:hH.]\s*(\d{2})", text)
        if not found:
            return None, None
        start = int(found[0][0]) * 60 + int(found[0][1])
        end = int(found[1][0]) * 60 + int(found[1][1]) if len(found) > 1 else None
        return start, end

    @classmethod
    def _duration_minutes(cls, heure, time_text):
        start, end = cls._parse_heure(heure)
        if start is not None and end is not None and end > start:
            return end - start
        m = re.search(r"\d+", str(time_text or "").translate(cls._AR_DIGITS))
        return int(m.group()) if m else 60

    @classmethod
    def schedule_time_bounds(cls, df):
        if df is None or df.empty:
            return cls.FIRST_HOUR, cls.LAST_HOUR
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        df = df[df["date"].dt.dayofweek <= 5]
        starts = []
        ends = []
        for row in df.itertuples(index=False):
            classe = str(row.classe or "").strip()
            majal = str(row.majal or "").strip()
            if not classe and not majal:
                continue
            start, _end = cls._parse_heure(row.heure)
            if start is None or not 0 <= start < 24 * 60:
                continue
            duration = max(
                60, cls._duration_minutes(row.heure, row.time)
            )
            starts.append(start // 60)
            ends.append(min(24, (start + duration + 59) // 60))
        if not starts:
            return cls.FIRST_HOUR, cls.LAST_HOUR
        first_hour = min(starts)
        last_hour = max(ends)
        return first_hour, max(first_hour + 1, last_hour)

    @classmethod
    def build_schedule(cls, df, first_hour=None, last_hour=None):
        """يرجع {صف_اليوم: [(عمود، امتداد، نص، تكرار)]} بعد اختيار الأكثر تكرارًا دون تداخل."""
        if df is None or df.empty:
            return {}
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        df["wd"] = df["date"].dt.dayofweek
        df = df[df["wd"] <= 5]          # من الإثنين إلى السبت فقط

        first_hour = (
            cls.FIRST_HOUR if first_hour is None else first_hour
        )
        last_hour = cls.LAST_HOUR if last_hour is None else last_hour
        n_slots = last_hour - first_hour
        counter = {}
        for r in df.itertuples(index=False):
            classe = str(r.classe or "").strip()
            majal = str(r.majal or "").strip()
            if not classe and not majal:
                continue
            start, _end = cls._parse_heure(r.heure)
            if start is None:
                continue
            col = start // 60 - first_hour
            if not 0 <= col < n_slots:
                continue
            minutes = cls._duration_minutes(r.heure, r.time)
            span = max(
                1,
                min((minutes + 59) // 60, n_slots - col),
            )
            key = (int(r.wd), col, span, classe, majal)
            counter[key] = counter.get(key, 0) + 1

        schedule = {}
        for wd in range(len(cls.DAYS)):
            candidates = [(cnt, k) for k, cnt in counter.items() if k[0] == wd]
            # الأكثر تكرارًا أولًا، وعند التساوي الأبكر ثم الأطول
            candidates.sort(key=lambda x: (-x[0], x[1][1], -x[1][2]))
            occupied, cells = set(), []
            for cnt, (_wd, col, span, classe, majal) in candidates:
                cols = set(range(col, col + span))
                if cols & occupied:
                    continue
                occupied |= cols
                text = f"{classe}:{majal}" if classe and majal else (classe or majal)
                cells.append((col, span, text, cnt))
            schedule[wd] = cells
        return schedule

    # ---------- العرض ----------
    def fill_table(self, schedule):
        palette = ["#DBEAFE", "#DCFCE7", "#FEF3C7", "#FCE7F3", "#E0E7FF", "#CCFBF1", "#FFEDD5"]
        colors, total = {}, 0
        for wd, cells in schedule.items():
            for col, span, text, cnt in cells:
                majal = text.split(":", 1)[-1]
                color = colors.setdefault(majal, palette[len(colors) % len(palette)])
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(QColor(color))
                item.setForeground(QColor("#111827"))
                item.setToolTip(f"{text}\nتكرّرت {cnt} مرة" + (" (حصة مدتها 120 دقيقة)" if span > 1 else ""))
                self.table.setItem(wd, col, item)
                if span > 1:
                    self.table.setSpan(wd, col, 1, span)
                total += 1
        if total:
            self.info_label.setText(
                "يُملأ الجدول بأكثر حصة تكرّرت في كل يوم وساعة بحسب السجل. "
                "الحصص ذات 120 دقيقة تُدمج خليتاها."
            )
        else:
            self.info_label.setText("لا توجد حصص مسجلة بعد لبناء جدول الأوقات.")





class ConceptManagerDialog(QDialog):
    """حوار إدارة المفاهيم (CRUD كامل لجدول concept).

    يُفتح بالاختصار Ctrl+Shift+R. عمود name فريد (UNIQUE) ولا يُسمح بالتكرار.
    """

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("إدارة المفاهيم المتصلة بالمقاربة بالكفايات")
        self.resize(720, 520)
        self.setLayoutDirection(Qt.RightToLeft)
        self.db = db if db else Database(DB_PATH)
        self.selected_concept_id = None
        self.init_ui()
        self.load_concepts()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        title_label = QLabel("🧠 إدارة المفاهيم (اسم العنصر + محتوى العنصر)")
        title_label.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #1E40AF; padding: 5px;"
        )
        main_layout.addWidget(title_label)

        btns_layout = QHBoxLayout()
        for text, slot, act_type in [
            ("＋ إضافة", self.add_concept, "add"),
            ("✎ تعديل", self.edit_concept, "edit"),
            ("－ حذف", self.delete_concept, "delete"),
        ]:
            b = QPushButton(text)
            b.setMinimumWidth(110)
            b.setProperty("actionType", act_type)
            b.clicked.connect(slot)
            btns_layout.addWidget(b)
        btns_layout.addStretch()
        main_layout.addLayout(btns_layout)

        self.tbl_concepts = QTableWidget(0, 2)
        self.tbl_concepts.setHorizontalHeaderLabels(["اسم العنصر", "محتوى العنصر"])
        self.tbl_concepts.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_concepts.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_concepts.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_concepts.setAlternatingRowColors(True)
        self.tbl_concepts.verticalHeader().setVisible(False)
        self.tbl_concepts.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_concepts.itemSelectionChanged.connect(self.on_concept_selected)
        main_layout.addWidget(self.tbl_concepts, 1)

        self.lbl_status = QLabel("0 مفهوم")
        self.lbl_status.setStyleSheet("color: #6B7280; padding: 3px;")
        main_layout.addWidget(self.lbl_status)

        close_btn = QPushButton("إغلاق")
        close_btn.setProperty("actionType", "close")
        close_btn.clicked.connect(self.accept)

        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        bottom_layout.addWidget(close_btn)
        main_layout.addLayout(bottom_layout)

    def load_concepts(self):
        self.tbl_concepts.setUpdatesEnabled(False)
        try:
            self.tbl_concepts.setRowCount(0)
            with self.db.connect() as con:
                rows = con.execute(
                    "SELECT id, name, content FROM concept ORDER BY name COLLATE NOCASE"
                ).fetchall()
            for rec_id, name, content in rows:
                r = self.tbl_concepts.rowCount()
                self.tbl_concepts.insertRow(r)
                item_name = QTableWidgetItem(name)
                item_name.setData(Qt.UserRole, rec_id)
                self.tbl_concepts.setItem(r, 0, item_name)
                self.tbl_concepts.setItem(r, 1, QTableWidgetItem(content or ""))
            self.lbl_status.setText(f"{self.tbl_concepts.rowCount()} مفهوم")
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", f"تعذر تحميل المفاهيم:\n{exc}")
        finally:
            self.tbl_concepts.setUpdatesEnabled(True)

    def on_concept_selected(self):
        row = self.tbl_concepts.currentRow()
        if row < 0:
            self.selected_concept_id = None
            return
        item = self.tbl_concepts.item(row, 0)
        self.selected_concept_id = item.data(Qt.UserRole) if item else None

    def add_concept(self):
        dlg = EntryDialog(
            "إضافة مفهوم جديد",
            [("اسم العنصر:", "line", ""), ("محتوى العنصر:", "multiline", "")],
            parent=self,
        )
        if not dlg.exec():
            return
        vals = dlg.values()
        name = vals[0]
        content = vals[1]
        try:
            self.db.save_concept(name, content)
            self.load_concepts()
        except ValueError as exc:
            QMessageBox.warning(self, "اسم مكرر", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", f"تعذر إضافة المفهوم:\n{exc}")

    def edit_concept(self):
        if not self.selected_concept_id:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد مفهوم للتعديل.")
            return
        row = self.tbl_concepts.currentRow()
        current_name = self.tbl_concepts.item(row, 0).text()
        current_content = self.tbl_concepts.item(row, 1).text()
        dlg = EntryDialog(
            "تعديل المفهوم",
            [("اسم العنصر:", "line", ""), ("محتوى العنصر:", "multiline", "")],
            values=[current_name, current_content],
            parent=self,
        )
        if not dlg.exec():
            return
        vals = dlg.values()
        try:
            self.db.save_concept(vals[0], vals[1], concept_id=self.selected_concept_id)
            self.load_concepts()
        except ValueError as exc:
            QMessageBox.warning(self, "اسم مكرر", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", f"تعذر تعديل المفهوم:\n{exc}")

    def delete_concept(self):
        if not self.selected_concept_id:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد مفهوم للحذف.")
            return
        row = self.tbl_concepts.currentRow()
        name = self.tbl_concepts.item(row, 0).text()
        confirm = QMessageBox.question(
            self, "تأكيد الحذف",
            f"هل تريد حذف المفهوم '{name}'؟",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self.db.delete_concept(self.selected_concept_id)
            self.selected_concept_id = None
            self.load_concepts()
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", f"تعذر حذف المفهوم:\n{exc}")
