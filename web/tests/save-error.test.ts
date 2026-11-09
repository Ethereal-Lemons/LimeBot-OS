import assert from "node:assert/strict";
import test from "node:test";
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from "axios";

import { classifySaveError, extractErrorDetail, saveErrorToastText } from "../src/lib/save-error.js";

function httpError(status: number, data: unknown): AxiosError {
    const config = { headers: {} } as InternalAxiosRequestConfig;
    const response = { status, data, statusText: "", headers: {}, config } as AxiosResponse;
    return new AxiosError(`Request failed with status code ${status}`, "ERR_BAD_REQUEST", config, {}, response);
}

test("a request that never got a response is a network failure, and says nothing was lost", () => {
    const error = new AxiosError("Network Error", "ERR_NETWORK");
    const info = classifySaveError(error, "your settings");
    assert.equal(info.category, "network");
    assert.equal(info.retryable, true);
    assert.match(info.hint, /Your settings are still here/);
});

test("a timeout warns that the save may have gone through", () => {
    const info = classifySaveError(new AxiosError("timeout of 5000ms exceeded", "ECONNABORTED"));
    assert.equal(info.category, "timeout");
    assert.match(info.hint, /may or may not/);
});

test("401 and 403 point at the dashboard access key", () => {
    for (const status of [401, 403]) {
        const info = classifySaveError(httpError(status, { detail: "Invalid API key" }));
        assert.equal(info.category, "authentication");
        assert.match(info.title, /access key/);
        assert.equal(info.status, status);
    }
});

test("a validation error shows what the server said", () => {
    const info = classifySaveError(httpError(422, { detail: [{ msg: "WEB_PORT must be between 1 and 65535" }] }));
    assert.equal(info.category, "invalid");
    assert.equal(info.detail, "WEB_PORT must be between 1 and 65535");
    assert.match(saveErrorToastText(info), /WEB_PORT must be between/);
});

test("a rejected provider key is told apart from a rejected dashboard key", () => {
    const info = classifySaveError(httpError(400, { detail: "OpenAI returned 401: incorrect api key provided" }));
    assert.equal(info.category, "authentication");
    assert.match(info.title, /provider/i);
});

test("other statuses map to conflict, missing, rate limit and server errors", () => {
    assert.equal(classifySaveError(httpError(404, {})).category, "not_found");
    assert.equal(classifySaveError(httpError(409, {})).category, "conflict");
    assert.equal(classifySaveError(httpError(429, {})).category, "rate_limit");
    const server = classifySaveError(httpError(500, { detail: "disk full" }));
    assert.equal(server.category, "server");
    assert.equal(server.retryable, true);
});

test("plain errors and strings still classify", () => {
    assert.equal(classifySaveError(new Error("connection refused")).category, "network");
    assert.equal(classifySaveError("something odd").category, "unknown");
});

test("extractErrorDetail understands strings, detail, message and lists", () => {
    assert.equal(extractErrorDetail("  boom "), "boom");
    assert.equal(extractErrorDetail({ message: "nope" }), "nope");
    assert.equal(extractErrorDetail({ detail: ["a", "b"] }), "a; b");
    assert.equal(extractErrorDetail(null), "");
});
