"""Chat, task planner, and archive panel shown from the desktop pet."""

from __future__ import annotations

import html
import json
import re
import threading
from datetime import datetime
import socket
import os
import markdown

from PyQt6.QtCore import QDateTime, QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import config
from ai.llm_client import OllamaClient
from data.memory_store import MemoryStore
from data.settings_store import normalize_provider
from data.todo_store import TodoStore, normalize_priority
from pet.device_monitor import DeviceMonitor, DeviceSnapshot
from pet.settings_dialog import SettingsDialog
from pet.voice import MicrosoftVoice
from pet.window_tracker import WindowTracker


SYSTEM_PROMPT = (
    "You are a concise, friendly planning assistant inside a desktop pet. "
    "Help the user turn work into clear next actions. Use the todo and work summaries when helpful. "
    "If the user asks to add a todo, reminder, task, or plan item, end with exactly "
    'TODO_JSON: {"title":"...","due_at":"YYYY-MM-DDTHH:MM:SS","priority":"high|normal|low","notes":"..."} '
    "Use null for due_at and an empty string for notes when either does not apply. "
    "Create TODO_JSON only for explicit future tracking requests; do not create todos for casual mentions "
    "of sleep, rest, or already-completed work. "
    "If the user says a todo is finished, done, fixed, completed, no longer needed, or should stop being mentioned, "
    "end with exactly "
    'DONE_JSON: {"task_id":123,"title":"...","all_related":true} '
    "Use null for task_id when the user names the task instead of an id. "
    "To preserve context efficiently, optionally end with exactly "
    'MEMORY_JSON: {"memories":[{"kind":"preference|project|work_style|profile|instruction|context","summary":"...","confidence":0.7}],"rollup":"..."} '
    "Use MEMORY_JSON only for durable preferences, project plans, working style, recurring needs, "
    "or important ongoing context. Keep memories under 180 characters and the rollup under 700 characters. "
    "Never save a completed todo as an active memory. Treat completed or archived todos as historical only. "
    "Do not store secrets, API keys, passwords, private file contents, or raw chat transcripts. "
    "If multiple structured lines are needed, put TODO_JSON, DONE_JSON, and MEMORY_JSON on separate final lines."
)


class TaskItemWidget(QWidget):
    """One row in the open-task or archive list."""

    def __init__(self, task: dict, is_archive: bool, parent_bubble: ChatBubble):
        super().__init__()
        self.task = task
        self.is_archive = is_archive
        self.parent_bubble = parent_bubble
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        
        label_text = parent_bubble._task_label(task)
        self.label = QLabel(label_text, self)
        self.label.setStyleSheet("background: transparent;")
        self.label.setWordWrap(True)
        if not is_archive:
            due_state = parent_bubble._due_state(task)
            if due_state == "overdue":
                self.label.setStyleSheet("color: #ff8a8a; background: transparent;")
            elif task.get("priority") == "high":
                self.label.setStyleSheet("color: #50d890; background: transparent;")
        else:
            self.label.setStyleSheet("color: #6e737a; background: transparent;")
            
        layout.addWidget(self.label, 1)
        
        self.btn_widget = QWidget(self)
        sp = self.btn_widget.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.btn_widget.setSizePolicy(sp)
        self.btn_layout = QHBoxLayout(self.btn_widget)
        self.btn_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_layout.setSpacing(6)
        
        if not is_archive:
            self.btn_done = QPushButton("✓", self.btn_widget)
            self.btn_done.setObjectName("actionBtnDone")
            self.btn_done.setFixedSize(32, 32)
            self.btn_done.setToolTip("Done")
            self.btn_done.clicked.connect(self._on_done)
            
            self.btn_delete = QPushButton("✕", self.btn_widget)
            self.btn_delete.setObjectName("actionBtnDelete")
            self.btn_delete.setFixedSize(32, 32)
            self.btn_delete.setToolTip("Delete")
            self.btn_delete.clicked.connect(self._on_delete)
            
            self.btn_layout.addWidget(self.btn_done)
            self.btn_layout.addWidget(self.btn_delete)
        else:
            self.btn_restore = QPushButton("↺", self.btn_widget)
            self.btn_restore.setObjectName("actionBtnRestore")
            self.btn_restore.setFixedSize(32, 32)
            self.btn_restore.setToolTip("Restore")
            self.btn_restore.clicked.connect(self._on_restore)
            
            self.btn_delete = QPushButton("✕", self.btn_widget)
            self.btn_delete.setObjectName("actionBtnDelete")
            self.btn_delete.setFixedSize(32, 32)
            self.btn_delete.setToolTip("Delete Permanently")
            self.btn_delete.clicked.connect(self._on_archive_delete)
            
            self.btn_layout.addWidget(self.btn_restore)
            self.btn_layout.addWidget(self.btn_delete)
            
        self.btn_widget.setVisible(False)
        layout.addWidget(self.btn_widget)

    def enterEvent(self, event):
        self.btn_widget.setVisible(True)
        super().enterEvent(event)
        
    def leaveEvent(self, event):
        self.btn_widget.setVisible(False)
        super().leaveEvent(event)
        
    def _on_done(self):
        self.parent_bubble.todo_store.mark_done(int(self.task["id"]))
        self.parent_bubble._forget_task_context(self.task)
        self.parent_bubble._append("Planner", f"Completed #{self.task['id']}: {self.task['title']}")
        self.parent_bubble._refresh_planner()

    def _on_delete(self):
        self.parent_bubble.todo_store.delete_task(int(self.task["id"]))
        self.parent_bubble._forget_task_context(self.task)
        self.parent_bubble._append("Planner", f"Deleted #{self.task['id']}: {self.task['title']}")
        self.parent_bubble._refresh_planner()
        
    def _on_restore(self):
        self.parent_bubble.todo_store.mark_undone(int(self.task["id"]))
        self.parent_bubble._append("Planner", f"Restored #{self.task['id']}: {self.task['title']}")
        self.parent_bubble._refresh_planner()
        
    def _on_archive_delete(self):
        self.parent_bubble.todo_store.delete_task(int(self.task["id"]))
        self.parent_bubble._forget_task_context(self.task)
        self.parent_bubble._append("Planner", f"Permanently deleted #{self.task['id']}: {self.task['title']}")
        self.parent_bubble._refresh_planner()


class ChatBubble(QWidget):
    """Combined assistant chat, task planner, archive, and settings entry point."""

    response_ready = pyqtSignal(str, object, object, object, str)
    closed = pyqtSignal()

    def __init__(
        self,
        llm_client: OllamaClient,
        todo_store: TodoStore,
        work_tracker,
        window_tracker: WindowTracker | None = None,
        device_monitor: DeviceMonitor | None = None,
        memory_store: MemoryStore | None = None,
        voice: MicrosoftVoice | None = None,
    ):
        super().__init__()
        self.llm_client = llm_client
        self.todo_store = todo_store
        self.work_tracker = work_tracker
        self.window_tracker = window_tracker
        self.device_monitor = device_monitor
        self.memory_store = memory_store
        self.voice = voice
        self.pet_name = socket.gethostname() or os.environ.get("COMPUTERNAME", "Desktop Pet")
        self.history: list[dict[str, str]] = []
        self._transcript_entries: list[tuple[str, str]] = []

        self.setWindowTitle(config.APP_NAME)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(520, 480)
        self.resize(560, 620)

        self._build_ui()
        self._refresh_planner()
        self.response_ready.connect(self._receive_response)

    # ------------------------------------------------------------------ #
    #  UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Minimal header ---
        header_widget = QWidget(self)
        header_widget.setObjectName("headerWidget")
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(20, 14, 20, 10)
        
        icon_label = QLabel(self)
        from PyQt6.QtGui import QPixmap
        import config
        icon_pixmap = QPixmap(str(config.BASE_DIR / "assets" / "pet_icon.png"))
        icon_pixmap = icon_pixmap.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        icon_label.setPixmap(icon_pixmap)
        header_layout.addWidget(icon_label)
        
        title = QLabel(f" {self.pet_name}", self)
        title.setObjectName("panelTitle")
        header_layout.addWidget(title, 1)
        self.minimize_button = QPushButton("\u2014", self)
        self.minimize_button.setObjectName("iconButton")
        self.minimize_button.setFixedSize(36, 36)
        self.minimize_button.setToolTip("Minimize")
        self.minimize_button.clicked.connect(self.showMinimized)
        header_layout.addWidget(self.minimize_button)

        self.fullscreen_button = QPushButton("\u25a1", self)
        self.fullscreen_button.setObjectName("iconButton")
        self.fullscreen_button.setFixedSize(36, 36)
        self.fullscreen_button.setToolTip("Toggle Fullscreen")
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)
        header_layout.addWidget(self.fullscreen_button)

        self.settings_button = QPushButton("\u2699", self)
        self.settings_button.setObjectName("iconButton")
        self.settings_button.setFixedSize(36, 36)
        self.settings_button.setToolTip("Settings")
        self.settings_button.clicked.connect(self._open_settings)
        header_layout.addWidget(self.settings_button)
        root.addWidget(header_widget)

        # --- Tabbed content ---
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("mainTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_chat_tab(), "\U0001f4ac Chat")
        self.tabs.addTab(self._build_tasks_tab(), "\u2705 Tasks")
        self.tabs.addTab(self._build_archive_tab(), "\U0001f5c4 Archive")
        root.addWidget(self.tabs, 1)

        # --- Compact status bar ---
        self.status_bar = QLabel("", self)
        self.status_bar.setObjectName("statusBar")
        root.addWidget(self.status_bar)

        self._apply_stylesheet()

    def _build_chat_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 12, 16, 10)
        layout.setSpacing(10)

        # Chat transcript
        self.transcript = QTextBrowser(tab)
        self.transcript.setOpenExternalLinks(True)
        layout.addWidget(self.transcript, 1)

        # Quick-action chips (below transcript, near input like suggestion chips)
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        plan_button = QPushButton("\U0001f4cb Plan day", tab)
        next_button = QPushButton("\U0001f449 Next step", tab)
        work_button = QPushButton("\u23f1 Status", tab)
        for button in (plan_button, next_button, work_button):
            button.setObjectName("chipButton")
            quick_row.addWidget(button)
        quick_row.addStretch()
        plan_button.clicked.connect(
            lambda: self._quick_ask("Make a practical plan for today from my open todos.")
        )
        next_button.clicked.connect(
            lambda: self._quick_ask("What is the best next task to do, and why?")
        )
        work_button.clicked.connect(
            lambda: self._quick_ask("Review my current work streak and suggest a productive next move.")
        )
        layout.addLayout(quick_row)

        # Chat input row (provider selector removed — lives in Settings now)
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input = QLineEdit(tab)
        self.input.setPlaceholderText("Ask, plan, or add a todo\u2026")
        self.input.setObjectName("chatInput")
        self.input.returnPressed.connect(self._send_from_input)
        self.send_button = QPushButton("\u27A4", tab)
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedSize(42, 42)
        self.send_button.clicked.connect(self._send_from_input)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_button)
        layout.addLayout(input_row)

        return tab

    def _build_tasks_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 12, 16, 10)
        layout.setSpacing(10)

        # --- Add-task row ---
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.task_input = QLineEdit(tab)
        self.task_input.setPlaceholderText("Add a task\u2026")
        self.task_input.returnPressed.connect(self._add_task_from_fields)
        self.detail_toggle = QPushButton("\u25be", tab)
        self.detail_toggle.setObjectName("secondaryButton")
        self.detail_toggle.setFixedSize(36, 36)
        self.detail_toggle.setCheckable(True)
        self.detail_toggle.setToolTip("Show details (priority, due date, notes)")
        self.detail_toggle.clicked.connect(self._toggle_task_details)
        self.add_task_button = QPushButton("+", tab)
        self.add_task_button.setFixedSize(36, 36)
        self.add_task_button.clicked.connect(self._add_task_from_fields)
        add_row.addWidget(self.task_input, 1)
        add_row.addWidget(self.detail_toggle)
        add_row.addWidget(self.add_task_button)
        layout.addLayout(add_row)

        # --- Detail panel (progressive disclosure — hidden by default) ---
        self.detail_panel = QFrame(tab)
        self.detail_panel.setObjectName("detailPanel")
        self.detail_panel.setVisible(False)
        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(0, 4, 0, 4)
        detail_layout.setSpacing(8)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(8)
        self.priority_box = QComboBox(self.detail_panel)
        self.priority_box.addItem("High", "high")
        self.priority_box.addItem("Normal", "normal")
        self.priority_box.addItem("Low", "low")
        self.priority_box.setCurrentIndex(1)

        self.due_enabled = QCheckBox("Due", self.detail_panel)
        self.due_edit = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600), self.detail_panel)
        self.due_edit.setCalendarPopup(True)
        self.due_edit.setDisplayFormat("MMM d, yyyy h:mm AP")
        self.due_edit.setEnabled(False)
        self.due_enabled.toggled.connect(self.due_edit.setEnabled)

        detail_row.addWidget(self.priority_box)
        detail_row.addWidget(self.due_enabled)
        detail_row.addWidget(self.due_edit, 1)
        detail_layout.addLayout(detail_row)

        self.notes_input = QLineEdit(self.detail_panel)
        self.notes_input.setPlaceholderText("Notes (optional)")
        detail_layout.addWidget(self.notes_input)

        layout.addWidget(self.detail_panel)

        # --- Filter row ---
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.filter_box = QComboBox(tab)
        self.filter_box.addItem("All open", "all")
        self.filter_box.addItem("Due today", "today")
        self.filter_box.addItem("Overdue", "overdue")
        self.filter_box.addItem("No date", "no_date")
        self.filter_box.currentIndexChanged.connect(self._refresh_planner)

        filter_row.addWidget(self.filter_box, 1)
        layout.addLayout(filter_row)

        # --- Task list ---
        self.task_list = QListWidget(tab)
        self.task_list.setAlternatingRowColors(False)
        layout.addWidget(self.task_list, 1)

        return tab

    def _build_archive_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 12, 16, 10)
        layout.setSpacing(10)

        # --- Archive list ---
        self.archive_list = QListWidget(tab)
        self.archive_list.setAlternatingRowColors(False)
        layout.addWidget(self.archive_list, 1)

        return tab

    def _toggle_task_details(self) -> None:
        visible = self.detail_toggle.isChecked()
        self.detail_panel.setVisible(visible)
        self.detail_toggle.setText("\u25b4" if visible else "\u25be")

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet("""
            QWidget {
                background: #1a1b23;
                color: #e0e0e4;
                font-family: "Segoe UI Variable", "Inter", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QWidget#headerWidget {
                background: #1a1b23;
                border-bottom: 1px solid #2a2b35;
            }
            QLabel#panelTitle {
                font-size: 18px;
                font-weight: 700;
                color: #ffffff;
            }
            QLabel#statusBar {
                background: #141519;
                color: #6e737a;
                font-size: 12px;
                padding: 9px 20px;
                border-top: 1px solid #2a2b35;
            }
            /* ---- Tabs ---- */
            QTabWidget::pane {
                border: none;
                background: #1a1b23;
            }
            QTabBar {
                background: #1a1b23;
            }
            QTabBar::tab {
                background: transparent;
                color: #6e737a;
                padding: 10px 22px;
                font-size: 14px;
                font-weight: 600;
                border: none;
                border-bottom: 2px solid transparent;
            }
            QTabBar::tab:selected {
                color: #50d890;
                border-bottom: 2px solid #50d890;
            }
            QTabBar::tab:hover:!selected {
                color: #a0a4aa;
            }
            /* ---- Inputs ---- */
            QLineEdit, QDateTimeEdit, QComboBox {
                background: #22232e;
                border: 1px solid #33343f;
                border-radius: 10px;
                padding: 9px 14px;
                color: #e0e0e4;
            }
            QLineEdit:focus, QDateTimeEdit:focus, QComboBox:focus {
                border: 1px solid #50d890;
            }
            QLineEdit#chatInput {
                padding: 11px 16px;
                font-size: 14px;
                border-radius: 21px;
                background: #1f2029;
            }
            /* ---- Content areas ---- */
            QTextBrowser {
                background: #141519;
                border: 1px solid #25262f;
                border-radius: 14px;
                padding: 10px;
            }
            QListWidget {
                background: #141519;
                border: 1px solid #25262f;
                border-radius: 14px;
                padding: 6px;
            }
            QListWidget::item {
                border-bottom: 1px solid #1f2028;
                padding: 0px;
                border-radius: 8px;
            }
            QPushButton#actionBtnDone, QPushButton#actionBtnRestore {
                background: #282938;
                color: #50d890;
                border: 1px solid #33343f;
                border-radius: 6px;
                font-size: 14px;
                padding: 0px;
            }
            QPushButton#actionBtnDone:hover, QPushButton#actionBtnRestore:hover {
                background: #33344a;
            }
            QPushButton#actionBtnDelete {
                background: #282938;
                color: #ff8a8a;
                border: 1px solid #33343f;
                border-radius: 6px;
                font-size: 14px;
                padding: 0px;
            }
            QPushButton#actionBtnDelete:hover {
                background: #4a2525;
            }
            QListWidget::item:selected {
                background: #282938;
                color: #ffffff;
            }
            /* ---- Buttons ---- */
            QPushButton {
                background: #50d890;
                color: #0f1a14;
                border: 0;
                border-radius: 10px;
                padding: 9px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #62e49f;
            }
            QPushButton#sendButton {
                border-radius: 21px;
                font-size: 16px;
                padding: 0;
            }
            QPushButton#secondaryButton {
                background: #282938;
                color: #c0c2c8;
                border: 1px solid #33343f;
            }
            QPushButton#secondaryButton:hover {
                background: #33344a;
            }
            QPushButton#dangerButton {
                background: #351e1e;
                color: #ff8a8a;
                border: 1px solid #4a2828;
            }
            QPushButton#dangerButton:hover {
                background: #4a2525;
            }
            QPushButton#chipButton {
                background: #22232e;
                color: #a0a4aa;
                border: 1px solid #33343f;
                border-radius: 16px;
                padding: 6px 14px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton#chipButton:hover {
                background: #2d2e3a;
                color: #50d890;
                border-color: #50d890;
            }
            QPushButton#iconButton {
                background: transparent;
                color: #6e737a;
                border: none;
                font-size: 18px;
                padding: 0;
            }
            QPushButton#iconButton:hover {
                color: #50d890;
            }
            QPushButton:disabled {
                background: #282938;
                color: #4a4b55;
            }
            /* ---- Checkbox ---- */
            QCheckBox {
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid #33343f;
                background: #22232e;
            }
            QCheckBox::indicator:checked {
                background: #50d890;
                border: 1px solid #50d890;
            }
            QFrame#detailPanel {
                background: transparent;
            }
        """)

    # ------------------------------------------------------------------ #
    #  Public helpers
    # ------------------------------------------------------------------ #

    def ask(self, message: str) -> None:
        """Open the panel and submit a message programmatically."""
        self.show_near_pet(None)
        self._send(message)

    def show_message(self, message: str) -> None:
        """Append a proactive message to the transcript without opening the UI.

        The full window is opened only when the user explicitly clicks
        the mini-bubble or the pet sprite.
        """
        self._append("Pet", message)

    def show_near_pet(self, pet_geometry) -> None:
        """Refresh planner data and show the panel near the active work area."""
        self._refresh_planner()
        self._move_to_active_window_center(pet_geometry)
        self.show()
        self.raise_()
        self.activateWindow()

    def focus_input(self) -> None:
        """Focus the chat tab input after the panel is opened."""
        self.tabs.setCurrentIndex(0)
        self.input.setFocus()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)

    # ------------------------------------------------------------------ #
    #  Window positioning
    # ------------------------------------------------------------------ #

    def _move_to_active_window_center(self, pet_geometry) -> None:
        """Center the panel over the active window, with screen-edge clamping."""
        target = self._active_window_rect()
        if target is None:
            target = self._fallback_screen_rect(pet_geometry)
        screen = QApplication.screenAt(target.center()) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else target

        x = target.center().x() - self.width() // 2
        y = target.center().y() - self.height() // 2
        max_x = max(available.left(), available.right() - self.width() + 1)
        max_y = max(available.top(), available.bottom() - self.height() + 1)
        x = min(max(x, available.left()), max_x)
        y = min(max(y, available.top()), max_y)
        self.move(x, y)

    def _active_window_rect(self) -> QRect | None:
        if self.window_tracker is None:
            return None
        rect = self.window_tracker.get_foreground_window_rect()
        if rect is None or rect.width < 260 or rect.height < 180:
            return None

        center = QPoint((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
        screen = QApplication.screenAt(center) or QApplication.primaryScreen()
        if screen is None:
            return QRect(rect.left, rect.top, rect.width, rect.height)
        available = screen.availableGeometry()
        left = max(rect.left, available.left())
        top = max(rect.top, available.top())
        right = min(rect.right, available.right() + 1)
        bottom = min(rect.bottom, available.bottom() + 1)
        if right <= left or bottom <= top:
            return available
        return QRect(left, top, right - left, bottom - top)

    def _fallback_screen_rect(self, pet_geometry) -> QRect:
        point = QCursor.pos()
        if pet_geometry is not None:
            point = pet_geometry.center()
        screen = QApplication.screenAt(point) or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else self.geometry()

    # ------------------------------------------------------------------ #
    #  Task CRUD
    # ------------------------------------------------------------------ #

    def _add_task_from_fields(self) -> None:
        """Create a task from the planner form fields."""
        title = self.task_input.text().strip()
        if not title:
            self.task_input.setFocus()
            return
        due_at = None
        if self.due_enabled.isChecked():
            due_dt = self.due_edit.dateTime().toPyDateTime().astimezone()
            due_at = due_dt.isoformat(timespec="seconds")
        priority = self.priority_box.currentData() or "normal"
        notes = self.notes_input.text().strip()
        task_id = self.todo_store.add_task(title, due_at, priority, notes)

        self.task_input.clear()
        self.notes_input.clear()
        self.due_enabled.setChecked(False)
        self.priority_box.setCurrentIndex(1)
        self._append("Planner", f"Saved #{task_id}: {title}")
        self._refresh_planner()

    # ------------------------------------------------------------------ #
    #  Planner refresh — now updates status bar + tab badge
    # ------------------------------------------------------------------ #

    def _refresh_planner(self) -> None:
        """Refresh task rows, status text, and the Tasks tab badge."""
        if not hasattr(self, "task_list"):
            return

        counts = self.todo_store.task_counts()

        # --- Compact status bar ---
        work_text = self.work_tracker.summary_text()
        active_part = work_text.split(".")[0].strip() if work_text else "No activity"
        status_parts = [active_part]
        if counts["open"]:
            status_parts.append(f"{counts['open']} open")
        if counts["due_today"]:
            status_parts.append(f"{counts['due_today']} due today")
        if counts["overdue"]:
            status_parts.append(f"\u26a0 {counts['overdue']} overdue")
        if hasattr(self, "status_bar"):
            self.status_bar.setText("  \u00b7  ".join(status_parts))

        # --- Update tab badge ---
        if hasattr(self, "tabs"):
            badge = f"\u2705 Tasks ({counts['open']})" if counts["open"] else "\u2705 Tasks"
            self.tabs.setTabText(1, badge)
            self._refresh_archive()

        # --- Refresh task list ---
        self.task_list.clear()
        tasks = self._filtered_tasks()
        if not tasks:
            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.task_list.addItem(item)
            label = QLabel("No tasks yet \u2014 add one above or ask the assistant!")
            label.setStyleSheet("padding: 12px; color: #a0a4aa;")
            self.task_list.setItemWidget(item, label)
            item.setSizeHint(label.sizeHint())
            return

        for task in tasks:
            item = QListWidgetItem()
            self.task_list.addItem(item)
            widget = TaskItemWidget(task, is_archive=False, parent_bubble=self)
            item.setSizeHint(widget.sizeHint())
            self.task_list.setItemWidget(item, widget)

    def _refresh_archive(self) -> None:
        """Refresh completed tasks shown in the Archive tab."""
        if not hasattr(self, "archive_list"):
            return
        self.archive_list.clear()
        tasks = self.todo_store.list_archived_tasks(100)
        if not tasks:
            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.archive_list.addItem(item)
            label = QLabel("No archived tasks.")
            label.setStyleSheet("padding: 12px; color: #a0a4aa;")
            self.archive_list.setItemWidget(item, label)
            item.setSizeHint(label.sizeHint())
            return

        for task in tasks:
            item = QListWidgetItem()
            self.archive_list.addItem(item)
            widget = TaskItemWidget(task, is_archive=True, parent_bubble=self)
            item.setSizeHint(widget.sizeHint())
            self.archive_list.setItemWidget(item, widget)

    # ------------------------------------------------------------------ #
    #  Task filtering / formatting
    # ------------------------------------------------------------------ #

    def _filtered_tasks(self) -> list[dict]:
        """Return open tasks matching the active planner filter."""
        scope = self.filter_box.currentData() or "all"
        tasks = self.todo_store.list_open_tasks(100)
        if scope == "all":
            return tasks
        if scope == "today":
            today = datetime.now().astimezone().date()
            return [
                task
                for task in tasks
                if (due_at := self._parse_due_at(task)) is not None and due_at.date() == today
            ]
        return [task for task in tasks if self._due_state(task) == scope]

    def _due_state(self, task: dict) -> str:
        due_at = self._parse_due_at(task)
        if due_at is None:
            return "no_date"
        now = datetime.now().astimezone()
        if due_at < now:
            return "overdue"
        if due_at.date() == now.date():
            return "today"
        return "upcoming"

    def _task_label(self, task: dict) -> str:
        title = task.get("title", "Untitled")
        priority = str(task.get("priority") or "normal").upper()
        due = self._format_due(task)
        notes = f"\n   {task['notes']}" if task.get("notes") else ""
        return f"#{task['id']}  [{priority}] {title}\n   {due}{notes}"

    def _format_due(self, task: dict) -> str:
        due_at = self._parse_due_at(task)
        if due_at is None:
            return "No due date"
        label = due_at.strftime("%a, %b %d, %Y %I:%M %p").replace(" 0", " ")
        if due_at < datetime.now().astimezone():
            return f"Overdue - {label}"
        return f"Due {label}"

    def _parse_due_at(self, task: dict) -> datetime | None:
        value = task.get("due_at")
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.astimezone()
        return parsed.astimezone()

    # ------------------------------------------------------------------ #
    #  Chat send / receive
    # ------------------------------------------------------------------ #

    def _quick_ask(self, message: str) -> None:
        if not self.input.isEnabled():
            return
        self._send(message)

    def _send_from_input(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._send(text)

    def _send(self, text: str) -> None:
        """Send one user message while keeping the Qt UI thread responsive."""
        if not self.input.isEnabled():
            return
        device_snapshot = self._device_snapshot()
        provider = self._selected_provider()
        route = self.llm_client.resolve_provider(provider, device_snapshot)
        self._append("You", text)
        if (
            config.LLM_DEVICE_GATING
            and device_snapshot is not None
            and not device_snapshot.safe_for_llm
            and provider == "auto"
            and route == "local"
        ):
            self._append("Pet", self._resource_warning_text(device_snapshot))
            return
        self.input.setEnabled(False)
        self.send_button.setEnabled(False)
        self._append("Pet", f"Thinking on {'Ollama Cloud' if route == 'cloud' else 'local Ollama'}...")
        messages = self._build_messages(text, device_snapshot)
        thread = threading.Thread(
            target=self._run_llm,
            args=(text, messages, provider, device_snapshot),
            daemon=True,
        )
        thread.start()

    def _run_llm(
        self,
        user_text: str,
        messages: list[dict[str, str]],
        provider: str,
        device_snapshot: DeviceSnapshot | None,
    ) -> None:
        """Call the model on a worker thread and emit a UI-thread signal."""
        reply = self.llm_client.chat(messages, provider=provider, device_snapshot=device_snapshot)
        display, task, completion_payload, memory_payload = extract_structured_reply(reply)
        self.response_ready.emit(display, task, completion_payload, memory_payload, user_text)

    def _receive_response(
        self,
        display: str,
        task: object,
        completion_payload: object,
        memory_payload: object,
        user_text: str,
    ) -> None:
        """Render an assistant response and persist any extracted todo."""
        self._remove_last_thinking_line()
        completed = self._complete_referenced_tasks(completion_payload)
        if task:
            task_id = self.todo_store.add_task(
                task["title"],
                task.get("due_at"),
                task.get("priority", "normal"),
                task.get("notes", ""),
            )
            due = f" due {task.get('due_at')}" if task.get("due_at") else ""
            priority = normalize_priority(task.get("priority", "normal"))
            display = (
                f"{display}\n\nTodo saved #{task_id}: "
                f"[{priority}] {task['title']}{due}"
            ).strip()
            self._refresh_planner()
        if completed:
            display = f"{display}\n\n{completed}".strip()
            self._refresh_planner()
        self._remember_context(memory_payload)
        self._append("Pet", display or "Done.")
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": display})
        self.history = self.history[-8:]
        self.input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.input.setFocus()

    # ------------------------------------------------------------------ #
    #  LLM context / provider
    # ------------------------------------------------------------------ #

    def _build_messages(
        self,
        user_text: str,
        device_snapshot: DeviceSnapshot | None = None,
    ) -> list[dict[str, str]]:
        """Build the privacy-safe prompt context for chat replies."""
        device_summary = (
            device_snapshot.summary_text()
            if device_snapshot is not None
            else "Device telemetry is unavailable."
        )
        memory_summary = self._memory_summary()
        recent_work = self._recent_work_summary()
        context = (
            f"Current local time: {datetime.now().astimezone().isoformat(timespec='seconds')}\n"
            f"Saved context:\n{memory_summary}\n"
            f"Open todos:\n{self.todo_store.open_tasks_summary()}\n"
            f"Recently completed todos (historical only; do not ask about these as active work):\n"
            f"{self.todo_store.recent_completed_summary()}\n"
            f"Work summary:\n{self.work_tracker.summary_text()}\n"
            f"Recent work pattern:\n{recent_work}\n"
            f"Device summary:\n{device_summary}"
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": context},
            *self.history[-8:],
            {"role": "user", "content": user_text},
        ]

    def _memory_summary(self) -> str:
        if self.memory_store is None:
            return "No saved long-term memory yet."
        try:
            completed_titles = [
                str(task.get("title", ""))
                for task in self.todo_store.list_archived_tasks(20)
            ]
            return self.memory_store.context_summary(exclude_texts=completed_titles)
        except Exception as exc:  # pragma: no cover - defensive for tray app.
            print(f"Memory context unavailable: {exc}")
            return "Saved memory is temporarily unavailable."

    def _remember_context(self, memory_payload: object) -> None:
        if self.memory_store is None or not memory_payload:
            return
        try:
            self.memory_store.apply_payload(memory_payload)
        except Exception as exc:  # pragma: no cover - defensive for tray app.
            print(f"Memory update failed: {exc}")

    def _recent_work_summary(self) -> str:
        try:
            return self.work_tracker.recent_summary_text()
        except Exception as exc:  # pragma: no cover - defensive for tray app.
            print(f"Recent work summary unavailable: {exc}")
            return "Recent work pattern is temporarily unavailable."

    def _complete_referenced_tasks(self, completion_payload: object) -> str:
        completions = normalize_completion_payloads(completion_payload)
        if not completions:
            return ""
        completed_tasks: list[dict] = []
        for completion in completions:
            matches = self.todo_store.mark_done_by_reference(
                completion.get("task_id"),
                completion.get("title", ""),
                bool(completion.get("all_related", True)),
            )
            for task in matches:
                if not any(int(task["id"]) == int(existing["id"]) for existing in completed_tasks):
                    completed_tasks.append(task)
                    self._forget_task_context(task)
        if not completed_tasks:
            return "I heard that it is done, but I could not match it to an open todo."
        if len(completed_tasks) == 1:
            task = completed_tasks[0]
            return f"Marked done #{task['id']}: {task['title']}"
        labels = ", ".join(f"#{task['id']}" for task in completed_tasks)
        return f"Marked done {labels}."

    def _forget_task_context(self, task: dict) -> None:
        if self.memory_store is None:
            return
        try:
            self.memory_store.archive_related_to(str(task.get("title", "")))
        except Exception as exc:  # pragma: no cover - defensive for tray app.
            print(f"Task memory cleanup failed: {exc}")

    def _device_snapshot(self) -> DeviceSnapshot | None:
        if self.device_monitor is None:
            return None
        return self.device_monitor.snapshot()

    def _selected_provider(self) -> str:
        if not hasattr(self, "provider_box"):
            return self.llm_client.settings().chat_provider
        return normalize_provider(self.provider_box.currentData())

    def _sync_provider_box(self) -> None:
        if not hasattr(self, "provider_box"):
            return
        settings = self.llm_client.settings()
        index = self.provider_box.findData(normalize_provider(settings.chat_provider))
        self.provider_box.blockSignals(True)
        if index >= 0:
            self.provider_box.setCurrentIndex(index)
        self.provider_box.blockSignals(False)

    def _chat_provider_changed(self) -> None:
        settings = self.llm_client.settings()
        settings.chat_provider = self._selected_provider()
        self.llm_client.save_settings(settings)

    # ------------------------------------------------------------------ #
    #  Settings / warnings
    # ------------------------------------------------------------------ #

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.llm_client, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self._sync_provider_box()
            self._refresh_planner()

    def _resource_warning_text(self, device_snapshot: DeviceSnapshot) -> str:
        """Create a local warning shown when device load blocks local LLM use."""
        hot_parts = []
        if (
            device_snapshot.cpu_percent is not None
            and device_snapshot.cpu_percent >= config.SMART_NUDGE_MAX_CPU_PERCENT
        ):
            hot_parts.append(f"CPU {device_snapshot.cpu_percent:.0f}%")
        if (
            device_snapshot.memory_percent is not None
            and device_snapshot.memory_percent >= config.SMART_NUDGE_MAX_MEMORY_PERCENT
        ):
            hot_parts.append(f"RAM {device_snapshot.memory_percent:.0f}%")
        load = " and ".join(hot_parts) if hot_parts else device_snapshot.reason
        verb = "are" if len(hot_parts) > 1 else "is"
        return f"HEY! {load} {verb} running hot. I am not firing the model right now."

    # ------------------------------------------------------------------ #
    #  Transcript rendering
    # ------------------------------------------------------------------ #

    def _append(self, speaker: str, text: str) -> None:
        """Append one transcript entry and rerender the chat history."""
        self._transcript_entries.append((speaker, text))
        self._transcript_entries = self._transcript_entries[-80:]
        self._render_transcript()
        self._speak_pet_message(speaker, text)

    def _speak_pet_message(self, speaker: str, text: str) -> None:
        """Speak real pet messages when voice output is enabled."""
        if self.voice is None or speaker != "Pet":
            return
        if str(text or "").strip().startswith("Thinking"):
            return
        self.voice.speak_if_enabled(text)

    def _render_transcript(self) -> None:
        styles = """
        <style>
            body {
                font-family: "Segoe UI Variable", "Inter", "Segoe UI", sans-serif;
                font-size: 14px;
                color: #e0e0e4;
                margin: 0;
                padding: 16px;
                background: #141519;
            }
            .speaker {
                font-size: 11px;
                font-weight: 600;
                margin-bottom: 4px;
                margin-left: 8px;
                color: #6e737a;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            /* Markdown Elements */
            p { margin-top: 0; margin-bottom: 6px; line-height: 1.4; }
            code {
                background-color: rgba(255, 255, 255, 0.1);
                padding: 2px 4px;
                border-radius: 4px;
                font-family: "Cascadia Code", Consolas, monospace;
                font-size: 13px;
            }
            pre {
                background-color: rgba(0, 0, 0, 0.3);
                padding: 12px;
                border-radius: 8px;
                font-family: "Cascadia Code", Consolas, monospace;
                font-size: 13px;
                white-space: pre-wrap;
            }
            ul, ol {
                margin-top: 4px;
                margin-bottom: 8px;
                padding-left: 24px;
            }
            li {
                margin-bottom: 4px;
            }
            a { color: #50d890; text-decoration: none; border-bottom: 1px solid #50d890; }
            .user-link { color: #ffffff; border-bottom: 1px solid #ffffff; }
            table { border-collapse: collapse; margin: 8px 0; }
            th, td { border: 1px solid rgba(255,255,255,0.2); padding: 4px 8px; }
            th { background-color: rgba(0,0,0,0.2); font-weight: 600; }
        </style>
        """
        blocks = []
        for i, (speaker, text) in enumerate(self._transcript_entries):
            is_user = speaker == "You"

            if speaker == "Planner":
                bg_color = "#2c2620"
                fg_color = "#f2e3d5"
            elif is_user:
                # Vibrant messenger blue for user
                bg_color = "#0078FF"
                fg_color = "#ffffff"
            else:
                # Sleek dark slate for bot
                bg_color = "#262730"
                fg_color = "#e0e0e4"
                speaker = self.pet_name

            safe_speaker = html.escape(speaker)
            
            # Parse markdown
            try:
                md_html = markdown.markdown(text, extensions=['fenced_code', 'nl2br', 'sane_lists', 'tables'])
                if is_user:
                    md_html = md_html.replace('<a href=', '<a class="user-link" href=')
            except Exception:
                md_html = html.escape(text).replace("\\n", "<br>")
                
            bubble_content = f'<div style="color:{fg_color};">{md_html}</div>'

            show_speaker = not is_user
            if show_speaker and i > 0 and self._transcript_entries[i-1][0] == self._transcript_entries[i][0]:
                show_speaker = False

            speaker_label = f'<div class="speaker">{safe_speaker}</div>' if show_speaker else ''

            if is_user:
                blocks.append(
                    f'<table width="100%" style="margin-top:6px; margin-bottom:6px;" cellspacing="0" cellpadding="0"><tr>'
                    f'<td width="15%"></td>'
                    f'<td align="right">'
                    f'<table cellspacing="0" cellpadding="12" style="background-color:{bg_color}; border-radius:18px;">'
                    f'<tr><td>{bubble_content}</td></tr></table>'
                    f'</td></tr></table>'
                )
            else:
                blocks.append(
                    f'<table width="100%" style="margin-top:6px; margin-bottom:6px;" cellspacing="0" cellpadding="0"><tr>'
                    f'<td align="left">'
                    f'{speaker_label}'
                    f'<table cellspacing="0" cellpadding="12" style="background-color:{bg_color}; border-radius:18px;">'
                    f'<tr><td>{bubble_content}</td></tr></table>'
                    f'</td>'
                    f'<td width="15%"></td></tr></table>'
                )
                
        self.transcript.setHtml(styles + "".join(blocks))
        QTimer.singleShot(
            0,
            lambda: self.transcript.verticalScrollBar().setValue(
                self.transcript.verticalScrollBar().maximum()
            ),
        )

    def _remove_last_thinking_line(self) -> None:
        if (
            self._transcript_entries
            and self._transcript_entries[-1][0] == "Pet"
            and self._transcript_entries[-1][1].startswith("Thinking")
        ):
            self._transcript_entries.pop()
            self._render_transcript()


def extract_structured_reply(reply: str) -> tuple[str, dict | None, object | None, dict | None]:
    """Split assistant text from optional TODO_JSON, DONE_JSON, and MEMORY_JSON lines."""
    lines = reply.splitlines()
    task = None
    completion_payload = None
    memory_payload = None
    kept = []
    todo_pattern = re.compile(r"^TODO_JSON:\s*(.+)\s*$")
    done_pattern = re.compile(r"^DONE_JSON:\s*(.+)\s*$")
    memory_pattern = re.compile(r"^MEMORY_JSON:\s*(.+)\s*$")
    for line in lines:
        stripped = line.strip()
        todo_match = todo_pattern.match(stripped)
        done_match = done_pattern.match(stripped)
        memory_match = memory_pattern.match(stripped)
        if not todo_match and not done_match and not memory_match:
            kept.append(line)
            continue
        raw = (todo_match or done_match or memory_match).group(1).strip()
        candidate = parse_json_object(raw)
        if not isinstance(candidate, dict):
            continue
        if todo_match:
            task = normalize_task_payload(candidate)
        elif done_match:
            completion_payload = candidate
        else:
            memory_payload = candidate
    return "\n".join(kept).strip(), task, completion_payload, memory_payload


def extract_todo_json(reply: str) -> tuple[str, dict | None]:
    """Split assistant text from an optional strict `TODO_JSON` line."""
    display, task, _, _ = extract_structured_reply(reply)
    return display, task


def parse_json_object(raw: str) -> dict | None:
    try:
        candidate = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return candidate if isinstance(candidate, dict) else None


def normalize_task_payload(candidate: dict) -> dict | None:
    title = str(candidate.get("title", "")).strip()
    due_at = candidate.get("due_at")
    if not title:
        return None
    return {
        "title": title[:240],
        "due_at": due_at if due_at else None,
        "priority": normalize_priority(candidate.get("priority")),
        "notes": str(candidate.get("notes") or "").strip()[:500],
    }


def normalize_completion_payloads(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    raw_items = payload.get("tasks") or payload.get("completed") or payload.get("items")
    if raw_items is None:
        raw_items = [payload]
    elif isinstance(raw_items, dict):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        return []

    completions = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        task_id = parse_optional_int(item.get("task_id") or item.get("id"))
        title = str(item.get("title") or item.get("task") or "").strip()
        if task_id is None and not title:
            continue
        completions.append(
            {
                "task_id": task_id,
                "title": title[:240],
                "all_related": bool(item.get("all_related", True)),
            }
        )
    return completions


def parse_optional_int(value: object) -> int | None:
    if value is None:
        return None
    if str(value).strip().lower() in {"", "null", "none"}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
