import { useCallback, useEffect, useMemo, useState } from "react";
import axios, { AxiosError, type AxiosResponse } from "axios";
import { toast } from "sonner";
import { API_BASE_URL } from "@/lib/api";
import { cn } from "@/lib/utils";
import { classifySaveError, type SaveErrorCategory, type SaveErrorInfo } from "@/lib/save-error";
import { classifyToolError } from "@/lib/tool-display";
import { useUnsaved, useBeforeUnloadGuard } from "@/hooks/useUnsaved";
import { useSaveState } from "@/hooks/useSaveState";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Textarea } from "../ui/textarea";
import { Avatar, AvatarFallback, AvatarImage } from "../ui/avatar";
import {
    EmptyState,
    Field,
    LoadFailed,
    LoadingRows,
    Note,
    SaveBar,
    SaveErrorNote,
    SettingsPage,
    SettingsSection,
    SettingsTabs,
    StatusPill,
} from "../settings/kit";
import {
    ArrowRight, Bot, ChevronRight, MessageSquare, Radio, RefreshCw, Send, User, Users, Wand2,
} from "lucide-react";

interface Relationship { id: string; name: string; affinity: number; level: string; }
interface PersonaData {
    name: string; emoji: string; pfp_url: string; style: string;
    discord_style?: string; telegram_style?: string; whatsapp_style?: string; web_style?: string;
    reaction_emojis?: string; soul_summary: string; catchphrases?: string;
    interests?: string; birthday?: string; mood?: string;
    enable_dynamic_personality?: boolean; relationships?: Relationship[];
}
interface PersonaPageProps { onNavigate?: (view: string) => void; }
type PreviewChannel = "web" | "discord" | "telegram" | "whatsapp";
interface PersonaPreviewResponse {
    channel: PreviewChannel; model: string; effective_style: string; style_source: string;
    system_prompt_excerpt: string; preview_text?: string | null; error?: string | null;
}

const DEFAULT_PERSONA: PersonaData = {
    name: "", emoji: "", pfp_url: "", style: "", discord_style: "", telegram_style: "",
    whatsapp_style: "", web_style: "", reaction_emojis: "", soul_summary: "",
    catchphrases: "", interests: "", birthday: "", mood: "",
    enable_dynamic_personality: false, relationships: [],
};
const CHANNELS: Array<{ id: PreviewChannel; label: string }> = [
    { id: "web", label: "Web" },
    { id: "discord", label: "Discord" },
    { id: "telegram", label: "Telegram" },
    { id: "whatsapp", label: "WhatsApp" },
];

const clonePersona = (data?: Partial<PersonaData>): PersonaData =>
    JSON.parse(JSON.stringify({ ...DEFAULT_PERSONA, ...(data || {}) }));

function effectiveStyle(persona: PersonaData, channel: PreviewChannel) {
    const base = persona.style.trim();
    const web = (persona.web_style || "").trim();
    const platform = String(persona[`${channel}_style` as keyof PersonaData] || "").trim();
    if (channel === "web") return { text: web || base, source: web ? "Web override" : "Base style" };
    if (platform) return { text: platform, source: `${CHANNELS.find((item) => item.id === channel)?.label} override` };
    return web ? { text: web, source: "Web fallback" } : { text: base, source: "Base style" };
}

const affinityTone = (value: number) => value >= 70 ? "bg-primary" : value >= 30 ? "bg-amber-500" : "bg-muted-foreground/50";
const affinityLabel = (value: number) => value >= 70 ? "Close" : value >= 30 ? "Warm" : "Distant";

/** The backend answers 200 with `{error}` when a save fails; surface that as a failed save. */
function rejectedByServer(message: string): AxiosError {
    const response = { data: { detail: message }, status: 422, statusText: "Unprocessable", headers: {}, config: {} } as AxiosResponse;
    return new AxiosError(message, "ERR_BAD_REQUEST", undefined, undefined, response);
}

/** Provider text from a preview that reached the backend but not the model. */
function providerErrorInfo(message: string): SaveErrorInfo {
    const tool = classifyToolError(message);
    const known: Partial<Record<string, SaveErrorCategory>> = {
        authentication: "authentication",
        rate_limit: "rate_limit",
        timeout: "timeout",
        network: "network",
    };
    return {
        category: known[tool.category] ?? "unknown",
        title: tool.category === "unknown" ? "The preview could not be generated" : tool.title,
        hint: tool.hint,
        retryable: tool.retryable,
        detail: tool.detail,
    };
}

function previewRequestError(error: unknown): SaveErrorInfo {
    const info = classifySaveError(error, "the preview");
    if (info.category === "network") return { ...info, hint: "Check that the backend is running, then try again." };
    if (info.category === "timeout") return { ...info, hint: "The model may still be answering. Wait a moment, then try again." };
    if (info.category === "server" || info.category === "unknown") {
        return { ...info, title: "The preview could not be generated", hint: "The details below may explain it.", retryable: true };
    }
    return info;
}

export function PersonaPage({ onNavigate }: PersonaPageProps) {
    const [persona, setPersona] = useState<PersonaData>(clonePersona());
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<unknown>(null);
    const [tab, setTab] = useState("identity");
    const [selectedChannel, setSelectedChannel] = useState<PreviewChannel>("web");
    const [previewMessage, setPreviewMessage] = useState("Introduce yourself and explain how you'd help me here.");
    const [previewLoading, setPreviewLoading] = useState(false);
    const [previewResult, setPreviewResult] = useState<PersonaPreviewResponse | null>(null);
    const [previewError, setPreviewError] = useState<SaveErrorInfo | null>(null);
    const [previewRequestKey, setPreviewRequestKey] = useState("");

    const { dirty: isDirty, markSaved, restore, ready } = useUnsaved(persona);
    const { saving, error: saveError, savedAt, run, clearError } = useSaveState("your persona changes");
    useBeforeUnloadGuard(isDirty);

    const fetchPersona = useCallback(async () => {
        try {
            setLoading(true);
            const response = await axios.get(`${API_BASE_URL}/api/persona`);
            const next = clonePersona(response.data);
            setPersona(next);
            markSaved(next);
            setPreviewResult(null);
            setPreviewError(null);
            setPreviewRequestKey("");
            setLoadError(null);
        } catch (error) {
            console.error("Failed to fetch persona:", error);
            setLoadError(error);
        } finally {
            setLoading(false);
        }
    }, [markSaved]);

    useEffect(() => { void fetchPersona(); }, [fetchPersona]);

    const missing = useMemo(() => [!persona.name.trim() && "display name", !persona.style.trim() && "base style"].filter(Boolean) as string[], [persona.name, persona.style]);
    const relationships = persona.relationships || [];
    const avgAffinity = relationships.length ? Math.round(relationships.reduce((sum, item) => sum + item.affinity, 0) / relationships.length) : 0;
    const overrideCount = CHANNELS.filter((channel) => String(persona[`${channel.id}_style` as keyof PersonaData] || "").trim()).length;
    const selectedStyle = effectiveStyle(persona, selectedChannel);
    const previewSignature = useMemo(() => JSON.stringify({ persona, selectedChannel, previewMessage }), [persona, selectedChannel, previewMessage]);
    const previewIsStale = previewRequestKey !== previewSignature;
    const changeCount = useMemo(() => {
        const saved = restore();
        if (!saved) return 0;
        return (Object.keys(persona) as Array<keyof PersonaData>)
            .filter((key) => JSON.stringify(persona[key]) !== JSON.stringify(saved[key])).length;
    }, [persona, restore]);

    const requestPreview = async () => {
        try {
            setPreviewLoading(true);
            setPreviewError(null);
            const response = await axios.post<PersonaPreviewResponse>(`${API_BASE_URL}/api/persona/preview`, { persona, channel: selectedChannel, user_message: previewMessage });
            setPreviewResult(response.data);
            setPreviewRequestKey(previewSignature);
        } catch (error) {
            console.error("Failed to preview persona:", error);
            setPreviewResult(null);
            setPreviewError(previewRequestError(error));
        } finally {
            setPreviewLoading(false);
        }
    };

    const handleSave = async () => {
        const payload = clonePersona(persona);
        const ok = await run(async () => {
            const response = await axios.put(`${API_BASE_URL}/api/persona`, payload);
            if (response.data.status !== "success") throw rejectedByServer(response.data.error || "Unknown error");
        });
        if (ok) {
            markSaved(payload);
            toast.success("Persona saved successfully.");
        }
    };

    const handleDiscard = () => {
        const saved = restore();
        if (saved) setPersona(clonePersona(saved));
        clearError();
    };
    const handleReload = () => {
        if (isDirty && !window.confirm("Discard unsaved persona edits and reload from disk?")) return;
        clearError();
        void fetchPersona();
    };

    const pageProps = {
        title: "Persona",
        description: "Shape identity, voice, and social behavior without editing persona files by hand.",
        icon: Bot,
    };

    if (loading && !ready) {
        return (
            <SettingsPage {...pageProps}>
                <LoadingRows rows={6} className="py-8" label="Loading persona" />
            </SettingsPage>
        );
    }

    if (loadError && !ready) {
        return (
            <SettingsPage {...pageProps}>
                <LoadFailed error={loadError} subject="your persona" onRetry={() => void fetchPersona()} />
            </SettingsPage>
        );
    }

    const reloadError = loadError ? classifySaveError(loadError, "your persona") : null;

    return (
        <SettingsPage
            {...pageProps}
            actions={
                <Button variant="outline" size="sm" onClick={handleReload} disabled={saving || loading} className="gap-1.5">
                    <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
                    Reload
                </Button>
            }
            saveBar={
                <SaveBar
                    dirty={isDirty}
                    saving={saving}
                    onSave={() => void handleSave()}
                    onDiscard={handleDiscard}
                    error={saveError}
                    onDismissError={clearError}
                    savedAt={savedAt}
                    changeCount={changeCount}
                    saveLabel="Save persona"
                />
            }
        >
            {reloadError && (
                <SaveErrorNote
                    error={{ ...reloadError, title: reloadError.category === "network" ? "Could not reload the persona" : reloadError.title }}
                    onRetry={() => void fetchPersona()}
                    onDismiss={() => setLoadError(null)}
                    className="mt-4"
                />
            )}

            <div className="mt-5 flex items-start gap-4 border-b border-border/50 pb-5">
                <Avatar className="h-14 w-14 shrink-0 border border-border/60">
                    <AvatarImage src={persona.pfp_url} className="object-cover" />
                    <AvatarFallback className="bg-primary text-xl text-primary-foreground">{persona.emoji || persona.name.charAt(0) || "?"}</AvatarFallback>
                </Avatar>
                <div className="min-w-0 flex-1 space-y-1.5">
                    <p className="flex flex-wrap items-center gap-2 text-lg font-semibold leading-tight text-foreground">
                        <span className="truncate">{persona.name || "Unnamed persona"}</span>
                        <span>{persona.emoji || "🍋"}</span>
                    </p>
                    <p className="line-clamp-2 max-w-3xl text-sm leading-relaxed text-muted-foreground">
                        {persona.style || "Add a base voice to define how this persona speaks and behaves."}
                    </p>
                    <div className="flex flex-wrap gap-1.5 pt-0.5">
                        <StatusPill tone="muted">{overrideCount} channel {overrideCount === 1 ? "override" : "overrides"}</StatusPill>
                        <StatusPill tone="muted">{relationships.length} {relationships.length === 1 ? "relationship" : "relationships"}</StatusPill>
                        <StatusPill tone={persona.enable_dynamic_personality ? "ok" : "muted"}>Adaptive {persona.enable_dynamic_personality ? "on" : "off"}</StatusPill>
                    </div>
                </div>
            </div>

            {missing.length > 0 && (
                <Note tone="warn" title={`Missing ${missing.join(" and ")}`} className="mt-5">
                    The persona is incomplete until {missing.length > 1 ? "both are" : "it is"} filled in. You can still save what you have.
                </Note>
            )}

            <SettingsTabs
                value={tab}
                onValueChange={setTab}
                tabs={[
                    { value: "identity", label: "Identity", icon: User, content: <IdentityEditor persona={persona} setPersona={setPersona} /> },
                    { value: "voice", label: "Voice", icon: MessageSquare, content: <VoiceEditor persona={persona} setPersona={setPersona} /> },
                    {
                        value: "preview",
                        label: "Preview",
                        icon: Wand2,
                        content: (
                            <PreviewStudio
                                persona={persona}
                                selectedChannel={selectedChannel}
                                setSelectedChannel={setSelectedChannel}
                                selectedStyle={selectedStyle}
                                previewMessage={previewMessage}
                                setPreviewMessage={setPreviewMessage}
                                previewLoading={previewLoading}
                                previewResult={previewResult}
                                previewError={previewError}
                                dismissPreviewError={() => setPreviewError(null)}
                                previewIsStale={previewIsStale}
                                requestPreview={requestPreview}
                            />
                        ),
                    },
                    {
                        value: "adaptive",
                        label: "Adaptive",
                        icon: Radio,
                        content: <AdaptiveEditor persona={persona} setPersona={setPersona} relationships={relationships} avgAffinity={avgAffinity} onNavigate={onNavigate} />,
                    },
                ]}
            />
        </SettingsPage>
    );
}

function IdentityEditor({ persona, setPersona }: { persona: PersonaData; setPersona: (value: PersonaData) => void }) {
    return (
        <>
            <SettingsSection title="Profile" description="The public profile people see across every channel.">
                <div className="grid gap-5 sm:grid-cols-2">
                    <Field label="Display name" htmlFor="persona-name">
                        <Input id="persona-name" value={persona.name} onChange={(e) => setPersona({ ...persona, name: e.target.value })} placeholder="Lisa, Sage, LimeBot…" />
                    </Field>
                    <Field label="Signature emoji" htmlFor="persona-emoji">
                        <Input id="persona-emoji" value={persona.emoji} onChange={(e) => setPersona({ ...persona, emoji: e.target.value })} placeholder="🍋" />
                    </Field>
                    <Field label="Avatar URL" htmlFor="persona-avatar">
                        <Input id="persona-avatar" value={persona.pfp_url} onChange={(e) => setPersona({ ...persona, pfp_url: e.target.value })} placeholder="https://example.com/avatar.jpg" />
                    </Field>
                    <Field label="Birthday" htmlFor="persona-birthday">
                        <Input id="persona-birthday" value={persona.birthday} onChange={(e) => setPersona({ ...persona, birthday: e.target.value })} placeholder="July 15" />
                    </Field>
                </div>
            </SettingsSection>

            <SettingsSection title="Personality notes" description="Small details that make the persona feel specific.">
                <div className="grid gap-5 sm:grid-cols-2">
                    <Field label="Interests" htmlFor="persona-interests">
                        <Input id="persona-interests" value={persona.interests} onChange={(e) => setPersona({ ...persona, interests: e.target.value })} placeholder="Coding, 3D art, electronic music…" />
                    </Field>
                    <Field label="Catchphrases" htmlFor="persona-catchphrases" hint="Separate phrases with semicolons.">
                        <Input id="persona-catchphrases" value={persona.catchphrases} onChange={(e) => setPersona({ ...persona, catchphrases: e.target.value })} placeholder="Let's squeeze it; Fresh take incoming" />
                    </Field>
                </div>
            </SettingsSection>

            <SettingsSection title="Soul" description={<>Core values live in <code className="rounded bg-muted px-1 py-0.5">SOUL.md</code> and are read-only here.</>}>
                <p className="border-l-2 border-border pl-3 text-sm leading-relaxed text-muted-foreground">
                    {persona.soul_summary || "No soul summary is available."}
                </p>
            </SettingsSection>
        </>
    );
}

function VoiceEditor({ persona, setPersona }: { persona: PersonaData; setPersona: (value: PersonaData) => void }) {
    return (
        <>
            <SettingsSection title="Base voice" description="The default personality inherited by every channel.">
                <Field label="Style" htmlFor="persona-style">
                    <Textarea id="persona-style" value={persona.style} onChange={(e) => setPersona({ ...persona, style: e.target.value })} placeholder="Warm, concise, playful, direct…" rows={5} />
                </Field>
            </SettingsSection>

            <SettingsSection title="Channel overrides" description="Leave a field empty to inherit its fallback.">
                <div className="divide-y divide-border/50 border-y border-border/50">
                    {CHANNELS.map((channel) => {
                        const current = effectiveStyle(persona, channel.id);
                        const key = `${channel.id}_style` as keyof PersonaData;
                        const value = String(persona[key] || "");
                        return (
                            <div key={channel.id} className="space-y-2 py-4">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <label htmlFor={`persona-${channel.id}-style`} className="text-sm font-medium text-foreground">{channel.label}</label>
                                    <StatusPill tone={value.trim() ? "ok" : "muted"}>{value.trim() ? "Custom" : current.source}</StatusPill>
                                </div>
                                <Textarea
                                    id={`persona-${channel.id}-style`}
                                    value={value}
                                    onChange={(e) => setPersona({ ...persona, [key]: e.target.value })}
                                    placeholder={`Inherits: ${current.text || "empty base voice"}`}
                                    rows={3}
                                />
                            </div>
                        );
                    })}
                </div>
            </SettingsSection>
        </>
    );
}

interface PreviewStudioProps {
    persona: PersonaData; selectedChannel: PreviewChannel; setSelectedChannel: (value: PreviewChannel) => void;
    selectedStyle: { text: string; source: string }; previewMessage: string; setPreviewMessage: (value: string) => void;
    previewLoading: boolean; previewResult: PersonaPreviewResponse | null; previewError: SaveErrorInfo | null;
    dismissPreviewError: () => void; previewIsStale: boolean; requestPreview: () => Promise<void>;
}
function PreviewStudio(props: PreviewStudioProps) {
    const { persona, selectedChannel, setSelectedChannel, selectedStyle, previewMessage, setPreviewMessage, previewLoading, previewResult, previewError, dismissPreviewError, previewIsStale, requestPreview } = props;
    const channelLabel = CHANNELS.find((item) => item.id === selectedChannel)?.label;
    return (
        <SettingsSection title="Voice preview" description="Test the real prompt path before saving. The preview uses your current edits, saved or not.">
            <div className="flex flex-wrap gap-1" role="group" aria-label="Preview channel">
                {CHANNELS.map((channel) => (
                    <button
                        key={channel.id}
                        type="button"
                        aria-pressed={selectedChannel === channel.id}
                        onClick={() => setSelectedChannel(channel.id)}
                        className={cn(
                            "rounded-md px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            selectedChannel === channel.id ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground"
                        )}
                    >
                        {channel.label}
                    </button>
                ))}
            </div>

            <div className="space-y-1.5 border-l-2 border-border pl-3">
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-foreground">Effective style</span>
                    <StatusPill tone="muted">{previewResult?.style_source || selectedStyle.source}</StatusPill>
                </div>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">
                    {previewResult?.effective_style || selectedStyle.text || "No effective style yet."}
                </p>
            </div>

            <Field label="Test message" htmlFor="persona-preview-message">
                <Textarea id="persona-preview-message" value={previewMessage} onChange={(e) => setPreviewMessage(e.target.value)} rows={3} />
            </Field>

            <Button onClick={() => void requestPreview()} disabled={previewLoading} className="gap-2">
                <Bot className="h-4 w-4" />
                {previewLoading ? "Generating…" : "Generate preview"}
            </Button>

            {previewError && <SaveErrorNote error={previewError} onRetry={() => void requestPreview()} onDismiss={dismissPreviewError} />}

            <div className="space-y-3 rounded-md bg-muted/40 p-3.5">
                <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                    <Bot className="h-3.5 w-3.5" />
                    {persona.name || "LimeBot"}
                    <ArrowRight className="h-3 w-3" />
                    {channelLabel}
                    <StatusPill tone={previewResult && !previewIsStale ? "ok" : "muted"} className="ml-auto">{previewIsStale ? "Stale" : "Current"}</StatusPill>
                </div>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                    {previewResult?.preview_text || (previewLoading ? "Generating preview…" : "Generate a preview to see the model response.")}
                </p>
                {previewResult?.error && <SaveErrorNote error={providerErrorInfo(previewResult.error)} onRetry={() => void requestPreview()} />}
            </div>

            <details className="group">
                <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium text-foreground">
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground transition-transform group-open:rotate-90" />
                    <Send className="h-3.5 w-3.5 text-muted-foreground" />
                    Inspect prompt excerpt
                </summary>
                <pre className="mt-2 max-h-[360px] overflow-auto whitespace-pre-wrap rounded-md bg-muted/40 p-3 text-xs leading-relaxed">
                    {previewResult?.system_prompt_excerpt || "Generate a preview to inspect the prompt excerpt."}
                </pre>
            </details>
        </SettingsSection>
    );
}

interface AdaptiveEditorProps { persona: PersonaData; setPersona: (value: PersonaData) => void; relationships: Relationship[]; avgAffinity: number; onNavigate?: (view: string) => void; }
function AdaptiveEditor({ persona, setPersona, relationships, avgAffinity, onNavigate }: AdaptiveEditorProps) {
    const adaptive = Boolean(persona.enable_dynamic_personality);
    return (
        <>
            <SettingsSection
                title="Adaptive behaviour"
                icon={Radio}
                description="Mood, reactions, and relationship context learned over time."
                badge={<StatusPill tone={adaptive ? "ok" : "warn"}>{adaptive ? "Active" : "Off"}</StatusPill>}
            >
                {!adaptive ? (
                    <Note tone="warn" title="Adaptive behavior is off">
                        Enable dynamic personality in Configuration to persist mood and relationship changes.
                        {onNavigate && (
                            <div className="pt-2">
                                <Button variant="outline" size="sm" onClick={() => onNavigate("config")}>Open Configuration</Button>
                            </div>
                        )}
                    </Note>
                ) : (
                    <div className="grid gap-5 md:grid-cols-2">
                        <Field label="Current mood" htmlFor="persona-mood">
                            <Textarea id="persona-mood" value={persona.mood} onChange={(e) => setPersona({ ...persona, mood: e.target.value })} rows={3} />
                        </Field>
                        <Field label="Reaction emoji map" htmlFor="persona-reactions">
                            <Textarea id="persona-reactions" value={persona.reaction_emojis} onChange={(e) => setPersona({ ...persona, reaction_emojis: e.target.value })} rows={3} placeholder="happy: 😊, 😂; sad: 😢" />
                        </Field>
                    </div>
                )}
            </SettingsSection>

            <SettingsSection
                title="Relationships"
                icon={Users}
                description="People LimeBot has built a profile for, strongest first."
                badge={relationships.length ? <StatusPill tone="muted">{relationships.length} total · avg affinity {avgAffinity}</StatusPill> : undefined}
            >
                {relationships.length ? (
                    <div className="divide-y divide-border/50 border-y border-border/50">
                        {relationships.map((item) => (
                            <div key={item.id} className="grid gap-3 py-3 md:grid-cols-[1fr_auto_1fr] md:items-center">
                                <div className="min-w-0">
                                    <div className="truncate text-sm font-medium text-foreground">{item.name}</div>
                                    <div className="truncate text-xs text-muted-foreground">{item.id}</div>
                                </div>
                                <div className="flex flex-wrap gap-1.5">
                                    <StatusPill tone="muted">{item.level}</StatusPill>
                                    <StatusPill tone="info">{affinityLabel(item.affinity)}</StatusPill>
                                </div>
                                <div>
                                    <div className="mb-1 flex justify-between text-xs text-muted-foreground">
                                        <span>Affinity</span>
                                        <span className="tabular-nums">{item.affinity}</span>
                                    </div>
                                    <div className="h-1.5 rounded-full bg-muted">
                                        <div className={cn("h-1.5 rounded-full", affinityTone(item.affinity))} style={{ width: `${Math.max(4, Math.min(item.affinity, 100))}%` }} />
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <EmptyState icon={Users} title="No relationship profiles yet" className="py-8" />
                )}
            </SettingsSection>
        </>
    );
}
