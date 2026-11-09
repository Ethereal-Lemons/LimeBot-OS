import assert from "node:assert/strict";
import test from "node:test";

import { needsApproval, rowState, summarizeTurn } from "../src/lib/tool-state.js";

const exec = (over: { status?: string; result?: string }) => ({
    status: "completed",
    ...over,
});

test("rowState maps backend statuses to what the row shows", () => {
    assert.equal(rowState(exec({ status: "running" }), null), "running");
    assert.equal(rowState(exec({ status: "planned" }), null), "planned");
    assert.equal(rowState(exec({ status: "completed" }), null), "done");
    assert.equal(rowState(exec({ status: "error" }), null), "failed");
    assert.equal(rowState(exec({ status: "waiting_confirmation" }), null), "waiting");
    assert.equal(rowState(exec({ status: "waiting_confirmation" }), "approved"), "approved");
    assert.equal(rowState(exec({ status: "waiting_confirmation" }), "denied"), "denied");
});

test("the old blocked-result marker still counts as needing approval", () => {
    assert.equal(needsApproval(exec({ result: "ACTION BLOCKED: Confirmation Required" })), true);
    assert.equal(needsApproval(exec({ result: "ok" })), false);
});

test("a recovered failure does not paint a completed turn as failed", () => {
    const calls = [exec({ status: "error" }), exec({ status: "completed" })];
    assert.equal(summarizeTurn(calls, "completed").failed, 0);
    assert.equal(summarizeTurn(calls, "completed").recovered, true);
    assert.equal(summarizeTurn(calls, "failed").failed, 1);
    assert.equal(summarizeTurn(calls).failed, 1);
});

test("waiting and running calls are counted separately", () => {
    const summary = summarizeTurn([exec({ status: "waiting_confirmation" }), exec({ status: "running" }), exec({})]);
    assert.deepEqual(
        { waiting: summary.waiting, running: summary.running, done: summary.done, total: summary.total },
        { waiting: 1, running: 1, done: 1, total: 3 }
    );
});
