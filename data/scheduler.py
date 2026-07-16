"""Qt scheduler for due tasks, break nudges, and break-return events."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import config
from data.todo_store import TodoStore
from pet.work_tracker import WorkTracker


class Scheduler(QObject):
    """Emit proactive planner events at a lightweight polling cadence."""

    due_task = pyqtSignal(dict)
    break_nudge = pyqtSignal(int)
    break_return = pyqtSignal()

    def __init__(self, todo_store: TodoStore, work_tracker: WorkTracker):
        super().__init__()
        self.todo_store = todo_store
        self.work_tracker = work_tracker
        self._notified_task_ids: set[int] = set()
        self._next_break_nudge = config.BREAK_INTERVAL_SECONDS
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        """Start periodic checks and immediately perform the first tick."""
        self._timer.start(config.SCHEDULER_INTERVAL_SECONDS * 1000)
        self._tick()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        """Poll stores/trackers and emit any newly due proactive events."""
        snapshot = self.work_tracker.snapshot()
        if snapshot.current_streak_seconds <= 0 or snapshot.is_idle:
            self._next_break_nudge = config.BREAK_INTERVAL_SECONDS

        if not config.PROACTIVE:
            self.work_tracker.consume_break_resume_event()
            return

        for task in self.todo_store.due_tasks():
            task_id = int(task["id"])
            if task_id in self._notified_task_ids:
                continue
            self._notified_task_ids.add(task_id)
            self.due_task.emit(task)

        if snapshot.current_streak_seconds >= self._next_break_nudge:
            self.break_nudge.emit(snapshot.current_streak_seconds)
            self._next_break_nudge += config.BREAK_INTERVAL_SECONDS

        if self.work_tracker.consume_break_resume_event():
            self.break_return.emit()
