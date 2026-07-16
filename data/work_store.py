"""SQLite storage for completed work sessions."""

from __future__ import annotations

import re
import sqlite3
import threading
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import config


class WorkStore:
    """Thread-safe repository for summarized work-session rows."""

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
        """Create the work-session table if this is a fresh local database."""
        with self._lock:
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS work_sessions (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            started_at TEXT NOT NULL,
                            ended_at TEXT NOT NULL,
                            active_seconds INTEGER NOT NULL,
                            idle_seconds INTEGER NOT NULL,
                            foreground_app_summary TEXT NOT NULL
                        )
                        """
                    )
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_started ON work_sessions(started_at)")

    def add_session(
        self,
        started_at: datetime,
        ended_at: datetime,
        active_seconds: int,
        idle_seconds: int,
        foreground_app_summary: str,
    ) -> int:
        """Persist a completed work session and return its database id."""
        with self._lock:
            with closing(self._connect()) as conn:
                with conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO work_sessions(
                            started_at, ended_at, active_seconds, idle_seconds, foreground_app_summary
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            started_at.isoformat(timespec="seconds"),
                            ended_at.isoformat(timespec="seconds"),
                            int(active_seconds),
                            int(idle_seconds),
                            foreground_app_summary,
                        ),
                    )
            return int(cursor.lastrowid)

    def get_today_sessions(self, now: datetime | None = None) -> list[dict]:
        """Return sessions that started during the local day containing `now`."""
        start, end = local_day_bounds(now)
        with self._lock:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    """
                    SELECT id, started_at, ended_at, active_seconds, idle_seconds, foreground_app_summary
                    FROM work_sessions
                    WHERE started_at >= ? AND started_at < ?
                    ORDER BY started_at
                    """,
                    (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_today_active_seconds(self, now: datetime | None = None) -> int:
        """Return total active seconds for sessions started today."""
        start, end = local_day_bounds(now)
        with self._lock:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    """
                    SELECT COALESCE(SUM(active_seconds), 0) AS total
                    FROM work_sessions
                    WHERE started_at >= ? AND started_at < ?
                    """,
                    (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
                ).fetchone()
        return int(row["total"] if row else 0)

    def recent_summary_text(self, days: int = 7, limit: int = 120) -> str:
        """Return a compact multi-day work pattern summary for prompts."""
        days = max(1, min(30, int(days)))
        limit = max(1, min(500, int(limit)))
        cutoff = datetime.now().astimezone() - timedelta(days=days)
        with self._lock:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    """
                    SELECT active_seconds, foreground_app_summary
                    FROM work_sessions
                    WHERE started_at >= ?
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (cutoff.isoformat(timespec="seconds"), limit),
                ).fetchall()
        if not rows:
            return f"No completed work sessions in the last {days} days."
        total_active = sum(max(0, int(row["active_seconds"])) for row in rows)
        app_mix = merge_bucket_summaries(rows)
        return (
            f"Last {days} days: {len(rows)} completed sessions, "
            f"{format_duration(total_active)} active. Typical app mix: {app_mix}."
        )


def local_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return timezone-aware local start/end bounds for the current day."""
    now = now or datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def format_duration(seconds: int) -> str:
    """Format seconds as a short hours/minutes string without UI imports."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def merge_bucket_summaries(rows: list[sqlite3.Row]) -> str:
    """Return weighted foreground-bucket percentages from work sessions."""
    weights: Counter[str] = Counter()
    total_weight = 0.0
    for row in rows:
        active_seconds = max(0, int(row["active_seconds"]))
        if active_seconds <= 0:
            continue
        for bucket, percent in parse_bucket_summary(str(row["foreground_app_summary"])):
            weight = active_seconds * (percent / 100.0)
            weights[bucket] += weight
            total_weight += weight
    if total_weight <= 0:
        return "not enough data"
    parts = []
    for bucket, weight in weights.most_common(4):
        parts.append(f"{bucket} {round((weight / total_weight) * 100)}%")
    return ", ".join(parts)


def parse_bucket_summary(summary: str) -> list[tuple[str, int]]:
    parts = []
    for raw_part in summary.split(","):
        part = raw_part.strip()
        match = re.match(r"(.+?)\s+(\d+)%$", part)
        if not match:
            continue
        parts.append((match.group(1).strip(), int(match.group(2))))
    return parts
