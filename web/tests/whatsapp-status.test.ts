import assert from "node:assert/strict";
import test from "node:test";

import { describeWhatsAppStatus } from "../src/lib/whatsapp-status.js";

test("a visible QR code means the bridge is waiting for a scan", () => {
    assert.deepEqual(describeWhatsAppStatus("scanning", true, false), { state: "waiting", label: "Waiting for scan" });
});

test("scanning without a QR yet is still connecting", () => {
    assert.deepEqual(describeWhatsAppStatus("scanning", false, false), { state: "running", label: "Connecting" });
    assert.equal(describeWhatsAppStatus("connecting", false, false).state, "running");
});

test("connected wins over a stale QR, unreachable wins over everything", () => {
    assert.equal(describeWhatsAppStatus("connected", true, false).label, "Linked");
    assert.deepEqual(describeWhatsAppStatus("connected", false, true), { state: "failed", label: "Bridge unreachable" });
});

test("idle states are quiet", () => {
    assert.equal(describeWhatsAppStatus("disabled", false, false).label, "Disabled");
    assert.equal(describeWhatsAppStatus("disconnected", false, false).label, "Not linked");
});
