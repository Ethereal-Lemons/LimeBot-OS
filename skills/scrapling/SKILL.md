---
name: scrapling
description: An adaptive, stealthy web scraper that bypasses common anti-bot protections using Scrapling's StealthyFetcher, curl_cffi, and browserforge to extract raw text or CSS selectors.
dependencies:
  python:
    - scrapling
    - curl_cffi
    - browserforge
  node: []
  binaries: []
---

# Scrapling Web Scraper 🕷️

An adaptive Web Scraping skill implementing the Scrapling framework (`D4Vinci/Scrapling`). This tool is designed for advanced and stealthy web scraping, bypassing common anti-bot protections.

## Usage

You can use the `api.py` script to scrape URLs, optionally specifying CSS selectors or requesting text extraction.

### Command Structure
```bash
python skills/scrapling/api.py <URL> [OPTIONS]
```

### Options
- `--selector <CSS>`: Extract specific elements using a CSS selector (e.g., `h1`, `.class-name`).
- `--text`: Return raw text instead of HTML elements.
- `--stealthy`: Use `StealthyFetcher` to bypass anti-bot mechanisms.

### Examples

**Extract title text:**
```bash
python skills/scrapling/api.py "http://example.com" --selector h1 --text
```

**Get full page HTML:**
```bash
python skills/scrapling/api.py "http://example.com"
```

**Use stealth mode for protected sites:**
```bash
python skills/scrapling/api.py "https://bot.sannysoft.com/" --stealthy --text
```

## Dependencies
- `scrapling`
- `curl_cffi` (for TLS fingerprint bypass)
- `browserforge` (for user-agent generation)
