# -*- coding: utf-8 -*-
"""Extracted module completion."""
import re
import threading

from PySide6.QtCore import Qt, QObject, QRunnable, QTimer, Signal, QThreadPool
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTextEdit
from database_connection import connect_database


class CompletionWorkerSignals(QObject):
    finished = Signal(object)


class CompletionWorker(QRunnable):
    _smart_models = {}
    _smart_models_lock = threading.Lock()

    def __init__(self, request_id, db_path, key_name, activity_title, text,
                 position, cached_values, allow_completion,
                 contextual_words=None):
        super().__init__()
        self.signals = CompletionWorkerSignals()
        self.request_id = request_id
        self.db_path = db_path
        self.key_name = key_name
        self.activity_title = activity_title
        self.text = text
        self.position = position
        self.cached_values = cached_values
        self.allow_completion = allow_completion
        self.contextual_words = contextual_words

    def run(self):
        values = self.cached_values
        error = None
        try:
            if values is None:
                values = self._get_values()
            suffix = self._contextual_completion_suffix()
            if suffix is None:
                suffix = (
                    self._completion_suffix(values)
                    if self.allow_completion else ""
                )
            suggested_text = (
                self.text[:self.position] + suffix + self.text[self.position:]
            )
            suggestions, suggestion_error = self._smart_suggestions(
                suggested_text.strip(), values
            )
            error = suggestion_error
        except Exception as exc:
            suffix = ""
            suggestions = []
            error = f"Autocomplete failed: {exc}"
        self.signals.finished.emit((
            self.request_id, self.text, self.position, self.key_name,
            self.activity_title, suffix, suggestions, values, error
        ))

    def _contextual_completion_suffix(self):
        if self.contextual_words is None:
            return None
        if self.position != len(self.text):
            return ""

        before_cursor = self.text[:self.position]
        tokens = re.findall(r"\S+", before_cursor)
        if not tokens:
            return ""

        words = list(self.contextual_words)
        if len(tokens) > len(words) + 1:
            return None
        if len(tokens) == len(words) + 1:
            return ""

        trailing_space = bool(before_cursor and before_cursor[-1].isspace())
        if len(tokens) == 1:
            return (" " if not trailing_space else "") + " ".join(words)
        if trailing_space:
            next_word_index = len(tokens) - 1
            return " ".join(words[next_word_index:])

        word_index = len(tokens) - 2
        typed_word = tokens[-1]
        expected_word = words[word_index]
        if expected_word.startswith(typed_word):
            return expected_word[len(typed_word):] + (
                " " + " ".join(words[word_index + 1:])
                if word_index + 1 < len(words) else ""
            )
        return ""

    def _get_values(self):
        with connect_database(
            self.db_path, timeout=10, cached_statements=256
        ) as con:
            rows = con.execute(
                """SELECT d.value_text
                   FROM nachat_detail d
                   JOIN nachat n ON n.id = d.nachat_id
                   WHERE d.key_name = ? AND n.title = ?
                     AND COALESCE(d.value_text, '') <> ''""",
                (self.key_name, self.activity_title),
            ).fetchall()
            if not rows:
                rows = con.execute(
                    """SELECT value_text FROM nachat_detail
                       WHERE key_name = ? AND COALESCE(value_text, '') <> ''""",
                    (self.key_name,),
                ).fetchall()
        return list(dict.fromkeys(
            str(row[0]).strip() for row in rows
            if row[0] is not None and str(row[0]).strip()
        ))

    def _completion_suffix(self, values):
        before_cursor = self.text[:self.position]
        if not before_cursor or not values:
            return ""

        from collections import Counter

        if before_cursor[-1].isspace():
            words = before_cursor.strip().split()
            if not words:
                return ""
            last_word = words[-1]
            next_word_counts = Counter()
            for value in values:
                words = [w.strip(".,،؛:!?؟()[]{}\"'«»") for w in value.split()]
                for index in range(len(words) - 1):
                    if words[index] == last_word and words[index + 1]:
                        next_word_counts[words[index + 1]] += 1
            candidates = next_word_counts.most_common(1)
            return candidates[0][0] + " " if candidates else ""

        word_match = re.search(r"[^\s]+$", before_cursor)
        if not word_match:
            return ""
        typed_word = word_match.group(0)
        if len(typed_word) < 2:
            return ""

        word_counts = Counter()
        for value in values:
            for word in value.split():
                clean_word = word.strip(".,،؛:!?؟()[]{}\"'«»")
                if clean_word:
                    word_counts[clean_word] += 1
        matches = [
            word for word in word_counts
            if word.startswith(typed_word) and word != typed_word
        ]
        if not matches:
            return ""
        matches.sort(key=lambda word: (-word_counts[word], len(word), word))
        return matches[0][len(typed_word):]

    def _smart_suggestions(self, query, values, limit=6):
        if len(query) < 2 or not values:
            return [], None

        cache_key = (self.db_path, self.key_name, self.activity_title)
        fingerprint = tuple(values)
        with self._smart_models_lock:
            cached = self._smart_models.get(cache_key)
            if cached is None or cached[0] != fingerprint:
                try:
                    from sklearn.feature_extraction.text import TfidfVectorizer
                    vectorizer = TfidfVectorizer(
                        analyzer="char_wb", ngram_range=(2, 5),
                        lowercase=False, sublinear_tf=True
                    )
                    matrix = vectorizer.fit_transform(values)
                    cached = (fingerprint, vectorizer, matrix)
                    self._smart_models[cache_key] = cached
                except (ImportError, ValueError):
                    return self._fallback_suggestions(query, values, limit), None

        try:
            from sklearn.metrics.pairwise import cosine_similarity
            vectorizer, matrix = cached[1], cached[2]
            scores = cosine_similarity(vectorizer.transform([query]), matrix).ravel()
            ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
            return ([(values[i], float(score)) for i, score in ranked
                     if score > 0 and values[i] != query][:limit], None)
        except Exception as exc:
            return self._fallback_suggestions(query, values, limit), (
                f"Smart autocomplete ranking failed: {exc}"
            )

    @staticmethod
    def _fallback_suggestions(query, values, limit):
        from difflib import SequenceMatcher
        ranked = sorted(
            ((value, SequenceMatcher(None, query, value).ratio()) for value in values),
            key=lambda item: item[1], reverse=True
        )
        return [(value, score) for value, score in ranked
                if score > 0.12 and value != query][:limit]


class AutoCompletionTextEdit(QTextEdit):
    """إكمال واقتراح ذكي دون تنفيذ عمليات قاعدة البيانات على خيط الواجهة."""
    suggestions_ready = Signal(object)

    def __init__(self, db, key_combo, activity_title, parent=None):
        super().__init__(parent)
        self.db = db
        self.key_combo = key_combo
        self.activity_title = activity_title
        self.is_completing = False
        self.skip_next_completion = False
        self._completion_cache = {}
        self._request_id = 0
        self._completion_timer = QTimer(self)
        self._completion_timer.setSingleShot(True)
        self._completion_timer.setInterval(120)
        self._completion_timer.timeout.connect(self._start_completion)
        self._worker_running = False
        self._pending_request = False
        self._contextual_words = None
        self.setAcceptRichText(False)
        self.textChanged.connect(self.on_text_changed)

    def keyPressEvent(self, event):
        cursor = self.textCursor()
        if event.key() in (Qt.Key_Backspace, Qt.Key_Delete):
            self.skip_next_completion = True
            if cursor.hasSelection():
                cursor.clearSelection()
                self.setTextCursor(cursor)
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key_Tab, Qt.Key_Right) and cursor.hasSelection():
            cursor.clearSelection()
            cursor.movePosition(QTextCursor.End)
            self.setTextCursor(cursor)
            event.accept()
            return
        if event.key() in (Qt.Key_Left, Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End):
            self.skip_next_completion = True
            super().keyPressEvent(event)
            return
        self.skip_next_completion = False
        super().keyPressEvent(event)

    def on_text_changed(self):
        if self.is_completing or self.skip_next_completion:
            return
        self.schedule_completion()

    def schedule_completion(self, allow_completion=True):
        self._request_id += 1
        self._allow_completion = allow_completion
        self._completion_timer.start()

    def refresh_for_key_change(self, *_args):
        self.schedule_completion(allow_completion=False)

    def set_contextual_completion_words(self, words):
        contextual_words = tuple(words) if words is not None else None
        if contextual_words == self._contextual_words:
            return
        self._contextual_words = contextual_words
        self.schedule_completion()

    def _start_completion(self):
        if self._worker_running:
            self._pending_request = True
            return
        text = self.toPlainText()
        position = self.textCursor().position()
        key_name = self.key_combo.currentText().strip()
        cache_key = (key_name, self.activity_title)
        self._worker_running = True
        worker = CompletionWorker(
            self._request_id, self.db.path, key_name, self.activity_title,
            text, position, self._completion_cache.get(cache_key),
            self._allow_completion, self._contextual_words,
        )
        worker.signals.finished.connect(self._apply_completion_result)
        QThreadPool.globalInstance().start(worker)

    def _apply_completion_result(self, result):
        self._worker_running = False
        (request_id, text, position, key_name, activity_title, suffix,
         suggestions, values, error) = result
        if values is not None:
            self._completion_cache[(key_name, activity_title)] = values
        if error:
            print(error)
        if (
            request_id != self._request_id
            or text != self.toPlainText()
            or position != self.textCursor().position()
            or key_name != self.key_combo.currentText().strip()
            or activity_title != self.activity_title
        ):
            self._start_pending_request()
            return
        if suffix:
            self.is_completing = True
            try:
                cursor = self.textCursor()
                cursor.insertText(suffix)
                cursor.setPosition(position)
                cursor.setPosition(position + len(suffix), QTextCursor.KeepAnchor)
                self.setTextCursor(cursor)
            finally:
                self.is_completing = False
        self.suggestions_ready.emit(suggestions)

        self._start_pending_request()

    def _start_pending_request(self):
        if self._pending_request:
            self._pending_request = False
            self._completion_timer.stop()
            self._start_completion()
