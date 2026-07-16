"""SQLite todo storage with small in-place migrations."""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, time
from pathlib import Path

import config


PRIORITIES = {"high", "normal", "low"}
TASK_MATCH_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "do",
    "done",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "make",
    "my",
    "of",
    "on",
    "or",
    "plan",
    "schedule",
    "task",
    "that",
    "the",
    "this",
    "to",
    "with",
}


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

    def mark_done_by_reference(
        self,
        task_id: int | None = None,
        title: str = "",
        all_related: bool = True,
    ) -> list[dict]:
        """Mark open tasks done by id and/or fuzzy title reference."""
        title = str(title or "").strip()
        matches = self.find_open_task_matches(task_id, title)
        if not all_related and matches:
            matches = matches[:1]
        if not matches:
            return []

        completed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        ids = [int(task["id"]) for task in matches]
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute(
                        f"""
                        UPDATE tasks
                        SET done = 1, completed_at = ?, updated_at = ?
                        WHERE done = 0 AND id IN ({placeholders})
                        """,
                        (completed_at, completed_at, *ids),
                    )
        return matches

    def find_open_task_matches(
        self,
        task_id: int | None = None,
        title: str = "",
        limit: int = 100,
    ) -> list[dict]:
        """Return open tasks matching an id or fuzzy title reference."""
        title = str(title or "").strip()
        tasks = self.list_open_tasks(limit)
        matches: list[tuple[float, dict]] = []

        if task_id is not None:
            for task in tasks:
                if int(task["id"]) == int(task_id):
                    matches.append((2.0, task))
                    break

        if title:
            for task in tasks:
                if any(int(task["id"]) == int(existing["id"]) for _, existing in matches):
                    continue
                score = task_match_score(title, str(task.get("title", "")))
                if score >= 0.55:
                    matches.append((score, task))

        matches.sort(
            key=lambda item: (
                -item[0],
                item[1].get("due_at") is None,
                str(item[1].get("due_at") or ""),
                int(item[1]["id"]),
            )
        )
        return [task for _, task in matches]

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

    def recent_completed_summary(self, limit: int = 8) -> str:
        """Return recent completions as historical context for prompts."""
        tasks = self.list_archived_tasks(limit)
        if not tasks:
            return "No recently completed todos."
        lines = []
        for task in tasks:
            completed = f" completed {task['completed_at']}" if task.get("completed_at") else ""
            lines.append(f"- #{task['id']}: {task['title']}{completed}")
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


def task_match_score(reference: str, title: str) -> float:
    """Return a rough 0..1 similarity score for user completion text."""
    reference_key = normalized_text(reference)
    title_key = normalized_text(title)
    if not reference_key or not title_key:
        return 0.0
    if reference_key == title_key or reference_key in title_key or title_key in reference_key:
        return 1.0

    reference_tokens = meaningful_tokens(reference_key)
    title_tokens = meaningful_tokens(title_key)
    if not reference_tokens or not title_tokens:
        return 0.0
    shared = reference_tokens & title_tokens
    if not shared:
        return 0.0
    score = len(shared) / max(1, min(len(reference_tokens), len(title_tokens)))
    if len(shared) == 1 and len(next(iter(shared))) < 4:
        return 0.0
    return score


def normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def meaningful_tokens(value: str) -> set[str]:
    return {
        token
        for token in normalized_text(value).split()
        if len(token) >= 2 and token not in TASK_MATCH_STOP_WORDS
    }
