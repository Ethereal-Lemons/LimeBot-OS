import type { RowState } from "./tool-state.js";

/**
 * Maps the status words used by tasks, deliveries and sessions onto the same
 * states the chat tool rows use, so every page shows one set of glyphs.
 */
export function toRowState(status: string | undefined): RowState {
    switch ((status || "").toLowerCase()) {
        case "running":
        case "sending":
        case "retrying":
        case "active":
            return "running";
        case "waiting":
            return "waiting";
        case "completed":
        case "sent":
        case "connected":
        case "healthy":
            return "done";
        case "failed":
        case "error":
            return "failed";
        default:
            // queued, cancelled, unknown: quiet, not alarming
            return "planned";
    }
}

export function statusLabel(status: string | undefined): string {
    const text = (status || "unknown").replace(/_/g, " ");
    return text.charAt(0).toUpperCase() + text.slice(1);
}

/** "just now", "42s ago", "5m ago", "3h ago", "2d ago". `ts` is unix seconds. */
export function formatAgo(ts: number | undefined, nowMs: number = Date.now()): string {
    if (!ts) return "unknown";
    const seconds = Math.max(0, Math.round(nowMs / 1000 - ts));
    if (seconds < 5) return "just now";
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
}

export function formatDuration(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds < 0) return "";
    if (seconds < 1) return "<1s";
    if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
    return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function formatCount(value: number): string {
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
    if (value >= 10_000) return `${Math.round(value / 1000)}k`;
    if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
    return String(value);
}

export type Tone = "ready" | "warning" | "error" | "neutral";

/** Text colour for a tone. Red and amber are fixed; the rest follows the theme. */
export function toneText(tone: Tone): string {
    switch (tone) {
        case "ready":
            return "text-primary";
        case "warning":
            return "text-amber-500";
        case "error":
            return "text-red-500";
        default:
            return "text-muted-foreground";
    }
}
