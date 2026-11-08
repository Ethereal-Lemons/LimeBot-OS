"""Live-model task evaluation with hard spending caps.

Unlike ``core.task_eval`` (scripted replays), this runs the configured model on
realistic tasks inside a disposable workspace and scores the *resulting files*
with checks the model never sees. It answers "does a change make LimeBot finish
real work more often?" with a number.

Spending is bounded three ways: a per-case model-call cap, a per-case token cap,
and a run-wide token budget. Nothing is spent unless the caller opts in.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Any, Mapping
import uuid

from core.task_eval import (
    FixtureFile,
    SourceSnapshot,
    _atomic_write_json,
    _copy_source_snapshot,
    _valid_id,
    _validate_snapshot,
    _validate_workspace_path,
)

LIVE_TOOL_ALLOWLIST = frozenset(
    {
        "calculate",
        "diagnose_files",
        "edit_file",
        "list_dir",
        "read_file",
        "run_command",
        "run_steps",
        "search_files",
        "verify_files",
        "write_file",
    }
)
LIVE_SENSITIVE_TOOLS = frozenset({"edit_file", "write_file", "run_command", "run_steps"})
MUTATING_TOOLS = frozenset({"edit_file", "write_file"})
_COMMAND_PROGRAMS = frozenset({"python", "python3", "py", "pytest", "git"})
_GIT_READ_ONLY = frozenset({"status", "diff", "log", "show", "ls-files"})
_SHELL_META = re.compile(r"&&|\|\||[;|<>`]|\$\(")
_WORKER = Path(__file__).resolve().parents[1] / "scripts" / "live_eval_worker.py"
_SECRET_ENV_EXCLUDED = frozenset({"APP_API_KEY", "ELEVENLABS_API_KEY"})
_PASSTHROUGH_PREFIXES = ("LLM_", "LIMEBOT_TEST_LLM_")
_PASSTHROUGH_SUFFIXES = ("_API_KEY", "_API_BASE", "_BASE_URL", "_ENDPOINT", "_PROJECT", "_REGION")
DEFAULT_MAX_CALLS = 40
DEFAULT_MAX_TOKENS = 300_000
DEFAULT_TOTAL_TOKEN_BUDGET = 2_000_000


class BudgetExceeded(RuntimeError):
    """Raised instead of issuing a model call once a spending cap is reached."""


@dataclass(frozen=True)
class FileCheck:
    id: str
    path: str
    contains: str | None = None
    equals: str | None = None
    regex: str | None = None
    not_contains: str | None = None


@dataclass(frozen=True)
class ReplyCheck:
    id: str
    any_of: tuple[str, ...]


@dataclass(frozen=True)
class CommandCheck:
    id: str
    argv: tuple[str, ...]
    timeout: int = 30
    stdout_contains: str | None = None


@dataclass(frozen=True)
class LiveCase:
    id: str
    category: str
    prompt: str
    fixtures: tuple[FixtureFile, ...] = ()
    hidden_fixtures: tuple[FixtureFile, ...] = ()
    solution: tuple[FixtureFile, ...] = ()
    file_checks: tuple[FileCheck, ...] = ()
    reply_checks: tuple[ReplyCheck, ...] = ()
    command_checks: tuple[CommandCheck, ...] = ()
    timeout_seconds: int = 240
    max_model_calls: int = DEFAULT_MAX_CALLS
    max_tokens: int = DEFAULT_MAX_TOKENS

    @property
    def check_ids(self) -> tuple[str, ...]:
        return tuple(
            item.id
            for group in (self.file_checks, self.reply_checks, self.command_checks)
            for item in group
        )


@dataclass(frozen=True)
class LivePlan:
    cases: tuple[LiveCase, ...]
    snapshot: SourceSnapshot
    repeats: int = 1
    model: str = ""
    total_token_budget: int = DEFAULT_TOTAL_TOKEN_BUDGET


@dataclass(frozen=True)
class LiveRunEvidence:
    case_id: str
    category: str
    repeat: int
    status: str
    checks: Mapping[str, bool]
    tool_counts: Mapping[str, int]
    tool_failures: int
    model_calls: int
    tokens_in: int
    tokens_out: int
    wall_time_ms: int
    budget_exceeded: bool = False
    skipped: bool = False
    worker_code: str = "ok"

    @property
    def passed(self) -> bool:
        return (
            not self.skipped
            and not self.budget_exceeded
            and self.worker_code == "ok"
            and bool(self.checks)
            and all(self.checks.values())
        )

    @property
    def false_success(self) -> bool:
        """The agent reported completion but the hidden checks disagree."""
        return self.status == "completed" and bool(self.checks) and not all(
            self.checks.values()
        )


# ---------------------------------------------------------------------------
# Case loading and validation
# ---------------------------------------------------------------------------


def _fixtures(value: Any) -> tuple[FixtureFile, ...]:
    return tuple(
        FixtureFile(path=str(item["path"]), content=str(item["content"]))
        for item in (value or [])
    )


def live_case_from_dict(value: Mapping[str, Any]) -> LiveCase:
    try:
        checks = value.get("checks") or {}
        return LiveCase(
            id=str(value["id"]),
            category=str(value.get("category") or "general"),
            prompt=str(value["prompt"]),
            fixtures=_fixtures(value.get("fixtures")),
            hidden_fixtures=_fixtures(value.get("hidden_fixtures")),
            solution=_fixtures(value.get("solution")),
            file_checks=tuple(
                FileCheck(
                    id=str(item["id"]),
                    path=str(item["path"]),
                    contains=item.get("contains"),
                    equals=item.get("equals"),
                    regex=item.get("regex"),
                    not_contains=item.get("not_contains"),
                )
                for item in checks.get("files", [])
            ),
            reply_checks=tuple(
                ReplyCheck(
                    id=str(item["id"]),
                    any_of=tuple(str(text) for text in item["any_of"]),
                )
                for item in checks.get("reply", [])
            ),
            command_checks=tuple(
                CommandCheck(
                    id=str(item["id"]),
                    argv=tuple(str(part) for part in item["argv"]),
                    timeout=int(item.get("timeout") or 30),
                    stdout_contains=item.get("stdout_contains"),
                )
                for item in checks.get("commands", [])
            ),
            timeout_seconds=int(value.get("timeout_seconds") or 240),
            max_model_calls=int(value.get("max_model_calls") or DEFAULT_MAX_CALLS),
            max_tokens=int(value.get("max_tokens") or DEFAULT_MAX_TOKENS),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid live task case structure") from exc


def load_live_cases(directory: Path) -> tuple[LiveCase, ...]:
    if not directory.is_dir():
        raise ValueError("The live cases path must be a directory")
    files = sorted(directory.glob("*.json"))
    if not files:
        raise ValueError("No live evaluation cases were found")
    return tuple(
        live_case_from_dict(json.loads(item.read_text(encoding="utf-8")))
        for item in files
    )


def validate_live_case(case: LiveCase) -> None:
    if not _valid_id(case.id):
        raise ValueError(f"Invalid case id: {case.id!r}")
    if not case.prompt.strip() or len(case.prompt) > 4_000:
        raise ValueError(f"Case {case.id} has an invalid prompt")
    if not (1 <= case.timeout_seconds <= 900):
        raise ValueError(f"Case {case.id} has an invalid timeout")
    if not (1 <= case.max_model_calls <= 200):
        raise ValueError(f"Case {case.id} has an invalid model-call cap")
    if not (1_000 <= case.max_tokens <= 2_000_000):
        raise ValueError(f"Case {case.id} has an invalid token cap")
    for group in (case.fixtures, case.hidden_fixtures, case.solution):
        if len(group) > 40:
            raise ValueError(f"Case {case.id} has too many files")
        for item in group:
            _validate_workspace_path(item.path)
            if len(item.content) > 100_000:
                raise ValueError(f"Case {case.id} has an oversized file")
    if not case.check_ids:
        raise ValueError(f"Case {case.id} has no checks")
    if len(set(case.check_ids)) != len(case.check_ids) or not all(
        _valid_id(check_id) for check_id in case.check_ids
    ):
        raise ValueError(f"Case {case.id} has duplicate or unsafe check ids")
    for check in case.file_checks:
        _validate_workspace_path(check.path)
        modes = [
            value is not None
            for value in (check.contains, check.equals, check.regex, check.not_contains)
        ]
        if sum(modes) != 1:
            raise ValueError(f"Check {check.id} needs exactly one comparison")
        if check.regex is not None:
            re.compile(check.regex)
    for check in case.reply_checks:
        if not check.any_of:
            raise ValueError(f"Check {check.id} needs at least one phrase")
    for command in case.command_checks:
        if not command.argv or command.argv[0] != "python":
            raise ValueError(f"Check {command.id} must run python")
        if not (1 <= command.timeout <= 120):
            raise ValueError(f"Check {command.id} has an invalid timeout")


# ---------------------------------------------------------------------------
# Hidden-check evaluation (pure: only reads the workspace and runs python)
# ---------------------------------------------------------------------------


def write_fixture_files(project_dir: Path, files: tuple[FixtureFile, ...]) -> None:
    root = (project_dir / "workspace").resolve()
    root.mkdir(parents=True, exist_ok=True)
    for item in files:
        _validate_workspace_path(item.path)
        target = (project_dir / item.path).resolve()
        if target != root and root not in target.parents:
            raise ValueError("fixture escaped the workspace")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content.encode("utf-8"))


def evaluate_checks(
    case: LiveCase, project_dir: Path, reply_text: str = ""
) -> dict[str, bool]:
    """Score the workspace and reply. A check that cannot run counts as failed."""
    results: dict[str, bool] = {}
    for check in case.file_checks:
        try:
            content = (project_dir / check.path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            results[check.id] = False
            continue
        if check.equals is not None:
            results[check.id] = content == check.equals
        elif check.contains is not None:
            results[check.id] = check.contains in content
        elif check.not_contains is not None:
            results[check.id] = check.not_contains not in content
        else:
            results[check.id] = bool(re.search(check.regex or "", content, re.MULTILINE))
    lowered_reply = reply_text.casefold()
    for reply in case.reply_checks:
        results[reply.id] = any(text.casefold() in lowered_reply for text in reply.any_of)
    for command in case.command_checks:
        results[command.id] = _run_command_check(command, project_dir / "workspace")
    return results


def _check_environment() -> dict[str, str]:
    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC")
        if os.environ.get(key)
    }
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
        }
    )
    return env


def _run_command_check(command: CommandCheck, cwd: Path) -> bool:
    if not cwd.is_dir():
        return False
    argv = [sys.executable, *command.argv[1:]]
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=_check_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=command.timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    if command.stdout_contains is not None:
        return command.stdout_contains in (completed.stdout + completed.stderr)
    return True


# ---------------------------------------------------------------------------
# Tool policy for the disposable workspace
# ---------------------------------------------------------------------------


def command_allowed(command: str) -> bool:
    """Only python, pytest and read-only git commands run during an evaluation."""
    text = str(command or "").strip()
    if not text or _SHELL_META.search(text) or "\n" in text:
        return False
    parts = text.split()
    program = parts[0].strip("\"'").lower()
    if program.endswith(".exe"):
        program = program[:-4]
    if program not in _COMMAND_PROGRAMS:
        return False
    if program == "git":
        return len(parts) > 1 and parts[1] in _GIT_READ_ONLY
    return True


def is_workspace_path(raw: str, project_dir: Path) -> bool:
    """True for a path that stays inside ``project_dir/workspace``."""
    value = str(raw or "").strip()
    if not value:
        return False
    root = (project_dir / "workspace").resolve()
    candidate = Path(value)
    target = (candidate if candidate.is_absolute() else project_dir / candidate).resolve()
    return target == root or root in target.parents


# ---------------------------------------------------------------------------
# Spending caps
# ---------------------------------------------------------------------------


def _usage_numbers(usage: Any) -> tuple[int, int]:
    def read(name: str) -> int:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
        try:
            return max(int(value or 0), 0)
        except (TypeError, ValueError):
            return 0

    return read("prompt_tokens"), read("completion_tokens")


class _UsageStream:
    def __init__(self, stream: Any, on_usage) -> None:
        self._iterator = stream.__aiter__()
        self._on_usage = on_usage

    def __aiter__(self) -> "_UsageStream":
        return self

    async def __anext__(self) -> Any:
        chunk = await self._iterator.__anext__()
        usage = getattr(chunk, "usage", None)
        if usage:
            self._on_usage(usage)
        return chunk


class BudgetedLLMClient:
    """Wrap a real client: count calls and tokens, refuse past the caps."""

    def __init__(self, inner: Any, *, max_calls: int, max_tokens: int) -> None:
        self._inner = inner
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.exceeded = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def _record(self, usage: Any) -> None:
        prompt, completion = _usage_numbers(usage)
        self.tokens_in += prompt
        self.tokens_out += completion

    async def complete(self, provider: Any, request: Any) -> Any:
        if getattr(request, "session_id", None) == "__warmup__":
            # Readiness warmup is not part of the task and should cost nothing.
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="Evaluation warmup.", tool_calls=None)
                    )
                ],
                usage=None,
            )
        if self.calls >= self.max_calls or self.tokens >= self.max_tokens:
            self.exceeded = True
            raise BudgetExceeded("live evaluation spending cap reached")
        self.calls += 1
        response = await self._inner.complete(provider, request)
        if getattr(request, "stream", False):
            return _UsageStream(response, self._record)
        usage = getattr(response, "usage", None)
        if usage:
            self._record(usage)
        return response


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def worker_environment(
    project: Path, state: Path, source_env: Mapping[str, str], model: str = ""
) -> dict[str, str]:
    """Environment for the worker: provider credentials in, everything else out."""
    env = {
        key: source_env[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC")
        if source_env.get(key)
    }
    for key, value in source_env.items():
        if key in _SECRET_ENV_EXCLUDED or not value:
            continue
        if key.startswith(_PASSTHROUGH_PREFIXES) or key.endswith(_PASSTHROUGH_SUFFIXES):
            env[key] = value
    env.update(
        {
            "PYTHONPATH": str(project),
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "HOME": str(state),
            "USERPROFILE": str(state),
            "XDG_CONFIG_HOME": str(state / ".config"),
            "LIMEBOT_STATE_DIR": str(state),
            "LIMEBOT_EVAL_LIVE": "1",
            "LLM_EMBEDDING_MODEL": "disabled",
            "ALLOWED_PATHS": str(project),
            "ENABLE_DISCORD": "false",
            "ENABLE_WHATSAPP": "false",
            "ENABLE_TELEGRAM": "false",
            "ENABLE_DYNAMIC_PERSONALITY": "false",
            "APPROVAL_POLICY_PROFILE": "autonomous",
        }
    )
    if model:
        env["LLM_MODEL"] = model
    return env


def case_to_dict(case: LiveCase) -> dict[str, Any]:
    return asdict(case)


def run_live(
    plan: LivePlan,
    source_env: Mapping[str, str],
    *,
    progress=None,
) -> dict[str, Any]:
    """Run every case sequentially against the real model and write a report."""
    if plan.repeats < 1 or plan.repeats > 10:
        raise ValueError("repeats must be between 1 and 10")
    if plan.total_token_budget < 10_000:
        raise ValueError("total token budget is too small to run anything")
    _validate_snapshot(plan.snapshot)
    for case in plan.cases:
        validate_live_case(case)
    if not (source_env.get("LLM_MODEL") or plan.model):
        raise ValueError("No model configured: set LLM_MODEL or pass --model")

    evidence: list[LiveRunEvidence] = []
    spent = 0
    for repeat in range(plan.repeats):
        for case in plan.cases:
            if spent >= plan.total_token_budget:
                evidence.append(_skipped(case, repeat))
                continue
            row = _run_case(case, plan, repeat, source_env)
            spent += row.tokens_in + row.tokens_out
            evidence.append(row)
            if progress:
                progress(row, spent)

    return _write_report(plan, source_env, evidence, spent)


def _skipped(case: LiveCase, repeat: int) -> LiveRunEvidence:
    return LiveRunEvidence(
        case_id=case.id,
        category=case.category,
        repeat=repeat,
        status="skipped_budget",
        checks={},
        tool_counts={},
        tool_failures=0,
        model_calls=0,
        tokens_in=0,
        tokens_out=0,
        wall_time_ms=0,
        skipped=True,
        worker_code="skipped",
    )


def _run_case(
    case: LiveCase, plan: LivePlan, repeat: int, source_env: Mapping[str, str]
) -> LiveRunEvidence:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="limebot-live-eval-") as temp_name:
        root = Path(temp_name)
        project, state = root / "project", root / "state"
        project.mkdir()
        state.mkdir()
        _copy_source_snapshot(plan.snapshot.root, project)
        request_path, result_path = root / "request.json", root / "result.json"
        request_path.write_text(
            json.dumps(
                {
                    "case": case_to_dict(case),
                    "project_dir": str(project),
                    "state_dir": str(state),
                    "result_path": str(result_path),
                    "run_id": f"live_{uuid.uuid4().hex}",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        env = worker_environment(project, state, source_env, plan.model)
        worker_code = "worker_failed"
        result: dict[str, Any] = {}
        try:
            with (root / "worker.log").open("wb") as output:
                subprocess.run(
                    [sys.executable, str(_WORKER), str(request_path)],
                    cwd=project,
                    env=env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=case.timeout_seconds + 60,
                    check=False,
                )
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                worker_code = str(result.get("worker_code") or "worker_failed")
        except subprocess.TimeoutExpired:
            worker_code = "worker_timeout"
        except (OSError, ValueError):
            worker_code = "worker_failed"

    return LiveRunEvidence(
        case_id=case.id,
        category=case.category,
        repeat=repeat,
        status=str(result.get("status") or worker_code),
        checks={str(k): bool(v) for k, v in (result.get("checks") or {}).items()},
        tool_counts={str(k): int(v) for k, v in (result.get("tool_counts") or {}).items()},
        tool_failures=int(result.get("tool_failures") or 0),
        model_calls=int(result.get("model_calls") or 0),
        tokens_in=int(result.get("tokens_in") or 0),
        tokens_out=int(result.get("tokens_out") or 0),
        wall_time_ms=int((time.perf_counter() - started) * 1000),
        budget_exceeded=bool(result.get("budget_exceeded")),
        worker_code=worker_code,
    )


def summarize(rows: list[LiveRunEvidence]) -> dict[str, Any]:
    ran = [row for row in rows if not row.skipped]
    passed = sum(row.passed for row in ran)
    by_case: dict[str, list[bool]] = {}
    for row in ran:
        by_case.setdefault(row.case_id, []).append(row.passed)
    by_category: dict[str, list[bool]] = {}
    for row in ran:
        by_category.setdefault(row.category, []).append(row.passed)
    return {
        "runs": len(ran),
        "skipped": len(rows) - len(ran),
        "passed": passed,
        "pass_rate": round(passed / len(ran), 3) if ran else 0.0,
        "false_successes": sum(row.false_success for row in ran),
        "budget_exceeded": sum(row.budget_exceeded for row in ran),
        "avg_model_calls": round(sum(r.model_calls for r in ran) / len(ran), 1) if ran else 0.0,
        "tokens_in": sum(r.tokens_in for r in ran),
        "tokens_out": sum(r.tokens_out for r in ran),
        "by_case": {k: round(sum(v) / len(v), 3) for k, v in sorted(by_case.items())},
        "by_category": {k: round(sum(v) / len(v), 3) for k, v in sorted(by_category.items())},
    }


def _write_report(
    plan: LivePlan,
    source_env: Mapping[str, str],
    rows: list[LiveRunEvidence],
    spent: int,
) -> dict[str, Any]:
    from core.runtime_paths import get_data_dir

    report_id = f"live-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
    report = {
        "report_id": report_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": plan.model or source_env.get("LLM_MODEL", ""),
        "repeats": plan.repeats,
        "tokens_spent": spent,
        "token_budget": plan.total_token_budget,
        "summary": summarize(rows),
        "runs": [
            {**asdict(row), "passed": row.passed, "false_success": row.false_success}
            for row in rows
        ],
    }
    report_dir = get_data_dir() / "evaluations"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{report_id}.json"
    _atomic_write_json(path, report)
    report["report_path"] = str(Path("data") / "evaluations" / path.name)
    return report


def compare_reports(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Per-case pass-rate deltas between two live reports."""
    old = (before.get("summary") or {}).get("by_case") or {}
    new = (after.get("summary") or {}).get("by_case") or {}
    cases = sorted(set(old) | set(new))
    deltas = {
        case: round(float(new.get(case, 0.0)) - float(old.get(case, 0.0)), 3)
        for case in cases
        if case in old and case in new
    }
    regressions = [case for case, delta in deltas.items() if delta < 0]
    improvements = [case for case, delta in deltas.items() if delta > 0]
    old_rate = float((before.get("summary") or {}).get("pass_rate") or 0.0)
    new_rate = float((after.get("summary") or {}).get("pass_rate") or 0.0)
    if regressions and not improvements:
        verdict = "worse"
    elif improvements and not regressions:
        verdict = "better"
    elif not regressions and not improvements:
        verdict = "same"
    else:
        verdict = "mixed"
    return {
        "verdict": verdict,
        "pass_rate_before": old_rate,
        "pass_rate_after": new_rate,
        "regressions": regressions,
        "improvements": improvements,
        "deltas": deltas,
        "only_before": sorted(set(old) - set(new)),
        "only_after": sorted(set(new) - set(old)),
        "false_successes_before": int((before.get("summary") or {}).get("false_successes") or 0),
        "false_successes_after": int((after.get("summary") or {}).get("false_successes") or 0),
    }


def tool_counter(names: list[str]) -> dict[str, int]:
    return dict(Counter(names))


__all__ = [
    "BudgetExceeded",
    "BudgetedLLMClient",
    "CommandCheck",
    "FileCheck",
    "LiveCase",
    "LivePlan",
    "LiveRunEvidence",
    "ReplyCheck",
    "command_allowed",
    "compare_reports",
    "evaluate_checks",
    "is_workspace_path",
    "live_case_from_dict",
    "load_live_cases",
    "run_live",
    "summarize",
    "validate_live_case",
    "worker_environment",
    "write_fixture_files",
]
