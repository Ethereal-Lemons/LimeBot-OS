import assert from "node:assert/strict";
import test from "node:test";

import { classifyRequestError, requestErrorStatus } from "../src/lib/request-error.js";

const http = (status: number, data?: unknown) => ({ message: `Request failed with status code ${status}`, response: { status, data } });

test("a missing response means the backend is unreachable and worth retrying", () => {
    const info = classifyRequestError({ message: "Network Error", code: "ERR_NETWORK" });
    assert.equal(info.category, "network");
    assert.equal(info.retryable, true);
    assert.match(info.title, /reach/i);
});

test("an aborted request is reported as a timeout", () => {
    const info = classifyRequestError({ message: "timeout of 5000ms exceeded", code: "ECONNABORTED" });
    assert.equal(info.category, "timeout");
    assert.equal(info.retryable, true);
});

test("401 and 403 are credential problems that a retry will not fix", () => {
    for (const status of [401, 403]) {
        const info = classifyRequestError(http(status, { detail: "Invalid API key" }));
        assert.equal(info.category, "authentication");
        assert.equal(info.retryable, false);
        assert.match(info.detail, new RegExp(`HTTP ${status}: Invalid API key`));
    }
});

test("429 and 5xx are retryable and keep the server's explanation", () => {
    assert.equal(classifyRequestError(http(429)).category, "rate_limit");
    const server = classifyRequestError(http(503, { message: "warming up" }));
    assert.equal(server.retryable, true);
    assert.match(server.detail, /warming up/);
});

test("404 names the missing data and is not retryable", () => {
    const info = classifyRequestError(http(404), "the task queue");
    assert.equal(info.category, "not_found");
    assert.match(info.title, /task queue/);
    assert.equal(info.retryable, false);
});

test("unknown shapes still produce a usable message", () => {
    const info = classifyRequestError("boom");
    assert.equal(info.category, "network");
    assert.equal(info.detail, "boom");
    assert.equal(requestErrorStatus(null), undefined);
    assert.equal(requestErrorStatus(http(500)), 500);
});
