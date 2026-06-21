import test from "node:test";
import { strict as assert } from "node:assert";

import {
  clearSetupProgress,
  loadSetupProgress,
  normalizeSetupError,
  saveSetupProgress,
  setupRetryDelay,
  type StorageLike,
} from "../src/lib/setup-state.js";

function makeStorage(): StorageLike {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

test("setup progress survives a reload without storing secrets", () => {
  const storage = makeStorage();
  saveSetupProgress(storage, {
    phase: "restarting",
    restartToken: "restart-1",
    previousBootId: "boot-old",
    model: "openai-codex/gpt-5.4",
    startedAt: 123,
  });

  const restored = loadSetupProgress(storage);
  assert.equal(restored?.restartToken, "restart-1");
  assert.equal(restored?.previousBootId, "boot-old");
  assert.equal(JSON.stringify(restored).includes("API_KEY"), false);
});

test("invalid persisted progress is discarded", () => {
  const storage = makeStorage();
  storage.setItem("limebot_setup_progress_v1", "not-json");
  assert.equal(loadSetupProgress(storage), null);
  assert.equal(storage.getItem("limebot_setup_progress_v1"), null);
});

test("clear removes resumable progress", () => {
  const storage = makeStorage();
  saveSetupProgress(storage, {
    phase: "reconnecting",
    restartToken: "restart-2",
    previousBootId: "boot-old",
    model: "gemini/gemini-2.0-flash",
    startedAt: 456,
  });
  clearSetupProgress(storage);
  assert.equal(loadSetupProgress(storage), null);
});

test("retry delay is bounded exponential backoff", () => {
  assert.equal(setupRetryDelay(0), 500);
  assert.equal(setupRetryDelay(1), 1_000);
  assert.equal(setupRetryDelay(10), 3_000);
});

test("provider errors retain safe server guidance", () => {
  assert.deepEqual(normalizeSetupError({
    code: "invalid_credentials",
    message: "Check the key.",
    retryable: false,
  }), {
    code: "invalid_credentials",
    message: "Check the key.",
    retryable: false,
  });
  assert.match(normalizeSetupError({ code: "quota_exceeded" }).message, /quota/i);
});
