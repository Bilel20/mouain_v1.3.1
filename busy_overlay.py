"""Reusable wait overlay and background task runner for Qt widgets."""
import traceback

from PySide6.QtCore import (
    QEvent, QEventLoop, QObject, QPoint, QRunnable, Qt, QThreadPool, QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget


class BusyOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint,
        )
        self._angle = 0
        self._waiting_for_next_window = False
        self.setObjectName("applicationBusyOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(220, 154)
        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._advance)
        self._timer.start()
        parent.installEventFilter(self)
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        self._move_to_parent_center()
        self.show()
        QApplication.processEvents(
            QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
        )

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Move,
        ):
            self._move_to_parent_center()
        elif (
            self._waiting_for_next_window
            and watched is not self
            and isinstance(watched, QWidget)
            and watched.isWindow()
            and event.type() == QEvent.Type.Show
        ):
            self.close()
        return super().eventFilter(watched, event)

    def _move_to_parent_center(self):
        parent = self.parentWidget()
        if parent is None:
            return
        center = parent.mapToGlobal(parent.rect().center())
        self.move(
            center - QPoint(self.width() // 2, self.height() // 2)
        )

    def closeEvent(self, event):
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        super().closeEvent(event)

    def _advance(self):
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.setPen(QPen(QColor(148, 163, 184, 100), 1))
        painter.setBrush(QColor(255, 255, 255, 232))
        painter.drawRoundedRect(self.rect().adjusted(3, 3, -3, -3), 18, 18)

        center = QPoint(self.width() // 2, 55)
        radius = 30
        for index in range(12):
            angle = (self._angle + index * 30) % 360
            painter.save()
            painter.translate(center)
            painter.rotate(angle)
            color = QColor(37, 99, 235)
            color.setAlpha(35 + index * 18)
            painter.setPen(
                QPen(color, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            painter.drawLine(0, -radius, 0, -radius + 11)
            painter.restore()

        painter.setPen(QColor("#1E293B"))
        painter.drawText(
            8,
            99,
            self.width() - 16,
            36,
            Qt.AlignmentFlag.AlignCenter,
            "يرجى الانتظار قليلاً...",
        )


class _TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class _BackgroundTask(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = _TaskSignals()

    def run(self):
        try:
            self.signals.succeeded.emit(self.function())
        except Exception:
            self.signals.failed.emit(traceback.format_exc())
        finally:
            self.signals.finished.emit()


class _TaskHandle(QObject):
    def __init__(
        self, parent, task, on_success, on_error, overlay=None,
        keep_overlay_until_window=False,
    ):
        super().__init__(parent)
        self.owner = parent
        self.on_success = on_success
        self.on_error = on_error
        self.keep_overlay_until_window = keep_overlay_until_window
        self._callback_running = False
        self._worker_finished = False
        self.overlay = overlay or BusyOverlay(parent)
        self.worker = _BackgroundTask(task)
        self.worker.signals.succeeded.connect(self._succeed)
        self.worker.signals.failed.connect(self._fail)
        self.worker.signals.finished.connect(self._finish)

    def start(self):
        QThreadPool.globalInstance().start(self.worker)

    def _succeed(self, result):
        self._callback_running = True
        if self.keep_overlay_until_window:
            self.overlay._waiting_for_next_window = True
        else:
            self._dismiss_overlay()
        try:
            self.on_success(result)
        except Exception:
            self._report_error(traceback.format_exc())
        finally:
            self._dismiss_overlay()
            self._callback_running = False
            self._finalize_if_finished()

    def _fail(self, error):
        self._dismiss_overlay()
        self._callback_running = True
        try:
            self._report_error(error)
        finally:
            self._callback_running = False
            self._finalize_if_finished()

    def _report_error(self, error):
        if self.on_error is not None:
            try:
                self.on_error(error)
            except Exception:
                QMessageBox.critical(
                    self.owner,
                    "تعذرت المهمة",
                    "حدث خطأ أثناء عرض نتيجة المهمة:\n"
                    f"{traceback.format_exc()}",
                )
        else:
            QMessageBox.critical(
                self.owner, "تعذرت المهمة",
                f"حدث خطأ أثناء تنفيذ المهمة:\n{error}",
            )

    def _finish(self):
        self.overlay.close()
        self._worker_finished = True
        self._finalize_if_finished()

    def _finalize_if_finished(self):
        if self._worker_finished and not self._callback_running:
            self._clear_owner_task()
            self.deleteLater()

    def _dismiss_overlay(self):
        self.overlay.close()

    def _clear_owner_task(self):
        if getattr(self.owner, "_active_background_task", None) is self:
            self.owner._active_background_task = None


def start_background_task(
    parent, task, on_success, on_error=None, overlay=None,
    keep_overlay_until_window=False,
):
    """Run a non-UI task on Qt's thread pool and show a wait overlay."""
    active_task = getattr(parent, "_active_background_task", None)
    if active_task is not None:
        return False
    handle = _TaskHandle(
        parent,
        task,
        on_success,
        on_error,
        overlay,
        keep_overlay_until_window,
    )
    parent._active_background_task = handle
    handle.start()
    return True
