import assert from "node:assert/strict";
import test from "node:test";

import { classifyRequestError, requestFailureMessage } from "../src/lib/request-error.js";

const http = (status: number, data?: unknown) => ({ isAxiosError: true, message: `Request failed with status code ${status}`, response: { status, data } });

test("a missing response means the backend could not be reached", () => {
    const info = classifyRequestError({ isAxiosError: true, code: "ERR_NETWORK", message: "Network Error" });
    assert.equal(info.category, "network");
    assert.equal(info.retryable, true);
    assert.match(info.hint, /limebot status/);
});

test("401 and 403 point at the access key and are not retryable", () => {
    for (const status of [401, 403]) {
        const info = classifyRequestError(http(status));
        assert.equal(info.category, "unauthorized");
        assert.equal(info.retryable, false);
        assert.equal(info.status, status);
    }
});

test("404 names the subject so the message says what is missing", () => {
    const info = classifyRequestError(http(404), "browser sessions");
    assert.equal(info.category, "not_found");
    assert.match(info.title, /browser sessions/);
});

test("5xx is retryable and keeps the server's own explanation", () => {
    const info = classifyRequestError(http(500, { detail: "database is locked" }), "memory");
    assert.equal(info.category, "server");
    assert.equal(info.retryable, true);
    assert.equal(info.detail, "database is locked");
});

test("other 4xx is a rejection, not worth retrying unchanged", () => {
    const info = classifyRequestError(http(422, { message: "invalid cron expression" }));
    assert.equal(info.category, "rejected");
    assert.equal(info.retryable, false);
    assert.equal(info.detail, "invalid cron expression");
});

test("429 and timeouts are retryable", () => {
    assert.equal(classifyRequestError(http(429)).category, "rate_limit");
    assert.equal(classifyRequestError({ isAxiosError: true, code: "ECONNABORTED", message: "timeout of 5000ms exceeded" }).category, "timeout");
});

test("plain errors and junk values never throw", () => {
    assert.equal(classifyRequestError(new Error("boom")).detail, "boom");
    assert.equal(classifyRequestError(undefined).category, "network");
    assert.equal(classifyRequestError("nope").detail, "No details were returned.");
});

test("requestFailureMessage shows the server reason only when the person can act on it", () => {
    assert.equal(requestFailureMessage("Could not add the job", http(422, { detail: "bad cron" })), "Could not add the job: bad cron");
    assert.match(requestFailureMessage("Could not pause the job", http(503)), /^Could not pause the job: The backend failed/);
});
