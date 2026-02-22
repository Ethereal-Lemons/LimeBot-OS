#!/bin/bash
cd "$(dirname "$0")/.."
npm run lime-bot start -- --quick &
disown
