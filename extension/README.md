# LimeBot Companion Extension

LimeBot Companion is the browser extension for LimeBot. It gives you a lightweight companion surface while you browse so you can send page context, selected text, and tool approvals without bouncing back to the main dashboard every time.

## What it does

- Ask LimeBot about the current page
- Watch and analyze the video currently open in the active tab
- Send selected text to LimeBot
- Show recent replies, live status, and tool activity
- Let you approve or deny pending tools
- Use the same LimeBot name and avatar when a persona identity is available

## Browser behavior

- Chrome and Edge: opens as a native browser side panel
- Opera GX: opens the same companion UI in a normal extension tab

Opera GX fallback is expected in the current version.

## Before you load it

Start LimeBot first, then build the extension from the project root:

```bash
npm install
npm run start
npm run extension:build
```

The unpacked extension output is:

```text
extension/dist
```

## Install in Chrome

1. Open `chrome://extensions`
2. Turn on Developer mode
3. Click `Load unpacked`
4. Select `D:\Code\LimeBot-OS\extension\dist`

## Install in Edge

1. Open `edge://extensions`
2. Turn on Developer mode
3. Click `Load unpacked`
4. Select `D:\Code\LimeBot-OS\extension\dist`

## Install in Opera GX

1. Open `opera://extensions`
2. Turn on Developer mode
3. Click `Load unpacked`
4. Select `D:\Code\LimeBot-OS\extension\dist`

After loading, pin the extension if you want faster access from the toolbar.

## First-time setup

Open the extension popup and save these values:

- `Dashboard URL`: usually `http://localhost:5173`
- `API Base URL`: usually `http://localhost:8000`
- `WebSocket URL`: usually `ws://localhost:8000`
- `API Key`: your `APP_API_KEY` if you set one

If you did not set `APP_API_KEY`, leave the API key field empty.

## How to use it

### Open the companion

- Chrome or Edge: click `Open side panel`
- Opera GX: click `Open companion`

### Ask about a page

1. Open any normal `http` or `https` page
2. Click the extension
3. Click `Ask this page`

LimeBot receives the page title, URL, visible text excerpt, and your current session context.

### Send selected text

1. Select text on a page
2. Open the extension
3. Click `Send selected text`

### Watch the current video

1. Open a public video page such as YouTube, Vimeo, TikTok, or Loom
2. Click `Watch video`, use the matching context-menu action, or type `watch the current video` in the companion
3. LimeBot sends the page URL and current playback position through the guarded native `analyze_video` tool

The extension does not copy browser cookies or stream video bytes to LimeBot. Private, authenticated, DRM-protected, playlist-only, and livestream sources remain unsupported.

### Approve tools

When LimeBot requests approval for a tool, the companion shows the request so you can approve or deny it without switching back to the main dashboard.

## What to expect

- Browser-internal pages may block content capture
- Some PDFs or protected pages may not allow script access
- The extension does not scrape pages automatically in the background
- The extension is not a desktop pet or browser-wide overlay

## Quick test checklist

- The popup opens
- Settings save correctly
- The companion connects to LimeBot
- `Ask this page` sends page context
- `Send selected text` sends the selected content
- `Watch video` captures the active video URL and playback position
- Pending approvals appear and can be handled
- Opera GX opens the companion in a tab

## Privacy

The extension only sends page content after you explicitly trigger an action such as:

- `Ask this page`
- `Send selected text`
- `Watch video` or a current-video request typed in the companion
- the matching context menu actions
