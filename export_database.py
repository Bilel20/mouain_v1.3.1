# -*- coding: utf-8 -*-
"""Export a regular SQLite copy of the school database."""
import os
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

from app_config import DB_PATH


def _table_counts(connection):
    tables = connection.execute(
        """SELECT name FROM sqlite_master
           WHERE type='table' AND name NOT LIKE 'sqlite_%'"""
    ).fetchall()
    counts = {}
    for (name,) in tables:
        quoted_name = '"' + name.replace('"', '""') + '"'
        counts[name] = connection.execute(
            f"SELECT count(*) FROM {quoted_name}"
        ).fetchone()[0]
    return counts


def export_to_sqlite(destination_path, source_path=DB_PATH, overwrite=False):
    """Create and verify an unencrypted SQLite copy of the source database."""
    source = Path(source_path).resolve()
    destination = Path(destination_path).resolve()
    if source == destination:
        raise ValueError("The export destination must differ from the source database.")
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(f"Source database not found: {source}")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Export destination already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{secrets.token_hex(8)}.tmp"
    )
    reserved_destination = False
    try:
        with closing(sqlite3.connect(str(source), timeout=10)) as original:
            expected_counts = _table_counts(original)
            with closing(sqlite3.connect(str(temporary), timeout=10)) as exported:
                original.backup(exported)
                integrity = exported.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise RuntimeError(
                        f"Exported SQLite database failed integrity check: {integrity}"
                    )
                if _table_counts(exported) != expected_counts:
                    raise RuntimeError(
                        "Export verification failed: table row counts differ from the source."
                    )

        if not overwrite:
            descriptor = os.open(
                str(destination), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            os.close(descriptor)
            reserved_destination = True
        os.replace(temporary, destination)
        reserved_destination = False
        return destination
    finally:
        if temporary.exists():
            temporary.unlink()
        if reserved_destination and destination.exists():
            destination.unlink()
