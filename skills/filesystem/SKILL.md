---
name: filesystem
description: Safe local file operations and directory management.
dependencies:
  python: []
  node: []
  binaries: []
---

# Filesystem 📁
LimeBot's direct access to its local storage and authorized workspace paths. 

### Important:
This skill documentation is a strategy guide, not a callable tool name.
Do not call `filesystem` directly as a tool.
Use LimeBot's canonical tools instead:
- `list_dir(path)`: View files and subdirectories.
- `read_file(path)`: Load text content from a file.
- `search_files(query, path, mode)`: Search by filename or file content.
- `write_file(path, content)`: Overwrite or create a file. **Warning: This replaces existing content.**
- `delete_file(path)`: Permanently remove a file or folder. **CRITICAL: This action cannot be undone.**
- `run_command(command)`: Only if you truly need shell behavior the canonical tools do not cover.

### Strategy:
- Prefer `list_dir` and `read_file` for inspection.
- Prefer `search_files` over manually walking a large tree.
- Use `write_file` only when the user explicitly wants a file created or changed.
- Treat deletion as irreversible and explain that before approval.

### Security:
- All operations are restricted to paths defined in `ALLOWED_PATHS` within the `.env` file.
- The bot will explain *why* it needs to modify a file before asking for your approval in the dashboard.
