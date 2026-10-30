"""A hidden, reusable A4 page widget for composing cards and exporting them to PDF."""
from pathlib import Path

from PySide6.QtCore import QPoint, QSize, QMarginsF
from PySide6.QtGui import (
    QColor,
    QPageLayout,
    QPageSize,
    QPainter,
    QPalette,
    QPdfWriter,
)
from PySide6.QtWidgets import QVBoxLayout, QWidget


class A4PageWidget(QWidget):
    """Compose QWidget cards on an A4 page and render the page to a PDF."""

    DPI = 96
    PAGE_WIDTH_MM = 210
    PAGE_HEIGHT_MM = 297

    def __init__(
        self,
        orientation=QPageLayout.Orientation.Portrait,
        margins_mm=(10, 10, 10, 10),
        spacing=8,
        parent=None,
    ):
        super().__init__(parent)
        self._margins_mm = tuple(float(value) for value in margins_mm)
        if len(self._margins_mm) != 4 or any(
            value < 0 for value in self._margins_mm
        ):
            raise ValueError("margins_mm must contain four non-negative values.")
        if spacing < 0:
            raise ValueError("spacing must be non-negative.")

        self.cards_layout = QVBoxLayout(self)
        self.cards_layout.setContentsMargins(
            *(self._mm_to_pixels(value) for value in self._margins_mm)
        )
        self.cards_layout.setSpacing(spacing)

        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("white"))
        self.setPalette(palette)
        self.set_orientation(orientation)

    @staticmethod
    def _mm_to_pixels(value):
        return round(value * A4PageWidget.DPI / 25.4)

    @staticmethod
    def _normalize_orientation(orientation):
        if isinstance(orientation, str):
            normalized = orientation.strip().lower()
            if normalized == "portrait":
                return QPageLayout.Orientation.Portrait
            if normalized == "landscape":
                return QPageLayout.Orientation.Landscape
        elif orientation in (
            QPageLayout.Orientation.Portrait,
            QPageLayout.Orientation.Landscape,
        ):
            return orientation
        raise ValueError("orientation must be 'portrait' or 'landscape'.")

    def set_orientation(self, orientation):
        """Set the page direction to portrait or landscape."""
        self.orientation = self._normalize_orientation(orientation)
        width_mm, height_mm = self.PAGE_WIDTH_MM, self.PAGE_HEIGHT_MM
        if self.orientation == QPageLayout.Orientation.Landscape:
            width_mm, height_mm = height_mm, width_mm
        self.setFixedSize(
            QSize(self._mm_to_pixels(width_mm), self._mm_to_pixels(height_mm))
        )

    def add_card(self, card):
        """Add a QWidget card to the page in layout order."""
        if not isinstance(card, QWidget):
            raise TypeError("card must be a QWidget.")
        self.cards_layout.addWidget(card)

    def clear_cards(self):
        """Remove all cards without deleting widgets owned by the caller."""
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

    def set_cards(self, cards):
        """Replace the page cards with the widgets in ``cards``."""
        cards = list(cards)
        if any(not isinstance(card, QWidget) for card in cards):
            raise TypeError("Every card must be a QWidget.")
        self.clear_cards()
        for card in cards:
            self.add_card(card)

    def save_pdf(self, destination):
        """Render this page to a single-page A4 PDF and return its path."""
        pdf_path = Path(destination)
        if not str(destination).strip():
            raise ValueError("A PDF destination path is required.")

        writer = QPdfWriter(str(pdf_path))
        writer.setResolution(self.DPI)
        page_layout = QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            self.orientation,
            QMarginsF(0, 0, 0, 0),
            QPageLayout.Unit.Millimeter,
        )
        if not writer.setPageLayout(page_layout):
            raise RuntimeError("Could not configure the writer for A4 PDF output.")

        painter = QPainter()
        if not painter.begin(writer):
            raise RuntimeError(f"Could not start PDF rendering for: {pdf_path}")
        try:
            painter.setWindow(0, 0, self.width(), self.height())
            self.render(painter, QPoint(0, 0))
        finally:
            painter.end()

        if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
            raise RuntimeError(f"PDF output was not created: {pdf_path}")
        return pdf_path
