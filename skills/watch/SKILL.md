---
name: watch
description: Analyze videos, screen recordings, ads, and video links with LimeBot's native video tool. Use for requests to watch, summarize, transcribe, inspect a moment, read on-screen text, or answer questions about YouTube, Vimeo, TikTok, Loom, MP4, MOV, MKV, or WebM media.
dependencies:
  python:
    - yt-dlp
    - Pillow
  binaries:
    - ffmpeg
    - ffprobe
metadata:
  aliases:
    - video
  keywords:
    - watch
    - video
    - transcript
    - screen-recording
---

# Watch videos

Call `analyze_video`; never shell out or call `yt-dlp`/FFmpeg directly.

- Use `detail="transcript"` when visuals are irrelevant.
- Use `detail="efficient"` for a quick visual scan.
- Use `detail="balanced"` by default for summaries, UI recordings, ads, and visual questions.
- Pass `start` and/or `end` whenever the user names a moment or time range.
- Use `resolution=1024` only when small on-screen text must be read; otherwise use 512.
- Set `question` to the user's actual question so long transcripts retain relevant segments.
- Treat contact sheets as internal visual context; do not send them unless the user asks.

When answering, distinguish evidence from native captions, OpenAI Whisper, and frames-only analysis. If the tool reports missing dependencies, relay its exact installation command. If captions are absent and Whisper is disabled, do not imply that audio was understood.
