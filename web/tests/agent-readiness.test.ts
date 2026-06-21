import test from "node:test";
import { strict as assert } from "node:assert";

import {
  normalizeAgentReadiness,
  readinessLabel,
} from "../src/lib/agent-readiness.js";

test("normalizes ready and degraded readiness payloads", () => {
  const ready = normalizeAgentReadiness({
    status: "degraded",
    phase: "degraded",
    ready: true,
    elapsed_ms: 42,
    degraded_reasons: ["mcp_unavailable", 7],
  });
  assert.equal(ready.ready, true);
  assert.deepEqual(ready.degraded_reasons, ["mcp_unavailable"]);
  assert.match(readinessLabel(ready), /optional/i);
});

test("unknown or contradictory payloads remain not ready", () => {
  const readiness = normalizeAgentReadiness({ status: "mystery", ready: true });
  assert.equal(readiness.status, "starting");
  assert.equal(readiness.ready, false);
  assert.equal(readinessLabel(readiness), "Preparing agent");
});

test("phase labels explain required startup work", () => {
  const readiness = normalizeAgentReadiness({ status: "starting", phase: "skills" });
  assert.equal(readinessLabel(readiness), "Loading skills");
  assert.equal(
    readinessLabel(normalizeAgentReadiness({ status: "failed", phase: "failed" })),
    "Required capabilities failed to load",
  );
});
