export type MarkdownCodeNode = {
    kind: "inline" | "block";
    language: string;
    value: string;
};

export function classifyMarkdownCode(
    className: string | undefined,
    children: unknown,
): MarkdownCodeNode {
    const value = String(children ?? "");
    const languageMatch = /(?:^|\s)language-([\w-]+)/.exec(className || "");
    const isBlock = Boolean(languageMatch) || value.endsWith("\n");

    return {
        kind: isBlock ? "block" : "inline",
        language: languageMatch?.[1] || "text",
        value: isBlock ? value.replace(/\n$/, "") : value,
    };
}
