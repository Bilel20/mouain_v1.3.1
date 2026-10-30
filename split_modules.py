# -*- coding: utf-8 -*-
"""Split main.py into modules, keeping the UI class (SchoolPlanner) in main.py.

Usage: python split_modules.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "main.py"

# Classes/functions that move OUT of main.py, by module name.
MOVES = {
    "app_config": [
        "set_dialog_button_action_types", "load_custom_font",
        "ArabicDialogTranslator",
    ],
    "database": [
        "Database", "SchoolDataFormatter",
    ],
    "completion": [
        "CompletionWorker", "AutoCompletionTextEdit",
    ],
    "dialogs": [
        "YearImportDialog", "SchoolYearSelectionDialog", "LessonEntryDialog",
        "ActivityEntryDialog", "DetailEntryDialog", "BulkLessonEntryDialog",
        "EntryDialog", "DaySelectionDialog", "TimetableDialog",
        "CompetenceManagerDialog",
    ],
    "widgets": [
        "CustomCalendarWidget", "DayBrowseCalendar", "DayLessonsTreeDelegate",
        "DayLessonsBrowsePage", "OfficialProgramsWidget",
        "OfficialProgramsTreeWidget", "OfficialProgramsBrowsePage",
    ],
}

KEEP_IN_MAIN = {"SchoolPlanner"}


def read_source():
    return SRC.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")


def is_block_start(line):
    stripped = line.strip()
    if stripped.startswith("class ") and ":" in stripped:
        return "class"
    if re.match(r"^def \w+", stripped):
        return "def"
    return None


def block_name(line, kind):
    if kind == "class":
        return re.match(r"class\s+(\w+)", line.strip()).group(1)
    return re.match(r"def\s+(\w+)", line.strip()).group(1)


def find_blocks(lines):
    """Return [(name, kind, start_idx, end_idx_exclusive)] for top-level blocks."""
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        kind = is_block_start(line)
        if kind:
            indent = len(line) - len(line.lstrip())
            j = i + 1
            while j < n:
                nxt = lines[j]
                if nxt.strip() == "":
                    j += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip())
                if nxt_indent <= indent and nxt.strip() != "":
                    break
                j += 1
            blocks.append((block_name(line, kind), kind, i, j))
            i = j
        else:
            i += 1
    return blocks


def main():
    lines = read_source()
    blocks = find_blocks(lines)
    known = {b[0]: b for b in blocks}
    missing = []
    for module, names in MOVES.items():
        for name in names:
            if name not in known:
                missing.append(name)
    if missing:
        raise SystemExit("Not found in main.py: " + ", ".join(missing))

    module_of = {}
    for module, names in MOVES.items():
        for name in names:
            module_of[name] = module

    # Everything top-level except SchoolPlanner and the __main__ block moves out.
    move_set = {b[0] for b in blocks if b[0] in module_of}
    keep_set = {b[0] for b in blocks if b[0] in KEEP_IN_MAIN}

    # Collect original import block (lines before the first block that are imports/paths).
    first_block_start = min(b[2] for b in blocks)
    header_lines = lines[:first_block_start]

    module_sources = {m: [] for m in MOVES}
    for module, names in MOVES.items():
        for name in names:
            b = known[name]
            module_sources[module].append(
                (b[2], b[3], "\n".join(lines[b[2]:b[3]]).strip("\n"))
            )
    for m in module_sources:
        module_sources[m].sort(key=lambda t: t[0])

    APP_HEADER = "# -*- coding: utf-8 -*-\n"
    base_imports = {
        "app_config": (
            "import sys\nfrom pathlib import Path\n\n"
            "from PySide6.QtCore import QTranslator\n"
        ),
        "database": (
            "import re\nfrom contextlib import contextmanager\n"
            "from datetime import datetime\n\n"
            "from app_config import DB_PATH, SCHOOL_INFO_COLUMNS\n"
            "from database_connection import connect_database\nimport sqlite3\n"
        ),
        "completion": (
            "from collections import Counter\n\n"
            "from PySide6.QtCore import Qt, QObject, QRunnable, QTimer, Signal, QThreadPool\n"
            "from PySide6.QtGui import QTextCursor\n"
            "from PySide6.QtWidgets import QTextEdit, QCompleter, QStringListModel\n"
            "from database_connection import connect_database\nimport sqlite3\n"
        ),
        "dialogs": (
            "import re\nfrom datetime import datetime, time\n\n"
            "from PySide6.QtCore import Qt, QStringListModel, QTimer, Signal, QObject\n"
            "from PySide6.QtGui import QColor, QFontDatabase\n"
            "from PySide6.QtWidgets import (\n"
            "    QDialog, QDialogButtonBox, QAbstractItemView, QCalendarWidget,\n"
            "    QComboBox, QDateEdit, QFormLayout, QGridLayout, QGroupBox,\n"
            "    QHeaderView, QLabel, QLineEdit, QMessageBox, QSpinBox,\n"
            "    QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit,\n"
            "    QTimeEdit, QVBoxLayout, QWidget, QCompleter, QListWidget,\n"
            "    QListWidgetItem, QTreeWidget, QTreeWidgetItem, QToolButton,\n"
            ")\n\n"
            "from app_config import (DB_PATH, SCHOOL_INFO_COLUMNS,\n"
            "                        set_dialog_button_action_types)\n"
            "from completion import AutoCompletionTextEdit\n"
            "from database import Database\n"
        ),
        "widgets": (
            "from datetime import datetime, timedelta\n\n"
            "from PySide6.QtCore import Qt, QDate, QLocale, QMarginsF, QTimer\n"
            "from PySide6.QtGui import (\n"
            "    QColor, QFont, QPainter, QPageLayout, QPageSize, QTextCursor,\n"
            "    QTextBlockFormat, QTextCharFormat, QTextDocument, QTextOption,\n"
            ")\n"
            "from PySide6.QtPrintSupport import QPrinter, QPrintDialog, QPrintPreviewDialog\n"
            "from PySide6.QtWidgets import (\n"
            "    QAbstractItemView, QApplication, QCalendarWidget, QComboBox,\n"
            "    QDialog, QFileDialog, QFormLayout, QGridLayout, QGroupBox,\n"
            "    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,\n"
            "    QMessageBox, QStackedWidget, QStyledItemDelegate, QStyle,\n"
            "    QTableWidget, QTableWidgetItem, QTextEdit, QTimeEdit, QToolButton,\n"
            "    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,\n"
            ")\n\n"
            "from app_config import (ACTIVITIES_BY_MAJAL, DB_PATH,\n"
            "                        DETAIL_KEYS_BY_NAMAT, SCHOOL_INFO_COLUMNS,\n"
            "                        set_dialog_button_action_types)\n"
            "from completion import AutoCompletionTextEdit\n"
            "from database import Database, SchoolDataFormatter\n"
            "from dialogs import (ActivityEntryDialog, DetailEntryDialog,\n"
            "                     EntryDialog, LessonEntryDialog)\n"
            "import sqlite3\n"
        ),
    }

    for module, snippets in module_sources.items():
        parts = [APP_HEADER, f'"""Extracted module {module}."""\n', base_imports[module], ""]
        for _start, _end, text in snippets:
            parts.append(text)
            parts.append("\n\n\n")
        (ROOT / f"{module}.py").write_text("".join(parts), encoding="utf-8")
        print(f"wrote {module}.py ({sum(len(s[2]) for s in snippets)} chars of code)")

    # Rebuild main.py: header + imports + SchoolPlanner + main().
    planner_blocks = [known[n] for n in KEEP_IN_MAIN if n in known]
    planner_blocks.sort(key=lambda b: b[2])
    planner_parts = [APP_HEADER]
    planner_parts.append(
        '"""الواجهة الرئيسية (SchoolPlanner) — بقية الكود مقسم إلى:\n'
        "app_config, database, completion, dialogs, widgets.\n"
        '"""\n'
    )
    planner_parts.append(
        "import re\n"
    )
    # Import everything from the modules so no reference is broken.
    planner_parts.append("from app_config import *  # noqa: F401,F403\n")
    planner_parts.append("from database import *  # noqa: F401,F403\n")
    planner_parts.append("from completion import *  # noqa: F401,F403\n")
    planner_parts.append("from dialogs import *  # noqa: F401,F403\n")
    planner_parts.append("from widgets import *  # noqa: F401,F403\n")
    planner_parts.append("import json\n")
    planner_parts.append("from database_connection import connect_database\n")
    planner_parts.append("import sqlite3\n")
    planner_parts.append("import os\n")
    planner_parts.append("import shutil\n")
    planner_parts.append("import subprocess\n")
    planner_parts.append("import platform\n")
    planner_parts.append("import zipfile\n")
    planner_parts.append("from datetime import datetime, time, date\n")
    planner_parts.append("import pandas as pd\n")
    planner_parts.append("from PySide6.QtCore import QDate, QLocale, QStringListModel, QTimer\n")
    planner_parts.append("from PySide6.QtGui import QAction, QIcon, QFont, QTextDocument, QTextCursor\n")
    planner_parts.append("from PySide6.QtPrintSupport import QPrinter, QPrintDialog, QPrintPreviewDialog\n")
    planner_parts.append("from PySide6.QtWidgets import (\n"
                         "    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,\n"
                         "    QFormLayout, QStackedWidget, QGridLayout, QSplitter, QGroupBox,\n"
                         "    QLabel, QLineEdit, QComboBox, QSpinBox, QDateEdit, QTimeEdit,\n"
                         "    QTableWidget, QTableWidgetItem, QPushButton, QMessageBox,\n"
                         "    QDialog, QDialogButtonBox, QTextEdit, QHeaderView,\n"
                         "    QAbstractItemView, QCompleter, QMenu, QToolButton, QListWidget,\n"
                         "    QListWidgetItem, QTreeWidget, QTreeWidgetItem, QScrollArea,\n"
                         "    QFileDialog, QCalendarWidget, QSystemTrayIcon,\n"
                         ")\n")
    planner_parts.append("\n")
    for b in planner_blocks:
        planner_parts.append("\n".join(lines[b[2]:b[3]]).strip("\n"))
        planner_parts.append("\n\n\n")

    # original main() entrypoint (after last block)
    last_end = max(b[3] for b in blocks)
    tail = "\n".join(lines[last_end:]).strip("\n")
    if tail:
        planner_parts.append(tail)
        planner_parts.append("\n")
    new_main = "".join(planner_parts)
    SRC.write_text(new_main, encoding="utf-8")
    print("rewrote main.py")


if __name__ == "__main__":
    main()
