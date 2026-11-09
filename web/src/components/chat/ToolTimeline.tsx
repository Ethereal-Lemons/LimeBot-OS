import { useState } from "react";
import { ChevronRight, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { BotVisual } from "@/components/pet/BotVisual";
import type { TurnStatus } from "@/lib/chat-state";
import type { ToolExecution } from "./ToolCard";
import { rowState, summarizeTurn } from "@/lib/tool-state";
import { GroupSummary, ToolRow } from "./tool-ui";

interface ToolTimelineProps {
    executions: ToolExecution[];
    botIdentity?: { name: string; avatar: string | null };
    turnStatus?: TurnStatus;
    onConfirmSideChannel?: (confId: string, approved: boolean, sessionWhitelist: boolean) => Promise<void>;
}

/** Three or more tool calls in a row, grouped under one collapsible summary. */
export function ToolTimeline({ executions, botIdentity, turnStatus, onConfirmSideChannel }: ToolTimelineProps) {
    const summary = summarizeTurn(executions, turnStatus);
    const active = summary.running > 0 || summary.waiting > 0 || turnStatus === "running" || turnStatus === "retrying";
    const [manual, setManual] = useState<boolean | null>(null);
    // Groups that need attention open on their own; finished ones stay out of the way.
    const open = manual ?? (active || summary.failed > 0 || turnStatus === "blocked");
    const current = executions.find((execution) => rowState(execution, null) === "running");

    return (
        <div className="flex w-full min-w-0 max-w-[92%] gap-3">
            <BotVisual avatar={botIdentity?.avatar} size="chat" />
            <div className="min-w-0 flex-1 overflow-hidden rounded-xl border border-border/60 bg-card/50 shadow-sm">
                <button
                    type="button"
                    onClick={() => setManual(!open)}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/40"
                    aria-expanded={open}
                >
                    {active ? (
                        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
                    ) : (
                        <ChevronRight className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200", open && "rotate-90")} />
                    )}
                    <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Activity</span>
                    <span className="h-1 w-1 rounded-full bg-muted-foreground/40" />
                    <GroupSummary executions={executions} turnStatus={turnStatus} />
                    {!open && current && (
                        <span className="ml-1 min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground/80">
                            {current.logs?.length ? current.logs[current.logs.length - 1] : current.tool}
                        </span>
                    )}
                    {(open || !current) && <span className="flex-1" />}
                    {active && (
                        <ChevronRight className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground/70 transition-transform duration-200", open && "rotate-90")} />
                    )}
                </button>
                {open && (
                    <div className="border-t border-border/50">
                        {executions.map((execution, index) => (
                            <ToolRow
                                key={`${execution.tool_call_id}-${index}`}
                                execution={execution}
                                handlers={{ onConfirmSideChannel }}
                                isLast={index === executions.length - 1}
                            />
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
