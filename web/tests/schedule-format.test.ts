import assert from "node:assert/strict";
import test from "node:test";

import { describeCron, formatCountdown, formatTzOffset } from "../src/lib/schedule-format.js";

test("countdown drops seconds once a job is more than a day away", () => {
    assert.equal(formatCountdown(45), "45s");
    assert.equal(formatCountdown(3 * 3600 + 5 * 60 + 12), "3h 5m 12s");
    assert.equal(formatCountdown(2 * 86400 + 4 * 3600 + 3 * 60 + 12), "2d 4h 3m");
    assert.equal(formatCountdown(-3), "Running now");
});

test("timezone offsets keep half-hour zones readable", () => {
    assert.equal(formatTzOffset(0), "UTC+0");
    assert.equal(formatTzOffset(330), "UTC+5:30");
    assert.equal(formatTzOffset(-360), "UTC-6");
});

test("common cron shapes read as sentences and the rest stays raw", () => {
    assert.equal(describeCron("0 9 * * 1-5"), "Weekdays at 09:00");
    assert.equal(describeCron("30 18 * * *"), "Every day at 18:30");
    assert.equal(describeCron("*/15 * * * *"), "Every 15 minutes");
    assert.equal(describeCron("0 9 1 * *"), "0 9 1 * *");
    assert.equal(describeCron("not cron"), "not cron");
});
