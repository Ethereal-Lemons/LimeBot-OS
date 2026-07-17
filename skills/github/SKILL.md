---
name: github
description: Manage repositories, invitations, pull requests, and collaborator permissions.
dependencies:
  python: []
  node: []
  binaries: [gh]
---

# GitHub Skill

The GitHub skill delegates authentication and GitHub API transport to the official GitHub CLI (gh). It manages repositories, invitations, pull requests, and collaborator permissions without storing a separate API client or token session.

For review-only CI, use `limebot review-diff` and `.github/workflows/limebot-review.yml` instead. That entrypoint parses only an explicit unified diff, redacts likely credentials, uses no GitHub API mutation, and uploads an artifact without posting comments or pushing code. This skill is intentionally separate because its authenticated commands can modify repository state.

## Setup

This skill requires the GitHub CLI. Install it from [cli.github.com](https://cli.github.com/), then authenticate once:

```bash
gh auth login
gh auth status
```

The gh CLI owns credential storage, host selection, and authentication checks. For headless deployments, `GITHUB_TOKEN` or `GH_TOKEN` may be set in the LimeBot state `.env`; the skill passes that value only to the gh child process.

## Personalization (limebot.json)

You can set defaults and notifications in `limebot.json`:

```json
{
  "skills": {
    "entries": {
      "github": {
        "default_repo": "owner/repo",
        "default_base": "main",
        "pr_template": "## Summary\n- {title}\n\n## Testing\n- [ ] Not run\n",
        "default_labels": ["chore", "needs-review"],
        "default_reviewers": ["reviewer1", "reviewer2"],
        "notify_channels": ["web", "discord"],
        "notify_web_chat_id": "system",
        "notify_discord_channel_ids": ["123456789012345678"],
        "backend_url": "http://127.0.0.1:8000"
      }
    }
  }
}
```

## Usage

Run the skill using Python:

```bash
python {baseDir}/main.py <command> [args...]
```

Every command verifies `gh auth status` first. The underlying read and mutation requests use `gh api`, while pull requests use `gh pr create`.

### Commands

| Command | Description | Usage |
| :--- | :--- | :--- |
| `list-repos` | Lists all repositories you have access to. | `python {baseDir}/main.py list-repos` |
| `accept-invites` | Automatically accepts all pending repository invitations. | `python {baseDir}/main.py accept-invites` |
| `create-pr` | Creates a new pull request in a repository. | `python {baseDir}/main.py create-pr <owner/repo> <head> <base> <title> [body]` |
| `create-pr` | Uses defaults if configured. | `python {baseDir}/main.py create-pr <head> <base> <title> [body]` |
| `create-pr` | Uses defaults if configured. | `python {baseDir}/main.py create-pr <head> <title> [body]` |
| `user-info` | Displays information about the authenticated user. | `python {baseDir}/main.py user-info` |
| `invite-collaborator`| Invites a user as a collaborator to one of your repositories. | `python {baseDir}/main.py invite-collaborator <repo_name> <username>` |

## Examples

**Create a Pull Request:**
```bash
python {baseDir}/main.py create-pr LimeBot-OS/web feature-branch main "Add new feature" "This PR adds a cool new feature."
```

**Invite a Collaborator:**
```bash
python {baseDir}/main.py invite-collaborator LimeBot brite
```
