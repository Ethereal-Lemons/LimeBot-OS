import { mock } from "../mock-api";

// What the app shell asks for on boot, so any page can render around it.
mock("GET", /^\/api\/setup\/status$/, () => ({ configured: true, persona_ready: true }));
mock("GET", /^\/api\/identity$/, () => ({ name: "Jennie", avatar: null }));
mock("GET", /^\/api\/ready$/, () => ({ status: "ready", ready: true, phase: "agent", elapsed_ms: 0, degraded_reasons: [], failure_code: null }));
