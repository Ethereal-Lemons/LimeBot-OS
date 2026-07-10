# Backend Dockerfile (prod-friendly)
FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# System deps + Node.js (needed for skills/CLI)
RUN apt-get update && apt-get install -y \
    curl \
    git \
    build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Optional Python capabilities are opt-in and closed to known profiles.
ARG LIMEBOT_FEATURES=""
RUN for feature in $LIMEBOT_FEATURES; do \
      case "$feature" in \
        browser|memory|documents|mcp) pip install --no-cache-dir -r "requirements-$feature.txt" ;; \
        *) echo "Unknown LIMEBOT_FEATURES entry: $feature" >&2; exit 2 ;; \
      esac; \
    done

# App code
COPY . .

# Only the root Node runtime dependency is needed by the Python backend. The
# production frontend is built in its own image.
RUN npm ci --omit=dev --workspaces=false

# Optional Playwright browser install
ARG INSTALL_BROWSER=0
RUN if [ "$INSTALL_BROWSER" = "1" ]; then \
      pip install --no-cache-dir -r requirements-browser.txt && \
      playwright install --with-deps chromium; \
    fi

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/live', timeout=2)" || exit 1

CMD ["python", "main.py"]
