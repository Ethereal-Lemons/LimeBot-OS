---
name: download_image
description: Fetch high-resolution images from the web (Pinterest, Wikimedia, Getty, etc.) and save them to the local 'temp' folder. 
---

# Image Downloader 🖼️
Use this skill when you need to download a high-quality image from a URL to share it with the user.

### 🎯 Pro Strategy (The "Hunter" Mindset):
LimeBot, don't get stuck in "perfectionist loops". If you find a page with multiple images:
1. **Meta-Grab**: Use `browser_snapshot` to see if there's a direct image link in the search results or a `meta` tag.
2. **Grab & Go**: Don't click "Next" or "Download" buttons on ad-heavy sites (like UHDPaper or WallpaperFlare). If you see a working `.jpg` or `.png` link, **take it immediately**.
3. **Smart Hunt**: If you have a Reddit or Pinterest page URL, just hand it to this skill! The script will automatically scrape the highest quality version for you.

### 🚀 Execution Command:
`run_command("python skills/download_image/main.py '<url>' 'temp/<filename>'")`

### 📋 Requirements:
- **URL**: A direct image link OR a page URL (Reddit, Pinterest, Wikimedia).
- **Filename**: Descriptive name (e.g., `lisa_figaro.jpg`). **Always save to `temp/`.**

### 📤 Output Handling:
To show the image to the user, use exactly: `![Description](temp/filename.jpg)`.
