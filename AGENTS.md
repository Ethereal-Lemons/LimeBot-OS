# 🍋 AGENTS.md — LimeBot Developer & Agent Reference

This document is the authoritative source of truth for the LimeBot codebase. It is read by developers building on LimeBot **and** by coding agents working in this repository. Everything here is accurate to the current implementation.

It is **not** injected into the live chat model system prompt. Chat turns use the compact persona + tool rules in `core/prompt.py`. Do not dump this encyclopedia into the model context.

Documentation split:
- `README.md` is the user-facing setup guide for installing LimeBot and the browser companion.
- `extension/README.md` is the user-facing setup guide for the extension itself.
- `AGENTS.md` stays implementation-focused and should not be used as the main onboarding doc for end users.

---

## 🏛️ Architecture Overview

LimeBot is an **event-driven agentic system**. Every user interaction is an `InboundMessage` that flows through an async message bus, gets processed by the agent loop, and produces `OutboundMessage` events routed back to the originating channel.

```
Channel (Discord / WhatsApp / Web / Telegram) or CronManager
        │  InboundMessage
        ▼
   DurableJobQueue (SQLite) for cron, queued work, and live companion/web chat
        │  then MessageBus (asyncio.Queue)
        ▼
   AgentLoop._process_message()
     ├─ Auto-RAG (vector search + grep)
     ├─ Stable prompt cache (30s TTL)
     ├─ LLM call, retries, and provider failover (`core/llm_call_service.py`)
     ├─ `core.stream_consumer.consume_stream` (stream events and output)
     ├─ `core.tool_call_parser` (serialized tool-call recovery)
     ├─ Tool execution loop (up to `MAX_ITERATIONS`, default 40)
     ├─ Tag processing (save_soul, log_memory, etc.)
     └─ OutboundMessage → bounded per-channel worker → Channel.send()
```

---

## 📁 Core Module Reference

### `core/loop.py` — AgentLoop

The heart of the system. Manages:

- **Session history** per `session_key` (`channel_chatid`), with mutations and
  dirty-flag persistence owned by `TurnHistoryService`
- **Stable prompt cache** — the rarely-changing part of the system prompt (soul + identity + user context) is cached for 30 seconds per `(sender_id, channel)` pair. Only the volatile suffix (memory, RAG results, timestamp) is rebuilt each message.
- **Auto-RAG** — `core/turn_preparation_service.py` runs bounded semantic vector search (then Markdown grep fallback) concurrently with history loading and injects matching memories into the prompt automatically.
- **Tool execution loop** — after each LLM response, if tool calls are returned, executes them in parallel and loops back to the LLM (up to `MAX_ITERATIONS`, default 40). Sensitive tools require user confirmation.
- **Durable task runs** — state-changing and coding requests receive a persistent task-run identity, acceptance criteria, checkpoints, continuation slices, and crash-resume metadata. A turn can return a progress update while the same task continues from its checkpoint.
- **Operator invariants** (`core/operator_invariants.py`) — the loop, not the prompt, gates terminal success for durable / coding / file-mutating / isolated-subagent task runs. Every eligible non-empty turn outside explicit read-only Plan mode receives the profile- and channel-available tool catalog; the model chooses whether to call a tool.
  1. **VERIFY** — a mutating turn cannot go `completed` / Ready / IDLE claiming success until `verify_files` (or the tests/commands the task used as proof) ran on the files the turn changed. Missing proof refuses success: the loop injects a continuation that requires verify, or completes as `blocked` with the missing-proof named in outbound chat.
  2. **MERGE** — copy-isolated `spawn_agent` work that retained a changeset cannot succeed while that clone is unapplied. The parent must call `apply_workspace_changeset`; the live tree stays unchanged until apply. An unapplied leftover is a hard stop that names `workspace_id` in chat, not a leftover directory the user is expected to notice.
  3. **NO SILENT FAIL** — tool errors, rejections, policy denials, verify failures, refused merges, and blocked task-run terminals must appear in the user-visible outbound turn. Ready/IDLE with empty chat after a failure is a loop bug. Child sub-agent failures are copied into the parent `SUB-AGENT REPORT`.
- **Progress-aware recovery** — typed tool failures classify invalid arguments, skill defects, dependencies, authentication, policy, timeouts, transient providers, command failures, cancellation, and unknown errors. Diagnostics are free; corrective recovery is bounded by `TOOL_RECOVERY_MAX_CORRECTIVE_FAILURES` (default 5), and duplicate no-progress actions are gated before execution. Recovery evidence does not by itself mark a task complete; VERIFY still has to pass.
- **Sub-agent delegation** — `spawn_agent` creates an isolated session that runs its own tool loop, can use named specialist profiles, defaults coding/review work to a temporary copy-on-write workspace, captures a bounded structured diff, and reports back to the parent without merging changes implicitly. The parent loop then enforces MERGE: success requires `apply_workspace_changeset`, or the turn is blocked with the unapplied `workspace_id`.
- **Capability readiness gate** — skill, subagent, MCP, and tool discovery run through explicit startup phases. User turns wait for required skills/tools before prompt or schema construction; optional MCP failures produce `degraded` readiness rather than blocking chat. Embedding and LLM warmups are not part of the required gate.
- **Per-session dedup** — identical consecutive messages within 2 seconds are silently dropped, keyed per session (not globally).
- **History budget** — `_context_budget_tokens()` allows half of the model's input window (clamped to 12k–120k tokens; 48k when the window is unknown; override with `LIMEBOT_CONTEXT_TOKENS`). Only when history exceeds it are the oldest tool results shrunk to a 1,200-character head/tail digest (the current turn and the 8 most recent results are never touched); a single very long turn is also shrunk past 1.5x the budget; if still over at the next turn, older messages are summarized (up to 400 words, keeping paths, commands and decisions).
- **Stream consumption** — `core/stream_consumer.py` aggregates provider chunks, streams web output, records usage and timing, and hands tool syntax to the parser. `AgentLoop._consume_stream` remains a compatibility wrapper that supplies the active bus and callbacks.
- **Text-carried tool calls** — when a provider puts a tool call in assistant
  text instead of its structured field, `core/tool_call_parser.py` recovers the
  supported wire formats and removes their syntax from the user-visible reply.
- **Provider failover** — retry timing, circuit-breaker decisions, fallback
  routing, and user-visible provider notices live in `core/llm_call_service.py`.
  `AgentLoop._llm_call_with_retry` keeps the established call boundary and
  injects the active provider, tool, image, logging, and channel callbacks.

**Key configuration knobs (set via `self.config`):**
- `config.llm.model` — active chat model
- `config.llm.enable_dynamic_personality` — enables mood, affinity, and proactive jobs
- `config.autonomous_mode` — bypasses confirmation gate for sensitive tools
- `config.discord.voice_enabled` — enables the optional experimental Discord voice conversation path
- `config.whitelist.allowed_paths` — roots for filesystem access

### `core/tool_recovery_service.py` — Recovery Coordination and Visibility

Owns recovery tool-catalog rebuilding, exact tool contracts, retry instructions,
the corrective-failure budget, recovery event sequencing, coding step-cap
fallbacks, and user-visible tool-failure text. `ToolRecoveryRequest` and
`ToolRecoveryResult` define the queue boundary, including whether recovery was
queued. The service decides from current outcomes whether to queue another
model step. `AgentLoop` keeps compatibility methods and supplies current
outcomes, history append, dirty marking, and progress/session-event callbacks;
it applies the returned block/queue status to the turn. `core/recovery_controller.py`
remains the source of truth for evidence generations, no-progress gates, and
recovery state.

### `core/task_runs.py` — Durable Task Runs

Stores task-run state in `data/task_runs.sqlite` so multi-step work survives a single
LLM turn and process restarts. Each run records the original goal, derived acceptance
criteria, phase, checkpoint, current action, provider, workspace, continuation slices,
and corrective-failure budget. Terminal outcomes are `completed`, `blocked`, or
`cancelled`; interrupted active runs are returned to `retrying` on startup.

### `core/recovery_controller.py` — Recovery Policy

Classifies tool outcomes and tracks evidence generations, action signatures, mutations,
inspection, verification, and corrective budget. On recovery, LimeBot rebuilds the tool
catalog from registered names and supplies the failing tool plus exact diagnostic,
editing, verification, and command tools. Invalid-argument retries require successful
source/schema inspection first. Authentication, policy, cancellation, and non-editable
source blockers remain hard stops. `verification_state` is evidence for recovery only;
`core/operator_invariants.py` is what refuses terminal success without proof.

---

### `core/tool_outcomes.py` — Typed tool results

Defines the frozen `ToolOutcome` evidence used by recovery and task runs. It
recognizes tool errors and command exit codes, derives task phases, classifies
failures, and stores only bounded redacted diagnostic excerpts. Recovery reads
these outcomes; it does not own terminal-success policy.

### `core/tool_runtime.py` — Per-call tool runtime

Owns one tool call from profile authorization and host preflight through
dispatch, profile-scoped cache lookup/invalidation, and error conversion.
`ToolExecutionContext` is frozen and carries the session key, user text,
attachments, and active profile explicitly. `AgentLoop` composes the runtime
with its browser, search, background-task, and tag handlers. Approval
authorization and UI coordination use `ToolApprovalFlowService`; per-call
event publication uses `ToolBatchEventService`; execution and recovery
integration remain coordinated by `AgentLoop`. Retry safety and cacheability
are separate: mutable browser page state is not cached.

### `core/capability_service.py` — Capability routing and inventory

Owns sticky per-session skill routing, full profile-filtered tool catalog exposure, compact
capability prompt construction, and the redacted capability resolver used by
chat and the dashboard API. It receives explicit callbacks for registries,
tool definitions, session state, attachments, and debug events. `AgentLoop`
keeps its existing methods as compatibility wrappers.

### `core/browser_search_service.py` — Browser and host-owned search

Owns typed browser command execution and host-search orchestration. Its private
`BrowserSearchToolAdapter` translates the existing tool names and argument
objects into typed requests. `ToolRuntime` retains profile authorization,
capability preflight, routing, and caching. The service receives the
session-scoped browser manager, progress callback, and chat-image attachment
callback; `core.web_search.py` retains engine selection, parsing, retries, and
response formatting. Explicit source URLs keep normal browser, search, and media
tools available so the agent can choose how to inspect or deliver the content.

### `core/tool_call_parser.py` — Text-carried tool-call recovery

Provides pure functions to recover supported provider-native tool-call text
formats and remove tool syntax from assistant prose. It handles the legacy JSON,
XML, Kimi, Python-like, and provider envelope forms. Stream consumption calls
the parser; parsing does not depend on `AgentLoop` or mutable turn state.

### `core/stream_consumer.py` — Provider stream consumption

Owns streamed chunk aggregation, web chunk/thinking/activity publication,
duplicate-prefix handling, rate-limit notices, usage updates, timing, and
structured or text-carried tool-call collection. The pure text syntax logic
lives in `core/tool_call_parser.py`. `AgentLoop._consume_stream` remains a
compatibility wrapper and injects the active bus, metrics, session manager, and
trace/debug callbacks.

### `core/llm_call_service.py` — Provider retry and failover

Owns the LLM request retry loop, provider circuit-breaker updates, authentication
and transient-error failover, Qwen endpoint retries, image-input fallback, and
user-visible provider progress messages. `ProviderErrorTypes` carries the
active provider exception classes so `AgentLoop` compatibility patches and
provider-specific error handling stay aligned.

### `core/tool_batch_executor.py` — Tool-call ordering

Runs adjacent read-only tool calls concurrently while preserving their result
order. Every mutation or shell call flushes pending reads and runs as a barrier,
so write-then-verify causality stays intact. After execution it consumes results
in model-call order, so event publication and history writes cannot race across
calls. `AgentLoop` supplies the per-call execution and result callbacks, invokes
`ToolApprovalFlowService`, and coordinates the surrounding turn. It delegates
history mutation/persistence to `TurnHistoryService` and event payloads plus
tool-start/finish audit events to `ToolBatchEventService`.

### `core/tool_batch_events.py` — Tool-call event lifecycle

Owns planned, running, result, and exception event payloads plus per-session
tool-started/tool-finished audit records. It preserves sensitive-argument
redaction, result truncation, turn/message attribution, WhatsApp progress text,
and best-effort delivery for terminal UI events. `AgentLoop` supplies its
existing parsing, preview, delivery, and audit callbacks.

### `core/confirmation.py` — Confirmation state

Builds sensitive-tool previews and embeds, owns pending confirmation records
and per-session approval whitelists, and handles approval resolution, session
release, and waiter cleanup. `AgentLoop` exposes dict-shaped compatibility
properties for channels and integrates the approval result with the batch flow.

### `core/tool_approval_flow.py` — Tool approval coordination

Owns the per-call approval flow: policy decisions, unattended denials,
auto-allow audit events, confirmation previews and embeds, pending request
publication to the originating channel and web dashboard, and wait outcomes
for approval, denial, timeout, and cancellation. `AgentLoop` supplies the
policy, manager, profile, event, and delivery callbacks and keeps its existing
batch entry point and result shape.

### `core/approval_policy.py` — Tool approval decisions

Resolves the effective global/profile approval policy, unattended allowlists,
channel exceptions, and session whitelist keys for one tool call. It is
stateless and receives the current config and session whitelist from
`AgentLoop`; pending state is owned by `ConfirmationManager`.

### `core/discord_tool_service.py` — Discord tools

Owns message/DM delivery, embed validation and delivery, and live guild text
channel listing. It publishes through LimeBot's existing outbound bus; the
`Toolbox` wrappers keep the registered tool names and argument schemas stable.

### `core/prompt.py` — Prompt Builder

Builds the system prompt from persona files. Two-part architecture:

**`build_stable_system_prompt()`** — rarely changes, cached 30s:
- Injects `SOUL.md` + `IDENTITY.md`
- Channel-specific style override (Discord → Web → General fallback chain)
- Dynamic personality block (affinity score → behavior tier) if enabled
- User context from `persona/users/{sender_id}.md`
- Skills system prompt additions
- Filesystem access declaration

**`get_volatile_prompt_suffix()`** — rebuilt every message:
- Auto-RAG recalled context
- Today's episodic memory journal (last 5 entries + long-term essence preview),
  cached by date/privacy/path/mtime/size until either source file changes
- Current timestamp

**Persona validation functions** (all use atomic temp-file writes + timestamped backups + auto-rotation to 3 most recent):
- `validate_and_save_soul(content)` — requires 100+ chars, soul keyword presence
- `validate_and_save_identity(content)` — requires `**Name:**`, `**Style:**`, 50+ chars
- `validate_and_save_mood(content)` — atomic write, no content minimum
- `validate_and_save_relationships(content)` — atomic write for `RELATIONSHIPS.md`

**Setup mode** — on startup, LimeBot bootstraps local `SOUL.md`, `IDENTITY.md`, and `MEMORY.md` from their `.example` templates when they are missing. If `SOUL.md` or `IDENTITY.md` are still missing or invalid after that, `is_setup_complete()` returns False and the entire system prompt is replaced with a setup interview prompt. The agent must emit `<save_soul>` and `<save_identity>` tags with valid content to exit setup mode.

The web first-run wizard completes through `POST /api/setup/complete`. The backend atomically writes configuration, performs a real minimal LLM request, and schedules exactly one restart only after validation succeeds. The browser stores only a restart token, boot ID, model ID, and timestamp in `sessionStorage`, so it can resume after a refresh without persisting provider credentials there. It waits for a new backend boot ID instead of assuming a fixed restart duration. Once `APP_API_KEY` exists, API authentication is active even while the persona interview is still incomplete.

### `core/prompt_coordinator.py` — Prompt Assembly

Owns stable-prompt reads and the 30-second `(sender_id, channel)` cache, then
assembles per-message capability, skill, subagent, ponytail, and volatile memory
context. Persona updates invalidate one sender's entries; model and tool
definition changes clear the cache. `AgentLoop` keeps its prompt methods as
compatibility wrappers and supplies capability routing/catalog callbacks.

### `core/turn_preparation_service.py` — Pre-inference context

Runs bounded Auto-RAG and persisted-history loading concurrently before prompt
creation. It records recall and load timings, handles skipped, timed-out, and
failed retrievals, and returns recalled context plus restored history in a
small result value. `AgentLoop` supplies vector, session, activity, and trace
callbacks.

### `core/conversation_image_service.py` — Conversation image context

Owns image-input deduplication, short-lived image references per session,
image-safe history markers, image eviction before persistence, and retry
fallback for text-only models. `AgentLoop` keeps the existing image helpers as
compatibility wrappers and supplies history, metrics, and rendering callbacks.

### Agent profiles — Persistent conversational roster

- `core/profiles.py` stores validated profile metadata atomically in
  `data/profiles.json`. The `default` profile preserves the legacy persona and
  session locations; named profiles use `persona/profiles/{profile_id}/` and
  profile-prefixed session keys so history stays separate.
- `core/profile_context.py` binds profile ID, model, and persona paths to the
  current asyncio task. Concurrent turns do not mutate the shared
  `AgentLoop.model`.
- `core/paths.py`, `core/vectors.py`, `core/prompt.py`, `core/tag_parser.py`,
  `core/reflection.py`, and `core/tools.py` use the active profile's persona
  and Markdown memory. Named profiles cannot read one another's persona or
  memory, or access the legacy default persona through file/search tools.
- `core/profile_api.py` exposes authenticated create, update, archive, restore,
  and persona routes under `/api/profiles`. Channel adapters remain on
  `default`; web chat and the browser companion send an explicit profile ID.
- Profile tool allowlists only restrict LimeBot's registered tools. Profile
  path rules are intersected with global allowed paths, and a profile cannot
  weaken the global approval mode. Named profiles have no shell or sub-agent
  access by default; they require explicit tool opt-in, and any profile path
  restriction disables those shared-workspace tools. This is an application
  policy boundary, not OS-level isolation for shell commands.
- Profile IDs accompany session metadata, task and scheduled-job context,
  approval requests, and tool events. Archiving keeps profile data and history
  for later restoration; the built-in `default` profile cannot be archived.

### `core/inbound_turn_lifecycle.py` — Managed inbound turn lifecycle

Owns dispatch and execution lifecycle for one inbound message: profile
preflight, task-run follow-up matching, durable-job claiming, tracker/registry
registration, cancellation, durable-job heartbeats, cron completion, and
active-task cleanup. `AgentLoop._dispatch_message()` and
`_run_managed_message()` remain compatibility wrappers; the registry, task
tracker, job queue, and scheduler remain the authoritative stores.

### `core/memory_tool_service.py` — Memory tool domain

Implements Markdown-backed memory search and save behind an explicit storage
boundary. `Toolbox` supplies the active profile's Markdown paths and vector
service; Markdown remains the source of truth if vector indexing is unavailable.

### Tool-domain services

- `core/command_validation.py` owns deterministic command-policy validation.
  `Toolbox.validate_command()` supplies the active workspace, project root, and
  unsafe-command setting.
- `core/command_execution_service.py` owns validated process startup, streamed
  output, stall/timeout handling, process-tree cancellation, and skill
  dependency diagnostics. `Toolbox` supplies validation, sanitized environment,
  workspace, progress, and browser-launch callbacks.
- `core/file_search_service.py` owns bounded directory listing, filename/content
  search, pagination/sorting, ripgrep integration, and the Python fallback.
  `Toolbox` supplies active path resolution, allowlist checks, and display paths.
- `core/file_read_service.py` owns bounded text reads, line slicing, hashing
  metadata, and DOCX/PDF extraction. `Toolbox` supplies active path policy,
  workspace resolution, and raw-file hashing.
- `core/file_mutation_service.py` owns bounded writes, hash-guarded edits,
  deletion, file verification, and optional diagnostics. `Toolbox` supplies path
  policy, persona restrictions, workspace resolution, atomic replacement,
  sanitized environment, and compatibility wrappers.
- `core/subagent_workspace_service.py` owns subagent launch, isolated-copy
  retention, explicit changeset application, and clone cleanup. `Toolbox` supplies
  the active agent/registry, workspace root, and guarded file-operation callbacks.
- `core/calculator.py` contains the bounded, side-effect-free arithmetic
  evaluator used by `Toolbox.calculate()`.
- `core/cron_tool_service.py` owns profile-aware scheduler operations used by
  the cron tools. `Toolbox` keeps compatibility wrappers for existing callers.
- `core/discord_tool_service.py` owns Discord message/embed delivery and live
  channel listing; the `Toolbox` methods preserve the registered tool surface.
- `core/skill_tool_service.py` owns skill inspection, transactional edits via
  `SkillEditor`, and skill creation. Skills created in an isolated workspace
  remain there and do not change global enablement.
- `core/spreadsheet_tool_service.py` owns worksheet validation, XLSX styling,
  and atomic workbook creation. `Toolbox` supplies the existing path-policy
  callbacks and preserves the registered tool surface.
- `core/voice_tool_service.py` owns on-demand voice synthesis and publication
  through the current chat bus; automatic voice delivery remains in the loop.
- `core/discord_voice.py` owns optional per-guild Discord voice sessions,
  per-speaker utterance buffering, silence-based turn splitting, temporary WAV
  creation, ElevenLabs transcription, and reply playback. It starts only when
  `ENABLE_DISCORD_VOICE=true` and the optional `discord-voice` CLI feature has
  installed the pinned Pycord DAVE-receive build. It refuses to listen until the
  voice client's DAVE session is ready. Audio files are removed after STT.
- `core/safe_http_fetch.py` owns public-address checks, redirect-hop validation,
  response-size limits, and download type detection for URL downloads and
  readable pages.
- `core/url_tool_service.py` owns safe URL downloads and readable-page
  extraction, including bounded fetches, text cleanup, truncation, and download
  persistence. `Toolbox` supplies its allowed download directory, progress,
  display-path, and safe-fetcher callbacks.
- `core/image_provider_service.py` owns image backend selection and provider
  request/response handling for Codex, Gemini, OpenAI, and LiteLLM.
- `core/image_generation_service.py` owns prompt/reference workflow, provider
  fallback, and local image saving. `Toolbox` supplies provider wrappers and
  output policy; generated previews are published by `MediaDeliveryService`.
- `core/image_reference_service.py` collects explicit or attached reference
  images, checks shareability, enforces format and size limits, and loads the
  bytes passed to image providers. `Toolbox` supplies its path-policy callback.
- `core/media_delivery_service.py` owns current-chat file publication,
  generated-image previews, and per-turn duplicate suppression. `Toolbox`
  supplies path-policy and safe-fetch callbacks.
- `core/capability_service.py` owns sticky per-session skill routing,
  full profile-filtered tool catalog exposure, capability prompt assembly, and the redacted
  capability inventory. `AgentLoop` preserves its existing public and prompt
  callbacks as compatibility wrappers.
- `core/video/` owns the optional bounded video-analysis pipeline; `Toolbox`
  exposes it through the registered tool wrapper. Path policy lives in
  `core/tool_path_policy.py` behind `Toolbox` compatibility wrappers.

### Task-run services

- `core/task_run_policy.py` owns the pure acceptance decision that combines
  verification evidence with operator invariants.
- `core/task_run_continuation.py` owns checkpoint persistence and the durable
  queue/MessageBus handoff for a continuation slice. The task-run store,
  durable job queue, and bus remain the state authorities.

### `core/task_eval.py` — Offline task evaluation

Runs synthetic replay scenarios against two source snapshots in separate
temporary copies. Reports contain normalized outcomes, assertion codes, tool
counts, and rounded timings, not raw prompts, replies, fixtures, or persona
text. The allowlisted worker is a trusted-code runner, not an OS sandbox for
malicious source snapshots. Use `python scripts/evaluate_tasks.py compare`
for a local baseline/candidate comparison; see `evals/README.md`.

---

### `core/subagents.py` — Subagent Registry

Discovers, loads, and describes named subagent profiles from built-ins and writable project/user locations.

- **Built-in specialists** — ships with named profiles such as `reviewer`, `verifier`, and `explorer`
- **Prompt injection** — `get_prompt_additions(current_message)` adds available subagents and their descriptions to the prompt; the model chooses whether a specialist fits the task
- **Selection-aware routing** — if a global default specialist is selected, the prompt nudges the model to prefer that specialist unless the user asks otherwise
- **Project shadowing** — project-defined subagents can override built-in ones with the same name

This registry is what makes `spawn_agent` more than a generic worker launcher: the model can compare each specialist's description and tool access.

### `core/subagent_runner.py` — Subagent execution

Owns a delegated subagent's isolated workspace lifecycle, profile prompt,
multi-turn LLM/tool loop, bounded workspace diff report, parent notification,
and cleanup. `AgentLoop.run_subagent` remains the compatibility entry point and
injects the current model, session manager, tool callbacks, message bus, and
persona paths.

---

### `core/tag_parser.py` — XML Tag Processor

Called after every LLM response. Strips recognized tags, executes side effects, returns cleaned reply text.

| Tag | Effect |
|-----|--------|
| `<save_soul>...</save_soul>` | Validates and atomically overwrites `SOUL.md`. Invalidates stable prompt cache. |
| `<save_identity>...</save_identity>` | Validates and atomically overwrites `IDENTITY.md`. Invalidates stable prompt cache. |
| `<save_user>...</save_user>` | Validates (injection check + 20 char min) and atomically writes `persona/users/{sender_id}.md`. |
| `<save_mood>...</save_mood>` | Writes `MOOD.md` if `validate_mood` callable is provided. |
| `<save_relationship>...</save_relationship>` | Writes `RELATIONSHIPS.md` if `validate_relationship` is provided and `ENABLE_DYNAMIC_PERSONALITY=true`. |
| `<log_memory>...</log_memory>` | Appends a timestamped entry to today's daily journal. Queues a vector embedding in the background. The native `memory_save` tool is preferred for explicit user requests. |
| `<save_memory>...</save_memory>` | Overwrites `MEMORY.md` (long-term essence). Queues vector embedding. The native `memory_save` tool is preferred for explicit user requests. |
| `<discord_send>...</discord_send>` | Publishes a message to the specified Discord `channel_id`. Always routes to the `discord` channel regardless of originating context. |
| `<discord_embed>...</discord_embed>` | Publishes a rich embed to the specified Discord `channel_id`. |

**All tags are stripped from the reply shown to the user.**

---

### `core/tool_path_policy.py` — Tool Path Policy

Owns active-workspace path remapping, global and profile allowlists, protected
persona namespaces, and outbound file-sharing checks. Each policy decision uses
a snapshot of the active roots and profile context. `Toolbox` retains its
existing path methods as compatibility wrappers.

### `core/tools.py` — Toolbox

Sandboxed OS interface. Filesystem methods check `_is_path_allowed()` before
touching the filesystem; that compatibility method delegates to
`core/tool_path_policy.py`.

**Path security rules:**
- Only paths under `ALLOWED_PATHS` (+ project root) are accessible
- Hard-blocked filenames: `.env`, `limebot.json`, `config.py`, `secrets.py`, `package-lock.json`
- Hard-blocked extensions: `.pem`, `.key`, `.p12`, `.pfx`
- `.env*` prefix is blocked by pattern regardless of rest of filename
- Host capability gate (`core/tool_capability.py`, via `preflight_tool_call`) refuses `read_file` on jpeg/png/gif/webp and `browser_navigate` to image URLs before execute. Chat-attached images are injected into vision and are not offered as filesystem reads. When a user supplies a source URL, the agent retains the normal browser, search, and media tools.

**Available tools:**

| Tool | Requires confirmation | Description |
|------|-----------------------|-------------|
| `read_file(path, include_hash)` | No | Read file contents (20k char limit); optionally return the raw-file SHA-256 for stale-edit protection |
| `edit_file(path, edits, expected_sha256)` | **Yes** | Apply exact, preflighted, atomic UTF-8 text edits with hash/anchor/no-op guards and a bounded diff. If the intended text is already on disk after a crash resume, returns `already_applied` instead of failing the job. |
| `write_file(path, content)` | **Yes** | Create or overwrite a file |
| `verify_files(paths, include_diagnostics)` | No | Run bounded read-only conflict-marker, syntax, TOML/JSON/Python, and Git whitespace checks; optionally invoke installed diagnostics. For mutating task runs the loop requires this (or an equivalent proof command) before success; skipping it cannot complete the turn. |
| `diagnose_files(paths, provider)` | No | Optionally run installed Ruff, Pyright, ESLint, or TypeScript diagnostics through direct subprocess arguments; missing providers return `skipped` |
| `create_spreadsheet(path, sheets, title)` | **Yes** | Create a styled, formula-capable `.xlsx` workbook without an ad-hoc script |
| `delete_file(path)` | **Yes** | Delete a file or directory tree |
| `list_dir(path)` | No | List directory contents |
| `run_command(command)` | **Yes** | Execute shell command in project root, or in the active temporary subagent workspace |
| `calculate(expression)` | No | Safely evaluate bounded arithmetic for prices, totals, percentages, and conversions |
| `memory_search(query)` | No | Search durable Markdown memory, using vectors when available |
| `memory_save(content, scope)` | No | Persist an explicit fact/event to the Markdown journal or long-term memory |
| `web_search(query, count, kind)` | No | Host-owned live search. `kind` is `web`, `news`, or `images`. The host fetches and parses results (Playwright internally); the model never drives a search engine |
| `send_media(path, caption)` | No | Share a local file **or remote http(s) URL** into Discord/WhatsApp (and leftover file-share paths). Web chat photos use host attach after `web_search(kind="images")` instead |
| `send_voice(text, channel)` | No | Synthesize `text` with ElevenLabs and send it as a voice message — an mp3 file on Discord/WhatsApp, an inline playable clip on web. Requires `ELEVENLABS_API_KEY`. |
| `generate_image(prompt, model, size, quality, count, reference_images, use_attached_images)` | No | Generate a new image or transform up to four allowed local/current-chat reference images. Explicit prompts saying not to generate an image fail closed before provider use. Current images and reference-style follow-ups can reuse the session's most recent image for 30 minutes; missing references fail closed instead of silently becoming text-only generation. Image generation has no outer LimeBot tool deadline, while individual provider requests retain transport timeouts. |
| `send_discord_message(message, channel_id, user_id)` | No | Send a Discord message to a server channel or user DM |
| `send_discord_embed(...)` | No | Send a native Discord embed to a server channel or user DM |
| `list_discord_channels()` | No | List guild text channels available to the bot |
| `cron_add(message, context, time_expr, cron_expr)` | No | Schedule a one-time or repeating job |
| `cron_list()` | No | List all pending scheduled jobs |
| `cron_remove(job_id)` | **Yes** | Cancel a scheduled job |
| `run_steps(commands)` | **Yes** | Run two or more shell commands in order without `&&`. Stops on the first rejection or nonzero exit. |
| `spawn_agent(task, isolation)` | No | Delegate a long, parallelizable, or specialist-matched task; `auto` isolates coding/review work, `copy` always captures changes in a temporary workspace, and `none` explicitly shares the live workspace |
| `apply_workspace_changeset(workspace_id)` | **Yes** | Apply a retained copy-isolation changeset to the live tree with hash-guarded `edit_file`/`write_file`, then delete leftover clones |
| `analyze_video(source, question, detail, start, end, max_frames, resolution)` | No | Analyze an allowed local video or public HTTP(S) video and return a transcript plus up to three contact sheets. |
| `browser_navigate(url)` | No | Open a specific webpage. Always registered (the browser skill is not required). Named URLs and open/visit/go-to requests use this, not `web_search` |
| `browser_act(action, ...)` | No | Snapshot, click, type, scroll, wait, press, back, tabs, switch_tab, or download on the current page. Always registered with `browser_navigate` |
| `browser_extract(mode, selector)` | No | Read visible page text or list media. Do not use this to search. Always registered with `browser_navigate` |

### `core/video/` — Native Video Analysis

The optional video package prefers native captions, then uses OpenAI Whisper
only when `VIDEO_WHISPER_ENABLED=true`, and otherwise returns frames-only
evidence for visual modes. Remote `yt-dlp` traffic is forced through a
per-job loopback proxy that validates and pins every destination to public IP
space, enforces a 500 MiB aggregate response limit, and exists only for the
job lifetime. FFmpeg receives local paths through argument arrays with
`-nostdin`; URLs are never passed to it. Jobs live under random
`temp/video/` directories and expire after one hour.

`analyze_video` is a native tool even when the optional dependencies are not
installed; in that state it returns `npm run lime-bot feature install video`.
The separately enabled `watch` skill only teaches selection of transcript,
efficient, balanced, focused ranges, and 1024-resolution text inspection.

**`run_command` security filter:** Live chat blocks `;`, `&&`, `||`, `>`, `<`, `` ` ``, `$()`, `\n`, `sudo`, `chmod`, `chown`, and `IFS=` / `PYTHONPATH=` assignments. Bare `|` is allowed except when piped into an interpreter (`| sh`, `| python`, …). Isolated `spawn_agent` copies additionally allow `&&`, `||`, `|`, and redirects while still blocking sudo, env assignment, live-root escapes, backticks, and `$()`. Use `run_steps` for a sequential unittest then `py_compile` chain. Env-assignment denials are LimeBot command policy, not an OS/environment block.

`run_command` also rejects the root `main.py` service entrypoint because it is
long-running and belongs behind `limebot start`. One-shot commands use
`RUN_COMMAND_MAX_SECONDS` (default 180 seconds) as a hard safety cap even when
`COMMAND_TIMEOUT=0`; command cancellation closes stdin and kills the process
tree. Skill docs must invoke their own entrypoint, for example
`python {baseDir}/main.py user-info` for the GitHub skill.

`run_command` strips secret-suffixed environment variables from child
processes. The exact built-in `python skills/discord/main.py` invocation may
receive `APP_API_KEY` in its child environment so it can authenticate requests
to `/api/skill`; arbitrary commands and isolated workspaces never receive it.

When `spawn_agent` uses copy isolation, file tools and `run_command` are rooted
in a temporary clone outside the live project. Parent-directory escapes and
absolute live-project paths are rejected. Changed clones are retained with a
`workspace_id` and applyable capture. The parent merges them with
`apply_workspace_changeset` (hash-guarded `edit_file` / `write_file`), then
leftover `temp/bakeoff-*-isolated` and `/tmp/limebot-subagent-*` directories
are deleted. Clean clones are removed immediately.

**Tool result limits** (per-tool, to control context window growth):

| Tool | Limit |
|------|-------|
| `read_file` | 8,000 chars |
| `edit_file` | 8,000 chars |
| `verify_files` | 6,000 chars |
| `diagnose_files` | 8,000 chars |
| `spawn_agent` | 12,000 chars |
| `run_steps` | 6,000 chars |
| `apply_workspace_changeset` | 8,000 chars |
| `browser_extract` | 5,000 chars |
| `memory_search` | 4,000 chars |
| `browser_act` | 3,000 chars |
| `web_search` | 6,000 chars |
| `run_command` | 6,000 chars |
| `list_dir` | 3,000 chars |
| Everything else | 4,000 chars |

**Web search (`core/web_search.py`)** — one model-facing tool: `web_search(kind=web|news|images)`. The host fetches SERP HTML (Playwright internally), parses organic cards, drops ads and tracking URLs (`aclk`, DoubleClick, Google Ads, leftover click-wrappers), unwraps Bing organic `ck/a` and news `apiclick` destinations, ranks official hosts for software version queries, and retries another engine when a layout is empty, ads-only, or thinner than three organic hits. For `kind=news`, school-assembly / exam-prep / Yahoo / Mashable / “catching our eye” roundups and leftover MSN BingNewsVerp chrome are dropped; world desks (Reuters, AP, BBC, AFP, NYT, Washington Post, The Guardian, Al Jazeera, FT, WSJ, Bloomberg, NPR, DW, France 24) are ranked first, and the host retries until at least three world-desk hits are merged. Python “What’s New” / docs queries prefer `docs.python.org` and drop WhatsApp leftovers. Live FX queries drop history URLs, JS-only Xe shells, and default-pair junk (calculator.net EUR/USD 1.366 when the query is USD/GTQ); they require both query currencies on the card, parse a numeric GTQ-per-USD rate from HTML/snippets (retrying another source when the first page is an empty SPA or the wrong pair), and tell the model to compute Q1000 / rate rather than refuse or reuse another pair. Engine names and browser clicks are not model tools. There is no Tavily / Brave Search API / SerpAPI / DuckDuckGo i.js provider chain. If Playwright is missing, search fails with `BROWSER_INSTALL_HINT` (`npm run lime-bot feature install browser && npm run install-browser`). Empty results tell the model to answer from what it knows — never to open a search engine. A named page URL is `browser_navigate`, not search; `browser_navigate` / `browser_act` / `browser_extract` stay registered even when the browser skill is off. `browser_extract` returns compact HTML table rows first so Active Python Releases survive truncation.

**Chat photo delivery:** "download/send/bring me a picture of X in this chat" (including Spanish tráeme/pásame/mándame) is `web_search(kind="images")`. After at least one image URL, the host downloads it through the SSRF-guarded fetch and delivers it. Web stamps `metadata.image` + `attachments`. Discord/WhatsApp send the file. That is not `generate_image`, `spawn_agent`, `send_media`, or `run_command`, and it does not wait for a model wrap-up. Live chat media fetch is not a filesystem write and does not need confirmation. Web photo-send turns do not register `send_media`. Discord/WhatsApp may still expose `send_media` as a leftover file-share tool.

**Remote media delivery** — `send_media` accepts a remote http(s) URL, downloads it to `temp/downloads/` through an SSRF-guarded fetch (`fetch_url_to_temp`: only public IPs, http(s) only, per-hop redirect validation, 15MB cap), then delivers it. On the web channel it emits the standard `metadata.image` + `attachments` envelope; on Discord/WhatsApp it sends the file. Web photo-send turns do not register `send_media`; the host attach path uses the same envelope internally.

**Voice delivery** — there are two independent paths:
- **On demand** via the `send_voice(text, channel)` tool. `core/voice_tool_service.py` synthesizes with `ElevenLabsTTS.synthesize_to_file()` and publishes an mp3 as a `type:"file"` message (Discord/WhatsApp, `cleanup_file:True`) or a `voice_url` message (web). This works regardless of the auto-TTS channel toggles.
- **Automatic** via `core/loop.py`'s delivery block, gated by the Voice tab config (`enabled`, `channels`, `send_text_with_audio`). `_resolve_voice_delivery()` decides `audio_only` / `audio_and_text` (Discord/WhatsApp) / `web_url` (web) / `text`. Default `channels` is `["web"]`, so chat channels stay text-only until opted in.

---

### `core/vectors.py` — Vector Memory

LanceDB-backed semantic memory with automatic provider detection.

**Embedding model selection** (priority order):
1. `config.llm.embedding_model` if explicitly set
2. Auto-detected provider-compatible default from `config.llm.model` when that embedding provider is actually available
3. Gemini fallback when `GEMINI_API_KEY` or `GOOGLE_API_KEY` is available
4. Local Ollama fallback when `LLM_EMBEDDING_ALLOW_LOCAL_FALLBACK=true`
5. Markdown lexical recall when no semantic embedding candidate is available

| Chat model provider | Embedding model used |
|---------------------|---------------------|
| `gemini` / `vertex_ai` | `gemini/gemini-embedding-001` |
| `openai` / `azure` | `text-embedding-3-small` |
| `moonshot` | `moonshot/moonshot-embed-v1` |
| `nvidia` | `nvidia_nim/NV-Embed-v2` |
| `ollama` / `local` | `ollama/nomic-embed-text` |
| `deepseek`, `anthropic`, `xai` | Falls back to `gemini/gemini-embedding-001` when a Gemini/Google key exists |

Embedding credentials are separate from chat-model credentials and may be billed separately by the provider. If no semantic candidate works, LimeBot searches the Markdown source of truth instead of treating memory as broken. Local Ollama embeddings avoid provider billing but use local compute.

**`memory_save(content, scope)`** — explicit memory writes append to the Markdown source of truth (`scope=journal` by default, or `scope=long_term`). Vector indexing is queued afterward when embeddings are available; a failed or missing embedding provider never prevents the Markdown write.

**`search_grep(query, limit)`** — keyword scan of `persona/MEMORY.md` plus all `persona/memory/*.md` files (or the equivalent `LIMEBOT_STATE_DIR` paths). Results are scored by keyword hit count, tolerate accents/case differences, and are cached with a 30-second TTL that invalidates when a source file changes.

---

### `core/reflection.py` — Reflection Engine

Background service that runs every 4 hours via the cron scheduler. When the `@reflect_and_distill` sentinel fires:

1. Reads today's episodic journal (`persona/memory/YYYY-MM-DD.md`)
2. Reads current `MEMORY.md`
3. Calls the LLM with a distillation prompt
4. The response is processed by `process_tags()` — any `<save_memory>` tag updates `MEMORY.md`

The singleton (`get_reflection_service`) updates its model automatically if `config.llm.model` changes between calls.

---

### `core/scheduler.py` — CronManager

Persistent job scheduler with cron expression support.

- Jobs survive restarts (stored in `data/cron.json`)
- One-time jobs: specified as Unix timestamp
- Repeating jobs: standard 5-field cron expression via `croniter`
- Timezone support via `tz_offset` (minutes from UTC)
- Schedule drift prevention: next trigger is computed from the **scheduled** time, not `time.time()` at execution
- Recurring job misfire guard: repeating jobs missed by more than 60 seconds while the app was offline are skipped and advanced to the next future slot instead of replaying backlog runs
- Proactive system jobs (when `ENABLE_DYNAMIC_PERSONALITY=true`):
  - Morning greeting at 8:00 AM daily
  - Silence check-in at 10:00 AM daily

**Time expression shortcuts for `cron_add`:** `10s`, `5m`, `2h`, `1d`

---

### `core/session_manager.py` — Session Persistence

Tracks session metadata (model, token usage, injected files, timestamps) and chat history.

- Session metadata stored in `persona/sessions/`
- History serialized separately per session key
- In-memory dict is the authoritative state; disk is written on flush
- `update_session()` does **not** reload from disk (avoids blocking I/O and read-modify-write races under concurrent sessions)

### `core/turn_history_service.py` — Turn-history writes

Owns per-session history append/replacement, dirty tracking, and debounced
flushes to `SessionManager`. It persists safe copies of message content so
large image payloads are not written to disk while live history stays usable
for the model. `AgentLoop` keeps its existing history mapping and uses
compatibility wrappers for writes and flushes.

---

### `core/bus.py` — MessageBus

Decouples channels from the agent loop with bounded, ordered queues.

- **Inbound queue** — one queue, consumed by `AgentLoop.run()`.
- **Per-channel workers** — each subscribed channel has one ordered worker, so
  a blocked Discord or stale web connection cannot delay another channel.
- **Bounded backpressure** — durable messages are never dropped; a channel
  containing only durable queued messages applies channel-local backpressure.
- **Ephemeral coalescing** — typing, chunk, thinking, activity, matching, and
  progress events can be coalesced or evicted near capacity. Chunk/thinking
  content is concatenated in publication order. Unknown event types default to
  durable.
- **Durability boundary** — ephemeral events bypass delivery history. Final
  text, media, embeds, errors, approvals, and other outcomes remain tracked.
- **Shutdown** — router cancellation closes and awaits all worker tasks; queue
  and worker sets never grow without a bound.

### `core/delivery_tracker.py` — Delivery Persistence

- Keeps active deliveries plus a 500-entry terminal history.
- Mutations update in-memory state immediately and never await filesystem I/O.
- A debounced writer serializes an immutable snapshot outside the tracker lock,
  writes a temporary sibling, and atomically replaces `data/deliveries.json`.
- Dirty generations coalesce while a write is active. `flush()` waits until the
  latest generation is durable and is called during `AgentLoop.stop()`.

### `core/metrics.py` — Metrics Persistence

- Metric recording normalizes and enqueues events without writing on the event
  loop thread.
- One bounded daemon writer preserves JSONL order and batches appends.
- Queue overflow increments `dropped_events`; metrics never block product work
  or recursively log writer failures.
- `flush(timeout)` and `close(timeout)` provide bounded test/shutdown behavior.

---

## 📡 Channel Reference

### Web Channel (`channels/web.py`)
FastAPI application serving:
- **WebSocket** (`/ws`, `/ws/client`) — bidirectional streaming, tool progress, confirmation requests
- **REST API** — persona, config, sessions, cron, skills, logs, metrics, LLM health
- WebSocket auth: `api_key` query param checked against `APP_API_KEY`
- Caches the WhatsApp QR code and re-sends it to new WebSocket connections
- Chat UI renders `SUB-AGENT REPORT` replies as structured cards and suppresses adjacent empty orchestration thoughts/tool groups when they only describe `spawn_agent` handoff noise
- Web sends use a shorter timeout for ephemeral updates than durable outcomes;
  a socket is removed after its first timeout.
- Assistant output uses literal plain text while streaming, then performs one
  rich Markdown render after completion. Final message identity and content are
  unchanged.
- `GET /api/live` reports process liveness without authentication-sensitive configuration. Authenticated `GET /api/ready` reports redacted capability phases and returns HTTP 503 until required skills/tools are loaded; `degraded` is HTTP 200.
- `GET /api/capabilities/resolve?text=...` returns a redacted capability snapshot with matched skills, required tools, selected schemas, and a ready/degraded/unavailable reason.

### Browser Companion Extension (`extension/`)
- Manifest V3 browser companion for page help, selected text handoff, live task status, and tool approvals
- Reuses the web channel backend over REST and WebSocket instead of introducing a separate backend service
- Uses LimeBot's persona name and avatar when `/api/identity` returns them
- Captures an explicitly requested active video page plus its HTML5 player timestamp for `analyze_video`; it never forwards browser cookies or video bytes
- Chrome and Edge open the companion with `chrome.sidePanel`
- Opera GX falls back to opening the same companion surface in a regular extension tab when native side panel support is unavailable
- The old web mascot pop-out flow is not part of the current product surface
### Local App-Server API

Authenticated companion clients share a local-first contract under `/api/app/*`:
The app-server remains disabled with HTTP 503 (and rejects `/ws/app`) until `APP_API_KEY` is configured, even though setup-time APIs can operate before authentication is enabled globally.

- `GET /api/app/state` returns active workspaces/tasks, redacted pending approvals, channel status, and agent readiness.
- `GET /api/app/workspaces/{workspace_id}/events` returns bounded, normalized workspace/session events without raw tool arguments, command text, artifact paths, or arbitrary metadata.
- `POST /api/app/workspaces/{workspace_id}/message` creates a durable attempt and enqueues an ordinary `InboundMessage` with workspace context; it does not execute tools directly.
- `POST /api/app/approvals/{conf_id}` delegates to the same policy-audited `confirm_tool()` path as web/Discord approvals.
- `/ws/app` emits stable `workspace_event` envelopes and supports only state refresh/ping messages. Existing `/ws` and `/ws/client` chat protocols remain separate.

Artifact serializers reveal only identity/title/type and whether a local artifact exists; they never return artifact paths or file contents. There is intentionally no shell, filesystem-read, credential, or OAuth endpoint in this API.

### Discord Channel (`channels/discord.py`)
- Responds to DMs and `@mentions` only (ignores ambient channel messages)
- `/voice_join` starts one speech session per guild from the caller's current regular voice channel; `/voice_leave` is limited to its owner or a moderator
- Voice transcripts enter the normal `InboundMessage` bus under a separate per-guild voice session; final replies retain the voice session ID for speech playback
- Voice receive uses the optional pinned Pycord PR build, not the default `discord.py` dependency; install with `limebot feature install discord-voice` and enable `ENABLE_DISCORD_VOICE=true`
- Voice sessions only answer speech addressed to LimeBot by name (`voice_name` in the Voice tab, default the persona name; `listen_mode=always` disables this). The audio is still transcribed by ElevenLabs to check for the name, then discarded. The same speaker may continue for `voice_follow_up_seconds` after being addressed. `/voice_mute` / `/voice_unmute` (and the `voice_set_listening` tool, mute only) pause capture entirely.
- Voice tools `voice_list_members`, `voice_move_members`, `voice_set_listening` act only inside the session's guild; moving people or muting requires the session owner or a moderator, plus Discord's Move Members permission. They are deliberately not in the confirmation list so they work hands-free.
- Voice sessions use a tool allowlist (`core/voice_tool_policy.py`): the catalog hides everything else and `ToolRuntime` refuses it, so file, shell, sub-agent, image, and media tools are unavailable. `DISCORD_VOICE_EXTRA_TOOLS` opts more in.
- The terminal shows WARNING and above (`LIMEBOT_CONSOLE_LEVEL`); `logs/limebot.log` keeps the full log. Fire-and-forget session event writes are tracked and drained in `AgentLoop.stop()`.
- Tool-call embeds show only failures by default; `DISCORD_TOOL_EMBEDS=full` restores per-call embeds. Button callbacks accept both discord.py and Pycord argument orders.
- `DISCORD_ALLOW_FROM` — comma-separated user IDs (empty = allow all)
- `DISCORD_ALLOW_CHANNELS` — comma-separated channel IDs (empty = allow all)
- Sends long responses split at word boundaries

### WhatsApp Channel (`channels/whatsapp.py`)
- Connects to an external `whatsapp-web.js` bridge over WebSocket
- Contact management: `allowed` / `pending` / `blocked` lists in `data/contacts.json`
- Pending contacts trigger a notification to the web dashboard for approval

---

## 🧩 Skills System

Skills live in `skills/<name>/`. Minimum structure:

```
skills/
└── my_skill/
    ├── SKILL.md      # LLM instructions — describes the skill's commands and strategy
    └── skill.py      # OR main.py / scripts/ — execution logic called by run_command
```

`SKILL.md` is injected into the system prompt when the skill is enabled. The LLM reads it and knows how to invoke the skill (typically via `run_command`).

**Enable/disable** via `limebot.json`:
```json
{
  "skills": {
    "enabled": ["browser", "download_image", "filesystem", "discord", "docx-creator", "scrapling", "vm-lab"]
  }
}
```

**Install from GitHub:**
```bash
npm run lime-bot skill install https://github.com/user/skill-repo
```

**Invoke enabled skills from chat:**
```text
/skill <skill_name> <task>
/<skill_name> <task>
/skills
```

Only registered enabled skill names and their configured aliases can be invoked this way. Raw `SKILL.md` paths or arbitrary filesystem-style slash strings are rejected.

`vm-lab` is the allowlisted QEMU/KVM helper (`python skills/vm-lab/vm_lab.py ...`). Destinations stay under `ALLOWED_PATHS` or `$LIMEBOT_STATE_DIR/vm-lab`. Isolated explorer/reviewer reads inherit the parent allowlist so they can see `AGENTS.md` and allowlisted `temp/` without inventing permission errors.

---

## 🗂️ Persona File Reference

| File | Purpose | Who writes it |
|------|---------|---------------|
| `persona/SOUL.md` | Core values, personality, behavioral boundaries | Agent via `<save_soul>` tag |
| `persona/IDENTITY.md` | Name, emoji, avatar, style, catchphrases | Agent via `<save_identity>` or web dashboard |
| `persona/MEMORY.md` | Long-term distilled memory essence | Reflection engine via `<save_memory>` or explicit `memory_save(scope="long_term")` |
| `persona/MOOD.md` | Current emotional state | Agent via `<save_mood>` |
| `persona/RELATIONSHIPS.md` | Cross-user relationship registry | Agent via `<save_relationship>` |
| `persona/memory/YYYY-MM-DD.md` | Daily episodic journal | Agent via `<log_memory>` or explicit `memory_save(scope="journal")` |
| `persona/users/{sender_id}.md` | Per-user profile (affinity, facts, in-jokes) | Agent via `<save_user>` |

**Backup policy:** Every soul/identity write creates a timestamped `.bak` file (`SOUL.md.1234567890.bak`). The 3 most recent backups are kept; older ones are automatically deleted.

**Template policy:** The repository ships `persona/*.example` starter templates. The live runtime persona files (`SOUL.md`, `IDENTITY.md`, `MEMORY.md`, etc.) are treated as local state and should not be committed.

---

## 🔐 Security Model

**Confirmation gate** — tools that modify state require the user to click Approve in the web dashboard. The agent loop waits up to 5 minutes for confirmation before timing out. Per-session whitelist: once approved for a session, a tool type doesn't ask again.

**Approval policy profiles** — `APPROVAL_POLICY_PROFILE` accepts `manual`, `session`, `review`, or `autonomous`. Manual preserves the existing confirmation/session-whitelist flow; session makes that remembered-approval intent explicit; review ignores session whitelists; autonomous bypasses prompts but not hard path and command safety checks. Legacy `AUTONOMOUS_MODE=true` maps to `autonomous` only when no named profile is configured. Requests, decisions, and timeouts are appended to the session event log with redacted metadata and client source.

Sensitive tools: `edit_file`, `write_file`, `delete_file`, `run_command`, `cron_remove`

**Bypass conditions:**
- `APPROVAL_POLICY_PROFILE=autonomous` in `.env` — skips confirmation for sensitive tools
- `AUTONOMOUS_MODE=true` — legacy alias used only when no named profile is set
- Per-session whitelist — user clicked "Always allow this session"
- **Unattended jobs** — scheduled/queued work (`metadata.is_scheduler` or a durable job id) auto-approves sensitive tools only when the path or command matches `UNATTENDED_PATH_ALLOWLIST` / `UNATTENDED_COMMAND_ALLOWLIST`. Live chat stays gated. This is not a global autonomous switch.

### Durable job queue (`core/job_queue.py`)

SQLite at `data/jobs.sqlite` (or `$LIMEBOT_STATE_DIR/data/jobs.sqlite`). Cron, explicit queued work, and live companion/web/Discord chats persist **before** `publish_inbound` via `persist_user_inbound()`. Chat jobs stay confirmation-gated (`unattended=false`). States: `queued` → `running` → `succeeded|failed|cancelled`. Exclusive lease + heartbeat. On boot, interrupted `running` jobs are re-queued instead of vanish or fail-closed. Transient provider errors retry with backoff unless an irreversible tool already succeeded. If writes already landed and a later `edit_file` is stale, the turn completes instead of marking the job failed. Cron `last_status=ok` is written only from `CronManager.mark_run_finished()` after the agent turn ends.

Copy-isolated sub-agents may still **read** the parent `source_root` and the parent's `ALLOWED_PATHS` (so `AGENTS.md` and allowlisted `temp/` are visible). Writes stay in the temporary clone.

TaskTracker's JSON projection still fail-closes dead coroutines for the dashboard. The SQLite queue is the source of truth for work that must resume.

### Cursor plugins (`core/plugin_manifest.py`, `core/plugin_installer.py`)

`limebot plugin install` accepts a local Cursor plugin folder or a GitHub path such as `cursor/plugins/github` (resolved to `third_party/github`) or `cursor/plugins/create-plugin`. Official schemas are vendored under `schemas/cursor-plugin/`. Skills load through `get_skill_dirs()`; MCP servers merge into `mcp/mcp_config.json`. Do not vendor the entire `cursor/plugins` tree.

GitHub capability is provided by the official plugin, not a built-in `skills/github` tree.

**Path enforcement:** Every filesystem tool call goes through `_is_path_allowed()`. Blocked:
- Anything outside `ALLOWED_PATHS` + project root
- `.env` and `.env.*` prefixed files
- `limebot.json`, `config.py`, `secrets.py`, `package-lock.json`
- `.pem`, `.key`, `.p12`, `.pfx` extensions

---

## 🔄 Dynamic Personality System

Enabled by `ENABLE_DYNAMIC_PERSONALITY=true`.

**Affinity tiers** (based on `**Affinity Score:**` in the user profile):

| Score | Behavior |
|-------|----------|
| 0–29 | Professional/stranger mode — polite, no nicknames, maintains distance |
| 30–69 | Friendly/acquaintance mode — warm, uses name, more casual |
| 70–100 | Close friend mode — fully expressive, playful, protective |

**Proactive system jobs** are registered on startup:
- Morning greeting (8:00 AM) — bot initiates conversation
- Silence check-in (10:00 AM) — bot checks in if user has been quiet

**Agent instructions for dynamic persona:**
- Update `**Affinity Score:**` and `**Relationship Level:**` in the user's profile as the relationship evolves
- Use `<save_mood>` when mood significantly shifts (excited, tired, annoyed)
- Use `<save_relationship>` to update the global relationship registry
- Use `<save_user>` to record new facts, milestones, and in-jokes

---

## 🛠️ CLI Reference

The JavaScript toolchain requires Node.js 22.19 or newer. Both `start` and `doctor` reject an older runtime before dependency installation begins.

First start installs the `core` profile only: root + `web` npm workspaces and
`requirements.txt` (common Python packages plus stable `discord.py`). Optional features use a closed allowlist in
`bin/feature-install.js`. On Windows, `.cmd`/`.bat` dependency shims are invoked
through `ComSpec /d /s /c` with an argument array; direct `spawn("npm.cmd")`
can raise `EINVAL` on supported Windows/Node combinations and must not be
reintroduced.

```bash
npm run lime-bot <command> [options]
```

| Command | Description |
|---------|-------------|
| `setup` | First-run helper: check Node/Python in plain language, write `.env`, optionally `--recommended` browser + Chromium |
| `start` | Start backend + frontend (auto-install on first run) |
| `start -- --quick` | Fast boot, skip dependency and update checks |
| `stop` | Kill all LimeBot processes |
| `status` | Check active ports (8000 backend, 5173 frontend) |
| `update` | Fast-forward source safely, back up runtime state, and refresh dependencies |
| `update --check` | Report remote status and classify local changes before updating |
| `update --rollback` | Restore the previous guarded commit when no tracked source edits exist |
| `auth codex <login\|import\|status\|logout>` | Manage local ChatGPT Codex OAuth from the CLI |
| `doctor` | Validate Python, Node, `.env` config |
| `logs` | Tail `logs/limebot.log` |
| `skill list` | List installed skills |
| `skill install <url>` | Install from GitHub |
| `plugin install <source>` | Install a Cursor plugin package (local folder or `owner/repo/path`) |
| `plugin list` / `plugin uninstall` | List or remove installed plugins |
| `skill uninstall <name>` | Remove a skill |
| `skill enable <name>` | Enable a disabled skill |
| `skill disable <name>` | Disable without uninstalling |
| `install-browser` | Install Chromium for Playwright |
| `feature install <browser\|memory\|documents\|mcp\|video\|whatsapp\|extension\|all>` | Install one closed optional profile, or all profiles plus launch-verified Chromium |
| `review-diff --diff-file <path> --output <path>` | Parse only the supplied unified diff and write a redacted review artifact; `--invoke-model` optionally calls the configured LLM without tools. |

`LIMEBOT_STATE_DIR` can point at a user-owned directory outside the checkout.
When set, mutable configuration, persona data, and installed skills use that
directory while shipped skills continue loading from the repository.

**Codex OAuth notes:**
- The OAuth sign-in flow is intentionally CLI-only (`limebot auth codex ...`), not browser-dashboard driven.
- Stored Codex credentials live in local runtime state at `data/oauth_profiles.json` and should not be committed.
- Importing an existing Codex CLI login reads `%USERPROFILE%\.codex\auth.json` (or `CODEX_HOME/auth.json` if set).

**Global install** (run once in project root):
```bash
npm link
# Then use: limebot start, limebot logs, etc.
```

## Docker Contract

- `docker/prepare.sh` and `docker/prepare.ps1` create ignored runtime files and
  directories before Compose evaluates file mounts. They never overwrite
  existing configuration.
- The frontend Nginx container is the only published service (host port 3000,
  bound to `127.0.0.1` by default). `/api`, `/temp`, and `/ws` proxy to the
  private backend service. LAN binding requires both
  `LIMEBOT_DOCKER_BIND_HOST=0.0.0.0` and `APP_API_KEY`.
- Compose sets `WEB_HOST=0.0.0.0` with
  `LIMEBOT_TRUSTED_PROXY_ONLY=true`. This exception is safe only while the
  backend has `expose` but no host `ports`. Publishing the backend requires an
  `APP_API_KEY` and removal of that unauthenticated proxy-only assumption.
- WhatsApp uses the optional `whatsapp` Compose profile and binds its bridge to
  the container network. Core Docker startup must not build or start it.
- `LIMEBOT_DOCKER_FEATURES` selects optional Python build profiles, including
  `video` (which installs FFmpeg in the backend image);
  `LIMEBOT_DOCKER_INSTALL_BROWSER=1` adds and launch-installs Chromium.
- Root, web, and bridge images use committed lockfiles with `npm ci`. The
  backend installs only root runtime Node dependencies; web and bridge builds
  stay in their own images.
- Backend, frontend, and bridge define liveness health checks. Frontend startup
  waits for backend health rather than mere process creation.
- Host 24/7 deploy uses `deploy/systemd/limebot.service` (or
  `limebot autorun enable`) with `GET /api/live` as the heartbeat. Compose
  already sets `restart: unless-stopped`.
- Root, web, and bridge build contexts each exclude credentials, local state,
  dependency trees, generated output, and session data.

---

## ⚡ Performance Notes

- **Fast response path (default)** — `LIMEBOT_AI_HARNESS_MODE=fast` uses an 80ms Auto-RAG budget; `balanced` uses 200ms. Every eligible non-empty turn receives the tools available to its current profile and channel; explicit read-only Plan mode omits executable tools. The model chooses whether to call tools. These settings optimize LimeBot overhead, not provider generation speed.
- **First-output metrics** — every provider iteration records `provider_first_delta` and `turn_first_output_queued` with `initial`, `post_tool`, or `synthesis` iteration metadata; typing indicators are excluded.
- **Rendered memory cache** — today's last five journal entries and the first 800 long-term-memory characters are cached by date, privacy scope, resolved path, nanosecond mtime, and size. Writes invalidate on the next prompt build; RAG results are never cached here.
- **Stable prompt cache** — 30s TTL per `(sender_id, channel)` pair avoids rebuilding the full system prompt on every message
- **Tool result cache** — read-only tools (`read_file`, `list_dir`, `memory_search`, browser extractors) cache their results to avoid redundant calls within a session
- **Dirty-flag history** — history is only written to disk when it actually changed; a `_history_dirty` flag per session prevents unnecessary I/O
- **Per-tool result limits** — each tool has its own character limit instead of one global cap, preserving context window budget proportionally
- **Grep cache** — `search_grep` results are cached for 30 seconds to avoid scanning all Markdown memory files on every message; file signatures invalidate stale entries immediately after a write
- **History summarization** — when the session exceeds the token budget, the LLM summarizes older turns before they're evicted; the summary is inserted back into history as a system message
- **Dependency profiles** — normal startup installs only `requirements.txt` plus root/web npm. Browser, memory, documents, MCP, and video are explicit optional Python manifests; WhatsApp and extension are optional Node workspace profiles. The opt-in `discord-voice` feature switches the Python Discord package to the pinned experimental Pycord DAVE-receive manifest; startup and updates retain that selected manifest. `requirements-dev.txt` includes stable Python profiles and test tooling, not the Discord voice replacement.
- **Dependency fingerprints** — state schema 2 records core profiles and successful optional features. Old broad-install state forces one core refresh. Independent core npm/Python lanes run concurrently, then write `data/dependency-state.json` once after both settle; failed lanes are never current.
- **Optional capability contract** — every optional capability declares a profile, closed install command, missing/degraded readiness behavior, and fingerprint or sentinel. Optional imports cannot break core startup. Browser setup launch-verifies Chromium. The project-directory watcher observes first `.env` creation, and WhatsApp installs/builds before launch.
- **Liveness-first combined startup** — normal backend + frontend startup waits only for `/api/live`, then starts Vite immediately while capabilities continue loading. Backend-only startup and diagnostics still wait for authenticated `/api/ready`; the web composer and automatic persona kickoff keep using capability readiness before accepting agent work.
- **Background update discovery** — `start` reads only a recent local cache before launching and performs remote Git/npm discovery afterward in a handled background task. `start -- --quick` skips both cached presentation and remote discovery.
- **Recoverable virtual environments** — if the venv directory exists without its expected Python executable, startup renames it to a timestamped sibling, creates and validates a replacement, and restores the preserved directory if repair fails. Dependency installation always invokes the validated venv Python, never system Python.
- **Channel-isolated streaming** — bounded per-channel workers preserve local
  order while preventing cross-channel head-of-line blocking. Replaceable
  ephemeral updates coalesce; durable outcomes apply backpressure and retain
  delivery tracking.
- **Nonblocking persistence** — delivery snapshots and metric JSONL events are
  written by bounded background writers. Explicit bounded flushes run during
  shutdown.
- **Lightweight stream rendering** — in-progress assistant text bypasses the
  Markdown parser; completed content switches once to the lazy rich renderer.
- **Lazy code highlighting** — ordinary Markdown loads without Prism. Fenced code dynamically loads a small `PrismLight` bundle with common languages; unknown languages render safely as plain text.

---

*Evolve with intention.* 🍋
