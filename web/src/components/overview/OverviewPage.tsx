import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import {
    Activity,
    Cpu,
    Download,
    Globe,
    KeyRound,
    RefreshCw,
    RotateCcw,
    Trash2,
    Upload,
    User,
} from "lucide-react";
import { toast } from "sonner";

import { API_BASE_URL } from "@/lib/api";
import { type ConfigApiResponse } from "@/lib/config-secrets";
import { cn } from "@/lib/utils";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

interface Stats {
    uptime: number;
    gateway_url: string;
    channels: Array<{ name: string; type: string; status: string }>;
    sessions: number;
    sessions_count: number;
    instances_count?: number;
    subagents_count?: number;
    cron_status: string;
}

interface LLMHealth {
    status: string;
    latency_ms: number;
    model: string;
    error?: string;
}

interface ConfirmActionState {
    title: string;
    description: string;
    actionLabel: string;
    tone?: "default" | "destructive";
    onConfirm: () => void | Promise<void>;
}

interface PersonaStatus {
    mood?: string;
    enable_dynamic_personality?: boolean;
    relationships?: Array<{ name: string; affinity: number; level: string }>;
    telegram_style?: string;
    discord_style?: string;
    whatsapp_style?: string;
    web_style?: string;
}

interface SetupStatus {
    persona_ready?: boolean;
    persona_missing?: string[];
}

interface SummaryItemProps {
    label: string;
    value: string;
    detail: string;
    status?: "ready" | "warning" | "error" | "neutral";
}

function SummaryItem({ label, value, detail, status = "neutral" }: SummaryItemProps) {
    return (
        <div className="min-w-0 bg-card px-5 py-4 sm:px-6">
            <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <span
                    className={cn(
                        "h-2 w-2 rounded-full",
                        status === "ready" && "bg-primary",
                        status === "warning" && "bg-amber-500",
                        status === "error" && "bg-destructive",
                        status === "neutral" && "bg-muted-foreground/40",
                    )}
                />
                {label}
            </div>
            <div className="mt-2 truncate text-lg font-semibold text-foreground">{value}</div>
            <div className="mt-0.5 truncate text-xs text-muted-foreground" title={detail}>{detail}</div>
        </div>
    );
}

function SectionStatus({ children, tone = "ready" }: { children: React.ReactNode; tone?: "ready" | "warning" | "error" }) {
    return (
        <Badge
            variant="outline"
            className={cn(
                "gap-1.5 font-medium",
                tone === "ready" && "border-primary/25 bg-primary/8 text-primary",
                tone === "warning" && "border-amber-500/25 bg-amber-500/8 text-amber-600 dark:text-amber-400",
                tone === "error" && "border-destructive/25 bg-destructive/8 text-destructive",
            )}
        >
            <span className={cn("h-1.5 w-1.5 rounded-full", tone === "ready" && "bg-primary", tone === "warning" && "bg-amber-500", tone === "error" && "bg-destructive")} />
            {children}
        </Badge>
    );
}

export function OverviewPage() {
    const [identity, setIdentity] = useState<{ name: string; avatar: string | null } | null>(null);
    const [personaStatus, setPersonaStatus] = useState<PersonaStatus | null>(null);
    const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
    const [stats, setStats] = useState<Stats | null>(null);
    const [llmHealth, setLlmHealth] = useState<LLMHealth | null>(null);
    const [gatewayTokenLabel, setGatewayTokenLabel] = useState("");
    const [gatewayTokenValue, setGatewayTokenValue] = useState("");
    const [loading, setLoading] = useState(true);
    const [checkingLLM, setCheckingLLM] = useState(false);
    const [actionBusy, setActionBusy] = useState<null | "connect" | "token">(null);
    const [confirmAction, setConfirmAction] = useState<ConfirmActionState | null>(null);

    const fetchIdentity = useCallback(() => {
        axios.get(`${API_BASE_URL}/api/identity`)
            .then((res) => setIdentity(res.data))
            .catch((error: unknown) => {
                if (!isUnauthorized(error)) console.error("Failed to fetch identity:", error);
            });
    }, []);

    const fetchStats = useCallback(() => {
        axios.get(`${API_BASE_URL}/api/stats`)
            .then((res) => setStats(res.data))
            .catch((error: unknown) => {
                if (!isUnauthorized(error)) console.error("Failed to fetch stats:", error);
            })
            .finally(() => setLoading(false));
    }, []);

    const checkLLM = useCallback(() => {
        setCheckingLLM(true);
        axios.get(`${API_BASE_URL}/api/llm/health`)
            .then((res) => setLlmHealth(res.data))
            .catch((error: unknown) => setLlmHealth({ status: "Error", latency_ms: 0, model: "Unknown", error: str(error) }))
            .finally(() => setCheckingLLM(false));
    }, []);

    const fetchGatewayConfig = useCallback(() => {
        axios.get<ConfigApiResponse>(`${API_BASE_URL}/api/config`)
            .then((res) => {
                setGatewayTokenLabel(res.data?.secrets?.APP_API_KEY?.masked || "");
                setGatewayTokenValue(localStorage.getItem("limebot_api_key") || "");
            })
            .catch(() => {
                setGatewayTokenLabel("");
                setGatewayTokenValue(localStorage.getItem("limebot_api_key") || "");
            });
    }, []);

    const fetchPersonaStatus = useCallback(() => {
        axios.get(`${API_BASE_URL}/api/persona`)
            .then((res) => setPersonaStatus(res.data))
            .catch((error: unknown) => {
                if (!isUnauthorized(error)) console.error("Failed to fetch persona status:", error);
            });

        axios.get(`${API_BASE_URL}/api/setup/status`)
            .then((res) => setSetupStatus(res.data))
            .catch(() => setSetupStatus(null));
    }, []);

    const refreshAll = useCallback(() => {
        fetchStats();
        fetchIdentity();
        fetchGatewayConfig();
        fetchPersonaStatus();
        checkLLM();
    }, [checkLLM, fetchGatewayConfig, fetchIdentity, fetchPersonaStatus, fetchStats]);

    const openConfirm = (action: ConfirmActionState) => setConfirmAction(action);

    const runConfirmAction = async () => {
        if (!confirmAction) return;
        const action = confirmAction;
        setConfirmAction(null);
        await action.onConfirm();
    };

    const restartBackend = () => {
        openConfirm({
            title: "Restart backend?",
            description: "This will restart the backend service and briefly interrupt live sessions.",
            actionLabel: "Restart backend",
            tone: "destructive",
            onConfirm: async () => {
                try {
                    await axios.post(`${API_BASE_URL}/api/control/restart`);
                    toast.success("Backend restart requested");
                } catch (error) {
                    toast.error("Failed to restart backend", { description: str(error) });
                }
            },
        });
    };

    const clearRuntimeData = (kind: "cache" | "logs") => {
        const isCache = kind === "cache";
        openConfirm({
            title: isCache ? "Clear tool cache?" : "Clear system logs?",
            description: isCache
                ? "Cached tool results will be removed for all active sessions."
                : "This removes the current log buffer from the dashboard.",
            actionLabel: isCache ? "Clear cache" : "Clear logs",
            tone: "destructive",
            onConfirm: async () => {
                try {
                    const res = await axios.post(`${API_BASE_URL}/api/control/clear-${kind}`);
                    toast.success(isCache ? "Cache cleared" : "Logs cleared", {
                        description: res.data.message || (isCache ? "Cache cleared." : "Logs cleared."),
                    });
                } catch (error) {
                    toast.error(isCache ? "Failed to clear cache" : "Failed to clear logs", { description: str(error) });
                }
            },
        });
    };

    const connectClient = async () => {
        if (!stats?.gateway_url) return;
        setActionBusy("connect");
        const storedKey = gatewayTokenValue || localStorage.getItem("limebot_api_key") || "";

        if (gatewayTokenLabel && !storedKey) {
            toast.error("Gateway token required", {
                description: "This server requires APP_API_KEY, but this browser has no cached token. Rotate or re-enter a token before copying the client URL.",
            });
            setActionBusy(null);
            return;
        }

        let wsWithKey = stats.gateway_url;
        if (storedKey) {
            try {
                const url = new URL(stats.gateway_url);
                url.searchParams.set("api_key", storedKey);
                wsWithKey = url.toString();
            } catch {
                const separator = stats.gateway_url.includes("?") ? "&" : "?";
                wsWithKey = `${stats.gateway_url}${separator}api_key=${encodeURIComponent(storedKey)}`;
            }
        }

        try {
            await navigator.clipboard.writeText(wsWithKey);
            toast.success("Client URL copied", { description: "WebSocket endpoint copied to clipboard." });
        } catch {
            toast.error("Clipboard unavailable", { description: wsWithKey });
        } finally {
            setActionBusy(null);
        }
    };

    const generateNewToken = () => {
        openConfirm({
            title: "Rotate gateway token?",
            description: "Existing clients will be disconnected after the backend restarts with the new APP_API_KEY.",
            actionLabel: "Rotate token",
            tone: "destructive",
            onConfirm: async () => {
                setActionBusy("token");
                const newToken = crypto.randomUUID();
                try {
                    await axios.post(`${API_BASE_URL}/api/config`, { env: { APP_API_KEY: newToken } });
                    localStorage.setItem("limebot_api_key", newToken);
                    axios.defaults.headers.common["X-API-Key"] = newToken;
                    setGatewayTokenValue(newToken);
                    setGatewayTokenLabel(`••••${newToken.slice(-4)}`);
                    toast.success("Token rotated", { description: "APP_API_KEY updated. Backend is restarting." });
                } catch (error) {
                    toast.error("Failed to rotate token", { description: str(error) });
                } finally {
                    setActionBusy(null);
                }
            },
        });
    };

    const exportPersona = async () => {
        try {
            const res = await axios.get(`${API_BASE_URL}/api/persona/export`);
            const data = res.data;
            if (data.error) throw new Error(data.error);
            const blob = new Blob([data.content], { type: "text/markdown" });
            const url = window.URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = data.filename || "limebot_persona.md";
            anchor.click();
            window.URL.revokeObjectURL(url);
            toast.success("Persona exported", { description: anchor.download });
        } catch (error) {
            toast.error("Export failed", { description: str(error) });
        }
    };

    const importPersonaFile = (file: File) => {
        const reader = new FileReader();
        reader.onload = async (event) => {
            try {
                const res = await axios.post(`${API_BASE_URL}/api/persona/import`, { content: event.target?.result });
                if (res.data.error) throw new Error(res.data.error);
                toast.success("Persona imported", { description: res.data.message });
                window.location.reload();
            } catch (error) {
                toast.error("Import failed", { description: str(error) });
            }
        };
        reader.readAsText(file);
    };

    useEffect(() => {
        refreshAll();
        const interval = setInterval(fetchStats, 5000);
        return () => clearInterval(interval);
    }, [fetchStats, refreshAll]);

    const strongestRelationship = personaStatus?.relationships?.[0];
    const styleCoverage = [
        personaStatus?.web_style,
        personaStatus?.discord_style,
        personaStatus?.telegram_style,
        personaStatus?.whatsapp_style,
    ].filter(Boolean).length;
    const llmTone = llmHealth?.status === "Healthy" ? "ready" : llmHealth?.status === "Error" || llmHealth?.status === "Quota Exceeded" ? "error" : "warning";
    const sessionCount = stats?.sessions_count ?? stats?.sessions ?? 0;

    if (loading && !stats) {
        return (
            <div className="flex h-full min-h-[50vh] items-center justify-center bg-background">
                <RefreshCw className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
        );
    }

    return (
        <div className="h-full overflow-y-auto bg-background">
            <div className="mx-auto max-w-7xl space-y-5 px-4 py-5 sm:px-6 lg:px-8 lg:py-7">
                <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                    <div>
                        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Overview</h1>
                        <p className="mt-1 text-sm text-muted-foreground">Runtime health, connected services, and maintenance.</p>
                    </div>
                    <div className="flex items-center gap-3">
                        <span className="hidden text-xs text-muted-foreground sm:inline">Runtime updates every 5 seconds</span>
                        <Button variant="outline" size="sm" onClick={refreshAll}>
                            <RefreshCw className={cn((loading || checkingLLM) && "animate-spin")} />
                            Refresh
                        </Button>
                    </div>
                </header>

                <Card className="overflow-hidden shadow-none">
                    <CardContent className="grid gap-px bg-border p-0 sm:grid-cols-2 xl:grid-cols-4">
                        <SummaryItem label="Runtime" value="Online" detail={`${formatUptime(stats?.uptime || 0)} uptime`} status="ready" />
                        <SummaryItem
                            label="Model"
                            value={llmHealth?.model || "Checking connection"}
                            detail={llmHealth?.latency_ms ? `${llmHealth.latency_ms} ms response` : llmHealth?.status || "Waiting for health check"}
                            status={llmTone}
                        />
                        <SummaryItem
                            label="Activity"
                            value={`${sessionCount} session${sessionCount === 1 ? "" : "s"}`}
                            detail={`${stats?.instances_count || 0} instances · ${stats?.subagents_count || 0} sub-agents`}
                            status="neutral"
                        />
                        <SummaryItem
                            label="Automation"
                            value={stats?.cron_status || "Unknown"}
                            detail={`${stats?.channels?.filter((channel) => channel.status === "Connected").length || 0} connected channels`}
                            status={stats?.cron_status?.toLowerCase() === "running" ? "ready" : "neutral"}
                        />
                    </CardContent>
                </Card>

                <div className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
                    <div className="space-y-5">
                        <Card className="shadow-none">
                            <CardHeader className="flex-row items-start justify-between space-y-0 pb-3">
                                <div>
                                    <CardTitle className="text-base">Channels</CardTitle>
                                    <CardDescription className="mt-1">Connections currently available to LimeBot.</CardDescription>
                                </div>
                                <Globe className="h-4 w-4 text-muted-foreground" />
                            </CardHeader>
                            <CardContent>
                                {stats?.channels?.length ? (
                                    <div className="divide-y rounded-md border">
                                        {stats.channels.map((channel) => {
                                            const connected = channel.status === "Connected";
                                            return (
                                                <div key={`${channel.name}-${channel.type}`} className="flex items-center justify-between gap-4 px-4 py-3">
                                                    <div className="min-w-0">
                                                        <div className="truncate text-sm font-medium capitalize text-foreground">{channel.name}</div>
                                                        <div className="mt-0.5 truncate text-xs text-muted-foreground">{channel.type}</div>
                                                    </div>
                                                    <SectionStatus tone={connected ? "ready" : "warning"}>{channel.status}</SectionStatus>
                                                </div>
                                            );
                                        })}
                                    </div>
                                ) : (
                                    <div className="rounded-md border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
                                        No channels are configured yet.
                                    </div>
                                )}
                            </CardContent>
                        </Card>

                        <Card className="shadow-none">
                            <CardHeader>
                                <div className="flex items-center gap-2">
                                    <KeyRound className="h-4 w-4 text-muted-foreground" />
                                    <CardTitle className="text-base">Gateway access</CardTitle>
                                </div>
                                <CardDescription>Connection details for companion apps and remote clients.</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                <div className="grid gap-4 md:grid-cols-2">
                                    <div className="space-y-1.5">
                                        <div className="text-xs font-medium text-muted-foreground">WebSocket endpoint</div>
                                        <code className="block truncate rounded-md border bg-muted/30 px-3 py-2.5 text-xs text-foreground" title={stats?.gateway_url}>
                                            {stats?.gateway_url || "Not available"}
                                        </code>
                                    </div>
                                    <div className="space-y-1.5">
                                        <div className="text-xs font-medium text-muted-foreground">Authentication</div>
                                        <div className="flex h-[38px] items-center justify-between rounded-md border px-3 text-sm">
                                            <span>{gatewayTokenLabel ? "API key configured" : "No key cached"}</span>
                                            <SectionStatus tone={gatewayTokenLabel ? "ready" : "warning"}>{gatewayTokenLabel || "Setup needed"}</SectionStatus>
                                        </div>
                                    </div>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <Button size="sm" onClick={connectClient} disabled={actionBusy !== null || !stats?.gateway_url}>
                                        <Globe />
                                        Copy client URL
                                    </Button>
                                    <Button variant="outline" size="sm" onClick={generateNewToken} disabled={actionBusy !== null}>
                                        <RefreshCw className={cn(actionBusy === "token" && "animate-spin")} />
                                        Rotate token
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>
                    </div>

                    <div className="space-y-5">
                        <Card className="shadow-none">
                            <CardHeader className="pb-4">
                                <CardTitle className="text-base">Persona</CardTitle>
                                <CardDescription>Identity and behavior currently loaded by the agent.</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-5">
                                <div className="flex items-center gap-3">
                                    {identity?.avatar ? (
                                        <img src={identity.avatar} alt="" className="h-11 w-11 rounded-full border object-cover" />
                                    ) : (
                                        <div className="flex h-11 w-11 items-center justify-center rounded-full border bg-muted">
                                            <User className="h-5 w-5 text-muted-foreground" />
                                        </div>
                                    )}
                                    <div className="min-w-0 flex-1">
                                        <div className="truncate font-medium text-foreground">{identity?.name || "LimeBot"}</div>
                                        <div className="mt-1">
                                            <SectionStatus tone={setupStatus?.persona_ready ? "ready" : "warning"}>
                                                {setupStatus?.persona_ready ? "Ready" : "Setup incomplete"}
                                            </SectionStatus>
                                        </div>
                                    </div>
                                </div>

                                <dl className="divide-y rounded-md border text-sm">
                                    <div className="flex items-start justify-between gap-4 px-3 py-2.5">
                                        <dt className="text-muted-foreground">Dynamic personality</dt>
                                        <dd className="font-medium">{personaStatus?.enable_dynamic_personality ? "Enabled" : "Disabled"}</dd>
                                    </div>
                                    <div className="flex items-start justify-between gap-4 px-3 py-2.5">
                                        <dt className="text-muted-foreground">Mood</dt>
                                        <dd className="max-w-[60%] truncate text-right font-medium" title={personaStatus?.mood}>{personaStatus?.mood?.trim() || "Not set"}</dd>
                                    </div>
                                    <div className="flex items-start justify-between gap-4 px-3 py-2.5">
                                        <dt className="text-muted-foreground">Closest relationship</dt>
                                        <dd className="text-right font-medium">{strongestRelationship?.name || "None yet"}</dd>
                                    </div>
                                    <div className="flex items-start justify-between gap-4 px-3 py-2.5">
                                        <dt className="text-muted-foreground">Channel styles</dt>
                                        <dd className="font-medium">{styleCoverage} of 4</dd>
                                    </div>
                                </dl>

                                {setupStatus?.persona_missing?.length ? (
                                    <p className="rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                                        Missing: {setupStatus.persona_missing.join(", ")}
                                    </p>
                                ) : null}

                                <div className="flex gap-2 border-t pt-4">
                                    <Button variant="outline" size="sm" className="flex-1" onClick={exportPersona}>
                                        <Download /> Export
                                    </Button>
                                    <Button variant="outline" size="sm" className="relative flex-1" asChild>
                                        <label>
                                            <Upload /> Import
                                            <input
                                                type="file"
                                                className="sr-only"
                                                accept=".md,.markdown"
                                                onChange={(event) => {
                                                    const file = event.target.files?.[0];
                                                    if (!file) return;
                                                    openConfirm({
                                                        title: "Import persona backup?",
                                                        description: "This will overwrite the current Identity and Soul files. A backup will be created automatically.",
                                                        actionLabel: "Import persona",
                                                        tone: "destructive",
                                                        onConfirm: async () => importPersonaFile(file),
                                                    });
                                                    event.target.value = "";
                                                }}
                                            />
                                        </label>
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>

                        <Card className="shadow-none">
                            <CardHeader>
                                <div className="flex items-center gap-2">
                                    <Activity className="h-4 w-4 text-muted-foreground" />
                                    <CardTitle className="text-base">Maintenance</CardTitle>
                                </div>
                                <CardDescription>Occasional runtime and diagnostic actions.</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-2">
                                <Button variant="outline" className="w-full justify-between" onClick={() => clearRuntimeData("cache")}>
                                    Clear tool cache
                                    <RefreshCw />
                                </Button>
                                <Button variant="outline" className="w-full justify-between" onClick={() => clearRuntimeData("logs")}>
                                    Clear system logs
                                    <Trash2 />
                                </Button>
                                <Button variant="destructive" className="w-full justify-between" onClick={restartBackend}>
                                    Restart backend
                                    <RotateCcw />
                                </Button>
                            </CardContent>
                        </Card>

                        <div className="flex items-center gap-2 px-1 text-xs text-muted-foreground">
                            <Cpu className="h-3.5 w-3.5" />
                            <span className="truncate">Model check: {checkingLLM ? "in progress" : llmHealth?.status || "unavailable"}</span>
                        </div>
                    </div>
                </div>
            </div>

            <AlertDialog open={confirmAction !== null} onOpenChange={(open) => !open && setConfirmAction(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{confirmAction?.title}</AlertDialogTitle>
                        <AlertDialogDescription>{confirmAction?.description}</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={runConfirmAction}
                            className={cn(confirmAction?.tone === "destructive" && "bg-destructive text-destructive-foreground hover:bg-destructive/90")}
                        >
                            {confirmAction?.actionLabel || "Confirm"}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}

function formatUptime(seconds: number) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const parts = [];
    if (days) parts.push(`${days}d`);
    if (hours) parts.push(`${hours}h`);
    if (minutes) parts.push(`${minutes}m`);
    if (!parts.length) parts.push(`${Math.floor(seconds % 60)}s`);
    return parts.join(" ");
}

function isUnauthorized(error: unknown) {
    return axios.isAxiosError(error) && error.response?.status === 401;
}

function str(error: unknown): string {
    if (typeof error === "string") return error;
    if (error instanceof Error) return error.message;
    return String(error);
}
