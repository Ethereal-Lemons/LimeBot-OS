#!/bin/bash
# LimeBot Gateway — entry point for autorun (systemd / launchd / manual).
# When run by systemd, this process must stay in the foreground so systemd
# can track it correctly. The old `& disown` pattern caused immediate exit
# and a restart loop.

cd "$(dirname "$0")/.."
exec npm run lime-bot start -- --quick
