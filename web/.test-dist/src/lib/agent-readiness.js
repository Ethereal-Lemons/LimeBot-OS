export const INITIAL_AGENT_READINESS = {
    status: "starting",
    phase: "agent",
    ready: false,
    elapsed_ms: 0,
    degraded_reasons: [],
    failure_code: null,
};
export function normalizeAgentReadiness(payload) {
    const value = (payload && typeof payload === "object" ? payload : {});
    const rawStatus = typeof value.status === "string" ? value.status : "starting";
    const status = ["starting", "ready", "degraded", "failed", "timeout"].includes(rawStatus)
        ? rawStatus
        : "starting";
    return {
        status,
        phase: typeof value.phase === "string" && value.phase ? value.phase : "agent",
        ready: value.ready === true && (status === "ready" || status === "degraded"),
        elapsed_ms: typeof value.elapsed_ms === "number" ? Math.max(0, value.elapsed_ms) : 0,
        degraded_reasons: Array.isArray(value.degraded_reasons)
            ? value.degraded_reasons.filter((item) => typeof item === "string")
            : [],
        failure_code: typeof value.failure_code === "string" ? value.failure_code : null,
    };
}
export function readinessLabel(readiness) {
    if (readiness.ready && readiness.status === "degraded")
        return "Ready with optional integrations unavailable";
    if (readiness.ready)
        return "Skills and tools ready";
    if (readiness.status === "failed")
        return "Required capabilities failed to load";
    if (readiness.status === "timeout")
        return "Capability loading timed out";
    const labels = {
        created: "Starting agent",
        skills: "Loading skills",
        subagents: "Loading subagents",
        mcp: "Connecting optional MCP servers",
        tools: "Building tool catalog",
        agent: "Preparing agent",
    };
    return labels[readiness.phase] || "Preparing skills and tools";
}
