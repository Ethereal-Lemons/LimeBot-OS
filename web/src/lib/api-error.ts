import axios from "axios";

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function getApiErrorStatus(error: unknown): number | undefined {
    return axios.isAxiosError<unknown>(error) ? error.response?.status : undefined;
}

export function getApiErrorMessage(error: unknown, fallback: string): string {
    if (axios.isAxiosError<unknown>(error)) {
        const response = error.response?.data;
        if (isRecord(response)) {
            for (const key of ["detail", "message", "error"]) {
                const message = response[key];
                if (typeof message === "string" && message.trim()) return message;
            }
        }
    }
    return error instanceof Error && error.message ? error.message : fallback;
}
