# Privacy Notes

Desktop Pet is designed to be local-first and conservative with user context.

## What is stored locally

- Todos, notes, due dates, priorities, and completion timestamps in `data/pet.db`.
- Work-session durations and coarse foreground app summaries in `data/pet.db`.
- Summarized assistant memory in `data/pet.db`, including durable preferences,
  project context, work style, and a compact rolling conversation summary.
- Ollama routing settings in `data/settings.json`.
- An Ollama Cloud API key in `data/settings.json` if the user saves one in
  Settings. Alternatively, the app can read `OLLAMA_API_KEY` from the environment.

## What is not stored

- Typed text from the keyboard hook.
- Raw chat transcripts.
- Raw foreground window titles.
- Full browser URLs.
- Screenshots or screen contents.

## What may be sent to an LLM

When the user chats or a proactive nudge runs, the model prompt may include:

- Open todo summaries.
- Recent completed todo summaries, treated as historical context.
- Saved memory summaries and the compact rolling conversation summary.
- Counts for open, due-today, and overdue tasks.
- Work summary text such as active time and current coarse app bucket.
- Recent coarse work patterns across completed sessions.
- Device telemetry such as CPU, memory, battery, and load status.

The app should not send raw window titles, keyboard-hook typed text, raw chat
transcripts, screenshots, secrets, or private files. Keep that boundary intact
when adding features.

## Local and cloud modes

Local Ollama calls go to the configured local host, usually
`http://localhost:11434`. Ollama Cloud calls go to the configured cloud host and
include the configured bearer token.

The `auto` provider uses cloud fallback only when it is configured and local
CPU/RAM is above the configured threshold.
