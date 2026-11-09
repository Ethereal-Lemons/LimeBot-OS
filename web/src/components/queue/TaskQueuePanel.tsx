import { useCallback, useEffect, useState, type ReactNode } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { Bot, CheckCircle2, ChevronRight, CloudOff, Globe, Inbox, MessageSquare, RefreshCw, Send, Server, Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import { classifyToolError } from "@/lib/tool-display";
import { formatAgo, formatDuration, statusLabel, toRowState, toneText } from "@/lib/ops-status";
import { Switch } from "@/components/ui/switch";
import { ErrorNotice } from "@/components/chat/tool-ui";
import {
    EmptyState,
    IconButton,
    PageFrame,
    RequestError,
    Segmented,
    SkeletonRows,
    StatusGlyph,
} from "@/components/ops/ops-ui";


interface Task {
    id: string;
    taskType: string;
    status: "queued" | "running" | "waiting" | "retrying" | "completed" | "failed" | "cancelled";
    summary: string;
    channel: string;
    sessionKey?: string;
    chatId: string;
    createdAt: number;
    updatedAt: number;
    completedAt?: number;
    error?: string;
    metadata: Record<string, unknown>;
}

interface Delivery {
    id: string;
    channel: string;
    chatId: string;
    target: string;
    messageKind: string;
    status: "queued" | "sending" | "retrying" | "sent" | "failed";
    attempts: number;
    preview: string;
    createdAt: number;
    updatedAt: number;
    sentAt?: number;
    error?: string;
    metadata: Record<string, unknown>;
}

const asRecord = (value: unknown): Record<string, unknown> =>
    value && typeof value === "object" ? (value as Record<string, unknown>) : {};

const asString = (value: unknown, fallback = ""): string =>
    typeof value === "string" ? value : fallback;

const asNumber = (value: unknown, fallback = 0): number =>
    typeof value === "number" && Number.isFinite(value) ? value : fallback;

const summarizeDelivery = (raw: Record<string, unknown>, metadata: Record<string, unknown>): string => {
    const directPreview = asString(raw.preview);
    if (directPreview) return directPreview;

    const metadataPreview = asString(metadata.preview);
    if (metadataPreview) return metadataPreview;

    const content = asString(metadata.content);
    if (content) return content.slice(0, 180);

    const summary = asString(metadata.summary);
    if (summary) return summary;

    const target = asString(raw.target);
    const kind = asString(raw.message_kind, "message");
    return target ? `${kind} to ${target}` : kind;
};

const normalizeTask = (raw: unknown): Task => {
    const item = asRecord(raw);
    return {
        id: asString(item.task_id || item.id, "unknown-task"),
        taskType: asString(item.type || item.task_type, "task"),
        status: asString(item.status, "queued") as Task["status"],
        summary: asString(item.summary, "No summary"),
        channel: asString(item.channel, "unknown"),
        sessionKey: asString(item.session_key),
        chatId: asString(item.chat_id),
        createdAt: asNumber(item.created_at),
        updatedAt: asNumber(item.updated_at, asNumber(item.created_at)),
        completedAt: item.completed_at ? asNumber(item.completed_at) : undefined,
        error: asString(item.error),
        metadata: asRecord(item.metadata),
    };
};

const normalizeDelivery = (raw: unknown): Delivery => {
    const item = asRecord(raw);
    const metadata = asRecord(item.metadata);
    const lastAttemptAt = asNumber(item.last_attempt_at);
    const sentAt = asNumber(item.sent_at);
    return {
        id: asString(item.delivery_id || item.id, "unknown-delivery"),
        channel: asString(item.channel, "unknown"),
        chatId: asString(item.chat_id || item.target),
        target: asString(item.target),
        messageKind: asString(item.message_kind || item.delivery_type, "text"),
        status: asString(item.status, "queued") as Delivery["status"],
        attempts: asNumber(item.attempts),
        preview: summarizeDelivery(item, metadata),
        createdAt: asNumber(item.created_at),
        updatedAt: sentAt || lastAttemptAt || asNumber(item.updated_at, asNumber(item.created_at)),
        sentAt: sentAt || undefined,
        error: asString(item.last_error || item.error),
        metadata,
    };
};

const HISTORY_LIMIT = 50;

type Pane = "work" | "deliveries";
type Scope = "active" | "history";

const ACTIVE_TASK = new Set(["queued", "running", "waiting", "retrying"]);
const ACTIVE_DELIVERY = new Set(["queued", "sending", "retrying"]);

/** Re-renders on an interval so relative times keep moving while polling is paused. */
function useNow(intervalMs: number): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        const id = window.setInterval(() => setNow(Date.now()), intervalMs);
        return () => window.clearInterval(id);
    }, [intervalMs]);
    return now;
}

function typeIcon(type?: string) {
    const normalized = String(type || "").toLowerCase();
    const cls = "h-3.5 w-3.5 shrink-0 text-muted-foreground";
    if (normalized.includes("message")) return <MessageSquare className={cls} />;
    if (normalized.includes("browser")) return <Globe className={cls} />;
    if (normalized.includes("job")) return <Zap className={cls} />;
    if (normalized.includes("subagent")) return <Bot className={cls} />;
    return <Server className={cls} />;
}

function stateTone(status: string) {
    const state = toRowState(status);
    if (state === "failed") return "error" as const;
    if (state === "waiting") return "warning" as const;
    if (state === "planned") return "neutral" as const;
    return "ready" as const;
}

function clock(ts: number) {
    return ts ? new Date(ts * 1000).toLocaleString() : "";
}

function Meta({ children }: { children: ReactNode }) {
    return <span className="whitespace-nowrap">{children}</span>;
}

function QueueRow({
    status,
    icon,
    title,
    summary,
    quote,
    meta,
    createdAt,
    now,
    error,
    errorTool,
    detail,
}: {
    status: string;
    icon: ReactNode;
    title: string;
    summary: string;
    quote?: boolean;
    meta: ReactNode[];
    createdAt: number;
    now: number;
    error?: string;
    errorTool: string;
    detail?: string;
}) {
    const [open, setOpen] = useState(false);
    const state = toRowState(status);
    const failed = state === "failed" || !!error;
    const expandable = !!error || summary.length > 120 || !!detail;
    const expanded = open || failed;
    return (
        <li className={cn(state === "waiting" && "bg-amber-500/[0.05]")}>
            <button
                type="button"
                onClick={() => expandable && setOpen((value) => !value)}
                aria-expanded={expandable ? expanded : undefined}
                className={cn(
                    "flex w-full items-start gap-3 px-4 py-2.5 text-left transition-colors",
                    expandable ? "hover:bg-foreground/[0.05]" : "cursor-default"
                )}
            >
                <span className="mt-0.5">
                    <StatusGlyph state={state} />
                </span>
                <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                        {icon}
                        <span className="min-w-0 truncate text-xs font-medium text-foreground">{title}</span>
                        <span className={cn("shrink-0 text-[10px] font-medium", toneText(stateTone(status)))}>{statusLabel(status)}</span>
                    </span>
                    <span className={cn("mt-0.5 block text-[13px] leading-snug text-foreground/85", !expanded && "line-clamp-2", quote && "italic")}>
                        {quote ? `“${summary}”` : summary}
                    </span>
                    <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-[10px] text-muted-foreground">
                        {meta.map((item, index) => (
                            <Meta key={index}>{item}</Meta>
                        ))}
                    </span>
                </span>
                <span className="flex shrink-0 items-center gap-1 pt-0.5 text-[11px] text-muted-foreground" title={clock(createdAt)}>
                    {formatAgo(createdAt, now)}
                    {expandable && <ChevronRight className={cn("h-3.5 w-3.5 transition-transform", expanded && "rotate-90")} />}
                </span>
            </button>
            {expanded && (error || detail) && (
                <div className="space-y-2 px-4 pb-3 pl-[2.9rem]">
                    {error && <ErrorNotice info={classifyToolError(error)} tool={errorTool} />}
                    {detail && <p className="font-mono text-[11px] text-muted-foreground">{detail}</p>}
                </div>
            )}
        </li>
    );
}

function Column({
    title,
    visible,
    scope,
    onScope,
    activeCount,
    historyCount,
    activeLabel,
    historyLabel,
    children,
}: {
    title: string;
    visible: boolean;
    scope: Scope;
    onScope: (scope: Scope) => void;
    activeCount: number;
    historyCount: number;
    activeLabel: string;
    historyLabel: string;
    children: ReactNode;
}) {
    return (
        <section className={cn("min-h-0 min-w-0 flex-1 flex-col", visible ? "flex" : "hidden lg:flex")} aria-label={title}>
            <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border/60 px-4 py-2">
                <h2 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/90">{title}</h2>
                <Segmented
                    label={`${title} filter`}
                    value={scope}
                    onChange={onScope}
                    options={[
                        { value: "active", label: `${activeLabel} ${activeCount}` },
                        { value: "history", label: `${historyLabel} ${historyCount}` },
                    ]}
                />
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        </section>
    );
}

export function TaskQueuePanel() {
    const [tasks, setTasks] = useState<Task[]>([]);
    const [deliveries, setDeliveries] = useState<Delivery[]>([]);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState<unknown>(null);
    const [loaded, setLoaded] = useState(false);
    const [autoRefresh, setAutoRefresh] = useState(true);
    const [pane, setPane] = useState<Pane>("work");
    const [workScope, setWorkScope] = useState<Scope>("active");
    const [deliveryScope, setDeliveryScope] = useState<Scope>("active");
    const now = useNow(15000);

    const fetchData = useCallback(async (manual = false) => {
        if (manual) setRefreshing(true);
        try {
            const [tasksRes, deliveriesRes] = await Promise.all([
                axios.get(`${API_BASE_URL}/api/tasks`),
                axios.get(`${API_BASE_URL}/api/deliveries`),
            ]);

            setTasks([...(tasksRes.data.tasks || [])].map(normalizeTask).sort((a, b) => b.createdAt - a.createdAt));
            setDeliveries([...(deliveriesRes.data.deliveries || [])].map(normalizeDelivery).sort((a, b) => b.createdAt - a.createdAt));
            setError(null);
            setLoaded(true);
        } catch (err) {
            // Keep what is on screen; the notice says it may be out of date.
            setError(err);
            console.error("Failed to fetch observability data:", err);
        } finally {
            setLoading(false);
            if (manual) setRefreshing(false);
        }
    }, []);

    useEffect(() => {
        void fetchData();
        if (!autoRefresh) return;
        const id = window.setInterval(() => void fetchData(), 2000);
        return () => window.clearInterval(id);
    }, [autoRefresh, fetchData]);

    const activeTasks = tasks.filter((t) => ACTIVE_TASK.has(t.status));
    const pastTasks = tasks.filter((t) => !ACTIVE_TASK.has(t.status));
    const activeDeliveries = deliveries.filter((d) => ACTIVE_DELIVERY.has(d.status));
    const pastDeliveries = deliveries.filter((d) => !ACTIVE_DELIVERY.has(d.status));
    const failedTasks = pastTasks.filter((t) => t.status === "failed").length;
    const failedDeliveries = pastDeliveries.filter((d) => d.status === "failed").length;

    const shownTasks = workScope === "active" ? activeTasks : pastTasks.slice(0, HISTORY_LIMIT);
    const shownDeliveries = deliveryScope === "active" ? activeDeliveries : pastDeliveries.slice(0, HISTORY_LIMIT);

    const actions = (
        <>
            <label className="flex cursor-pointer select-none items-center gap-2 text-xs text-muted-foreground">
                <Switch
                    checked={autoRefresh}
                    onCheckedChange={setAutoRefresh}
                    aria-label="Live updates"
                    className="h-4 w-7 [&>span]:h-3 [&>span]:w-3 data-[state=checked]:[&>span]:translate-x-3"
                />
                <span className="hidden sm:inline">{autoRefresh ? "Live" : "Paused"}</span>
            </label>
            <IconButton label="Refresh now" onClick={() => void fetchData(true)} disabled={refreshing}>
                <RefreshCw className={cn(refreshing && "animate-spin")} />
            </IconButton>
        </>
    );

    const unavailable = !loaded && !!error;
    const summary = [
        activeTasks.length ? `${activeTasks.length} running` : "Idle",
        failedTasks ? `${failedTasks} failed` : null,
        activeDeliveries.length ? `${activeDeliveries.length} pending delivery` : null,
        failedDeliveries ? `${failedDeliveries} failed delivery` : null,
    ]
        .filter(Boolean)
        .join(" · ");

    const emptyBlock = (icon: typeof CheckCircle2, title: string, hint: string) => <EmptyState icon={icon} title={title} hint={hint} />;

    return (
        <PageFrame title="Task queue" description={loading ? "Loading agent work and deliveries…" : unavailable ? "Unavailable" : summary} actions={actions} scroll={false} maxWidth="max-w-none">
            {error ? (
                <div className="shrink-0 px-4 pt-3 sm:px-6">
                    <RequestError error={error} what="the task queue" onRetry={() => void fetchData(true)} retrying={refreshing} stale={loaded} />
                </div>
            ) : null}

            <div className="flex shrink-0 justify-center border-b border-border/60 px-4 py-2 lg:hidden">
                <Segmented
                    label="Queue section"
                    value={pane}
                    onChange={setPane}
                    options={[
                        { value: "work", label: `Agent work ${activeTasks.length || ""}`.trim() },
                        { value: "deliveries", label: `Deliveries ${activeDeliveries.length || ""}`.trim() },
                    ]}
                />
            </div>

            <div className="flex min-h-0 flex-1 lg:divide-x lg:divide-border/60">
                <Column
                    title="Agent work"
                    visible={pane === "work"}
                    scope={workScope}
                    onScope={setWorkScope}
                    activeCount={activeTasks.length}
                    historyCount={pastTasks.length}
                    activeLabel="In flight"
                    historyLabel="History"
                >
                    {loading ? (
                        <SkeletonRows rows={5} />
                    ) : unavailable ? (
                        emptyBlock(CloudOff, "Nothing to show yet", "Work and deliveries appear here once the backend answers.")
                    ) : shownTasks.length === 0 ? (
                        workScope === "active"
                            ? emptyBlock(CheckCircle2, "Nothing running", "Work shows up here the moment the agent starts on it.")
                            : emptyBlock(Inbox, "No task history yet", "Finished, failed and cancelled work is listed here.")
                    ) : (
                        <ul className="divide-y divide-border/50">
                            {shownTasks.map((task) => {
                                const agent = asString(task.metadata.profile_name, asString(task.metadata.profile_id, "Default"));
                                return (
                                    <QueueRow
                                        key={task.id}
                                        status={task.status}
                                        icon={typeIcon(task.taskType)}
                                        title={task.taskType}
                                        summary={task.summary}
                                        meta={[
                                            task.channel,
                                            agent,
                                            task.id.slice(0, 8),
                                            task.completedAt ? formatDuration(task.completedAt - task.createdAt) : "",
                                        ].filter(Boolean)}
                                        createdAt={task.createdAt}
                                        now={now}
                                        error={task.error}
                                        errorTool={task.taskType}
                                    />
                                );
                            })}
                            {workScope === "history" && pastTasks.length > HISTORY_LIMIT && (
                                <li className="px-4 py-3 text-center text-[11px] text-muted-foreground">Showing the latest {HISTORY_LIMIT} of {pastTasks.length}</li>
                            )}
                        </ul>
                    )}
                </Column>

                <Column
                    title="Outbound deliveries"
                    visible={pane === "deliveries"}
                    scope={deliveryScope}
                    onScope={setDeliveryScope}
                    activeCount={activeDeliveries.length}
                    historyCount={pastDeliveries.length}
                    activeLabel="Pending"
                    historyLabel="Sent"
                >
                    {loading ? (
                        <SkeletonRows rows={5} />
                    ) : unavailable ? (
                        emptyBlock(CloudOff, "Nothing to show yet", "Work and deliveries appear here once the backend answers.")
                    ) : shownDeliveries.length === 0 ? (
                        deliveryScope === "active"
                            ? emptyBlock(CheckCircle2, "Nothing waiting to send", "Replies queue here while a channel is busy or retrying.")
                            : emptyBlock(Send, "No deliveries yet", "Replies sent to Discord, WhatsApp and the web chat are listed here.")
                    ) : (
                        <ul className="divide-y divide-border/50">
                            {shownDeliveries.map((delivery) => {
                                const agent = asString(delivery.metadata.profile_name) || asString(delivery.metadata.profile_id);
                                return (
                                    <QueueRow
                                        key={delivery.id}
                                        status={delivery.status}
                                        icon={<Send className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                                        title={`To ${delivery.channel}`}
                                        summary={delivery.preview}
                                        quote
                                        meta={[
                                            delivery.messageKind,
                                            agent,
                                            delivery.attempts > 1 ? `${delivery.attempts} attempts` : "",
                                            delivery.id.slice(0, 8),
                                        ].filter(Boolean)}
                                        createdAt={delivery.createdAt}
                                        now={now}
                                        error={delivery.error}
                                        errorTool={`${delivery.channel} delivery`}
                                    />
                                );
                            })}
                            {deliveryScope === "history" && pastDeliveries.length > HISTORY_LIMIT && (
                                <li className="px-4 py-3 text-center text-[11px] text-muted-foreground">Showing the latest {HISTORY_LIMIT} of {pastDeliveries.length}</li>
                            )}
                        </ul>
                    )}
                </Column>
            </div>
        </PageFrame>
    );
}
