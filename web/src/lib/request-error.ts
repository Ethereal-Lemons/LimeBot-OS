/**
 * Turns a failed dashboard request into something a person can act on.
 * Same idea as `classifyToolError` in tool-display.ts, but for HTTP calls.
 * Pure and structural (no axios import) so it can be unit tested in Node.
 */

export type RequestErrorCategory =
    | "unauthorized"
    | "not_found"
    | "rate_limit"
    | "timeout"
    | "network"
    | "server"
    | "rejected"
    | "unknown";

export interface RequestErrorInfo {
    category: RequestErrorCategory;
    /** One line, plain language. */
    title: string;
    /** What the person can do about it. */
    hint: string;
    /** True when trying again as-is can plausibly work. */
    retryable: boolean;
    /** Raw message from the server or the network layer, for a details panel. */
    detail: string;
    status?: number;
}

interface ErrorLike {
    message?: unknown;
    code?: unknown;
    response?: { status?: unknown; data?: unknown };
}

function asRecord(value: unknown): Record<string, unknown> | null {
    return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function serverMessage(data: unknown): string {
    if (typeof data === "string") return data.trim().slice(0, 400);
    const record = asRecord(data);
    if (!record) return "";
    for (const key of ["detail", "message", "error"]) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) return value.trim().slice(0, 400);
    }
    return "";
}

export function classifyRequestError(error: unknown, subject = "this page"): RequestErrorInfo {
    const like = (asRecord(error) || {}) as ErrorLike;
    const status = typeof like.response?.status === "number" ? like.response.status : undefined;
    const code = typeof like.code === "string" ? like.code : "";
    const message = typeof like.message === "string" ? like.message : error instanceof Error ? error.message : "";
    const detail = serverMessage(like.response?.data) || message || "No details were returned.";

    if (status === 401 || status === 403) {
        return {
            category: "unauthorized",
            title: "LimeBot did not accept the access key",
            hint: "Enter the API key again from the sign-in prompt, or check APP_API_KEY in Configuration.",
            retryable: false,
            detail,
            status,
        };
    }
    if (status === 404) {
        return {
            category: "not_found",
            title: `The backend has no endpoint for ${subject}`,
            hint: "The running backend may be older than this dashboard. Update it and restart LimeBot.",
            retryable: false,
            detail,
            status,
        };
    }
    if (status === 429) {
        return {
            category: "rate_limit",
            title: "Too many requests",
            hint: "Wait a few seconds, then try again.",
            retryable: true,
            detail,
            status,
        };
    }
    if (status !== undefined && status >= 500) {
        return {
            category: "server",
            title: `The backend failed while loading ${subject}`,
            hint: "Its log usually says why. Try again, or restart LimeBot if it keeps happening.",
            retryable: true,
            detail,
            status,
        };
    }
    if (status !== undefined && status >= 400) {
        return {
            category: "rejected",
            title: "The backend rejected the request",
            hint: "Check the values you entered and try again.",
            retryable: false,
            detail,
            status,
        };
    }
    if (code === "ECONNABORTED" || code === "ETIMEDOUT" || /timeout/i.test(message)) {
        return {
            category: "timeout",
            title: "The backend took too long to answer",
            hint: "It may be busy. Try again in a moment.",
            retryable: true,
            detail,
        };
    }
    if (!like.response || code === "ERR_NETWORK" || /network error|failed to fetch/i.test(message)) {
        return {
            category: "network",
            title: "Could not reach the LimeBot backend",
            hint: "Check that LimeBot is running (limebot status), then try again.",
            retryable: true,
            detail,
        };
    }
    return {
        category: "unknown",
        title: "Something went wrong",
        hint: "The details may explain it. Copy them if you need to report it.",
        retryable: true,
        detail,
        status,
    };
}

/** Short form for toasts: what failed plus the server's reason when it gave one. */
export function requestFailureMessage(action: string, error: unknown): string {
    const info = classifyRequestError(error);
    const reason = info.category === "rejected" || info.category === "unknown" ? info.detail : info.title;
    return `${action}: ${reason}`;
}
