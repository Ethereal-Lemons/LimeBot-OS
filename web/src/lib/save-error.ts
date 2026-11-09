import axios from "axios";
import { classifyToolError } from "./tool-display.js";

/**
 * Turns a failed save/load request into something a person can act on.
 * It follows the same idea as `classifyToolError`: a category, a one-line
 * title, a hint about what to do, and the raw detail kept for copying.
 */

export type SaveErrorCategory =
    | "network"
    | "timeout"
    | "authentication"
    | "rate_limit"
    | "invalid"
    | "not_found"
    | "conflict"
    | "server"
    | "unknown";

export interface SaveErrorInfo {
    category: SaveErrorCategory;
    title: string;
    hint: string;
    retryable: boolean;
    /** Server-provided message when there is one, otherwise the transport message. */
    detail: string;
    status?: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Pulls a readable message out of FastAPI style `detail` (string or validation list). */
export function extractErrorDetail(data: unknown): string {
    if (typeof data === "string") return data.trim();
    if (!isRecord(data)) return "";
    for (const key of ["detail", "message", "error"]) {
        const value = data[key];
        if (typeof value === "string" && value.trim()) return value.trim();
        if (Array.isArray(value)) {
            const parts = value
                .map((item) => (isRecord(item) && typeof item.msg === "string" ? item.msg : typeof item === "string" ? item : ""))
                .filter(Boolean);
            if (parts.length) return parts.join("; ");
        }
    }
    return "";
}

export function classifySaveError(error: unknown, subject = "your changes"): SaveErrorInfo {
    if (axios.isAxiosError<unknown>(error)) {
        const status = error.response?.status;
        const serverDetail = extractErrorDetail(error.response?.data);

        if (!error.response) {
            if (error.code === "ECONNABORTED" || /timeout/i.test(error.message)) {
                return {
                    category: "timeout",
                    title: "The server took too long to answer",
                    hint: `${capitalize(subject)} may or may not have been saved. Reload the page to check before trying again.`,
                    retryable: true,
                    detail: error.message,
                };
            }
            return {
                category: "network",
                title: "Could not reach LimeBot",
                hint: `Check that the backend is running. ${capitalize(subject)} are still here and nothing was lost.`,
                retryable: true,
                detail: error.message,
            };
        }

        const detail = serverDetail || error.message;
        if (status === 401 || status === 403) {
            return {
                category: "authentication",
                title: "The dashboard access key was rejected",
                hint: "Sign in again with the current access key, then save once more.",
                retryable: false,
                detail,
                status,
            };
        }
        if (status === 404) {
            return {
                category: "not_found",
                title: "That item no longer exists",
                hint: "It may have been removed elsewhere. Reload to see the current list.",
                retryable: false,
                detail,
                status,
            };
        }
        if (status === 409) {
            return {
                category: "conflict",
                title: "Someone else changed this first",
                hint: "Reload to get the latest version, then re-apply your edit.",
                retryable: false,
                detail,
                status,
            };
        }
        if (status === 429) {
            return {
                category: "rate_limit",
                title: "Too many requests",
                hint: "Wait a moment and save again.",
                retryable: true,
                detail,
                status,
            };
        }
        if (status === 400 || status === 413 || status === 422) {
            // The backend explains what is wrong; let the provider-style classifier
            // sharpen vague messages such as a rejected provider key.
            const tool = classifyToolError(detail);
            if (tool.category === "authentication") {
                return {
                    category: "authentication",
                    title: "A provider rejected the API key",
                    hint: "Re-enter the key from the provider dashboard and check it has permission for the selected model.",
                    retryable: false,
                    detail,
                    status,
                };
            }
            return {
                category: "invalid",
                title: serverDetail ? "A value was not accepted" : "The server rejected these values",
                hint: serverDetail
                    ? "Fix the highlighted value and save again."
                    : "Check the fields for typos or out-of-range numbers.",
                retryable: false,
                detail,
                status,
            };
        }
        if (status !== undefined && status >= 500) {
            const tool = classifyToolError(detail);
            if (tool.category === "authentication") {
                return {
                    category: "authentication",
                    title: "A provider rejected the API key",
                    hint: "Re-enter the key from the provider dashboard and check it has permission for the selected model.",
                    retryable: false,
                    detail,
                    status,
                };
            }
            return {
                category: "server",
                title: "LimeBot hit an error while saving",
                hint: "The details below may explain it. Check the System Logs page if it keeps happening.",
                retryable: true,
                detail,
                status,
            };
        }
        return { category: "unknown", title: "Could not save", hint: "The details below may explain it.", retryable: false, detail, status };
    }

    const message = error instanceof Error && error.message ? error.message : typeof error === "string" ? error : "";
    const tool = classifyToolError(message);
    if (tool.category === "authentication") {
        return {
            category: "authentication",
            title: "A provider rejected the API key",
            hint: "Re-enter the key from the provider dashboard and try again.",
            retryable: false,
            detail: message,
        };
    }
    if (tool.category === "network") {
        return { category: "network", title: "Could not reach LimeBot", hint: tool.hint, retryable: true, detail: message };
    }
    return {
        category: "unknown",
        title: "Could not save",
        hint: "The details below may explain it.",
        retryable: false,
        detail: message,
    };
}

function capitalize(text: string): string {
    return text.charAt(0).toUpperCase() + text.slice(1);
}

/** One line for toasts: "<title>. <detail>" when the server said something specific. */
export function saveErrorToastText(info: SaveErrorInfo): string {
    if (info.detail && info.category !== "network" && info.category !== "timeout" && info.detail !== info.title) {
        return `${info.title}: ${info.detail}`;
    }
    return info.title;
}
