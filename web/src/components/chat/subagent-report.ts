export type ParsedSubagentReport = {
    sessionId?: string;
    agentName?: string;
    task: string;
    result: string;
};

export function parseSubagentReport(content: string): ParsedSubagentReport | null {
    const raw = String(content || "").trim();
    if (!raw.startsWith("--- SUB-AGENT REPORT")) return null;

    const match = raw.match(
        /^--- SUB-AGENT REPORT \(([^)]+)\)(?: \[([^\]]+)\])? ---\s*Task:\s*([\s\S]*?)\s*Result:\s*([\s\S]*)$/i,
    );
    if (!match) return null;

    return {
        sessionId: match[1]?.trim() || undefined,
        agentName: match[2]?.trim() || undefined,
        task: match[3]?.trim() || "",
        result: match[4]?.trim() || "",
    };
}
