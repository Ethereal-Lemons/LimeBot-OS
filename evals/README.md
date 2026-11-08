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
quality. For that, use the live evaluation below.

# Live task evaluation (real model, opt-in)

`evals/live/` holds realistic tasks (fix a failing test, rename across files,
aggregate CSVs, edit JSON precisely, triage a log, a Spanish prompt, and so on).
The configured model works on each one inside a disposable copy of the project,
and the result is scored by checks the model never sees: hidden test files that
are written only after the run, exact file comparisons, and the facts in its
reply. A run that says "done" while the checks fail is counted as a *false
success*, the number that matters most.

```powershell
python scripts/evaluate_live.py validate                 # free: every case fails untouched and is solvable
python scripts/evaluate_live.py plan                     # free: cases and the worst-case token spend
python scripts/evaluate_live.py run --yes --case scoped-edit   # spends tokens on one case
python scripts/evaluate_live.py run --yes --repeats 3    # the whole set, three times
python scripts/evaluate_live.py compare old.json new.json
```

Nothing is spent without `--yes`. Spending is capped three ways: a model-call
cap and a token cap per case (set in each case file), and a run-wide
`--budget` (default 2,000,000 tokens). A capped call is refused before it
reaches the provider, and the run is recorded as over budget rather than
passed. The model and credentials come from your `.env` (`LLM_MODEL` and the
provider `*_API_KEY` variables; ChatGPT Codex OAuth is not supported here);
`--model` overrides the model for one run. Other secrets, such as
`APP_API_KEY` and the ElevenLabs key, are not passed to the worker.

Tool access is limited to the file tools, `run_command`/`run_steps` and
`verify_files`. Edits must stay under `workspace/`, and commands must be python,
pytest, or read-only git. This is the same trusted-code boundary as the offline
runner, not an operating-system sandbox: the model can run any Python it writes
inside the disposable copy.

Reports go to `data/evaluations/live-*.json` and contain outcomes, check ids,
tool counts, call and token counts, and timings, never prompts, replies, or file
contents. Run before and after a change and `compare` the two reports: it lists
the cases that got worse or better. Pass rates on 13 cases are noisy, so use
`--repeats 3` or more before trusting a small difference.

To add a case, write a JSON file in `evals/live/` with `fixtures` (visible),
`hidden_fixtures` (checked after the run), a `solution`, and `checks`.
`tests/test_live_eval.py` and `validate` prove that a doing-nothing agent fails
and the solution passes.
