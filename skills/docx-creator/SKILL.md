---
name: docx-creator
description: "Use this skill whenever the user wants to create, read, edit, or manipulate Word documents (.docx files). Triggers include any mention of Word documents, .docx files, reports, memos, letters, templates, tables of contents, headings, page numbers, letterheads, tracked changes, comments, images, or document conversion."
---

# DOCX creation, editing, and analysis

Use this skill for professional Microsoft Word documents. A .docx file is a ZIP
archive containing WordprocessingML XML, so preserve the package structure and
validate the result after every edit.

## Quick reference

| Task | Approach |
| --- | --- |
| Read or analyze | Extract text with pandoc when available; otherwise inspect the package XML. |
| Create a simple document | Use scripts/create_docx.py. |
| Create a highly formatted document | Use docx-js or python-docx, then validate the output. |
| Edit an existing document | Unpack the DOCX, edit only the required XML, repack, and validate. |
| Convert legacy .doc | Convert to .docx with LibreOffice before editing. |
| Render for visual QA | Convert to PDF with LibreOffice, then render pages with Poppler. |

## LimeBot runtime and safety

- Resolve {baseDir} to this skill directory before running its scripts.
- Save outputs only inside the user's authorized workspace and obey the configured
  ALLOWED_PATHS roots.
- Never read or write credentials, .env files, configuration secrets, or files
  outside the requested document workspace.
- Use a new output filename unless the user explicitly asks to overwrite the
  source document.
- Do not claim a document is complete until it has been structurally validated.
- For edits, keep a copy of the original and make the smallest possible change.

## Dependencies

The simple creator uses python-docx. Install the optional documents feature
when it is not available:

    npm run lime-bot feature install documents

Optional tools:

- pandoc for text extraction with tracked-change awareness.
- LibreOffice for .doc conversion and PDF rendering.
- Poppler (pdftoppm) for page images.
- Node's docx package for rich programmatic document generation.

If an optional dependency is unavailable, report that clearly and use the
available fallback rather than silently producing an unverified file.

## Reading and conversion

Extract readable text when formatting is not needed:

    pandoc --track-changes=all input.docx -o output.md

For raw inspection, unpack the ZIP package into a temporary workspace and inspect
word/document.xml, headers, footers, relationships, styles, numbering, and
media. Do not edit the original archive in place.

Convert a legacy document before editing:

    python {baseDir}/scripts/office/soffice.py --convert-to docx --outdir output-dir input.doc

If that command is unavailable, use the repository's LibreOffice wrapper or
ask the user to install LibreOffice.

## Creating documents

### Simple LimeBot documents

Use the existing wrapper for straightforward title-and-section documents:

    python {baseDir}/scripts/create_docx.py --title "Your title" --filename "output.docx" --content "Section heading:Section body" "Another heading:More body"

For anything requiring tables, images, custom styles, page numbers, or complex
layouts, use a richer generator instead of forcing content through the simple
Heading:Body format.

### Page setup

docx-js defaults to A4. Set page size explicitly. For US Letter use 12,240 x
15,840 DXA with one-inch margins:

    sections: [{
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }
        }
      },
      children: []
    }]

For landscape, pass the portrait dimensions and set
orientation: PageOrientation.LANDSCAPE; docx-js swaps the edges internally.

### Styles and headings

- Use Arial or another universally available font as the default.
- Override built-in heading IDs exactly (Heading1, Heading2, and so on).
- Use HeadingLevel for heading paragraphs and include outlineLevel when a
  table of contents is required.
- Keep titles black and use consistent spacing before and after headings.
- Use real paragraph elements. Never encode multiple paragraphs with \n.

### Lists

Never insert a bullet character into text to simulate a list. Configure
LevelFormat.BULLET or decimal numbering and reuse the same numbering reference
when a list should continue.

### Tables

Tables need both table-level and cell-level widths for reliable rendering:

- Use WidthType.DXA, never percentage widths.
- Set width on the table and columnWidths whose values sum to that width.
- Set every cell's width to its matching column width.
- Add cell margins for readable padding.
- Use ShadingType.CLEAR for shaded cells; do not use solid shading that can
  render as a black block.
- Do not use empty tables as divider lines. Use a paragraph bottom border.

### Images, breaks, and links

- ImageRun must specify its file type (png, jpg, jpeg, gif, bmp, or svg) and
  complete alt text.
- Put PageBreak inside a Paragraph, or use pageBreakBefore.
- Use ExternalHyperlink for URLs and a bookmark plus InternalHyperlink for
  links inside the document.
- Keep image files in the package's word/media/ folder and add matching
  relationship and content-type entries when editing XML directly.

### Headers, footers, and TOCs

Use Header and Footer objects rather than tables for page furniture. Use
tab stops for two-column footer content and PageNumber.CURRENT for page
numbers. A TOC should reference real heading levels and use hyperlinks.

## Editing existing documents

Follow these steps in order:

1. Copy the source document to a working output path.
2. Unpack the DOCX ZIP into a temporary directory.
3. Edit only the relevant files under word/.
4. Preserve run properties (w:rPr) when changing formatted text.
5. Repack the directory as a DOCX.
6. Validate the package and render it for visual inspection when layout matters.

The skill includes package helpers for this workflow:

    python {baseDir}/scripts/office/unpack.py input.docx work/
    python {baseDir}/scripts/office/pack.py work/ output.docx --original input.docx
    python {baseDir}/scripts/office/validate.py output.docx

Use --force only when the destination is disposable. To produce a clean copy
with tracked changes accepted:

    python {baseDir}/scripts/office/accept_changes.py input.docx clean.docx

The common XML files are:

- word/document.xml for body content.
- word/styles.xml for styles.
- word/numbering.xml for lists.
- word/header*.xml and word/footer*.xml for page furniture.
- word/_rels/document.xml.rels and [Content_Types].xml for assets.

Use XML entities for typographic punctuation in new text:

    <w:t>Here&#x2019;s a quote: &#x201C;Hello&#x201D;</w:t>

When editing tracked changes:

- Use Claude as the author unless the user requests another name.
- Replace complete run elements when adding w:ins or w:del; do not put
  tracked-change elements inside an existing run.
- Use w:delText inside deletions and preserve the original run properties.
- If deleting an entire paragraph, mark its paragraph mark deleted so accepting
  changes does not leave an empty list item.
- Comments require package-level comment parts, relationships, and markers;
  do not create a comment marker without all three pieces.

## Structural validation

Before delivery, verify:

- The file is a readable ZIP archive with [Content_Types].xml and
  word/document.xml.
- Every XML part is well-formed.
- Relationships point to existing package parts.
- Images have matching media files, relationships, and content types.
- Tables, numbering, headers, footers, and TOC references remain present.
- The document opens without a repair warning in Word or LibreOffice.

For layout-sensitive work, convert the result to PDF and inspect every rendered
page. Check for clipped text, unexpected blank pages, broken table widths,
missing images, bad page breaks, and footer/header overlap. Iterate until the
rendered output is correct.

## Critical rules

- Set page size explicitly; do not rely on the A4 default.
- Never use Unicode bullets as a substitute for numbering configuration.
- Never use \n as a paragraph separator.
- Put page breaks inside paragraphs.
- Specify image types and complete alt text.
- Use DXA table widths with matching column and cell widths.
- Preserve XML relationships and formatting runs during edits.
- Validate and, when relevant, render every generated or modified document.
