"""Local work-session tracker based on Windows idle time and app buckets."""

from __future__ import annotations

import ctypes
import threading
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

import config
from data.work_store import WorkStore

try:
    import win32gui
except ImportError:  # pragma: no cover - lets imports succeed off Windows.
    win32gui = None


@dataclass(frozen=True)
class WorkSnapshot:
    """Current work state exposed to schedulers, UI, and LLM prompts."""

    today_active_seconds: int
    current_streak_seconds: int
    current_bucket: str
    is_idle: bool
    last_break_at: str | None
    current_session_started_at: str | None


class LASTINPUTINFO(ctypes.Structure):
    """ctypes shape required by the Win32 `GetLastInputInfo` API."""

    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class WorkTracker:
    """Poll user idle state and persist completed active-work sessions.

    Privacy boundary: this tracker converts foreground windows into coarse app
    buckets before storing or exposing them. Do not persist raw titles here.
    """

    def __init__(self, store: WorkStore):
        self.store = store
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._session: dict | None = None
        self._last_poll_at: datetime | None = None
        self._idle_started_at: datetime | None = None
        self._last_break_at: datetime | None = None
        self._break_resume_pending = False
        self._current_bucket = "other"
        self._is_idle = True

    def start(self) -> None:
        """Start the background polling thread if it is not already running."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="WorkTracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop polling and flush any active session to storage."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        with self._lock:
            if self._session is not None:
                self._flush_session(datetime.now().astimezone(), idle_seconds=0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:  # pragma: no cover - defensive for tray app.
                print(f"Work tracker poll failed: {exc}")
            self._stop_event.wait(config.POLL_INTERVAL_SECONDS)

    def _poll_once(self) -> None:
        """Update the in-memory session state from one idle/window sample."""
        now = datetime.now().astimezone()
        idle_for = get_idle_seconds()
        working = idle_for <= config.IDLE_THRESHOLD_SECONDS
        bucket = foreground_bucket()

        with self._lock:
            if self._last_poll_at is None:
                delta = 0
            else:
                delta = int(max(0, min((now - self._last_poll_at).total_seconds(), config.POLL_INTERVAL_SECONDS * 3)))
            self._last_poll_at = now
            self._current_bucket = bucket

            if working:
                if self._idle_started_at is not None:
                    idle_span = (now - self._idle_started_at).total_seconds()
                    if idle_span >= config.BREAK_THRESHOLD_SECONDS:
                        self._last_break_at = now
                        self._break_resume_pending = True
                    self._idle_started_at = None
                if self._session is None:
                    self._session = {
                        "started_at": now,
                        "active_seconds": 0,
                        "bucket_seconds": Counter(),
                    }
                self._session["active_seconds"] += delta
                if delta > 0:
                    self._session["bucket_seconds"][bucket] += delta
                self._is_idle = False
                return

            if self._session is not None:
                ended_at = now - timedelta(seconds=max(0, idle_for - config.IDLE_THRESHOLD_SECONDS))
                self._flush_session(ended_at, idle_seconds=int(idle_for))
            if self._idle_started_at is None:
                self._idle_started_at = now - timedelta(seconds=int(idle_for))
            self._is_idle = True

    def _flush_session(self, ended_at: datetime, idle_seconds: int) -> None:
        """Persist the current session, if it accumulated active time."""
        if self._session is None:
            return
        active_seconds = int(self._session["active_seconds"])
        if active_seconds > 0:
            self.store.add_session(
                started_at=self._session["started_at"],
                ended_at=ended_at,
                active_seconds=active_seconds,
                idle_seconds=max(0, idle_seconds),
                foreground_app_summary=summarize_buckets(self._session["bucket_seconds"]),
            )
        self._session = None

    def snapshot(self) -> WorkSnapshot:
        """Return a thread-safe summary of stored and in-progress work time."""
        with self._lock:
            current_active = int(self._session["active_seconds"]) if self._session else 0
            started_at = self._session["started_at"].isoformat(timespec="seconds") if self._session else None
            last_break = self._last_break_at.isoformat(timespec="seconds") if self._last_break_at else None
            return WorkSnapshot(
                today_active_seconds=self.store.get_today_active_seconds() + current_active,
                current_streak_seconds=current_active,
                current_bucket=self._current_bucket,
                is_idle=self._is_idle,
                last_break_at=last_break,
                current_session_started_at=started_at,
            )

    def summary_text(self) -> str:
        """Return a compact work summary suitable for chat context."""
        snap = self.snapshot()
        last_break = snap.last_break_at or "not yet this run"
        return (
            f"Today active: {format_duration(snap.today_active_seconds)}. "
            f"Current streak: {format_duration(snap.current_streak_seconds)}. "
            f"Current app bucket: {snap.current_bucket}. "
            f"Last break: {last_break}."
        )

    def recent_summary_text(self, days: int = 7) -> str:
        """Return a compact recent work-pattern summary from stored sessions."""
        return self.store.recent_summary_text(days)

    def consume_break_resume_event(self) -> bool:
        """Return and clear the pending break-resume signal."""
        with self._lock:
            if not self._break_resume_pending:
                return False
            self._break_resume_pending = False
            return True


def get_idle_seconds() -> int:
    """Return seconds since last user input, or zero if Win32 access fails."""
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0
        tick_count = ctypes.windll.kernel32.GetTickCount()
        elapsed_ms = (tick_count - lii.dwTime) & 0xFFFFFFFF
        return int(elapsed_ms / 1000)
    except Exception:
        return 0


def foreground_bucket() -> str:
    """Classify the current foreground window into a privacy-safe bucket."""
    if win32gui is None:
        return "other"
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return "other"
    title = win32gui.GetWindowText(hwnd) or ""
    class_name = win32gui.GetClassName(hwnd) or ""
    return bucket_for_window(title, class_name)


def bucket_for_window(title: str, class_name: str) -> str:
    """Map a raw title/class pair to a coarse app bucket.

    Callers should use the returned bucket and avoid persisting the raw inputs.
    """
    text = f"{title} {class_name}".lower()
    allowlist = [
        ("social media", ("facebook", "instagram", "twitter", "x.com", "tiktok", "reddit", "threads", "linkedin")),
        ("media", ("spotify", "vlc", "youtube", "netflix", "twitch", "media player")),
        ("IDE", ("visual studio code", "pycharm", "intellij", "visual studio", "sublime", "notepad++")),
        ("terminal", ("powershell", "command prompt", "windows terminal", "cmd.exe", "consolewindowclass")),
        ("chat app", ("slack", "discord", "teams", "telegram", "whatsapp", "signal")),
        ("office", ("word", "excel", "powerpoint", "onenote", "acrobat")),
        ("design app", ("figma", "photoshop", "illustrator", "blender", "affinity")),
        ("file manager", ("file explorer", "cabinetwclass", "explorer")),
        ("game", ("steam", "epic games", "unity", "unreal")),
        ("system", ("settings", "control panel", "task manager")),
        ("browser", ("chrome", "edge", "firefox", "brave", "opera", "chromium")),
    ]
    for bucket, needles in allowlist:
        if any(needle in text for needle in needles):
            return bucket
    return "other"


def summarize_buckets(counter: Counter) -> str:
    """Convert accumulated bucket seconds into a compact percentage summary."""
    total = sum(counter.values())
    if total <= 0:
        return "other"
    parts = []
    for bucket, seconds in counter.most_common(4):
        percent = round((seconds / total) * 100)
        parts.append(f"{bucket} {percent}%")
    return ", ".join(parts)


def format_duration(seconds: int) -> str:
    """Format seconds as a short hours/minutes string."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
