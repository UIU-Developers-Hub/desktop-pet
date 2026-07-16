"""JSON-backed settings for model routing and Ollama hosts."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import config


PROVIDERS = {"auto", "local", "cloud"}


@dataclass
class AppSettings:
    """User-editable LLM routing and pet voice settings.

    Instances are normalized before use so the rest of the app can assume valid
    provider names, non-empty URLs, and bounded timeouts.
    """

    local_base_url: str = config.OLLAMA_URL
    local_model: str = config.OLLAMA_MODEL
    cloud_base_url: str = config.OLLAMA_CLOUD_URL
    cloud_model: str = config.OLLAMA_CLOUD_MODEL
    cloud_api_key: str = ""
    chat_provider: str = config.CHAT_PROVIDER
    cloud_fallback_enabled: bool = config.OLLAMA_CLOUD_FALLBACK
    timeout_seconds: int = config.LLM_TIMEOUT_SECONDS
    voice_enabled: bool = False

    def normalized(self) -> "AppSettings":
        """Clean fields in place and return `self` for compact call chains."""
        self.local_base_url = clean_url(self.local_base_url, config.OLLAMA_URL)
        self.cloud_base_url = clean_url(self.cloud_base_url, config.OLLAMA_CLOUD_URL)
        self.local_model = str(self.local_model or config.OLLAMA_MODEL).strip()
        self.cloud_model = str(self.cloud_model or config.OLLAMA_CLOUD_MODEL).strip()
        self.cloud_api_key = str(self.cloud_api_key or "").strip()
        self.chat_provider = normalize_provider(self.chat_provider)
        self.cloud_fallback_enabled = bool(self.cloud_fallback_enabled)
        self.voice_enabled = bool(self.voice_enabled)
        try:
            self.timeout_seconds = max(2, min(120, int(self.timeout_seconds)))
        except (TypeError, ValueError):
            self.timeout_seconds = config.LLM_TIMEOUT_SECONDS
        return self


class SettingsStore:
    """Thread-safe loader/saver for `data/settings.json`."""

    def __init__(self, path: Path = config.SETTINGS_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def load(self) -> AppSettings:
        """Load settings, falling back to defaults and `OLLAMA_API_KEY`."""
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                raw = {}
        settings = settings_from_dict(raw)
        if not settings.cloud_api_key:
            settings.cloud_api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
        return settings.normalized()

    def save(self, settings: AppSettings) -> None:
        """Write settings as sorted, human-readable JSON."""
        settings = settings.normalized()
        data = asdict(settings)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(data, indent=2, sort_keys=True),
                encoding="utf-8",
            )


def settings_from_dict(raw: dict[str, Any]) -> AppSettings:
    """Merge raw JSON data onto default settings, ignoring unknown keys."""
    defaults = AppSettings()
    values = asdict(defaults)
    for key in values:
        if key in raw:
            values[key] = raw[key]
    return AppSettings(**values)


def normalize_provider(value: str | None) -> str:
    """Return a supported provider name, defaulting invalid values to `auto`."""
    provider = str(value or "auto").strip().lower()
    return provider if provider in PROVIDERS else "auto"


def clean_url(value: str | None, fallback: str) -> str:
    """Trim a URL-like setting and fall back when it is empty."""
    text = str(value or "").strip().rstrip("/")
    return text or fallback
