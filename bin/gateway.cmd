@echo off
cd /d "%~dp0.."
start "" /b npm run lime-bot start -- --quick
exit
