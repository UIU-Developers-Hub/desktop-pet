"""SQLite todo storage with small in-place migrations."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, time
from pathlib import Path

import config


PRIORITIES = {"high", "normal", "low"}


class TodoStore:
    """Thread-safe task repository used by the planner and LLM workflows."""

    def __init__(self, db_path: Path = config.DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create the task table and add columns from older local databases."""
        with self._lock:
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS tasks (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            title TEXT NOT NULL,
                            due_at TEXT,
                            done INTEGER NOT NULL DEFAULT 0,
                            created_at TEXT NOT NULL
                        )
                        """
                    )
                    self._ensure_columns(conn)
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(done, due_at)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(done, priority)")

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        migrations = {
            "priority": "ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'",
            "notes": "ALTER TABLE tasks ADD COLUMN notes TEXT NOT NULL DEFAULT ''",
            "updated_at": "ALTER TABLE tasks ADD COLUMN updated_at TEXT",
            "completed_at": "ALTER TABLE tasks ADD COLUMN completed_at TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                conn.execute(statement)

    def add_task(
        self,
        title: str,
        due_at: str | None = None,
        priority: str = "normal",
        notes: str = "",
    ) -> int:
        """Create a task and return its database id."""
        title = title.strip()
        if not title:
            raise ValueError("Task title cannot be empty")
        normalized_due = normalize_due_at(due_at)
        normalized_priority = normalize_priority(priority)
        notes = str(notes or "").strip()
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock:
            with closing(self._connect()) as conn:
                with conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO tasks(title, due_at, done, created_at, priority, notes, updated_at)
                        VALUES (?, ?, 0, ?, ?, ?, ?)
                        """,
                        (
                            title,
                            normalized_due,
                            created_at,
                            normalized_priority,
                            notes,
                            created_at,
                        ),
                    )
            return int(cursor.lastrowid)

    def mark_done(self, task_id: int) -> None:
        completed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock:
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute(
                        """
                        UPDATE tasks
                        SET done = 1, completed_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (completed_at, completed_at, task_id),
                    )

    def delete_task(self, task_id: int) -> None:
        with self._lock:
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def mark_undone(self, task_id: int) -> None:
        updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock:
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute(
                        """
                        UPDATE tasks
                        SET done = 0, completed_at = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (updated_at, task_id),
                    )

    def list_open_tasks(self, limit: int = 25) -> list[dict]:
        """Return open tasks ordered by due date, priority, and creation time."""
        with self._lock:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    """
                    SELECT id, title, due_at, done, created_at, priority, notes, updated_at, completed_at
                    FROM tasks
                    WHERE done = 0
                    ORDER BY
                        due_at IS NULL,
                        due_at,
                        CASE priority
                            WHEN 'high' THEN 0
                            WHEN 'normal' THEN 1
                            ELSE 2
                        END,
                        created_at
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_archived_tasks(self, limit: int = 100) -> list[dict]:
        """Return completed tasks newest first for the Archive tab."""
        with self._lock:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    """
                    SELECT id, title, due_at, done, created_at, priority, notes, updated_at, completed_at
                    FROM tasks
                    WHERE done = 1
                    ORDER BY completed_at DESC, created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def due_tasks(self, now: datetime | None = None) -> list[dict]:
        """Return open tasks due at or before `now`."""
        now = now or datetime.now().astimezone()
        now_iso = now.isoformat(timespec="seconds")
        with self._lock:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    """
                    SELECT id, title, due_at, done, created_at, priority, notes, updated_at, completed_at
                    FROM tasks
                    WHERE done = 0
                      AND due_at IS NOT NULL
                      AND due_at <= ?
                    ORDER BY due_at
                    """,
                    (now_iso,),
                ).fetchall()
        return [dict(row) for row in rows]

    def task_counts(self) -> dict[str, int]:
        """Return open, overdue, and due-today counts for UI and nudges."""
        now = datetime.now().astimezone()
        start = datetime.combine(now.date(), time.min).astimezone()
        end = datetime.combine(now.date(), time.max).astimezone()
        with self._lock:
            with closing(self._connect()) as conn:
                open_total = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE done = 0"
                ).fetchone()[0]
                overdue = conn.execute(
                    """
                    SELECT COUNT(*) FROM tasks
                    WHERE done = 0 AND due_at IS NOT NULL AND due_at < ?
                    """,
                    (now.isoformat(timespec="seconds"),),
                ).fetchone()[0]
                due_today = conn.execute(
                    """
                    SELECT COUNT(*) FROM tasks
                    WHERE done = 0 AND due_at IS NOT NULL AND due_at BETWEEN ? AND ?
                    """,
                    (
                        start.isoformat(timespec="seconds"),
                        end.isoformat(timespec="seconds"),
                    ),
                ).fetchone()[0]
        return {
            "open": int(open_total),
            "overdue": int(overdue),
            "due_today": int(due_today),
        }

    def open_tasks_summary(self, limit: int = 12) -> str:
        """Return a compact text summary safe to include in LLM prompts."""
        tasks = self.list_open_tasks(limit)
        if not tasks:
            return "No open todos."
        lines = []
        for task in tasks:
            priority = task.get("priority") or "normal"
            due = f" due {task['due_at']}" if task.get("due_at") else ""
            notes = f" ({task['notes']})" if task.get("notes") else ""
            lines.append(f"- #{task['id']} [{priority}]: {task['title']}{due}{notes}")
        return "\n".join(lines)


def normalize_due_at(value: str | None) -> str | None:
    """Normalize ISO-like date/time text to a local timezone ISO timestamp."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    try:
        if len(text) == 10:
            parsed_date = date.fromisoformat(text)
            dt = datetime.combine(parsed_date, time(hour=9)).astimezone()
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.astimezone()
            else:
                dt = dt.astimezone()
    except ValueError:
        return None
    return dt.isoformat(timespec="seconds")


def normalize_priority(value: str | None) -> str:
    """Map arbitrary priority text onto the supported priority set."""
    text = str(value or "normal").strip().lower()
    if text in {"urgent", "important"}:
        text = "high"
    if text not in PRIORITIES:
        return "normal"
    return text
