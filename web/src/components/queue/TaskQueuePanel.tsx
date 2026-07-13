import { useEffect, useState, useRef } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import {
    ActivitySquare,
    CheckCircle2,
    XCircle,
    Clock,
    RefreshCw,
    Server,
    Zap,
    MessageSquare,
    Globe,
    Bot
} from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

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

export function TaskQueuePanel() {
    const [tasks, setTasks] = useState<Task[]>([]);
    const [deliveries, setDeliveries] = useState<Delivery[]>([]);
    const [loading, setLoading] = useState(true);
    const [autoRefresh, setAutoRefresh] = useState(true);
    const pollInterval = useRef<number | null>(null);

    const fetchData = async () => {
        try {
            const [tasksRes, deliveriesRes] = await Promise.all([
                axios.get(`${API_BASE_URL}/api/tasks`),
                axios.get(`${API_BASE_URL}/api/deliveries`)
            ]);

            const sortedTasks = [...(tasksRes.data.tasks || [])]
                .map(normalizeTask)
                .sort((a, b) => b.createdAt - a.createdAt);
            const sortedDeliveries = [...(deliveriesRes.data.deliveries || [])]
                .map(normalizeDelivery)
                .sort((a, b) => b.createdAt - a.createdAt);

            setTasks(sortedTasks);
            setDeliveries(sortedDeliveries);
        } catch (error) {
            console.error("Failed to fetch observability data:", error);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchData();
        if (autoRefresh) {
            pollInterval.current = window.setInterval(fetchData, 2000);
        }
        return () => {
            if (pollInterval.current) clearInterval(pollInterval.current);
        };
    }, [autoRefresh]);

    const activeTasks = tasks.filter((t) =>
        t.status === "queued" || t.status === "running" || t.status === "waiting" || t.status === "retrying"
    );
    const completedTasks = tasks.filter((t) =>
        t.status === "completed" || t.status === "failed" || t.status === "cancelled"
    );

    const activeDeliveries = deliveries.filter((d) =>
        d.status === "queued" || d.status === "sending" || d.status === "retrying"
    );
    const pastDeliveries = deliveries.filter((d) =>
        d.status === "sent" || d.status === "failed"
    );

    const getStatusIcon = (status: string) => {
        switch (status) {
            case "running":
            case "sending":
            case "queued":
            case "waiting":
            case "retrying":
                return <Clock className="w-4 h-4 text-blue-500 animate-pulse" />;
            case "completed":
            case "sent":
                return <CheckCircle2 className="h-4 w-4 text-primary" />;
            case "failed":
            case "cancelled":
                return <XCircle className="w-4 h-4 text-red-500" />;
            default:
                return <ActivitySquare className="w-4 h-4 text-gray-500" />;
        }
    };

    const getTypeIcon = (type?: string) => {
        const normalized = String(type || "").toLowerCase();
        if (normalized.includes("message")) return <MessageSquare className="w-4 h-4" />;
        if (normalized.includes("browser")) return <Globe className="w-4 h-4" />;
        if (normalized.includes("job")) return <Zap className="w-4 h-4" />;
        if (normalized.includes("subagent")) return <Bot className="w-4 h-4" />;
        return <Server className="w-4 h-4" />;
    };

    const formatTime = (ts: number) => {
        if (!ts) return "--:--:--";
        return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    };

    const renderTaskCard = (task: Task) => (
        <div key={task.id} className="flex flex-col p-3 rounded-lg border border-border bg-card/50 hover:bg-card/80 transition-colors mb-2">
            <div className="flex justify-between items-start mb-1">
                <div className="flex items-center gap-2">
                    {getTypeIcon(task.taskType)}
                    <span className="font-medium text-sm">{task.taskType}</span>
                    <Badge variant="outline" className="text-xs font-mono">{task.channel}</Badge>
                </div>
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                    {formatTime(task.createdAt)}
                    {getStatusIcon(task.status)}
                </div>
            </div>
            <p className="text-sm text-foreground/80 break-words">{task.summary}</p>
            {task.error && (
                <div className="mt-2 p-2 rounded bg-red-500/10 border border-red-500/20 text-xs text-red-500 font-mono break-all">
                    {task.error}
                </div>
            )}
            <div className="mt-2 flex justify-between text-xs text-muted-foreground">
                <span className="font-mono">{task.id.slice(0, 8)}</span>
                {task.completedAt && <span>Duration: {(task.completedAt - task.createdAt).toFixed(1)}s</span>}
            </div>
        </div>
    );

    const renderDeliveryCard = (delivery: Delivery) => (
        <div key={delivery.id} className="flex flex-col p-3 rounded-lg border border-border bg-card/50 hover:bg-card/80 transition-colors mb-2">
            <div className="flex justify-between items-start mb-1">
                <div className="flex items-center gap-2">
                    <span className="font-medium text-sm capitalize">Delivery to {delivery.channel}</span>
                    <Badge variant={delivery.status === "failed" ? "destructive" : "secondary"} className="text-xs">
                        {delivery.status}
                    </Badge>
                </div>
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                    {formatTime(delivery.createdAt)}
                    {getStatusIcon(delivery.status)}
                </div>
            </div>
            <p className="text-sm text-foreground/80 break-words italic">"{delivery.preview}"</p>
            {delivery.error && (
                <div className="mt-2 p-2 rounded bg-red-500/10 border border-red-500/20 text-xs text-red-500 font-mono break-all">
                    {delivery.error}
                </div>
            )}
            <div className="mt-2 flex justify-between text-xs text-muted-foreground">
                <span className="font-mono">{delivery.id.slice(0, 8)}</span>
                <span>{delivery.messageKind}</span>
            </div>
        </div>
    );

    return (
        <div className="h-full flex flex-col p-6 max-w-7xl mx-auto w-full gap-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Task Queue</h1>
                    <p className="text-muted-foreground mt-1">Real-time observability of agent operations and outbound deliveries.</p>
                </div>
                <div className="flex items-center gap-4">
                    <div className="flex items-center gap-2">
                        <span className="text-sm text-muted-foreground">Live Updates</span>
                        <div
                            className={`h-5 w-10 cursor-pointer rounded-full p-1 transition-colors ${autoRefresh ? "bg-primary" : "bg-muted"}`}
                            onClick={() => setAutoRefresh(!autoRefresh)}
                        >
                            <div className={`w-3 h-3 bg-white rounded-full transition-transform ${autoRefresh ? "translate-x-5" : "translate-x-0"}`} />
                        </div>
                    </div>
                    <button
                        onClick={fetchData}
                        className="p-2 hover:bg-accent rounded-full transition-colors"
                        disabled={loading}
                    >
                        <RefreshCw className={`w-5 h-5 ${loading ? "animate-spin" : ""}`} />
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 h-[calc(100vh-180px)] min-h-[600px]">
                <Card className="flex flex-col h-full border-border/50 shadow-sm overflow-hidden">
                    <CardHeader className="bg-card pb-4 shrink-0">
                        <CardTitle className="flex items-center gap-2">
                            <ActivitySquare className="w-5 h-5 text-blue-500" />
                            Agent Work
                            {activeTasks.length > 0 && (
                                <Badge variant="secondary" className="bg-blue-500/10 text-blue-500 ml-2">
                                    {activeTasks.length} Active
                                </Badge>
                            )}
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="flex-1 overflow-hidden p-0">
                        <Tabs defaultValue="active" className="h-full flex flex-col">
                            <div className="px-6 border-b border-border/50 bg-card">
                                <TabsList className="bg-transparent mb-0 h-10 w-full justify-start">
                                    <TabsTrigger value="active" className="data-[state=active]:bg-transparent data-[state=active]:border-b-2 data-[state=active]:border-blue-500 rounded-none px-4">
                                        In-Flight ({activeTasks.length})
                                    </TabsTrigger>
                                    <TabsTrigger value="past" className="data-[state=active]:bg-transparent data-[state=active]:border-b-2 data-[state=active]:border-blue-500 rounded-none px-4">
                                        Recent History ({completedTasks.length})
                                    </TabsTrigger>
                                </TabsList>
                            </div>

                            <TabsContent value="active" className="flex-1 overflow-hidden m-0">
                                <ScrollArea className="h-full w-full p-4">
                                    {activeTasks.length === 0 ? (
                                        <div className="h-40 flex flex-col items-center justify-center text-muted-foreground gap-2">
                                            <CheckCircle2 className="w-8 h-8 opacity-20" />
                                            <p>No active tasks</p>
                                        </div>
                                    ) : (
                                        activeTasks.map(renderTaskCard)
                                    )}
                                </ScrollArea>
                            </TabsContent>

                            <TabsContent value="past" className="flex-1 overflow-hidden m-0">
                                <ScrollArea className="h-full w-full p-4">
                                    {completedTasks.length === 0 ? (
                                        <div className="h-40 flex flex-col items-center justify-center text-muted-foreground">
                                            <p>No task history</p>
                                        </div>
                                    ) : (
                                        completedTasks.slice(0, 50).map(renderTaskCard)
                                    )}
                                </ScrollArea>
                            </TabsContent>
                        </Tabs>
                    </CardContent>
                </Card>

                <Card className="flex flex-col h-full border-border/50 shadow-sm overflow-hidden">
                    <CardHeader className="bg-card pb-4 shrink-0">
                        <CardTitle className="flex items-center gap-2">
                            <Server className="h-5 w-5 text-primary" />
                            Outbound Deliveries
                            {activeDeliveries.length > 0 && (
                                <Badge variant="secondary" className="ml-2 bg-primary/10 text-primary">
                                    {activeDeliveries.length} Pending
                                </Badge>
                            )}
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="flex-1 overflow-hidden p-0">
                        <Tabs defaultValue="active" className="h-full flex flex-col">
                            <div className="px-6 border-b border-border/50 bg-card">
                                <TabsList className="bg-transparent mb-0 h-10 w-full justify-start">
                                    <TabsTrigger value="active" className="rounded-none px-4 data-[state=active]:border-b-2 data-[state=active]:border-primary data-[state=active]:bg-transparent">
                                        Queue ({activeDeliveries.length})
                                    </TabsTrigger>
                                    <TabsTrigger value="past" className="rounded-none px-4 data-[state=active]:border-b-2 data-[state=active]:border-primary data-[state=active]:bg-transparent">
                                        Sent ({pastDeliveries.length})
                                    </TabsTrigger>
                                </TabsList>
                            </div>

                            <TabsContent value="active" className="flex-1 overflow-hidden m-0">
                                <ScrollArea className="h-full w-full p-4">
                                    {activeDeliveries.length === 0 ? (
                                        <div className="h-40 flex flex-col items-center justify-center text-muted-foreground gap-2">
                                            <CheckCircle2 className="w-8 h-8 opacity-20" />
                                            <p>Queue is empty</p>
                                        </div>
                                    ) : (
                                        activeDeliveries.map(renderDeliveryCard)
                                    )}
                                </ScrollArea>
                            </TabsContent>

                            <TabsContent value="past" className="flex-1 overflow-hidden m-0">
                                <ScrollArea className="h-full w-full p-4">
                                    {pastDeliveries.length === 0 ? (
                                        <div className="h-40 flex flex-col items-center justify-center text-muted-foreground">
                                            <p>No delivery history</p>
                                        </div>
                                    ) : (
                                        pastDeliveries.slice(0, 50).map(renderDeliveryCard)
                                    )}
                                </ScrollArea>
                            </TabsContent>
                        </Tabs>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
