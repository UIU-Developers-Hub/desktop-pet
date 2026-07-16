"""Microsoft voice output for pet messages."""

from __future__ import annotations

import html
import queue
import re
import threading

from data.settings_store import SettingsStore

try:  # Windows-only COM modules provided by pywin32.
    import pythoncom
    import win32com.client
except ImportError:  # pragma: no cover - lets the module import off Windows.
    pythoncom = None
    win32com = None


_MAX_SPEECH_CHARS = 900


class MicrosoftVoice:
    """Speak pet messages with the default Windows Microsoft voice."""

    def __init__(self, settings_store: SettingsStore):
        self.settings_store = settings_store
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._stopped = False

    def speak_if_enabled(self, text: str) -> None:
        """Queue text for speech when the user has enabled voice in Settings."""
        if self._stopped or pythoncom is None or win32com is None or not self._voice_enabled():
            return
        speech_text = clean_speech_text(text)
        if not speech_text:
            return
        self._ensure_worker()
        self._queue.put(speech_text)

    def stop(self) -> None:
        """Ask the background speech thread to exit."""
        self._stopped = True
        self._queue.put(None)

    def _voice_enabled(self) -> bool:
        try:
            return self.settings_store.load().voice_enabled
        except Exception:
            return False

    def _ensure_worker(self) -> None:
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run,
                name="MicrosoftVoice",
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        if pythoncom is None or win32com is None:
            return
        pythoncom.CoInitialize()
        try:
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            while True:
                text = self._queue.get()
                if text is None:
                    self._queue.task_done()
                    break
                try:
                    if self._voice_enabled():
                        voice.Speak(text)
                except Exception:
                    pass
                finally:
                    self._queue.task_done()
        finally:
            pythoncom.CoUninitialize()


def clean_speech_text(text: str) -> str:
    """Convert UI/Markdown-ish pet text into plain text for speech."""
    cleaned = str(text or "")
    cleaned = re.sub(r"```.*?```", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = "\n".join(
        line
        for line in cleaned.splitlines()
        if not line.strip().startswith(("TODO_JSON:", "MEMORY_JSON:"))
    )
    cleaned = re.sub(r"[*_>#~|]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", html.unescape(cleaned)).strip()
    if len(cleaned) > _MAX_SPEECH_CHARS:
        cleaned = f"{cleaned[: _MAX_SPEECH_CHARS - 3].rstrip()}..."
    return cleaned
