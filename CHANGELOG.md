# Changelog

## 1.0.8 - 2026-03-31
### Added
- Lightweight subagent system with built-in specialist profiles such as reviewer, verifier, and explorer.
- New Subagents dashboard page to create, edit, delete, and manage specialist profiles visually.
- Sidebar assistant mode selector for choosing the default subagent behavior.
- Structured subagent report cards in chat so delegated results are easier to read than raw orchestration text.
- Support for project and user subagent directories in both `.limebot/agents` and `.claude/agents`.
- Unit coverage for subagent registry loading, shadowing, selection, and tool schema behavior.

### Changed
- `spawn_agent` can now target named specialist profiles and optionally run in the background.
- Prompt guidance now includes subagent recommendations so delegation is more intentional and task-matched.
- Tool definitions now advertise available named subagents to the model.
- Chat UI suppresses noisy empty orchestration traces around delegated subagent work.
- RAG trace handling is more defensive when trace buckets are missing or malformed.

### Fixed
- Subagent API responses now use the same loaded registry instance for definitions, default selection, and selector options.
- Explicit `tools: []` for subagents now correctly means “no tools” instead of falling back to inherited tools.
- Tool alias normalization now handles `read_filejson`-style names more safely.
- Voice Preview Studio channel cards no longer overflow when labels and badges get tight on smaller widths.

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
