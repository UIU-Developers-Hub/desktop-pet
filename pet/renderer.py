"""Transparent sprite renderer and mouse interaction surface."""

from __future__ import annotations

import random
import time
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap, QRegion, QTransform
from PyQt6.QtWidgets import QApplication, QWidget

import config


FRAME_COUNTS = {
    "idle": 4,
    "talk": 4,
    "react": 4,
    "rest": 4,
    "actions": 4,
}

LOCAL_ACTIONS = {
    "idle": "idle.png",
    "talk": "talk.png",
    "react": "react.png",
    "rest": "rest.png",
    "actions": "actions.png",
}

CAT_PACK_ACTIONS = {
    "idle": "Idle.png",
    "talk": "Box3.png",
    "react": "Box3.png",
    "rest": "Box3.png",
}

STATE_COLORS = {
    "idle": QColor("#50d890"),
    "talk": QColor("#ff8fab"),
    "react": QColor("#bca7ff"),
    "rest": QColor("#a6a6a6"),
    "actions": QColor("#bca7ff"),
}

MASK_ALPHA_THRESHOLD = 8
MASK_PADDING_PIXELS = 1
IDLE_USER_BLINK_FRAME_INDEX = 2
IDLE_APP_BLINK_FRAME_INDEX = 4
IDLE_BLINK_MIN_SECONDS = 4.0
IDLE_BLINK_MAX_SECONDS = 9.0


class PetRenderer(QWidget):
    """Always-on-top pet sprite widget.

    The widget masks itself to the current frame's alpha channel so transparent
    pixels are click-through while the visible sprite remains draggable/clickable.
    """

    clicked = pyqtSignal()
    drag_started = pyqtSignal()
    drag_released = pyqtSignal()

    def __init__(self, sprite_dir: Path):
        super().__init__()
        self.sprite_dir = Path(sprite_dir)
        self.frame_size = config.SPRITE_FRAME_SIZE
        self.scale = config.SPRITE_SCALE
        self.state = "idle"
        self.frame_index = 0
        self._frames: dict[str, list[QPixmap]] = {}
        self._current_scaled: QPixmap | None = None
        self.loaded_source = "placeholder"
        self._drag_press_global: QPoint | None = None
        self._drag_offset = QPoint()
        self._drag_started = False
        self._is_flipped = False
        self._look_target_x: int | None = None
        self._idle_attention = "user"
        self._idle_blinking = False
        self._rng = random.Random()
        self._next_idle_blink_at = 0.0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(self.frame_size * self.scale, self.frame_size * self.scale)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.ensure_placeholder_sprites()
        self._load_frames()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.next_frame)
        self._timer.start(config.SPRITE_TICK_MS)
        self._schedule_next_idle_blink()
        self._update_mask()

    def ensure_placeholder_sprites(self) -> None:
        """Generate simple sprite strips so missing assets never block startup."""
        self.sprite_dir.mkdir(parents=True, exist_ok=True)
        for state, count in FRAME_COUNTS.items():
            path = self.sprite_dir / f"{state}.png"
            if path.exists():
                continue
            strip = QPixmap(self.frame_size * count, self.frame_size)
            strip.fill(QColor(0, 0, 0, 0))
            painter = QPainter(strip)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            base = STATE_COLORS[state]
            for index in range(count):
                x = index * self.frame_size
                bob = 1 if index % 2 else 0
                painter.fillRect(x + 8, 9 + bob, 16, 15, base)
                painter.fillRect(x + 6, 13 + bob, 4, 9, base.darker(110))
                painter.fillRect(x + 22, 13 + bob, 4, 9, base.darker(110))
                painter.fillRect(x + 10, 6 + bob, 5, 5, base.lighter(112))
                painter.fillRect(x + 17, 6 + bob, 5, 5, base.lighter(112))
                painter.fillRect(x + 12, 14 + bob, 3, 3, QColor("#102018"))
                painter.fillRect(x + 18, 14 + bob, 3, 3, QColor("#102018"))
                if state == "talk" and index % 2:
                    painter.fillRect(x + 14, 20 + bob, 5, 2, QColor("#102018"))
                else:
                    painter.fillRect(x + 14, 20 + bob, 4, 1, QColor("#102018"))
                painter.fillRect(x + 10, 25, 5, 3, base.darker(130))
                painter.fillRect(x + 18, 25, 5, 3, base.darker(130))
            painter.end()
            strip.save(str(path), "PNG")

    def _load_frames(self) -> None:
        """Load frames from local assets, fallback pack, or placeholders."""
        self._frames.clear()
        self._load_local_sprite_frames()
        self._load_cat_pack_frames()
        self._load_fallback_frames()

    def _load_local_sprite_frames(self) -> None:
        loaded_any = False
        for state, filename in LOCAL_ACTIONS.items():
            frames = self._load_strip(self.sprite_dir / filename)
            if not frames:
                continue
            self._frames[state] = frames
            loaded_any = True
        if loaded_any:
            self.loaded_source = "assets/sprites"

    def _load_cat_pack_frames(self) -> None:
        if not config.CAT_PACK_DIR.exists():
            return
        loaded_any = False
        for state, filename in CAT_PACK_ACTIONS.items():
            if state in self._frames:
                continue
            frames = self._load_strip(config.CAT_PACK_DIR / filename)
            if not frames:
                continue
            self._frames[state] = frames
            loaded_any = True
        if loaded_any and self.loaded_source == "placeholder":
            self.loaded_source = "CatPackFree"

    def _load_fallback_frames(self) -> None:
        for state in FRAME_COUNTS:
            if state in self._frames:
                continue
            frames = self._load_strip(self.sprite_dir / f"{state}.png")
            if frames:
                self._frames[state] = frames

    def _load_strip(self, path: Path) -> list[QPixmap]:
        """Load a horizontal sprite strip into individual pixmap frames."""
        strip = QPixmap(str(path))
        if strip.isNull():
            return []
        source_size = strip.height()
        if source_size not in (32, 64):
            source_size = self.frame_size
        frame_count = strip.width() // source_size
        if frame_count <= 0:
            return []
        frames = []
        for index in range(frame_count):
            frames.append(strip.copy(index * source_size, 0, source_size, source_size))
        return frames

    def set_state(self, state: str) -> None:
        """Switch animation state, falling back to idle when unavailable."""
        if state not in self._frames:
            state = "idle"
        if state != self.state:
            self.state = state
            self._idle_blinking = False
            self.frame_index = self._idle_pose_frame() if state == "idle" else 0
            if state == "idle":
                self._schedule_next_idle_blink()
            self._update_mask()
            self.update()

    def set_idle_attention(self, attention: str) -> None:
        """Choose whether the idle pose looks toward the user or active app."""
        if attention not in {"user", "app"}:
            attention = "user"
        if attention == self._idle_attention:
            return
        self._idle_attention = attention
        if self.state == "idle" and not self._idle_blinking:
            self.frame_index = self._idle_pose_frame()
            self._update_mask()
            self.update()

    def set_look_target(self, point: QPoint | None) -> None:
        """Update the horizontal target used to flip the sprite direction."""
        target_x = point.x() if point is not None else None
        if target_x == self._look_target_x:
            return
        self._look_target_x = target_x
        self._update_facing()

    def has_state(self, state: str) -> bool:
        return state in self._frames

    def available_states(self) -> list[str]:
        return sorted(self._frames)

    def next_frame(self) -> None:
        """Advance animation and refresh the alpha mask."""
        frames = self._frames.get(self.state) or self._frames.get("idle", [])
        if not frames:
            return
        if self.state == "idle":
            self._next_idle_frame(frames)
            self._update_mask()
            self.update()
            return
        self.frame_index = (self.frame_index + 1) % len(frames)
        self._update_mask()
        self.update()

    def _next_idle_frame(self, frames: list[QPixmap]) -> None:
        blink_frame_index = self._idle_blink_frame_index(frames)
        if blink_frame_index is None:
            self.frame_index = (self.frame_index + 1) % len(frames)
            return

        now = time.monotonic()
        if self._idle_blinking:
            self._idle_blinking = False
            self.frame_index = self._idle_pose_frame()
        elif now >= self._next_idle_blink_at:
            self._idle_blinking = True
            self.frame_index = blink_frame_index
            self._schedule_next_idle_blink()
        else:
            self.frame_index = self._idle_pose_frame()

    def _idle_blink_frame_index(self, frames: list[QPixmap]) -> int | None:
        if self._idle_attention == "app" and len(frames) > IDLE_APP_BLINK_FRAME_INDEX:
            return IDLE_APP_BLINK_FRAME_INDEX
        if len(frames) > IDLE_USER_BLINK_FRAME_INDEX:
            return IDLE_USER_BLINK_FRAME_INDEX
        return None

    def _idle_pose_frame(self) -> int:
        frames = self._frames.get("idle", [])
        if not frames:
            return 0
        preferred = 1 if self._idle_attention == "app" and len(frames) > 1 else 0
        return min(preferred, len(frames) - 1)

    def _schedule_next_idle_blink(self) -> None:
        self._next_idle_blink_at = time.monotonic() + self._rng.uniform(
            IDLE_BLINK_MIN_SECONDS,
            IDLE_BLINK_MAX_SECONDS,
        )

    def current_frame(self) -> QPixmap | None:
        """Return the current frame, transformed for facing direction."""
        frames = self._frames.get(self.state) or self._frames.get("idle", [])
        if not frames:
            return None
        frame = frames[self.frame_index % len(frames)]
        if self._is_flipped:
            frame = frame.transformed(QTransform().scale(-1, 1))
        return frame

    def _update_mask(self) -> None:
        """Rebuild the window mask from the visible pixels of the frame."""
        frame = self.current_frame()
        if frame is None:
            return
        self._current_scaled = frame.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        mask = self._alpha_region(self._current_scaled)
        if mask.isEmpty():
            self.clearMask()
            return
        self.setMask(mask)

    def _alpha_region(self, pixmap: QPixmap) -> QRegion:
        """Convert non-transparent pixels into a Qt region for hit testing."""
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        region = QRegion()
        padding = MASK_PADDING_PIXELS
        height = image.height()
        width = image.width()

        for y in range(height):
            run_start: int | None = None
            for x in range(width):
                alpha = image.pixelColor(x, y).alpha()
                if alpha > MASK_ALPHA_THRESHOLD:
                    if run_start is None:
                        run_start = x
                    continue
                if run_start is not None:
                    region = region.united(
                        self._padded_run(run_start, x, y, width, height, padding)
                    )
                    run_start = None

            if run_start is not None:
                region = region.united(
                    self._padded_run(run_start, width, y, width, height, padding)
                )

        return region

    @staticmethod
    def _padded_run(
        start: int,
        end: int,
        y: int,
        width: int,
        height: int,
        padding: int,
    ) -> QRegion:
        x = max(0, start - padding)
        rect_y = max(0, y - padding)
        rect_right = min(width, end + padding)
        rect_bottom = min(height, y + padding + 1)
        return QRegion(QRect(x, rect_y, rect_right - x, rect_bottom - rect_y))

    def paintEvent(self, event) -> None:
        del event
        frame = self._current_scaled or self.current_frame()
        if frame is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(0, 0, frame)
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_press_global = event.globalPosition().toPoint()
            self._drag_offset = self._drag_press_global - self.frameGeometry().topLeft()
            self._drag_started = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_press_global is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return

        global_pos = event.globalPosition().toPoint()
        if not self._drag_started:
            distance = (global_pos - self._drag_press_global).manhattanLength()
            if distance < config.DRAG_START_DISTANCE:
                event.accept()
                return
            self._drag_started = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.drag_started.emit()

        self.move(self._clamp_to_available_screen(global_pos - self._drag_offset))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_press_global is not None:
            was_dragging = self._drag_started
            self._drag_press_global = None
            self._drag_started = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            if was_dragging:
                self.drag_released.emit()
            else:
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _clamp_to_available_screen(self, top_left: QPoint) -> QPoint:
        """Keep the dragged sprite inside the active screen's available area."""
        center = QPoint(top_left.x() + self.width() // 2, top_left.y() + self.height() // 2)
        screen = QApplication.screenAt(center) or QApplication.primaryScreen()
        if screen is None:
            return top_left
        available = screen.availableGeometry()
        max_x = max(available.left(), available.right() - self.width())
        max_y = max(available.top(), available.bottom() - self.height() - config.SCREEN_MARGIN)
        x = min(max(top_left.x(), available.left()), max_x)
        y = min(max(top_left.y(), available.top()), max_y)
        return QPoint(x, y)

    def show_at_start_position(self) -> None:
        """Show the pet near the bottom-right of the primary screen."""
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else self.geometry()
        x = available.right() - self.width() - config.SCREEN_MARGIN
        y = available.bottom() - self.height() - config.SCREEN_MARGIN
        self.move(QPoint(max(available.left(), x), max(available.top(), y)))
        self.show()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._update_facing()

    def _check_side(self) -> None:
        self._update_facing()

    def _update_facing(self) -> None:
        widget_center_x = self.geometry().center().x()
        if self._look_target_x is not None:
            should_flip = self._look_target_x > widget_center_x
            if self._is_flipped != should_flip:
                self._is_flipped = should_flip
                self._update_mask()
                self.update()
            return

        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        if not screen:
            return

        screen_center_x = screen.availableGeometry().center().x()
        should_flip = widget_center_x < screen_center_x
        if self._is_flipped != should_flip:
            self._is_flipped = should_flip
            self._update_mask()
            self.update()
