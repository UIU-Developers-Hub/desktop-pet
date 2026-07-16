"""Small speech bubble shown beside the pet for proactive messages."""

from __future__ import annotations

from PyQt6.QtCore import (
    QPoint,
    QPropertyAnimation,
    QRect,
    QEasingCurve,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

import html
import markdown

import config

# Layout constants
_PADDING_H = 16
_PADDING_V = 12
_CORNER_RADIUS = 14
_TAIL_SIZE = 10
_MARGIN_FROM_PET = 8
_MAX_MESSAGE_LEN = 400

# Colors (matching the existing dark theme)
_BG_COLOR = QColor("#22232e")
_BORDER_COLOR = QColor("#33343f")
_TEXT_COLOR = QColor("#e0e0e4")
_ACCENT_COLOR = QColor("#50d890")


class MiniBubble(QWidget):
    """A small speech-bubble that appears next to the pet sprite.

    Emits ``clicked`` when the user clicks the bubble, so the caller can
    open the full ChatBubble UI.
    """

    clicked = pyqtSignal(str)  # carries the message text
    hidden = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._message = ""
        self._tail_on_right = False  # True ➜ tail points right (pet is to the right)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # --- internal label for text ---
        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        # The maximum width is set dynamically in show_message based on screen size
        self._label.setFont(self._make_font())
        self._label.setStyleSheet(f"color: {_TEXT_COLOR.name()}; background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            _PADDING_H + _TAIL_SIZE,  # extra left when tail is on the left
            _PADDING_V,
            _PADDING_H + _TAIL_SIZE,  # extra right when tail is on the right
            _PADDING_V,
        )
        layout.addWidget(self._label)

        # --- auto-hide timer ---
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._auto_hide)

        # --- fade-in animation ---
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(220)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def show_message(self, message: str, pet_rect: QRect) -> None:
        """Display *message* in a speech bubble adjacent to *pet_rect*."""
        self._hide_timer.stop()

        # Determine screen width to calculate max width (25% of screen width)
        screen = QApplication.screenAt(pet_rect.center()) or QApplication.primaryScreen()
        screen_width = screen.availableGeometry().width() if screen else 1920
        max_width = int(screen_width * 0.25)
        
        # Update label maximum width dynamically
        self._label.setMaximumWidth(max_width - 2 * _PADDING_H)

        # Truncate very long messages
        if len(message) > _MAX_MESSAGE_LEN:
            message = message[: _MAX_MESSAGE_LEN - 1].rstrip() + "…"
        self._message = message
        
        try:
            md_html = markdown.markdown(message, extensions=['fenced_code', 'nl2br', 'sane_lists', 'tables'])
        except Exception:
            md_html = html.escape(message).replace("\n", "<br>")
            
        self._label.setText(f"🐉 {md_html}")

        # Resize to fit content
        self._label.adjustSize()
        content_w = self._label.sizeHint().width() + 2 * _PADDING_H + _TAIL_SIZE
        content_h = self._label.sizeHint().height() + 2 * _PADDING_V
        self.setFixedSize(min(content_w, max_width + _TAIL_SIZE), content_h)

        # Position next to the pet
        self._position_near_pet(pet_rect)

        # Show with fade-in
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()

        # Auto-hide after timeout
        self._hide_timer.start(config.CHECKIN_DISPLAY_MS)

    def dismiss(self) -> None:
        """Immediately hide the bubble."""
        self._hide_timer.stop()
        was_visible = self.isVisible()
        self.hide()
        if was_visible:
            self.hidden.emit()

    # ------------------------------------------------------------------ #
    #  Positioning
    # ------------------------------------------------------------------ #

    def _position_near_pet(self, pet_rect: QRect) -> None:
        screen = QApplication.screenAt(pet_rect.center()) or QApplication.primaryScreen()
        if screen is None:
            self.move(pet_rect.left() - self.width() - _MARGIN_FROM_PET, pet_rect.top())
            self._tail_on_right = True
            return

        available = screen.availableGeometry()

        # Try placing to the LEFT of the pet first
        x_left = pet_rect.left() - self.width() - _MARGIN_FROM_PET
        # If that overflows the left screen edge, place to the RIGHT
        if x_left < available.left():
            x = pet_rect.right() + _MARGIN_FROM_PET
            self._tail_on_right = False  # tail on left side, pointing right→ pet
        else:
            x = x_left
            self._tail_on_right = True  # tail on right side, pointing left→ pet... wait

        # Clarify: _tail_on_right means the tail arrow is on the right side of the bubble
        # If bubble is to the LEFT of pet  → tail on right ✓
        # If bubble is to the RIGHT of pet → tail on left
        if x_left < available.left():
            self._tail_on_right = False
        else:
            self._tail_on_right = True

        # Vertically center on the pet
        y = pet_rect.center().y() - self.height() // 2
        y = max(available.top(), min(y, available.bottom() - self.height()))
        x = max(available.left(), min(x, available.right() - self.width()))

        self.move(x, y)

    # ------------------------------------------------------------------ #
    #  Painting — rounded rect + tail arrow
    # ------------------------------------------------------------------ #

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()
        tail = _TAIL_SIZE

        # Bubble body rect (inset from the tail side)
        if self._tail_on_right:
            body = QRect(0, 0, w - tail, h)
        else:
            body = QRect(tail, 0, w - tail, h)

        # Draw rounded-rect body
        path = QPainterPath()
        path.addRoundedRect(body.x() + 0.5, body.y() + 0.5,
                            body.width() - 1, body.height() - 1,
                            _CORNER_RADIUS, _CORNER_RADIUS)

        # Draw tail triangle
        cy = h // 2  # vertical center
        tail_path = QPainterPath()
        if self._tail_on_right:
            # Tail on right side pointing toward the pet (to the right)
            tx = body.right() - 1
            tail_path.moveTo(tx, cy - tail // 2)
            tail_path.lineTo(tx + tail, cy)
            tail_path.lineTo(tx, cy + tail // 2)
            tail_path.closeSubpath()
        else:
            # Tail on left side pointing toward the pet (to the left)
            tx = body.left() + 1
            tail_path.moveTo(tx, cy - tail // 2)
            tail_path.lineTo(tx - tail, cy)
            tail_path.lineTo(tx, cy + tail // 2)
            tail_path.closeSubpath()

        combined = path.united(tail_path)

        # Fill
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_BG_COLOR)
        painter.drawPath(combined)

        # Border
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_BORDER_COLOR, 1.2))
        painter.drawPath(combined)

        # Subtle accent line at top
        accent_path = QPainterPath()
        accent_path.addRoundedRect(body.x() + 1, body.y() + 1,
                                   body.width() - 2, 3,
                                   _CORNER_RADIUS, _CORNER_RADIUS)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_ACCENT_COLOR)
        painter.setOpacity(0.5)
        painter.drawPath(accent_path)

        painter.end()

    # ------------------------------------------------------------------ #
    #  Mouse events
    # ------------------------------------------------------------------ #

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._hide_timer.stop()
            was_visible = self.isVisible()
            self.hide()
            if was_visible:
                self.hidden.emit()
            self.clicked.emit(self._message)
            event.accept()
            return
        super().mousePressEvent(event)

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _auto_hide(self) -> None:
        # Fade out then hide
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self._on_fade_out_done)
        self._fade_anim.start()

    def _on_fade_out_done(self) -> None:
        was_visible = self.isVisible()
        self.hide()
        self.setWindowOpacity(1.0)
        if was_visible:
            self.hidden.emit()
        # Disconnect so we don't stack connections
        try:
            self._fade_anim.finished.disconnect(self._on_fade_out_done)
        except TypeError:
            pass

    @staticmethod
    def _make_font() -> QFont:
        font = QFont("Segoe UI Variable", 12)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font
