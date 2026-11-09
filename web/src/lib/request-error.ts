import { classifyToolError, type ToolErrorInfo } from "./tool-display.js";

/**
 * Turns a failed dashboard request into the same plain-language shape the tool
 * rows use (`ToolErrorInfo`), so every page can say what happened, what to do,
 * and whether a retry is worth offering. Works on axios errors by shape, so it
 * has no runtime dependency on axios.
 */

interface ErrorShape {
    message?: unknown;
    code?: unknown;
    response?: { status?: unknown; data?: unknown };
}

function asShape(error: unknown): ErrorShape {
    return typeof error === "object" && error !== null ? (error as ErrorShape) : {};
}

function serverDetail(data: unknown): string {
    if (typeof data === "string") return data.trim();
    if (typeof data === "object" && data !== null && !Array.isArray(data)) {
        const record = data as Record<string, unknown>;
        for (const key of ["detail", "message", "error"]) {
            const value = record[key];
            if (typeof value === "string" && value.trim()) return value.trim();
        }
    }
    return "";
}

export function requestErrorStatus(error: unknown): number | undefined {
    const status = asShape(error).response?.status;
    return typeof status === "number" ? status : undefined;
}

export function classifyRequestError(error: unknown, what = "this page"): ToolErrorInfo {
    const shape = asShape(error);
    const status = requestErrorStatus(error);
    const message = typeof shape.message === "string" ? shape.message : typeof error === "string" ? error : "";
    const detail = serverDetail(shape.response?.data) || message || "No details were returned.";
    const withDetail = (info: Omit<ToolErrorInfo, "detail">): ToolErrorInfo => ({
        ...info,
        detail: status ? `HTTP ${status}: ${detail}` : detail,
    });

    if (status === 401 || status === 403) {
        return withDetail({
            category: "authentication",
            title: status === 401 ? "The dashboard is not signed in" : "This request is not allowed",
            hint: "The API key is missing or wrong. Enter it again from the key prompt, or check APP_API_KEY in Configuration.",
            retryable: false,
        });
    }
    if (status === 404) {
        return withDetail({
            category: "not_found",
            title: `The backend has no data for ${what}`,
            hint: "The backend may be an older version than this dashboard. Update LimeBot and reload.",
            retryable: false,
        });
    }
    if (status === 429) {
        return withDetail({
            category: "rate_limit",
            title: "Too many requests",
            hint: "Wait a few seconds, then retry.",
            retryable: true,
        });
    }
    if (status !== undefined && status >= 500) {
        return withDetail({
            category: "unknown",
            title: "The backend hit an error",
            hint: "It may still be starting or restarting. Retry in a moment; the Logs page shows the cause.",
            retryable: true,
        });
    }
    if (!shape.response) {
        if (shape.code === "ECONNABORTED" || /timeout/i.test(message)) {
            return withDetail({
                category: "timeout",
                title: "The backend took too long to answer",
                hint: "It may be busy. Retry in a moment.",
                retryable: true,
            });
        }
        return withDetail({
            category: "network",
            title: "Can't reach the LimeBot backend",
            hint: "Check that LimeBot is running, then retry. This page keeps the last data it loaded.",
            retryable: true,
        });
    }
    const classified = classifyToolError(detail);
    return { ...classified, detail };
}
