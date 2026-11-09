import {
    MessageSquare,
    LayoutDashboard,
    Settings,
    Users,
    Activity,
    Zap,
    FileText,
    Palette,
    User2,
    Brain,
    Cpu,
    Bot,
    List,
    Globe,
    Volume2,
    PanelLeftClose,
    PanelLeftOpen,
    type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { BotVisual } from "@/components/pet/BotVisual";
import { useEffect, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { getApiErrorStatus } from "@/lib/api-error";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";

interface SidebarProps {
    className?: string;
    /** Forces the full-width layout, used inside the mobile drawer. */
    forceExpanded?: boolean;
    botIdentity?: { name: string; avatar: string | null };
    activeView?: string;
    onNavigate?: (view: string) => void;
    runtimeStatus?: {
        isConnected: boolean;
        autonomousMode: boolean;
        pendingApprovals: number;
    };
}

interface SelectionOption {
    value: string;
    label: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseSelectionOptions(value: unknown): SelectionOption[] | null {
    if (!Array.isArray(value)) return null;
    const options = value.flatMap((item): SelectionOption[] => {
        if (!isRecord(item) || typeof item.value !== "string") return [];
        return [{
            value: item.value,
            label: typeof item.label === "string" ? item.label : item.value,
        }];
    });
    return options.length ? options : null;
}

const COLLAPSE_KEY = "limebot-sidebar-collapsed";

function readCollapsed(): boolean {
    try {
        return window.localStorage.getItem(COLLAPSE_KEY) === "1";
    } catch {
        return false;
    }
}

interface NavEntry {
    id: string;
    icon: LucideIcon;
    label: string;
}

const NAV_GROUPS: Array<{ title: string; items: NavEntry[] }> = [
    {
        title: "Workspace",
        items: [
            { id: "chat", icon: MessageSquare, label: "Chat" },
            { id: "overview", icon: LayoutDashboard, label: "Overview" },
            { id: "queue", icon: List, label: "Task Queue" },
            { id: "memory", icon: Brain, label: "Memory" },
            { id: "channels", icon: Activity, label: "Channels" },
            { id: "logs", icon: FileText, label: "System Logs" },
            { id: "instances", icon: Users, label: "Instances" },
            { id: "cron", icon: Zap, label: "Cron Jobs" },
        ],
    },
    {
        title: "Agent",
        items: [
            { id: "profiles", icon: Users, label: "Agents" },
            { id: "skills", icon: Zap, label: "Skills" },
            { id: "subagents", icon: Bot, label: "Subagents" },
            { id: "browsers", icon: Globe, label: "Browser Sessions" },
            { id: "mcp", icon: Cpu, label: "MCP" },
            { id: "voice", icon: Volume2, label: "ElevenLabs Voice" },
        ],
    },
    {
        title: "Settings",
        items: [
            { id: "persona", icon: User2, label: "Persona" },
            { id: "appearance", icon: Palette, label: "Appearance" },
            { id: "config", icon: Settings, label: "Configuration" },
        ],
    },
];

export function Sidebar({
    className,
    forceExpanded,
    botIdentity,
    activeView = "chat",
    onNavigate,
    runtimeStatus,
}: SidebarProps) {
    const [collapsedPreference, setCollapsedPreference] = useState(readCollapsed);
    const collapsed = !forceExpanded && collapsedPreference;
    const [activeModel, setActiveModel] = useState("Loading...");
    const [memoryLabel, setMemoryLabel] = useState("Checking...");
    const [subagentSelection, setSubagentSelection] = useState("auto");
    const [subagentOptions, setSubagentOptions] = useState<Array<{ value: string; label: string }>>([
        { value: "auto", label: "Auto" },
    ]);
    const [savingSubagentSelection, setSavingSubagentSelection] = useState(false);

    const toggleCollapsed = () => {
        setCollapsedPreference((value) => {
            const next = !value;
            try {
                window.localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
            } catch {
                // Preference only; the sidebar still works without storage.
            }
            return next;
        });
    };

    const handleSubagentSelectionChange = async (value: string) => {
        const previous = subagentSelection;
        setSubagentSelection(value);
        setSavingSubagentSelection(true);
        try {
            const res = await axios.put<{ default_selection?: string; selection_options?: unknown }>(`${API_BASE_URL}/api/subagents/settings`, {
                default_selection: value,
            });
            setSubagentSelection(res.data?.default_selection || value);
            const options = parseSelectionOptions(res.data?.selection_options);
            if (options && options.length > 0) {
                setSubagentOptions(options);
            }
        } catch (error) {
            console.error("Failed to update subagent mode:", error);
            setSubagentSelection(previous);
        } finally {
            setSavingSubagentSelection(false);
        }
    };

    useEffect(() => {
        let isMounted = true;

        const refreshRuntimeDetails = async () => {
            try {
                const [configRes, memoryRes, subagentRes] = await Promise.all([
                    axios.get<{ env?: { LLM_MODEL?: string } }>(`${API_BASE_URL}/api/config`),
                    axios.get<{ enabled?: boolean; mode?: string }>(`${API_BASE_URL}/api/memory`),
                    axios.get<{ default_selection?: string; selection_options?: unknown }>(`${API_BASE_URL}/api/subagents`),
                ]);

                if (!isMounted) return;

                setActiveModel(configRes.data?.env?.LLM_MODEL || "Unknown");
                setSubagentSelection(subagentRes.data?.default_selection || "auto");
                const options = parseSelectionOptions(subagentRes.data?.selection_options)
                    || [{ value: "auto", label: "Auto" }];
                setSubagentOptions(options);

                const enabled = memoryRes.data?.enabled;
                const mode = memoryRes.data?.mode || (enabled ? "vector" : "grep_fallback");
                if (mode === "grep_fallback") {
                    setMemoryLabel("Markdown fallback");
                } else if (enabled === false) {
                    setMemoryLabel("Offline");
                } else {
                    setMemoryLabel("Vector online");
                }
            } catch (error: unknown) {
                const status = getApiErrorStatus(error);
                if (status !== 401 && status !== 403) {
                    console.error("Failed to load runtime details:", error);
                }
                if (!isMounted) return;
                setActiveModel("Unavailable");
                setMemoryLabel("Unavailable");
                setSubagentSelection("auto");
            }
        };

        refreshRuntimeDetails();
        const interval = window.setInterval(refreshRuntimeDetails, 15000);
        return () => {
            isMounted = false;
            window.clearInterval(interval);
        };
    }, []);

    const connected = !!runtimeStatus?.isConnected;
    const pending = runtimeStatus?.pendingApprovals || 0;

    return (
        <aside
            className={cn(
                "hidden h-full shrink-0 flex-col border-r border-border/60 bg-card/40 backdrop-blur-sm transition-[width] duration-200 md:flex",
                collapsed ? "w-[68px]" : "w-64",
                className
            )}
        >
            <div className={cn("flex h-14 shrink-0 items-center gap-2 px-3", collapsed && "justify-center px-0")}>
                <button
                    type="button"
                    className={cn("group flex min-w-0 items-center gap-2.5 rounded-lg p-1 text-left", !collapsed && "flex-1")}
                    onClick={() => onNavigate?.("persona")}
                    title={botIdentity?.name || "LimeBot"}
                >
                    <BotVisual avatar={botIdentity?.avatar} size="sidebar" />
                    {!collapsed && (
                        <span className="truncate text-[15px] font-semibold tracking-tight text-foreground transition-colors group-hover:text-primary">
                            {botIdentity?.name || "LimeBot"}
                        </span>
                    )}
                </button>
                {!collapsed && !forceExpanded && (
                    <button
                        type="button"
                        onClick={toggleCollapsed}
                        className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                        aria-label="Collapse sidebar"
                        title="Collapse sidebar"
                    >
                        <PanelLeftClose className="h-4 w-4" />
                    </button>
                )}
            </div>

            {collapsed && (
                <button
                    type="button"
                    onClick={toggleCollapsed}
                    className="mx-auto mb-1 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    aria-label="Expand sidebar"
                    title="Expand sidebar"
                >
                    <PanelLeftOpen className="h-4 w-4" />
                </button>
            )}

            <nav className="flex-1 space-y-4 overflow-y-auto px-2 py-2">
                {!collapsed && (
                    <div className="px-1">
                        <Select
                            value={subagentSelection}
                            onValueChange={handleSubagentSelectionChange}
                            disabled={savingSubagentSelection}
                        >
                            <SelectTrigger
                                className="h-8 rounded-lg border-border/60 bg-background/50 text-xs"
                                title="Assistant mode: applies to web, Discord, Telegram and WhatsApp"
                            >
                                <Bot className="mr-1.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                <SelectValue placeholder="Assistant mode" />
                            </SelectTrigger>
                            <SelectContent>
                                {subagentOptions.map((option) => (
                                    <SelectItem key={option.value} value={option.value}>
                                        {option.label}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                )}

                {NAV_GROUPS.map((group, index) => (
                    <div key={group.title} className="space-y-0.5">
                        {collapsed ? (
                            index > 0 && <div className="mx-3 mb-2 h-px bg-border/60" />
                        ) : (
                            <div className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground/70">
                                {group.title}
                            </div>
                        )}
                        {group.items.map((item) => (
                            <NavItem
                                key={item.id}
                                icon={item.icon}
                                label={item.label}
                                collapsed={collapsed}
                                active={activeView === item.id}
                                badge={item.id === "chat" && pending > 0 ? pending : undefined}
                                onClick={() => onNavigate?.(item.id)}
                            />
                        ))}
                    </div>
                ))}
            </nav>

            <div className="shrink-0 border-t border-border/60 p-2">
                <div
                    className={cn(
                        "flex items-center gap-2.5 rounded-lg px-2 py-2",
                        collapsed && "justify-center px-0"
                    )}
                    title={`${connected ? "Gateway live" : "Reconnecting"} · ${activeModel} · ${runtimeStatus?.autonomousMode ? "Autonomous" : "Guarded"} · Memory: ${memoryLabel}`}
                >
                    <span className="relative flex h-2.5 w-2.5 shrink-0">
                        {connected && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/40" />}
                        <span className={cn("relative inline-flex h-2.5 w-2.5 rounded-full", connected ? "bg-primary" : "bg-amber-500")} />
                    </span>
                    {!collapsed && (
                        <div className="min-w-0 flex-1">
                            <div className="truncate text-xs font-medium text-foreground">{activeModel}</div>
                            <div className="truncate text-[10px] text-muted-foreground">
                                {connected ? "Live" : "Reconnecting"} · {runtimeStatus?.autonomousMode ? "Autonomous" : "Guarded"}
                                {pending > 0 ? ` · ${pending} to approve` : ""}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </aside>
    );
}

function NavItem({
    icon: Icon,
    label,
    active,
    collapsed,
    badge,
    onClick,
}: {
    icon: LucideIcon;
    label: string;
    active?: boolean;
    collapsed?: boolean;
    badge?: number;
    onClick?: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            title={collapsed ? label : undefined}
            aria-current={active ? "page" : undefined}
            className={cn(
                "group relative flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                collapsed && "justify-center px-0",
                active
                    ? "bg-primary/10 font-medium text-primary"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
            )}
        >
            {active && <span className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-primary" />}
            <Icon className="h-4 w-4 shrink-0" />
            {!collapsed && <span className="truncate">{label}</span>}
            {badge !== undefined && (
                <span
                    className={cn(
                        "flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-bold text-black",
                        collapsed ? "absolute right-2 top-1" : "ml-auto"
                    )}
                >
                    {badge}
                </span>
            )}
        </button>
    );
}
