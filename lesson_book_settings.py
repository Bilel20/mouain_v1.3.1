"""Settings page for the lesson book and its printed activity details."""
import json

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app_config import DETAIL_KEYS_BY_NAMAT
from database_connection import connect_database


PRINT_DETAIL_EXCLUSIONS_KEY = "lessonBook/printDetailExclusions"


def _settings_store(settings=None):
    return settings or QSettings("Bilel20", "Mouain_v1.2")


def load_print_detail_exclusions(settings=None):
    raw = _settings_store(settings).value(PRINT_DETAIL_EXCLUSIONS_KEY, "{}")
    try:
        exclusions = json.loads(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("إعدادات خصائص الطباعة المحفوظة غير صالحة.") from exc
    if not isinstance(exclusions, dict) or any(
        not isinstance(mode, str)
        or not isinstance(keys, list)
        or any(not isinstance(key, str) for key in keys)
        for mode, keys in exclusions.items()
    ):
        raise ValueError("إعدادات خصائص الطباعة المحفوظة غير صالحة.")
    return {
        mode: set(keys)
        for mode, keys in exclusions.items()
    }


def filter_print_details(payload, exclusions):
    filtered_payload = {
        **payload,
        "lessons": [
            {
                **lesson,
                "activities": [
                    {
                        **activity,
                        "details": [
                            detail
                            for detail in activity.get("details", [])
                            if str(detail.get("key_name") or "").strip()
                            not in exclusions.get(
                                str(lesson.get("namat_taallom") or "").strip(),
                                set(),
                            )
                        ],
                    }
                    for activity in lesson.get("activities", [])
                ],
            }
            for lesson in payload.get("lessons", [])
        ],
    }
    return filtered_payload


class LessonBookSettingsPage(QWidget):
    def __init__(
        self,
        db,
        open_timetable,
        open_teaching_assignments,
        open_teacher_info,
        parent=None,
        settings=None,
    ):
        super().__init__(parent)
        self.db = db
        self.settings = _settings_store(settings)
        self.detail_checkboxes = {}
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        page_layout = QVBoxLayout(self)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 20, 24, 24)
        content_layout.setSpacing(14)

        shortcuts_group = QGroupBox("إعدادات دفتر إعداد الدروس")
        shortcuts_layout = QHBoxLayout(shortcuts_group)
        for label, callback in (
            ("جدول أوقات المعلم", open_timetable),
            ("الأقسام وأنشطة التدريس", open_teaching_assignments),
            ("بيانات المعلم والمؤسسة", open_teacher_info),
        ):
            button = QPushButton(label)
            button.setProperty("actionType", "browse")
            button.clicked.connect(callback)
            shortcuts_layout.addWidget(button)
        content_layout.addWidget(shortcuts_group)

        print_group = QGroupBox("الخصائص الظاهرة عند الطباعة")
        print_layout = QVBoxLayout(print_group)
        hint = QLabel(
            "حدد الخصائص التي تريد إظهارها في معاينة وطباعة دفتر إعداد "
            "الدروس لكل نمط تعلم. تحفظ التغييرات تلقائيًا."
        )
        hint.setWordWrap(True)
        print_layout.addWidget(hint)

        exclusions = load_print_detail_exclusions(self.settings)
        available_keys = self._available_detail_keys()
        for mode, default_keys in DETAIL_KEYS_BY_NAMAT.items():
            mode_group = QGroupBox(mode)
            mode_grid = QGridLayout(mode_group)
            mode_keys = list(dict.fromkeys((*default_keys, *available_keys)))
            for index, key in enumerate(mode_keys):
                checkbox = QCheckBox(key)
                checkbox.setChecked(key not in exclusions.get(mode, set()))
                checkbox.toggled.connect(
                    lambda checked, selected_mode=mode, selected_key=key:
                    self._save_detail_choice(
                        selected_mode, selected_key, checked
                    )
                )
                self.detail_checkboxes[(mode, key)] = checkbox
                mode_grid.addWidget(checkbox, index // 3, index % 3)
            print_layout.addWidget(mode_group)
        content_layout.addWidget(print_group)
        content_layout.addStretch()
        scroll_area.setWidget(content)
        page_layout.addWidget(scroll_area)

    def _available_detail_keys(self):
        with connect_database(self.db.path) as connection:
            rows = connection.execute("""
                SELECT DISTINCT trim(key_name)
                FROM nachat_detail
                WHERE trim(COALESCE(key_name, '')) <> ''
                ORDER BY trim(key_name)
            """).fetchall()
        return [row[0] for row in rows]

    def _save_detail_choice(self, mode, key, enabled):
        exclusions = load_print_detail_exclusions(self.settings)
        mode_exclusions = exclusions.setdefault(mode, set())
        if enabled:
            mode_exclusions.discard(key)
        else:
            mode_exclusions.add(key)
        self.settings.setValue(
            PRINT_DETAIL_EXCLUSIONS_KEY,
            json.dumps(
                {name: sorted(values) for name, values in exclusions.items()},
                ensure_ascii=False,
            ),
        )
