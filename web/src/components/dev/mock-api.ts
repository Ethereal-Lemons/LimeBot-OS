import axios, { AxiosError, type AxiosAdapter, type AxiosResponse, type InternalAxiosRequestConfig } from "axios";
import { api } from "@/lib/api";

/**
 * Fake backend for reviewing pages without LimeBot running.
 * Open the app with `?ui=mock`. Flags (all optional, combine freely):
 *
 *   &fail=network|401|403|404|409|422|500   every non-GET request fails that way
 *   &failload=network|500                  every GET fails that way (shows load failure states)
 *   &empty=1                               handlers should return empty lists
 *   &latency=800                           delay each response by this many ms (default 120)
 *   &view=config                           open this dashboard view first
 *
 * Handlers live in ./mock/*.ts and register themselves with `mock()`.
 */

export interface MockRequest {
    method: string;
    path: string;
    params: Record<string, unknown>;
    data: unknown;
    match: RegExpMatchArray;
}

export type MockHandler = (request: MockRequest) => unknown | Promise<unknown>;

interface Route {
    method: string;
    pattern: RegExp;
    handler: MockHandler;
}

const routes: Route[] = [];

export function mock(method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE", pattern: RegExp, handler: MockHandler) {
    routes.unshift({ method, pattern, handler });
}

const query = () => new URLSearchParams(window.location.search);
export const mockFlags = {
    get empty() {
        return query().get("empty") === "1";
    },
    get fail() {
        return query().get("fail");
    },
    get failLoad() {
        return query().get("failload");
    },
    get latency() {
        const value = Number(query().get("latency"));
        return Number.isFinite(value) && query().has("latency") ? value : 120;
    },
};

/** A handler can return this to answer with a specific status. */
export class MockHttpError extends Error {
    constructor(public status: number, public body: unknown) {
        super(typeof body === "string" ? body : `Mock error ${status}`);
    }
}

function failure(config: InternalAxiosRequestConfig, how: string): AxiosError {
    if (how === "network") return new AxiosError("Network Error", "ERR_NETWORK", config);
    const status = Number(how) || 500;
    const bodies: Record<number, unknown> = {
        401: { detail: "Invalid or missing API key" },
        403: { detail: "Forbidden" },
        404: { detail: "Not found" },
        409: { detail: "This was changed elsewhere" },
        422: { detail: [{ msg: "A value is out of range" }] },
        500: { detail: "Internal error while writing the configuration file" },
    };
    return responseError(config, status, bodies[status] ?? { detail: `Mock error ${status}` });
}

function responseError(config: InternalAxiosRequestConfig, status: number, data: unknown): AxiosError {
    const response = { data, status, statusText: String(status), headers: {}, config } as AxiosResponse;
    return new AxiosError(`Request failed with status code ${status}`, "ERR_BAD_REQUEST", config, {}, response);
}

const adapter: AxiosAdapter = async (config) => {
    const method = (config.method || "get").toUpperCase();
    const url = new URL(config.url || "/", window.location.origin);
    const path = url.pathname;
    await new Promise((resolve) => setTimeout(resolve, mockFlags.latency));

    // Only the pages under review are failed on purpose; the app shell keeps working.
    const shell = /^\/api\/(setup\/status|identity|ready)$/.test(path);
    if (!shell) {
        if (method !== "GET" && mockFlags.fail) throw failure(config, mockFlags.fail);
        if (method === "GET" && mockFlags.failLoad) throw failure(config, mockFlags.failLoad);
    }

    let data: unknown = config.data;
    if (typeof data === "string") {
        try {
            data = JSON.parse(data);
        } catch {
            /* leave as text */
        }
    }
    const params = { ...(config.params as Record<string, unknown> | undefined), ...Object.fromEntries(url.searchParams) };

    for (const route of routes) {
        if (route.method !== method) continue;
        const match = path.match(route.pattern);
        if (!match) continue;
        try {
            const body = await route.handler({ method, path, params, data, match });
            const response = { data: body ?? {}, status: 200, statusText: "OK", headers: {}, config } as AxiosResponse;
            return response;
        } catch (error) {
            if (error instanceof MockHttpError) throw responseError(config, error.status, error.body);
            throw error;
        }
    }
    console.warn(`[mock-api] no handler for ${method} ${path}`);
    throw responseError(config, 404, { detail: `No mock for ${method} ${path}` });
};

export async function installMockApi() {
    axios.defaults.adapter = adapter;
    api.defaults.adapter = adapter;
    await import("./mock/index");
}

