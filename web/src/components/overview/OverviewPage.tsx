import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import {
    Copy,
    Download,
    Eraser,
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
import {
    FactRow,
    PageBody,
    PageFrame,
    QuietButton,
    RefreshButton,
    RequestError,
    SectionHead,
    SkeletonRows,
    StatusDot,
    StatusGlyph,
} from "@/components/ops/ops-ui";
import { toRowState, toneText, type Tone } from "@/lib/ops-status";

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

interface HealthTileProps {
    label: string;
    value: string;
    detail: string;
    tone?: Tone;
}

function HealthTile({ label, value, detail, tone = "neutral" }: HealthTileProps) {
    return (
        <div className="min-w-0 border-border/60 pl-4 max-lg:odd:border-l-0 max-lg:odd:pl-0 lg:first:border-l-0 lg:first:pl-0 border-l">
            <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <StatusDot tone={tone} />
                {label}
            </div>
            <div className="mt-1.5 truncate text-xl font-semibold tracking-tight text-foreground" title={value}>{value}</div>
            <div className={cn("mt-0.5 truncate text-xs", tone === "error" ? "text-red-500" : "text-muted-foreground")} title={detail}>{detail}</div>
        </div>
    );
}

/** Channel health uses the same glyphs as tool rows: only real failures are red. */
function channelGlyphState(status: string) {
    if (/fail|error/i.test(status)) return "failed" as const;
    if (/^(connecting|starting|initiali[sz]ing|reconnecting)/i.test(status)) return "running" as const;
    return toRowState(status);
}

function ActionRow({
    label,
    hint,
    icon,
    tone = "default",
    onClick,
    disabled,
}: {
    label: string;
    hint: string;
    icon: React.ReactNode;
    tone?: "default" | "danger";
    onClick: () => void;
    disabled?: boolean;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={disabled}
            className="group -mx-2 flex w-[calc(100%+1rem)] items-center gap-3 rounded-md px-2 py-2 text-left transition-colors hover:bg-foreground/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
            <span className={cn("min-w-0 flex-1", tone === "danger" ? "text-red-500" : "text-foreground")}>
                <span className="block text-sm font-medium">{label}</span>
                <span className="block truncate text-xs text-muted-foreground">{hint}</span>
            </span>
            <span className={cn("shrink-0 [&_svg]:h-4 [&_svg]:w-4", tone === "danger" ? "text-red-500" : "text-muted-foreground group-hover:text-foreground")}>{icon}</span>
        </button>
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
    const [statsError, setStatsError] = useState<unknown>(null);
    const [refreshing, setRefreshing] = useState(false);
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
        return axios.get(`${API_BASE_URL}/api/stats`)
            .then((res) => {
                setStats(res.data);
                setStatsError(null);
            })
            .catch((error: unknown) => {
                setStatsError(error);
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
        setRefreshing(true);
        void fetchStats().finally(() => setRefreshing(false));
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
        const interval = setInterval(() => void fetchStats(), 5000);
        return () => clearInterval(interval);
    }, [fetchStats, refreshAll]);

    const strongestRelationship = personaStatus?.relationships?.[0];
    const styleCoverage = [
        personaStatus?.web_style,
        personaStatus?.discord_style,
        personaStatus?.telegram_style,
        personaStatus?.whatsapp_style,
    ].filter(Boolean).length;
    const llmTone: Tone = llmHealth?.status === "Healthy" ? "ready" : llmHealth?.status === "Error" || llmHealth?.status === "Quota Exceeded" ? "error" : "warning";
    const sessionCount = stats?.sessions_count ?? stats?.sessions ?? 0;
    const connectedChannels = stats?.channels?.filter((channel) => channel.status === "Connected").length || 0;
    const cronRunning = stats?.cron_status?.toLowerCase() === "running";
    const pageActions = (
        <>
            <span className="hidden items-center gap-1.5 text-[11px] text-muted-foreground sm:inline-flex">
                <StatusDot tone={statsError ? "warning" : "ready"} pulse={!statsError} />
                {statsError ? "Not updating" : "Live, every 5s"}
            </span>
            <RefreshButton onClick={refreshAll} busy={refreshing || checkingLLM} />
        </>
    );

    if (loading && !stats && !statsError) {
        return (
            <PageFrame title="Overview" description="Runtime health, connected services and maintenance." actions={pageActions}>
                <PageBody>
                    <SkeletonRows rows={6} className="-mx-4" />
                </PageBody>
            </PageFrame>
        );
    }

    return (
        <PageFrame title="Overview" description="Runtime health, connected services and maintenance." actions={pageActions}>
            <PageBody className="space-y-8 py-6">
                {statsError ? (
                    <RequestError error={statsError} what="runtime stats" onRetry={refreshAll} retrying={refreshing} stale={!!stats} />
                ) : null}

                <section aria-label="Runtime summary" className="grid grid-cols-2 gap-y-6 lg:grid-cols-4">
                    <HealthTile
                        label="Runtime"
                        value={stats ? "Online" : statsError ? "Unreachable" : "Checking"}
                        detail={stats ? `${formatUptime(stats.uptime || 0)} uptime` : statsError ? "No answer from the backend" : "Waiting for stats"}
                        tone={stats ? "ready" : statsError ? "error" : "neutral"}
                    />
                    <HealthTile
                        label="Model"
                        value={llmHealth?.model && llmHealth.model !== "Unknown" ? llmHealth.model : llmHealth?.status || "Checking"}
                        detail={
                            llmHealth?.error
                                ? llmHealth.error
                                : llmHealth?.latency_ms
                                    ? `${llmHealth.latency_ms} ms response`
                                    : checkingLLM
                                        ? "Checking connection"
                                        : llmHealth?.status || "Waiting for health check"
                        }
                        tone={llmHealth ? llmTone : "neutral"}
                    />
                    <HealthTile
                        label="Activity"
                        value={`${sessionCount} session${sessionCount === 1 ? "" : "s"}`}
                        detail={`${stats?.instances_count || 0} instances · ${stats?.subagents_count || 0} sub-agents`}
                    />
                    <HealthTile
                        label="Automation"
                        value={stats?.cron_status || "Unknown"}
                        detail={`${connectedChannels} connected channel${connectedChannels === 1 ? "" : "s"}`}
                        tone={cronRunning ? "ready" : "neutral"}
                    />
                </section>

                <div className="grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
                    <div className="space-y-8">
                        <section>
                            <SectionHead title="Channels" hint="Connections available to LimeBot" />
                            {stats?.channels?.length ? (
                                <ul className="mt-1 divide-y divide-border/50 border-y border-border/50">
                                    {stats.channels.map((channel) => (
                                        <li key={`${channel.name}-${channel.type}`} className="flex items-center gap-3 py-2.5">
                                            <StatusGlyph state={channelGlyphState(channel.status)} />
                                            <div className="min-w-0 flex-1">
                                                <div className="truncate text-sm font-medium capitalize text-foreground">{channel.name}</div>
                                                <div className="truncate text-xs text-muted-foreground">{channel.type}</div>
                                            </div>
                                            <span className={cn("shrink-0 text-xs font-medium", toneText(channel.status === "Connected" ? "ready" : /fail|error/i.test(channel.status) ? "error" : "neutral"))}>
                                                {channel.status}
                                            </span>
                                        </li>
                                    ))}
                                </ul>
                            ) : (
                                <p className="mt-1 border-y border-dashed border-border/60 py-6 text-center text-sm text-muted-foreground">
                                    {statsError && !stats ? "Channel status is unavailable until the backend answers." : "No channels are configured yet. Add one from the Channels page."}
                                </p>
                            )}
                        </section>

                        <section>
                            <SectionHead title="Gateway access" hint="For companion apps and remote clients" />
                            <dl className="mt-1 border-t border-border/50">
                                <FactRow label="WebSocket endpoint" title={stats?.gateway_url}>
                                    <code className="font-mono text-xs">{stats?.gateway_url || "Not available"}</code>
                                </FactRow>
                                <FactRow label="Authentication">
                                    <span className={cn("inline-flex items-center gap-1.5", toneText(gatewayTokenLabel ? "ready" : "warning"))}>
                                        <StatusDot tone={gatewayTokenLabel ? "ready" : "warning"} />
                                        {gatewayTokenLabel ? `API key ${gatewayTokenLabel}` : "No key cached in this browser"}
                                    </span>
                                </FactRow>
                            </dl>
                            <div className="mt-3 flex flex-wrap gap-2">
                                <QuietButton tone="primary" onClick={connectClient} disabled={actionBusy !== null || !stats?.gateway_url}>
                                    <Copy />
                                    Copy client URL
                                </QuietButton>
                                <QuietButton onClick={generateNewToken} disabled={actionBusy !== null}>
                                    <RefreshCw className={cn(actionBusy === "token" && "animate-spin")} />
                                    Rotate token
                                </QuietButton>
                            </div>
                        </section>
                    </div>

                    <div className="space-y-8">
                        <section>
                            <SectionHead title="Persona" hint="Loaded by the agent" />
                            <div className="mt-2 flex items-center gap-3">
                                {identity?.avatar ? (
                                    <img src={identity.avatar} alt="" className="h-10 w-10 rounded-full border border-border object-cover" />
                                ) : (
                                    <div className="flex h-10 w-10 items-center justify-center rounded-full border border-border bg-foreground/10">
                                        <User className="h-4 w-4 text-muted-foreground" />
                                    </div>
                                )}
                                <div className="min-w-0 flex-1">
                                    <div className="truncate text-sm font-semibold text-foreground">{identity?.name || "LimeBot"}</div>
                                    <div className={cn("inline-flex items-center gap-1.5 text-xs font-medium", toneText(!setupStatus ? "neutral" : setupStatus.persona_ready ? "ready" : "warning"))}>
                                        <StatusDot tone={!setupStatus ? "neutral" : setupStatus.persona_ready ? "ready" : "warning"} />
                                        {!setupStatus ? "Status unknown" : setupStatus.persona_ready ? "Ready" : "Setup incomplete"}
                                    </div>
                                </div>
                            </div>

                            <dl className="mt-3 border-t border-border/50">
                                <FactRow label="Dynamic personality">{personaStatus?.enable_dynamic_personality ? "Enabled" : "Disabled"}</FactRow>
                                <FactRow label="Mood" title={personaStatus?.mood}>{personaStatus?.mood?.trim() || "Not set"}</FactRow>
                                <FactRow label="Closest relationship">{strongestRelationship?.name || "None yet"}</FactRow>
                                <FactRow label="Channel styles">{styleCoverage} of 4</FactRow>
                            </dl>

                            {setupStatus?.persona_missing?.length ? (
                                <p className="mt-3 rounded-lg border-l-2 border-amber-400/70 bg-amber-500/[0.06] py-2 pl-3 pr-2 text-xs text-muted-foreground">
                                    <span className="font-semibold text-foreground">Still missing: </span>
                                    {setupStatus.persona_missing.join(", ")}
                                </p>
                            ) : null}

                            <div className="mt-3 flex gap-2">
                                <QuietButton onClick={exportPersona}>
                                    <Download /> Export
                                </QuietButton>
                                <label className="relative inline-flex">
                                    <span className="inline-flex h-8 cursor-pointer items-center justify-center gap-1.5 rounded-md border border-border px-2.5 text-xs font-medium text-foreground transition-colors focus-within:ring-2 focus-within:ring-ring hover:bg-foreground/[0.07] [&_svg]:h-3.5 [&_svg]:w-3.5">
                                        <Upload /> Import
                                    </span>
                                    <input
                                        type="file"
                                        className="absolute inset-0 cursor-pointer opacity-0"
                                        aria-label="Import persona backup"
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
                            </div>
                        </section>

                        <section>
                            <SectionHead title="Maintenance" hint="Occasional runtime actions" />
                            <div className="mt-1 space-y-0.5">
                                <ActionRow label="Clear tool cache" hint="Drops cached tool results for all sessions" icon={<Eraser />} onClick={() => clearRuntimeData("cache")} />
                                <ActionRow label="Clear system logs" hint="Empties the log buffer shown on the Logs page" icon={<Trash2 />} onClick={() => clearRuntimeData("logs")} />
                                <ActionRow label="Restart backend" hint="Briefly interrupts live sessions" icon={<RotateCcw />} tone="danger" onClick={restartBackend} />
                            </div>
                        </section>
                    </div>
                </div>
            </PageBody>

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
                            className={cn(confirmAction?.tone === "destructive" && "bg-red-600 text-white hover:bg-red-700")}
                        >
                            {confirmAction?.actionLabel || "Confirm"}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </PageFrame>
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
