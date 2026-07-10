#!/bin/sh
set -eu

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

if [ ! -f limebot.json ]; then
  printf '{\n  "skills": {\n    "enabled": []\n  }\n}\n' > limebot.json
  echo "Created limebot.json"
fi

if [ ! -f allowed_paths.txt ]; then
  printf '# Paths available to LimeBot inside the container.\n./persona\n./logs\n./temp\n' > allowed_paths.txt
  echo "Created allowed_paths.txt"
fi

mkdir -p data logs temp persona/memory persona/sessions skills bridge/session
echo "Docker runtime files are ready."
