# Security Policy

## Supported versions

This project is currently pre-release. Security fixes should target the default
branch unless maintainers create release branches later.

## Reporting a vulnerability

For a public fork, add a private contact address before publishing. Please do
not open a public issue for secrets exposure, data leakage, or command execution
bugs until maintainers have had a chance to review the report.

## Sensitive areas

- Keyboard monitoring must only keep timing metadata, never typed content.
- Foreground-window tracking must store coarse app buckets, never raw titles.
- `data/settings.json` can contain an Ollama Cloud API key and is ignored by Git.
- LLM prompts should only include task summaries, work summaries, coarse app
  buckets, and device telemetry.
