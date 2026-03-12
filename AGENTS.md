# 🍋 AGENTS.md — LimeBot Developer & Agent Reference

This document is the authoritative source of truth for the LimeBot codebase. It is read by developers building on LimeBot **and** by the agent itself at runtime. Everything here is accurate to the current implementation.

---

## 🏛️ Architecture Overview

LimeBot is an **event-driven agentic system**. Every user interaction is an `InboundMessage` that flows through an async message bus, gets processed by the agent loop, and produces `OutboundMessage` events routed back to the originating channel.

```
Channel (Discord / WhatsApp / Web)
        │  InboundMessage
        ▼
   MessageBus (asyncio.Queue)
        │
        ▼
   AgentLoop._process_message()
     ├─ Auto-RAG (vector search + grep)
     ├─ Stable prompt cache (30s TTL)
     ├─ LLM call (LiteLLM / acompletion)
     ├─ Stream consumption + tool call extraction
     ├─ Tool execution loop (up to 30 iterations)
     ├─ Tag processing (save_soul, log_memory, etc.)
     └─ OutboundMessage → MessageBus → Channel.send()
```

---

## 📁 Core Module Reference

### `core/loop.py` — AgentLoop

The heart of the system. Manages:

- **Session history** per `session_key` (`channel_chatid`) with dirty-flag persistence
- **Stable prompt cache** — the rarely-changing part of the system prompt (soul + identity + user context) is cached for 30 seconds per `(sender_id, channel)` pair. Only the volatile suffix (memory, RAG results, timestamp) is rebuilt each message.
- **Auto-RAG** — before every LLM call, runs semantic vector search (falls back to grep if embeddings are unavailable). Injects matching memories into the prompt automatically.
- **Tool execution loop** — after each LLM response, if tool calls are returned, executes them in parallel and loops back to the LLM (up to 30 iterations). Sensitive tools require user confirmation.
- **Sub-agent delegation** — `spawn_agent` tool creates an isolated session that runs its own tool loop and reports back.
- **Per-session dedup** — identical consecutive messages within 2 seconds are silently dropped, keyed per session (not globally).
- **History summarization** — when token count exceeds limit, older messages are summarized by the LLM and squashed; large tool outputs from old turns are truncated in-place.

**Key configuration knobs (set via `self.config`):**
- `config.llm.model` — active chat model
- `config.llm.enable_dynamic_personality` — enables mood, affinity, and proactive jobs
- `config.autonomous_mode` — bypasses confirmation gate for sensitive tools
- `config.whitelist.allowed_paths` — roots for filesystem access

---

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
- Today's episodic memory journal (last 5 entries + long-term essence preview)
- Current timestamp

**Persona validation functions** (all use atomic temp-file writes + timestamped backups + auto-rotation to 3 most recent):
- `validate_and_save_soul(content)` — requires 100+ chars, soul keyword presence
- `validate_and_save_identity(content)` — requires `**Name:**`, `**Style:**`, 50+ chars
- `validate_and_save_mood(content)` — atomic write, no content minimum
- `validate_and_save_relationships(content)` — atomic write for `RELATIONSHIPS.md`

**Setup mode** — if `SOUL.md` or `IDENTITY.md` are missing or invalid, `is_setup_complete()` returns False and the entire system prompt is replaced with a setup interview prompt. The agent must emit `<save_soul>` and `<save_identity>` tags with valid content to exit setup mode.

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
| `<log_memory>...</log_memory>` | Appends a timestamped entry to today's daily journal. Queues a vector embedding in the background. |
| `<save_memory>...</save_memory>` | Overwrites `MEMORY.md` (long-term essence). Queues vector embedding. |
| `<discord_send>...</discord_send>` | Publishes a message to the specified Discord `channel_id`. Always routes to the `discord` channel regardless of originating context. |
| `<discord_embed>...</discord_embed>` | Publishes a rich embed to the specified Discord `channel_id`. |

**All tags are stripped from the reply shown to the user.**

---

### `core/tools.py` — Toolbox

Sandboxed OS interface. All methods check `_is_path_allowed()` before touching the filesystem.

**Path security rules:**
- Only paths under `ALLOWED_PATHS` (+ project root) are accessible
- Hard-blocked filenames: `.env`, `limebot.json`, `config.py`, `secrets.py`, `package-lock.json`
- Hard-blocked extensions: `.pem`, `.key`, `.p12`, `.pfx`
- `.env*` prefix is blocked by pattern regardless of rest of filename

**Available tools:**

| Tool | Requires confirmation | Description |
|------|-----------------------|-------------|
| `read_file(path)` | No | Read file contents (20k char limit) |
| `write_file(path, content)` | **Yes** | Create or overwrite a file |
| `delete_file(path)` | **Yes** | Delete a file or directory tree |
| `list_dir(path)` | No | List directory contents |
| `run_command(command)` | **Yes** | Execute shell command in project root |
| `memory_search(query)` | No | Semantic search across vector memory |
| `cron_add(message, context, time_expr, cron_expr)` | No | Schedule a one-time or repeating job |
| `cron_list()` | No | List all pending scheduled jobs |
| `cron_remove(job_id)` | **Yes** | Cancel a scheduled job |
| `spawn_agent(task)` | No | Delegate a task to a background sub-agent |

**`run_command` security filter:** Blocks `;`, `&&`, `||`, `|`, `>`, `<`, `` ` ``, `$()`, `\n`, `sudo`, `chmod`, `chown`, `ifs=`, `pythonpath=`.

**Tool result limits** (per-tool, to control context window growth):

| Tool | Limit |
|------|-------|
| `read_file` | 8,000 chars |
| `browser_extract` | 5,000 chars |
| `browser_get_page_text` | 5,000 chars |
| `memory_search` | 3,000 chars |
| `browser_snapshot` | 3,000 chars |
| `google_search` | 2,000 chars |
| `run_command` | 2,000 chars |
| `browser_list_media` | 1,000 chars |
| `list_dir` | 500 chars |
| Everything else | 2,000 chars |

---

### `core/vectors.py` — Vector Memory

LanceDB-backed semantic memory with automatic provider detection.

**Embedding model selection** (priority order):
1. `config.llm.embedding_model` if explicitly set
2. Auto-detected from `config.llm.model` provider prefix
3. Default: `gemini/gemini-embedding-001`

| Chat model provider | Embedding model used |
|---------------------|---------------------|
| `gemini` / `vertex_ai` | `gemini/gemini-embedding-001` |
| `openai` / `azure` | `text-embedding-3-small` |
| `nvidia` | `nvidia/NV-Embed-v2` |
| `ollama` / `local` | `ollama/nomic-embed-text` |
| `deepseek`, `anthropic`, `xai` | Falls back to `gemini/gemini-embedding-001` |

If the embedding API fails or no key is found, vector search is disabled for the session and grep fallback takes over. The disable state is session-local (resets on restart).

**`search_grep(query, limit)`** — keyword scan of all `persona/memory/*.md` files. Results are scored by keyword hit count. Results are cached with a 30-second TTL.

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

---

### `core/bus.py` — MessageBus

Decouples channels from the agent loop using `asyncio.Queue`.

- **Inbound queue** — one queue, consumed by `AgentLoop.run()`
- **Outbound routing** — dict of `channel_name → send_callback`; `dispatch_outbound()` reads the queue and calls the right subscriber

---

## 📡 Channel Reference

### Web Channel (`channels/web.py`)
FastAPI application serving:
- **WebSocket** (`/ws`, `/ws/client`) — bidirectional streaming, tool progress, confirmation requests
- **REST API** — persona, config, sessions, cron, skills, logs, metrics, LLM health
- WebSocket auth: `api_key` query param checked against `APP_API_KEY`
- Caches the WhatsApp QR code and re-sends it to new WebSocket connections

### Discord Channel (`channels/discord.py`)
- Responds to DMs and `@mentions` only (ignores ambient channel messages)
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
    "enabled": ["browser", "download_image", "filesystem"]
  }
}
```

**Install from GitHub:**
```bash
npm run lime-bot skill install https://github.com/user/skill-repo
```

---

## 🗂️ Persona File Reference

| File | Purpose | Who writes it |
|------|---------|---------------|
| `persona/SOUL.md` | Core values, personality, behavioral boundaries | Agent via `<save_soul>` tag |
| `persona/IDENTITY.md` | Name, emoji, avatar, style, catchphrases | Agent via `<save_identity>` or web dashboard |
| `persona/MEMORY.md` | Long-term distilled memory essence | Reflection engine via `<save_memory>` |
| `persona/MOOD.md` | Current emotional state | Agent via `<save_mood>` |
| `persona/RELATIONSHIPS.md` | Cross-user relationship registry | Agent via `<save_relationship>` |
| `persona/memory/YYYY-MM-DD.md` | Daily episodic journal | Agent via `<log_memory>` |
| `persona/users/{sender_id}.md` | Per-user profile (affinity, facts, in-jokes) | Agent via `<save_user>` |

**Backup policy:** Every soul/identity write creates a timestamped `.bak` file (`SOUL.md.1234567890.bak`). The 3 most recent backups are kept; older ones are automatically deleted.

---

## 🔐 Security Model

**Confirmation gate** — tools that modify state require the user to click Approve in the web dashboard. The agent loop waits up to 5 minutes for confirmation before timing out. Per-session whitelist: once approved for a session, a tool type doesn't ask again.

Sensitive tools: `write_file`, `delete_file`, `run_command`, `cron_remove`

**Bypass conditions:**
- `AUTONOMOUS_MODE=true` in `.env` — skips confirmation for all tools
- Per-session whitelist — user clicked "Always allow this session"

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

```bash
npm run lime-bot <command> [options]
```

| Command | Description |
|---------|-------------|
| `start` | Start backend + frontend (auto-install on first run) |
| `start -- --quick` | Fast boot, skip dependency checks |
| `stop` | Kill all LimeBot processes |
| `status` | Check active ports (8000 backend, 5173 frontend) |
| `doctor` | Validate Python, Node, `.env` config |
| `logs` | Tail `logs/limebot.log` |
| `skill list` | List installed skills |
| `skill install <url>` | Install from GitHub |
| `skill uninstall <name>` | Remove a skill |
| `skill enable <name>` | Enable a disabled skill |
| `skill disable <name>` | Disable without uninstalling |
| `install-browser` | Install Chromium for Playwright |

**Global install** (run once in project root):
```bash
npm link
# Then use: limebot start, limebot logs, etc.
```

---

## ⚡ Performance Notes

- **Stable prompt cache** — 30s TTL per `(sender_id, channel)` pair avoids rebuilding the full system prompt on every message
- **Tool result cache** — read-only tools (`read_file`, `list_dir`, `memory_search`, browser extractors) cache their results to avoid redundant calls within a session
- **Dirty-flag history** — history is only written to disk when it actually changed; a `_history_dirty` flag per session prevents unnecessary I/O
- **Per-tool result limits** — each tool has its own character limit instead of one global cap, preserving context window budget proportionally
- **Grep cache** — `search_grep` results are cached for 30 seconds to avoid scanning all memory files on every message
- **History summarization** — when the session exceeds the token budget, the LLM summarizes older turns before they're evicted; the summary is inserted back into history as a system message

---

*Evolve with intention.* 🍋
