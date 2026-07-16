# Contributing

Thanks for helping improve Desktop Pet. This project is intentionally small:
a Windows PyQt desktop app, a local SQLite store, and an Ollama-backed planning
assistant.

## Development setup

1. Use Windows with Python 3.11 or newer.
2. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

4. Start Ollama if you want live chat features:

   ```powershell
   ollama serve
   ollama pull nemotron-3-nano:4b
   ```

5. Run the app:

   ```powershell
   python main.py
   ```

## Contribution workflow

1. Open an issue or describe the change clearly in your pull request.
2. Keep changes focused. Separate UI, storage, tracking, and LLM-routing changes
   when possible.
3. Run the syntax check before submitting:

   ```powershell
   python -m compileall -q main.py config.py ai data pet
   ```

4. Manually test the affected workflow from the tray app.
5. Update `README.md`, `docs/ARCHITECTURE.md`, or `docs/PRIVACY.md` when behavior,
   setup, storage, or telemetry changes.

## Code style

- Prefer clear names and small functions over clever abstractions.
- Keep comments short and useful. Explain contracts, threading, privacy, or
  platform assumptions.
- Do not store or send raw typed text.
- Do not store raw chat transcripts; use summarized memory items and rollups for
  durable context.
- Do not persist raw foreground window titles. Use coarse buckets instead.
- Keep long-running or blocking work off the Qt UI thread.
- Protect SQLite writes with the existing store locks.
- Keep Windows-only imports guarded so modules remain importable on other
  platforms for inspection and syntax checks.

## Pull request checklist

- [ ] The app starts with `python main.py` on Windows.
- [ ] `python -m compileall -q main.py config.py ai data pet` passes.
- [ ] Local runtime files such as `.venv/`, `data/pet.db`, and
      `data/settings.json` are not committed.
- [ ] New behavior is documented.
- [ ] Privacy-sensitive changes are called out in the PR description.
