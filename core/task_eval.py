"""Offline, paired task evaluation for isolated LimeBot source snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Literal, Mapping


OFFLINE_TOOL_ALLOWLIST = frozenset(
    {
        "calculate",
        "edit_file",
        "list_dir",
        "memory_search",
        "memory_save",
        "read_file",
        "search_files",
        "verify_files",
        "write_file",
    }
)
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_NETWORK_TAG = re.compile(r"<\s*/?\s*(?:discord_send|discord_embed)\b", re.IGNORECASE)
_SOURCE_DIRS = ("core", "channels")
_IGNORED_SOURCE_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".limebot",
        "data",
        "temp",
        "logs",
        "persona",
    }
)
_BLOCKED_FIXTURE_NAMES = frozenset(
    {".env", "limebot.json", "config.py", "secrets.py", "agents.md"}
)
_WORKER = Path(__file__).resolve().parents[1] / "scripts" / "task_eval_worker.py"


@dataclass(frozen=True)
class FixtureFile:
    path: str
    content: str


@dataclass(frozen=True)
class PlannedToolCall:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplayTurn:
    content: str = ""
    tool_calls: tuple[PlannedToolCall, ...] = ()


@dataclass(frozen=True)
class ReplyCheck:
    id: str
    contains: str


@dataclass(frozen=True)
class FileCheck:
    id: str
    path: str
    contains: str | None = None
    equals: str | None = None


@dataclass(frozen=True)
class Expectations:
    status: Literal["completed", "blocked", "cancelled"] = "completed"
    verification: Literal["passed", "failed", "missing", "not_required"] = "not_required"
    required_tools: tuple[str, ...] = ()
    reply_checks: tuple[ReplyCheck, ...] = ()
    file_checks: tuple[FileCheck, ...] = ()


@dataclass(frozen=True)
class TaskCase:
    id: str
    prompt: str
    replay: tuple[ReplayTurn, ...]
    fixtures: tuple[FixtureFile, ...] = ()
    memory: str = ""
    approvals: tuple[str, ...] = ()
    expectations: Expectations = field(default_factory=Expectations)
    timeout_seconds: int = 90


@dataclass(frozen=True)
class SourceSnapshot:
    id: str
    root: Path


@dataclass(frozen=True)
class EvaluationPlan:
    cases: tuple[TaskCase, ...]
    baseline: SourceSnapshot
    candidate: SourceSnapshot
    repeats: int = 1
    timeout_seconds: int = 90


@dataclass(frozen=True)
class RunEvidence:
    case_id: str
    variant_id: str
    repeat: int
    task_run_id: str
    status: str
    verification: str
    assertions: tuple[str, ...]
    tool_names: tuple[str, ...]
    tool_failures: int
    failure_categories: tuple[str, ...]
    model_calls: int
    model_time_ms: int
    tool_calls: int
    tool_time_ms: int
    wall_time_ms: int

    @property
    def passed(self) -> bool:
        return bool(self.assertions) and all(
            assertion.endswith(":pass") for assertion in self.assertions
        )


@dataclass(frozen=True)
class EvaluationReport:
    report_id: str
    created_at: str
    baseline_id: str
    candidate_id: str
    repeats: int
    runs: tuple[RunEvidence, ...]
    aggregate: Mapping[str, int | float]
    recommendation: str
    reason_codes: tuple[str, ...]
    report_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def task_case_from_dict(value: Mapping[str, Any]) -> TaskCase:
    """Parse a JSON task fixture into the small evaluator domain model."""
    try:
        expected = value.get("expectations") or {}
        return TaskCase(
            id=str(value["id"]),
            prompt=str(value["prompt"]),
            replay=tuple(
                ReplayTurn(
                    content=str(turn.get("content") or ""),
                    tool_calls=tuple(
                        PlannedToolCall(
                            name=str(call["name"]),
                            arguments=dict(call.get("arguments") or {}),
                        )
                        for call in (turn.get("tool_calls") or [])
                    ),
                )
                for turn in value["replay"]
            ),
            fixtures=tuple(
                FixtureFile(path=str(item["path"]), content=str(item["content"]))
                for item in (value.get("fixtures") or [])
            ),
            memory=str(value.get("memory") or ""),
            approvals=tuple(str(name) for name in value.get("approvals", [])),
            expectations=Expectations(
                status=str(expected.get("status") or "completed"),
                verification=str(expected.get("verification") or "not_required"),
                required_tools=tuple(str(name) for name in expected.get("required_tools", [])),
                reply_checks=tuple(
                    ReplyCheck(id=str(check["id"]), contains=str(check["contains"]))
                    for check in expected.get("reply_checks", [])
                ),
                file_checks=tuple(
                    FileCheck(
                        id=str(check["id"]),
                        path=str(check["path"]),
                        contains=(str(check["contains"]) if "contains" in check else None),
                        equals=(str(check["equals"]) if "equals" in check else None),
                    )
                    for check in expected.get("file_checks", [])
                ),
            ),
            timeout_seconds=int(value.get("timeout_seconds") or 90),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid task case structure") from exc


def validate_plan(plan: EvaluationPlan) -> None:
    if not plan.cases:
        raise ValueError("At least one evaluation case is required")
    if plan.repeats < 1 or plan.repeats > 20:
        raise ValueError("repeats must be between 1 and 20")
    if plan.timeout_seconds < 1 or plan.timeout_seconds > 600:
        raise ValueError("timeout_seconds must be between 1 and 600")
    if not _valid_id(plan.baseline.id) or not _valid_id(plan.candidate.id):
        raise ValueError("Snapshot IDs must use letters, digits, dot, underscore, or dash")
    if plan.baseline.id == plan.candidate.id:
        raise ValueError("Baseline and candidate IDs must differ")
    _validate_snapshot(plan.baseline)
    _validate_snapshot(plan.candidate)

    case_ids: set[str] = set()
    for case in plan.cases:
        if not _valid_id(case.id) or case.id in case_ids:
            raise ValueError("Task case IDs must be unique safe identifiers")
        case_ids.add(case.id)
        if not case.prompt.strip() or len(case.prompt) > 4_000:
            raise ValueError(f"Task case {case.id} has an invalid prompt")
        if len(case.memory) > 100_000:
            raise ValueError(f"Task case {case.id} has oversized synthetic memory")
        if not case.replay or len(case.replay) > 30:
            raise ValueError(f"Task case {case.id} must have 1 to 30 replay turns")
        if case.timeout_seconds < 1 or case.timeout_seconds > 600:
            raise ValueError(f"Task case {case.id} has an invalid timeout")
        if case.expectations.status not in {"completed", "blocked", "cancelled"}:
            raise ValueError(f"Task case {case.id} has an invalid expected status")
        if case.expectations.verification not in {
            "passed", "failed", "missing", "not_required"
        }:
            raise ValueError(f"Task case {case.id} has an invalid verification expectation")
        seen_paths: set[str] = set()
        if len(case.fixtures) > 32:
            raise ValueError(f"Task case {case.id} has too many fixture files")
        for fixture in case.fixtures:
            _validate_workspace_path(fixture.path)
            if fixture.path in seen_paths:
                raise ValueError(f"Task case {case.id} repeats a fixture path")
            seen_paths.add(fixture.path)
            if len(fixture.content) > 100_000:
                raise ValueError(f"Task case {case.id} has an oversized fixture")
        for call in case.replay:
            if _NETWORK_TAG.search(call.content):
                raise ValueError("Offline replay content contains a network delivery tag")
            for tool_call in call.tool_calls:
                if tool_call.name not in OFFLINE_TOOL_ALLOWLIST:
                    raise ValueError(
                        f"Offline replay tool is not allowed: {tool_call.name}"
                    )
                try:
                    serialized_args = json.dumps(tool_call.arguments, allow_nan=False)
                except (TypeError, ValueError) as exc:
                    raise ValueError("Tool arguments must be JSON values") from exc
                if len(serialized_args) > 20_000:
                    raise ValueError("Tool arguments exceed the offline fixture limit")
        for check in case.expectations.reply_checks:
            if not _valid_id(check.id) or not check.contains:
                raise ValueError(f"Task case {case.id} has an invalid reply check")
        for check in case.expectations.file_checks:
            if not _valid_id(check.id) or (check.contains is None) == (check.equals is None):
                raise ValueError(f"Task case {case.id} has an invalid file check")
            _validate_workspace_path(check.path)
        if any(name not in OFFLINE_TOOL_ALLOWLIST for name in case.expectations.required_tools):
            raise ValueError(f"Task case {case.id} expects a disallowed tool")
        if any(name not in {"edit_file", "write_file"} for name in case.approvals):
            raise ValueError(f"Task case {case.id} has an unsupported approval tool")


async def evaluate(plan: EvaluationPlan) -> EvaluationReport:
    """Run aligned baseline/candidate pairs with offline scripted turns."""
    validate_plan(plan)
    evidence: list[RunEvidence] = []

    for repeat in range(plan.repeats):
        variants = (
            (plan.baseline, plan.candidate)
            if repeat % 2 == 0
            else (plan.candidate, plan.baseline)
        )
        for case in plan.cases:
            for variant in variants:
                evidence.append(
                    await _run_case(
                        case,
                        variant,
                        repeat,
                        timeout=min(plan.timeout_seconds, case.timeout_seconds),
                    )
                )

    evidence.sort(key=lambda item: (item.case_id, item.repeat, item.variant_id))
    baseline_rows = [row for row in evidence if row.variant_id == plan.baseline.id]
    candidate_rows = [row for row in evidence if row.variant_id == plan.candidate.id]
    baseline_by_pair = {(row.case_id, row.repeat): row for row in baseline_rows}
    candidate_by_pair = {(row.case_id, row.repeat): row for row in candidate_rows}
    aligned_keys = sorted(set(baseline_by_pair) & set(candidate_by_pair))
    regressions = [
        key
        for key in aligned_keys
        if baseline_by_pair[key].passed and not candidate_by_pair[key].passed
    ]
    baseline_passes = sum(row.passed for row in baseline_rows)
    candidate_passes = sum(row.passed for row in candidate_rows)
    if regressions or candidate_passes < baseline_passes:
        recommendation = "keep_baseline"
        reasons = ("candidate_regression",)
    elif candidate_passes > baseline_passes:
        recommendation = "candidate_promising"
        reasons = ("candidate_improved_expected_checks",)
    elif candidate_rows and all(row.passed for row in candidate_rows):
        recommendation = "task_path_passed"
        reasons = ("no_expected_check_regressions",)
    else:
        recommendation = "inconclusive"
        reasons = ("expected_checks_failed_or_incomplete",)

    from core.runtime_paths import get_data_dir

    report_dir = get_data_dir() / "evaluations"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"eval-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
    report_path = report_dir / f"{report_id}.json"
    report_reference = str(Path("data") / "evaluations" / report_path.name)
    report = EvaluationReport(
        report_id=report_id,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        baseline_id=plan.baseline.id,
        candidate_id=plan.candidate.id,
        repeats=plan.repeats,
        runs=tuple(evidence),
        aggregate={
            "baseline_runs": len(baseline_rows),
            "candidate_runs": len(candidate_rows),
            "baseline_passes": baseline_passes,
            "candidate_passes": candidate_passes,
            "candidate_regressions": len(regressions),
            "paired_runs": len(aligned_keys),
        },
        recommendation=recommendation,
        reason_codes=reasons,
        report_path=report_reference,
    )
    _atomic_write_json(report_path, report.to_dict())
    return report


async def _run_case(
    case: TaskCase, variant: SourceSnapshot, repeat: int, *, timeout: int
) -> RunEvidence:
    task_run_id = f"eval_{uuid.uuid4().hex}"
    wall_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="limebot-eval-") as temp_name:
        attempt_root = Path(temp_name)
        project = attempt_root / "project"
        state = attempt_root / "state"
        request_path = attempt_root / "request.json"
        result_path = attempt_root / "result.json"
        log_path = attempt_root / "worker.log"
        project.mkdir()
        state.mkdir()
        _copy_source_snapshot(variant.root, project)
        payload = {
            "case": _case_to_dict(case),
            "task_run_id": task_run_id,
            "project_dir": str(project),
            "state_dir": str(state),
            "result_path": str(result_path),
        }
        request_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        env = _worker_environment(project, state)
        worker_code = "worker_failed"
        try:
            with log_path.open("wb") as output:
                subprocess.run(
                    [sys.executable, str(_WORKER), str(request_path)],
                    cwd=project,
                    env=env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=timeout + 15,
                    check=False,
                )
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                worker_code = str(result.get("worker_code") or "worker_failed")
            else:
                result = {}
        except subprocess.TimeoutExpired:
            result = {}
            worker_code = "worker_timeout"
        except (OSError, json.JSONDecodeError, ValueError):
            result = {}
            worker_code = "worker_failed"

    elapsed_ms = int((time.perf_counter() - wall_started) * 1000)
    if worker_code != "ok":
        assertion = "worker:fail"
        return RunEvidence(
            case_id=case.id,
            variant_id=variant.id,
            repeat=repeat,
            task_run_id=task_run_id,
            status=worker_code,
            verification="missing",
            assertions=(assertion,),
            tool_names=(),
            tool_failures=1,
            failure_categories=(worker_code,),
            model_calls=0,
            model_time_ms=0,
            tool_calls=0,
            tool_time_ms=0,
            wall_time_ms=elapsed_ms,
        )

    status = str(result.get("status") or "incomplete")
    verification = str(result.get("verification") or "missing")
    tool_names = tuple(sorted(set(map(str, result.get("tool_names") or []))))
    assertions = [
        f"status:{'pass' if status == case.expectations.status else 'fail'}",
    ]
    if case.expectations.verification != "not_required":
        assertions.append(
            "verification:"
            + ("pass" if verification == case.expectations.verification else "fail")
        )
    assertions.extend(
        f"tool:{name}:{'pass' if name in tool_names else 'fail'}"
        for name in case.expectations.required_tools
    )
    reply_checks = result.get("reply_checks") or {}
    assertions.extend(
        f"reply:{check.id}:{'pass' if reply_checks.get(check.id) else 'fail'}"
        for check in case.expectations.reply_checks
    )
    file_checks = result.get("file_checks") or {}
    assertions.extend(
        f"file:{check.id}:{'pass' if file_checks.get(check.id) else 'fail'}"
        for check in case.expectations.file_checks
    )
    return RunEvidence(
        case_id=case.id,
        variant_id=variant.id,
        repeat=repeat,
        task_run_id=task_run_id,
        status=status,
        verification=verification,
        assertions=tuple(assertions),
        tool_names=tool_names,
        tool_failures=int(result.get("tool_failures") or 0),
        failure_categories=tuple(
            sorted(set(map(str, result.get("failure_categories") or [])))
        ),
        model_calls=int(result.get("model_calls") or 0),
        model_time_ms=int(result.get("model_time_ms") or 0),
        tool_calls=int(result.get("tool_calls") or 0),
        tool_time_ms=int(result.get("tool_time_ms") or 0),
        wall_time_ms=elapsed_ms,
    )


def _copy_source_snapshot(source: Path, target: Path) -> None:
    source = source.expanduser().resolve()
    _validate_snapshot(SourceSnapshot("snapshot", source))
    for name in _SOURCE_DIRS:
        shutil.copytree(
            source / name,
            target / name,
            ignore=_source_ignore,
        )
    shutil.copy2(source / "config.py", target / "config.py")
    (target / "skills").mkdir()


def _source_ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(directory) / name
        lowered = name.lower()
        if (
            lowered in _IGNORED_SOURCE_NAMES
            or lowered.startswith(".env")
            or path.is_symlink()
            or lowered in _BLOCKED_FIXTURE_NAMES
            or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db"}
        ):
            ignored.add(name)
    return ignored


def _worker_environment(project: Path, state: Path) -> dict[str, str]:
    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC")
        if os.environ.get(key)
    }
    env.update(
        {
            "PYTHONPATH": str(project),
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "HOME": str(state),
            "USERPROFILE": str(state),
            "XDG_CONFIG_HOME": str(state / ".config"),
            "LIMEBOT_STATE_DIR": str(state),
            "LIMEBOT_EVAL_OFFLINE": "1",
            "LLM_MODEL": "openai/gpt-evaluation",
            "LLM_EMBEDDING_MODEL": "disabled",
            "OPENAI_API_KEY": "sk-evaluation-placeholder",
            "ALLOWED_PATHS": str(project),
            "ENABLE_DISCORD": "false",
            "ENABLE_WHATSAPP": "false",
            "ENABLE_TELEGRAM": "false",
            "ENABLE_DYNAMIC_PERSONALITY": "false",
        }
    )
    return env


def _case_to_dict(case: TaskCase) -> dict[str, Any]:
    return {
        "id": case.id,
        "prompt": case.prompt,
        "timeout_seconds": case.timeout_seconds,
        "replay": [
            {
                "content": turn.content,
                "tool_calls": [
                    {"name": call.name, "arguments": dict(call.arguments)}
                    for call in turn.tool_calls
                ],
            }
            for turn in case.replay
        ],
        "fixtures": [asdict(fixture) for fixture in case.fixtures],
        "memory": case.memory,
        "approvals": list(case.approvals),
        "expectations": asdict(case.expectations),
    }


def _validate_snapshot(snapshot: SourceSnapshot) -> None:
    root = snapshot.root.expanduser().resolve()
    if not root.is_dir() or any(not (root / name).is_dir() for name in _SOURCE_DIRS):
        raise ValueError(f"Snapshot {snapshot.id} must contain core/ and channels/")
    if not (root / "config.py").is_file():
        raise ValueError(f"Snapshot {snapshot.id} must contain config.py")


def _validate_workspace_path(value: str) -> None:
    normalized = str(value).replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or ":" in normalized
        or "\0" in normalized
    ):
        raise ValueError("Fixture paths must stay inside the disposable workspace")
    if path.parts[0] != "workspace":
        raise ValueError("Fixture paths must start with workspace/")
    for part in path.parts:
        lowered = part.lower()
        if (
            lowered in _BLOCKED_FIXTURE_NAMES
            or lowered.startswith(".env")
            or Path(lowered).suffix in {".pem", ".key", ".p12", ".pfx"}
        ):
            raise ValueError("Fixture path uses a blocked filename")


def _valid_id(value: str) -> bool:
    return bool(_CASE_ID.fullmatch(str(value or "")))


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
