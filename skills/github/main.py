import sys
import json
import urllib.request
import ssl
from urllib.error import URLError, HTTPError
import os

DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"


def _read_env_value(key: str) -> str | None:
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"
    )
    try:
        with open(env_path, "r") as f:
            for line in f:
                if line.startswith(f"{key}="):
                    return line.strip().split("=", 1)[1]
    except Exception:
        pass
    return os.environ.get(key)


def get_skill_config() -> dict:
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "limebot.json"
    )
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    skills = data.get("skills", {})
    entries = skills.get("entries", {})
    return entries.get("github", {}) if isinstance(entries, dict) else {}


def get_token():
    # Attempt to load token from .env file
    token = _read_env_value("GITHUB_TOKEN") or _read_env_value("GH_TOKEN")
    if token:
        return token
    # Fallback to environment variables
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def api_request(method, endpoint, data=None):
    token = get_token()
    if not token:
        print("Error: GitHub token not found in .env")
        sys.exit(1)

    url = f"https://api.github.com{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "LimeBot-GitHub-Skill",
    }

    req_data = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=req_data, headers=headers, method=method)

    # Context to avoid SSL issues if they happen on local machines
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            if response.status == 204:
                return None
            body = response.read().decode("utf-8")
            return json.loads(body) if body else None
    except HTTPError as e:
        print(f"HTTP Error {e.code}: {e.read().decode('utf-8')}")
        sys.exit(1)
    except URLError as e:
        print(f"URL Error: {e.reason}")
        sys.exit(1)


def notify_backend(message: str, cfg: dict, kind: str = None, data: dict | None = None):
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
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{backend_url}/api/notify",
        data=data,
        headers={
            "Content-Type": "application/json",
            **({"x-api-key": api_key} if api_key else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as response:
            response.read()
    except Exception:
        # Notifications are best-effort
        pass


def get_current_user():
    """Dynamically fetches the current authenticated user's login to prevent hardcoding errors."""
    user_data = api_request("GET", "/user")
    return user_data.get("login")


def _render_pr_template(template: str, context: dict) -> str:
    if not template:
        return ""
    if isinstance(template, list):
        template = "\n".join(template)
    try:
        return template.format(**context)
    except Exception:
        return str(template)


def main():
    cfg = get_skill_config()
    if len(sys.argv) < 2:
        print("Usage: python main.py <command> [args...]")
        print("Commands:")
        print("  list-repos")
        print("  accept-invites")
        print("  create-pr <owner/repo> <head_branch> <base_branch> <title> [body]")
        print(
            "  create-pr <head_branch> <base_branch> <title> [body]  (uses default_repo)"
        )
        print(
            "  create-pr <head_branch> <title> [body]              (uses default_repo + default_base)"
        )
        print("  user-info")
        print("  invite-collaborator <repo_name> <username>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list-repos":
        repos = api_request("GET", "/user/repos?per_page=100")
        for r in repos:
            print(f"{r['full_name']} - {r['html_url']}")

    elif cmd == "accept-invites":
        invites = api_request("GET", "/user/repository_invitations")
        if not invites:
            print("No pending repository invitations.")
            return
        for inv in invites:
            inv_id = inv["id"]
            repo_name = inv["repository"]["full_name"]
            print(f"Accepting invitation for {repo_name} (ID: {inv_id})...")
            api_request("PATCH", f"/user/repository_invitations/{inv_id}")
            print(f"Successfully accepted invitation for {repo_name}.")

    elif cmd == "create-pr":
        default_repo = cfg.get("default_repo")
        default_base = cfg.get("default_base")
        args = sys.argv[2:]
        if len(args) < 2:
            print(
                "Usage: python main.py create-pr <owner/repo> <head_branch> <base_branch> <title> [body]"
            )
            sys.exit(1)

        if len(args) >= 4:
            repo = args[0]
            head = args[1]
            base = args[2]
            title = args[3]
            body = args[4] if len(args) > 4 else ""
        elif len(args) == 3:
            if not default_repo:
                print("Error: default_repo is not configured.")
                sys.exit(1)
            repo = default_repo
            head = args[0]
            base = args[1]
            title = args[2]
            body = ""
        else:
            if not default_repo or not default_base:
                print("Error: default_repo and default_base are not configured.")
                sys.exit(1)
            repo = default_repo
            head = args[0]
            base = default_base
            title = args[1]
            body = ""

        if not body:
            body = _render_pr_template(
                cfg.get("pr_template", ""),
                {"repo": repo, "head": head, "base": base, "title": title},
            )

        data = {"title": title, "head": head, "base": base, "body": body}
        res = api_request("POST", f"/repos/{repo}/pulls", data)
        pr_url = res.get("html_url")
        pr_number = res.get("number")
        print(f"Pull Request created: {pr_url}")

        labels = cfg.get("default_labels") or []
        reviewers = cfg.get("default_reviewers") or []
        if isinstance(labels, str):
            labels = [labels]
        if isinstance(reviewers, str):
            reviewers = [reviewers]

        if labels and pr_number:
            api_request(
                "POST",
                f"/repos/{repo}/issues/{pr_number}/labels",
                {"labels": labels},
            )

        if reviewers and pr_number:
            api_request(
                "POST",
                f"/repos/{repo}/pulls/{pr_number}/requested_reviewers",
                {"reviewers": reviewers},
            )

        if pr_url:
            note = f"GitHub PR created: {pr_url}"
            if labels:
                note += f" | labels: {', '.join(labels)}"
            if reviewers:
                note += f" | reviewers: {', '.join(reviewers)}"
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

    elif cmd == "user-info":
        user = api_request("GET", "/user")
        print(f"User: {user['login']} ({user['name']})")
        print(f"Profile: {user['html_url']}")

    elif cmd == "invite-collaborator":
        if len(sys.argv) < 4:
            print("Usage: python main.py invite-collaborator <repo_name> <username>")
            sys.exit(1)

        repo_name = sys.argv[2]
        target_user = sys.argv[3]

        # DYNAMICALLY GET USER to avoid assuming the username based on email/handles
        owner = get_current_user()
        if not owner:
            print("Could not fetch current authenticated user.")
            sys.exit(1)

        print(
            f"Authenticated as '{owner}'. Inviting '{target_user}' to '{owner}/{repo_name}'..."
        )

        # GitHub PUT to add collaborator
        res = api_request(
            "PUT", f"/repos/{owner}/{repo_name}/collaborators/{target_user}"
        )
        if res is not None and "html_url" in res:
            print(f"Success! Invitation sent to {target_user}.")
            print(f"Invitation URL: {res['html_url']}")
        else:
            print(
                f"Success! {target_user} is already a collaborator or invitation sent."
            )

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
