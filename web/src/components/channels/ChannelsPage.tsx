import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { Activity, MessageCircle, RefreshCw, Send, User, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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
import { WhatsAppContacts } from "./WhatsAppContacts";
import { type ConfigApiResponse, type ConfigSecretsMap, getSecretInfo } from "@/lib/config-secrets";
import { classifyRequestError } from "@/lib/request-error";
import { cn } from "@/lib/utils";
import type { RowState } from "@/lib/tool-state";
import { DiscordIcon, WhatsAppIcon } from "./ChannelIcons";
import { WhatsAppConnectionSection } from "./WhatsAppConnectionSection";
import { Notice, PageFrame, PageHeader, RequestError, RowButton, SkeletonRows, StatusPill } from "@/components/ui/data-page";
import { StatusGlyph } from "@/components/chat/tool-ui";
import {
    AccessSummary,
    AdvancedToggle,
    Field,
    Panel,
    SecretField,
    SelectField,
    StatusBanner,
    StyleOverrideEditor,
    ToggleRow,
    ValueOverrideEditor,
} from "./channel-ui";
import {
    DEFAULT_TELEGRAM_VOICE,
    EMOJI_USAGE,
    TELEGRAM_TONES,
    TONES,
    VERBOSITIES,
    buildTelegramStyle,
    clonePersonaDraft,
    objectToStyleRows,
    objectToValueRows,
    styleRowsToObject,
    valueRowsToObject,
    type ConfigState,
    type DiscordUiConfig,
    type PersonaDraft,
    type SecretDrafts,
    type StyleOverrideRow,
    type TelegramStatus,
    type TelegramVoiceDraft,
    type ValueOverrideRow,
} from "./channel-config";

type ChannelTab = "discord" | "telegram" | "whatsapp";
type SaveStatus = { type: "success" | "error"; message: string } | null;

interface TabMeta {
    id: ChannelTab;
    name: string;
    icon: React.ReactNode;
    state: RowState;
    label: string;
    unsaved: boolean;
}

function ChannelTabs({ tabs, active, onSelect }: { tabs: TabMeta[]; active: ChannelTab; onSelect: (tab: ChannelTab) => void }) {
    return (
        <div role="tablist" aria-label="Messaging channels" className="grid grid-cols-3 border-b border-border/60">
            {tabs.map((tab) => {
                const selected = tab.id === active;
                return (
                    <button
                        key={tab.id}
                        type="button"
                        role="tab"
                        id={`channel-tab-${tab.id}`}
                        aria-selected={selected}
                        aria-controls={`channel-panel-${tab.id}`}
                        onClick={() => onSelect(tab.id)}
                        className={cn(
                            "relative flex flex-col items-start gap-1 px-2 pb-2.5 pt-1 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex-row sm:items-center sm:gap-3 sm:px-3",
                            selected ? "text-foreground" : "text-muted-foreground hover:text-foreground"
                        )}
                    >
                        <span className="flex items-center gap-2 text-sm font-medium">
                            {tab.icon}
                            {tab.name}
                            {tab.unsaved && <span className="h-1.5 w-1.5 rounded-full bg-amber-500" title="Unsaved changes" aria-label="Unsaved changes" />}
                        </span>
                        <span className="flex items-center gap-1.5">
                            <StatusPill state={tab.state} label={tab.label} className="[&>span:first-child]:scale-75" />
                        </span>
                        <span className={cn("absolute inset-x-0 -bottom-px h-0.5 rounded-full transition-colors", selected ? "bg-primary" : "bg-transparent")} />
                    </button>
                );
            })}
        </div>
    );
}

export function ChannelsPage({ initialTab = "discord" }: { initialTab?: ChannelTab } = {}) {
    const [config, setConfig] = useState<ConfigState>({});
    const [savedConfig, setSavedConfig] = useState<ConfigState>({});
    const [secretMeta, setSecretMeta] = useState<ConfigSecretsMap>({});
    const [secretDrafts, setSecretDrafts] = useState<SecretDrafts>({});
    const [clearedSecrets, setClearedSecrets] = useState<string[]>([]);
    const [discordConfig, setDiscordConfig] = useState<DiscordUiConfig>({});
    const [savedDiscordConfig, setSavedDiscordConfig] = useState<DiscordUiConfig>({});
    const [guildStyleRows, setGuildStyleRows] = useState<StyleOverrideRow[]>([]);
    const [savedGuildStyleRows, setSavedGuildStyleRows] = useState<StyleOverrideRow[]>([]);
    const [channelStyleRows, setChannelStyleRows] = useState<StyleOverrideRow[]>([]);
    const [savedChannelStyleRows, setSavedChannelStyleRows] = useState<StyleOverrideRow[]>([]);
    const [nicknameRows, setNicknameRows] = useState<ValueOverrideRow[]>([]);
    const [savedNicknameRows, setSavedNicknameRows] = useState<ValueOverrideRow[]>([]);
    const [themeRows, setThemeRows] = useState<ValueOverrideRow[]>([]);
    const [savedThemeRows, setSavedThemeRows] = useState<ValueOverrideRow[]>([]);
    const [globalAvatarUrl, setGlobalAvatarUrl] = useState("");
    const [savedGlobalAvatarUrl, setSavedGlobalAvatarUrl] = useState("");
    const [personaDraft, setPersonaDraft] = useState<PersonaDraft>(clonePersonaDraft());
    const [telegramVoiceDraft, setTelegramVoiceDraft] = useState<TelegramVoiceDraft>(DEFAULT_TELEGRAM_VOICE);
    const [savedTelegramVoiceDraft, setSavedTelegramVoiceDraft] = useState<TelegramVoiceDraft>(DEFAULT_TELEGRAM_VOICE);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<unknown>(null);
    const [saving, setSaving] = useState(false);
    const [status, setStatus] = useState<SaveStatus>(null);
    const [discordSaving, setDiscordSaving] = useState(false);
    const [discordStatus, setDiscordStatus] = useState<SaveStatus>(null);
    const [telegramStatus, setTelegramStatus] = useState<TelegramStatus | null>(null);
    const [activeTab, setActiveTab] = useState<ChannelTab>(initialTab);
    const [showDiscordAdvanced, setShowDiscordAdvanced] = useState(false);
    const [showTelegramAdvanced, setShowTelegramAdvanced] = useState(false);
    const [showWhatsAppContacts, setShowWhatsAppContacts] = useState(false);
    const [reloadPrompt, setReloadPrompt] = useState(false);

    const fetchConfig = useCallback(() => {
        return axios
            .get<ConfigApiResponse<ConfigState>>(`${API_BASE_URL}/api/config`)
            .then((res) => {
                if (res.data.env) {
                    setConfig(res.data.env);
                    setSavedConfig(res.data.env);
                }
                setSecretMeta(res.data.secrets || {});
                setSecretDrafts({});
                setClearedSecrets([]);
                setLoadError(null);
            })
            .catch((err) => {
                console.error("Failed to load config:", err);
                setLoadError(err);
            })
            .finally(() => setLoading(false));
    }, []);

    const fetchDiscordConfig = useCallback(() => {
        axios
            .get(`${API_BASE_URL}/api/discord/config`)
            .then((res) => {
                const dc = res.data.discord || {};
                setDiscordConfig(dc);
                setSavedDiscordConfig(dc);
                const nextGuildRows = objectToStyleRows(dc.style_overrides?.guilds);
                const nextChannelRows = objectToStyleRows(dc.style_overrides?.channels);
                const nextNicknameRows = objectToValueRows(dc.nickname_templates?.guilds);
                const nextThemeRows = objectToValueRows(dc.embed_theme?.guilds);
                setGuildStyleRows(nextGuildRows);
                setSavedGuildStyleRows(nextGuildRows);
                setChannelStyleRows(nextChannelRows);
                setSavedChannelStyleRows(nextChannelRows);
                setNicknameRows(nextNicknameRows);
                setSavedNicknameRows(nextNicknameRows);
                setThemeRows(nextThemeRows);
                setSavedThemeRows(nextThemeRows);
                setGlobalAvatarUrl(dc.avatar_overrides?.global || "");
                setSavedGlobalAvatarUrl(dc.avatar_overrides?.global || "");
            })
            .catch((err) => {
                if (err.response?.status !== 401) {
                    console.error("Failed to load discord config:", err);
                    setDiscordStatus({ type: "error", message: "Failed to load Discord personalization." });
                }
            });
    }, []);

    const fetchPersonaDraft = useCallback(() => {
        axios
            .get(`${API_BASE_URL}/api/persona`)
            .then((res) => {
                const nextPersona = clonePersonaDraft(res.data);
                const nextVoice = {
                    ...DEFAULT_TELEGRAM_VOICE,
                    notes: nextPersona.telegram_style || "",
                };
                setPersonaDraft(nextPersona);
                setTelegramVoiceDraft(nextVoice);
                setSavedTelegramVoiceDraft(nextVoice);
            })
            .catch((err) => {
                if (err.response?.status !== 401) {
                    console.error("Failed to load persona draft:", err);
                }
            });
    }, []);

    const fetchTelegramStatus = useCallback(() => {
        axios
            .get(`${API_BASE_URL}/api/telegram/status`)
            .then((res) => {
                setTelegramStatus(res.data || null);
            })
            .catch((err) => {
                if (err.response?.status !== 401) {
                    console.error("Failed to load telegram status:", err);
                }
                setTelegramStatus(null);
            });
    }, []);

    useEffect(() => {
        void fetchConfig();
        fetchDiscordConfig();
        fetchTelegramStatus();
        fetchPersonaDraft();
    }, [fetchConfig, fetchDiscordConfig, fetchTelegramStatus, fetchPersonaDraft]);

    const handleSave = () => {
        setSaving(true);
        setStatus(null);

        const updateData = {
            DISCORD_ALLOW_FROM: config.DISCORD_ALLOW_FROM,
            DISCORD_ALLOW_CHANNELS: config.DISCORD_ALLOW_CHANNELS,
            DISCORD_ACTIVITY_TYPE: config.DISCORD_ACTIVITY_TYPE,
            DISCORD_STATUS: config.DISCORD_STATUS,
            DISCORD_ACTIVITY_TEXT: config.DISCORD_ACTIVITY_TEXT,
            ENABLE_DISCORD: config.ENABLE_DISCORD,
            ENABLE_TELEGRAM: config.ENABLE_TELEGRAM,
            TELEGRAM_API_BASE: config.TELEGRAM_API_BASE,
            TELEGRAM_ALLOW_FROM: config.TELEGRAM_ALLOW_FROM,
            TELEGRAM_ALLOW_CHATS: config.TELEGRAM_ALLOW_CHATS,
            TELEGRAM_POLL_TIMEOUT: config.TELEGRAM_POLL_TIMEOUT,
            PERSONALITY_WHITELIST: config.PERSONALITY_WHITELIST,
            WHATSAPP_ALLOW_FROM: config.WHATSAPP_ALLOW_FROM,
            ENABLE_WHATSAPP: config.ENABLE_WHATSAPP,
        };

        const secretUpdates = Object.fromEntries(Object.entries(secretDrafts).filter(([, value]) => value.trim() !== ""));

        axios
            .post(`${API_BASE_URL}/api/config`, {
                env: { ...updateData, ...secretUpdates },
                clear_secrets: clearedSecrets,
            })
            .then((res) => {
                const data = res.data;
                if (data.error) throw new Error(data.error);
                if (secretDrafts.APP_API_KEY?.trim()) {
                    localStorage.setItem("limebot_api_key", secretDrafts.APP_API_KEY.trim());
                    axios.defaults.headers.common["X-API-Key"] = secretDrafts.APP_API_KEY.trim();
                }
                setStatus({ type: "success", message: "Channel settings saved." });
                setSavedConfig(updateData);
                setSecretMeta((prev) => {
                    const next = { ...prev };
                    for (const key of ["DISCORD_TOKEN", "TELEGRAM_BOT_TOKEN"] as const) {
                        if (clearedSecrets.includes(key)) {
                            next[key] = { configured: false, masked: "", last4: "" };
                        } else if (secretDrafts[key]?.trim()) {
                            const val = secretDrafts[key].trim();
                            next[key] = { configured: true, masked: `••••${val.slice(-4)}`, last4: val.slice(-4) };
                        }
                    }
                    return next;
                });
                setSecretDrafts({});
                setClearedSecrets([]);
                fetchTelegramStatus();
                setSaving(false);
            })
            .catch((err) => {
                console.error("Failed to save config:", err);
                const info = classifyRequestError(err);
                setStatus({ type: "error", message: `Could not save the channel settings. ${info.title}.` });
                setSaving(false);
            });
    };

    const handleChange = (key: string, value: string) => {
        setConfig((prev) => ({ ...prev, [key]: value }));
    };

    const handleSecretChange = (key: string, value: string) => {
        setSecretDrafts((prev) => ({ ...prev, [key]: value }));
        setClearedSecrets((prev) => prev.filter((item) => item !== key));
    };

    const clearSecret = (key: string) => {
        setSecretDrafts((prev) => ({ ...prev, [key]: "" }));
        setClearedSecrets((prev) => (prev.includes(key) ? prev : [...prev, key]));
    };

    const undoClearSecret = (key: string) => {
        setClearedSecrets((prev) => prev.filter((item) => item !== key));
    };

    const updateDiscordField = <Key extends keyof DiscordUiConfig>(key: Key, value: DiscordUiConfig[Key]) => {
        setDiscordConfig((prev) => ({ ...prev, [key]: value }));
    };

    const updateDefaultStyle = (change: Record<string, string>) =>
        updateDiscordField("style_overrides", {
            ...(discordConfig.style_overrides || {}),
            default: { ...(discordConfig.style_overrides?.default || {}), ...change },
        });

    const handleDiscordSave = () => {
        setDiscordSaving(true);
        setDiscordStatus(null);
        const payload: DiscordUiConfig = {
            signature: discordConfig.signature || "",
            emoji_set: discordConfig.emoji_set || [],
            verbosity_limits: discordConfig.verbosity_limits || { short: 600, medium: 1800, long: 4000 },
            tone_prefixes: discordConfig.tone_prefixes || {},
            style_overrides: {
                default: discordConfig.style_overrides?.default || {},
                guilds: styleRowsToObject(guildStyleRows),
                channels: styleRowsToObject(channelStyleRows),
            },
            embed_theme: {
                default: discordConfig.embed_theme?.default || "",
                guilds: valueRowsToObject(themeRows),
            },
            nickname_templates: {
                default: discordConfig.nickname_templates?.default || "",
                guilds: valueRowsToObject(nicknameRows),
            },
            avatar_overrides: {
                global: globalAvatarUrl.trim(),
            },
        };

        axios
            .post(`${API_BASE_URL}/api/discord/config`, { discord: payload })
            .then((res) => {
                if (res.data.error) throw new Error(res.data.error);
                setDiscordStatus({ type: "success", message: "Discord personalization saved. The bot is restarting." });
                setSavedDiscordConfig(payload);
                setSavedGuildStyleRows(guildStyleRows);
                setSavedChannelStyleRows(channelStyleRows);
                setSavedNicknameRows(nicknameRows);
                setSavedThemeRows(themeRows);
                setSavedGlobalAvatarUrl(globalAvatarUrl.trim());
            })
            .catch((err) => {
                console.error("Failed to save discord config:", err);
                setDiscordStatus({ type: "error", message: `Could not save Discord personalization. ${classifyRequestError(err).title}.` });
            })
            .finally(() => setDiscordSaving(false));
    };

    const handleTelegramStyleSave = () => {
        const nextStyle = buildTelegramStyle(telegramVoiceDraft);
        const payload = clonePersonaDraft({
            ...personaDraft,
            telegram_style: nextStyle,
        });
        setDiscordSaving(true);
        setDiscordStatus(null);
        axios
            .put(`${API_BASE_URL}/api/persona`, payload)
            .then((res) => {
                if (res.data.error) throw new Error(res.data.error);
                setPersonaDraft(payload);
                setSavedTelegramVoiceDraft(telegramVoiceDraft);
                setDiscordStatus({ type: "success", message: "Telegram voice saved." });
            })
            .catch((err) => {
                console.error("Failed to save Telegram voice:", err);
                setDiscordStatus({ type: "error", message: `Could not save the Telegram voice. ${classifyRequestError(err).title}.` });
            })
            .finally(() => setDiscordSaving(false));
    };

    const configDirty = useMemo(
        () => JSON.stringify(config) !== JSON.stringify(savedConfig) || JSON.stringify(secretDrafts) !== JSON.stringify({}) || clearedSecrets.length > 0,
        [config, savedConfig, secretDrafts, clearedSecrets]
    );

    const discordStyleDirty = useMemo(
        () =>
            JSON.stringify(discordConfig) !== JSON.stringify(savedDiscordConfig) ||
            JSON.stringify(guildStyleRows) !== JSON.stringify(savedGuildStyleRows) ||
            JSON.stringify(channelStyleRows) !== JSON.stringify(savedChannelStyleRows) ||
            JSON.stringify(nicknameRows) !== JSON.stringify(savedNicknameRows) ||
            JSON.stringify(themeRows) !== JSON.stringify(savedThemeRows) ||
            globalAvatarUrl.trim() !== savedGlobalAvatarUrl.trim(),
        [
            discordConfig,
            savedDiscordConfig,
            guildStyleRows,
            savedGuildStyleRows,
            channelStyleRows,
            savedChannelStyleRows,
            nicknameRows,
            savedNicknameRows,
            themeRows,
            savedThemeRows,
            globalAvatarUrl,
            savedGlobalAvatarUrl,
        ]
    );

    const telegramVoiceDirty = useMemo(() => JSON.stringify(telegramVoiceDraft) !== JSON.stringify(savedTelegramVoiceDraft), [telegramVoiceDraft, savedTelegramVoiceDraft]);

    useEffect(() => {
        if (!configDirty && !discordStyleDirty && !telegramVoiceDirty) return;
        const onBeforeUnload = (event: BeforeUnloadEvent) => {
            event.preventDefault();
            event.returnValue = "";
        };
        window.addEventListener("beforeunload", onBeforeUnload);
        return () => window.removeEventListener("beforeunload", onBeforeUnload);
    }, [configDirty, discordStyleDirty, telegramVoiceDirty]);

    const discardConfigChanges = () => {
        setConfig(savedConfig);
        setSecretDrafts({});
        setClearedSecrets([]);
        setStatus(null);
    };

    const discardDiscordChanges = () => {
        setDiscordConfig(savedDiscordConfig);
        setGuildStyleRows(savedGuildStyleRows);
        setChannelStyleRows(savedChannelStyleRows);
        setNicknameRows(savedNicknameRows);
        setThemeRows(savedThemeRows);
        setGlobalAvatarUrl(savedGlobalAvatarUrl);
        setDiscordStatus(null);
    };

    const discardTelegramVoice = () => {
        setTelegramVoiceDraft(savedTelegramVoiceDraft);
        setDiscordStatus(null);
    };

    const activeDraftDirty = configDirty || (activeTab === "discord" && discordStyleDirty) || (activeTab === "telegram" && telegramVoiceDirty);

    const reloadConfig = () => {
        setLoading(true);
        void fetchConfig();
    };

    const reloadActive = () => {
        reloadConfig();
        if (activeTab === "discord") fetchDiscordConfig();
        if (activeTab === "telegram") {
            fetchTelegramStatus();
            fetchPersonaDraft();
        }
        setReloadPrompt(false);
    };

    const discordEnabled = config.ENABLE_DISCORD !== "false";
    const telegramEnabled = config.ENABLE_TELEGRAM === "true";
    const whatsappEnabled = config.ENABLE_WHATSAPP === "true";
    const overrideCount =
        guildStyleRows.filter((row) => row.target.trim()).length +
        channelStyleRows.filter((row) => row.target.trim()).length +
        nicknameRows.filter((row) => row.target.trim() && row.value.trim()).length +
        themeRows.filter((row) => row.target.trim() && row.value.trim()).length;
    const secretConfigured = (key: "DISCORD_TOKEN" | "TELEGRAM_BOT_TOKEN") =>
        !clearedSecrets.includes(key) && (!!secretDrafts[key]?.trim() || getSecretInfo(secretMeta, key).configured);
    const telegramTokenConfigured = secretConfigured("TELEGRAM_BOT_TOKEN");
    const discordTokenConfigured = secretConfigured("DISCORD_TOKEN");

    const tabs: TabMeta[] = [
        {
            id: "discord",
            name: "Discord",
            icon: <DiscordIcon className="h-4 w-4" />,
            state: !discordEnabled ? "planned" : discordTokenConfigured ? "done" : "waiting",
            label: !discordEnabled ? "Off" : discordTokenConfigured ? "Ready" : "Token needed",
            unsaved: discordStyleDirty,
        },
        {
            id: "telegram",
            name: "Telegram",
            icon: <Send className="h-4 w-4" />,
            state: telegramStatus?.last_error ? "failed" : telegramStatus?.connected ? "done" : telegramEnabled ? (telegramTokenConfigured ? "running" : "waiting") : "planned",
            label: telegramStatus?.last_error ? "Error" : telegramStatus?.connected ? "Connected" : telegramEnabled ? (telegramTokenConfigured ? "Starting" : "Token needed") : "Off",
            unsaved: telegramVoiceDirty,
        },
        {
            id: "whatsapp",
            name: "WhatsApp",
            icon: <WhatsAppIcon className="h-4 w-4" />,
            state: whatsappEnabled ? "done" : "planned",
            label: whatsappEnabled ? "On" : "Off",
            unsaved: false,
        },
    ];

    const reloadButton = (
        <RowButton
            onClick={() => (activeDraftDirty ? setReloadPrompt(true) : reloadActive())}
            disabled={loading}
            aria-label="Reload from disk"
            title="Reload from disk"
        >
            <RefreshCw className={cn(loading && "animate-spin")} />
            <span className="hidden sm:inline">Reload</span>
        </RowButton>
    );

    // Never show editable forms filled with defaults if the real config did not load:
    // saving them would overwrite the person's actual settings.
    if (loading && Object.keys(savedConfig).length === 0) {
        return (
            <PageFrame width="max-w-4xl">
                <PageHeader title="Channels" description="Connect Discord, Telegram and WhatsApp." />
                <SkeletonRows count={3} />
            </PageFrame>
        );
    }
    if (loadError !== null && Object.keys(savedConfig).length === 0) {
        return (
            <PageFrame width="max-w-4xl">
                <PageHeader title="Channels" description="Connect Discord, Telegram and WhatsApp." />
                <RequestError error={loadError} subject="channel settings" onRetry={reloadConfig} retrying={loading} />
            </PageFrame>
        );
    }

    const discordTab = (
        <div className="space-y-4">
            <Panel title="Connection" description="Mentions and DMs reach LimeBot through a Discord bot token.">
                <ToggleRow
                    id="enable_discord"
                    label="Discord integration"
                    description="Connect to Discord on startup using the token below."
                    checked={discordEnabled}
                    onChange={(checked) => handleChange("ENABLE_DISCORD", checked ? "true" : "false")}
                />
                <SecretField
                    id="DISCORD_TOKEN"
                    label="Bot token"
                    placeholder="MT..."
                    meta={secretMeta}
                    value={secretDrafts.DISCORD_TOKEN || ""}
                    clearing={clearedSecrets.includes("DISCORD_TOKEN")}
                    onChange={(value) => handleSecretChange("DISCORD_TOKEN", value)}
                    onClear={() => clearSecret("DISCORD_TOKEN")}
                    onUndoClear={() => undoClearSecret("DISCORD_TOKEN")}
                />
                <AccessSummary
                    items={[
                        { label: config.DISCORD_ALLOW_FROM ? "Restricted users" : "Anyone can use it", restricted: !!config.DISCORD_ALLOW_FROM },
                        { label: config.DISCORD_ALLOW_CHANNELS ? "Restricted channels" : "All channels", restricted: !!config.DISCORD_ALLOW_CHANNELS },
                    ]}
                />
            </Panel>

            <AdvancedToggle
                open={showDiscordAdvanced}
                onToggle={() => setShowDiscordAdvanced((value) => !value)}
                label="Presence, access rules and personalization"
                summary={showDiscordAdvanced ? "Hide" : overrideCount > 0 ? `${overrideCount} ${overrideCount === 1 ? "override" : "overrides"}` : "Optional"}
                disabled={!discordEnabled}
            />

            {discordEnabled && showDiscordAdvanced && (
                <div className="space-y-4 animate-in fade-in slide-in-from-top-1 duration-150">
                    <Panel title="Presence" description="How LimeBot appears in the Discord member list." icon={<Activity className="h-4 w-4 text-muted-foreground" />}>
                        <div className="grid gap-3 sm:grid-cols-3">
                            <SelectField
                                label="Status"
                                value={config.DISCORD_STATUS || "online"}
                                onChange={(value) => handleChange("DISCORD_STATUS", value)}
                                options={[
                                    { value: "online", label: "Online" },
                                    { value: "idle", label: "Idle" },
                                    { value: "dnd", label: "Do not disturb" },
                                    { value: "invisible", label: "Invisible" },
                                ]}
                            />
                            <SelectField
                                label="Activity type"
                                value={config.DISCORD_ACTIVITY_TYPE || "playing"}
                                onChange={(value) => handleChange("DISCORD_ACTIVITY_TYPE", value)}
                                options={[
                                    { value: "playing", label: "Playing" },
                                    { value: "watching", label: "Watching" },
                                    { value: "listening", label: "Listening" },
                                    { value: "competing", label: "Competing" },
                                ]}
                            />
                            <Field label="Activity text" htmlFor="discord_activity_text">
                                <Input id="discord_activity_text" value={config.DISCORD_ACTIVITY_TEXT || ""} onChange={(e) => handleChange("DISCORD_ACTIVITY_TEXT", e.target.value)} placeholder="Visual Studio Code" className="h-9" />
                            </Field>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Shows as <span className="font-medium text-foreground">{config.DISCORD_ACTIVITY_TYPE || "playing"} {config.DISCORD_ACTIVITY_TEXT || "LimeBot"}</span>
                        </p>
                    </Panel>

                    <Panel title="Access control" description="Limit who and where LimeBot answers. Leave empty to allow everyone." icon={<User className="h-4 w-4 text-muted-foreground" />}>
                        <Field label="Allowed user IDs" htmlFor="discord_users" hint="Comma-separated Discord user IDs.">
                            <Input id="discord_users" value={config.DISCORD_ALLOW_FROM || ""} onChange={(e) => handleChange("DISCORD_ALLOW_FROM", e.target.value)} placeholder="123456789012345678, 987654321…" className="h-9 font-mono text-sm" />
                        </Field>
                        <Field label="Allowed channel IDs" htmlFor="discord_channels" hint="Comma-separated channel IDs.">
                            <Input id="discord_channels" value={config.DISCORD_ALLOW_CHANNELS || ""} onChange={(e) => handleChange("DISCORD_ALLOW_CHANNELS", e.target.value)} placeholder="123456789012345678" className="h-9 font-mono text-sm" />
                        </Field>
                        <Field label="Elevated persona user IDs" htmlFor="personality_whitelist" hint="Users who may bypass identity isolation and reach the partner persona. Use sparingly.">
                            <Input id="personality_whitelist" value={config.PERSONALITY_WHITELIST || ""} onChange={(e) => handleChange("PERSONALITY_WHITELIST", e.target.value)} placeholder="UserID1, UserID2…" className="h-9 font-mono text-sm" />
                        </Field>
                    </Panel>

                    <Panel title="Personalization" description="Tone, length, emoji, signature and per-server overrides." icon={<MessageCircle className="h-4 w-4 text-muted-foreground" />}>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Signature">
                                <Input value={discordConfig.signature || ""} onChange={(e) => updateDiscordField("signature", e.target.value)} placeholder="LimeBot" className="h-9" />
                            </Field>
                            <Field label="Emoji set" hint="Comma-separated.">
                                <Input
                                    value={(discordConfig.emoji_set || []).join(", ")}
                                    onChange={(e) =>
                                        updateDiscordField(
                                            "emoji_set",
                                            e.target.value
                                                .split(",")
                                                .map((s) => s.trim())
                                                .filter(Boolean)
                                        )
                                    }
                                    placeholder="🍋, ⚙️, ✨"
                                    className="h-9"
                                />
                            </Field>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-3">
                            <SelectField label="Default tone" value={discordConfig.style_overrides?.default?.tone || "neutral"} onChange={(value) => updateDefaultStyle({ tone: value })} options={TONES} />
                            <SelectField label="Default verbosity" value={discordConfig.style_overrides?.default?.verbosity || "medium"} onChange={(value) => updateDefaultStyle({ verbosity: value })} options={VERBOSITIES} />
                            <SelectField label="Default emoji usage" value={discordConfig.style_overrides?.default?.emoji_usage || "light"} onChange={(value) => updateDefaultStyle({ emoji_usage: value })} options={EMOJI_USAGE} />
                        </div>
                        <div className="grid gap-3 sm:grid-cols-3">
                            <Field label="Embed colour (hex)">
                                <Input
                                    value={discordConfig.embed_theme?.default || ""}
                                    onChange={(e) => updateDiscordField("embed_theme", { ...(discordConfig.embed_theme || {}), default: e.target.value })}
                                    placeholder="#57F287"
                                    className="h-9 font-mono"
                                />
                            </Field>
                            <Field label="Nickname template">
                                <Input
                                    value={discordConfig.nickname_templates?.default || ""}
                                    onChange={(e) => updateDiscordField("nickname_templates", { ...(discordConfig.nickname_templates || {}), default: e.target.value })}
                                    placeholder="LimeBot • DevOps"
                                    className="h-9"
                                />
                            </Field>
                            <Field label="Message prefix">
                                <Input value={discordConfig.style_overrides?.default?.prefix || ""} onChange={(e) => updateDefaultStyle({ prefix: e.target.value })} placeholder="Heads up:" className="h-9" />
                            </Field>
                        </div>

                        <div className="space-y-5 border-t border-border/50 pt-4">
                            <StyleOverrideEditor
                                title="Per-server style"
                                hint="Give one Discord server its own tone and length."
                                addLabel="Add server"
                                targetPlaceholder="Guild ID"
                                emptyText="No server-specific style yet."
                                rows={guildStyleRows}
                                setRows={setGuildStyleRows}
                            />
                            <StyleOverrideEditor
                                title="Per-channel style"
                                hint="Use when one room needs a different voice."
                                addLabel="Add channel"
                                targetPlaceholder="Channel ID"
                                emptyText="No channel-specific style yet."
                                rows={channelStyleRows}
                                setRows={setChannelStyleRows}
                            />
                            <ValueOverrideEditor
                                title="Per-server nickname"
                                hint="Guild ID to nickname template."
                                addLabel="Add nickname"
                                valuePlaceholder="LimeBot • DevOps"
                                emptyText="No nickname overrides yet."
                                rows={nicknameRows}
                                setRows={setNicknameRows}
                            />
                            <ValueOverrideEditor
                                title="Per-server embed colour"
                                hint="Guild ID to a default embed colour."
                                addLabel="Add colour"
                                valuePlaceholder="#57F287"
                                emptyText="No colour overrides yet."
                                rows={themeRows}
                                setRows={setThemeRows}
                            />
                            <Field label="Global avatar URL" hint="Discord bots support a single global avatar.">
                                <Input value={globalAvatarUrl} onChange={(e) => setGlobalAvatarUrl(e.target.value)} placeholder="https://…/avatar.png" className="h-9" />
                            </Field>
                        </div>
                    </Panel>
                </div>
            )}
        </div>
    );

    const telegramTab = (
        <div className="space-y-4">
            {telegramTokenConfigured && !telegramEnabled && (
                <Notice title="Token saved, channel still off.">Turn the integration on below before expecting the bot to answer.</Notice>
            )}
            {telegramStatus?.last_error && (
                <RequestError
                    info={{ category: "unknown", title: "Telegram health check failed", hint: "Check the bot token and that the API base URL is reachable.", retryable: true, detail: telegramStatus.last_error }}
                    onRetry={fetchTelegramStatus}
                />
            )}

            <Panel title="Connection" description="Bot API long polling, private chats and groups.">
                <ToggleRow
                    id="enable_telegram"
                    label="Telegram integration"
                    description="Poll Telegram using the bot token below."
                    checked={telegramEnabled}
                    onChange={(checked) => handleChange("ENABLE_TELEGRAM", checked ? "true" : "false")}
                />
                <SecretField
                    id="TELEGRAM_BOT_TOKEN"
                    label="Bot token"
                    placeholder="123456789:AA..."
                    meta={secretMeta}
                    value={secretDrafts.TELEGRAM_BOT_TOKEN || ""}
                    clearing={clearedSecrets.includes("TELEGRAM_BOT_TOKEN")}
                    onChange={(value) => {
                        handleSecretChange("TELEGRAM_BOT_TOKEN", value);
                        if (value.trim() && config.ENABLE_TELEGRAM !== "true") handleChange("ENABLE_TELEGRAM", "true");
                    }}
                    onClear={() => clearSecret("TELEGRAM_BOT_TOKEN")}
                    onUndoClear={() => undoClearSecret("TELEGRAM_BOT_TOKEN")}
                />
                <dl className="grid grid-cols-3 divide-x divide-border/50 rounded-lg border border-border/60 bg-background/40 text-xs">
                    {[
                        ["Health", telegramStatus?.status ? telegramStatus.status.replace("_", " ") : "Unknown"],
                        ["Bot", telegramStatus?.display_name || (telegramStatus?.username ? `@${telegramStatus.username}` : "Not checked")],
                        ["Bot ID", telegramStatus?.bot_id ? String(telegramStatus.bot_id) : "—"],
                    ].map(([term, value]) => (
                        <div key={term} className="min-w-0 px-3 py-2">
                            <dt className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">{term}</dt>
                            <dd className="mt-0.5 truncate font-medium capitalize text-foreground">{value}</dd>
                        </div>
                    ))}
                </dl>
                <AccessSummary
                    items={[
                        { label: config.TELEGRAM_ALLOW_FROM ? "Restricted users" : "Anyone can use it", restricted: !!config.TELEGRAM_ALLOW_FROM },
                        { label: config.TELEGRAM_ALLOW_CHATS ? "Restricted chats" : "All chats", restricted: !!config.TELEGRAM_ALLOW_CHATS },
                    ]}
                />
            </Panel>

            <AdvancedToggle
                open={showTelegramAdvanced}
                onToggle={() => setShowTelegramAdvanced((value) => !value)}
                label="Voice, polling and access rules"
                summary={showTelegramAdvanced ? "Hide" : "Optional"}
            />

            {showTelegramAdvanced && (
                <div className="space-y-4 animate-in fade-in slide-in-from-top-1 duration-150">
                    <Panel title="Voice" description="How LimeBot sounds on Telegram. Feeds the persona prompt." icon={<Wand2 className="h-4 w-4 text-muted-foreground" />}>
                        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                            <SelectField label="Tone" value={telegramVoiceDraft.tone} onChange={(value) => setTelegramVoiceDraft((prev) => ({ ...prev, tone: value }))} options={TELEGRAM_TONES} />
                            <SelectField label="Verbosity" value={telegramVoiceDraft.verbosity} onChange={(value) => setTelegramVoiceDraft((prev) => ({ ...prev, verbosity: value }))} options={VERBOSITIES} />
                            <SelectField label="Emoji usage" value={telegramVoiceDraft.emojiUsage} onChange={(value) => setTelegramVoiceDraft((prev) => ({ ...prev, emojiUsage: value }))} options={EMOJI_USAGE} />
                            <Field label="Signoff">
                                <Input value={telegramVoiceDraft.signoff} onChange={(e) => setTelegramVoiceDraft((prev) => ({ ...prev, signoff: e.target.value }))} placeholder="Optional closer" className="h-9" />
                            </Field>
                        </div>
                        <Field label="Extra guidance">
                            <Textarea value={telegramVoiceDraft.notes} onChange={(e) => setTelegramVoiceDraft((prev) => ({ ...prev, notes: e.target.value }))} placeholder="Optional notes for Telegram-specific delivery…" className="min-h-[88px] text-sm" />
                        </Field>
                        <div className="rounded-lg border border-border/60 bg-background/40 p-3">
                            <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80">Generated style</div>
                            <p className="mt-1.5 text-xs leading-relaxed text-foreground/90">{buildTelegramStyle(telegramVoiceDraft)}</p>
                        </div>
                    </Panel>

                    <Panel title="Polling and access" description="Changes here apply after the backend restarts.">
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="API base URL" htmlFor="telegram_api_base">
                                <Input id="telegram_api_base" value={config.TELEGRAM_API_BASE || ""} onChange={(e) => handleChange("TELEGRAM_API_BASE", e.target.value)} placeholder="https://api.telegram.org" className="h-9 font-mono text-sm" />
                            </Field>
                            <Field label="Polling timeout (seconds)" htmlFor="telegram_poll_timeout">
                                <Input id="telegram_poll_timeout" type="number" min="1" value={config.TELEGRAM_POLL_TIMEOUT || "30"} onChange={(e) => handleChange("TELEGRAM_POLL_TIMEOUT", e.target.value)} className="h-9" />
                            </Field>
                            <Field label="Allowed user IDs" htmlFor="telegram_users" hint="Leave empty to allow anyone who can reach the bot.">
                                <Input id="telegram_users" value={config.TELEGRAM_ALLOW_FROM || ""} onChange={(e) => handleChange("TELEGRAM_ALLOW_FROM", e.target.value)} placeholder="123456789, 987654321" className="h-9 font-mono text-sm" />
                            </Field>
                            <Field label="Allowed chat IDs" htmlFor="telegram_chats" hint="Works for private chats and groups.">
                                <Input id="telegram_chats" value={config.TELEGRAM_ALLOW_CHATS || ""} onChange={(e) => handleChange("TELEGRAM_ALLOW_CHATS", e.target.value)} placeholder="-1001234567890, 123456789" className="h-9 font-mono text-sm" />
                            </Field>
                        </div>
                    </Panel>
                </div>
            )}
        </div>
    );

    const whatsappTab = (
        <div className="space-y-4">
            <Panel title="Connection" description="LimeBot talks to WhatsApp through a local bridge you pair with your phone.">
                <ToggleRow
                    id="enable_whatsapp"
                    label="WhatsApp integration"
                    description="Connect to the WhatsApp bridge on startup."
                    checked={whatsappEnabled}
                    onChange={(checked) => handleChange("ENABLE_WHATSAPP", checked ? "true" : "false")}
                />
                <Notice title="Runs without asking.">Tools such as file writes and commands run immediately on WhatsApp; there is no approval prompt on this channel.</Notice>
            </Panel>

            {whatsappEnabled && (
                <div className="space-y-4 animate-in fade-in slide-in-from-top-1 duration-150">
                    <WhatsAppConnectionSection />
                    <AdvancedToggle
                        open={showWhatsAppContacts}
                        onToggle={() => setShowWhatsAppContacts((value) => !value)}
                        label="Contact approvals and access"
                        summary={showWhatsAppContacts ? "Hide" : "Manage"}
                    />
                    {showWhatsAppContacts && <WhatsAppContacts />}
                </div>
            )}
        </div>
    );

    const showSaveBar = configDirty || (activeTab === "discord" && discordStyleDirty) || (activeTab === "telegram" && telegramVoiceDirty);

    return (
        <PageFrame width="max-w-4xl" className="pb-6">
            <PageHeader title="Channels" description="Where LimeBot listens and answers. Each channel is set up independently." actions={reloadButton} />

            {loadError !== null && <RequestError compact error={loadError} subject="channel settings" onRetry={reloadConfig} retrying={loading} />}

            <ChannelTabs tabs={tabs} active={activeTab} onSelect={setActiveTab} />

            <StatusBanner status={status} onDismiss={() => setStatus(null)} />
            <StatusBanner status={discordStatus} onDismiss={() => setDiscordStatus(null)} />

            <div role="tabpanel" id={`channel-panel-${activeTab}`} aria-labelledby={`channel-tab-${activeTab}`}>
                {activeTab === "discord" ? discordTab : activeTab === "telegram" ? telegramTab : whatsappTab}
            </div>

            {showSaveBar && (
                <div className="sticky bottom-3 z-20 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-amber-400/40 bg-card/95 px-3 py-2 shadow-lg backdrop-blur animate-in fade-in slide-in-from-bottom-2 duration-150">
                    <span className="flex items-center gap-2 text-xs font-medium text-foreground">
                        <StatusGlyph state="waiting" />
                        Unsaved changes
                    </span>
                    <div className="ml-auto flex flex-wrap items-center gap-1.5">
                        {configDirty && (
                            <>
                                <Button variant="ghost" size="sm" className="h-8 px-3 text-xs" onClick={discardConfigChanges} disabled={saving}>
                                    Discard
                                </Button>
                                <Button size="sm" className="h-8 px-3 text-xs" onClick={handleSave} disabled={saving}>
                                    {saving ? "Saving…" : "Save channel config"}
                                </Button>
                            </>
                        )}
                        {activeTab === "discord" && discordStyleDirty && (
                            <>
                                <Button variant="ghost" size="sm" className="h-8 px-3 text-xs" onClick={discardDiscordChanges} disabled={discordSaving}>
                                    Discard draft
                                </Button>
                                <Button variant="outline" size="sm" className="h-8 px-3 text-xs" onClick={handleDiscordSave} disabled={discordSaving}>
                                    {discordSaving ? "Saving…" : "Save personalization"}
                                </Button>
                            </>
                        )}
                        {activeTab === "telegram" && telegramVoiceDirty && (
                            <>
                                <Button variant="ghost" size="sm" className="h-8 px-3 text-xs" onClick={discardTelegramVoice} disabled={discordSaving}>
                                    Discard voice
                                </Button>
                                <Button variant="outline" size="sm" className="h-8 px-3 text-xs" onClick={handleTelegramStyleSave} disabled={discordSaving}>
                                    {discordSaving ? "Saving…" : "Save Telegram voice"}
                                </Button>
                            </>
                        )}
                    </div>
                </div>
            )}

            <AlertDialog open={reloadPrompt} onOpenChange={setReloadPrompt}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
                        <AlertDialogDescription>Reloading replaces what you have typed with the settings saved on disk.</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Keep editing</AlertDialogCancel>
                        <AlertDialogAction onClick={reloadActive}>Discard and reload</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </PageFrame>
    );
}

