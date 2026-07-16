"""Application entry point and dependency wiring for Desktop Pet."""

import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import config
from ai.llm_client import OllamaClient
from data.memory_store import MemoryStore
from data.scheduler import Scheduler
from data.settings_store import SettingsStore
from data.todo_store import TodoStore
from data.work_store import WorkStore
from pet.behavior import BehaviorEngine
from pet.chat_bubble import ChatBubble
from pet.device_monitor import DeviceMonitor
from pet.hotkeys import GlobalHotkey
from pet.input_activity import InputActivityMonitor
from pet.mini_bubble import MiniBubble
from pet.renderer import PetRenderer
from pet.smart_nudge import SmartNudgeEngine
from pet.voice import MicrosoftVoice
from pet.window_tracker import WindowTracker
from pet.work_tracker import WorkTracker


def make_tray_icon() -> QIcon:
    """Build a tray icon from the active sprite assets, with a tiny fallback."""
    for sprite_path in (config.SPRITE_DIR / "idle.png", config.CAT_PACK_DIR / "Idle.png"):
        sprite_strip = QPixmap(str(sprite_path))
        if not sprite_strip.isNull():
            frame_size = sprite_strip.height()
            frame = sprite_strip.copy(0, 0, frame_size, frame_size)
            return QIcon(
                frame.scaled(
                    32,
                    32,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
            )

    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.fillRect(8, 10, 16, 14, QColor("#50d890"))
    painter.fillRect(11, 7, 4, 4, QColor("#50d890"))
    painter.fillRect(17, 7, 4, 4, QColor("#50d890"))
    painter.fillRect(12, 14, 3, 3, QColor("#102018"))
    painter.fillRect(18, 14, 3, 3, QColor("#102018"))
    painter.end()
    return QIcon(pixmap)


def main() -> int:
    """Start the Qt tray application and connect all runtime services."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.SPRITE_DIR.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    todo_store = TodoStore(config.DB_PATH)
    work_store = WorkStore(config.DB_PATH)
    memory_store = MemoryStore(config.DB_PATH)
    settings_store = SettingsStore(config.SETTINGS_PATH)
    llm_client = OllamaClient(settings_store)
    voice = MicrosoftVoice(settings_store)
    window_tracker = WindowTracker()
    work_tracker = WorkTracker(work_store)
    device_monitor = DeviceMonitor()
    input_monitor = InputActivityMonitor()

    renderer = PetRenderer(config.SPRITE_DIR)
    chat_bubble = ChatBubble(
        llm_client,
        todo_store,
        work_tracker,
        window_tracker,
        device_monitor,
        memory_store,
        voice,
    )
    mini_bubble = MiniBubble()
    behavior = BehaviorEngine(renderer, window_tracker, chat_bubble, input_monitor, mini_bubble)
    scheduler = Scheduler(todo_store, work_tracker)
    smart_nudge = SmartNudgeEngine(todo_store, work_tracker, device_monitor, llm_client)
    hotkey = GlobalHotkey(config.HOTKEY)

    def show_chat() -> None:
        mini_bubble.dismiss()  # hide the mini bubble when full UI opens
        behavior.set_chat_active(True)
        chat_bubble.show_near_pet(renderer.geometry())
        chat_bubble.focus_input()

    def stop_talking_later() -> None:
        QTimer.singleShot(800, lambda: behavior.set_chat_active(chat_bubble.isVisible()))

    def ask_work_time() -> None:
        mini_bubble.dismiss()
        behavior.set_chat_active(True)
        chat_bubble.ask("How long have I worked today?")
        chat_bubble.focus_input()

    renderer.clicked.connect(show_chat)
    chat_bubble.closed.connect(stop_talking_later)
    hotkey.activated.connect(show_chat)
    scheduler.due_task.connect(behavior.handle_due_task)
    scheduler.break_nudge.connect(behavior.handle_break_nudge)
    scheduler.break_return.connect(behavior.handle_break_return)
    smart_nudge.nudge_ready.connect(behavior.handle_smart_nudge)

    tray = QSystemTrayIcon(make_tray_icon(), app)
    tray.setToolTip(f"{config.APP_NAME} - {config.HOTKEY}")

    menu = QMenu()
    open_action = QAction("Open chat", menu)
    open_action.triggered.connect(show_chat)
    worked_action = QAction("Ask work time", menu)
    worked_action.triggered.connect(ask_work_time)
    proactive_action = QAction(f"Proactive: {'on' if config.PROACTIVE else 'off'}", menu)
    proactive_action.setEnabled(False)
    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(open_action)
    menu.addAction(worked_action)
    menu.addSeparator()
    menu.addAction(proactive_action)
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: show_chat()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    tray.show()

    work_tracker.start()
    input_monitor_started = input_monitor.start()
    renderer.show_at_start_position()
    behavior.start()
    scheduler.start()
    smart_nudge.start()

    if not hotkey.start():
        tray.showMessage(
            config.APP_NAME,
            f"Could not register {config.HOTKEY}. The tray menu still works.",
            QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )
    if not input_monitor_started:
        tray.showMessage(
            config.APP_NAME,
            "Typing monitor could not start, so focus-sleep wakeups are disabled.",
            QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )

    def cleanup() -> None:
        """Stop hooks, timers, and background workers before Qt exits."""
        hotkey.stop()
        smart_nudge.stop()
        scheduler.stop()
        behavior.stop()
        voice.stop()
        input_monitor.stop()
        work_tracker.stop()

    app.aboutToQuit.connect(cleanup)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
