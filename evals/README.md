# Offline task evaluation

The suite contains only synthetic requests, file fixtures, and deterministic
tool-call replays. Each baseline and candidate attempt runs in a fresh temporary
copy containing only `core/`, `channels/`, and `config.py`, with a separate
synthetic state directory. The worker exposes a small local-tool allowlist;
network, browser, media, connector, shell, and subagent tools are unavailable.
This is a trusted-code evaluation runner, not an operating-system sandbox for
malicious source snapshots.

Run the current snapshot against itself:

```powershell
python scripts/evaluate_tasks.py compare --baseline . --candidate .
```

Compare two existing LimeBot checkouts:

```powershell
python scripts/evaluate_tasks.py compare --baseline ..\limebot-main --candidate . --repeats 3 --fail-on-regression
```

Reports are written under the configured LimeBot state directory at
`data/evaluations/`. They contain normalized outcomes, assertion codes, tool
names and counts, and rounded timings. They do not contain prompts, replies,
fixture contents, tool arguments, provider output, or persona text. Offline
replays exercise LimeBot's real managed turn, tool preflight, approvals,
verification, and durable task records, but they do not measure model reasoning
quality. Live-model evaluation is not part of this first version because a
reliable per-provider spending cap is not yet available.
