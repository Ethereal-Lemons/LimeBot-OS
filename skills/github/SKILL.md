---
name: github
description: Manage repositories, invitations, pull requests, and collaborator permissions.
dependencies:
  python: []
  node: []
  binaries: []
---

# GitHub Skill

The GitHub skill allows LimeBot to interact with the GitHub API to manage repositories, invitations, pull requests, and collaborator permissions.

## Setup

This skill requires a GitHub Personal Access Token (PAT) with appropriate scopes (e.g., `repo`, `user`).

1.  Create a GitHub PAT at [github.com/settings/tokens](https://github.com/settings/tokens).
2.  Add the token to your `.env` file in the project root:
    ```env
    GITHUB_TOKEN=your_github_token_here
    ```
Alternatively, you can use the environment variable `GH_TOKEN`.

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
python main.py <command> [args...]
```

### Commands

| Command | Description | Usage |
| :--- | :--- | :--- |
| `list-repos` | Lists all repositories you have access to. | `python main.py list-repos` |
| `accept-invites` | Automatically accepts all pending repository invitations. | `python main.py accept-invites` |
| `create-pr` | Creates a new pull request in a repository. | `python main.py create-pr <owner/repo> <head> <base> <title> [body]` |
| `create-pr` | Uses defaults if configured. | `python main.py create-pr <head> <base> <title> [body]` |
| `create-pr` | Uses defaults if configured. | `python main.py create-pr <head> <title> [body]` |
| `user-info` | Displays information about the authenticated user. | `python main.py user-info` |
| `invite-collaborator`| Invites a user as a collaborator to one of your repositories. | `python main.py invite-collaborator <repo_name> <username>` |

## Examples

**Create a Pull Request:**
```bash
python main.py create-pr LimeBot-OS/web feature-branch main "Add new feature" "This PR adds a cool new feature."
```

**Invite a Collaborator:**
```bash
python main.py invite-collaborator LimeBot brite
```
