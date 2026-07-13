/** Normalize model-authored Markdown without removing real paragraph breaks. */
export function compactMarkdownSpacing(content: string) {
    return content
        .replace(/\n{3,}/g, "\n\n")
        .replace(/\n[\t ]*\n(?=[\t ]*(?:[-*+] |\d+[.)] ))/g, "\n");
}
