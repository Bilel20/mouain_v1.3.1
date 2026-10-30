# -*- coding: utf-8 -*-
"""Shared configuration for regular SQLite database connections."""
import sqlite3


def connect_database(path, **kwargs):
    """Open a standard SQLite database with the application's connection settings."""
    kwargs.setdefault("timeout", 10)
    kwargs.setdefault("cached_statements", 256)
    connection = sqlite3.connect(str(path), **kwargs)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection
    except Exception:
        connection.close()
        raise
