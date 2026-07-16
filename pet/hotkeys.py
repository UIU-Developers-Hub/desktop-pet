"""Global hotkey wrapper around the optional `keyboard` dependency."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

try:
    import keyboard
except ImportError:  # pragma: no cover - requirements install supplies it.
    keyboard = None


class GlobalHotkey(QObject):
    """Register one global hotkey and re-emit it as a Qt signal."""

    activated = pyqtSignal()

    def __init__(self, combo: str):
        super().__init__()
        self.combo = combo
        self._handler = None

    def start(self) -> bool:
        """Register the configured hotkey, returning false on failure."""
        if keyboard is None:
            return False
        try:
            self._handler = keyboard.add_hotkey(self.combo, lambda: self.activated.emit())
            return True
        except Exception as exc:
            print(f"Could not register global hotkey {self.combo}: {exc}")
            return False

    def stop(self) -> None:
        """Unregister the hotkey if it was registered."""
        if keyboard is None or self._handler is None:
            return
        try:
            keyboard.remove_hotkey(self._handler)
        except Exception:
            pass
        self._handler = None
