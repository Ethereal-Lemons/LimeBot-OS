# Changelog

## 1.0.7 - 2026-03-29
### Added
- Telegram channel scaffold with Bot API long polling, config loading, startup wiring, and tests.
- Telegram dashboard controls in Channels and Credentials so bot token, API base, allow lists, and polling timeout can be managed from the UI.
- Cron pause/resume controls in the dashboard and scheduler API, including persisted active state for jobs.

### Changed
- Persona files are now local-first runtime state. Fresh installs bootstrap `SOUL.md`, `IDENTITY.md`, and `MEMORY.md` from shipped `.example` templates instead of relying on tracked live persona files.
- Cron tooling now reports whether a job is active or paused.

### Fixed
- NVIDIA embedding provider resolution now maps to LiteLLM's supported `nvidia_nim/NV-Embed-v2` format and keeps legacy NVIDIA embedding config values working.
- Telegram integration is now usable end-to-end from the dashboard once the bot token is saved and the channel is enabled.

## 1.0.3 - 2026-02-26
### Added
- Discord personalization UI with per-guild/per-channel tone, verbosity, emoji usage, signatures, and embed theming.
- GitHub skill defaults and notifications: default repo/base/PR template, auto-labels/reviewers, and Discord/Web notifications.
- Skill dependency visibility in the UI, including required deps and missing-deps alerts.
- Unit tests for Discord personalization and WhatsApp safety checks.

### Changed
- Discord avatar override is now global-only (bots do not support per-guild avatars).
- Tool embeds are branded and themed for Discord.
- Skill metadata includes dependencies and per-skill requirements files.

### Fixed
- Reflection skips LLM calls when no journal exists (prevents setup-time errors).
