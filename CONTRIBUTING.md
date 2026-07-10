# Contributing to LimeBot 🍋

We're thrilled you want to help make LimeBot even better! Whether you're fixing a bug, adding a new skill, or improving the dashboard, your contributions are welcome.

## 🛠 Development Setup

1. Fork the repository and clone it:
   ```bash
   git clone https://github.com/<your-username>/LimeBot.git
   cd LimeBot
   ```
2. Start everything with the CLI (creates venv, installs deps, launches backend + frontend):
   ```bash
   npm run lime-bot start
   ```
3. Or run manually in two terminals:
   ```bash
   # Terminal 1 — Backend
   cp .env.example .env   # fill in your keys
   python -m pip install -r requirements-dev.txt
   python main.py

   # Terminal 2 — Frontend
   npm install --include-workspace-root --workspace web
   npm --prefix web run dev
   ```

## 🚀 How to Contribute

### Adding New Skills
Skills are LimeBot's extension system. To create one:
1. Create a new folder in `skills/` (e.g. `skills/my-skill/`).
2. Add a `SKILL.md` with YAML frontmatter (`name`, `description`) and detailed instructions for the LLM.
3. Add your logic — a `skill.py` (Python), Node.js module, or CLI scripts in a `scripts/` subfolder.
4. If your skill has dependencies, include a `requirements.txt` or `package.json` — the installer handles both automatically.
5. Test by installing locally:
   ```bash
   python -m core.skill_installer install ./skills/my-skill
   ```

Look at existing skills like `browser`, `download_image`, or `docx-creator` for reference.

### Improving the Dashboard
The frontend is React + Vite + shadcn/ui:
- Components live in `web/src/components/` — organized by feature (`chat/`, `config/`, `memory/`, `persona/`, `sessions/`, `skills/`).
- Real-time updates flow through the WebSocket hook in `web/src/hooks/useChat.ts`.
- UI primitives are shadcn/ui components in `web/src/components/ui/`.
- Type-check before submitting: `cd web && npx tsc --noEmit`.

### Enhancing the Core
The core logic lives in `core/`:

| File | Purpose |
|------|---------|
| `loop.py` | The main agentic loop — LLM calls, tool dispatch, streaming |
| `tools.py` | Toolbox implementation — file ops, command execution, memory search |
| `tool_defs.py` | Declarative tool schemas (base + browser + skill tools) |
| `prompt.py` | System prompt construction and persona injection |
| `bus.py` | Internal async message bus for cross-component communication |
| `vectors.py` | LanceDB vector storage and semantic search |
| `scheduler.py` | Cron and reminder system (persisted to `data/cron.json`) |
| `session_manager.py` | Multi-session context isolation |
| `skill_installer.py` | Git-based skill installer with dependency management |
| `reflection.py` | Background memory distillation into `MEMORY.md` |
| `browser.py` | Playwright browser automation |

### Adding a Channel
Channels live in `channels/`:
- `web.py` — FastAPI + WebSocket server for the dashboard
- `discord.py` — discord.py integration
- `whatsapp.py` — WhatsApp bridge connector

Each channel receives messages from its platform, routes them through the agentic loop, and streams responses back.

## 📐 Guidelines

- **Keep it lightweight** — avoid unnecessary dependencies. LimeBot should stay fast to start and easy to deploy.
- **No hardcoded keys or secrets** — everything goes through `.env` or the config API.
- **Test your changes** — at minimum, ensure `python main.py` starts without errors and `cd web && npx tsc --noEmit` passes.
- **Use descriptive commits** — `fix: stall detection for interactive commands` > `fix stuff`.

## 📜 Code of Conduct
- Be respectful and helpful.
- Keep the lightweight philosophy in mind — avoid unnecessary bloat.

## 🍎 Inspiration
LimeBot is a community-driven effort inspired by [OpenClaw](https://github.com/openclaw/openclaw). We strive to maintain compatibility with the agentic concepts established there while staying lightweight.
