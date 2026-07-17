import subprocess
import importlib.util
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "skills" / "github" / "main.py"
_SPEC = importlib.util.spec_from_file_location("limebot_github_skill", _MODULE_PATH)
github = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(github)


def test_flatten_paginated_gh_api_response():
    assert github._flatten_paginated([[{"id": 1}], [{"id": 2}, {"id": 3}]]) == [
        {"id": 1},
        {"id": 2},
        {"id": 3},
    ]
    assert github._flatten_paginated({"id": 1}) == []


def test_run_gh_uses_cli_and_redacts_child_process_token(monkeypatch):
    calls = {}

    monkeypatch.setattr(github, "_gh_path", lambda: "gh")
    monkeypatch.setattr(github, "_gh_env", lambda: {"GH_TOKEN": "secret"})

    def fake_run(command, **kwargs):
        calls["command"] = command
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "github-user\n", "")

    monkeypatch.setattr(github.subprocess, "run", fake_run)

    assert github._run_gh(["auth", "status"]) == "github-user"
    assert calls["command"] == ["gh", "auth", "status"]
    assert calls["kwargs"]["env"] == {"GH_TOKEN": "secret"}
    assert calls["kwargs"]["capture_output"] is True


def test_gh_api_paginates_repo_listing_and_passes_json_on_stdin(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[1].startswith("user/repos"):
            return '[[{"id": 1}]]'
        return '{"ok": true}'

    monkeypatch.setattr(github, "_run_gh", fake_run)

    repos = github.api_request("GET", "user/repos?per_page=100")
    assert repos == [{"id": 1}]

    response = github.api_request(
        "POST", "repos/example/project/issues/1/labels", {"labels": ["bug"]}
    )
    assert response == {"ok": True}
    args, kwargs = calls[1]
    assert args == [
        "api",
        "repos/example/project/issues/1/labels",
        "--method",
        "POST",
        "--input",
        "-",
    ]
    assert kwargs["input_text"] == '{"labels": ["bug"]}'


def test_gh_env_injects_token_only_when_process_environment_lacks_one(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        github, "_read_env_value", lambda key: "state-token" if key == "GH_TOKEN" else None
    )

    env = github._gh_env()

    assert env["GH_TOKEN"] == "state-token"
