# -*- coding: utf-8 -*-
"""Extracted module widgets."""
from pathlib import Path

from PySide6.QtCore import (
    Qt, QDate, QLocale, QSize, QTimer,
)
from PySide6.QtGui import (
    QColor, QTextCharFormat, QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCalendarWidget, QComboBox,
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
    QHeaderView, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSplitter, QDateEdit,
    QMessageBox,
    QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QFrame,
    QGridLayout,
)

from app_config import (
    set_dialog_button_action_types,
)
from busy_overlay import start_background_task
from database_connection import connect_database
from day_pdf_preview import (
    DayPdfPreviewDialog,
)
from lesson_book_settings import (
    filter_print_details,
    load_print_detail_exclusions,
)
import sqlite3


def _wrapped_text_height(text, width, font, padding=8):
    """قياس ارتفاع النص بعد الالتفاف على عرض معين باستخدام QTextDocument."""
    doc = QTextDocument()
    doc.setDefaultFont(font)
    doc.setPlainText(str(text or ""))
    doc.setTextWidth(max(20, width))
    return int(doc.size().height()) + padding


class CustomCalendarWidget(QCalendarWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._holiday_date_disabling_enabled = False
        self._holiday_dates = set()
        self._holiday_types = {}
        self._custom_disabled_dates = set()
        self._custom_disabled_tooltip = ""
        self._last_enabled_date = self.selectedDate()
        self.selectionChanged.connect(self.restore_enabled_selection)
        self.refresh_recorded_dates()

    @property
    def _disabled_dates(self):
        return self._holiday_dates

    def set_disabled_dates(
        self, dates, tooltip, background_color="#E2E8F0"
    ):
        self._custom_disabled_dates = {
            day for day in dates if day.isValid()
        }
        self._custom_disabled_tooltip = str(tooltip)
        self._custom_disabled_background_color = QColor(background_color)
        selected = self.selectedDate()
        if self._is_disabled_date(selected):
            self._last_enabled_date = self._nearest_enabled_date(selected)
            if not self._is_disabled_date(self._last_enabled_date):
                self.blockSignals(True)
                self.setSelectedDate(self._last_enabled_date)
                self.blockSignals(False)
        else:
            self._last_enabled_date = selected
        self._apply_disabled_date_formats()

    def set_holiday_date_disabling_enabled(self, enabled):
        self._holiday_date_disabling_enabled = bool(enabled)
        if self._holiday_date_disabling_enabled:
            self.refresh_holiday_dates()

    def showEvent(self, event):
        super().showEvent(event)
        if self._holiday_date_disabling_enabled:
            self.refresh_holiday_dates()

    def refresh_holiday_dates(self):
        disabled_dates = set()
        holiday_types = {}
        for (
            _holiday_id, start_text, end_text, _name, holiday_type
        ) in self.db.holidays():
            current_date = QDate.fromString(start_text, "yyyy-MM-dd")
            end_date = QDate.fromString(end_text, "yyyy-MM-dd")
            while current_date.isValid() and current_date <= end_date:
                disabled_dates.add(current_date)
                holiday_types[current_date] = holiday_type
                current_date = current_date.addDays(1)
        self._holiday_dates = disabled_dates
        self._holiday_types = holiday_types
        selected = self.selectedDate()
        if not self._is_disabled_date(selected):
            self._last_enabled_date = selected
        elif self._is_disabled_date(self._last_enabled_date):
            self._last_enabled_date = self._nearest_enabled_date(selected)
            if not self._is_disabled_date(self._last_enabled_date):
                self.blockSignals(True)
                self.setSelectedDate(self._last_enabled_date)
                self.blockSignals(False)
        self._apply_disabled_date_formats()

    def _is_disabled_date(self, day):
        return (
            day in self._holiday_dates
            or day in self._custom_disabled_dates
        )

    def _nearest_enabled_date(self, reference_date):
        minimum = self.minimumDate()
        maximum = self.maximumDate()
        for distance in range(1, 367):
            for candidate in (
                reference_date.addDays(distance),
                reference_date.addDays(-distance),
            ):
                if (
                    candidate.isValid()
                    and minimum <= candidate <= maximum
                    and not self._is_disabled_date(candidate)
                ):
                    return candidate
        return reference_date

    def restore_enabled_selection(self):
        selected = self.selectedDate()
        if self._is_disabled_date(selected):
            self.blockSignals(True)
            fallback = self._last_enabled_date
            if self._is_disabled_date(fallback):
                fallback = self._nearest_enabled_date(selected)
            self.setSelectedDate(fallback)
            self.blockSignals(False)
            return
        self._last_enabled_date = selected

    def _apply_disabled_date_formats(self):
        for disabled_date in self._holiday_dates | self._custom_disabled_dates:
            disabled_format = QTextCharFormat()
            disabled_format.setForeground(QColor("#94A3B8"))
            if disabled_date in self._holiday_dates:
                disabled_format.setBackground(QColor("#E2E8F0"))
                holiday_type = self._holiday_types.get(
                    disabled_date, "official"
                )
                tooltip = (
                    "عطلة رسمية" if holiday_type == "official"
                    else "غياب طارئ غير رسمي"
                ) + " — لا يمكن اختيار هذا اليوم"
            else:
                disabled_format.setBackground(
                    self._custom_disabled_background_color
                )
                tooltip = self._custom_disabled_tooltip
            disabled_format.setToolTip(tooltip)
            self.setDateTextFormat(disabled_date, disabled_format)

    def refresh_recorded_dates(self):
        try:
            self.setDateTextFormat(QDate(), QTextCharFormat())
            year_id = getattr(self, "year_id", self.db.year_id)
            with self.db.connect() as con:
                cursor = con.cursor()
                cursor.execute("""
                    SELECT DISTINCT d.date 
                    FROM day_data d
                    JOIN hessa h ON h.day_data_id = d.id
                    WHERE d.ann_scolaire_id = ?
                    """, (year_id,))
                rows = cursor.fetchall()
                incomplete_dates = self.db.incomplete_workday_dates(year_id)
                recorded_format = QTextCharFormat()
                recorded_format.setBackground(QColor("#D4EDDA"))
                recorded_format.setForeground(QColor("#155724"))
                incomplete_format = QTextCharFormat()
                incomplete_format.setBackground(QColor("#FEE2E2"))
                incomplete_format.setForeground(QColor("#991B1B"))
                incomplete_format.setToolTip("يحتوي هذا اليوم على حصة غير مكتملة")
                for row in rows:
                    if row[0]:
                        qd = QDate.fromString(row[0], "yyyy-MM-dd")
                        if qd.isValid():
                            self.setDateTextFormat(
                                qd,
                                incomplete_format
                                if row[0] in incomplete_dates else recorded_format,
                            )
            self._apply_disabled_date_formats()
        except Exception as e:
            print(f"Error loading recorded dates: {e}")


class OfficialProgramsTreeWidget(QWidget):
    def __init__(
        self, db, level_id, level_name, majal_id=None, majal_name="",
        parent=None, activity_name=None, embedded=False
    ):
        super().__init__(parent, Qt.Widget if embedded else Qt.Window)
        self.db = db
        self.level_id = level_id
        self.majal_id = majal_id
        self.level_name = level_name
        self.majal_name = majal_name
        self.activity_name = activity_name
        self.setObjectName("officialProgramsTreeDialog")
        self.setWindowTitle("شجرة البرامج الرسمية")
        self.setLayoutDirection(Qt.RightToLeft)
        if not embedded:
            self.setMinimumSize(900, 600)

        layout = QVBoxLayout(self)
        self.info_label = QLabel()
        self.info_label.setObjectName("officialTreeInfo")
        self._update_info_label()
        layout.addWidget(self.info_label)

        self.tree = QTreeWidget()
        self.tree.setObjectName("officialProgramsTree")
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setAnimated(True)
        self.tree.setIndentation(26)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self.tree.setColumnWidth(1, 56)
        layout.addWidget(self.tree, 1)

        self.status_label = QLabel("اضغط أيقونة النسخ بجانب العنصر المطلوب.")
        self.status_label.setObjectName("officialTreeStatus")
        layout.addWidget(self.status_label)
        self.refresh_tree()

    def _update_info_label(self):
        scope = f"المستوى: {self.level_name or '—'}"
        if self.majal_id:
            scope += f"    |    المجال: {self.majal_name or '—'}"
        if self.activity_name:
            scope += f"    |    النشاط: {self.activity_name}"
        self.info_label.setText(scope)

    def refresh_tree(self):
        self._update_info_label()
        if not self.level_id:
            self.tree.clear()
            self.status_label.setText("اختر المستوى والمجال والنشاط لعرض البرامج الرسمية.")
            return

        query = """
            SELECT mj.name, cmj.id, cmj.competence,
                cm.id, cm.matiere, cm.competence,
                mk.id, mk.competence,
                os.id, os.objectif_specifique,
                cnt.id, cnt.content
            FROM competence_majal cmj
            JOIN majal mj ON mj.id=cmj.id_majal
            LEFT JOIN competence_matiere cm
                ON cm.id_competence_majal=cmj.id
            LEFT JOIN moukawan_kifaya mk
                ON mk.id_competence_matiere=cm.id
            LEFT JOIN objectif_specifique os
                ON os.id_moukawa_kifaya=mk.id
            LEFT JOIN content cnt
                ON cnt.id_objectif_specifique=os.id
            WHERE cmj.id_level=?
        """
        params = [self.level_id]
        if self.majal_id:
            query += " AND cmj.id_majal=?"
            params.append(self.majal_id)
        if self.activity_name:
            query += " AND cm.matiere=?"
            params.append(self.activity_name)
        query += """
            ORDER BY mj.name, cmj.id, cm.matiere, cm.id,
                mk.id, os.id, cnt.id
        """

        with self.db.connect() as con:
            rows = con.execute(query, params).fetchall()

        self.tree.clear()
        domain_items = {}
        activity_items = {}
        component_items = {}
        objective_items = {}

        for row in rows:
            (
                domain_name, domain_competence_id, domain_competence,
                activity_id, activity_name, activity_competence,
                component_id, component_text,
                objective_id, objective_text,
                content_id, content_text
            ) = row

            domain_item = domain_items.get(domain_competence_id)
            if domain_item is None:
                domain_item = self._create_tree_item(
                    self.tree.invisibleRootItem(),
                    f"المجال: {domain_name} — كفاية المجال: {domain_competence}"
                )
                domain_items[domain_competence_id] = domain_item

            if activity_id is None:
                continue
            activity_item = activity_items.get(activity_id)
            if activity_item is None:
                activity_item = self._create_tree_item(
                    domain_item,
                    f"النشاط: {activity_name} — كفاية النشاط: {activity_competence}"
                )
                activity_items[activity_id] = activity_item

            if component_id is None:
                continue
            component_item = component_items.get(component_id)
            if component_item is None:
                component_item = self._create_tree_item(activity_item, component_text)
                component_items[component_id] = component_item

            if objective_id is None:
                continue
            objective_item = objective_items.get(objective_id)
            if objective_item is None:
                objective_item = self._create_tree_item(component_item, objective_text)
                objective_items[objective_id] = objective_item

            if content_id is not None:
                self._create_tree_item(objective_item, content_text)

        self.tree.expandToDepth(1)
        if not rows:
            self.status_label.setText("لا توجد برامج رسمية مسجلة ضمن التحديد الحالي.")
        else:
            self.status_label.setText("اضغط أيقونة النسخ بجانب أي مجال أو كفاية أو هدف أو محتوى لنسخه.")

    def _create_tree_item(self, parent, text):
        text = str(text or "")
        item = QTreeWidgetItem(parent, [text, ""])
        item.setData(0, Qt.UserRole, text)
        item.setToolTip(0, text)

        copy_button = QToolButton(self.tree)
        copy_button.setObjectName("officialTreeCopyButton")
        copy_button.setText("📋")
        copy_button.setToolTip("نسخ النص")
        copy_button.setAutoRaise(True)
        copy_button.setFixedWidth(44)
        copy_button.setMaximumWidth(48)
        copy_button.clicked.connect(lambda _checked=False, value=text: self.copy_text(value))
        self.tree.setItemWidget(item, 1, copy_button)
        return item

    def copy_text(self, text):
        QApplication.clipboard().setText(text)
        self.status_label.setText("تم نسخ النص إلى الحافظة.")


class OfficialProgramsBrowsePage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setLayoutDirection(Qt.RightToLeft)

        layout = QVBoxLayout(self)
        filters = QGroupBox("اختر المستوى والمجال والنشاط")
        filters_layout = QHBoxLayout(filters)
        self.combo_level = QComboBox()
        self.combo_majal = QComboBox()
        self.combo_activity = QComboBox()
        self.combo_level.currentIndexChanged.connect(self.on_level_changed)
        self.combo_majal.currentIndexChanged.connect(self.on_majal_changed)
        self.combo_activity.currentIndexChanged.connect(self.on_activity_changed)

        filters_layout.addWidget(QLabel("المستوى:"))
        filters_layout.addWidget(self.combo_level, 1)
        filters_layout.addWidget(QLabel("المجال:"))
        filters_layout.addWidget(self.combo_majal, 1)
        filters_layout.addWidget(QLabel("النشاط / المادة:"))
        filters_layout.addWidget(self.combo_activity, 1)
        layout.addWidget(filters)

        self.tree_view = OfficialProgramsTreeWidget(
            db, None, "", parent=self, embedded=True
        )
        results_group = QGroupBox("شجرة نتائج البرامج الرسمية")
        results_layout = QVBoxLayout(results_group)
        results_layout.addWidget(self.tree_view)
        layout.addWidget(results_group, 1)
        self._reset_combo(self.combo_level, "اختر المستوى")
        self._reset_combo(self.combo_majal, "اختر المجال")
        self._reset_combo(self.combo_activity, "اختر النشاط / المادة")
        self.combo_majal.setEnabled(False)
        self.combo_activity.setEnabled(False)

    @staticmethod
    def _reset_combo(combo, placeholder):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(placeholder, None)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def load_levels(self):
        self._reset_combo(self.combo_level, "اختر المستوى")
        self._reset_combo(self.combo_majal, "اختر المجال")
        self._reset_combo(self.combo_activity, "اختر النشاط / المادة")
        self.combo_majal.setEnabled(False)
        self.combo_activity.setEnabled(False)
        self.tree_view.level_id = None
        self.tree_view.majal_id = None
        self.tree_view.activity_name = None
        self.tree_view.refresh_tree()
        try:
            with self.db.connect() as con:
                rows = con.execute(
                    """SELECT DISTINCT l.id, l.name
                       FROM level l
                       JOIN competence_majal cmj ON cmj.id_level=l.id
                       ORDER BY l.name"""
                ).fetchall()
            for level_id, name in rows:
                self.combo_level.addItem(name, level_id)
        except sqlite3.Error as exc:
            QMessageBox.critical(
                self, "تعذر تحميل المستويات",
                f"تعذر تحميل مستويات البرامج الرسمية:\n{exc}"
            )

    def on_level_changed(self, *_args):
        level_id = self.combo_level.currentData()
        self._reset_combo(self.combo_majal, "اختر المجال")
        self._reset_combo(self.combo_activity, "اختر النشاط / المادة")
        self.combo_majal.setEnabled(bool(level_id))
        self.combo_activity.setEnabled(False)
        self.tree_view.level_id = None
        self.tree_view.majal_id = None
        self.tree_view.activity_name = None
        self.tree_view.refresh_tree()
        if not level_id:
            return

        try:
            with self.db.connect() as con:
                rows = con.execute(
                    """SELECT DISTINCT m.id, m.name
                       FROM majal m
                       JOIN competence_majal cmj ON cmj.id_majal=m.id
                       WHERE cmj.id_level=?
                       ORDER BY m.name""",
                    (level_id,),
                ).fetchall()
            for majal_id, name in rows:
                self.combo_majal.addItem(name, majal_id)
        except sqlite3.Error as exc:
            QMessageBox.critical(
                self, "تعذر تحميل المجالات",
                f"تعذر تحميل مجالات المستوى المحدد:\n{exc}"
            )

    def on_majal_changed(self, *_args):
        level_id = self.combo_level.currentData()
        majal_id = self.combo_majal.currentData()
        self._reset_combo(self.combo_activity, "اختر النشاط / المادة")
        self.combo_activity.setEnabled(bool(level_id and majal_id))
        self.tree_view.level_id = None
        self.tree_view.majal_id = None
        self.tree_view.activity_name = None
        self.tree_view.refresh_tree()
        if not level_id or not majal_id:
            return

        try:
            with self.db.connect() as con:
                rows = con.execute(
                    """SELECT DISTINCT cm.matiere
                       FROM competence_matiere cm
                       JOIN competence_majal cmj
                         ON cmj.id=cm.id_competence_majal
                       WHERE cmj.id_level=? AND cmj.id_majal=?
                       ORDER BY cm.matiere""",
                    (level_id, majal_id),
                ).fetchall()
            for (activity_name,) in rows:
                self.combo_activity.addItem(activity_name, activity_name)
        except sqlite3.Error as exc:
            QMessageBox.critical(
                self, "تعذر تحميل الأنشطة",
                f"تعذر تحميل أنشطة المجال المحدد:\n{exc}"
            )

    def on_activity_changed(self, *_args):
        activity_name = self.combo_activity.currentData()
        level_id = self.combo_level.currentData()
        majal_id = self.combo_majal.currentData()
        self.tree_view.level_id = level_id if activity_name else None
        self.tree_view.level_name = self.combo_level.currentText() if activity_name else ""
        self.tree_view.majal_id = majal_id if activity_name else None
        self.tree_view.majal_name = self.combo_majal.currentText() if activity_name else ""
        self.tree_view.activity_name = activity_name
        try:
            self.tree_view.refresh_tree()
        except sqlite3.Error as exc:
            QMessageBox.critical(
                self, "تعذر عرض البرامج",
                f"تعذر تحميل شجرة البرامج الرسمية:\n{exc}"
            )


class CompetencesCbaBrowsePage(QWidget):
    """صفحة تصفح المفاهيم المتصلة بالمقاربة بالكفايات.

    شريط بحث في الأعلى + شجرة تعرض المفاهيم (الاسم ثم المحتوى كطفل)،
    وأمام كل عنصر زر نسخ صغير ينسخ صيغة:
        الاسم:
        <المحتوى>
    """

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setLayoutDirection(Qt.RightToLeft)
        self.setObjectName("competencesCbaBrowsePage")

        root_layout = QVBoxLayout(self)
        self.page_stack = QStackedWidget(self)
        root_layout.addWidget(self.page_stack)

        self.browse_page = QWidget()
        layout = QVBoxLayout(self.browse_page)
        search_box = QGroupBox("البحث في المفاهيم")
        search_layout = QHBoxLayout(search_box)
        search_layout.addStretch(1)
        self.search_edit = QLineEdit()
        self.search_edit.setMaximumWidth(520)
        self.search_edit.setPlaceholderText(
            "اكتب كلمة للبحث في أسماء المفاهيم أو محتواها…"
        )
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.refresh_tree)
        search_layout.addWidget(self.search_edit, 1)
        search_layout.addStretch(1)
        layout.addWidget(search_box)

        browse_hint = QLabel("لاستعراض العنصر ومحتواه، انقر عليه مرتين متتاليتين.")
        browse_hint.setObjectName("competencesCbaBrowseHint")
        browse_hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(browse_hint)

        self.tree = QTreeWidget()
        self.tree.setObjectName("competencesCbaTree")
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setAnimated(True)
        self.tree.setIndentation(26)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self.tree.setColumnWidth(1, 56)
        self.tree.itemDoubleClicked.connect(self._show_concept_for_item)
        layout.addWidget(self.tree, 1)

        self.status_label = QLabel("اضغط أيقونة النسخ بجانب أي مفهوم لنسخه.")
        self.status_label.setObjectName("competencesCbaStatus")
        layout.addWidget(self.status_label)
        self.page_stack.addWidget(self.browse_page)

        self.details_page = QWidget()
        details_layout = QVBoxLayout(self.details_page)
        self.details_name_label = QLabel()
        self.details_name_label.setAlignment(Qt.AlignCenter)
        self.details_name_label.setWordWrap(True)
        self.details_name_label.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #1E40AF; padding: 10px;"
        )
        details_layout.addWidget(self.details_name_label)

        self.details_content_edit = QTextEdit()
        self.details_content_edit.setReadOnly(True)
        self.details_content_edit.setObjectName("conceptDetailsContent")
        details_layout.addWidget(self.details_content_edit, 1)

        self.details_status_label = QLabel()
        self.details_status_label.setAlignment(Qt.AlignCenter)
        details_layout.addWidget(self.details_status_label)

        details_buttons = QHBoxLayout()
        details_buttons.addStretch()
        self.details_copy_button = QPushButton("نسخ المحتوى")
        self.details_copy_button.setProperty("actionType", "copy_paste")
        self.details_copy_button.clicked.connect(self.copy_details_content)
        details_buttons.addWidget(self.details_copy_button)
        self.details_back_button = QPushButton("العودة")
        self.details_back_button.setProperty("actionType", "back")
        self.details_back_button.clicked.connect(
            lambda: self.page_stack.setCurrentWidget(self.browse_page)
        )
        details_buttons.addWidget(self.details_back_button)
        details_buttons.addStretch()
        details_layout.addLayout(details_buttons)
        self.page_stack.addWidget(self.details_page)

        self._all_concepts = []
        self.load_concepts()

    def load_concepts(self):
        try:
            with self.db.connect() as con:
                concepts = con.execute(
                    "SELECT id, name, content FROM concept ORDER BY name COLLATE NOCASE"
                ).fetchall()
        except sqlite3.Error as exc:
            self._all_concepts = []
            QMessageBox.critical(
                self, "تعذر تحميل المفاهيم",
                f"تعذر تحميل المفاهيم من قاعدة البيانات:\n{exc}"
            )
        else:
            self._all_concepts = concepts
        self.refresh_tree()

    def refresh_tree(self, *_args):
        query = self.search_edit.text().strip()
        self.tree.clear()
        matches = 0
        for _concept_id, name, content in self._all_concepts:
            haystack = f"{name or ''}\n{content or ''}"
            if query and query.lower() not in haystack.lower():
                continue
            matches += 1
            concept_item = self._create_tree_item(
                self.tree.invisibleRootItem(), f"{name or ''}",
                name, content, self._copy_payload(name, content),
            )
            self._create_tree_item(
                concept_item, content or "—",
                name, content, self._copy_payload(name, content),
            )
        self.tree.expandToDepth(1)
        total = len(self._all_concepts)
        if not total:
            self.status_label.setText(
                "لا توجد مفاهيم مسجلة بعد. استخدم Ctrl+Shift+R لإضافة مفاهيم."
            )
        elif matches == total:
            self.status_label.setText(
                f"عرض {total} مفهوم. اضغط أيقونة النسخ بجانب أي عنصر لنسخه."
            )
        else:
            self.status_label.setText(
                f"نتائج البحث: {matches} من {total} مفهوم."
            )

    @staticmethod
    def _copy_payload(name, content):
        return f"الاسم: {name or ''}\nالمحتوى: {content or ''}"

    def _create_tree_item(self, parent, text, name, content, copy_text):
        text = str(text or "")
        item = QTreeWidgetItem(parent, [text, ""])
        item.setData(0, Qt.UserRole, copy_text)
        item.setData(0, Qt.UserRole + 1, (name or "", content or ""))
        item.setToolTip(0, text)

        usable_width = max(
            120, self.tree.viewport().width() - self.tree.indentation() - 80
        )
        wrapped_height = _wrapped_text_height(text, usable_width, self.tree.font())
        item.setSizeHint(0, QSize(0, wrapped_height))

        copy_button = QToolButton(self.tree)
        copy_button.setObjectName("officialTreeCopyButton")
        copy_button.setText("📋")
        copy_button.setToolTip("نسخ النص")
        copy_button.setAutoRaise(True)
        copy_button.setFixedSize(28, 28)
        copy_button.setIconSize(QSize(16, 16))
        copy_button.setMaximumWidth(48)
        copy_button.clicked.connect(
            lambda _checked=False, value=copy_text: self.copy_text(value)
        )
        self.tree.setItemWidget(item, 1, copy_button)
        return item

    def _show_concept_for_item(self, item, _column):
        name, content = item.data(0, Qt.UserRole + 1)
        self.show_concept_details(name, content)

    def show_concept_details(self, name, content):
        self.details_name_label.setText(str(name or ""))
        self.details_content_edit.setPlainText(str(content or ""))
        self.details_status_label.clear()
        self.page_stack.setCurrentWidget(self.details_page)

    def copy_details_content(self):
        QApplication.clipboard().setText(self.details_content_edit.toPlainText())
        self.details_status_label.setText("تم نسخ المحتوى إلى الحافظة.")

    def copy_text(self, text):
        QApplication.clipboard().setText(text)
        self.status_label.setText("تم نسخ النص إلى الحافظة.")

class DayBrowseCalendar(CustomCalendarWidget):
    def __init__(self, db, year_id, parent=None):
        self.year_id = year_id
        super().__init__(db, parent)
        self.currentPageChanged.connect(self.refresh_recorded_dates)

    def refresh_recorded_dates(self, *_args):
        try:
            with self.db.connect() as con:
                rows = con.execute("""
                    SELECT DISTINCT d.date
                    FROM day_data d
                    JOIN hessa h ON h.day_data_id=d.id
                    WHERE d.ann_scolaire_id=?
                """, (self.year_id,)).fetchall()
            self.recorded_dates = {row[0] for row in rows}
            incomplete_dates = self.db.incomplete_workday_dates(self.year_id)

            self.setDateTextFormat(QDate(), QTextCharFormat())
            first_day = QDate(self.yearShown(), self.monthShown(), 1)
            day_count = first_day.daysInMonth()
            unrecorded_format = QTextCharFormat()
            unrecorded_format.setForeground(QColor("#94A3B8"))
            recorded_format = QTextCharFormat()
            recorded_format.setBackground(QColor("#D4EDDA"))
            recorded_format.setForeground(QColor("#155724"))
            incomplete_format = QTextCharFormat()
            incomplete_format.setBackground(QColor("#FEE2E2"))
            incomplete_format.setForeground(QColor("#991B1B"))
            incomplete_format.setToolTip(
                "يحتوي هذا اليوم على حصة غير مكتملة"
            )
            for day_number in range(1, day_count + 1):
                date_value = QDate(self.yearShown(), self.monthShown(), day_number)
                day_text = date_value.toString("yyyy-MM-dd")
                if day_text in incomplete_dates:
                    self.setDateTextFormat(date_value, incomplete_format)
                elif day_text in self.recorded_dates:
                    self.setDateTextFormat(date_value, recorded_format)
                else:
                    self.setDateTextFormat(date_value, unrecorded_format)
        except sqlite3.Error as exc:
            raise RuntimeError(f"تعذر تحميل الأيام المسجلة: {exc}") from exc


class DayLessonsBrowsePage(QWidget):
    def __init__(self, db, day_id=None, day_text=None, parent=None, on_back=None):
        super().__init__(parent)
        self.db = db
        self.day_id = day_id
        self.day_text = day_text or QDate.currentDate().toString("yyyy-MM-dd")
        self.on_back = on_back
        self.setObjectName("dayLessonsBrowsePage")
        self.setWindowTitle("عرض الأيام المسجلة")
        self.setLayoutDirection(Qt.RightToLeft)
        self._changing_date = False

        layout = QVBoxLayout(self)
        agenda_row = QHBoxLayout()
        agenda_row.addWidget(QLabel("اليوم:"))
        self.date_edit = QDateEdit()
        self.date_edit.setObjectName("dayLessonsDateEdit")
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setReadOnly(False)
        self.date_edit.lineEdit().setReadOnly(True)
        self.date_edit.setDisplayFormat("dddd، dd/MM/yyyy")
        self.date_edit.setLocale(QLocale(QLocale.Arabic, QLocale.Tunisia))
        self.calendar = DayBrowseCalendar(self.db, self.db.year_id, self)
        self.calendar.setLocale(QLocale(QLocale.Arabic, QLocale.Tunisia))
        self.date_edit.setCalendarWidget(self.calendar)
        initial_date = QDate.fromString(self.day_text, "yyyy-MM-dd")
        initial_date = initial_date if initial_date.isValid() else QDate.currentDate()
        initial_date = self._set_school_year_date_range(initial_date)
        self.date_edit.setDate(initial_date)
        self.calendar.setSelectedDate(initial_date)
        self.calendar.clicked.connect(self.on_calendar_date_clicked)
        agenda_row.addWidget(self.date_edit)
        self.day_status = QLabel()
        self.day_status.setObjectName("dayLessonsStatus")
        agenda_row.addWidget(self.day_status, 1)
        layout.addLayout(agenda_row)

        content_splitter = QSplitter(Qt.Horizontal)
        info_panel = QWidget()
        info_layout = QVBoxLayout(info_panel)
        info_layout.setContentsMargins(0, 0, 0, 0)
        self.school_info_card = QFrame()
        self.school_info_card.setObjectName("dayLessonsTeacherCard")
        self.school_info_card_layout = QVBoxLayout(self.school_info_card)
        self.school_info_card_layout.setContentsMargins(14, 14, 14, 14)
        self.school_info_card_layout.setSpacing(12)

        teacher_header = QFrame()
        teacher_header.setObjectName("dayLessonsTeacherHeader")
        teacher_header_layout = QHBoxLayout(teacher_header)
        teacher_header_layout.setContentsMargins(12, 12, 12, 12)
        teacher_icon = QLabel("♙")
        teacher_icon.setObjectName("dayLessonsTeacherIcon")
        teacher_header_layout.addWidget(teacher_icon)
        teacher_heading = QVBoxLayout()
        teacher_title = QLabel("بطاقة المعلم")
        teacher_title.setObjectName("dayLessonsTeacherTitle")
        self.teacher_subtitle = QLabel("بيانات السنة الدراسية المحددة")
        self.teacher_subtitle.setObjectName("dayLessonsTeacherSubtitle")
        self.teacher_subtitle.setWordWrap(True)
        teacher_heading.addWidget(teacher_title)
        teacher_heading.addWidget(self.teacher_subtitle)
        teacher_header_layout.addLayout(teacher_heading, 1)
        self.school_info_card_layout.addWidget(teacher_header)

        self.school_info_content = QWidget()
        self.school_info_content.setObjectName("dayLessonsTeacherDetails")
        self.school_info_content_layout = QVBoxLayout(
            self.school_info_content
        )
        self.school_info_content_layout.setContentsMargins(0, 0, 0, 0)
        self.school_info_content_layout.setSpacing(10)
        self.school_info_card_layout.addWidget(
            self.school_info_content, 1
        )

        self.school_info_scroll = QScrollArea()
        self.school_info_scroll.setObjectName("dayLessonsTeacherScroll")
        self.school_info_scroll.setWidgetResizable(True)
        self.school_info_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.school_info_scroll.setWidget(self.school_info_card)
        info_layout.addWidget(self.school_info_scroll, 1)

        self.lessons_scroll = QScrollArea()
        self.lessons_scroll.setObjectName("dayLessonsCardsScroll")
        self.lessons_scroll.setWidgetResizable(True)
        self.lessons_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        self.lessons_container = QWidget()
        self.lessons_layout = QVBoxLayout(self.lessons_container)
        self.lessons_layout.setContentsMargins(8, 8, 8, 8)
        self.lessons_layout.setSpacing(14)
        self.lessons_layout.addStretch(1)
        self.lessons_scroll.setWidget(self.lessons_container)
        content_splitter.addWidget(info_panel)
        content_splitter.addWidget(self.lessons_scroll)
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 3)
        content_splitter.setSizes([300, 950])
        layout.addWidget(content_splitter, 1)

        self.print_button = QPushButton("طباعة")
        self.print_button.setObjectName("dayLessonsExportButton")
        self.print_button.setProperty("actionType", "print")
        self.print_button.clicked.connect(self.print_day)
        self.export_pdf_button = QPushButton("تصدير PDF")
        self.export_pdf_button.setObjectName("dayLessonsExportButton")
        self.export_pdf_button.setProperty("actionType", "export")
        self.export_pdf_button.clicked.connect(self.export_pdf)
        self.preview_pdf_button = QPushButton("عرض")
        self.preview_pdf_button.setObjectName("dayLessonsPreviewButton")
        self.preview_pdf_button.setProperty("actionType", "browse")
        self.preview_pdf_button.clicked.connect(self.preview_pdf)
        self.export_json_button = QPushButton("تصدير JSON")
        self.export_json_button.setObjectName("dayLessonsExportButton")
        self.export_json_button.setProperty("actionType", "export")
        self.export_json_button.clicked.connect(self.export_json)
        back_button = QPushButton("التصرف في الدفتر")
        back_button.setProperty("actionType", "back")
        if self.on_back:
            back_button.clicked.connect(self.on_back)
        action_buttons = QHBoxLayout()
        action_buttons.addWidget(self.print_button)
        action_buttons.addWidget(self.preview_pdf_button)
        action_buttons.addWidget(self.export_pdf_button)
        action_buttons.addWidget(self.export_json_button)
        action_buttons.addWidget(back_button)
        info_layout.addLayout(action_buttons)
        info_layout.addStretch()

        self._update_school_info()
        if self.day_id and self.db.lessons(self.day_id):
            self.load_lesson_cards()
        else:
            self.day_id = None
            self._clear_lesson_cards()
            self._set_day_available(False)

    def set_day(self, day_id, day_text):
        self.day_id = day_id
        self.day_text = day_text or QDate.currentDate().toString("yyyy-MM-dd")
        selected_date = QDate.fromString(self.day_text, "yyyy-MM-dd")
        if not selected_date.isValid():
            selected_date = QDate.currentDate()
        selected_date = self._set_school_year_date_range(selected_date)
        self.day_text = selected_date.toString("yyyy-MM-dd")
        self.date_edit.setDate(selected_date)
        self.calendar.setSelectedDate(selected_date)
        self.calendar.setCurrentPage(selected_date.year(), selected_date.month())
        self.calendar.refresh_recorded_dates()
        if self.day_id and self.db.lessons(self.day_id):
            self.load_lesson_cards()
        else:
            self.day_id = None
            self._clear_lesson_cards()
            self._update_school_info()
            self._set_day_available(False)

    def _set_school_year_date_range(self, selected_date):
        school_info = self.db.school_info()
        if not school_info:
            raise ValueError("بيانات السنة الدراسية المحددة غير موجودة.")
        year_name = str(school_info[0] or "").strip()
        try:
            start_year_text, end_year_text = year_name.split("-", 1)
            start_year = int(start_year_text)
            end_year = int(end_year_text)
        except ValueError as error:
            raise ValueError(
                f"اسم السنة الدراسية غير صالح: {year_name}"
            ) from error
        if end_year != start_year + 1:
            raise ValueError(f"اسم السنة الدراسية غير صالح: {year_name}")

        first_day = QDate(start_year, 9, 1)
        last_day = QDate(end_year, 6, 30)
        self.date_edit.setDateRange(first_day, last_day)
        self.calendar.setDateRange(first_day, last_day)
        if selected_date < first_day:
            return first_day
        if selected_date > last_day:
            return last_day
        return selected_date

    def _update_school_info(self):
        school_info = self.db.school_info()
        values = school_info or ()
        self.school_info_card.setUpdatesEnabled(False)
        try:
            has_details = False
            while self.school_info_content_layout.count():
                item = self.school_info_content_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()

            if not values:
                self.teacher_subtitle.setText(
                    "بيانات السنة الدراسية المحددة"
                )
                empty_label = QLabel("لا توجد بيانات متوفرة لهذه السنة.")
                empty_label.setObjectName("dayLessonsTeacherEmpty")
                empty_label.setWordWrap(True)
                self.school_info_content_layout.addWidget(empty_label)
                return
            self.teacher_subtitle.setText(
                f"السنة الدراسية: {str(values[0] or '—').strip()}"
            )

            institution_fields = tuple(
                (label, str(value or "").strip())
                for label, value in (
                    ("المؤسسة", values[1]),
                    ("المعلم", values[2]),
                )
                if value is not None and str(value).strip()
            )
            if institution_fields:
                self.school_info_content_layout.addWidget(
                    self._teacher_info_value_card(
                        "المؤسسة والمعلم", institution_fields
                    )
                )
                has_details = True

            professional_fields = (
                ("المتفقد", values[3]),
                ("الرتبة", values[4]),
                ("المساعد البيداغوجي", values[5]),
                ("الشهادة العلمية", values[6]),
                ("تاريخ الانتداب", values[7]),
                ("تاريخ الترسيم", values[8]),
                ("آخر زيارة", values[9]),
                ("العدد البيداغوجي", values[10]),
                ("خلاصة تقرير الزيارة", values[11]),
                ("اللغة", values[12]),
            )
            professional_fields = tuple(
                (label, str(value or "").strip())
                for label, value in professional_fields
                if value is not None and str(value).strip()
            )
            if professional_fields:
                self.school_info_content_layout.addWidget(
                    self._teacher_info_value_card(
                        "المسار المهني", professional_fields
                    )
                )
                has_details = True

            personal_fields = (
                ("الهاتف", values[13]),
                ("البريد الإلكتروني", values[14]),
                ("الحالة العائلية", values[15]),
                ("عدد الأطفال", values[16]),
            )
            personal_fields = tuple(
                (label, str(value or "").strip())
                for label, value in personal_fields
                if value is not None and str(value).strip()
            )
            if personal_fields:
                self.school_info_content_layout.addWidget(
                    self._teacher_info_value_card(
                        "معلومات شخصية", personal_fields
                    )
                )
                has_details = True
            if not has_details:
                empty_label = QLabel(
                    "لا توجد تفاصيل مسجلة لهذه السنة الدراسية."
                )
                empty_label.setObjectName("dayLessonsTeacherEmpty")
                empty_label.setWordWrap(True)
                self.school_info_content_layout.addWidget(empty_label)
            self.school_info_content_layout.addStretch(1)
        finally:
            self.school_info_card.setUpdatesEnabled(True)

    @staticmethod
    def _teacher_info_value_card(title, fields):
        group = QFrame()
        group.setObjectName("dayLessonsTeacherGroup")
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(10, 10, 10, 10)
        group_layout.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("dayLessonsTeacherGroupTitle")
        group_layout.addWidget(heading)

        for label_text, value_text in fields:
            if not value_text:
                continue
            row = QFrame()
            row.setObjectName("dayLessonsTeacherField")
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(8, 6, 8, 6)
            row_layout.setSpacing(2)
            label = QLabel(label_text)
            label.setObjectName("dayLessonsTeacherFieldLabel")
            value = QLabel(value_text)
            value.setObjectName("dayLessonsTeacherFieldValue")
            value.setWordWrap(True)
            row_layout.addWidget(label)
            row_layout.addWidget(value)
            group_layout.addWidget(row)
        return group

    def _set_day_available(self, available):
        self.print_button.setEnabled(available)
        self.export_pdf_button.setEnabled(available)
        self.preview_pdf_button.setEnabled(available)
        self.export_json_button.setEnabled(available)
        self.day_status.setText(
            f"اليوم: {self.day_text}" if available else
            "لا توجد حصص لهذا اليوم. الأيام غير المسجلة غير متاحة للعرض."
        )

    def _restore_calendar_selection(self, displayed_date):
        previous_date = QDate.fromString(self.day_text, "yyyy-MM-dd")
        if previous_date.isValid():
            self.calendar.setSelectedDate(previous_date)
            self.date_edit.setDate(previous_date)
        self.calendar.setCurrentPage(displayed_date.year(), displayed_date.month())

    def _reopen_calendar_popup(self):
        popup = self.calendar.window()
        popup.show()
        popup.raise_()

    def on_calendar_date_clicked(self, selected_date):
        selected_day = selected_date.toString("yyyy-MM-dd")
        if selected_day not in self.calendar.recorded_dates:
            self._restore_calendar_selection(selected_date)
            self.day_status.setText("هذا اليوم غير مسجل. اختر يوماً مميزاً في الأجندة.")
            QTimer.singleShot(0, self._reopen_calendar_popup)
            return

        try:
            day_id = self.db.recorded_day_id(selected_day)
            if not day_id:
                self._restore_calendar_selection(selected_date)
                self.day_status.setText("هذا اليوم غير مسجل. اختر يوماً مميزاً في الأجندة.")
                QTimer.singleShot(0, self._reopen_calendar_popup)
                return
            self.day_id = day_id
            self.day_text = selected_day
            self.date_edit.setDate(selected_date)
            self.load_lesson_cards()
        except sqlite3.Error as exc:
            QMessageBox.critical(self, "تعذر تحميل اليوم", f"حدث خطأ أثناء تحميل الحصص:\n{exc}")

    def _clear_lesson_cards(self):
        while self.lessons_layout.count() > 1:
            item = self.lessons_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def load_lesson_cards(self):
        self._clear_lesson_cards()
        group_labels = {
            group_key: group["level_label"]
            for group_key, group in self.db.class_groups().items()
        }
        with self.db.connect() as con:
            lessons = con.execute("""
                SELECT h.id, h.hessa_number,
                    COALESCE(g.group_key, c.name), m.name, h.heure,
                    h.time, h.wehda_number, lm.name
                FROM hessa h JOIN classe c ON c.id=h.classe_id
                LEFT JOIN class_group g ON g.id=h.class_group_id
                LEFT JOIN majal m ON m.id=h.majal_id
                LEFT JOIN learning_mode lm ON lm.id=h.learning_mode_id
                WHERE h.day_data_id=?
                ORDER BY h.id
            """, (self.day_id,)).fetchall()

            for lesson in lessons:
                (
                    lesson_id, lesson_number, classe, majal, heure, duration,
                    unit_number, learning_mode
                ) = lesson
                if classe in group_labels:
                    classe = f"{classe} — {group_labels[classe]}"
                activities = con.execute("""
                    SELECT n.id, n.title, n.time, cl.name
                    FROM nachat n
                    LEFT JOIN classe cl ON cl.id=n.classe_id
                    WHERE n.hessa_id=?
                    ORDER BY n.id
                """, (lesson_id,)).fetchall()
                activity_rows = []
                for activity_id, title, activity_time, class_name in activities:
                    details = con.execute("""
                        SELECT key_name, value_text
                        FROM nachat_detail
                        WHERE nachat_id=?
                        ORDER BY id
                    """, (activity_id,)).fetchall()
                    activity_rows.append(
                        (
                            (
                                f"{title} — {class_name}"
                                if class_name else title
                            ),
                            activity_time,
                            details,
                        )
                    )
                self._add_lesson_card(
                    lesson_number, classe, majal, heure, duration,
                    unit_number, learning_mode, activity_rows,
                )

        self._update_school_info()
        self._set_day_available(bool(lessons))

    def _add_lesson_card(
        self, lesson_number, classe, majal, heure, duration, unit_number,
        learning_mode, activities,
    ):
        colors = (
            ("#2563EB", "#EFF6FF", "#BFDBFE"),
            ("#059669", "#ECFDF5", "#A7F3D0"),
            ("#D97706", "#FFFBEB", "#FDE68A"),
            ("#7C3AED", "#F5F3FF", "#DDD6FE"),
            ("#DB2777", "#FDF2F8", "#FBCFE8"),
            ("#0891B2", "#ECFEFF", "#A5F3FC"),
            ("#DC2626", "#FEF2F2", "#FECACA"),
            ("#4F46E5", "#EEF2FF", "#C7D2FE"),
        )
        accent, tint, border = colors[
            (self.lessons_layout.count() - 1) % len(colors)
        ]

        lesson_card = QFrame()
        lesson_card.setObjectName("dayLessonCard")
        lesson_card.setStyleSheet(
            "QFrame#dayLessonCard {"
            f"background: #FFFFFF; border: 1px solid {border};"
            f"border-right: 5px solid {accent}; border-radius: 12px;"
            "}"
        )
        card_layout = QVBoxLayout(lesson_card)
        card_layout.setContentsMargins(14, 12, 14, 14)
        card_layout.setSpacing(10)

        title = QLabel(str(classe or "—"))
        title.setStyleSheet(
            f"color: {accent}; font-size: 12pt; font-weight: 700;"
            "background: transparent; border: none;"
        )
        card_layout.addWidget(title)

        metadata = (
            ("المجال", majal),
            ("ساعة التدريس", heure),
            ("التوقيت", duration),
            ("الوحدة", unit_number),
            ("الحصة", lesson_number),
            ("نمط التعلم", learning_mode),
        )
        metadata_row = QGridLayout()
        metadata_row.setHorizontalSpacing(8)
        metadata_row.setVerticalSpacing(6)
        for index, (label, value) in enumerate(metadata):
            chip = QLabel(f"{label}: {value or '—'}")
            chip.setWordWrap(True)
            chip.setStyleSheet(
                f"color: #334155; background: {tint};"
                f"border: 1px solid {border}; border-radius: 7px;"
                "padding: 5px 8px;"
            )
            metadata_row.addWidget(chip, index // 3, index % 3)
        card_layout.addLayout(metadata_row)

        activities_title = QLabel(f"الأنشطة ({len(activities)})")
        activities_title.setStyleSheet(
            "color: #475569; font-weight: 700;"
            "background: transparent; border: none;"
        )
        card_layout.addWidget(activities_title)
        if not activities:
            empty_label = QLabel("لا توجد أنشطة مسجلة لهذه الحصة.")
            empty_label.setStyleSheet(
                "color: #64748B; padding: 6px;"
                "background: transparent; border: none;"
            )
            card_layout.addWidget(empty_label)
        for activity_title, activity_time, details in activities:
            activity_card = QFrame()
            activity_card.setObjectName("dayActivityCard")
            activity_card.setStyleSheet(
                "QFrame#dayActivityCard {"
                f"background: {tint}; border: 1px solid {border};"
                "border-radius: 9px;"
                "}"
            )
            activity_layout = QVBoxLayout(activity_card)
            activity_layout.setContentsMargins(10, 8, 10, 8)
            activity_layout.setSpacing(5)
            activity_heading = QLabel(
                f"{activity_title or 'نشاط دون عنوان'}"
                + (f"  ·  {activity_time}" if activity_time else "")
            )
            activity_heading.setWordWrap(True)
            activity_heading.setStyleSheet(
                f"color: {accent}; font-weight: 700;"
                "background: transparent; border: none;"
            )
            activity_layout.addWidget(activity_heading)
            for key_name, value_text in details:
                detail_label = QLabel(
                    f"{key_name or '—'}: {value_text or '—'}"
                )
                detail_label.setWordWrap(True)
                detail_label.setStyleSheet(
                    "color: #334155; background: transparent;"
                    "border: none;"
                )
                activity_layout.addWidget(detail_label)
            card_layout.addWidget(activity_card)

        self.lessons_layout.insertWidget(
            self.lessons_layout.count() - 1, lesson_card
        )

    def _day_payload(self):
        return self._build_day_payload(
            self.db.path, self.db.year_id, self.day_id
        )

    @staticmethod
    def _build_day_payload(database_path, school_year_id, day_id):
        with connect_database(database_path) as con:
            con.row_factory = sqlite3.Row
            day_row = con.execute("""
                SELECT id, date FROM day_data
                WHERE id=? AND ann_scolaire_id=?
            """, (day_id, school_year_id)).fetchone()
            if not day_row:
                raise ValueError("اليوم المحدد لم يعد موجوداً في قاعدة البيانات.")

            school_info = con.execute("""
                SELECT nom_annee, nom_ecole, nom_enseignant, nom_inspecteur, grade
                FROM ann_scolaire WHERE id=?
            """, (school_year_id,)).fetchone()
            year, school, teacher, inspector, grade = school_info or ("", "", "", "", "")
            payload = {
                "date": day_row["date"],
                "school_info": {
                    "nom_annee": year or "",
                    "nom_ecole": school or "",
                    "nom_enseignant": teacher or "",
                    "nom_inspecteur": inspector or "",
                    "grade": grade or "",
                },
                "lessons": [],
            }
            lessons = con.execute("""
                SELECT h.id, COALESCE(g.group_key, c.name) AS classe,
                    l.name AS classe_level, g.group_key AS class_group_key,
                    m.name AS majal, h.heure, h.time, h.wehda_number,
                    h.hessa_number, lm.name AS namat_taallom
                FROM hessa h
                JOIN classe c ON c.id=h.classe_id
                LEFT JOIN class_group g ON g.id=h.class_group_id
                JOIN level l ON l.id=c.level_id
                LEFT JOIN majal m ON m.id=h.majal_id
                LEFT JOIN learning_mode lm ON lm.id=h.learning_mode_id
                WHERE h.day_data_id=? ORDER BY h.id
            """, (day_id,)).fetchall()
            for lesson in lessons:
                lesson_data = {
                    "id": lesson["id"],
                    "classe": lesson["classe"] or "",
                    "classe_level": lesson["classe_level"] or "",
                    "class_group_key": lesson["class_group_key"],
                    "majal": lesson["majal"] or "",
                    "heure": lesson["heure"] or "",
                    "time": lesson["time"] or "",
                    "wehda_number": lesson["wehda_number"],
                    "hessa_number": lesson["hessa_number"],
                    "namat_taallom": lesson["namat_taallom"] or "",
                    "activities": [],
                }
                lesson_data["class_members"] = [
                    {
                        "classe": member["name"],
                        "level": member["level"],
                    }
                    for member in con.execute("""
                        SELECT cl.name, l.name AS level
                        FROM hessa_class_member hm
                        JOIN classe cl ON cl.id=hm.classe_id
                        JOIN level l ON l.id=cl.level_id
                        WHERE hm.hessa_id=? ORDER BY hm.position
                    """, (lesson["id"],)).fetchall()
                ]
                activities = con.execute("""
                    SELECT n.id, n.title, n.time, cl.name AS classe
                    FROM nachat n
                    LEFT JOIN classe cl ON cl.id=n.classe_id
                    WHERE n.hessa_id=? ORDER BY n.id
                """, (lesson["id"],)).fetchall()
                for activity in activities:
                    details = con.execute("""
                        SELECT key_name, value_text FROM nachat_detail
                        WHERE nachat_id=? ORDER BY id
                    """, (activity["id"],)).fetchall()
                    lesson_data["activities"].append({
                        "id": activity["id"],
                        "title": activity["title"] or "",
                        "time": activity["time"] or "",
                        "classe": activity["classe"],
                        "details": [
                            {"key_name": detail["key_name"] or "",
                             "value_text": detail["value_text"] or ""}
                            for detail in details
                        ],
                    })
                payload["lessons"].append(lesson_data)
        if not payload["lessons"]:
            raise ValueError("لا توجد حصص مسجلة لليوم المحدد.")
        return payload

    def export_json(self):
        import json

        default_path = (
            Path.home() / "Documents" / f"school_day_{self.day_text}.json"
        )
        file_path, _ = QFileDialog.getSaveFileName(
            self, "تصدير اليوم بصيغة JSON", str(default_path),
            "ملفات JSON (*.json)"
        )
        if not file_path:
            return
        if Path(file_path).suffix.lower() != ".json":
            file_path += ".json"

        database_path = self.db.path
        year_id = self.db.year_id
        day_id = self.day_id

        def export_day_json():
            payload = self._build_day_payload(
                database_path, year_id, day_id
            )
            with open(file_path, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, indent=4)

        start_background_task(
            self,
            export_day_json,
            lambda _result: QMessageBox.information(
                self, "نجاح التصدير", f"تم تصدير اليوم إلى:\n{file_path}"
            ),
            lambda error: QMessageBox.critical(
                self, "تعذر تصدير JSON",
                f"حدث خطأ أثناء تصدير اليوم:\n{error}",
            ),
        )

    def print_day(self, pdf_path=None):
        database_path = self.db.path
        year_id = self.db.year_id
        day_id = self.day_id

        def open_day_print(payload):
            try:
                payload = filter_print_details(
                    payload, load_print_detail_exclusions()
                )
                if pdf_path:
                    dialog = DayPdfPreviewDialog(payload, self)
                    dialog._export_pages_async(
                        pdf_path,
                        lambda exported_path: QMessageBox.information(
                            self,
                            "نجاح التصدير",
                            f"تم تصدير اليوم إلى:\n{exported_path}",
                        ),
                        lambda error: QMessageBox.critical(
                            self,
                            "تعذر تصدير PDF",
                            f"حدث خطأ أثناء تصدير اليوم:\n{error}",
                        ),
                    )
                    return

                preview_dialog = DayPdfPreviewDialog(payload, self)
                preview_dialog.print_pages()
            except (OSError, ValueError, RuntimeError) as exc:
                title = "تعذر تصدير PDF" if pdf_path else "تعذر طباعة اليوم"
                message = (
                    "حدث خطأ أثناء تصدير اليوم"
                    if pdf_path else "حدث خطأ أثناء الطباعة"
                )
                QMessageBox.critical(self, title, f"{message}:\n{exc}")

        start_background_task(
            self,
            lambda: self._build_day_payload(
                database_path, year_id, day_id
            ),
            open_day_print,
            lambda error: QMessageBox.critical(
                self,
                "تعذر تجهيز الطباعة",
                f"حدث خطأ أثناء تجهيز بيانات اليوم:\n{error}",
            ),
            keep_overlay_until_window=True,
        )

    def export_pdf(self):
        database_path = self.db.path
        year_id = self.db.year_id
        day_id = self.day_id

        def export_payload(payload):
            try:
                payload = filter_print_details(
                    payload, load_print_detail_exclusions()
                )
                dialog = DayPdfPreviewDialog(payload, self)
                dialog.export_pdf()
            except (OSError, ValueError, RuntimeError) as exc:
                QMessageBox.critical(
                    self, "تعذر تصدير PDF",
                    f"حدث خطأ أثناء تجهيز التصدير:\n{exc}",
                )

        start_background_task(
            self,
            lambda: self._build_day_payload(
                database_path, year_id, day_id
            ),
            export_payload,
            lambda error: QMessageBox.critical(
                self, "تعذر تصدير PDF",
                f"حدث خطأ أثناء تصدير اليوم:\n{error}",
            ),
        )

    def preview_pdf(self):
        database_path = self.db.path
        year_id = self.db.year_id
        day_id = self.day_id

        def open_preview(payload):
            try:
                payload = filter_print_details(
                    payload, load_print_detail_exclusions()
                )
                DayPdfPreviewDialog(payload, self).exec()
            except (OSError, ValueError, RuntimeError) as exc:
                QMessageBox.critical(
                    self, "تعذرت معاينة اليوم",
                    f"حدث خطأ أثناء تجهيز المعاينة:\n{exc}",
                )

        start_background_task(
            self,
            lambda: self._build_day_payload(
                database_path, year_id, day_id
            ),
            open_preview,
            lambda error: QMessageBox.critical(
                self, "تعذرت معاينة اليوم",
                f"حدث خطأ أثناء تجهيز المعاينة:\n{error}",
            ),
            keep_overlay_until_window=True,
        )


class OfficialProgramsWidget(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setLayoutDirection(Qt.RightToLeft)
        
        self.selected_level_id = None
        self.selected_majal_id = None
        self.selected_competence_matiere_id = None
        self.selected_moukawan_id = None
        self.selected_objectif_id = None
        self.program_tree_dialog = None

        main_layout = QVBoxLayout(self)
        filters_group = QGroupBox("الفلاتر واختيار البرامج")
        filters_layout = QHBoxLayout(filters_group)

        self.combo_level = QComboBox()
        self.combo_level.currentIndexChanged.connect(self.on_level_changed)

        self.combo_majal = QComboBox()
        self.combo_majal.currentIndexChanged.connect(self.on_majal_changed)

        self.combo_matiere = QComboBox()
        self.combo_matiere.currentIndexChanged.connect(self.on_matiere_changed)

        self.show_tree_button = QPushButton("عرض الشجرة")
        self.show_tree_button.setProperty("actionType", "browse")
        self.show_tree_button.clicked.connect(self.open_program_tree)

        filters_layout.addWidget(QLabel("المستوى:"))
        filters_layout.addWidget(self.combo_level, 1)
        filters_layout.addWidget(QLabel("المجال:"))
        filters_layout.addWidget(self.combo_majal, 1)
        filters_layout.addWidget(QLabel("المادة/الكفاية:"))
        filters_layout.addWidget(self.combo_matiere, 2)
        filters_layout.addWidget(self.show_tree_button)

        main_layout.addWidget(filters_group)

        competences_group = QGroupBox("الكفايات الأساسية (المجال والمادة)")
        comp_layout = QVBoxLayout(competences_group)

        majal_comp_layout = QHBoxLayout()
        self.txt_competence_majal = QLineEdit()
        self.txt_competence_majal.setReadOnly(True)
        btn_copy_majal = QPushButton("📋")
        btn_copy_majal.setFixedWidth(60)
        btn_copy_majal.clicked.connect(lambda: self.copy_text(self.txt_competence_majal))
        
        btn_edit_majal = QPushButton("✎")
        btn_edit_majal.setFixedWidth(60)
        btn_edit_majal.clicked.connect(self.edit_competence_majal)

        majal_comp_layout.addWidget(QLabel("كفاية المجال:"))
        majal_comp_layout.addWidget(self.txt_competence_majal, 1)
        majal_comp_layout.addWidget(btn_copy_majal)
        majal_comp_layout.addWidget(btn_edit_majal)
        comp_layout.addLayout(majal_comp_layout)

        matiere_comp_layout = QHBoxLayout()
        self.txt_competence_matiere = QLineEdit()
        self.txt_competence_matiere.setReadOnly(True)
        btn_copy_matiere = QPushButton("📋")
        btn_copy_matiere.setFixedWidth(60)
        btn_copy_matiere.clicked.connect(lambda: self.copy_text(self.txt_competence_matiere))

        btn_edit_matiere = QPushButton("✎")
        btn_edit_matiere.setFixedWidth(60)
        btn_edit_matiere.clicked.connect(self.edit_competence_matiere)

        matiere_comp_layout.addWidget(QLabel("كفاية المادة:"))
        matiere_comp_layout.addWidget(self.txt_competence_matiere, 1)
        matiere_comp_layout.addWidget(btn_copy_matiere)
        matiere_comp_layout.addWidget(btn_edit_matiere)
        comp_layout.addLayout(matiere_comp_layout)

        main_layout.addWidget(competences_group)

        tables_splitter = QSplitter(Qt.Horizontal)

        # 1. جدول مكونات الكفاية (مع التفاف النص وتوسيع الصفوف تلقائياً)
        moukawan_widget = QGroupBox("مكونات الكفاية")
        moukawan_layout = QVBoxLayout(moukawan_widget)
        moukawan_btns = QHBoxLayout()
        for text, slot, act_type in [
            ("＋ إضافة", self.add_moukawan, "add"), 
            ("✎ تعديل", self.edit_moukawan, "edit"), 
            ("－ حذف", self.delete_moukawan, "delete"), 
            ("📋 نسخ", self.copy_moukawan, "copy_paste"), 
            ("📌 لصق", self.paste_moukawan, "copy_paste")
        ]:
            b = QPushButton(text)
            b.setMinimumWidth(38)
            b.setFixedHeight(28)
            b.setProperty("actionType", act_type)
            b.clicked.connect(slot)
            moukawan_btns.addWidget(b)
        moukawan_btns.addStretch()
        
        moukawan_layout.addLayout(moukawan_btns)
        moukawan_layout.addSpacing(8)
        self.tbl_moukawan = self.create_clean_table(["مكون الكفاية"])
        self.tbl_moukawan.setWordWrap(True)
        self.tbl_moukawan.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tbl_moukawan.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_moukawan.itemSelectionChanged.connect(self.on_moukawan_selected)
        moukawan_layout.addWidget(self.tbl_moukawan)
        tables_splitter.addWidget(moukawan_widget)

        # 2. جدول الأهداف المميزة (مع التفاف النص وتوسيع الصفوف تلقائياً)
        objectif_widget = QGroupBox("الأهداف المميزة")
        objectif_layout = QVBoxLayout(objectif_widget)
        objectif_btns = QHBoxLayout()
        for text, slot, act_type in [
            ("＋ إضافة", self.add_objectif, "add"), 
            ("✎ تعديل", self.edit_objectif, "edit"), 
            ("－ حذف", self.delete_objectif, "delete"), 
            ("📋 نسخ", self.copy_objectif, "copy_paste"), 
            ("📌 لصق", self.paste_objectif, "copy_paste")
        ]:
            b = QPushButton(text)
            b.setMinimumWidth(38)
            b.setFixedHeight(28)
            b.setProperty("actionType", act_type)
            b.clicked.connect(slot)
            objectif_btns.addWidget(b)
        objectif_btns.addStretch()
        
        objectif_layout.addLayout(objectif_btns)
        objectif_layout.addSpacing(8)
        self.tbl_objectif = self.create_clean_table(["الهدف المميز"])
        self.tbl_objectif.setWordWrap(True)
        self.tbl_objectif.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tbl_objectif.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_objectif.itemSelectionChanged.connect(self.on_objectif_selected)
        objectif_layout.addWidget(self.tbl_objectif)
        tables_splitter.addWidget(objectif_widget)

        # 3. جدول المحتويات (مع التفاف النص وتوسيع الصفوف تلقائياً)
        content_widget = QGroupBox("المحتويات")
        content_layout = QVBoxLayout(content_widget)
        content_btns = QHBoxLayout()
        for text, slot, act_type in [
            ("＋ إضافة", self.add_content, "add"), 
            ("✎ تعديل", self.edit_content, "edit"), 
            ("－ حذف", self.delete_content, "delete"), 
            ("📋 نسخ", self.copy_content, "copy_paste"), 
            ("📌 لصق", self.paste_content, "copy_paste")
        ]:
            b = QPushButton(text)
            b.setMinimumWidth(38)
            b.setFixedHeight(28)
            b.setProperty("actionType", act_type)
            b.clicked.connect(slot)
            content_btns.addWidget(b)
        content_btns.addStretch()
        
        content_layout.addLayout(content_btns)
        content_layout.addSpacing(8)
        self.tbl_content = self.create_clean_table(["المحتوى"])
        self.tbl_content.setWordWrap(True)
        self.tbl_content.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tbl_content.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        content_layout.addWidget(self.tbl_content)
        tables_splitter.addWidget(content_widget)

        tables_splitter.setSizes([330, 330, 330])
        main_layout.addWidget(tables_splitter, 1)

        self.load_levels_and_majals()

    def multi_input_dialog(self, title, fields_info):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(600)
        layout = QVBoxLayout(dlg)
        form = QFormLayout()
        
        text_editors = []
        for label_text, default_text in fields_info:
            txt = QTextEdit()
            txt.setAcceptRichText(False)
            txt.setPlainText(default_text)
            
            font_metrics = txt.fontMetrics()
            txt.setFixedHeight(font_metrics.lineSpacing() * 5 + 16)
            
            form.addRow(label_text, txt)
            text_editors.append(txt)
            
        layout.addLayout(form)
        
        btns = QDialogButtonBox()
        btns.addButton("موافق", QDialogButtonBox.AcceptRole)
        btns.addButton("إلغاء", QDialogButtonBox.RejectRole)
        set_dialog_button_action_types(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        
        if dlg.exec() == QDialog.Accepted:
            results = [editor.toPlainText().strip() for editor in text_editors]
            return results, True
        return [], False

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

            self.update_matieres_combo()
        except Exception as e:
            print(f"Error loading filters: {e}")

    def on_level_changed(self):
        self.selected_level_id = self.combo_level.currentData()
        self.update_matieres_combo()

    def on_majal_changed(self):
        self.selected_majal_id = self.combo_majal.currentData()
        self.update_matieres_combo()

    def update_matieres_combo(self):
        self.combo_matiere.blockSignals(True)
        self.combo_matiere.clear()
        self.selected_level_id = self.combo_level.currentData()
        self.selected_majal_id = self.combo_majal.currentData()
        
        if not self.selected_level_id or not self.selected_majal_id:
            self.combo_matiere.blockSignals(False)
            return

        try:
            with self.db.connect() as con:
                cursor = con.cursor()
                query = """
                    cm.id, cm.matiere, cm.competence, cm.id_competence_majal,
                    cmajal.competence AS majal_competence
                    FROM competence_matiere cm
                    JOIN competence_majal cmajal ON cm.id_competence_majal = cmajal.id
                    WHERE cmajal.id_majal = ? AND cmajal.id_level = ?
                """
                params = [self.selected_majal_id, self.selected_level_id]

                for row in cursor.execute(f"SELECT {query}", params).fetchall():
                    display_text = f"{row[1]} — {row[2]}"
                    self.combo_matiere.addItem(display_text, {
                        "competence_matiere_id": row[0],
                        "matiere": row[1],
                        "competence_matiere": row[2],
                        "id_competence_majal": row[3],
                        "majal_competence": row[4]
                    })
        except Exception as e:
            print(f"Error updating matieres: {e}")
        
        self.combo_matiere.blockSignals(False)
        self.on_matiere_changed()

    def open_program_tree(self):
        level_id = self.combo_level.currentData()
        if not level_id:
            QMessageBox.information(self, "اختيار المستوى", "يرجى اختيار المستوى الدراسي أولاً.")
            return

        if self.program_tree_dialog is None:
            try:
                self.program_tree_dialog = OfficialProgramsTreeWidget(
                    self.db,
                    level_id,
                    self.combo_level.currentText(),
                    self.combo_majal.currentData(),
                    self.combo_majal.currentText(),
                    self
                )
            except sqlite3.Error as exc:
                QMessageBox.critical(
                    self, "تعذر عرض البرامج",
                    f"تعذر تحميل شجرة البرامج الرسمية:\n{exc}"
                )
                return
        else:
            self.program_tree_dialog.level_id = level_id
            self.program_tree_dialog.level_name = self.combo_level.currentText()
            self.program_tree_dialog.majal_id = self.combo_majal.currentData()
            self.program_tree_dialog.majal_name = self.combo_majal.currentText()
            try:
                self.program_tree_dialog.refresh_tree()
            except sqlite3.Error as exc:
                QMessageBox.critical(self, "تعذر عرض البرامج", f"تعذر تحميل شجرة البرامج الرسمية:\n{exc}")
                return

        self.program_tree_dialog.setWindowModality(Qt.NonModal)
        self.program_tree_dialog.showMaximized()
        self.program_tree_dialog.raise_()
        self.program_tree_dialog.activateWindow()

    def on_matiere_changed(self):
        data = self.combo_matiere.currentData()
        if not data:
            self.txt_competence_majal.clear()
            self.txt_competence_matiere.clear()
            self.selected_competence_matiere_id = None
            self.tbl_moukawan.setRowCount(0)
            self.tbl_objectif.setRowCount(0)
            self.tbl_content.setRowCount(0)
            return

        self.selected_competence_matiere_id = data["competence_matiere_id"]
        self.txt_competence_majal.setText(data["majal_competence"])
        self.txt_competence_matiere.setText(data["competence_matiere"])
        self.load_moukawan_data()

    def load_moukawan_data(self):
        self.tbl_moukawan.setRowCount(0)
        self.tbl_objectif.setRowCount(0)
        self.tbl_content.setRowCount(0)
        if not self.selected_competence_matiere_id:
            return

        try:
            with self.db.connect() as con:
                for row in con.execute("SELECT id, competence FROM moukawan_kifaya WHERE id_competence_matiere = ?", (self.selected_competence_matiere_id,)).fetchall():
                    r = self.tbl_moukawan.rowCount()
                    self.tbl_moukawan.insertRow(r)
                    item_comp = QTableWidgetItem(row[1])
                    item_comp.setData(Qt.UserRole, row[0])
                    self.tbl_moukawan.setItem(r, 0, item_comp)
            
            # تكييف الارتفاع تلقائياً لعرض النصوص الطويلة بالكامل
            self.tbl_moukawan.resizeRowsToContents()
        except Exception as e:
            print(f"Error loading moukawan: {e}")

    def on_moukawan_selected(self):
        row = self.tbl_moukawan.currentRow()
        if row < 0:
            self.selected_moukawan_id = None
            self.tbl_objectif.setRowCount(0)
            return
        item = self.tbl_moukawan.item(row, 0)
        self.selected_moukawan_id = item.data(Qt.UserRole) if item else None
        self.load_objectif_data()

    def load_objectif_data(self):
        self.tbl_objectif.setRowCount(0)
        self.tbl_content.setRowCount(0)
        if not self.selected_moukawan_id:
            return

        try:
            with self.db.connect() as con:
                for row in con.execute("SELECT id, objectif_specifique, description FROM objectif_specifique WHERE id_moukawa_kifaya = ?", (self.selected_moukawan_id,)).fetchall():
                    r = self.tbl_objectif.rowCount()
                    self.tbl_objectif.insertRow(r)
                    item_obj = QTableWidgetItem(row[1])
                    item_obj.setData(Qt.UserRole, row[0])
                    item_obj.setData(Qt.UserRole + 1, row[2] or "")
                    self.tbl_objectif.setItem(r, 0, item_obj)
            
            self.tbl_objectif.resizeRowsToContents()
        except Exception as e:
            print(f"Error loading objectifs: {e}")

    def on_objectif_selected(self):
        row = self.tbl_objectif.currentRow()
        if row < 0:
            self.selected_objectif_id = None
            self.tbl_content.setRowCount(0)
            return
        item = self.tbl_objectif.item(row, 0)
        self.selected_objectif_id = item.data(Qt.UserRole) if item else None
        self.load_content_data()

    def load_content_data(self):
        self.tbl_content.setRowCount(0)
        if not self.selected_objectif_id:
            return

        try:
            with self.db.connect() as con:
                for row in con.execute("SELECT id, content, description FROM content WHERE id_objectif_specifique = ?", (self.selected_objectif_id,)).fetchall():
                    r = self.tbl_content.rowCount()
                    self.tbl_content.insertRow(r)
                    item_cnt = QTableWidgetItem(row[1])
                    item_cnt.setData(Qt.UserRole, row[0])
                    item_cnt.setData(Qt.UserRole + 1, row[2] or "")
                    self.tbl_content.setItem(r, 0, item_cnt)
            
            # تكييف الارتفاع تلقائياً لعرض النصوص الطويلة بالكامل
            self.tbl_content.resizeRowsToContents()
        except Exception as e:
            print(f"Error loading contents: {e}")

    def copy_text(self, line_edit):
        line_edit.selectAll()
        line_edit.copy()

    def edit_competence_majal(self):
        data = self.combo_matiere.currentData()
        if not data:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد المادة أولاً.")
            return
        new_val, ok = self.input_dialog("تعديل كفاية المجال", "أدخل النص الجديد:", self.txt_competence_majal.text())
        if ok and new_val:
            try:
                with self.db.connect() as con:
                    con.execute("UPDATE competence_majal SET competence = ? WHERE id = ?", (new_val, data["id_competence_majal"]))
                    con.commit()
                self.update_matieres_combo()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر التحديث: {e}")

    def edit_competence_matiere(self):
        data = self.combo_matiere.currentData()
        if not data:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد المادة أولاً.")
            return
        new_val, ok = self.input_dialog("تعديل كفاية المادة", "أدخل النص الجديد:", self.txt_competence_matiere.text())
        if ok and new_val:
            try:
                with self.db.connect() as con:
                    con.execute("UPDATE competence_matiere SET competence = ? WHERE id = ?", (new_val, data["competence_matiere_id"]))
                    con.commit()
                self.update_matieres_combo()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر التحديث: {e}")

    def add_moukawan(self):
        if not self.selected_competence_matiere_id:
            QMessageBox.warning(self, "تنبيه", "يرجى اختيار المادة أولاً.")
            return
        val, ok = self.input_dialog("إضافة مكون كفاية", "أدخل مكون الكفاية:")
        if ok and val:
            try:
                with self.db.connect() as con:
                    con.execute("INSERT INTO moukawan_kifaya (competence, id_competence_matiere) VALUES (?, ?)", (val, self.selected_competence_matiere_id))
                    con.commit()
                self.load_moukawan_data()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر الإضافة: {e}")

    def edit_moukawan(self):
        row = self.tbl_moukawan.currentRow()
        if row < 0:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد مكون كفاية للتعديل.")
            return
        item = self.tbl_moukawan.item(row, 0)
        rec_id = item.data(Qt.UserRole)
        val, ok = self.input_dialog("تعديل مكون الكفاية", "أدخل النص الجديد:", item.text())
        if ok and val:
            try:
                with self.db.connect() as con:
                    con.execute("UPDATE moukawan_kifaya SET competence = ? WHERE id = ?", (val, rec_id))
                    con.commit()
                self.load_moukawan_data()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر التعديل: {e}")

    def delete_moukawan(self):
        row = self.tbl_moukawan.currentRow()
        if row < 0:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد مكون كفاية للحذف.")
            return
        item = self.tbl_moukawan.item(row, 0)
        rec_id = item.data(Qt.UserRole)
        if QMessageBox.question(self, "تأكيد", "هل أنت متأكد من الحذف؟") == QMessageBox.Yes:
            try:
                with self.db.connect() as con:
                    con.execute("DELETE FROM moukawan_kifaya WHERE id = ?", (rec_id,))
                    con.commit()
                self.load_moukawan_data()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر الحذف: {e}")

    def copy_moukawan(self):
        row = self.tbl_moukawan.currentRow()
        if row >= 0:
            item = self.tbl_moukawan.item(row, 0)
            self.copied_moukawan_text = item.text()
            moukawan_id = item.data(Qt.UserRole)
            self.copied_moukawan_data = {
                "competence": self.copied_moukawan_text,
                "objectifs": [],
            }
            with self.db.connect() as con:
                objectifs = con.execute(
                    """SELECT id, objectif_specifique, description
                       FROM objectif_specifique
                       WHERE id_moukawa_kifaya = ?
                       ORDER BY id""",
                    (moukawan_id,),
                ).fetchall()
                for objectif_id, objectif_text, objectif_description in objectifs:
                    contents = con.execute(
                        """SELECT content, description FROM content
                           WHERE id_objectif_specifique = ? ORDER BY id""",
                        (objectif_id,),
                    ).fetchall()
                    self.copied_moukawan_data["objectifs"].append({
                        "objectif_specifique": objectif_text,
                        "description": objectif_description or "",
                        "contents": [
                            {"content": content_text, "description": description or ""}
                            for content_text, description in contents
                        ],
                    })
            QApplication.clipboard().setText(self.copied_moukawan_text)

    def paste_moukawan(self):
        if hasattr(self, 'copied_moukawan_text') and self.selected_competence_matiere_id:
            try:
                with self.db.connect() as con:
                    cursor = con.execute(
                        "INSERT INTO moukawan_kifaya (competence, id_competence_matiere) VALUES (?, ?)",
                        (self.copied_moukawan_text, self.selected_competence_matiere_id),
                    )
                    new_moukawan_id = cursor.lastrowid
                    copied_data = getattr(self, "copied_moukawan_data", None)
                    if copied_data:
                        for objectif in copied_data["objectifs"]:
                            objectif_cursor = con.execute(
                                """INSERT INTO objectif_specifique
                                   (objectif_specifique, id_moukawa_kifaya, description)
                                   VALUES (?, ?, ?)""",
                                (
                                    objectif["objectif_specifique"],
                                    new_moukawan_id,
                                    objectif["description"],
                                ),
                            )
                            new_objectif_id = objectif_cursor.lastrowid
                            for content in objectif["contents"]:
                                con.execute(
                                    """INSERT INTO content
                                       (content, id_objectif_specifique, description)
                                       VALUES (?, ?, ?)""",
                                    (
                                        content["content"],
                                        new_objectif_id,
                                        content["description"],
                                    ),
                                )
                    con.commit()
                self.load_moukawan_data()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر اللصق: {e}")

    def add_objectif(self):
        if not self.selected_moukawan_id:
            QMessageBox.warning(self, "تنبيه", "يرجى اختيار مكون كفاية أولاً.")
            return
            
        fields = [
            ("الهدف المميز:", ""),
            ("الملاحظات:", "")
        ]
        vals, ok = self.multi_input_dialog("إضافة هدف مميز جديد", fields)
        
        if ok and vals[0]:
            val = vals[0]
            desc = vals[1]
            try:
                with self.db.connect() as con:
                    con.execute(
                        "INSERT INTO objectif_specifique (objectif_specifique, id_moukawa_kifaya, description) VALUES (?, ?, ?)", 
                        (val, self.selected_moukawan_id, desc)
                    )
                    con.commit()
                self.load_objectif_data()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر الإضافة: {e}")

    def edit_objectif(self):
        row = self.tbl_objectif.currentRow()
        if row < 0:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد هدف مميز للتعديل.")
            return
        item_obj = self.tbl_objectif.item(row, 0)
        rec_id = item_obj.data(Qt.UserRole)
        current_text = item_obj.text()
        current_desc = item_obj.data(Qt.UserRole + 1) or ""
        
        fields = [
            ("تعديل الهدف المميز:", current_text),
            ("تعديل الملاحظات:", current_desc)
        ]
        vals, ok = self.multi_input_dialog("تعديل الهدف المميز", fields)
        
        if ok and vals[0]:
            val = vals[0]
            desc = vals[1]
            try:
                with self.db.connect() as con:
                    con.execute(
                        "UPDATE objectif_specifique SET objectif_specifique = ?, description = ? WHERE id = ?", 
                        (val, desc, rec_id)
                    )
                    con.commit()
                self.load_objectif_data()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر التعديل: {e}")

    def delete_objectif(self):
        row = self.tbl_objectif.currentRow()
        if row < 0:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد هدف مميز للحذف.")
            return
        item = self.tbl_objectif.item(row, 0)
        rec_id = item.data(Qt.UserRole)
        if QMessageBox.question(self, "تأكيد", "هل أنت متأكد من الحذف؟") == QMessageBox.Yes:
            try:
                with self.db.connect() as con:
                    con.execute("DELETE FROM objectif_specifique WHERE id = ?", (rec_id,))
                    con.commit()
                self.load_objectif_data()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر الحذف: {e}")

    def copy_objectif(self):
        row = self.tbl_objectif.currentRow()
        if row >= 0:
            item = self.tbl_objectif.item(row, 0)
            self.copied_obj_text = item.text()
            self.copied_obj_desc = item.data(Qt.UserRole + 1) or ""
            objectif_id = item.data(Qt.UserRole)
            with self.db.connect() as con:
                contents = con.execute(
                    """SELECT content, description FROM content
                       WHERE id_objectif_specifique = ? ORDER BY id""",
                    (objectif_id,),
                ).fetchall()
            self.copied_obj_contents = [
                {"content": content_text, "description": description or ""}
                for content_text, description in contents
            ]
            QApplication.clipboard().setText(self.copied_obj_text)

    def paste_objectif(self):
        if hasattr(self, 'copied_obj_text') and self.selected_moukawan_id:
            try:
                with self.db.connect() as con:
                    cursor = con.execute(
                        """INSERT INTO objectif_specifique
                           (objectif_specifique, id_moukawa_kifaya, description)
                           VALUES (?, ?, ?)""",
                        (self.copied_obj_text, self.selected_moukawan_id, self.copied_obj_desc),
                    )
                    new_objectif_id = cursor.lastrowid
                    for content in getattr(self, "copied_obj_contents", []):
                        con.execute(
                            """INSERT INTO content
                               (content, id_objectif_specifique, description)
                               VALUES (?, ?, ?)""",
                            (
                                content["content"],
                                new_objectif_id,
                                content["description"],
                            ),
                        )
                    con.commit()
                self.load_objectif_data()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر اللصق: {e}")

    def add_content(self):
        if not self.selected_objectif_id:
            QMessageBox.warning(self, "تنبيه", "يرجى اختيار هدف مميز أولاً.")
            return
            
        fields = [
            ("المحتوى:", ""),
            ("الملاحظات (اختياري):", "")
        ]
        vals, ok = self.multi_input_dialog("إضافة محتوى جديد", fields)
        
        if ok and vals[0]:
            val = vals[0]
            desc = vals[1]
            try:
                with self.db.connect() as con:
                    con.execute(
                        "INSERT INTO content (content, id_objectif_specifique, description) VALUES (?, ?, ?)", 
                        (val, self.selected_objectif_id, desc)
                    )
                    con.commit()
                self.load_content_data()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر الإضافة: {e}")

    def edit_content(self):
        row = self.tbl_content.currentRow()
        if row < 0:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد محتوى للتعديل.")
            return
        item_cnt = self.tbl_content.item(row, 0)
        rec_id = item_cnt.data(Qt.UserRole)
        current_text = item_cnt.text()
        current_desc = item_cnt.data(Qt.UserRole + 1) or ""
        
        fields = [
            ("تعديل المحتوى:", current_text),
            ("تعديل الملاحظات:", current_desc)
        ]
        vals, ok = self.multi_input_dialog("تعديل المحتوى", fields)
        
        if ok and vals[0]:
            val = vals[0]
            desc = vals[1]
            try:
                with self.db.connect() as con:
                    con.execute(
                        "UPDATE content SET content = ?, description = ? WHERE id = ?", 
                        (val, desc, rec_id)
                    )
                    con.commit()
                self.load_content_data()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر التعديل: {e}")

    def delete_content(self):
        row = self.tbl_content.currentRow()
        if row < 0:
            QMessageBox.warning(self, "تنبيه", "يرجى تحديد محتوى للحذف.")
            return
        item = self.tbl_content.item(row, 0)
        rec_id = item.data(Qt.UserRole)
        if QMessageBox.question(self, "تأكيد", "هل أنت متأكد من الحذف؟") == QMessageBox.Yes:
            try:
                with self.db.connect() as con:
                    con.execute("DELETE FROM content WHERE id = ?", (rec_id,))
                    con.commit()
                self.load_content_data()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر الحذف: {e}")

    def copy_content(self):
        row = self.tbl_content.currentRow()
        if row >= 0:
            item = self.tbl_content.item(row, 0)
            self.copied_cnt_text = item.text()
            self.copied_cnt_desc = item.data(Qt.UserRole + 1) or ""
            self.copied_cnt_data = {
                "content": self.copied_cnt_text,
                "description": self.copied_cnt_desc,
            }
            QApplication.clipboard().setText(self.copied_cnt_text)

    def paste_content(self):
        if hasattr(self, 'copied_cnt_text') and self.selected_objectif_id:
            try:
                with self.db.connect() as con:
                    copied_data = getattr(self, "copied_cnt_data", None) or {
                        "content": self.copied_cnt_text,
                        "description": self.copied_cnt_desc,
                    }
                    con.execute(
                        """INSERT INTO content
                           (content, id_objectif_specifique, description)
                           VALUES (?, ?, ?)""",
                        (
                            copied_data["content"],
                            self.selected_objectif_id,
                            copied_data["description"],
                        ),
                    )
                    con.commit()
                self.load_content_data()
            except Exception as e:
                QMessageBox.critical(self, "خطأ", f"تعذر اللصق: {e}")

    def input_dialog(self, title, label_text, default_text=""):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(450)
        layout = QVBoxLayout(dlg)
        form = QFormLayout()
        
        txt = QTextEdit() if len(default_text) > 30 or "\n" in default_text or "المجال" in title or "المادة" in title else QLineEdit()
        if isinstance(txt, QTextEdit):
            txt.setPlainText(default_text)
            txt.setMinimumHeight(100)
        else:
            txt.setText(default_text)
            
        form.addRow(label_text, txt)
        layout.addLayout(form)
        
        btns = QDialogButtonBox()
        btns.addButton("موافق", QDialogButtonBox.AcceptRole)
        btns.addButton("إلغاء", QDialogButtonBox.RejectRole)
        set_dialog_button_action_types(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        
        if dlg.exec() == QDialog.Accepted:
            val = txt.toPlainText().strip() if isinstance(txt, QTextEdit) else txt.text().strip()
            return val, True
        return "", False
