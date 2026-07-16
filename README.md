# Desktop Pet

Desktop Pet is a Windows desktop companion built with Python, PyQt6, SQLite, and
Ollama. It sits on the desktop as a transparent always-on-top sprite, opens a
task/chat panel on click or hotkey, tracks coarse work sessions locally, and can
offer gentle proactive nudges when the user has due tasks, a long active streak,
or high local device load.

The project is small enough to clone, run, inspect, and contribute to without a
large framework. It is also privacy-conscious: the keyboard hook keeps timing
metadata only, and foreground-window tracking stores coarse app buckets instead
of raw titles.

## Features

- Transparent draggable desktop pet sprite with idle, talking, reaction, and
  sleep/rest animation states.
- System tray app with quick access to chat, work-status questions, and quit.
- Global hotkey support, defaulting to `Ctrl+Alt+Space`.
- Task planner with quick add, optional due dates, priority, notes, filters,
  completion, restore, delete, and archive views.
- Local SQLite storage for tasks and work sessions.
- Work tracking based on Windows idle time and coarse foreground app buckets.
- Persistent summarized memory for user preferences, project plans, and working
  style, so chat context survives restarts without storing raw transcripts.
- Focus-sleep mode during intense typing, followed by a short check-in after the
  user pauses.
- Proactive nudges for due tasks, long active streaks, break returns,
  distractions, overdue tasks, praise, jokes, and local resource pressure.
- Ollama local chat support, Ollama Cloud support, and automatic cloud fallback
  when local CPU/RAM is high and cloud credentials are configured.
- Device telemetry gate that avoids starting local model calls when the machine
  is already busy.
- Runtime placeholder sprites when image assets are missing, so the app can
  still start.
- Markdown rendering in assistant replies.

## Requirements

- Windows 10 or newer.
- Python 3.11 or newer.
- Ollama for local model calls.
- A pulled local model, by default `nemotron-3-nano:4b`.
- Optional: an Ollama Cloud API key for cloud routing/fallback.

The app uses Windows-specific dependencies (`pywin32`, `keyboard`, and Win32 idle
APIs), so Linux/macOS are not supported runtime targets.

## Quick Start

Clone the repository, create a virtual environment, install dependencies, and
run the app:

```powershell
git clone <your-fork-or-repo-url> desktop-pet
cd desktop-pet
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

For local chat, make sure Ollama is running and the configured model is
available:

```powershell
ollama serve
ollama pull nemotron-3-nano:4b
ollama list
```

If Ollama is not reachable, the app still opens and returns friendly fallback
messages for model-backed features.

## Usage

- Left-click the pet, left-click the tray icon, or press `Ctrl+Alt+Space` to
  open the task/chat panel.
- Right-click the tray icon for the menu.
- Drag the pet to move it temporarily; it snaps back to the bottom ground line on
  release.
- Use the Tasks tab to add, filter, complete, restore, and delete tasks.
- Use the Chat tab to ask planning questions or create todos in natural
  language.
- Open Settings from the panel to choose local/cloud routing, set hosts/models,
  detect models, and save an Ollama Cloud API key.

## Configuration

Default values live in `config.py`. User-edited LLM settings are saved to
`data/settings.json`, which is ignored by Git because it can contain a cloud API
key.

Common settings:

```python
HOTKEY = "ctrl+alt+space"
PROACTIVE = True
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "nemotron-3-nano:4b"
OLLAMA_CLOUD_URL = "https://ollama.com"
OLLAMA_CLOUD_MODEL = "gpt-oss:120b"
CHAT_PROVIDER = "auto"
LLM_TIMEOUT_SECONDS = 30
LLM_DEVICE_GATING = True
```

Smart nudge and work tracking thresholds are also in `config.py`, including
typing intensity, wake-check-in delays, idle thresholds, break intervals, CPU/RAM
limits, and proactive cooldowns.

## Data and Privacy

Runtime data is created under `data/`:

- `data/pet.db`: tasks, work sessions, and summarized assistant memory such as
  durable preferences, project context, and rolling conversation rollups.
- `data/settings.json`: local/cloud model routing settings and optional cloud API
  key.

The app does not store raw chat transcripts, keyboard-hook typed text, raw window
titles, screenshots, or screen contents. See [docs/PRIVACY.md](docs/PRIVACY.md)
for the privacy boundary that contributors should preserve.

## Project Layout

```text
desktop-pet/
  main.py                  # Qt application composition and tray wiring
  config.py                # Defaults for paths, timings, models, and thresholds
  ai/
    llm_client.py          # Ollama local/cloud client and routing
  data/
    scheduler.py           # Qt timer for due tasks and break events
    settings_store.py      # JSON settings loader/saver
    todo_store.py          # SQLite todo store
    work_store.py          # SQLite work-session store
    memory_store.py        # SQLite summarized memory store
  pet/
    behavior.py            # Pet state machine and proactive behavior
    chat_bubble.py         # Chat, planner, archive, and todo extraction UI
    device_monitor.py      # CPU/RAM/battery snapshots
    hotkeys.py             # Global hotkey wrapper
    input_activity.py      # Timestamp-only keyboard activity monitor
    mini_bubble.py         # Lightweight proactive speech bubble
    renderer.py            # Sprite rendering, masking, dragging, and animation
    settings_dialog.py     # LLM routing/settings dialog
    smart_nudge.py         # Smart nudge selection and message generation
    window_tracker.py      # Foreground window geometry helpers
    work_tracker.py        # Idle/work-session tracker
  assets/
    sprites/               # Runtime sprite strips
  docs/
    ARCHITECTURE.md
    PRIVACY.md
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for runtime flow, threading,
data model, LLM routing, and asset loading notes.

## Development

Install dependencies and run the app from the repository root:

```powershell
pip install -r requirements.txt
python main.py
```

Run the syntax check before submitting changes:

```powershell
python -m compileall -q main.py config.py ai data pet
```

There is not a full automated test suite yet. When changing behavior, manually
test the affected tray, panel, task, tracking, or LLM workflow on Windows.

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), keep
changes focused, and update docs when behavior or privacy boundaries change.

Important contribution rules:

- Do not persist typed text.
- Do not store or send raw foreground window titles.
- Keep blocking LLM/network calls off the Qt UI thread.
- Keep Windows-only imports guarded so modules remain easy to inspect.
- Do not commit `.venv/`, `data/pet.db`, `data/settings.json`, `__pycache__/`, or
  other generated local state.

## License

MIT. See [LICENSE](LICENSE).
