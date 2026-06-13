# LimeBot

LimeBot is a local-first AI companion with memory, persona, tools, and a web dashboard. It can chat on the web, help with files and tasks, remember past conversations, and connect to channels like Discord and WhatsApp.

This guide is for new users who want to set up the bot and the browser companion extension.

## Browser companion

LimeBot also includes a browser companion extension for Chrome, Edge, and Opera GX. It can:

- ask LimeBot about the current page
- send selected text to LimeBot
- show live status and approvals while you browse

Extension setup lives in [extension/README.md](extension/README.md).

## What you get

- A local web dashboard for chatting with LimeBot
- Persistent memory and persona files stored on your machine
- Tool approvals for sensitive actions like shell commands and file writes
- Optional channels like Discord, WhatsApp, and Telegram
- A browser companion extension for page help, selected text, and approvals

## Requirements

- Node.js 18 or newer
- Python 3.11 to 3.14
- Git
- An API key for the model provider you want to use

Recommended:

- 8 GB RAM
- A Chromium-based browser for the extension

## 1. Install LimeBot

```bash
git clone https://github.com/LemonMantis5571/LimeBot.git
cd LimeBot
npm install
```

## 2. Configure LimeBot

Copy the example environment file:

```bash
copy .env.example .env
```

If you are on macOS or Linux, use:

```bash
cp .env.example .env
```

Open `.env` and set at least:

```env
LLM_MODEL=gemini/gemini-2.0-flash
GEMINI_API_KEY=your_key_here
APP_API_KEY=
```

Notes:

- `LLM_MODEL` should match the provider key you configure.
- `APP_API_KEY` is optional, but recommended if you do not want an open local dashboard.
- `ALLOWED_PATHS` controls where LimeBot can read and write files.

## 3. Start LimeBot

```bash
npm run start
```

On first run LimeBot will:

- create its Python environment
- install backend and frontend dependencies
- start the backend and web app

Default local addresses:

- Dashboard: `http://localhost:5173`
- Backend API: `http://localhost:8000`

If LimeBot does not already have a valid persona, the first chat will guide you through creating one.

## 4. Optional channels

You can enable extra channels later in `.env`:

- `ENABLE_DISCORD=true`
- `ENABLE_WHATSAPP=true`
- `ENABLE_TELEGRAM=true`

Leave them disabled if you only want the web app and extension.

## 5. Build the browser companion extension

From the project root:

```bash
npm run extension:build
```

This creates the unpacked extension in:

```text
extension/dist
```

## 6. Load the extension

See the full browser guide in [extension/README.md](extension/README.md), but the short version is:

### Chrome or Edge

1. Open `chrome://extensions` or `edge://extensions`
2. Turn on Developer mode
3. Click `Load unpacked`
4. Select `D:\Code\LimeBot-OS\extension\dist`

### Opera GX

1. Open `opera://extensions`
2. Turn on Developer mode
3. Click `Load unpacked`
4. Select `D:\Code\LimeBot-OS\extension\dist`

In Chrome and Edge, the companion opens in the browser side panel.

In Opera GX, the same companion opens from the popup in a normal extension tab because Opera does not expose the same side panel API used by Chrome.

## 7. Configure the extension

Open the extension popup and fill in:

- `Dashboard URL`: usually `http://localhost:5173`
- `API Base URL`: usually `http://localhost:8000`
- `WebSocket URL`: usually `ws://localhost:8000`
- `API Key`: use the same value as `APP_API_KEY` if you set one

After saving, you can:

- ask LimeBot about the current page
- send selected text to LimeBot
- view live status and recent activity
- approve or deny tools from the companion

If LimeBot has a persona avatar and name set in `persona/IDENTITY.md`, the extension uses that identity when available.

## Common commands

```bash
npm run start
npm run stop
npm run status
npm run logs
npm run doctor
npm run extension:build
```

## Privacy and safety

- LimeBot stores its conversation history and persona locally.
- Sensitive tool actions require approval unless you explicitly enable autonomous mode.
- The extension only sends page content after you trigger an action like `Ask this page` or `Send selected text`.

## Troubleshooting

### The dashboard does not open

Run:

```bash
npm run doctor
```

Then check:

- Python is installed and on your path
- Node.js is installed and on your path
- your `.env` has a valid model and API key

### The extension cannot connect

Check that:

- LimeBot is running
- `API Base URL` matches your backend, usually `http://localhost:8000`
- `WebSocket URL` matches your backend, usually `ws://localhost:8000`
- your browser has access to `localhost` or `127.0.0.1`

### Opera GX does not show a native side panel

That is expected in the current version. Use `Open companion` from the popup, which opens the same companion in a browser tab.

## More docs

- Extension setup: [extension/README.md](extension/README.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Internal agent and architecture reference: [AGENTS.md](AGENTS.md)
