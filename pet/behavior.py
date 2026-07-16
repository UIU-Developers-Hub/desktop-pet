"""Pet behavior state machine for idle, talking, focus sleep, and nudges."""

from __future__ import annotations

import json
import random
import threading
import time

from PyQt6.QtCore import QObject, QPoint, QPointF, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

import config
from pet.renderer import PetRenderer
from pet.window_tracker import WindowTracker


CHECKIN_PROMPT = (
    "You are a tiny black dragon desktop pet. The user was typing intensely, "
    "then paused. Write exactly one brief, warm check-in question under 130 "
    "characters. Use todo/work context when helpful. Ask whether they completed "
    "the current task, need the next step, are tired, or want a small break. "
    "Do not ask about completed or archived todos as active work. "
    "Return only the question. No preamble, no bullets, no emoji."
)

IDLE_ATTENTION_MIN_SECONDS = 6.0
IDLE_ATTENTION_MAX_SECONDS = 14.0
IDLE_APP_WATCH_CHANCE = 0.55


class BehaviorEngine(QObject):
    """Coordinate renderer state, input activity, and proactive messages."""

    checkin_ready = pyqtSignal(str)

    def __init__(
        self,
        renderer: PetRenderer,
        window_tracker: WindowTracker,
        chat_bubble,
        input_monitor=None,
        mini_bubble=None,
    ):
        super().__init__()
        self.renderer = renderer
        self.window_tracker = window_tracker
        self.chat_bubble = chat_bubble
        self.input_monitor = input_monitor
        self.mini_bubble = mini_bubble
        self._chat_active = False
        self._popup_active = False
        self._talking = False
        self._dragging = False
        self._sleeping_for_focus = False
        self._quiet_wake_deadline: float | None = None
        self._idle_attention = "user"
        self._idle_look_target: QPoint | None = None
        self._next_idle_attention_at = 0.0
        self._last_checkin_at = 0.0
        self._checkin_inflight = False
        self._rng = random.Random()

        self.renderer.drag_started.connect(self.handle_drag_started)
        self.renderer.drag_released.connect(self.handle_drag_released)
        self.checkin_ready.connect(self._show_checkin)

        if self.mini_bubble is not None:
            self.mini_bubble.clicked.connect(self._mini_bubble_clicked)
            self.mini_bubble.hidden.connect(self._mini_bubble_hidden)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        """Start behavior ticks and place the renderer on the ground line."""
        self._timer.start(config.BEHAVIOR_TICK_MS)
        QTimer.singleShot(0, self._snap_to_bottom_right)

    def stop(self) -> None:
        self._timer.stop()

    def set_talking(self, talking: bool) -> None:
        self.set_chat_active(talking)

    def set_chat_active(self, active: bool) -> None:
        """Tell the behavior engine whether the full chat UI is open/active."""
        self._chat_active = active
        self._sync_visual_state()

    def _set_popup_active(self, active: bool) -> None:
        self._popup_active = active
        self._sync_visual_state()

    def _sync_visual_state(self) -> None:
        self._talking = self._chat_active or self._popup_active
        if self._dragging:
            return
        if self._talking:
            self._sleeping_for_focus = False
            self._quiet_wake_deadline = None
            self.renderer.set_look_target(None)
            self.renderer.set_state("talk")
        elif self._sleeping_for_focus:
            self.renderer.set_look_target(None)
            self.renderer.set_state("rest")
        else:
            self._apply_idle_attention()
            self.renderer.set_state("idle")

    def handle_due_task(self, task: dict) -> None:
        """React to a due task emitted by the scheduler."""
        if not config.PROACTIVE or self._is_intense_typing():
            return
        title = task.get("title", "A task")
        self._wake_and_say(f"Task due: {title}. Want to finish it now?")

    def handle_break_nudge(self, active_seconds: int) -> None:
        if not config.PROACTIVE or self._is_intense_typing():
            return
        minutes = max(1, round(active_seconds / 60))
        self._wake_and_say(f"You have been at it for about {minutes} minutes. Tired yet?")

    def handle_break_return(self) -> None:
        if not config.PROACTIVE or self._is_intense_typing():
            return
        self._wake_and_say("Nice reset. Are you ready for the next tiny step?")

    def handle_smart_nudge(self, nudge: dict) -> None:
        """Display an LLM or fallback nudge from the smart nudge engine."""
        if not config.PROACTIVE or self._is_intense_typing() or self._sleeping_for_focus:
            return
        message = str(nudge.get("message", "")).strip()
        if message:
            self._wake_and_say(message)

    def handle_drag_started(self) -> None:
        self._dragging = True
        self._sleeping_for_focus = False
        self._quiet_wake_deadline = None
        self.renderer.set_look_target(None)
        self.renderer.set_state("idle")

    def handle_drag_released(self) -> None:
        self._dragging = False
        target = self._ground_point_for_x(self.renderer.x())
        self.renderer.move(round(target.x()), round(target.y()))
        self._sync_visual_state()

    def _tick(self) -> None:
        """Advance focus-sleep and idle-attention state."""
        if self._dragging or self._talking:
            return

        if self._is_intense_typing():
            self._enter_focus_sleep()
            return

        if self._sleeping_for_focus:
            self.renderer.set_state("rest")
            if self._had_recent_keypress():
                self._quiet_wake_deadline = None
                return
            if self._quiet_wake_deadline is None:
                delay = self._rng.uniform(
                    config.WAKE_CHECKIN_MIN_SECONDS,
                    config.WAKE_CHECKIN_MAX_SECONDS,
                )
                self._quiet_wake_deadline = time.monotonic() + delay
            if time.monotonic() >= self._quiet_wake_deadline:
                self._sleeping_for_focus = False
                self._quiet_wake_deadline = None
                self._start_checkin()
            return

        self._apply_idle_attention()
        self.renderer.set_state("idle")

    def _enter_focus_sleep(self) -> None:
        self._sleeping_for_focus = True
        self._quiet_wake_deadline = None
        self.renderer.set_look_target(None)
        self.renderer.set_state("rest")

    def _is_intense_typing(self) -> bool:
        if self.input_monitor is None:
            return False
        return (
            self.input_monitor.keypresses_in_last(config.TYPING_ACTIVITY_WINDOW_SECONDS)
            >= config.INTENSE_TYPING_KEY_THRESHOLD
        )

    def _had_recent_keypress(self) -> bool:
        if self.input_monitor is None:
            return False
        return self.input_monitor.keypresses_in_last(1.0) > 0

    def _start_checkin(self) -> None:
        """Start one wake check-in call, respecting cooldown/in-flight guards."""
        now = time.monotonic()
        if self._checkin_inflight:
            return
        if now - self._last_checkin_at < config.WAKE_CHECKIN_COOLDOWN_SECONDS:
            self._apply_idle_attention()
            self.renderer.set_state("idle")
            return
        self._last_checkin_at = now
        self._checkin_inflight = True
        self._apply_idle_attention()
        self.renderer.set_state("idle")
        thread = threading.Thread(target=self._run_checkin_llm, name="WakeCheckinLLM", daemon=True)
        thread.start()

    def _run_checkin_llm(self) -> None:
        """Build check-in context and ask the routed model off the UI thread."""
        try:
            message = self._fallback_checkin_question()
            device = self._device_snapshot()
            route = self.chat_bubble.llm_client.resolve_provider("auto", device)
            if (
                config.LLM_DEVICE_GATING
                and device is not None
                and not device.safe_for_llm
                and route == "local"
            ):
                self.checkin_ready.emit(message)
                return

            reply = self.chat_bubble.llm_client.chat(
                self._build_checkin_messages(device),
                provider="auto",
                device_snapshot=device,
            )
            cleaned = self._clean_question(reply)
            self.checkin_ready.emit(cleaned or message)
        finally:
            self._checkin_inflight = False

    def _build_checkin_messages(self, device_snapshot) -> list[dict[str, str]]:
        """Build privacy-safe context for a short wake check-in question."""
        context = {
            "local_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "open_todos": self.chat_bubble.todo_store.open_tasks_summary(6),
            "recently_completed_todos": self.chat_bubble.todo_store.recent_completed_summary(6),
            "todo_counts": self.chat_bubble.todo_store.task_counts(),
            "work_summary": self.chat_bubble.work_tracker.summary_text(),
            "device_summary": (
                device_snapshot.summary_text()
                if device_snapshot is not None
                else "Device telemetry is unavailable."
            ),
        }
        return [
            {"role": "system", "content": CHECKIN_PROMPT},
            {"role": "user", "content": json.dumps(context, separators=(",", ":"))},
        ]

    def _device_snapshot(self):
        if getattr(self.chat_bubble, "device_monitor", None) is None:
            return None
        return self.chat_bubble.device_monitor.snapshot()

    def _fallback_checkin_question(self) -> str:
        """Return a local question when the model is unavailable or gated."""
        tasks = self.chat_bubble.todo_store.list_open_tasks(1)
        if tasks:
            title = str(tasks[0].get("title", "that task")).strip()
            if len(title) > 54:
                title = f"{title[:51]}..."
            return f"Did you finish \"{title}\", or do you need a smaller next step?"
        return "You paused. How are you doing: still focused, tired, or ready for a tiny break?"

    def _clean_question(self, reply: str) -> str:
        text = str(reply or "").strip()
        if not text:
            return ""
        blocked_prefixes = (
            "i cannot reach",
            "local cpu/ram",
            "ollama cloud is not configured",
            "local ollama returned",
            "ollama returned",
        )
        if text.lower().startswith(blocked_prefixes):
            return ""
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        first_line = first_line.strip("\"' ")
        if len(first_line) > 160:
            first_line = first_line[:157].rstrip() + "..."
        if first_line and not first_line.endswith("?"):
            first_line = f"{first_line}?"
        return first_line

    def _show_checkin(self, message: str) -> None:
        if self._dragging:
            return
        self._wake_and_say(message)

    def _wake_and_say(self, message: str) -> None:
        self._sleeping_for_focus = False
        self._quiet_wake_deadline = None
        self._set_popup_active(True)
        # Record the message in the transcript (full UI stays hidden)
        self.chat_bubble.show_message(message)
        # Show the lightweight mini-bubble next to the pet sprite
        if self.mini_bubble is not None:
            self.mini_bubble.show_message(message, self.renderer.geometry())
        else:
            QTimer.singleShot(config.CHECKIN_DISPLAY_MS, lambda: self._set_popup_active(False))

    def _mini_bubble_clicked(self, _message: str) -> None:
        """Open the full ChatBubble UI when the user clicks the mini bubble."""
        self._set_popup_active(False)
        self.set_chat_active(True)
        self.chat_bubble.show_near_pet(self.renderer.geometry())
        self.chat_bubble.focus_input()

    def _mini_bubble_hidden(self) -> None:
        self._set_popup_active(False)

    def _apply_idle_attention(self) -> None:
        now = time.monotonic()
        if now >= self._next_idle_attention_at:
            self._choose_next_idle_attention(now)
        self.renderer.set_idle_attention(self._idle_attention)
        self.renderer.set_look_target(self._idle_look_target)

    def _choose_next_idle_attention(self, now: float) -> None:
        target = self._active_window_look_target()
        if target is not None and self._rng.random() < IDLE_APP_WATCH_CHANCE:
            self._idle_attention = "app"
            self._idle_look_target = target
        else:
            self._idle_attention = "user"
            self._idle_look_target = None
        self._next_idle_attention_at = now + self._rng.uniform(
            IDLE_ATTENTION_MIN_SECONDS,
            IDLE_ATTENTION_MAX_SECONDS,
        )

    def _active_window_look_target(self) -> QPoint | None:
        rect = self.window_tracker.get_foreground_window_rect()
        if rect is None or rect.width < 260 or rect.height < 180:
            return None
        return QPoint((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)

    def _snap_to_bottom_right(self) -> None:
        point = self._bottom_right_point()
        self.renderer.move(point)

    def _bottom_right_point(self) -> QPoint:
        available = self._available_geometry()
        x = available.right() - self.renderer.width() - config.SCREEN_MARGIN
        y = available.bottom() - self.renderer.height() - config.SCREEN_MARGIN
        return QPoint(max(available.left(), x), max(available.top(), y))

    def _ground_point_for_x(self, x: float) -> QPointF:
        available = self._available_geometry()
        left = available.left() + config.SCREEN_MARGIN
        right = max(left, available.right() - self.renderer.width())
        ground_y = max(available.top(), available.bottom() - self.renderer.height() - config.SCREEN_MARGIN)
        return QPointF(min(max(x, left), right), ground_y)

    def _available_geometry(self):
        center = self.renderer.geometry().center()
        screen = QApplication.screenAt(center) or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else self.renderer.geometry()
