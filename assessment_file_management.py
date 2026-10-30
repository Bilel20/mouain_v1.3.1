"""Assessment and follow-up file setup."""
import sqlite3

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app_config import PLANS_NAMES, set_dialog_button_action_types
from database import (
    ASSESSMENT_FILE_TYPES,
    ASSESSMENT_TYPE_END_OF_SEMESTER,
    ASSESSMENT_TYPE_END_OF_TRIMESTER,
    ASSESSMENT_TYPE_END_OF_UNIT,
    PLAN_ACTIVITY_ALIASES,
)


_PERIODS_BY_TYPE = {
    ASSESSMENT_TYPE_END_OF_UNIT: tuple(str(number) for number in range(1, 9)),
    ASSESSMENT_TYPE_END_OF_TRIMESTER: ("الأول", "الثاني", "الثالث"),
    ASSESSMENT_TYPE_END_OF_SEMESTER: ("الأول", "الثاني"),
}


def _normalized_activity_name(name):
    normalized = str(name or "").strip().casefold()
    for canonical, aliases in PLAN_ACTIVITY_ALIASES.items():
        if normalized == canonical.casefold() or normalized in {
            alias.casefold() for alias in aliases
        }:
            return canonical.casefold()
    return normalized


def _activity_names_for_subjects(subjects):
    names = []
    for subject in subjects:
        activity = str(subject or "").strip()
        if activity and activity not in names:
            names.append(activity)
    return names


def _domain_for_activity(activity_name):
    details = PLANS_NAMES.get(str(activity_name or "").strip(), {})
    return str(details.get("المجال") or "").strip()


def _plan_activity_names(assigned_activity):
    details = PLANS_NAMES.get(str(assigned_activity or "").strip(), {})
    activities = details.get("الأنشطة")
    if not activities:
        return (str(assigned_activity or "").strip(),)
    return tuple(str(activity).strip() for activity in activities if activity)


def _assessment_plan_label(plan):
    plan_number = plan[11] if len(plan) > 11 else None
    plan_number = (
        str(plan_number).strip()
        if plan_number is not None and str(plan_number).strip()
        else "غير محدد"
    )
    class_name = str(plan[3] or "").strip() if len(plan) > 3 else ""
    shared_group_key = (
        str(plan[19] or "").strip() if len(plan) > 19 else ""
    )
    if shared_group_key:
        class_name = f"{shared_group_key} (مشترك)"
    title = str(plan[1] or "").strip()
    return f"عدد {plan_number} · {class_name} · {title}"


class AssessmentFileCard(QFrame):
    selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._floating_actions = []
        self.file_id = None

    def enable_selection(self, file_id):
        self.file_id = int(file_id)
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)

    def set_selected(self, selected):
        self.setProperty("selected", bool(selected))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and self.file_id is not None
        ):
            self.selected.emit(self.file_id)
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.file_id is not None:
                self.selected.emit(self.file_id)
        super().mousePressEvent(event)

    def add_floating_action(self, button):
        self._floating_actions.append(button)
        self._position_floating_actions()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_floating_actions()

    def _position_floating_actions(self):
        spacing = 2
        total_width = sum(button.width() for button in self._floating_actions)
        total_width += spacing * max(0, len(self._floating_actions) - 1)
        x = (self.width() - total_width) // 2
        for button in self._floating_actions:
            button.move(x, 8)
            button.raise_()
            x += button.width() + spacing


class NewAssessmentFileDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("ملف تقييم ومتابعة جديد")
        self.setObjectName("newAssessmentFileDialog")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        available_geometry = self.screen().availableGeometry()
        minimum_width = min(600, available_geometry.width())
        minimum_height = min(440, available_geometry.height())
        self.setMinimumSize(minimum_width, minimum_height)
        self.resize(
            min(760, max(minimum_width, int(available_geometry.width() * 0.72))),
            min(640, max(minimum_height, int(available_geometry.height() * 0.82))),
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        scroll_area = QScrollArea(self)
        scroll_area.setObjectName("assessmentFileFormScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 2, 8, 2)
        content_layout.setSpacing(12)

        selection_group = QGroupBox("بيانات القسم والنشاط")
        selection_layout = QHBoxLayout(selection_group)
        selection_layout.setContentsMargins(14, 18, 14, 14)
        selection_layout.setSpacing(14)

        self.class_combo = QComboBox()
        self.class_combo.setObjectName("assessmentClassCombo")
        self.class_combo.addItem("اختر القسم", None)
        self._center_combo(self.class_combo)
        self._load_class_options()

        self.activity_combo = QComboBox()
        self.activity_combo.setObjectName("assessmentActivityCombo")
        self.activity_combo.addItem("اختر النشاط", "")
        self._center_combo(self.activity_combo)
        self.activity_combo.setEnabled(False)
        selection_layout.addWidget(
            self._create_selector_field("القسم:", self.class_combo), 1
        )
        selection_layout.addWidget(
            self._create_selector_field("النشاط:", self.activity_combo), 1
        )
        content_layout.addWidget(selection_group)

        self.activity_selection_group = QGroupBox(
            "الأنشطة المطلوب تقييمها"
        )
        self.activity_selection_group.setObjectName(
            "assessmentActivitySelectionGroup"
        )
        self.activity_selection_layout = QGridLayout(
            self.activity_selection_group
        )
        self.activity_selection_layout.setContentsMargins(14, 14, 14, 14)
        self.activity_selection_layout.setHorizontalSpacing(14)
        self.activity_selection_layout.setVerticalSpacing(8)
        self.activity_checkboxes = {}
        self.activity_selection_group.hide()
        content_layout.addWidget(self.activity_selection_group)

        assessment_group = QGroupBox("بيانات التقييم")
        assessment_layout = QVBoxLayout(assessment_group)
        assessment_layout.setContentsMargins(14, 18, 14, 14)
        assessment_layout.setSpacing(12)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignHCenter
            | Qt.AlignmentFlag.AlignVCenter
        )
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self.domain_edit = QLineEdit()
        self.domain_edit.setObjectName("assessmentDomainEdit")
        self.domain_edit.setReadOnly(True)
        self.domain_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        form.addRow(self._centered_label("المجال:"), self.domain_edit)

        self.competence_edit = QPlainTextEdit()
        self.competence_edit.setObjectName("assessmentCompetenceEdit")
        self.competence_edit.setMinimumHeight(92)
        self.competence_edit.setMaximumHeight(140)
        text_option = self.competence_edit.document().defaultTextOption()
        text_option.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.competence_edit.document().setDefaultTextOption(text_option)
        self.competence_edit.setPlaceholderText(
            "تُملأ الكفاية آليًا عند توفرها، ويمكن إدخالها يدويًا."
        )
        form.addRow(
            self._centered_label("الكفاية:"), self.competence_edit
        )
        assessment_layout.addLayout(form)

        self.plan_combo = QComboBox()
        self.plan_combo.setObjectName("assessmentPlanCombo")
        self.plan_combo.addItem("دون مخطط مرتبط", None)
        self._center_combo(self.plan_combo)
        self.plan_combo.setEnabled(False)

        self.assessment_type_combo = QComboBox()
        self.assessment_type_combo.setObjectName(
            "assessmentTypeCombo"
        )
        self.assessment_type_combo.addItem("اختر نوع الملف", "")
        self._center_combo(self.assessment_type_combo)
        for assessment_type in ASSESSMENT_FILE_TYPES:
            self.assessment_type_combo.addItem(
                assessment_type, assessment_type
            )
            self._center_combo(self.assessment_type_combo)
        plan_type_layout = QHBoxLayout()
        plan_type_layout.setSpacing(14)
        plan_type_layout.addWidget(
            self._create_selector_field(
                "المخطط:", self.plan_combo
            ),
            1,
        )
        plan_type_layout.addWidget(
            self._create_selector_field(
                "نوع الملف:", self.assessment_type_combo
            ),
            1,
        )
        assessment_layout.addLayout(plan_type_layout)

        self.period_combo = QComboBox()
        self.period_combo.setObjectName("assessmentPeriodCombo")
        self._center_combo(self.period_combo)
        self.period_combo.setEnabled(False)
        self.period_label = QLabel("الفترة:")
        self.period_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.period_row = QHBoxLayout()
        self.period_row.addStretch()
        self.period_row.addWidget(self.period_label)
        self.period_row.addWidget(self.period_combo, 1)
        self.period_row.addStretch()
        assessment_layout.addLayout(self.period_row)
        self.period_label.hide()
        self.period_combo.hide()

        content_layout.addWidget(assessment_group)
        content_layout.addStretch()
        scroll_area.setWidget(content)
        layout.addWidget(scroll_area, 1)

        buttons = QDialogButtonBox()
        buttons.setObjectName("assessmentFileDialogButtons")
        self.create_button = buttons.addButton(
            "إنشاء", QDialogButtonBox.ButtonRole.AcceptRole
        )
        cancel_button = buttons.addButton(
            "إلغاء", QDialogButtonBox.ButtonRole.RejectRole
        )
        self.create_button.setProperty("actionType", "add")
        cancel_button.setProperty("actionType", "cancel")
        set_dialog_button_action_types(buttons)
        self.create_button.setEnabled(False)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.class_combo.currentIndexChanged.connect(
            self._class_changed
        )
        self.activity_combo.currentIndexChanged.connect(
            self._activity_changed
        )
        self.plan_combo.currentIndexChanged.connect(
            self._plan_changed
        )
        self.assessment_type_combo.currentIndexChanged.connect(
            self._assessment_type_changed
        )
        self.period_combo.currentIndexChanged.connect(
            self._update_create_state
        )
        self.class_combo.setFocus()

    @staticmethod
    def _center_combo(combo):
        combo.setEditable(True)
        combo.lineEdit().setReadOnly(True)
        combo.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)
        model = combo.model()
        for index in range(combo.count()):
            item = model.item(index)
            if item is not None:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    @staticmethod
    def _create_selector_field(label_text, editor):
        field = QWidget()
        field_layout = QVBoxLayout(field)
        field_layout.setContentsMargins(0, 0, 0, 0)
        field_layout.setSpacing(6)
        label = QLabel(label_text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        field_layout.addWidget(label)
        field_layout.addWidget(editor)
        return field

    @staticmethod
    def _centered_label(text):
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def _load_class_options(self):
        options = self.db.teaching_assignment_classes(
            include_groups=True,
            expand_groups=True,
        )
        for option in options:
            class_ids = tuple(
                dict.fromkeys(int(class_id) for class_id in option["class_ids"])
            )
            value = {
                "class_ids": class_ids,
                "subjects": list(option.get("subjects", [])),
            }
            self.class_combo.addItem(str(option["name"]), value)

    def _class_changed(self, *_):
        option = self.class_combo.currentData()
        self.activity_combo.blockSignals(True)
        self.activity_combo.clear()
        self.activity_combo.addItem("اختر النشاط", "")
        self.activity_combo.setEnabled(option is not None)
        if option is not None:
            for activity in _activity_names_for_subjects(
                option["subjects"]
            ):
                self.activity_combo.addItem(activity, activity)
        self.activity_combo.blockSignals(False)
        self._center_combo(self.activity_combo)
        self._activity_changed()

    def _activity_changed(self, *_):
        activity = str(self.activity_combo.currentData() or "").strip()
        self._set_activity_options(activity)
        self.plan_combo.blockSignals(True)
        self.plan_combo.clear()
        self.plan_combo.addItem("دون مخطط مرتبط", None)
        plan_rows = []
        if activity:
            selected_class = self.class_combo.currentData()
            selected_class_ids = set(selected_class["class_ids"])
            selected_domain = _domain_for_activity(activity)
            relevant_activities = {
                _normalized_activity_name(name)
                for name in _plan_activity_names(activity)
            }
            for plan in self.db.teaching_plans():
                plan_class_ids = (
                    set(plan[20])
                    if len(plan) > 20 and plan[20]
                    else {plan[2]}
                )
                if (
                    not plan_class_ids.intersection(selected_class_ids)
                    or str(plan[5] or "").strip() != selected_domain
                ):
                    continue
                plan_items = self.db.teaching_plan_items(plan[0])
                if any(
                    _normalized_activity_name(item[0][1])
                    in relevant_activities
                    for item in plan_items
                ):
                    plan_rows.append(plan)
            for plan in plan_rows:
                self.plan_combo.addItem(
                    _assessment_plan_label(plan), int(plan[0])
                )
            self._center_combo(self.plan_combo)
        self.plan_combo.setEnabled(bool(activity))
        self.plan_combo.blockSignals(False)
        self._plan_changed()

    def _set_activity_options(self, activity):
        for checkbox in self.activity_checkboxes.values():
            self.activity_selection_layout.removeWidget(checkbox)
            checkbox.deleteLater()
        self.activity_checkboxes.clear()

        activities = _plan_activity_names(activity) if activity else ()
        self.activity_selection_group.setVisible(bool(activities))
        automatic_selection = len(activities) == 1
        for index, name in enumerate(activities):
            checkbox = QCheckBox(name)
            checkbox.setObjectName("assessmentActivityOption")
            checkbox.setChecked(automatic_selection)
            checkbox.setEnabled(not automatic_selection)
            checkbox.stateChanged.connect(self._update_create_state)
            self.activity_checkboxes[name] = checkbox
            self.activity_selection_layout.addWidget(
                checkbox, index // 2, index % 2
            )

    def _selected_activities(self):
        return [
            name
            for name, checkbox in self.activity_checkboxes.items()
            if checkbox.isChecked()
        ]

    def _plan_changed(self, *_):
        activity = str(self.activity_combo.currentData() or "").strip()
        plan_id = self.plan_combo.currentData()
        domain = ""
        competence_values = []
        if plan_id is not None:
            plan = next(
                (
                    row for row in self.db.teaching_plans()
                    if row[0] == plan_id
                ),
                None,
            )
            if plan is not None:
                domain = (
                    str(plan[5] or "").strip()
                    or _domain_for_activity(activity)
                )
                plan_competence = (
                    str(plan[7] or "").strip()
                    if len(plan) > 7 else ""
                )
                if plan_competence:
                    competence_values.append(plan_competence)
            if not competence_values and domain:
                competence_values = self._domain_competences(domain)
        else:
            domain = _domain_for_activity(activity)
            if activity and domain:
                competence_values = self._domain_competences(domain)

        self.domain_edit.setText(domain)
        self.domain_edit.setReadOnly(bool(domain))
        unique_competences = list(dict.fromkeys(competence_values))
        competence_text = "\n".join(unique_competences)
        self.competence_edit.setPlainText(competence_text)
        self.competence_edit.setReadOnly(bool(competence_text))
        self._update_create_state()

    def _domain_competences(self, domain):
        majal_ids = {
            name: majal_id
            for majal_id, name in self.db.planning_majals()
        }
        majal_id = majal_ids.get(domain)
        if majal_id is None:
            return []

        selected_class = self.class_combo.currentData()
        competences = []
        for class_id in selected_class["class_ids"]:
            for _competence_majal_id, competence in (
                self.db.planning_competences_majal(class_id, majal_id)
            ):
                if str(competence or "").strip():
                    competences.append(str(competence).strip())
        return list(dict.fromkeys(competences))

    def _assessment_type_changed(self, *_):
        assessment_type = self.assessment_type_combo.currentData()
        periods = _PERIODS_BY_TYPE.get(assessment_type, ())
        self.period_combo.blockSignals(True)
        self.period_combo.clear()
        self.period_combo.addItems(periods)
        self._center_combo(self.period_combo)
        self.period_combo.setEnabled(bool(periods))
        self.period_combo.setVisible(bool(periods))
        self.period_label.setVisible(bool(periods))
        self.period_combo.blockSignals(False)
        self._update_create_state()

    def _update_create_state(self, *_):
        assessment_type = self.assessment_type_combo.currentData()
        has_period = (
            assessment_type not in _PERIODS_BY_TYPE
            or bool(self.period_combo.currentText())
        )
        self.create_button.setEnabled(
            self.class_combo.currentData() is not None
            and bool(self.activity_combo.currentData())
            and bool(self._selected_activities())
            and bool(assessment_type)
            and has_period
        )

    def _validate_and_accept(self):
        if not self.create_button.isEnabled():
            return
        self.accept()

    def values(self):
        selected_class = self.class_combo.currentData()
        if selected_class is None:
            raise ValueError("يجب اختيار قسم.")
        return {
            "class_ids": list(selected_class["class_ids"]),
            "activity_name": str(self.activity_combo.currentData() or ""),
            "selected_activities": self._selected_activities(),
            "teaching_plan_id": self.plan_combo.currentData(),
            "assessment_type": str(
                self.assessment_type_combo.currentData() or ""
            ),
            "period": (
                self.period_combo.currentText()
                if self.assessment_type_combo.currentData()
                in _PERIODS_BY_TYPE else ""
            ),
            "domain": self.domain_edit.text().strip(),
            "competence_text": (
                self.competence_edit.toPlainText().strip()
            ),
        }


class AssessmentFileManagementPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.last_saved_file_id = None
        self.selected_assessment_file_id = None
        self._file_cards = {}
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)
        actions = (
            (
                "assessmentFileNewButton",
                "ملف تقييم ومتابعة جديد",
                "add",
                True,
            ),
            (
                "assessmentFileActivitiesButton",
                "إعداد أنشطة ملف التقييم والمتابعة",
                "edit",
                False,
            ),
            ("assessmentFileImportJsonButton", "استيراد JSON", "browse", False),
        )
        self.action_buttons = {}
        for object_name, text, action_type, enabled in actions:
            button = QPushButton(text)
            button.setObjectName(object_name)
            button.setProperty("actionType", action_type)
            button.setEnabled(enabled)
            if not enabled:
                button.setToolTip("سيتم تفعيل هذا الإجراء لاحقًا.")
            if object_name == "assessmentFileNewButton":
                button.clicked.connect(self.add_new_file)
            actions_layout.addWidget(button)
            self.action_buttons[object_name] = button
        actions_layout.addStretch()
        page_layout.addLayout(actions_layout)

        files_header = QHBoxLayout()
        files_title = QLabel("ملفات التقييم والمتابعة المسجلة")
        files_title.setObjectName("assessmentFilesTitle")
        files_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.files_count_label = QLabel()
        self.files_count_label.setObjectName("assessmentFilesCount")
        files_header.addWidget(files_title)
        files_header.addWidget(self.files_count_label)
        files_header.addStretch()
        page_layout.addLayout(files_header)

        self.files_scroll = QScrollArea(self)
        self.files_scroll.setObjectName("assessmentFilesScrollArea")
        self.files_scroll.setWidgetResizable(True)
        self.files_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.files_container = QWidget()
        self.files_grid = QGridLayout(self.files_container)
        self.files_grid.setContentsMargins(4, 4, 8, 8)
        self.files_grid.setHorizontalSpacing(12)
        self.files_grid.setVerticalSpacing(12)
        self.files_scroll.setWidget(self.files_container)
        page_layout.addWidget(self.files_scroll, 1)
        self._refresh_files()

    def _refresh_files(self):
        while self.files_grid.count():
            item = self.files_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._file_cards.clear()

        records = self.db.assessment_files()
        record_ids = {record["id"] for record in records}
        if self.selected_assessment_file_id not in record_ids:
            self.selected_assessment_file_id = None
        plan_ids = {
            record["teaching_plan_id"]
            for record in records
            if record.get("teaching_plan_id") is not None
        }
        plans_by_id = {}
        if plan_ids:
            plans_by_id = {
                plan[0]: _assessment_plan_label(plan)
                for plan in self.db.teaching_plans()
                if plan[0] in plan_ids
            }

        self.files_count_label.setText(f"({len(records)})")
        if not records:
            empty_label = QLabel(
                "لا توجد ملفات تقييم ومتابعة مسجلة حتى الآن."
            )
            empty_label.setObjectName("assessmentFilesEmptyState")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setMinimumHeight(120)
            self.files_grid.addWidget(empty_label, 0, 0, 1, 2)
            return

        for index, record in enumerate(records):
            card = self._create_file_card(
                record,
                plans_by_id.get(
                    record["teaching_plan_id"],
                    (
                        f"مخطط رقم {record['teaching_plan_id']}"
                        if record["teaching_plan_id"] is not None
                        else "دون مخطط مرتبط"
                    ),
                ),
            )
            card.enable_selection(record["id"])
            card.selected.connect(self.select_file)
            delete_button = card.action_buttons[
                "assessmentFileCardDeleteButton"
            ]
            delete_button.setEnabled(True)
            delete_button.clicked.connect(
                lambda _checked=False, file_id=record["id"]: (
                    self.delete_file(file_id)
                )
            )
            self._file_cards[record["id"]] = card
            self.files_grid.addWidget(card, index // 2, index % 2)
        self.files_grid.setColumnStretch(0, 1)
        self.files_grid.setColumnStretch(1, 1)

    @staticmethod
    def _create_file_card(record, plan_label):
        card = AssessmentFileCard()
        card.setObjectName("assessmentFileCard")
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setStyleSheet(
            "QFrame#assessmentFileCard {"
            "background: #FFFFFF; border: 1px solid #D7DEE8;"
            "border-radius: 8px;}"
            "QFrame#assessmentFileCard[selected='true'] {"
            "background: #F3F8FF; border: 2px solid #2563EB;}"
            "QLabel[role='fieldLabel'] {"
            "color: #64748B; font-size: 11px;}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)

        assessment_type = str(record.get("assessment_type") or "").strip()
        distinguishing_text = (
            assessment_type.split(" في ", 1)[1]
            if " في " in assessment_type
            else assessment_type
        )
        period = str(record.get("period") or "").strip()
        if period:
            distinguishing_text = f"{distinguishing_text} — {period}"

        header = QHBoxLayout()
        title = QLabel(
            f"{record['activity_name']} — {distinguishing_text}"
        )
        title.setObjectName("assessmentFileCardTitle")
        title.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #1E3A5F;"
        )
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)
        card.action_buttons = {}
        for name, tooltip, standard_icon in (
            (
                "assessmentFileCardEditButton",
                "تعديل ملف التقييم والمتابعة",
                QStyle.StandardPixmap.SP_FileDialogDetailedView,
            ),
            (
                "assessmentFileCardDeleteButton",
                "حذف ملف التقييم والمتابعة",
                QStyle.StandardPixmap.SP_TrashIcon,
            ),
            (
                "assessmentFileCardExportJsonButton",
                "تصدير ملف التقييم والمتابعة بصيغة JSON",
                QStyle.StandardPixmap.SP_DialogSaveButton,
            ),
            (
                "assessmentFileCardExportPdfButton",
                "تصدير ملف التقييم والمتابعة بصيغة PDF",
                QStyle.StandardPixmap.SP_FileIcon,
            ),
        ):
            button = QToolButton(card)
            button.setObjectName(name)
            button.setIcon(card.style().standardIcon(standard_icon))
            button.setFixedSize(28, 28)
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.setEnabled(False)
            button.setStyleSheet(
                "QToolButton { background: transparent; border: none; "
                "padding: 3px; }"
                "QToolButton:hover { background: transparent; border: none; }"
            )
            card.action_buttons[name] = button
            card.add_floating_action(button)

        if period:
            assessment_type = f"{assessment_type} — {period}"

        selected_activities = record.get("selected_activities", [])
        activities_text = (
            "\n".join(f"• {activity}" for activity in selected_activities)
            or "غير محددة"
        )
        fields = (
            ("القسم", "، ".join(record.get("class_names", []))),
            ("الأنشطة المطلوب تقييمها", activities_text),
            ("المجال", record.get("domain") or "غير محدد"),
            ("نوع التقييم", assessment_type or "غير محدد"),
            ("المخطط", plan_label),
            ("تاريخ التسجيل", record.get("created_at") or "غير محدد"),
        )
        metadata = QGridLayout()
        metadata.setHorizontalSpacing(16)
        metadata.setVerticalSpacing(8)
        for index, (label_text, value_text) in enumerate(fields):
            row, column = divmod(index, 2)
            field = QWidget()
            field_layout = QVBoxLayout(field)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(2)
            label = QLabel(f"{label_text}:")
            label.setProperty("role", "fieldLabel")
            value = QLabel(str(value_text))
            value.setWordWrap(True)
            if label_text == "الأنشطة المطلوب تقييمها":
                value.setObjectName("assessmentFileCardActivities")
                value.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Preferred,
                )
                value.setAlignment(
                    Qt.AlignmentFlag.AlignRight
                    | Qt.AlignmentFlag.AlignTop
                )
            field_layout.addWidget(label)
            if label_text == "الأنشطة المطلوب تقييمها":
                field_layout.addWidget(
                    value, 0, Qt.AlignmentFlag.AlignRight
                )
            else:
                field_layout.addWidget(value)
            metadata.addWidget(field, row, column)
        metadata.setColumnStretch(0, 1)
        metadata.setColumnStretch(1, 1)
        layout.addLayout(metadata)

        competence = str(record.get("competence_text") or "").strip()
        if competence:
            competence_label = QLabel("كفاية المجال:")
            competence_label.setProperty("role", "fieldLabel")
            competence_value = QLabel(competence)
            competence_value.setWordWrap(True)
            layout.addWidget(competence_label)
            layout.addWidget(competence_value)
        return card

    def select_file(self, file_id):
        if file_id not in self._file_cards:
            return
        self.selected_assessment_file_id = file_id
        for card_id, card in self._file_cards.items():
            card.set_selected(card_id == file_id)

    def delete_file(self, file_id):
        card = self._file_cards.get(file_id)
        if card is None:
            return
        answer = QMessageBox.question(
            self,
            "تأكيد حذف ملف التقييم والمتابعة",
            "سيُحذف ملف التقييم والمتابعة المحدد نهائيًا. هل تريد المتابعة؟",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.db.delete_assessment_file(file_id)
        except (ValueError, sqlite3.Error) as error:
            QMessageBox.warning(
                self,
                "تعذر حذف ملف التقييم والمتابعة",
                str(error),
            )
            return
        if self.selected_assessment_file_id == file_id:
            self.selected_assessment_file_id = None
        self._refresh_files()

    def add_new_file(self):
        try:
            dialog = NewAssessmentFileDialog(self.db, self)
        except (ValueError, sqlite3.Error) as error:
            QMessageBox.warning(
                self,
                "تعذر تحميل بيانات ملف التقييم والمتابعة",
                str(error),
            )
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.last_saved_file_id = self.db.save_assessment_file(
                dialog.values()
            )
        except (ValueError, sqlite3.Error) as error:
            QMessageBox.warning(
                self,
                "تعذر حفظ ملف التقييم والمتابعة",
                str(error),
            )
            return
        self._refresh_files()
        QMessageBox.information(
            self,
            "تم الحفظ",
            "تم حفظ بيانات ملف التقييم والمتابعة.",
        )
