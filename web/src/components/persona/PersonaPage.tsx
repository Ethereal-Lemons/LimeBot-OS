import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Textarea } from "../ui/textarea";
import { Avatar, AvatarFallback, AvatarImage } from "../ui/avatar";
import { Badge } from "../ui/badge";
import { User, Sparkles, Link, MessageSquare, Save, RefreshCw, Globe, Heart, Cake, Quote, Users, Bot, Wand2, ShieldCheck, ArrowRight, Radio, Send } from "lucide-react";
import { cn } from "@/lib/utils";

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
    channel: PreviewChannel;
    model: string;
    effective_style: string;
    style_source: string;
    system_prompt_excerpt: string;
    preview_text?: string | null;
    error?: string | null;
}

const DEFAULT_PERSONA: PersonaData = {
    name: "", emoji: "", pfp_url: "", style: "", discord_style: "", telegram_style: "", whatsapp_style: "", web_style: "",
    reaction_emojis: "", soul_summary: "", catchphrases: "", interests: "", birthday: "", mood: "",
    enable_dynamic_personality: false, relationships: []
};
const CHANNELS: Array<{ id: PreviewChannel; label: string; accent: string }> = [
    { id: "web", label: "Web", accent: "text-primary" },
    { id: "discord", label: "Discord", accent: "text-[#5865F2]" },
    { id: "telegram", label: "Telegram", accent: "text-[#229ED9]" },
    { id: "whatsapp", label: "WhatsApp", accent: "text-[#25D366]" },
];

const clonePersona = (data?: Partial<PersonaData>): PersonaData =>
    JSON.parse(JSON.stringify({ ...DEFAULT_PERSONA, ...(data || {}) }));

function effectiveStyle(persona: PersonaData, channel: PreviewChannel) {
    const base = (persona.style || "").trim();
    const web = (persona.web_style || "").trim();
    const platform = (persona[`${channel}_style` as keyof PersonaData] || "").toString().trim();
    if (channel === "web") return { text: web || base, source: web ? "Web override" : "Base style" };
    return platform ? { text: platform, source: `${CHANNELS.find((item) => item.id === channel)?.label || channel} override` } : web ? { text: web, source: "Web fallback" } : { text: base, source: "Base style" };
}

const affinityTone = (affinity: number) => affinity >= 70 ? "bg-emerald-500" : affinity >= 30 ? "bg-amber-500" : "bg-slate-500";
const affinityLabel = (affinity: number) => affinity >= 70 ? "Close" : affinity >= 30 ? "Warm" : "Distant";

export function PersonaPage({ onNavigate }: PersonaPageProps) {
    const [persona, setPersona] = useState<PersonaData>(clonePersona());
    const [savedPersona, setSavedPersona] = useState<PersonaData>(clonePersona());
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [message, setMessage] = useState("");
    const [selectedChannel, setSelectedChannel] = useState<PreviewChannel>("web");
    const [previewMessage, setPreviewMessage] = useState("Introduce yourself and explain how you'd help me here.");
    const [previewLoading, setPreviewLoading] = useState(false);
    const [previewResult, setPreviewResult] = useState<PersonaPreviewResponse | null>(null);
    const [previewRequestKey, setPreviewRequestKey] = useState("");

    const fetchPersona = async () => {
        try {
            setLoading(true);
            const res = await axios.get(`${API_BASE_URL}/api/persona`);
            const next = clonePersona(res.data);
            setPersona(next);
            setSavedPersona(next);
            setPreviewResult(null);
            setPreviewRequestKey("");
        } catch (err) {
            console.error("Failed to fetch persona:", err);
            setMessage("Error loading persona");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { fetchPersona(); }, []);

    const isDirty = useMemo(() => JSON.stringify(persona) !== JSON.stringify(savedPersona), [persona, savedPersona]);
    useEffect(() => {
        if (!isDirty) return;
        const onBeforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
        window.addEventListener("beforeunload", onBeforeUnload);
        return () => window.removeEventListener("beforeunload", onBeforeUnload);
    }, [isDirty]);

    const missing = useMemo(() => {
        const list: string[] = [];
        if (!persona.name.trim()) list.push("display name");
        if (!persona.style.trim()) list.push("base style");
        return list;
    }, [persona.name, persona.style]);

    const relationships = persona.relationships || [];
    const strongest = relationships[0];
    const avgAffinity = relationships.length ? Math.round(relationships.reduce((sum, rel) => sum + rel.affinity, 0) / relationships.length) : 0;
    const selectedStyle = effectiveStyle(persona, selectedChannel);
    const previewSignature = useMemo(
        () => JSON.stringify({ persona, selectedChannel, previewMessage }),
        [persona, selectedChannel, previewMessage]
    );
    const previewIsStale = previewRequestKey !== previewSignature;

    const requestPreview = async () => {
        try {
            setPreviewLoading(true);
            const res = await axios.post<PersonaPreviewResponse>(`${API_BASE_URL}/api/persona/preview`, {
                persona,
                channel: selectedChannel,
                user_message: previewMessage,
            });
            setPreviewResult(res.data);
            setPreviewRequestKey(previewSignature);
        } catch (err) {
            console.error("Failed to preview persona:", err);
            setPreviewResult(null);
        } finally {
            setPreviewLoading(false);
        }
    };

    const handleSave = async () => {
        try {
            setSaving(true);
            const payload = clonePersona(persona);
            const res = await axios.put(`${API_BASE_URL}/api/persona`, payload);
            if (res.data.status === "success") {
                setPersona(payload);
                setSavedPersona(payload);
                setMessage("Persona saved successfully.");
                setTimeout(() => setMessage(""), 3000);
            } else {
                setMessage("Error: " + (res.data.error || "Unknown error"));
            }
        } catch (err) {
            console.error("Failed to save persona:", err);
            setMessage("Error saving persona");
        } finally {
            setSaving(false);
        }
    };

    const handleDiscard = () => { setPersona(clonePersona(savedPersona)); setMessage(""); };
    const handleReload = () => {
        if (isDirty && !window.confirm("Discard unsaved persona edits and reload from disk?")) return;
        void fetchPersona();
    };

    if (loading) return <div className="flex h-full items-center justify-center"><RefreshCw className="h-8 w-8 animate-spin text-primary" /></div>;

    return (
        <div className="flex h-full flex-col overflow-hidden">
            <div className="border-b border-border bg-card/90 backdrop-blur">
                <div className="flex flex-col gap-4 p-6 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                        <div className="flex items-center gap-2">
                            <h1 className="text-2xl font-bold text-foreground">Persona</h1>
                            <Badge variant={isDirty ? "secondary" : "outline"}>{isDirty ? "Unsaved changes" : "Saved"}</Badge>
                        </div>
                        <p className="mt-1 text-sm text-muted-foreground">Identity, channel voice, and adaptive behavior in one place.</p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <Button variant="outline" onClick={handleReload} disabled={saving} className="gap-2"><RefreshCw className="h-4 w-4" />Reload</Button>
                        <Button variant="outline" onClick={handleDiscard} disabled={!isDirty || saving}>Discard</Button>
                        <Button onClick={handleSave} disabled={saving || !isDirty} className="gap-2"><Save className="h-4 w-4" />{saving ? "Saving..." : "Save Changes"}</Button>
                    </div>
                </div>
            </div>

            <div className="flex-1 overflow-auto p-6">
                <div className="sticky top-0 z-20 mb-6 rounded-2xl border border-border/70 bg-background/92 p-4 shadow-sm backdrop-blur">
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                        <div className="space-y-1">
                            <div className="flex flex-wrap items-center gap-2">
                                <Badge variant={isDirty ? "secondary" : "outline"}>{isDirty ? "Draft differs from disk" : "No pending edits"}</Badge>
                                <Badge variant="outline">{missing.length ? `Needs ${missing.join(" + ")}` : "Identity fields look complete"}</Badge>
                                <Badge variant="outline">Dynamic persona {persona.enable_dynamic_personality ? "enabled" : "disabled"}</Badge>
                            </div>
                            <p className="text-sm text-muted-foreground">{isDirty ? "Review the preview, then save when the voice feels right." : "Persona is in sync with disk."}</p>
                        </div>
                        <div className="text-sm text-muted-foreground">{missing.length ? `Missing: ${missing.join(", ")}` : "Base persona is ready for platform tuning."}</div>
                    </div>
                </div>

                {message && <div className={`mb-4 rounded-lg p-3 text-sm ${message.includes("Error") ? "bg-destructive/10 text-destructive" : "bg-primary/10 text-primary"}`}>{message}</div>}

                <div className="grid gap-6 xl:grid-cols-[1.05fr_1.35fr]">
                    <Card className="bg-muted/30">
                        <CardHeader><CardTitle className="text-lg">Identity Snapshot</CardTitle><CardDescription>How the bot reads before any conversation starts.</CardDescription></CardHeader>
                        <CardContent className="space-y-4">
                            <div className="rounded-2xl border border-border bg-card p-5 shadow-sm">
                                <div className="flex items-start gap-4">
                                    <Avatar className="h-20 w-20 shadow-lg">
                                        <AvatarImage src={persona.pfp_url} className="object-cover" />
                                        <AvatarFallback className="bg-primary text-2xl text-primary-foreground">{persona.emoji || persona.name?.charAt(0) || "?"}</AvatarFallback>
                                    </Avatar>
                                    <div className="min-w-0 flex-1">
                                        <div className="flex flex-wrap items-center gap-2"><h3 className="text-2xl font-bold text-foreground">{persona.name || "Bot Name"}</h3><span className="text-2xl">{persona.emoji || "🍋"}</span></div>
                                        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{persona.style?.trim() || "No base style defined yet. Add a general voice so previews have something real to inherit."}</p>
                                    </div>
                                </div>
                            </div>
                            <div className="grid gap-3 sm:grid-cols-2">
                                <div className="rounded-xl border border-border bg-background/70 p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">Soul summary</div><p className="mt-2 text-sm leading-relaxed text-muted-foreground">{persona.soul_summary || "Derived from SOUL.md. Not edited here."}</p></div>
                                <div className="rounded-xl border border-border bg-background/70 p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">Editing boundary</div><p className="mt-2 text-sm leading-relaxed text-muted-foreground">This page edits identity and channel style. Core soul values still come from <code className="rounded bg-muted px-1 py-0.5">SOUL.md</code>.</p></div>
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="bg-muted/30">
                        <CardHeader><CardTitle className="flex items-center gap-2 text-lg"><Wand2 className="h-4 w-4 text-primary" />Voice Preview Studio</CardTitle><CardDescription>Run the real prompt builder against a test message for each channel.</CardDescription></CardHeader>
                        <CardContent className="space-y-4">
                            <div className="grid gap-3 md:grid-cols-4">
                                {CHANNELS.map((channel) => {
                                    const current = effectiveStyle(persona, channel.id);
                                    const selected = selectedChannel === channel.id;
                                    return (
                                        <button
                                            key={channel.id}
                                            type="button"
                                            onClick={() => setSelectedChannel(channel.id)}
                                            className={`h-full rounded-2xl border p-4 text-left transition-colors ${selected ? "border-primary bg-primary/5" : "border-border bg-background/70 hover:border-primary/30"}`}
                                        >
                                            <div className="flex flex-wrap items-start justify-between gap-2">
                                                <span className={`min-w-0 text-base font-semibold ${channel.accent}`}>{channel.label}</span>
                                                <Badge
                                                    variant={selected ? "secondary" : "outline"}
                                                    className="shrink-0 whitespace-nowrap self-start"
                                                >
                                                    {current.source}
                                                </Badge>
                                            </div>
                                            <p className="mt-2 line-clamp-4 text-sm leading-relaxed text-muted-foreground">
                                                {current.text ? `${current.text.slice(0, 110)}${current.text.length > 110 ? "..." : ""}` : "No explicit style text yet."}
                                            </p>
                                        </button>
                                    );
                                })}
                            </div>
                            <div className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
                                <div className="space-y-4 rounded-2xl border border-border bg-background/70 p-4">
                                    <div className="flex items-center justify-between gap-2"><div><div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">Effective style</div><div className="mt-1 text-lg font-semibold text-foreground">{CHANNELS.find((c) => c.id === selectedChannel)?.label}</div></div><Badge variant="secondary">{previewResult?.style_source || selectedStyle.source}</Badge></div>
                                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">{previewResult?.effective_style || selectedStyle.text || "This channel is inheriting an empty base voice. Add a base or channel style to make the preview useful."}</p>
                                    <div className="grid gap-2">
                                        <Label>Test Message</Label>
                                        <Textarea value={previewMessage} onChange={(e) => setPreviewMessage(e.target.value)} rows={4} placeholder="What should the bot respond to in this preview?" />
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <Button onClick={requestPreview} disabled={previewLoading} className="gap-2">
                                            <Bot className="h-4 w-4" />
                                            {previewLoading ? "Generating..." : "Generate Preview"}
                                        </Button>
                                        <Badge variant={previewIsStale ? "secondary" : "outline"}>{previewIsStale ? "Preview stale" : "Preview current"}</Badge>
                                    </div>
                                    <div className="rounded-xl border border-border bg-card/60 p-4">
                                        <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground"><Bot className="h-4 w-4" />Model reply</div>
                                        <div className="mt-3 rounded-2xl rounded-tl-none border border-border bg-muted/60 p-4">
                                            <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-foreground"><span>{persona.name || "LimeBot"}</span><span className="text-base">{persona.emoji || "🍋"}</span><ArrowRight className="h-3 w-3 text-muted-foreground" /><span className="text-muted-foreground">{CHANNELS.find((c) => c.id === selectedChannel)?.label}</span></div>
                                            <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">{previewResult?.preview_text || (previewLoading ? "Generating preview..." : "Generate a preview to see a real model response with the current prompt.")}</p>
                                            {previewResult?.error && <p className="mt-3 text-xs text-destructive">{previewResult.error}</p>}
                                        </div>
                                    </div>
                                </div>
                                <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
                                    <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground"><Send className="h-4 w-4" />Prompt excerpt</div>
                                    <p className="mt-2 text-sm text-muted-foreground">This is the actual stable prompt excerpt sent to the preview model, not a hardcoded sample.</p>
                                    <pre className="mt-3 max-h-[420px] overflow-auto rounded-2xl border border-border bg-muted/60 p-4 text-xs leading-relaxed text-foreground whitespace-pre-wrap">{previewResult?.system_prompt_excerpt || "Generate a preview to inspect the real prompt path for this channel."}</pre>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </div>

                <div className="mt-6 grid gap-6">
                    <Card>
                        <CardHeader><CardTitle className="text-lg">Identity Settings</CardTitle><CardDescription>Edit the public identity users see across every channel.</CardDescription></CardHeader>
                        <CardContent className="grid gap-4">
                            <div className="grid gap-4 sm:grid-cols-2">
                                <div className="grid gap-2"><label className="flex items-center gap-2 text-sm font-medium"><User className="h-4 w-4 text-muted-foreground" />Display Name</label><Input value={persona.name} onChange={(e) => setPersona({ ...persona, name: e.target.value })} placeholder="e.g., Lisa, Sage, LimeBot" /></div>
                                <div className="grid gap-2"><label className="flex items-center gap-2 text-sm font-medium"><Sparkles className="h-4 w-4 text-muted-foreground" />Emoji</label><Input value={persona.emoji} onChange={(e) => setPersona({ ...persona, emoji: e.target.value })} placeholder="e.g., 👑, 🍋, ✨" /></div>
                            </div>
                            <div className="grid gap-4 sm:grid-cols-2">
                                <div className="grid gap-2"><label className="flex items-center gap-2 text-sm font-medium"><Link className="h-4 w-4 text-muted-foreground" />Profile Picture URL</label><Input value={persona.pfp_url} onChange={(e) => setPersona({ ...persona, pfp_url: e.target.value })} placeholder="https://example.com/avatar.jpg" /></div>
                                <div className="grid gap-2"><label className="flex items-center gap-2 text-sm font-medium"><Cake className="h-4 w-4 text-muted-foreground" />Birthday</label><Input value={persona.birthday} onChange={(e) => setPersona({ ...persona, birthday: e.target.value })} placeholder="e.g., July 15th" /></div>
                            </div>
                            <div className="grid gap-2"><label className="flex items-center gap-2 text-sm font-medium"><Heart className="h-4 w-4 text-muted-foreground" />Interests</label><Input value={persona.interests} onChange={(e) => setPersona({ ...persona, interests: e.target.value })} placeholder="e.g., Coding, 3D Graphics, Electronic Music" /></div>
                            <div className="grid gap-2"><label className="flex items-center gap-2 text-sm font-medium"><Quote className="h-4 w-4 text-muted-foreground" />Catchphrases</label><Input value={persona.catchphrases} onChange={(e) => setPersona({ ...persona, catchphrases: e.target.value })} placeholder="e.g., Stay sharp!; Lemons are life." /></div>
                            <div className="grid gap-2"><label className="flex items-center gap-2 text-sm font-medium"><MessageSquare className="h-4 w-4 text-muted-foreground" />General Style / Base Personality</label><Textarea value={persona.style} onChange={(e) => setPersona({ ...persona, style: e.target.value })} placeholder="The root personality of the bot..." rows={4} /><p className="text-xs text-muted-foreground">This is the fallback voice unless a channel override is active.</p></div>
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader><CardTitle className="text-lg">Platform Overrides</CardTitle><CardDescription>Override the base voice only when a channel really needs it.</CardDescription></CardHeader>
                        <CardContent className="grid gap-4">
                            {CHANNELS.map((channel) => {
                                const current = effectiveStyle(persona, channel.id);
                                const styleKey = `${channel.id}_style` as keyof PersonaData;
                                const value = (persona[styleKey] || "").toString();
                                const updateValue = (next: string) => {
                                    setPersona({ ...persona, [styleKey]: next });
                                };
                                return (
                                    <div key={channel.id} className="rounded-2xl border border-border bg-background/60 p-4">
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                            <label className="flex items-center gap-2 text-sm font-medium">{channel.id === "web" ? <Globe className={`h-4 w-4 ${channel.accent}`} /> : <MessageSquare className={`h-4 w-4 ${channel.accent}`} />}{channel.label} Style</label>
                                            <div className="flex items-center gap-2"><Badge variant={value.trim() ? "secondary" : "outline"}>{value.trim() ? "Override active" : "Using fallback"}</Badge><Badge variant="outline">{current.source}</Badge></div>
                                        </div>
                                        <Textarea value={value} onChange={(e) => updateValue(e.target.value)} placeholder={channel.id === "web" ? (persona.style || "Fallback to base style...") : (persona.web_style || persona.style || "Fallback to previous layer...")} rows={3} className="mt-3" />
                                        <p className="mt-2 text-xs text-muted-foreground">Effective fallback path: {current.source}.</p>
                                    </div>
                                );
                            })}
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader><CardTitle className="flex items-center gap-2 text-lg"><Radio className="h-4 w-4 text-primary" />Adaptive Persona Dashboard</CardTitle><CardDescription>Mood, reactions, and relationship health without a dead disabled form.</CardDescription></CardHeader>
                        <CardContent className="space-y-6">
                            <div className="grid gap-3 md:grid-cols-4">
                                <div className="rounded-2xl border border-border bg-background/70 p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">Status</div><div className="mt-2 flex items-center gap-2 text-sm font-semibold text-foreground"><ShieldCheck className={`h-4 w-4 ${persona.enable_dynamic_personality ? "text-primary" : "text-muted-foreground"}`} />{persona.enable_dynamic_personality ? "Live" : "Disabled"}</div></div>
                                <div className="rounded-2xl border border-border bg-background/70 p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">Relationships</div><div className="mt-2 text-2xl font-bold text-foreground">{relationships.length}</div></div>
                                <div className="rounded-2xl border border-border bg-background/70 p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">Avg affinity</div><div className="mt-2 text-2xl font-bold text-foreground">{avgAffinity}</div></div>
                                <div className="rounded-2xl border border-border bg-background/70 p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">Strongest tie</div><div className="mt-2 text-sm font-semibold text-foreground">{strongest ? strongest.name : "None yet"}</div><div className="text-xs text-muted-foreground">{strongest ? `${strongest.level} • ${strongest.affinity}` : "No profile data"}</div></div>
                            </div>

                            {!persona.enable_dynamic_personality ? (
                                <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
                                    <div className="rounded-2xl border border-amber-500/20 bg-amber-500/5 p-5">
                                        <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-amber-500">Adaptive layer offline</div>
                                        <h3 className="mt-2 text-lg font-semibold text-foreground">Relationship history is visible, but live mood tracking is off.</h3>
                                        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">Enable <code className="rounded bg-background px-1 py-0.5">ENABLE_DYNAMIC_PERSONALITY</code> in configuration to persist mood shifts, relationship changes, and autonomous reactions.</p>
                                        {onNavigate && <Button variant="outline" className="mt-4" onClick={() => onNavigate("config")}>Open Configuration</Button>}
                                    </div>
                                    <div className="rounded-2xl border border-border bg-background/70 p-5"><div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">What unlocks</div><ul className="mt-3 space-y-2 text-sm text-muted-foreground"><li>Live mood notes in prompts</li><li>User relationship evolution</li><li>Reaction emoji routing</li><li>More adaptive social tone</li></ul></div>
                                </div>
                            ) : (
                                <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
                                    <div className="rounded-2xl border border-border bg-background/70 p-5"><label className="flex items-center gap-2 text-sm font-medium"><Sparkles className="h-4 w-4 text-primary" />Current Mood</label><Textarea value={persona.mood} onChange={(e) => setPersona({ ...persona, mood: e.target.value })} placeholder="How are you feeling right now?" rows={3} className="mt-3" /><p className="mt-2 text-xs text-muted-foreground">Mood notes affect prompt framing.</p></div>
                                    <div className="rounded-2xl border border-border bg-background/70 p-5"><label className="flex items-center gap-2 text-sm font-medium"><Sparkles className="h-4 w-4 text-amber-500" />Autonomous Reactions</label><Input value={persona.reaction_emojis} onChange={(e) => setPersona({ ...persona, reaction_emojis: e.target.value })} placeholder="happy:😊,😂; sad:😢,😭; ..." className="mt-3" /><p className="mt-2 text-xs text-muted-foreground">Keep this compact and structured.</p></div>
                                </div>
                            )}

                            <div className={cn("relative rounded-2xl border border-border bg-card/50 transition-all", !persona.enable_dynamic_personality && "overflow-hidden")}>
                                {!persona.enable_dynamic_personality && (
                                    <div className="pointer-events-none absolute inset-0 z-10 rounded-2xl backdrop-blur-[2px] bg-background/40" />
                                )}
                                <div className="flex items-center justify-between border-b border-border px-5 py-4"><div><div className="flex items-center gap-2 text-sm font-semibold text-foreground"><Users className="h-4 w-4 text-muted-foreground" />Relationships</div><p className="mt-1 text-xs text-muted-foreground">Sorted by affinity, so pressure and closeness are obvious.</p></div><Badge variant="outline">{relationships.length} profiles</Badge></div>
                                <div className="divide-y divide-border">
                                    {relationships.length > 0 ? relationships.map((rel) => (
                                        <div key={rel.id} className="grid gap-3 px-5 py-4 md:grid-cols-[1.1fr_0.7fr_1fr] md:items-center">
                                            <div className="min-w-0"><div className="font-medium text-foreground">{rel.name}</div><div className="text-xs text-muted-foreground">{rel.id}</div></div>
                                            <div className="flex items-center gap-2"><Badge variant="outline">{rel.level}</Badge><Badge variant="secondary">{affinityLabel(rel.affinity)}</Badge></div>
                                            <div><div className="mb-1 flex items-center justify-between text-xs text-muted-foreground"><span>Affinity</span><span className="font-semibold text-foreground">{rel.affinity}</span></div><div className="h-2 rounded-full bg-muted"><div className={`h-2 rounded-full ${affinityTone(rel.affinity)}`} style={{ width: `${Math.max(4, Math.min(rel.affinity, 100))}%` }} /></div></div>
                                        </div>
                                    )) : <div className="px-5 py-8 text-center text-sm text-muted-foreground">No relationship profiles recorded yet.</div>}
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>
    );
}
