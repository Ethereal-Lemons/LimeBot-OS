import { useState } from "react";
import { ChevronDown, ChevronUp, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { ToolExecution } from "./ToolCard";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";

interface ToolTimelineProps {
    executions: ToolExecution[];
    botIdentity?: { name: string; avatar: string | null };
}

export function ToolTimeline({ executions, botIdentity }: ToolTimelineProps) {
    const [expanded, setExpanded] = useState(false);

    const hasRunning = executions.some((e) => e.status === "running");
    const hasError = executions.some((e) => e.status === "error");
    const completed = executions.filter((e) => e.status === "completed").length;

    return (
        <div className="flex w-full gap-4 max-w-[90%]">
            <Avatar className="h-9 w-9 mt-1 shrink-0 border border-border shadow-sm">
                <AvatarImage src={botIdentity?.avatar || undefined} className="object-cover" />
                <AvatarFallback className="bg-primary text-primary-foreground text-xs font-bold">Bot</AvatarFallback>
            </Avatar>

            <div className="flex-1">
                <div
                    className={cn(
                        "rounded-2xl rounded-tl-none border border-border bg-muted/70 px-4 py-3",
                        "shadow-sm hover:border-primary/30 transition-colors cursor-pointer"
                    )}
                    onClick={() => setExpanded(!expanded)}
                >
                    <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-muted-foreground">
                            <span>Tool Timeline</span>
                            <span className="h-1 w-1 rounded-full bg-muted-foreground/40" />
                            <span>{executions.length} steps</span>
                        </div>
                        <div className="flex items-center gap-2">
                            {hasRunning && <Loader2 className="h-4 w-4 animate-spin text-primary" />}
                            {!hasRunning && hasError && <XCircle className="h-4 w-4 text-destructive" />}
                            {!hasRunning && !hasError && <CheckCircle2 className="h-4 w-4 text-primary" />}
                            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                        </div>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground/80">
                        {hasRunning
                            ? "Executing tools…"
                            : hasError
                                ? "One or more tools failed."
                                : `${completed} completed successfully.`}
                    </div>
                </div>

                {expanded && (
                    <div className="mt-2 rounded-xl border border-border/50 bg-background/60">
                        {executions.map((exec, idx) => (
                            <div
                                key={`${exec.tool_call_id}-${idx}`}
                                className={cn(
                                    "flex items-center gap-3 px-4 py-2 text-xs",
                                    idx < executions.length - 1 && "border-b border-border/40"
                                )}
                            >
                                <div className="w-6 text-muted-foreground/70 font-mono">{idx + 1}.</div>
                                <div className="flex-1">
                                    <div className="font-semibold text-foreground/80">{exec.tool}</div>
                                    <div className="text-[11px] text-muted-foreground/70 truncate">
                                        {JSON.stringify(exec.args).slice(0, 120)}
                                    </div>
                                </div>
                                <div className="flex items-center gap-2">
                                    {exec.status === "running" && <Loader2 className="h-3 w-3 animate-spin text-primary" />}
                                    {exec.status === "error" && <XCircle className="h-3 w-3 text-destructive" />}
                                    {exec.status === "completed" && <CheckCircle2 className="h-3 w-3 text-primary" />}
                                    {exec.status === "waiting_confirmation" && (
                                        <span className="text-[10px] font-semibold text-primary">WAITING</span>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
