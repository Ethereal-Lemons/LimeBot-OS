import { useEffect, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Activity, Zap, Globe, Cpu, RefreshCw, Power, RotateCcw, Download, Upload, User, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { type ConfigApiResponse } from "@/lib/config-secrets";
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

interface Stats {
    uptime: number;
    gateway_url: string;
    channels: Array<{ name: string, type: string, status: string }>;
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

export function OverviewPage() {
    const [identity, setIdentity] = useState<{ name: string, avatar: string | null } | null>(null);
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

    const fetchIdentity = () => {
        axios.get(`${API_BASE_URL}/api/identity`)
            .then(res => setIdentity(res.data))
            .catch(err => {
                if (err.response?.status !== 401) {
                    console.error("Failed to fetch identity:", err);
                }
            });
    };

    const fetchStats = () => {
        axios.get(`${API_BASE_URL}/api/stats`)
            .then(res => {
                setStats(res.data);
                setLoading(false);
            })
            .catch(err => {
                if (err.response?.status !== 401) {
                    console.error("Failed to fetch stats:", err);
                }
                setLoading(false);
            });
    };

    const checkLLM = () => {
        setCheckingLLM(true);
        axios.get(`${API_BASE_URL}/api/llm/health`)
            .then(res => {
                setLlmHealth(res.data);
                setCheckingLLM(false);
            })
            .catch(err => {
                setLlmHealth({ status: "Error", latency_ms: 0, model: "Unknown", error: str(err) });
                setCheckingLLM(false);
            });
    };

    const fetchGatewayConfig = () => {
        axios.get<ConfigApiResponse>(`${API_BASE_URL}/api/config`)
            .then(res => {
                const storedKey = localStorage.getItem("limebot_api_key") || "";
                setGatewayTokenLabel(res?.data?.secrets?.APP_API_KEY?.masked || "");
                setGatewayTokenValue(storedKey);
            })
            .catch(() => {
                setGatewayTokenLabel("");
                setGatewayTokenValue(localStorage.getItem("limebot_api_key") || "");
            });
    };

    const fetchPersonaStatus = () => {
        axios.get(`${API_BASE_URL}/api/persona`)
            .then(res => setPersonaStatus(res.data))
            .catch(err => {
                if (err.response?.status !== 401) {
                    console.error("Failed to fetch persona status:", err);
                }
            });

        axios.get(`${API_BASE_URL}/api/setup/status`)
            .then(res => setSetupStatus(res.data))
            .catch(() => setSetupStatus(null));
    };

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
                } catch (err) {
                    console.error(err);
                    toast.error("Failed to restart backend", { description: str(err) });
                }
            },
        });
    };

    const connectClient = async () => {
        if (!stats?.gateway_url) return;
        setActionBusy("connect");
        const storedKey = gatewayTokenValue || localStorage.getItem("limebot_api_key") || "";
        const requiresGatewayKey = Boolean(gatewayTokenLabel);

        if (requiresGatewayKey && !storedKey) {
            toast.error("Gateway token required", {
                description: "This server requires APP_API_KEY, but this browser has no cached token. Generate or re-enter a token before copying the client URL.",
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

    const generateNewToken = async () => {
        openConfirm({
            title: "Generate new gateway token?",
            description: "Existing clients will be disconnected after the backend restarts with the new APP_API_KEY.",
            actionLabel: "Generate token",
            tone: "destructive",
            onConfirm: async () => {
                setActionBusy("token");
                const newToken = crypto.randomUUID();
                axios.post(`${API_BASE_URL}/api/config`, { env: { APP_API_KEY: newToken } })
                    .then(() => {
                        localStorage.setItem("limebot_api_key", newToken);
                        axios.defaults.headers.common["X-API-Key"] = newToken;
                        setGatewayTokenValue(newToken);
                        setGatewayTokenLabel(`••••${newToken.slice(-4)}`);
                        toast.success("Token generated", { description: "APP_API_KEY updated. Backend is restarting." });
                    })
                    .catch(err => toast.error("Failed to generate token", { description: str(err) }))
                    .finally(() => setActionBusy(null));
            },
        });
    };

    const importPersonaFile = (file: File) => {
        const reader = new FileReader();
        reader.onload = (event) => {
            axios.post(`${API_BASE_URL}/api/persona/import`, {
                content: event.target?.result
            })
                .then(res => {
                    const data = res.data;
                    if (data.error) throw new Error(data.error);
                    toast.success("Persona imported", { description: data.message });
                    window.location.reload();
                })
                .catch(err => toast.error("Import failed", { description: str(err) }));
        };
        reader.readAsText(file);
    };

    useEffect(() => {
        fetchStats();
        fetchIdentity();
        fetchGatewayConfig();
        fetchPersonaStatus();
        // Initial LLM check
        checkLLM();

        const interval = setInterval(fetchStats, 5000);
        return () => clearInterval(interval);
    }, []);

    const formatUptime = (seconds: number) => {
        const days = Math.floor(seconds / (3600 * 24));
        const hours = Math.floor((seconds % (3600 * 24)) / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);

        const parts = [];
        if (days > 0) parts.push(`${days}d`);
        if (hours > 0) parts.push(`${hours}h`);
        if (minutes > 0) parts.push(`${minutes}m`);
        parts.push(`${secs}s`);

        return parts.join(' ');
    };

    const strongestRelationship = personaStatus?.relationships?.[0];
    const styleCoverage = [
        personaStatus?.web_style,
        personaStatus?.discord_style,
        personaStatus?.telegram_style,
        personaStatus?.whatsapp_style,
    ].filter(Boolean).length;

    if (loading && !stats) {
        return (
            <div className="flex items-center justify-center h-full min-h-[50vh]">
                <RefreshCw className="w-8 h-8 animate-spin text-primary" />
            </div>
        );
    }

    return (
        <div className="h-full overflow-y-auto bg-black/20">
            <div className="space-y-6 p-8">
                <div className="sticky top-0 z-20 -mx-8 mb-6 border-b border-border/60 bg-background/82 px-8 py-4 backdrop-blur-md">
                    <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
                        <div>
                            <h2 className="text-3xl font-bold tracking-tight text-foreground">System Overview</h2>
                            <p className="text-muted-foreground mt-1">Real-time telemetry and control dashboard.</p>
                        </div>
                        <div className="flex items-center gap-2">
                            <button
                                onClick={() => { fetchStats(); checkLLM(); fetchIdentity(); fetchGatewayConfig(); fetchPersonaStatus(); }}
                                className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground bg-card hover:bg-muted border border-border rounded-full transition-all"
                            >
                                <RefreshCw className={cn("w-3.5 h-3.5", (loading || checkingLLM) && "animate-spin")} />
                                Refresh Data
                            </button>
                        </div>
                    </div>
                </div>

                {/* Persona Banner */}
                <div className="bg-card/40 border border-white/5 rounded-2xl p-6 flex items-center gap-6 backdrop-blur-sm overflow-hidden relative">
                <div className="absolute top-0 right-0 p-8 opacity-5">
                    <User className="w-32 h-32" />
                </div>
                <div className="relative shrink-0">
                    <img
                        src={identity?.avatar || "https://via.placeholder.com/150"}
                        alt="Bot Avatar"
                        className="w-20 h-20 rounded-full object-cover border-2 border-primary shadow-lg shadow-primary/20"
                    />
                    <div className="absolute -bottom-1 -right-1 w-6 h-6 bg-green-500 border-4 border-card rounded-full"></div>
                </div>
                <div className="relative space-y-1">
                    <div className="flex items-center gap-3">
                        <h1 className="text-2xl font-bold text-foreground">{identity?.name || "Loading..."}</h1>
                        <span className="bg-primary/10 text-primary text-[10px] font-bold px-2 py-0.5 rounded-full border border-primary/20 tracking-wider">ACTIVE PERSONA</span>
                    </div>
                    <p className="text-sm text-muted-foreground flex items-center gap-2">
                        <span className="w-1.5 h-1.5 rounded-full bg-primary/40"></span>
                        Verified Identity from <code className="text-primary/70">persona/IDENTITY.md</code>
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                        <span className="rounded-full border border-border bg-background/60 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                            {setupStatus?.persona_ready ? "Persona ready" : "Setup incomplete"}
                        </span>
                        <span className="rounded-full border border-border bg-background/60 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                            Dynamic {personaStatus?.enable_dynamic_personality ? "on" : "off"}
                        </span>
                        <span className="rounded-full border border-border bg-background/60 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                            {styleCoverage} channel styles
                        </span>
                        <span className="rounded-full border border-border bg-background/60 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                            Mood {personaStatus?.mood ? "tracked" : "empty"}
                        </span>
                    </div>
                </div>
            </div>

                {/* Key Metrics Row */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                {/* System Health */}
                <div className="relative overflow-hidden rounded-xl border border-primary/20 bg-gradient-to-br from-primary/10 to-transparent p-6 shadow-2xl shadow-primary/5">
                    <div className="absolute top-0 right-0 p-4 opacity-20">
                        <Activity className="w-24 h-24 text-primary" />
                    </div>
                    <div className="relative z-10">
                        <h3 className="text-sm font-medium text-primary uppercase tracking-wider">System Status</h3>
                        <div className="mt-2 flex items-center gap-3">
                            <div className="relative flex h-4 w-4">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-500 opacity-75"></span>
                                <span className="relative inline-flex rounded-full h-4 w-4 bg-green-500"></span>
                            </div>
                            <span className="text-3xl font-bold text-foreground">Online</span>
                        </div>
                        <p className="mt-2 text-sm text-muted-foreground font-mono">
                            Uptime: <span className="text-foreground">{stats ? formatUptime(stats.uptime) : "0s"}</span>
                        </p>
                    </div>
                </div>

                {/* LLM Status */}
                <div className="rounded-xl border bg-card p-6 shadow-sm hover:border-primary/50 transition-colors group">
                    <div className="flex items-center justify-between mb-4">
                        <h3 className="text-sm font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-2">
                            <Cpu className="w-4 h-4" /> LLM API
                        </h3>
                        <span className={cn(
                            "text-xs font-bold px-2 py-0.5 rounded-full border",
                            llmHealth?.status === "Healthy" ? "bg-blue-500/10 text-blue-400 border-blue-500/20" :
                                llmHealth?.status === "Quota Exceeded" ? "bg-red-500/10 text-red-500 border-red-500/20" :
                                    "bg-yellow-500/10 text-yellow-500 border-yellow-500/20"
                        )}>
                            {llmHealth?.status || "Checking..."}
                        </span>
                    </div>
                    <div className="space-y-1">
                        <div className="text-2xl font-bold font-mono">
                            {llmHealth?.latency_ms ? `${llmHealth.latency_ms} ms` : "-"}
                        </div>
                        <div className="text-xs text-muted-foreground truncate" title={llmHealth?.model}>
                            {llmHealth?.model || "Unknown Model"}
                        </div>
                    </div>
                </div>

                {/* Sessions / Instances */}
                <div className="grid grid-cols-1 gap-4">
                    <div className="bg-card border rounded-xl p-4 shadow-sm flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className="p-2 rounded-lg bg-primary/10 text-primary">
                                <Globe className="w-5 h-5" />
                            </div>
                            <div>
                                <div className="text-2xl font-bold">{stats?.instances_count || 0}</div>
                                <div className="text-[10px] text-muted-foreground uppercase tracking-wider font-bold">Main Instances</div>
                            </div>
                        </div>
                        <div className="h-8 w-px bg-border"></div>
                        <div className="flex items-center gap-3">
                            <div className="p-2 rounded-lg bg-amber-500/10 text-amber-500">
                                <Zap className="w-5 h-5" />
                            </div>
                            <div>
                                <div className="text-2xl font-bold">{stats?.subagents_count || 0}</div>
                                <div className="text-[10px] text-muted-foreground uppercase tracking-wider font-bold">Active Sub-Agents</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

                <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <Card className="border-border/70 bg-card/60">
                        <CardContent className="p-4">
                            <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">Mood</div>
                            <div className="mt-2 text-sm font-semibold text-foreground">{personaStatus?.mood?.trim() || "Not set"}</div>
                        </CardContent>
                    </Card>
                    <Card className="border-border/70 bg-card/60">
                        <CardContent className="p-4">
                            <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">Strongest Tie</div>
                            <div className="mt-2 text-sm font-semibold text-foreground">{strongestRelationship?.name || "None yet"}</div>
                            <div className="mt-1 text-xs text-muted-foreground">{strongestRelationship ? `${strongestRelationship.level} • ${strongestRelationship.affinity}` : "No relationship data yet"}</div>
                        </CardContent>
                    </Card>
                    <Card className="border-border/70 bg-card/60">
                        <CardContent className="p-4">
                            <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">Missing Setup</div>
                            <div className="mt-2 text-sm font-semibold text-foreground">
                                {setupStatus?.persona_missing?.length ? setupStatus.persona_missing.join(", ") : "Nothing missing"}
                            </div>
                        </CardContent>
                    </Card>
                    <Card className="border-border/70 bg-card/60">
                        <CardContent className="p-4">
                            <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">Voice Coverage</div>
                            <div className="mt-2 text-sm font-semibold text-foreground">{styleCoverage}/4 channel overrides</div>
                            <div className="mt-1 text-xs text-muted-foreground">Web, Discord, Telegram, WhatsApp</div>
                        </CardContent>
                    </Card>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                    <div className="lg:col-span-2 space-y-6">
                    <Card className="border-muted bg-card/50">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <Zap className="w-5 h-5 text-primary" /> Gateway Connection
                            </CardTitle>
                            <CardDescription>Authentication and entry point details for remote clients.</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <div className="space-y-2">
                                    <label className="text-xs font-medium text-muted-foreground uppercase">WebSocket Endpoint</label>
                                    <div className="flex items-center gap-2">
                                        <code className="flex-1 p-2.5 bg-background rounded-md border border-input font-mono text-sm leading-none">
                                            {stats?.gateway_url || "ws://..."}
                                        </code>
                                    </div>
                                </div>

                                <div className="space-y-2">
                                    <label className="text-xs font-medium text-muted-foreground uppercase">Gateway Token</label>
                                    <div className="p-2.5 bg-background rounded-md border border-input font-mono text-sm leading-none text-muted-foreground overflow-hidden text-ellipsis">
                                        {gatewayTokenLabel || "Configured locally in this browser only"}
                                    </div>
                                </div>

                                <div className="space-y-2">
                                    <label className="text-xs font-medium text-muted-foreground uppercase">Auth System</label>
                                    <div className="flex items-center gap-2 text-sm text-foreground/80">
                                        <span className={cn("w-2 h-2 rounded-full", gatewayTokenLabel ? "bg-green-500" : "bg-yellow-500")}></span>
                                        {gatewayTokenLabel ? "API Key configured" : "No token cached in browser"}
                                    </div>
                                </div>

                                <div className="space-y-2">
                                    <label className="text-xs font-medium text-muted-foreground uppercase">Session Scope</label>
                                    <div className="p-2 bg-muted/20 rounded border border-border/50 text-xs font-mono text-muted-foreground">
                                        agent:main:default
                                    </div>
                                </div>
                            </div>
                            <div className="mt-6 flex gap-3">
                                <button
                                    onClick={connectClient}
                                    disabled={actionBusy !== null}
                                    className="px-4 py-2 bg-primary text-primary-foreground hover:bg-primary/90 rounded-md text-sm font-medium transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                                >
                                    Connect Client
                                </button>
                                <button
                                    onClick={generateNewToken}
                                    disabled={actionBusy !== null}
                                    className="px-4 py-2 bg-secondary text-secondary-foreground hover:bg-secondary/80 rounded-md text-sm font-medium transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                                >
                                    Generate New Token
                                </button>
                            </div>
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader className="pb-3">
                            <CardTitle className="text-base font-medium">Active Channels</CardTitle>
                        </CardHeader>
                        <CardContent className="grid gap-4">
                            {stats?.channels.map((channel, i) => (
                                <div key={i} className="flex items-center justify-between p-3 rounded-lg border border-border hover:bg-muted/30 transition-colors">
                                    <div className="flex items-center gap-4">
                                        <div className={cn(
                                            "w-10 h-10 rounded-full flex items-center justify-center bg-muted",
                                            channel.status === "Connected" ? "text-primary bg-primary/10" : "text-yellow-500 bg-yellow-500/10"
                                        )}>
                                            <Activity className="w-5 h-5" />
                                        </div>
                                        <div>
                                            <div className="font-semibold capitalize">{channel.name}</div>
                                            <div className="text-xs text-muted-foreground font-mono">{channel.type}</div>
                                        </div>
                                    </div>
                                    <div className={cn(
                                        "px-2.5 py-0.5 rounded-full text-xs font-bold uppercase",
                                        channel.status === "Connected" ? "bg-green-500/10 text-green-500" : "bg-yellow-500/10 text-yellow-500"
                                    )}>
                                        {channel.status}
                                    </div>
                                </div>
                            ))}
                        </CardContent>
                    </Card>
                </div>

                {/* Sidebar Actions */}
                <div className="space-y-6">
                    <div className="rounded-xl border bg-card p-5 space-y-4">
                        <div className="flex items-center gap-2 text-foreground font-semibold">
                            <Power className="w-4 h-4" /> Control Panel
                        </div>
                        <button
                            onClick={restartBackend}
                            className="w-full flex items-center justify-between px-4 py-3 bg-red-500/10 hover:bg-red-500/20 text-red-500 rounded-lg transition-colors text-sm font-medium border border-red-500/20 group"
                        >
                            <span>Restart Backend</span>
                            <RotateCcw className="w-4 h-4 group-hover:-rotate-180 transition-transform duration-500" />
                        </button>
                        <button
                            className="w-full flex items-center justify-between px-4 py-3 bg-muted/50 hover:bg-muted text-foreground rounded-lg transition-colors text-sm font-medium border border-border"
                            onClick={() => {
                                openConfirm({
                                    title: "Clear tool cache?",
                                    description: "Cached tool results will be removed for all active sessions.",
                                    actionLabel: "Clear cache",
                                    tone: "destructive",
                                    onConfirm: async () => {
                                        axios.post(`${API_BASE_URL}/api/control/clear-cache`)
                                            .then(res => toast.success("Cache cleared", { description: res.data.message || "Cache cleared." }))
                                            .catch(err => toast.error("Failed to clear cache", { description: str(err) }));
                                    },
                                });
                            }}
                        >
                            <span>Clear Cache</span>
                            <RefreshCw className="w-4 h-4" />
                        </button>
                        <button
                            className="w-full flex items-center justify-between px-4 py-3 bg-muted/50 hover:bg-muted text-foreground rounded-lg transition-colors text-sm font-medium border border-border"
                            onClick={() => {
                                openConfirm({
                                    title: "Clear system logs?",
                                    description: "This removes the current log buffer from the dashboard.",
                                    actionLabel: "Clear logs",
                                    tone: "destructive",
                                    onConfirm: async () => {
                                        axios.post(`${API_BASE_URL}/api/control/clear-logs`)
                                            .then(res => toast.success("Logs cleared", { description: res.data.message || "Logs cleared." }))
                                            .catch(err => toast.error("Failed to clear logs", { description: str(err) }));
                                    },
                                });
                            }}
                        >
                            <span>Clear Logs</span>
                            <Trash2 className="w-4 h-4" />
                        </button>

                        <div className="pt-4 border-t border-border/50">
                            <div className="flex items-center gap-2 text-foreground font-semibold mb-3">
                                <User className="w-4 h-4" /> Persona
                            </div>
                            <div className="grid grid-cols-2 gap-3">
                                <button
                                    onClick={() => {
                                        axios.get(`${API_BASE_URL}/api/persona/export`)
                                            .then(res => {
                                                const data = res.data;
                                                if (data.error) throw new Error(data.error);
                                                const blob = new Blob([data.content], { type: 'text/markdown' });
                                                const url = window.URL.createObjectURL(blob);
                                                const a = document.createElement('a');
                                                a.href = url;
                                                a.download = data.filename || "limebot_persona.md";
                                                a.click();
                                                toast.success("Persona exported", { description: data.filename || "limebot_persona.md" });
                                            })
                                            .catch(err => toast.error("Export failed", { description: str(err) }));
                                    }}
                                    className="flex flex-col items-center justify-center gap-2 p-3 bg-primary/10 hover:bg-primary/20 text-primary rounded-lg border border-primary/20 transition-colors"
                                >
                                    <Download className="w-5 h-5" />
                                    <span className="text-xs font-bold">Export</span>
                                </button>
                                <label className="flex flex-col items-center justify-center gap-2 p-3 bg-muted/50 hover:bg-muted text-foreground rounded-lg border border-border cursor-pointer transition-colors">
                                    <Upload className="w-5 h-5" />
                                    <span className="text-xs font-bold">Import</span>
                                    <input
                                        type="file"
                                        className="hidden"
                                        accept=".md,.markdown"
                                        onChange={(e) => {
                                            const file = e.target.files?.[0];
                                            if (!file) return;

                                            openConfirm({
                                                title: "Import persona backup?",
                                                description: "This will overwrite the current Identity and Soul files. A backup will be created automatically.",
                                                actionLabel: "Import persona",
                                                tone: "destructive",
                                                onConfirm: async () => importPersonaFile(file),
                                            });
                                            e.target.value = ''; // Reset
                                        }}
                                    />
                                </label>
                            </div>
                        </div>
                    </div>

                    <div className="rounded-xl border bg-card p-5">
                        <h4 className="text-sm font-bold mb-4">Quick Tips</h4>
                        <ul className="space-y-3">
                            <li className="text-xs text-muted-foreground flex gap-2">
                                <span className="text-primary font-bold">•</span>
                                <span>Use <code>tailscale serve</code> to expose the gateway securely.</span>
                            </li>
                            <li className="text-xs text-muted-foreground flex gap-2">
                                <span className="text-primary font-bold">•</span>
                                <span>Monitor <code>/logs</code> for real-time debug info.</span>
                            </li>
                            <li className="text-xs text-muted-foreground flex gap-2">
                                <span className="text-primary font-bold">•</span>
                                <span>Check LLM latency periodically to ensure API health.</span>
                            </li>
                        </ul>
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
                            className={cn(confirmAction?.tone === "destructive" && "bg-red-600 hover:bg-red-700 text-white")}
                        >
                            {confirmAction?.actionLabel || "Confirm"}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}

// Helper for error string conversion if missed
function str(e: any): string {
    if (typeof e === 'string') return e;
    if (e instanceof Error) return e.message;
    return String(e);
}
