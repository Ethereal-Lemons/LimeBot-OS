import { Bot } from "lucide-react";
import { MarkdownMessage } from "./MarkdownMessage";

export type ParsedSubagentReport = {
    sessionId?: string;
    agentName?: string;
    task: string;
    result: string;
};

function formatSubagentTitle(agentName?: string): string {
    const raw = String(agentName || "").trim();
    if (!raw) return "Subagent";
    return raw
        .split(/[-_\s]+/)
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
}

export function parseSubagentReport(content: string): ParsedSubagentReport | null {
    const raw = String(content || "").trim();
    if (!raw.startsWith("--- SUB-AGENT REPORT")) return null;

    const match = raw.match(
        /^--- SUB-AGENT REPORT \(([^)]+)\)(?: \[([^\]]+)\])? ---\s*Task:\s*([\s\S]*?)\s*Result:\s*([\s\S]*)$/i
    );
    if (!match) return null;

    return {
        sessionId: match[1]?.trim() || undefined,
        agentName: match[2]?.trim() || undefined,
        task: match[3]?.trim() || "",
        result: match[4]?.trim() || "",
    };
}

export function SubagentReportCard({
    report,
}: {
    report: ParsedSubagentReport;
}) {
    const title = formatSubagentTitle(report.agentName);
    const result = report.result || "No result returned.";

    return (
        <div className="w-full overflow-hidden rounded-2xl border border-border/70 bg-card/70 shadow-sm">
            <div className="flex items-center gap-2 border-b border-border/70 bg-muted/30 px-4 py-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    <Bot className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                    <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold text-foreground">{title}</span>
                        <span className="rounded-full border border-primary/20 bg-primary/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-primary">
                            Subagent
                        </span>
                    </div>
                    <div className="text-[11px] text-muted-foreground">
                        Specialized delegated result
                    </div>
                </div>
            </div>

            <div className="space-y-4 px-4 py-4">
                {report.task ? (
                    <div className="space-y-1">
                        <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-muted-foreground">
                            Task
                        </div>
                        <div className="text-sm text-foreground">{report.task}</div>
                    </div>
                ) : null}

                <div className="space-y-1">
                    <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-muted-foreground">
                        Result
                    </div>
                    <MarkdownMessage content={result} isUser={false} />
                </div>
            </div>
        </div>
    );
}
