import type { CapturedPageContext } from "@/lib/browser";
import type { PendingAction } from "@/lib/storage";

export const MAX_SELECTION_CHARS = 8000;
export const MAX_VISIBLE_TEXT_CHARS = 12000;

function clipLine(value: string, limit: number) {
  const text = value.trim();
  if (text.length <= limit) return text;
  return `${text.slice(0, limit).trim()}...`;
}

function safeTitle(title: string) {
  return title.trim() || "Untitled page";
}

export function buildAskPagePrompt(context: CapturedPageContext) {
  const excerpt = context.visibleText.trim() || "No visible page text was available.";

  return `I am browsing this page and want help understanding it.

URL: ${context.url}
Title: ${safeTitle(context.title)}

Visible page excerpt:
${excerpt}

Please summarize the useful parts and suggest what I might do next.`;
}

export function buildSelectedTextPrompt(context: CapturedPageContext) {
  const selection = context.selectedText.trim();
  if (!selection) {
    return null;
  }

  return `I selected this text on a page and want your help with it.

URL: ${context.url}
Title: ${safeTitle(context.title)}

Selected text:
${selection}`;
}

export function createPromptAction(
  source: PendingAction["source"],
  context: CapturedPageContext
): PendingAction {
  return {
    id: crypto.randomUUID(),
    kind: "prompt",
    source,
    prompt: buildAskPagePrompt(context),
    displayText: `Ask about "${clipLine(safeTitle(context.title), 72)}"`,
    createdAt: Date.now(),
  };
}

export function createSelectedTextAction(
  source: PendingAction["source"],
  context: CapturedPageContext
): PendingAction {
  const prompt = buildSelectedTextPrompt(context);
  if (!prompt) {
    return {
      id: crypto.randomUUID(),
      kind: "notice",
      source,
      level: "warning",
      message: "Select some text on the page first, then try sending it again.",
      createdAt: Date.now(),
    };
  }

  return {
    id: crypto.randomUUID(),
    kind: "prompt",
    source,
    prompt,
    displayText: `Send selection from "${clipLine(safeTitle(context.title), 64)}"`,
    createdAt: Date.now(),
  };
}

export function createNoticeAction(
  source: PendingAction["source"],
  message: string,
  level: "info" | "warning" = "info"
): PendingAction {
  return {
    id: crypto.randomUUID(),
    kind: "notice",
    source,
    message,
    level,
    createdAt: Date.now(),
  };
}
