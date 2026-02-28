import { useEffect, useMemo, useState } from "react";
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
    const [selectedToolId, setSelectedToolId] = useState<string | null>(null);

    const hasRunning = executions.some((e) => e.status === "running");
    const hasError = executions.some((e) => e.status === "error");
    const completed = executions.filter((e) => e.status === "completed").length;
    const runningExecution = executions.find((e) => e.status === "running");
    const newestExecution = executions[executions.length - 1];

    useEffect(() => {
        if (!executions.length) {
            setSelectedToolId(null);
            return;
        }
        if (selectedToolId && executions.some((e) => e.tool_call_id === selectedToolId)) {
            return;
        }
        const preferred = runningExecution || newestExecution;
        setSelectedToolId(preferred?.tool_call_id || null);
    }, [executions, selectedToolId, runningExecution, newestExecution]);

    const selectedExecution = useMemo(() => {
        if (!executions.length) return null;
        return (
            executions.find((e) => e.tool_call_id === selectedToolId) ||
            runningExecution ||
            newestExecution ||
            null
        );
    }, [executions, selectedToolId, runningExecution, newestExecution]);

    const getSummary = (exec: ToolExecution) => {
        const args = exec.args || {};
        switch (exec.tool) {
            case "read_file":
                return `Reading ${args.path || "file"}`;
            case "write_file":
                return `Writing ${args.path || "file"}`;
            case "run_command":
                return args.command ? `> ${args.command}` : "Running command";
            case "list_dir":
                return `Listing ${args.path || "."}`;
            case "memory_search":
                return args.query ? `Searching memory for "${args.query}"` : "Searching memory";
            default:
                return JSON.stringify(args).slice(0, 120);
        }
    };

    const getLatestAction = (exec: ToolExecution) => {
        if (exec.logs && exec.logs.length > 0) {
            return exec.logs[exec.logs.length - 1];
        }
        return getSummary(exec);
    };

    const currentAction = runningExecution
        ? getLatestAction(runningExecution)
        : selectedExecution
            ? getLatestAction(selectedExecution)
            : "";

    const getStatusLabel = (exec: ToolExecution) => {
        if (exec.status === "running") return "Running";
        if (exec.status === "completed") return "Completed";
        if (exec.status === "error") return "Failed";
        if (exec.status === "waiting_confirmation" || exec.status === "pending_confirmation") {
            return "Waiting confirmation";
        }
        return exec.status;
    };

    const getStatusTone = (exec: ToolExecution) => {
        if (exec.status === "running") return "text-primary";
        if (exec.status === "completed") return "text-primary";
        if (exec.status === "error") return "text-destructive";
        if (exec.status === "waiting_confirmation" || exec.status === "pending_confirmation") {
            return "text-amber-500";
        }
        return "text-muted-foreground";
    };

    return (
        <div className="flex w-full gap-4 max-w-[90%] min-w-0">
            <Avatar className="h-9 w-9 mt-1 shrink-0 border border-border shadow-sm">
                <AvatarImage src={botIdentity?.avatar || undefined} className="object-cover" />
                <AvatarFallback className="bg-primary text-primary-foreground text-xs font-bold">Bot</AvatarFallback>
            </Avatar>

            <div className="flex-1 min-w-0 overflow-hidden">
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
                    {currentAction && (
                        <div className="mt-2 text-[11px] text-muted-foreground/80 truncate overflow-hidden">
                            Current action: <span className="font-mono break-all">{currentAction}</span>
                        </div>
                    )}
                </div>

                {expanded && (
                    <div className="mt-2 rounded-xl border border-border/50 bg-background/60">
                        <div className="max-h-64 overflow-y-auto">
                            {executions.map((exec, idx) => {
                                const isSelected = selectedExecution?.tool_call_id === exec.tool_call_id;
                                return (
                                    <button
                                        key={`${exec.tool_call_id}-${idx}`}
                                        type="button"
                                        onClick={() => setSelectedToolId(exec.tool_call_id)}
                                        className={cn(
                                            "w-full flex items-center gap-3 px-4 py-2 text-left text-xs transition-colors",
                                            idx < executions.length - 1 && "border-b border-border/40",
                                            isSelected ? "bg-primary/10" : "hover:bg-muted/40"
                                        )}
                                    >
                                        <div className="w-6 text-muted-foreground/70 font-mono">{idx + 1}.</div>
                                        <div className="flex-1 min-w-0">
                                            <div className="font-semibold text-foreground/80 truncate">{exec.tool}</div>
                                            <div className="text-[11px] text-muted-foreground/70 truncate">
                                                {getSummary(exec)}
                                            </div>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            {exec.status === "running" && <Loader2 className="h-3 w-3 animate-spin text-primary" />}
                                            {exec.status === "error" && <XCircle className="h-3 w-3 text-destructive" />}
                                            {exec.status === "completed" && <CheckCircle2 className="h-3 w-3 text-primary" />}
                                            {(exec.status === "waiting_confirmation" || exec.status === "pending_confirmation") && (
                                                <span className="text-[10px] font-semibold text-primary">WAITING</span>
                                            )}
                                        </div>
                                    </button>
                                );
                            })}
                        </div>

                        {selectedExecution && (
                            <div className="border-t border-border/50 px-4 py-3 text-xs">
                                <div className="flex items-center justify-between gap-3">
                                    <div className="font-semibold text-foreground">Selected: {selectedExecution.tool}</div>
                                    <div className={cn("font-semibold", getStatusTone(selectedExecution))}>
                                        {getStatusLabel(selectedExecution)}
                                    </div>
                                </div>

                                <div className="mt-2 text-[11px] text-muted-foreground">
                                    Action: <span className="font-mono">{getLatestAction(selectedExecution)}</span>
                                </div>

                                <div className="mt-2 text-[11px] text-muted-foreground">
                                    Args:
                                </div>
                                <pre className="mt-1 rounded-md border border-border/50 bg-background/80 p-2 text-[11px] text-muted-foreground whitespace-pre-wrap break-words max-h-28 overflow-y-auto">
                                    {JSON.stringify(selectedExecution.args || {}, null, 2)}
                                </pre>

                                <div className="mt-2 text-[11px] text-muted-foreground">
                                    Live logs:
                                </div>
                                <div className="mt-1 rounded-md border border-border/50 bg-background/80 p-2 text-[11px] font-mono text-muted-foreground max-h-28 overflow-y-auto">
                                    {selectedExecution.logs && selectedExecution.logs.length > 0 ? (
                                        selectedExecution.logs.slice(-30).map((log, idx) => (
                                            <div key={idx} className="truncate">{log}</div>
                                        ))
                                    ) : selectedExecution.status === "running" ? (
                                        <div className="text-primary/70">Waiting for progress updates…</div>
                                    ) : (
                                        <div className="opacity-70">No logs captured.</div>
                                    )}
                                </div>

                                {selectedExecution.result && (
                                    <>
                                        <div className="mt-2 text-[11px] text-muted-foreground">Result:</div>
                                        <div className="mt-1 rounded-md border border-border/50 bg-background/80 p-2 text-[11px] text-muted-foreground whitespace-pre-wrap break-words max-h-32 overflow-y-auto">
                                            {selectedExecution.result}
                                        </div>
                                    </>
                                )}
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}
