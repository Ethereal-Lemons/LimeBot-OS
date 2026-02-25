---
name: download_image
description: A robust, "Honey Badger" image downloader that bypasses strict CDNs, ignores misleading Content-Types, and falls back to scraping if a direct link fails.
---

# Image Downloader (Honey Badger Edition) 🖼️
This skill is designed to be resilient. It doesn't trust HTTP headers; it trusts the actual file bytes. Use this to download images from the web even when servers try to block bots or serve incorrect MIME types.

### 🛡️ Robust Features:
1.  **Byte Sniffing**: Ignores `Content-Type` headers (often `binary/octet-stream`) and checks the file signature (magic numbers) to detect JPEGs, PNGs, GIFs, and WEBPs.
2.  **Smart Fallback**: If the URL returns HTML instead of an image, it automatically switches to scraping mode to find the high-res `og:image` or `twitter:image` tags.
3.  **Stealth Mode**: Uses a modern Chrome User-Agent to bypass basic anti-bot protections.

### 🚀 Execution Command:
`run_command("python skills/download_image/main.py '<url>' 'temp/<filename>'")`

### 📋 Requirements:
- **URL**: A direct image link OR a page URL (Reddit, Pinterest, 4KWallpapers, etc.).
- **Filename**: Descriptive name (e.g., `lisa_figaro.jpg`). **Always save to `temp/`.**

### 📤 Output Handling:
To show the image to the user, use a concise description: `![image](temp/filename.jpg)`.

> [!WARNING]
> DO NOT wrap your entire response inside the `![alt-text]` part of the image tag. Keep the alt-text short (e.g., "image" or "Lisa photo").

### ⚠️ Dependencies:
- `requests`
- `beautifulsoup4`
