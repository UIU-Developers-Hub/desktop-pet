"""Application defaults for Desktop Pet.

Values in this module are intentionally simple constants so new contributors can
tune the app without tracing through the UI. Runtime LLM settings saved from the
Settings dialog override the Ollama-related defaults in this file.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "pet.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
SPRITE_DIR = BASE_DIR / "assets" / "sprites"
CAT_PACK_DIR = BASE_DIR / "assets" / "CatPackFree"

APP_NAME = "Desktop Pet"

# Ollama routing defaults. Saved settings take precedence after the user opens
# the Settings dialog.
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "nemotron-3-nano:4b"
OLLAMA_CLOUD_URL = "https://ollama.com"
OLLAMA_CLOUD_MODEL = "gpt-oss:120b"
OLLAMA_CLOUD_FALLBACK = True
LLM_TIMEOUT_SECONDS = 30
LLM_DEVICE_GATING = True
CHAT_PROVIDER = "auto"

# Long-term memory stores compact summaries, not raw chat transcripts.
MEMORY_CONTEXT_LIMIT = 12
MEMORY_MAX_ITEMS = 80
MEMORY_ROLLUP_MAX_CHARS = 1200

HOTKEY = "ctrl+alt+space"
PROACTIVE = True

# Work tracking and scheduler cadence.
POLL_INTERVAL_SECONDS = 5
IDLE_THRESHOLD_SECONDS = 5 * 60
BREAK_THRESHOLD_SECONDS = 5 * 60
BREAK_INTERVAL_SECONDS = 50 * 60
SCHEDULER_INTERVAL_SECONDS = 10

# Sprite and behavior timings.
SPRITE_FRAME_SIZE = 128
SPRITE_SCALE = 1
SPRITE_TICK_MS = 650
BEHAVIOR_TICK_MS = 250
REST_REACTION_MS = 2400
DRAG_START_DISTANCE = 6
LANDING_DURATION_MS = 520
TYPING_ACTIVITY_WINDOW_SECONDS = 4
INTENSE_TYPING_KEY_THRESHOLD = 10
WAKE_CHECKIN_MIN_SECONDS = 8
WAKE_CHECKIN_MAX_SECONDS = 12
WAKE_CHECKIN_COOLDOWN_SECONDS = 120
CHECKIN_DISPLAY_MS = 5500

SCREEN_MARGIN = 16

# Smart nudges use coarse todo/work/device context only. They should not include
# typed text, raw window titles, or screenshots.
SMART_NUDGE_CHECK_SECONDS = 120
SMART_NUDGE_COOLDOWN_SECONDS = 15 * 60
SMART_NUDGE_RESOURCE_ALERT_COOLDOWN_SECONDS = 10 * 60
SMART_NUDGE_MIN_ACTIVE_SECONDS = 10 * 60
SMART_NUDGE_DISTRACTION_SECONDS = 8 * 60
SMART_NUDGE_BREAK_SECONDS = 45 * 60
SMART_NUDGE_PRAISE_SECONDS = 25 * 60
SMART_NUDGE_JOKE_SECONDS = 20 * 60
SMART_NUDGE_JOKE_CHANCE = 0.22
SMART_NUDGE_MAX_CPU_PERCENT = 82
SMART_NUDGE_MAX_MEMORY_PERCENT = 88
