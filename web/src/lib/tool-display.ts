/** Pure helpers that turn raw tool events into something a person can scan. */

export type ToolIconKey =
    | "file"
    | "write"
    | "terminal"
    | "folder"
    | "search"
    | "web"
    | "memory"
    | "agent"
    | "image"
    | "message"
    | "calendar"
    | "generic";

export interface ToolDescription {
    /** Short verb phrase, for example "Read file". */
    label: string;
    /** The thing acted on: a path, command, query, URL. May be empty. */
    target: string;
    icon: ToolIconKey;
}

const asText = (value: unknown): string => (typeof value === "string" ? value : "");

export function baseName(path: string): string {
    const clean = path.replace(/[\\/]+$/, "");
    return clean.split(/[\\/]/).pop() || clean;
}

function clip(text: string, max: number): string {
    const flat = text.replace(/\s+/g, " ").trim();
    return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

export function describeTool(tool: string, args: Record<string, unknown> | undefined): ToolDescription {
    const a = args || {};
    switch (tool) {
        case "read_file":
            return { label: "Read file", target: baseName(asText(a.path)), icon: "file" };
        case "write_file":
            return { label: "Write file", target: baseName(asText(a.path)), icon: "write" };
        case "edit_file":
            return { label: "Edit file", target: baseName(asText(a.path)), icon: "write" };
        case "delete_file":
            return { label: "Delete", target: baseName(asText(a.path)), icon: "write" };
        case "verify_files":
        case "diagnose_files": {
            const paths = Array.isArray(a.paths) ? a.paths.map(String) : [];
            return {
                label: tool === "verify_files" ? "Verify files" : "Diagnose files",
                target: paths.length > 1 ? `${baseName(paths[0])} +${paths.length - 1}` : baseName(paths[0] || ""),
                icon: "file",
            };
        }
        case "list_dir":
            return { label: "List folder", target: asText(a.path) || ".", icon: "folder" };
        case "search_files":
            return { label: "Search files", target: clip(asText(a.query), 60), icon: "search" };
        case "run_command":
            return { label: "Run command", target: clip(asText(a.command), 90), icon: "terminal" };
        case "run_steps": {
            const steps = Array.isArray(a.commands) ? a.commands.length : 0;
            return { label: "Run steps", target: steps ? `${steps} commands` : "", icon: "terminal" };
        }
        case "web_search":
        case "search_web":
            return { label: "Search the web", target: clip(asText(a.query), 80), icon: "web" };
        case "browser_navigate":
            return { label: "Open page", target: clip(asText(a.url), 80), icon: "web" };
        case "browser_act":
            return { label: "Use page", target: clip(asText(a.action), 40), icon: "web" };
        case "browser_extract":
            return { label: "Read page", target: clip(asText(a.mode) || asText(a.selector), 40), icon: "web" };
        case "memory_search":
            return { label: "Search memory", target: clip(asText(a.query), 70), icon: "memory" };
        case "memory_save":
            return { label: "Save to memory", target: clip(asText(a.content), 70), icon: "memory" };
        case "spawn_agent":
            return { label: "Delegate to agent", target: clip(asText(a.task), 80), icon: "agent" };
        case "generate_image":
            return { label: "Generate image", target: clip(asText(a.prompt), 70), icon: "image" };
        case "send_media":
        case "send_voice":
        case "send_discord_message":
        case "send_discord_embed":
            return { label: tool.replace(/^send_/, "Send ").replace(/_/g, " "), target: clip(asText(a.caption) || asText(a.message) || asText(a.text), 60), icon: "message" };
        case "cron_add":
        case "cron_list":
        case "cron_remove":
            return { label: tool === "cron_add" ? "Schedule job" : tool === "cron_list" ? "List jobs" : "Cancel job", target: clip(asText(a.message), 60), icon: "calendar" };
        case "calculate":
            return { label: "Calculate", target: clip(asText(a.expression), 60), icon: "generic" };
        default: {
            const readable = tool.replace(/^mcp_[^_]+_/, "").replace(/_/g, " ");
            return {
                label: readable.charAt(0).toUpperCase() + readable.slice(1),
                target: clip(JSON.stringify(a), 70) === "{}" ? "" : clip(JSON.stringify(a), 70),
                icon: "generic",
            };
        }
    }
}

// ── errors ──────────────────────────────────────────────────────────────────

export type ToolErrorCategory =
    | "policy"
    | "denied"
    | "authentication"
    | "rate_limit"
    | "timeout"
    | "empty"
    | "not_found"
    | "invalid_arguments"
    | "dependency"
    | "network"
    | "command_failed"
    | "unknown";

export interface ToolErrorInfo {
    category: ToolErrorCategory;
    /** One line, plain language. */
    title: string;
    /** What the person can do about it. */
    hint: string;
    /** True when trying again as-is can plausibly work. */
    retryable: boolean;
    /** The raw message without the leading "Error:" noise. */
    detail: string;
}

const RULES: Array<{ category: ToolErrorCategory; test: RegExp; title: string; hint: string; retryable: boolean }> = [
    {
        category: "denied",
        test: /denied by (the )?user|user (denied|rejected)|approval (was )?(denied|rejected|timed out)|not approved|cancelled by user/i,
        title: "You declined this action",
        hint: "Nothing was changed. Ask again if you want it to try a different approach.",
        retryable: false,
    },
    {
        category: "policy",
        test: /disabled in|not allowed|outside (of )?(the )?allowed|is blocked|blocked by|policy|only (python|read-only)|limited to the|hard-blocked|protected/i,
        title: "Blocked by a safety rule",
        hint: "This tool or path is not available here. A different tool or a text channel may allow it.",
        retryable: false,
    },
    {
        category: "authentication",
        test: /\b(401|403)\b|unauthori[sz]ed|forbidden|invalid api key|authentication|missing the permission|incorrect api key/i,
        title: "The service rejected the credentials",
        hint: "Check the API key and its permissions in Configuration.",
        retryable: false,
    },
    {
        category: "rate_limit",
        test: /\b429\b|rate.?limit|quota|usage limit|subscription_required|too many requests/i,
        title: "Rate limit or plan limit reached",
        hint: "Wait a moment, or check the plan and quota for this provider.",
        retryable: true,
    },
    {
        category: "timeout",
        test: /timed? ?out|timeout|deadline exceeded/i,
        title: "It took too long and was stopped",
        hint: "Try again, or ask for a smaller step.",
        retryable: true,
    },
    {
        category: "dependency",
        test: /SKILL_DEPS_MISSING|dependenc(y|ies) (is|are) missing|ModuleNotFoundError|command not found|is not installed|not recognized as an internal/i,
        title: "A required dependency is missing",
        hint: "Install the missing package or enable the optional feature, then retry.",
        retryable: false,
    },
    {
        category: "empty",
        test: /no usable results|no (organic )?results|returned no (results|matches)|nothing (found|matched)|0 matches/i,
        title: "The search came back empty",
        hint: "LimeBot will answer from what it knows. Plain wording without quotes or site: filters usually works better.",
        retryable: true,
    },
    {
        category: "not_found",
        test: /no such file|not found|does not exist|ENOENT|cannot find/i,
        title: "Could not find it",
        hint: "Check the path or name. Listing the folder first usually shows the right one.",
        retryable: false,
    },
    {
        category: "invalid_arguments",
        test: /invalid (argument|parameter|arguments)|missing required|unexpected keyword|expected .* got|must be (a|an|one of)|schema|validation/i,
        title: "The tool was called with the wrong arguments",
        hint: "LimeBot usually corrects this on its own; no action needed unless it keeps failing.",
        retryable: true,
    },
    {
        category: "network",
        test: /connection (refused|reset|closed|error)|network|ECONN|ENOTFOUND|dns|unreachable|temporarily unavailable|\b50[234]\b/i,
        title: "Could not reach the service",
        hint: "Check the connection or try again in a moment.",
        retryable: true,
    },
    {
        category: "command_failed",
        test: /exit code|non-?zero|command failed|traceback|failed with|returned [1-9]/i,
        title: "The command failed",
        hint: "The output below shows why. LimeBot can read it and try a fix.",
        retryable: false,
    },
];

export function stripErrorPrefix(result: string): string {
    return result.replace(/^\s*(error|failed|tool failed)\s*[:-]\s*/i, "").trim();
}

export function isErrorResult(result: string | undefined): boolean {
    if (!result) return false;
    return /^\s*(error|failed)\b\s*[:-]/i.test(result);
}

export function classifyToolError(result: string | undefined): ToolErrorInfo {
    const detail = stripErrorPrefix(result || "");
    for (const rule of RULES) {
        if (rule.test.test(detail)) {
            return { category: rule.category, title: rule.title, hint: rule.hint, retryable: rule.retryable, detail };
        }
    }
    return {
        category: "unknown",
        title: "Something went wrong",
        hint: "The details below may explain it. Copy them if you need to report it.",
        retryable: false,
        detail,
    };
}

/** Skill dependency notices are appended to results behind a marker. */
export function splitDependencyNotice(result: string | undefined): { notice: string; cleaned: string } {
    const marker = "[SKILL_DEPS_MISSING]";
    if (!result || !result.includes(marker)) return { notice: "", cleaned: result || "" };
    const [before, after] = result.split(marker, 2);
    return { notice: (after || "").trim(), cleaned: (before || "").trim() };
}

/** Compact JSON for an arguments panel: long strings are clipped, not dumped. */
export function formatArgs(args: Record<string, unknown> | undefined, maxString = 400): string {
    const clipValue = (value: unknown): unknown => {
        if (typeof value === "string") return value.length > maxString ? `${value.slice(0, maxString)}… (${value.length} chars)` : value;
        if (Array.isArray(value)) return value.slice(0, 20).map(clipValue);
        if (value && typeof value === "object") {
            return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, clipValue(v)]));
        }
        return value;
    };
    return JSON.stringify(clipValue(args || {}), null, 2);
}
