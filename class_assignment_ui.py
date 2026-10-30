"""Shared controls for selecting and editing assigned class sections."""
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget


def add_assignment_editor(combo, db, parent, on_saved, year_id=None):
    container = QWidget(parent)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    edit_button = QToolButton(container)
    edit_button.setText("✎")
    edit_button.setToolTip("تعديل الأقسام وأنشطة التدريس")
    edit_button.setAccessibleName("تعديل الأقسام وأنشطة التدريس")
    edit_button.setProperty("actionType", "edit")

    def open_editor():
        from dialogs import TeachingAssignmentsDialog

        dialog = TeachingAssignmentsDialog(db, year_id, parent)
        dialog.assignmentsChanged.connect(on_saved)
        dialog.exec()
        on_saved()

    edit_button.clicked.connect(open_editor)
    layout.addWidget(combo, 1)
    layout.addWidget(edit_button)
    return container
