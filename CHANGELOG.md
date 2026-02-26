# Changelog

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
