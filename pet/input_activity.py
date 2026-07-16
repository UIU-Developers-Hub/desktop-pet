"""Timestamp-only keyboard activity monitor for focus-sleep detection."""

from __future__ import annotations

import threading
import time
from collections import deque

try:
    import keyboard
except ImportError:  # pragma: no cover - requirements install supplies it.
    keyboard = None


IGNORED_KEYS = {
    "alt",
    "alt gr",
    "ctrl",
    "left alt",
    "left ctrl",
    "left shift",
    "left windows",
    "right alt",
    "right ctrl",
    "right shift",
    "right windows",
    "shift",
    "windows",
}


class InputActivityMonitor:
    """Keep recent key-down timestamps without recording typed content."""

    def __init__(self, max_history_seconds: int = 30):
        self.max_history_seconds = max(5, int(max_history_seconds))
        self._lock = threading.RLock()
        self._key_down_times: deque[float] = deque()
        self._hook = None

    def start(self) -> bool:
        """Install the global keyboard hook.

        Returns false when the dependency is unavailable or Windows blocks the
        hook, allowing the app to continue without focus-sleep behavior.
        """
        if keyboard is None:
            return False
        try:
            self._hook = keyboard.hook(self._handle_event, suppress=False)
            return True
        except Exception as exc:
            print(f"Could not start typing monitor: {exc}")
            return False

    def stop(self) -> None:
        """Remove the keyboard hook if it was installed."""
        if keyboard is None or self._hook is None:
            return
        try:
            keyboard.unhook(self._hook)
        except Exception:
            pass
        self._hook = None

    def keypresses_in_last(self, seconds: float) -> int:
        """Count non-modifier key-down events in the recent time window."""
        now = time.monotonic()
        cutoff = now - max(0.1, float(seconds))
        with self._lock:
            self._prune(now)
            return sum(1 for item in self._key_down_times if item >= cutoff)

    def seconds_since_last_keypress(self) -> float | None:
        """Return seconds since the last tracked key-down event."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if not self._key_down_times:
                return None
            return now - self._key_down_times[-1]

    def _handle_event(self, event) -> None:
        """Record key-down timestamps only; never record key names as content."""
        if getattr(event, "event_type", "") != "down":
            return
        key_name = str(getattr(event, "name", "") or "").strip().lower()
        if key_name in IGNORED_KEYS:
            return
        now = time.monotonic()
        with self._lock:
            self._key_down_times.append(now)
            self._prune(now)

    def _prune(self, now: float) -> None:
        cutoff = now - self.max_history_seconds
        while self._key_down_times and self._key_down_times[0] < cutoff:
            self._key_down_times.popleft()
