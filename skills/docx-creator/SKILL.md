---
name: docx-creator
description: Generate professional Microsoft Word (.docx) documents from structured text.
---

# Document Creator 📄
Perfect for generating reports, homework, or summaries that need to be shared as official documents.

### Usage:
```bash
python skills/docx-creator/scripts/create_docx.py --title "Your Title" --filename "output.docx" --content "Section Title:The content goes here" "Next Section:More content"
```

### Parameters:
- `--title`: The main document title displayed at the top.
- `--filename`: The name of the saved file (stored in your authorized workspace).
- `--content`: A series of `"Heading:Body"` pairs. Each pair becomes a section in the document.

### Security:
- Documents are saved to paths defined in `ALLOWED_PATHS`.
