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

### Commands:
- `roots`: List all configured base directories from `ALLOWED_PATHS`.
- `list(path)`: View files and subdirectories.
- `read(path)`: Load text content from a file.
- `mkdir(path)`: Create a new directory (recursive).
- `write(path, content)`: Overwrite or create a new file. **Warning: This replaces existing content.**
- `rename(src, dst)` / `move(src, dst)`: Relocate or rename items.
- `delete(path)`: Permanently remove a file or folder. **CRITICAL: This action cannot be undone.**

### Security:
- All operations are restricted to paths defined in `ALLOWED_PATHS` within the `.env` file.
- The bot will explain *why* it needs to modify a file before asking for your approval in the dashboard.
