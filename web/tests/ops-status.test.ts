import assert from "node:assert/strict";
import test from "node:test";

import { formatAgo, formatCount, formatDuration, statusLabel, toRowState } from "../src/lib/ops-status.js";

test("statuses from tasks, deliveries and channels share the tool-row states", () => {
    assert.equal(toRowState("running"), "running");
    assert.equal(toRowState("retrying"), "running");
    assert.equal(toRowState("sending"), "running");
    assert.equal(toRowState("waiting"), "waiting");
    assert.equal(toRowState("completed"), "done");
    assert.equal(toRowState("sent"), "done");
    assert.equal(toRowState("Connected"), "done");
    assert.equal(toRowState("failed"), "failed");
    assert.equal(toRowState("queued"), "planned");
    assert.equal(toRowState("cancelled"), "planned");
    assert.equal(toRowState(undefined), "planned");
});

test("statusLabel reads as a word", () => {
    assert.equal(statusLabel("quota_exceeded"), "Quota exceeded");
    assert.equal(statusLabel(undefined), "Unknown");
});

test("formatAgo is relative and tolerant of missing times", () => {
    const now = 1_000_000_000_000;
    const at = now / 1000;
    assert.equal(formatAgo(at - 2, now), "just now");
    assert.equal(formatAgo(at - 42, now), "42s ago");
    assert.equal(formatAgo(at - 5 * 60, now), "5m ago");
    assert.equal(formatAgo(at - 3 * 3600, now), "3h ago");
    assert.equal(formatAgo(at - 3 * 86400, now), "3d ago");
    assert.equal(formatAgo(0, now), "unknown");
    assert.equal(formatAgo(at + 100, now), "just now");
});

test("formatDuration and formatCount stay compact", () => {
    assert.equal(formatDuration(0.2), "<1s");
    assert.equal(formatDuration(4.26), "4.3s");
    assert.equal(formatDuration(75), "1m 15s");
    assert.equal(formatDuration(3720), "1h 2m");
    assert.equal(formatDuration(-1), "");
    assert.equal(formatCount(950), "950");
    assert.equal(formatCount(1250), "1.3k");
    assert.equal(formatCount(48000), "48k");
    assert.equal(formatCount(2_500_000), "2.5M");
});
