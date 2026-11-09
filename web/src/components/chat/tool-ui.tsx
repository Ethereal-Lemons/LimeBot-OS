import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
    AlertTriangle,
    Check,
    ChevronRight,
    Copy,
    FileText,
    FolderOpen,
    Globe,
    ImageIcon,
    Brain,
    Bot,
    CalendarClock,
    MessageSquare,
    Cpu,
    Pencil,
    Search,
    ShieldAlert,
    Terminal,
    X,
    type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
    classifyToolError,
    describeTool,
    formatArgs,
    isErrorResult,
    splitDependencyNotice,
    type ToolErrorInfo,
    type ToolIconKey,
} from "@/lib/tool-display";
import type { TurnStatus } from "@/lib/chat-state";
import { rowState, summarizeTurn, type RowState } from "@/lib/tool-state";
import type { ConfirmationPreview, ToolExecution } from "./ToolCard";

const ICONS: Record<ToolIconKey, LucideIcon> = {
    file: FileText,
    write: Pencil,
    terminal: Terminal,
    folder: FolderOpen,
    search: Search,
    web: Globe,
    memory: Brain,
    agent: Bot,
    image: ImageIcon,
    message: MessageSquare,
    calendar: CalendarClock,
    generic: Cpu,
};

const STATE_LABEL: Record<RowState, string> = {
    planned: "Queued",
    running: "Running",
    done: "Done",
    failed: "Failed",
    waiting: "Needs approval",
    approved: "Approved",
    denied: "Declined",
};

export function StatusGlyph({ state }: { state: RowState }) {
    const base = "relative flex h-5 w-5 shrink-0 items-center justify-center rounded-full";
    switch (state) {
        case "running":
            return (
                <span className={cn(base, "text-primary")} aria-label="Running">
                    <span className="absolute inset-0 rounded-full border-2 border-primary/20" />
                    <span className="absolute inset-0 animate-spin rounded-full border-2 border-transparent border-t-primary" />
                </span>
            );
        case "done":
        case "approved":
            return (
                <span className={cn(base, "bg-primary/15 text-primary")} aria-label="Done">
                    <Check className="h-3 w-3" strokeWidth={3} />
                </span>
            );
        case "failed":
        case "denied":
            return (
                <span className={cn(base, "bg-red-500/15 text-red-500")} aria-label="Failed">
                    <X className="h-3 w-3" strokeWidth={3} />
                </span>
            );
        case "waiting":
            return (
                <span className={cn(base, "bg-amber-500/15 text-amber-500")} aria-label="Needs approval">
                    <span className="absolute inset-0 animate-ping rounded-full bg-amber-400/20" />
                    <ShieldAlert className="relative h-3 w-3" />
                </span>
            );
        default:
            return (
                <span className={cn(base, "text-muted-foreground")} aria-label="Queued">
                    <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/50" />
                </span>
            );
    }
}

function stateTone(state: RowState): string {
    switch (state) {
        case "running":
        case "done":
        case "approved":
            return "text-primary";
        case "failed":
        case "denied":
            return "text-red-500";
        case "waiting":
            return "text-amber-500";
        default:
            return "text-muted-foreground";
    }
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
    const [copied, setCopied] = useState(false);
    return (
        <button
            type="button"
            onClick={async (event) => {
                event.stopPropagation();
                try {
                    await navigator.clipboard.writeText(text);
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1400);
                } catch {
                    toast.error("Could not copy to the clipboard");
                }
            }}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label={label}
        >
            {copied ? <Check className="h-3 w-3 text-primary" /> : <Copy className="h-3 w-3" />}
            {copied ? "Copied" : label}
        </button>
    );
}

function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
    return (
        <div className="space-y-1.5">
            <div className="flex items-center justify-between">
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80">{title}</div>
                {action}
            </div>
            {children}
        </div>
    );
}

const CODE = "max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border/60 bg-background/70 p-2.5 font-mono text-[11px] leading-relaxed text-muted-foreground";

export function ErrorNotice({ info, tool }: { info: ToolErrorInfo; tool: string }) {
    const [open, setOpen] = useState(info.category === "unknown" || info.category === "command_failed");
    const soft = ["denied", "policy", "invalid_arguments", "empty"].includes(info.category);
    const copyText = `${tool}: ${info.detail}`;
    return (
        <div
            role="alert"
            className={cn(
                "rounded-lg border-l-2 py-2 pl-3 pr-2",
                soft ? "border-amber-400/70 bg-amber-500/[0.06]" : "border-red-400/70 bg-red-500/[0.06]"
            )}
        >
            <div className="flex items-start gap-2">
                <AlertTriangle className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", soft ? "text-amber-500" : "text-red-500")} />
                <div className="min-w-0 flex-1">
                    <div className="text-xs font-semibold text-foreground">{info.title}</div>
                    <div className="mt-0.5 text-[11px] leading-snug text-muted-foreground">{info.hint}</div>
                    {info.detail && (
                        <button
                            type="button"
                            onClick={(event) => {
                                event.stopPropagation();
                                setOpen((value) => !value);
                            }}
                            className="mt-1.5 inline-flex items-center gap-1 text-[10px] font-medium text-muted-foreground hover:text-foreground"
                        >
                            <ChevronRight className={cn("h-3 w-3 transition-transform", open && "rotate-90")} />
                            Details
                        </button>
                    )}
                    {open && info.detail && (
                        <div className="mt-1.5 space-y-1">
                            <pre className={cn(CODE, "max-h-40")}>{info.detail}</pre>
                            <div className="flex justify-end">
                                <CopyButton text={copyText} label="Copy error" />
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

export function ApprovalPreview({ preview }: { preview: ConfirmationPreview }) {
    return (
        <div className="space-y-2 rounded-lg border border-amber-400/25 bg-amber-500/[0.06] p-3 text-[11px]">
            {preview.summary && <div className="text-xs font-semibold text-foreground">{preview.summary}</div>}
            {preview.risk_flags && preview.risk_flags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                    {preview.risk_flags.map((flag) => (
                        <span
                            key={flag}
                            className="rounded-full border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 text-[10px] font-medium capitalize text-amber-500"
                        >
                            {flag.replace(/_/g, " ")}
                        </span>
                    ))}
                </div>
            )}
            {preview.affected_paths && preview.affected_paths.length > 0 && (
                <Section title="Affected paths">
                    <pre className={cn(CODE, "max-h-24")}>{preview.affected_paths.join("\n")}</pre>
                </Section>
            )}
            {preview.command && (
                <Section title="Command">
                    <pre className={cn(CODE, "max-h-28")}>{preview.command}</pre>
                </Section>
            )}
            {preview.diff ? (
                <Section title="Changes">
                    <pre className={cn(CODE, "max-h-40")}>{preview.diff}</pre>
                </Section>
            ) : (
                preview.content_preview && (
                    <Section title="Content">
                        <pre className={cn(CODE, "max-h-40")}>{preview.content_preview}</pre>
                    </Section>
                )
            )}
            {preview.diff_error && <div className="text-muted-foreground">Change preview unavailable: {preview.diff_error}</div>}
        </div>
    );
}

export function ApprovalBar({
    disabled,
    onResolve,
}: {
    disabled?: boolean;
    onResolve: (approved: boolean, sessionWhitelist: boolean) => void;
}) {
    const button = "rounded-md px-3 py-1.5 text-xs font-semibold transition-colors disabled:cursor-wait disabled:opacity-50";
    return (
        <div className="flex flex-wrap items-center gap-2">
            <button
                type="button"
                disabled={disabled}
                onClick={(event) => {
                    event.stopPropagation();
                    onResolve(true, false);
                }}
                className={cn(button, "bg-primary text-primary-foreground hover:bg-primary/90")}
            >
                Allow once
            </button>
            <button
                type="button"
                disabled={disabled}
                onClick={(event) => {
                    event.stopPropagation();
                    onResolve(true, true);
                }}
                title="Allow this tool for the rest of the session"
                className={cn(button, "border border-border bg-transparent text-foreground hover:bg-muted")}
            >
                Allow for session
            </button>
            <div className="flex-1" />
            <button
                type="button"
                disabled={disabled}
                onClick={(event) => {
                    event.stopPropagation();
                    onResolve(false, false);
                }}
                className={cn(button, "text-red-500 hover:bg-red-500/10")}
            >
                Deny
            </button>
        </div>
    );
}

export interface ToolRowHandlers {
    onConfirmSideChannel?: (confId: string, approved: boolean, sessionWhitelist: boolean) => Promise<void>;
    onConfirm?: (toolCallId: string, approved: boolean) => void;
    onConfirmSession?: (toolCallId: string) => void;
}

export function ToolRow({
    execution,
    handlers,
    defaultOpen,
    isLast,
}: {
    execution: ToolExecution;
    handlers: ToolRowHandlers;
    defaultOpen?: boolean;
    isLast?: boolean;
}) {
    const [open, setOpen] = useState(!!defaultOpen);
    const [decision, setDecision] = useState<"approved" | "denied" | null>(null);
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        setDecision(null);
        setSubmitting(false);
    }, [execution.conf_id, execution.tool_call_id]);

    const rawState = rowState(execution, decision);
    const description = useMemo(() => describeTool(execution.tool, execution.args), [execution.tool, execution.args]);
    const Icon = ICONS[description.icon];
    const { notice: depsNotice, cleaned: result } = splitDependencyNotice(execution.result);
    const failed = rawState === "failed";
    const errorInfo = useMemo(
        () => (failed || isErrorResult(result) ? classifyToolError(depsNotice ? `dependencies are missing ${depsNotice}` : result) : null),
        [failed, result, depsNotice]
    );
    // A call the person declined is not a failure of the tool.
    const state: RowState = rawState === "failed" && errorInfo?.category === "denied" ? "denied" : rawState;
    const waiting = state === "waiting";
    const expanded = open || waiting || failed;

    const resolve = async (approved: boolean, sessionWhitelist: boolean) => {
        if (submitting || decision) return;
        setSubmitting(true);
        try {
            if (execution.conf_id && handlers.onConfirmSideChannel) {
                await handlers.onConfirmSideChannel(execution.conf_id, approved, sessionWhitelist);
            } else if (approved && sessionWhitelist) {
                handlers.onConfirmSession?.(execution.tool_call_id);
            } else {
                handlers.onConfirm?.(execution.tool_call_id, approved);
            }
            setDecision(approved ? "approved" : "denied");
        } catch {
            toast.error("The decision could not be sent. Try again.");
        } finally {
            setSubmitting(false);
        }
    };

    const hasBody = waiting || !!errorInfo || !!execution.preview || !!execution.logs?.length || !!result || Object.keys(execution.args || {}).length > 0;

    return (
        <div className={cn(!isLast && "border-b border-border/50")}>
            <button
                type="button"
                onClick={() => hasBody && setOpen((value) => !value)}
                className={cn(
                    "flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors",
                    hasBody ? "hover:bg-muted/40" : "cursor-default",
                    waiting && "bg-amber-500/[0.05]"
                )}
                aria-expanded={expanded}
            >
                <StatusGlyph state={state} />
                <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="shrink-0 text-xs font-medium text-foreground">{description.label}</span>
                {description.target && (
                    <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">{description.target}</span>
                )}
                {!description.target && <span className="flex-1" />}
                {execution.profile_name && (
                    <span className="hidden shrink-0 rounded-full border border-border/70 px-1.5 py-0.5 text-[10px] text-muted-foreground sm:inline">
                        {execution.profile_name}
                    </span>
                )}
                <span className={cn("shrink-0 text-[10px] font-medium", stateTone(state))}>{STATE_LABEL[state]}</span>
                {hasBody && (
                    <ChevronRight
                        className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground/70 transition-transform duration-200", expanded && "rotate-90")}
                    />
                )}
            </button>

            {expanded && hasBody && (
                <div className="space-y-3 px-3 pb-3 pl-[2.6rem] animate-in fade-in slide-in-from-top-1 duration-150">
                    {waiting && execution.preview && <ApprovalPreview preview={execution.preview} />}
                    {waiting && <ApprovalBar disabled={submitting} onResolve={resolve} />}
                    {decision && (
                        <div className={cn("text-[11px] font-medium", decision === "approved" ? "text-primary" : "text-red-500")}>
                            {decision === "approved" ? "Approval sent" : "Declined"}
                        </div>
                    )}
                    {errorInfo && <ErrorNotice info={errorInfo} tool={execution.tool} />}
                    {execution.logs && execution.logs.length > 0 && (
                        <Section title="Progress">
                            <div className="space-y-0.5 border-l border-primary/30 pl-2.5 font-mono text-[11px] text-muted-foreground">
                                {execution.logs.slice(-30).map((line, index) => (
                                    <div key={index} className="truncate">
                                        {line}
                                    </div>
                                ))}
                            </div>
                        </Section>
                    )}
                    {Object.keys(execution.args || {}).length > 0 && !waiting && (
                        <Section title="Input" action={<CopyButton text={JSON.stringify(execution.args, null, 2)} />}>
                            <pre className={CODE}>{formatArgs(execution.args)}</pre>
                        </Section>
                    )}
                    {result && !errorInfo && (
                        <Section title="Output" action={<CopyButton text={result} />}>
                            <pre className={CODE}>{result}</pre>
                        </Section>
                    )}
                </div>
            )}
        </div>
    );
}

export function GroupSummary({ executions, turnStatus }: { executions: ToolExecution[]; turnStatus?: TurnStatus }) {
    const summary = summarizeTurn(executions, turnStatus);
    let text = `${summary.total} steps`;
    if (summary.waiting) text = "Waiting for your approval";
    else if (summary.running || turnStatus === "running") text = "Working…";
    else if (turnStatus === "retrying") text = "Retrying from the last checkpoint…";
    else if (turnStatus === "blocked") text = "Blocked: needs your action";
    else if (turnStatus === "cancelled") text = "Cancelled";
    else if (summary.failed) text = `${summary.failed} failed`;
    else if (summary.recovered) text = `${summary.total} steps, recovered from an error`;
    else text = `${summary.done || summary.total} steps done`;
    const tone = summary.waiting || turnStatus === "blocked" ? "text-amber-500" : summary.failed ? "text-red-500" : "text-muted-foreground";
    return <span className={cn("text-xs font-medium", tone)}>{text}</span>;
}
