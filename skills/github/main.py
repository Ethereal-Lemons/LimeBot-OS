import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
GH_HOST = "github.com"
GH_TIMEOUT_SECONDS = 60


def _project_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _state_dir() -> Path | None:
    value = os.environ.get("LIMEBOT_STATE_DIR", "").strip()
    return Path(value).expanduser() if value else None


def _read_env_value(key: str) -> str | None:
    paths = []
    state_dir = _state_dir()
    if state_dir:
        paths.append(state_dir / ".env")
    paths.append(_project_dir() / ".env")

    for env_path in paths:
        try:
            with env_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, value = line.split("=", 1)
                    if name.strip() != key:
                        continue
                    value = value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                        value = value[1:-1]
                    return value
        except OSError:
            continue
    return os.environ.get(key)


def get_skill_config() -> dict:
    paths = []
    state_dir = _state_dir()
    if state_dir:
        paths.append(state_dir / "limebot.json")
    paths.append(_project_dir() / "limebot.json")

    data: dict[str, Any] = {}
    for config_path in paths:
        try:
            with config_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            break
        except (OSError, json.JSONDecodeError):
            continue

    skills = data.get("skills", {}) if isinstance(data, dict) else {}
    entries = skills.get("entries", {}) if isinstance(skills, dict) else {}
    config = entries.get("github", {}) if isinstance(entries, dict) else {}
    return config if isinstance(config, dict) else {}


def get_token() -> str | None:
    return _read_env_value("GITHUB_TOKEN") or _read_env_value("GH_TOKEN")


def _gh_path() -> str:
    path = shutil.which("gh")
    if not path:
        raise RuntimeError(
            "GitHub CLI (gh) is not installed or is not on PATH. "
            "Install it from https://cli.github.com/ and try again."
        )
    return path


def _gh_env() -> dict[str, str]:
    env = os.environ.copy()
    token = get_token()
    # A token in LimeBot's .env is scoped to the gh child process. It is never
    # included in command output or forwarded to the backend notification.
    if token and not env.get("GH_TOKEN") and not env.get("GITHUB_TOKEN"):
        env["GH_TOKEN"] = token
    return env


def _redact(text: str) -> str:
    token = get_token()
    if token:
        text = text.replace(token, "[redacted]")
    return text.replace("gho_", "gho_[redacted]")


def _run_gh(
    args: list[str],
    *,
    input_text: str | None = None,
    timeout: float = GH_TIMEOUT_SECONDS,
) -> str:
    command = [_gh_path(), *args]
    try:
        result = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_gh_env(),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"gh {' '.join(args)} timed out after {timeout:g} seconds."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Unable to run GitHub CLI: {exc}") from exc

    if result.returncode != 0:
        detail = _redact((result.stderr or result.stdout or "").strip())
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"gh {' '.join(args)} failed{suffix}")
    return result.stdout.strip()


def ensure_authenticated() -> None:
    try:
        _run_gh(["auth", "status", "--hostname", GH_HOST], timeout=15)
    except RuntimeError as exc:
        raise RuntimeError(
            "GitHub authentication is not available. Run gh auth login "
            "and then retry the GitHub command."
        ) from exc


def _flatten_paginated(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    flattened: list[Any] = []
    for page in value:
        if isinstance(page, list):
            flattened.extend(page)
        else:
            flattened.append(page)
    return flattened


def api_request(method: str, endpoint: str, data: dict | None = None) -> Any:
    normalized = endpoint.lstrip("/")
    args = ["api", normalized, "--method", method.upper()]
    if normalized.startswith("user/repos") or normalized.startswith(
        "user/repository_invitations"
    ):
        args.extend(["--paginate", "--slurp"])
    if data is not None:
        args.extend(["--input", "-"])
        input_text = json.dumps(data)
    else:
        input_text = None

    output = _run_gh(args, input_text=input_text)
    if not output:
        return None
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return output
    if "--slurp" in args:
        return _flatten_paginated(value)
    return value


def notify_backend(
    message: str, cfg: dict, kind: str | None = None, data: dict | None = None
) -> None:
    backend_url = cfg.get("backend_url") or DEFAULT_BACKEND_URL
    channels = cfg.get("notify_channels", [])
    if isinstance(channels, str):
        channels = [channels]
    if not channels:
        return
    payload = {
        "channels": channels,
        "content": message,
        "web_chat_id": cfg.get("notify_web_chat_id", "system"),
        "discord_channel_ids": cfg.get("notify_discord_channel_ids")
        or cfg.get("notify_discord_channel_id"),
        "kind": kind,
        "data": data or {},
    }
    api_key = _read_env_value("APP_API_KEY")
    request_data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{backend_url}/api/notify",
        data=request_data,
        headers={
            "Content-Type": "application/json",
            **({"x-api-key": api_key} if api_key else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except Exception:
        # Notifications are best-effort.
        pass


def get_current_user() -> dict[str, Any]:
    user = api_request("GET", "user")
    if not isinstance(user, dict):
        raise RuntimeError("GitHub returned an unexpected user response.")
    return user


def _render_pr_template(template: str, context: dict) -> str:
    if not template:
        return ""
    if isinstance(template, list):
        template = "\n".join(template)
    try:
        return template.format(**context)
    except Exception:
        return str(template)


def _create_pr(cfg: dict, args: list[str]) -> None:
    default_repo = cfg.get("default_repo")
    default_base = cfg.get("default_base")
    if len(args) < 2:
        raise RuntimeError(
            "Usage: python main.py create-pr <owner/repo> <head_branch> "
            "<base_branch> <title> [body]"
        )

    if len(args) >= 4:
        repo, head, base, title = args[:4]
        body = args[4] if len(args) > 4 else ""
    elif len(args) == 3:
        if not default_repo:
            raise RuntimeError("default_repo is not configured.")
        repo, head, base, title = default_repo, args[0], args[1], args[2]
        body = ""
    else:
        if not default_repo or not default_base:
            raise RuntimeError("default_repo and default_base are not configured.")
        repo, head, base, title = default_repo, args[0], default_base, args[1]
        body = ""

    if not body:
        body = _render_pr_template(
            cfg.get("pr_template", ""),
            {"repo": repo, "head": head, "base": base, "title": title},
        )

    command = [
        "pr",
        "create",
        "--repo",
        str(repo),
        "--head",
        str(head),
        "--base",
        str(base),
        "--title",
        str(title),
        "--body",
        str(body),
    ]
    labels = cfg.get("default_labels") or []
    reviewers = cfg.get("default_reviewers") or []
    if isinstance(labels, str):
        labels = [labels]
    if isinstance(reviewers, str):
        reviewers = [reviewers]
    for label in labels:
        command.extend(["--label", str(label)])
    for reviewer in reviewers:
        command.extend(["--reviewer", str(reviewer)])

    output = _run_gh(command)
    pr_url = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "")
    if not pr_url:
        raise RuntimeError("gh pr create returned no pull request URL.")
    print(f"Pull Request created: {pr_url}")

    note = f"GitHub PR created: {pr_url}"
    if labels:
        note += f" | labels: {', '.join(map(str, labels))}"
    if reviewers:
        note += f" | reviewers: {', '.join(map(str, reviewers))}"
    notify_backend(
        note,
        cfg,
        kind="github_pr",
        data={
            "repo": repo,
            "title": title,
            "url": pr_url,
            "labels": labels,
            "reviewers": reviewers,
            "head": head,
            "base": base,
        },
    )


def _print_usage() -> None:
    print("Usage: python main.py <command> [args...]")
    print("Commands:")
    print("  list-repos")
    print("  accept-invites")
    print("  create-pr <owner/repo> <head_branch> <base_branch> <title> [body]")
    print("  create-pr <head_branch> <base_branch> <title> [body] (uses default_repo)")
    print("  create-pr <head_branch> <title> [body] (uses default_repo + default_base)")
    print("  user-info")
    print("  invite-collaborator <repo_name> <username>")


def main() -> None:
    if len(sys.argv) < 2:
        _print_usage()
        raise SystemExit(1)

    cfg = get_skill_config()
    cmd = sys.argv[1]
    ensure_authenticated()

    if cmd == "list-repos":
        repos = api_request("GET", "user/repos?per_page=100") or []
        for repo in repos:
            print(f"{repo.get('full_name', '')} - {repo.get('html_url', '')}")
    elif cmd == "accept-invites":
        invites = api_request("GET", "user/repository_invitations") or []
        if not invites:
            print("No pending repository invitations.")
            return
        for invite in invites:
            invite_id = invite["id"]
            repo_name = invite["repository"]["full_name"]
            print(f"Accepting invitation for {repo_name} (ID: {invite_id})...")
            api_request("PATCH", f"user/repository_invitations/{invite_id}")
            print(f"Successfully accepted invitation for {repo_name}.")
    elif cmd == "create-pr":
        _create_pr(cfg, sys.argv[2:])
    elif cmd == "user-info":
        user = get_current_user()
        print(f"User: {user.get('login', '')} ({user.get('name') or 'unknown'})")
        print(f"Profile: {user.get('html_url', '')}")
    elif cmd == "invite-collaborator":
        if len(sys.argv) < 4:
            raise RuntimeError(
                "Usage: python main.py invite-collaborator <repo_name> <username>"
            )
        repo_name, target_user = sys.argv[2:4]
        owner = get_current_user().get("login")
        if not owner:
            raise RuntimeError("Could not determine the authenticated GitHub user.")
        print(
            f"Authenticated as '{owner}'. Inviting '{target_user}' "
            f"to '{owner}/{repo_name}'..."
        )
        response = api_request(
            "PUT", f"repos/{owner}/{repo_name}/collaborators/{target_user}"
        )
        if isinstance(response, dict) and response.get("html_url"):
            print(f"Success! Invitation sent to {target_user}.")
            print(f"Invitation URL: {response['html_url']}")
        else:
            print(
                f"Success! {target_user} is already a collaborator or invitation sent."
            )
    else:
        raise RuntimeError(f"Unknown command: {cmd}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
