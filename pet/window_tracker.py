"""Win32 foreground-window geometry helpers for positioning UI."""

from __future__ import annotations

from dataclasses import dataclass

try:
    import win32con
    import win32gui
except ImportError:  # pragma: no cover - lets imports succeed off Windows.
    win32con = None
    win32gui = None


@dataclass(frozen=True)
class WindowRect:
    """Small immutable rectangle wrapper around a Win32 window handle."""

    hwnd: int
    left: int
    top: int
    right: int
    bottom: int
    class_name: str = ""

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


class WindowTracker:
    """Read visible window geometry while keeping Win32 imports optional."""

    def available(self) -> bool:
        """Return true when pywin32 is importable in this environment."""
        return win32gui is not None

    def list_windows(self) -> list[WindowRect]:
        """Return visible, non-minimized windows large enough to matter."""
        if win32gui is None:
            return []
        results: list[WindowRect] = []

        def collect(hwnd, _extra) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.IsIconic(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            class_name = win32gui.GetClassName(hwnd)
            if not title and class_name != "Shell_TrayWnd":
                return True
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            rect = WindowRect(hwnd, left, top, right, bottom, class_name)
            if rect.width < 80 or rect.height < 40:
                return True
            results.append(rect)
            return True

        win32gui.EnumWindows(collect, None)
        return results

    def get_foreground_window_rect(self) -> WindowRect | None:
        """Return the current foreground window bounds, if available."""
        if win32gui is None:
            return None
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return WindowRect(hwnd, left, top, right, bottom, win32gui.GetClassName(hwnd))

    def get_taskbar_rect(self) -> WindowRect | None:
        """Return the Windows taskbar bounds, if available."""
        if win32gui is None:
            return None
        hwnd = win32gui.FindWindow("Shell_TrayWnd", None)
        if not hwnd:
            return None
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return WindowRect(hwnd, left, top, right, bottom, "Shell_TrayWnd")

    def get_landing_rects(self) -> list[WindowRect]:
        """Return candidate rectangles that the pet can visually react to."""
        rects = self.list_windows()
        taskbar = self.get_taskbar_rect()
        if taskbar is not None:
            rects.append(taskbar)
        return rects
