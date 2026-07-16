"""SQLite storage for privacy-safe assistant memory."""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config


MEMORY_KINDS = {"preference", "project", "work_style", "profile", "instruction", "context"}
MAX_MEMORY_CHARS = 240
MAX_ROLLUP_CHARS = 1200
SECRET_HINT_RE = re.compile(r"(?i)\b(api[_ -]?key|access[_ -]?token|password|secret|bearer)\b")
MEMORY_MATCH_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "completed",
    "done",
    "finished",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "task",
    "that",
    "the",
    "this",
    "to",
    "with",
}


@dataclass(frozen=True)
class MemoryItem:
    """One durable, summarized memory item."""

    id: int
    kind: str
    summary: str
    confidence: float
    created_at: str
    updated_at: str


class MemoryStore:
    """Thread-safe repository for compact long-term assistant context.

    This store intentionally keeps summaries and preferences, not raw chat
    transcripts. The chat layer decides what is durable enough to remember.
    """

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
        """Create memory tables if this is a fresh local database."""
        with self._lock:
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS memory_items (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            kind TEXT NOT NULL,
                            summary TEXT NOT NULL,
                            normalized_summary TEXT NOT NULL,
                            confidence REAL NOT NULL DEFAULT 0.7,
                            source TEXT NOT NULL DEFAULT 'chat',
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            last_used_at TEXT,
                            use_count INTEGER NOT NULL DEFAULT 0,
                            archived INTEGER NOT NULL DEFAULT 0
                        )
                        """
                    )
                    conn.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_unique
                        ON memory_items(kind, normalized_summary, archived)
                        """
                    )
                    conn.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_memory_active
                        ON memory_items(archived, confidence, updated_at)
                        """
                    )
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS conversation_rollups (
                            id INTEGER PRIMARY KEY CHECK (id = 1),
                            summary TEXT NOT NULL,
                            turn_count INTEGER NOT NULL DEFAULT 0,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        )
                        """
                    )

    def add_memory(
        self,
        kind: str,
        summary: str,
        confidence: float = 0.7,
        source: str = "chat",
    ) -> int | None:
        """Insert or refresh one summarized memory item."""
        summary = clean_summary(summary, MAX_MEMORY_CHARS)
        if not summary or looks_sensitive(summary):
            return None
        kind = normalize_kind(kind)
        normalized = normalized_key(summary)
        confidence = bounded_confidence(confidence)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        source = clean_summary(source, 40) or "chat"

        with self._lock:
            with closing(self._connect()) as conn:
                with conn:
                    row = conn.execute(
                        """
                        SELECT id, confidence
                        FROM memory_items
                        WHERE kind = ? AND normalized_summary = ? AND archived = 0
                        """,
                        (kind, normalized),
                    ).fetchone()
                    if row:
                        conn.execute(
                            """
                            UPDATE memory_items
                            SET summary = ?,
                                confidence = ?,
                                source = ?,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                summary,
                                max(float(row["confidence"]), confidence),
                                source,
                                now,
                                int(row["id"]),
                            ),
                        )
                        return int(row["id"])

                    cursor = conn.execute(
                        """
                        INSERT INTO memory_items(
                            kind, summary, normalized_summary, confidence,
                            source, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (kind, summary, normalized, confidence, source, now, now),
                    )
                    self._prune_locked(conn)
                    return int(cursor.lastrowid)

    def update_rollup(self, summary: str) -> None:
        """Replace the compact rolling conversation summary."""
        summary = clean_summary(summary, MAX_ROLLUP_CHARS)
        if not summary or looks_sensitive(summary):
            return
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock:
            with closing(self._connect()) as conn:
                with conn:
                    row = conn.execute("SELECT turn_count FROM conversation_rollups WHERE id = 1").fetchone()
                    if row:
                        conn.execute(
                            """
                            UPDATE conversation_rollups
                            SET summary = ?, turn_count = ?, updated_at = ?
                            WHERE id = 1
                            """,
                            (summary, int(row["turn_count"]) + 1, now),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO conversation_rollups(id, summary, turn_count, created_at, updated_at)
                            VALUES (1, ?, 1, ?, ?)
                            """,
                            (summary, now, now),
                        )

    def apply_payload(self, payload: object) -> int:
        """Apply a parsed MEMORY_JSON payload and return saved item count."""
        if not isinstance(payload, dict):
            return 0

        rollup = payload.get("rollup")
        if rollup:
            self.update_rollup(str(rollup))

        memories = payload.get("memories", [])
        if isinstance(memories, dict):
            memories = [memories]
        if not isinstance(memories, list):
            return 0

        saved = 0
        for item in memories:
            if not isinstance(item, dict):
                continue
            memory_id = self.add_memory(
                item.get("kind", "context"),
                item.get("summary", ""),
                item.get("confidence", 0.7),
                item.get("source", "chat"),
            )
            if memory_id is not None:
                saved += 1
        return saved

    def context_summary(
        self,
        limit: int = config.MEMORY_CONTEXT_LIMIT,
        exclude_texts: list[str] | None = None,
    ) -> str:
        """Return compact saved context for an LLM prompt."""
        limit = max(1, min(30, int(limit)))
        exclude_texts = [str(text) for text in (exclude_texts or []) if str(text or "").strip()]
        with self._lock:
            with closing(self._connect()) as conn:
                rollup = conn.execute(
                    "SELECT summary FROM conversation_rollups WHERE id = 1"
                ).fetchone()
                rows = conn.execute(
                    """
                    SELECT id, kind, summary, confidence, created_at, updated_at
                    FROM memory_items
                    WHERE archived = 0
                    ORDER BY confidence DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

        parts = []
        if rollup and rollup["summary"] and not any_text_related(str(rollup["summary"]), exclude_texts):
            parts.append(f"Conversation rollup:\n{rollup['summary']}")
        if rows:
            lines = [
                f"- [{row['kind']}] {row['summary']}"
                for row in rows
                if not any_text_related(str(row["summary"]), exclude_texts)
            ]
            if lines:
                parts.append("Durable memories:\n" + "\n".join(lines))
        return "\n\n".join(parts) or "No saved long-term memory yet."

    def archive_related_to(self, text: str) -> int:
        """Archive memory rows and clear rollups related to completed work."""
        text = str(text or "").strip()
        if not text:
            return 0
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        archived = 0
        with self._lock:
            with closing(self._connect()) as conn:
                with conn:
                    rows = conn.execute(
                        """
                        SELECT id, summary
                        FROM memory_items
                        WHERE archived = 0
                        """
                    ).fetchall()
                    ids = [
                        int(row["id"])
                        for row in rows
                        if text_related(str(row["summary"]), text)
                    ]
                    if ids:
                        placeholders = ",".join("?" for _ in ids)
                        conn.execute(
                            f"""
                            UPDATE memory_items
                            SET archived = 1, updated_at = ?
                            WHERE id IN ({placeholders})
                            """,
                            (now, *ids),
                        )
                        archived += len(ids)

                    rollup = conn.execute(
                        "SELECT summary FROM conversation_rollups WHERE id = 1"
                    ).fetchone()
                    if rollup and text_related(str(rollup["summary"]), text):
                        conn.execute(
                            """
                            UPDATE conversation_rollups
                            SET summary = ?, updated_at = ?
                            WHERE id = 1
                            """,
                            (
                                "Previous rolling context was cleared because related todo work is complete.",
                                now,
                            ),
                        )
                        archived += 1
        return archived

    def list_memories(self, limit: int = 50) -> list[MemoryItem]:
        """Return active memories for debugging or future UI surfaces."""
        limit = max(1, min(200, int(limit)))
        with self._lock:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    """
                    SELECT id, kind, summary, confidence, created_at, updated_at
                    FROM memory_items
                    WHERE archived = 0
                    ORDER BY confidence DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [
            MemoryItem(
                id=int(row["id"]),
                kind=str(row["kind"]),
                summary=str(row["summary"]),
                confidence=float(row["confidence"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def _prune_locked(self, conn: sqlite3.Connection) -> None:
        max_items = max(20, int(config.MEMORY_MAX_ITEMS))
        rows = conn.execute(
            """
            SELECT id
            FROM memory_items
            WHERE archived = 0
            ORDER BY confidence DESC, updated_at DESC
            LIMIT -1 OFFSET ?
            """,
            (max_items,),
        ).fetchall()
        if not rows:
            return
        ids = [int(row["id"]) for row in rows]
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE memory_items SET archived = 1 WHERE id IN ({placeholders})",
            ids,
        )


def normalize_kind(value: object) -> str:
    kind = str(value or "context").strip().lower()
    return kind if kind in MEMORY_KINDS else "context"


def bounded_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.7
    return max(0.1, min(1.0, confidence))


def clean_summary(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    return text[: max(1, int(limit))].strip()


def normalized_key(summary: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", summary.lower()).strip()
    return text[:220] or "memory"


def looks_sensitive(summary: str) -> bool:
    return bool(SECRET_HINT_RE.search(summary))


def any_text_related(summary: str, texts: list[str]) -> bool:
    return any(text_related(summary, text) for text in texts)


def text_related(left: str, right: str) -> bool:
    left_key = normalized_text(left)
    right_key = normalized_text(right)
    if not left_key or not right_key:
        return False
    if left_key in right_key or right_key in left_key:
        return True
    left_tokens = meaningful_tokens(left_key)
    right_tokens = meaningful_tokens(right_key)
    if not left_tokens or not right_tokens:
        return False
    shared = left_tokens & right_tokens
    if not shared:
        return False
    score = len(shared) / max(1, min(len(left_tokens), len(right_tokens)))
    return score >= 0.55 and (len(shared) > 1 or len(next(iter(shared))) >= 4)


def normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def meaningful_tokens(value: str) -> set[str]:
    return {
        token
        for token in normalized_text(value).split()
        if len(token) >= 2 and token not in MEMORY_MATCH_STOP_WORDS
    }
