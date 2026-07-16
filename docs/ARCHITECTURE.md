# Architecture

Desktop Pet is a Windows desktop companion built as a small PyQt6 application.
The app is organized around a transparent sprite widget, a task/chat panel, local
SQLite stores, and background monitors that feed proactive behavior.

## Runtime flow

`main.py` wires the application together:

1. Create the Qt application and tray menu.
2. Create stores for todos, work sessions, and settings.
3. Create monitors for active windows, input activity, and device load.
4. Create UI widgets: `PetRenderer`, `ChatBubble`, and `MiniBubble`.
5. Connect schedulers and behavior engines with Qt signals.
6. Start background polling, timers, hotkeys, and the event loop.

## Major modules

- `config.py`: default paths, timings, model names, thresholds, and feature flags.
- `pet/renderer.py`: transparent always-on-top sprite window, frame animation,
  alpha mask generation, click handling, and drag handling.
- `pet/behavior.py`: state machine for idle, talking, focus sleep, wake check-ins,
  drag release, and proactive speech.
- `pet/chat_bubble.py`: chat, task list, archive list, quick prompts, settings
  entry point, and todo extraction from LLM replies.
- `pet/mini_bubble.py`: lightweight speech bubble used for proactive nudges.
- `pet/work_tracker.py`: foreground app bucketing, idle detection, active-session
  tracking, and break-resume events.
- `pet/input_activity.py`: global keyboard hook that keeps keypress timestamps
  only.
- `pet/device_monitor.py`: CPU, memory, and battery snapshot for local LLM safety.
- `pet/smart_nudge.py`: proactive nudge selection and LLM/fallback message
  generation.
- `pet/window_tracker.py`: guarded Win32 window geometry helpers.
- `ai/llm_client.py`: Ollama local/cloud routing, model detection, timeouts, and
  user-facing failure messages.
- `data/todo_store.py`: SQLite-backed tasks with lightweight migrations.
- `data/work_store.py`: SQLite-backed work sessions.
- `data/memory_store.py`: SQLite-backed summarized long-term memory and compact
  rolling conversation context.
- `data/settings_store.py`: JSON-backed user settings, with `OLLAMA_API_KEY`
  fallback for the cloud key.
- `data/scheduler.py`: Qt timer that emits due-task, break, and return-from-break
  events.

## Threading model

Qt widgets stay on the main thread. Blocking operations run elsewhere:

- `WorkTracker` polls idle time and foreground buckets on a daemon thread.
- `ChatBubble`, `BehaviorEngine`, and `SmartNudgeEngine` call Ollama from daemon
  threads and return results through Qt signals.
- Stores use `threading.RLock` around SQLite work so UI and background threads can
  safely share them.

## Data model

The app creates `data/pet.db` at runtime. It contains:

- `tasks`: title, due date, done state, priority, notes, and timestamps.
- `work_sessions`: start/end time, active seconds, idle seconds, and a coarse app
  summary.
- `memory_items`: durable summarized facts such as preferences, project plans,
  recurring needs, and working style.
- `conversation_rollups`: one compact rolling chat summary used to preserve
  context across restarts without storing full transcripts.

`data/settings.json` stores model routing settings and can contain an API key.
Both files are ignored by Git.

## LLM routing

The default provider is `auto`:

- Use local Ollama when device load is below the configured CPU/RAM thresholds.
- If device load is high and cloud fallback is configured, route to Ollama Cloud.
- If local load is high and cloud fallback is unavailable, show a friendly warning
  instead of starting a local model call.

Chat prompts include current todos, current work state, recent coarse work
patterns, and saved summarized memory. Assistant replies may include hidden
`TODO_JSON` and `MEMORY_JSON` lines; the UI strips those lines before rendering
and persists the structured data locally.

## Asset loading

Runtime sprites are loaded from `assets/sprites/` first. `assets/CatPackFree/`
is used only as a fallback. Missing strips are replaced with generated placeholder
sprites so the app can still start.
