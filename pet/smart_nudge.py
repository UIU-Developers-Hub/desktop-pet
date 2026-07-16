"""Proactive nudge engine using coarse todo, work, and device context."""

from __future__ import annotations

import json
import random
import re
import threading
import time
from datetime import datetime
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import config
from ai.llm_client import OllamaClient
from data.todo_store import TodoStore
from pet.device_monitor import DeviceMonitor, DeviceSnapshot
from pet.work_tracker import WorkSnapshot, WorkTracker, format_duration


DISTRACTION_BUCKETS = {"social media", "media", "game", "chat app"}
PRODUCTIVE_BUCKETS = {"IDE", "terminal", "office", "design app"}

SMART_NUDGE_PROMPT = (
    "You are a tiny desktop pet coach. Return only compact JSON with keys "
    '"message", "action", and "tone". "message" must be under 140 characters. '
    '"action" must be one of "jump", "react", "rest", or "talk". '
    "Use the user's coarse todo, work, screen-time, and device metadata. "
    "If the reason is resource, warn that local CPU/RAM is hot and that you are using cloud fallback. "
    "Be brief, specific, playful, and useful. You may tease gently when the "
    "user is distracted, but do not insult them. Do not claim to know exact "
    "sites, apps, or private window titles."
)


class SmartNudgeEngine(QObject):
    """Select when to nudge and generate a short message safely off-thread."""

    nudge_ready = pyqtSignal(dict)

    def __init__(
        self,
        todo_store: TodoStore,
        work_tracker: WorkTracker,
        device_monitor: DeviceMonitor,
        llm_client: OllamaClient,
    ):
        super().__init__()
        self.todo_store = todo_store
        self.work_tracker = work_tracker
        self.device_monitor = device_monitor
        self.llm_client = llm_client
        self._rng = random.Random()
        self._lock = threading.Lock()
        self._inflight = False
        self._last_nudge_at = 0.0
        self._last_resource_alert_at = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        """Start periodic smart-nudge checks after a short initial delay."""
        self._timer.start(config.SMART_NUDGE_CHECK_SECONDS * 1000)
        QTimer.singleShot(15_000, self._tick)

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        """Evaluate current state and start at most one nudge generation."""
        if not config.PROACTIVE:
            return

        snapshot = self.work_tracker.snapshot()
        if snapshot.is_idle:
            return

        device = self.device_monitor.snapshot()
        if not device.safe_for_llm:
            if self.llm_client.resolve_provider("auto", device) == "cloud":
                self._maybe_start_cloud_resource_nudge(snapshot, device)
            else:
                self._maybe_emit_resource_alert(device)
            return

        if time.monotonic() - self._last_nudge_at < config.SMART_NUDGE_COOLDOWN_SECONDS:
            return

        if snapshot.current_streak_seconds < config.SMART_NUDGE_MIN_ACTIVE_SECONDS:
            return

        counts = self.todo_store.task_counts()
        reason = self._select_reason(snapshot, counts)
        if reason is None:
            return

        context = self._build_context(snapshot, device, counts, reason)
        self._start_llm(context, reason, "auto", device)

    def _start_llm(
        self,
        context: dict[str, Any],
        reason: dict[str, str],
        provider: str,
        device: DeviceSnapshot,
    ) -> None:
        """Launch a single background LLM request if none is already running."""
        with self._lock:
            if self._inflight:
                return
            self._inflight = True
        thread = threading.Thread(
            target=self._run_llm,
            args=(context, reason, provider, device),
            name="SmartNudgeLLM",
            daemon=True,
        )
        thread.start()

    def _maybe_emit_resource_alert(self, device: DeviceSnapshot) -> None:
        now = time.monotonic()
        if now - self._last_resource_alert_at < config.SMART_NUDGE_RESOURCE_ALERT_COOLDOWN_SECONDS:
            return
        self._last_resource_alert_at = now
        self._last_nudge_at = now
        self.nudge_ready.emit(resource_alert_nudge(device))

    def _maybe_start_cloud_resource_nudge(
        self,
        snapshot: WorkSnapshot,
        device: DeviceSnapshot,
    ) -> None:
        now = time.monotonic()
        if now - self._last_resource_alert_at < config.SMART_NUDGE_RESOURCE_ALERT_COOLDOWN_SECONDS:
            return
        counts = self.todo_store.task_counts()
        reason = {
            "kind": "resource",
            "detail": "Local CPU/RAM is high; use Ollama Cloud fallback and warn the user briefly.",
        }
        context = self._build_context(snapshot, device, counts, reason)
        self._last_resource_alert_at = now
        self._start_llm(context, reason, "cloud", device)

    def _select_reason(self, snapshot: WorkSnapshot, counts: dict[str, int]) -> dict[str, str] | None:
        """Pick the strongest current reason to nudge, or return none."""
        bucket = snapshot.current_bucket
        open_tasks = counts.get("open", 0)

        if snapshot.current_streak_seconds >= config.SMART_NUDGE_BREAK_SECONDS:
            return {
                "kind": "break",
                "detail": "Long active streak; suggest a short reset.",
            }

        if (
            bucket in DISTRACTION_BUCKETS
            and open_tasks > 0
            and snapshot.current_streak_seconds >= config.SMART_NUDGE_DISTRACTION_SECONDS
        ):
            return {
                "kind": "focus",
                "detail": "Current app bucket looks distracting while todos are still open.",
            }

        if counts.get("overdue", 0) > 0:
            return {
                "kind": "overdue",
                "detail": "There are overdue todos; ask for one concrete next step.",
            }

        if (
            bucket in PRODUCTIVE_BUCKETS
            and snapshot.current_streak_seconds >= config.SMART_NUDGE_PRAISE_SECONDS
        ):
            return {
                "kind": "praise",
                "detail": "The current streak is in a productive app bucket.",
            }

        if (
            snapshot.current_streak_seconds >= config.SMART_NUDGE_JOKE_SECONDS
            and self._rng.random() < config.SMART_NUDGE_JOKE_CHANCE
        ):
            return {
                "kind": "joke",
                "detail": "A tiny joke could reset attention without derailing work.",
            }

        return None

    def _build_context(
        self,
        snapshot: WorkSnapshot,
        device: DeviceSnapshot,
        counts: dict[str, int],
        reason: dict[str, str],
    ) -> dict[str, Any]:
        """Create the JSON-serializable prompt context for the model."""
        return {
            "local_time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "nudge_reason": reason,
            "todos": {
                "open": counts.get("open", 0),
                "due_today": counts.get("due_today", 0),
                "overdue": counts.get("overdue", 0),
            },
            "work": {
                "screen_time_today": format_duration(snapshot.today_active_seconds),
                "current_streak": format_duration(snapshot.current_streak_seconds),
                "current_app_bucket": snapshot.current_bucket,
                "last_break_at": snapshot.last_break_at,
                "session_started_at": snapshot.current_session_started_at,
            },
            "device": {
                "cpu_percent": device.cpu_percent,
                "memory_percent": device.memory_percent,
                "battery_percent": device.battery_percent,
                "power_plugged": device.power_plugged,
                "safe_for_llm": device.safe_for_llm,
                "reason": device.reason,
            },
        }

    def _run_llm(
        self,
        context: dict[str, Any],
        reason: dict[str, str],
        provider: str,
        device: DeviceSnapshot,
    ) -> None:
        """Call the routed model and emit a parsed nudge or local fallback."""
        try:
            messages = [
                {"role": "system", "content": SMART_NUDGE_PROMPT},
                {"role": "user", "content": json.dumps(context, separators=(",", ":"))},
            ]
            reply = self.llm_client.chat(messages, provider=provider, device_snapshot=device)
            nudge = parse_nudge_reply(reply)
            if nudge is None:
                nudge = fallback_nudge(context, reason)
            self._last_nudge_at = time.monotonic()
            self.nudge_ready.emit(nudge)
        finally:
            with self._lock:
                self._inflight = False


def parse_nudge_reply(reply: str) -> dict[str, str] | None:
    """Parse the compact JSON contract requested from the smart nudge prompt."""
    text = reply.strip()
    if not text or text.startswith(("I cannot reach Ollama", "Ollama returned", "Ollama answered")):
        return None

    match = re.search(r"\{.*\}", text, re.DOTALL)
    raw = match.group(0) if match else text
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    message = str(data.get("message", "")).strip()
    if not message:
        return None
    action = str(data.get("action", "react")).strip().lower()
    if action not in {"jump", "react", "rest", "talk"}:
        action = "react"
    tone = str(data.get("tone", "focus")).strip().lower()[:32] or "focus"
    return {
        "message": message[:180],
        "action": action,
        "tone": tone,
    }


def resource_alert_nudge(device: DeviceSnapshot) -> dict[str, str]:
    """Build a local warning when CPU/RAM is too hot for local LLM calls."""
    hot_parts = []
    if device.cpu_percent is not None and device.cpu_percent >= config.SMART_NUDGE_MAX_CPU_PERCENT:
        hot_parts.append(f"CPU {device.cpu_percent:.0f}%")
    if device.memory_percent is not None and device.memory_percent >= config.SMART_NUDGE_MAX_MEMORY_PERCENT:
        hot_parts.append(f"RAM {device.memory_percent:.0f}%")
    if not hot_parts:
        hot_parts.append(device.reason)
    load = " and ".join(hot_parts)
    verb = "are" if len(hot_parts) > 1 else "is"
    return {
        "message": f"HEY! {load} {verb} running hot. I am pausing brain-calls; close something heavy?",
        "action": "jump",
        "tone": "resource",
    }


def fallback_nudge(context: dict[str, Any], reason: dict[str, str]) -> dict[str, str]:
    """Return deterministic nudge text when model output is unavailable."""
    work = context["work"]
    todos = context["todos"]
    kind = reason.get("kind", "focus")
    if kind == "break":
        return {
            "message": f"{work['current_streak']} on deck. Stand up, breathe, then come back sharp?",
            "action": "rest",
            "tone": "break",
        }
    if kind == "focus":
        return {
            "message": f"{todos['open']} todos left and {work['current_app_bucket']} is winning. Pick one next step?",
            "action": "jump",
            "tone": "focus",
        }
    if kind == "overdue":
        return {
            "message": f"{todos['overdue']} overdue todo waiting. Tiny move: choose the next 5-minute action.",
            "action": "react",
            "tone": "focus",
        }
    if kind == "praise":
        return {
            "message": f"Solid streak: {work['current_streak']} in {work['current_app_bucket']}. Keep the rhythm.",
            "action": "talk",
            "tone": "praise",
        }
    if kind == "resource":
        device = context["device"]
        return resource_alert_nudge(
            DeviceSnapshot(
                cpu_percent=device.get("cpu_percent"),
                memory_percent=device.get("memory_percent"),
                battery_percent=device.get("battery_percent"),
                power_plugged=device.get("power_plugged"),
                safe_for_llm=False,
                reason=str(device.get("reason", "local device load is high")),
            )
        )
    return {
        "message": "Micro joke: my productivity plan is 90% snacks, 10% dramatic typing.",
        "action": "react",
        "tone": "joke",
    }
