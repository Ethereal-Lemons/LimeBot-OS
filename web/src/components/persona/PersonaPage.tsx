import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Textarea } from "../ui/textarea";
import { Avatar, AvatarFallback, AvatarImage } from "../ui/avatar";
import { Badge } from "../ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../ui/tabs";
import {
    ArrowRight, Bot, Cake, Globe, Heart, Link, MessageSquare, Quote,
    Radio, RefreshCw, Save, Send, ShieldCheck, Sparkles, User, Users, Wand2,
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
const CHANNELS: Array<{ id: PreviewChannel; label: string; accent: string }> = [
    { id: "web", label: "Web", accent: "text-primary" },
    { id: "discord", label: "Discord", accent: "text-[#5865F2]" },
    { id: "telegram", label: "Telegram", accent: "text-[#229ED9]" },
    { id: "whatsapp", label: "WhatsApp", accent: "text-[#25D366]" },
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

const affinityTone = (value: number) => value >= 70 ? "bg-primary" : value >= 30 ? "bg-amber-500" : "bg-slate-500";
const affinityLabel = (value: number) => value >= 70 ? "Close" : value >= 30 ? "Warm" : "Distant";

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
            const response = await axios.get(`${API_BASE_URL}/api/persona`);
            const next = clonePersona(response.data);
            setPersona(next);
            setSavedPersona(next);
            setPreviewResult(null);
            setPreviewRequestKey("");
        } catch (error) {
            console.error("Failed to fetch persona:", error);
            setMessage("Error loading persona");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { void fetchPersona(); }, []);
    const isDirty = useMemo(() => JSON.stringify(persona) !== JSON.stringify(savedPersona), [persona, savedPersona]);
    useEffect(() => {
        if (!isDirty) return;
        const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
        window.addEventListener("beforeunload", warn);
        return () => window.removeEventListener("beforeunload", warn);
    }, [isDirty]);

    const missing = useMemo(() => [!persona.name.trim() && "display name", !persona.style.trim() && "base style"].filter(Boolean) as string[], [persona.name, persona.style]);
    const relationships = persona.relationships || [];
    const strongest = relationships[0];
    const avgAffinity = relationships.length ? Math.round(relationships.reduce((sum, item) => sum + item.affinity, 0) / relationships.length) : 0;
    const overrideCount = CHANNELS.filter((channel) => String(persona[`${channel.id}_style` as keyof PersonaData] || "").trim()).length;
    const selectedStyle = effectiveStyle(persona, selectedChannel);
    const previewSignature = useMemo(() => JSON.stringify({ persona, selectedChannel, previewMessage }), [persona, selectedChannel, previewMessage]);
    const previewIsStale = previewRequestKey !== previewSignature;

    const requestPreview = async () => {
        try {
            setPreviewLoading(true);
            const response = await axios.post<PersonaPreviewResponse>(`${API_BASE_URL}/api/persona/preview`, { persona, channel: selectedChannel, user_message: previewMessage });
            setPreviewResult(response.data);
            setPreviewRequestKey(previewSignature);
        } catch (error) {
            console.error("Failed to preview persona:", error);
            setPreviewResult(null);
        } finally {
            setPreviewLoading(false);
        }
    };

    const handleSave = async () => {
        try {
            setSaving(true);
            const payload = clonePersona(persona);
            const response = await axios.put(`${API_BASE_URL}/api/persona`, payload);
            if (response.data.status !== "success") throw new Error(response.data.error || "Unknown error");
            setPersona(payload);
            setSavedPersona(payload);
            setMessage("Persona saved successfully.");
            window.setTimeout(() => setMessage(""), 3000);
        } catch (error) {
            console.error("Failed to save persona:", error);
            setMessage(`Error saving persona${error instanceof Error ? `: ${error.message}` : ""}`);
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
        <div className="flex h-full flex-col overflow-hidden bg-background">
            <header className="border-b border-border bg-card/90 px-5 py-4 backdrop-blur md:px-7">
                <div className="mx-auto flex max-w-7xl flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                        <div className="flex flex-wrap items-center gap-2">
                            <h1 className="text-2xl font-bold">Persona Studio</h1>
                            <Badge variant={isDirty ? "secondary" : "outline"}>{isDirty ? "Unsaved" : "Saved"}</Badge>
                            {missing.length > 0 && <Badge variant="outline">Missing {missing.join(" + ")}</Badge>}
                        </div>
                        <p className="mt-1 text-sm text-muted-foreground">Shape identity, voice, and social behavior without editing persona files by hand.</p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <Button variant="ghost" onClick={handleReload} disabled={saving} className="gap-2"><RefreshCw className="h-4 w-4" />Reload</Button>
                        <Button variant="outline" onClick={handleDiscard} disabled={!isDirty || saving}>Discard</Button>
                        <Button onClick={handleSave} disabled={saving || !isDirty} className="gap-2"><Save className="h-4 w-4" />{saving ? "Saving…" : "Save persona"}</Button>
                    </div>
                </div>
            </header>

            <main className="flex-1 overflow-auto p-4 md:p-7">
                <div className="mx-auto max-w-7xl space-y-5">
                    {message && <div className={cn("rounded-xl border p-3 text-sm", message.startsWith("Error") ? "border-destructive/30 bg-destructive/10 text-destructive" : "border-primary/20 bg-primary/10 text-primary")}>{message}</div>}

                    <Card className="overflow-hidden border-border/70 bg-gradient-to-br from-card via-card to-primary/5">
                        <CardContent className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-center md:p-6">
                            <div className="flex min-w-0 items-start gap-4">
                                <Avatar className="h-20 w-20 shrink-0 border shadow-md"><AvatarImage src={persona.pfp_url} className="object-cover" /><AvatarFallback className="bg-primary text-2xl text-primary-foreground">{persona.emoji || persona.name.charAt(0) || "?"}</AvatarFallback></Avatar>
                                <div className="min-w-0">
                                    <div className="flex flex-wrap items-center gap-2"><h2 className="text-2xl font-bold">{persona.name || "Unnamed persona"}</h2><span className="text-2xl">{persona.emoji || "🍋"}</span></div>
                                    <p className="mt-2 line-clamp-3 max-w-3xl text-sm leading-relaxed text-muted-foreground">{persona.style || "Add a base voice to define how this persona speaks and behaves."}</p>
                                    <div className="mt-3 flex flex-wrap gap-2"><Badge variant="outline">{overrideCount} channel overrides</Badge><Badge variant="outline">{relationships.length} relationships</Badge><Badge variant={persona.enable_dynamic_personality ? "secondary" : "outline"}>Adaptive {persona.enable_dynamic_personality ? "on" : "off"}</Badge></div>
                                </div>
                            </div>
                            <div className="grid grid-cols-3 gap-2 text-center">
                                <Metric value={CHANNELS.length} label="Channels" />
                                <Metric value={avgAffinity} label="Affinity" />
                                <Metric value={strongest?.name || "—"} label="Closest" compact />
                            </div>
                        </CardContent>
                    </Card>

                    <Tabs defaultValue="identity" className="grid gap-5 lg:grid-cols-[230px_minmax(0,1fr)] lg:items-start">
                        <Card className="lg:sticky lg:top-0"><CardContent className="p-3"><p className="px-3 pb-2 pt-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">Edit persona</p><TabsList className="grid h-auto grid-cols-2 gap-1 bg-transparent p-0 sm:grid-cols-4 lg:grid-cols-1"><PersonaTab value="identity" icon={<User className="h-4 w-4" />} label="Identity" /><PersonaTab value="voice" icon={<MessageSquare className="h-4 w-4" />} label="Voice" /><PersonaTab value="preview" icon={<Wand2 className="h-4 w-4" />} label="Preview" /><PersonaTab value="adaptive" icon={<Radio className="h-4 w-4" />} label="Adaptive" /></TabsList><div className="mt-3 hidden border-t px-3 pt-3 text-xs leading-relaxed text-muted-foreground lg:block">Core values remain in <code className="rounded bg-muted px-1 py-0.5">SOUL.md</code>.</div></CardContent></Card>

                        <div className="min-w-0">
                            <TabsContent value="identity" className="mt-0"><IdentityEditor persona={persona} setPersona={setPersona} /></TabsContent>
                            <TabsContent value="voice" className="mt-0"><VoiceEditor persona={persona} setPersona={setPersona} /></TabsContent>
                            <TabsContent value="preview" className="mt-0"><PreviewStudio persona={persona} selectedChannel={selectedChannel} setSelectedChannel={setSelectedChannel} selectedStyle={selectedStyle} previewMessage={previewMessage} setPreviewMessage={setPreviewMessage} previewLoading={previewLoading} previewResult={previewResult} previewIsStale={previewIsStale} requestPreview={requestPreview} /></TabsContent>
                            <TabsContent value="adaptive" className="mt-0"><AdaptiveEditor persona={persona} setPersona={setPersona} relationships={relationships} avgAffinity={avgAffinity} onNavigate={onNavigate} /></TabsContent>
                        </div>
                    </Tabs>
                </div>
            </main>
        </div>
    );
}

function Metric({ value, label, compact = false }: { value: string | number; label: string; compact?: boolean }) {
    return <div className="min-w-20 rounded-xl border bg-background/60 px-3 py-2"><div className={cn("font-bold", compact ? "truncate text-sm" : "text-lg")}>{value}</div><div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div></div>;
}

function PersonaTab({ value, icon, label }: { value: string; icon: React.ReactNode; label: string }) {
    return <TabsTrigger value={value} className="h-auto justify-start gap-2 px-3 py-2.5">{icon}{label}</TabsTrigger>;
}

function IdentityEditor({ persona, setPersona }: { persona: PersonaData; setPersona: (value: PersonaData) => void }) {
    return <Card><CardHeader className="pb-4"><CardTitle className="text-lg">Identity</CardTitle><CardDescription>The public profile people see across every channel.</CardDescription></CardHeader><CardContent className="grid gap-5"><div className="grid gap-4 sm:grid-cols-2"><Field label="Display name" icon={<User className="h-4 w-4" />}><Input value={persona.name} onChange={(e) => setPersona({ ...persona, name: e.target.value })} placeholder="Lisa, Sage, LimeBot…" /></Field><Field label="Signature emoji" icon={<Sparkles className="h-4 w-4" />}><Input value={persona.emoji} onChange={(e) => setPersona({ ...persona, emoji: e.target.value })} placeholder="🍋" /></Field><Field label="Avatar URL" icon={<Link className="h-4 w-4" />}><Input value={persona.pfp_url} onChange={(e) => setPersona({ ...persona, pfp_url: e.target.value })} placeholder="https://example.com/avatar.jpg" /></Field><Field label="Birthday" icon={<Cake className="h-4 w-4" />}><Input value={persona.birthday} onChange={(e) => setPersona({ ...persona, birthday: e.target.value })} placeholder="July 15" /></Field></div><div className="grid gap-4 sm:grid-cols-2"><Field label="Interests" icon={<Heart className="h-4 w-4" />}><Input value={persona.interests} onChange={(e) => setPersona({ ...persona, interests: e.target.value })} placeholder="Coding, 3D art, electronic music…" /></Field><Field label="Catchphrases" icon={<Quote className="h-4 w-4" />}><Input value={persona.catchphrases} onChange={(e) => setPersona({ ...persona, catchphrases: e.target.value })} placeholder="Separate phrases with semicolons" /></Field></div><details className="rounded-xl border bg-muted/20"><summary className="cursor-pointer px-4 py-3 text-sm font-medium">View read-only soul summary</summary><div className="border-t px-4 py-3 text-sm leading-relaxed text-muted-foreground">{persona.soul_summary || "No soul summary is available."}</div></details></CardContent></Card>;
}

function Field({ label, icon, children }: { label: string; icon: React.ReactNode; children: React.ReactNode }) {
    return <div className="grid gap-2"><Label className="flex items-center gap-2">{<span className="text-muted-foreground">{icon}</span>}{label}</Label>{children}</div>;
}

function VoiceEditor({ persona, setPersona }: { persona: PersonaData; setPersona: (value: PersonaData) => void }) {
    return <div className="space-y-4"><Card><CardHeader className="pb-4"><CardTitle className="text-lg">Base voice</CardTitle><CardDescription>The default personality inherited by every channel.</CardDescription></CardHeader><CardContent><Textarea value={persona.style} onChange={(e) => setPersona({ ...persona, style: e.target.value })} placeholder="Warm, concise, playful, direct…" rows={5} /></CardContent></Card><Card><CardHeader className="pb-4"><CardTitle className="text-lg">Channel overrides</CardTitle><CardDescription>Leave a field empty to inherit its fallback.</CardDescription></CardHeader><CardContent className="grid gap-4 md:grid-cols-2">{CHANNELS.map((channel) => { const current = effectiveStyle(persona, channel.id); const key = `${channel.id}_style` as keyof PersonaData; const value = String(persona[key] || ""); return <div key={channel.id} className="rounded-xl border bg-background/60 p-4"><div className="flex flex-wrap items-center justify-between gap-2"><Label className={cn("flex items-center gap-2", channel.accent)}>{channel.id === "web" ? <Globe className="h-4 w-4" /> : <MessageSquare className="h-4 w-4" />}{channel.label}</Label><Badge variant={value.trim() ? "secondary" : "outline"}>{value.trim() ? "Custom" : current.source}</Badge></div><Textarea value={value} onChange={(e) => setPersona({ ...persona, [key]: e.target.value })} placeholder={`Inherits: ${current.text || "empty base voice"}`} rows={3} className="mt-3" /></div>; })}</CardContent></Card></div>;
}

interface PreviewStudioProps { persona: PersonaData; selectedChannel: PreviewChannel; setSelectedChannel: (value: PreviewChannel) => void; selectedStyle: { text: string; source: string }; previewMessage: string; setPreviewMessage: (value: string) => void; previewLoading: boolean; previewResult: PersonaPreviewResponse | null; previewIsStale: boolean; requestPreview: () => Promise<void>; }
function PreviewStudio(props: PreviewStudioProps) {
    const { persona, selectedChannel, setSelectedChannel, selectedStyle, previewMessage, setPreviewMessage, previewLoading, previewResult, previewIsStale, requestPreview } = props;
    return <Card><CardHeader className="pb-4"><CardTitle className="flex items-center gap-2 text-lg"><Wand2 className="h-4 w-4 text-primary" />Voice preview</CardTitle><CardDescription>Test the real prompt path before saving.</CardDescription></CardHeader><CardContent className="space-y-5"><div className="flex flex-wrap gap-2">{CHANNELS.map((channel) => <Button key={channel.id} size="sm" variant={selectedChannel === channel.id ? "default" : "outline"} onClick={() => setSelectedChannel(channel.id)}>{channel.label}</Button>)}</div><div className="grid gap-5 xl:grid-cols-[0.9fr_1.1fr]"><div className="space-y-4"><div className="rounded-xl border bg-muted/20 p-4"><div className="flex justify-between gap-2"><span className="text-sm font-semibold">Effective style</span><Badge variant="outline">{previewResult?.style_source || selectedStyle.source}</Badge></div><p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">{previewResult?.effective_style || selectedStyle.text || "No effective style yet."}</p></div><Field label="Test message" icon={<MessageSquare className="h-4 w-4" />}><Textarea value={previewMessage} onChange={(e) => setPreviewMessage(e.target.value)} rows={4} /></Field><Button onClick={() => void requestPreview()} disabled={previewLoading} className="gap-2"><Bot className="h-4 w-4" />{previewLoading ? "Generating…" : "Generate preview"}</Button></div><div className="rounded-xl border bg-muted/20 p-4"><div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground"><Bot className="h-4 w-4" />{persona.name || "LimeBot"}<ArrowRight className="h-3 w-3" />{CHANNELS.find((item) => item.id === selectedChannel)?.label}<Badge variant={previewIsStale ? "secondary" : "outline"} className="ml-auto">{previewIsStale ? "Stale" : "Current"}</Badge></div><p className="mt-4 whitespace-pre-wrap text-sm leading-relaxed">{previewResult?.preview_text || (previewLoading ? "Generating preview…" : "Generate a preview to see the model response.")}</p>{previewResult?.error && <p className="mt-3 text-xs text-destructive">{previewResult.error}</p>}</div></div><details className="rounded-xl border"><summary className="flex cursor-pointer items-center gap-2 px-4 py-3 text-sm font-medium"><Send className="h-4 w-4" />Inspect prompt excerpt</summary><pre className="max-h-[360px] overflow-auto whitespace-pre-wrap border-t bg-muted/30 p-4 text-xs leading-relaxed">{previewResult?.system_prompt_excerpt || "Generate a preview to inspect the prompt excerpt."}</pre></details></CardContent></Card>;
}

interface AdaptiveEditorProps { persona: PersonaData; setPersona: (value: PersonaData) => void; relationships: Relationship[]; avgAffinity: number; onNavigate?: (view: string) => void; }
function AdaptiveEditor({ persona, setPersona, relationships, avgAffinity, onNavigate }: AdaptiveEditorProps) {
    return <Card><CardHeader className="pb-4"><CardTitle className="flex items-center gap-2 text-lg"><Radio className="h-4 w-4 text-primary" />Adaptive personality</CardTitle><CardDescription>Mood, reactions, and relationship context learned over time.</CardDescription></CardHeader><CardContent className="space-y-5"><div className="grid gap-3 sm:grid-cols-3"><Metric value={persona.enable_dynamic_personality ? "Active" : "Off"} label="Status" compact /><Metric value={relationships.length} label="Relationships" /><Metric value={avgAffinity} label="Avg affinity" /></div>{!persona.enable_dynamic_personality ? <div className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-4"><div className="flex items-center gap-2 font-semibold"><ShieldCheck className="h-4 w-4" />Adaptive behavior is off</div><p className="mt-1 text-sm text-muted-foreground">Enable dynamic personality in Configuration to persist mood and relationship changes.</p>{onNavigate && <Button variant="outline" size="sm" className="mt-3" onClick={() => onNavigate("config")}>Open Configuration</Button>}</div> : <div className="grid gap-4 md:grid-cols-2"><Field label="Current mood" icon={<Sparkles className="h-4 w-4" />}><Textarea value={persona.mood} onChange={(e) => setPersona({ ...persona, mood: e.target.value })} rows={3} /></Field><Field label="Reaction emoji map" icon={<Sparkles className="h-4 w-4" />}><Textarea value={persona.reaction_emojis} onChange={(e) => setPersona({ ...persona, reaction_emojis: e.target.value })} rows={3} placeholder="happy: 😊, 😂; sad: 😢" /></Field></div>}<div className="overflow-hidden rounded-xl border"><div className="flex items-center justify-between border-b px-4 py-3"><div className="flex items-center gap-2 text-sm font-semibold"><Users className="h-4 w-4" />Relationships</div><Badge variant="outline">{relationships.length}</Badge></div><div className="divide-y">{relationships.length ? relationships.map((item) => <div key={item.id} className="grid gap-3 px-4 py-3 md:grid-cols-[1fr_auto_1fr] md:items-center"><div className="min-w-0"><div className="truncate font-medium">{item.name}</div><div className="truncate text-xs text-muted-foreground">{item.id}</div></div><div className="flex gap-2"><Badge variant="outline">{item.level}</Badge><Badge variant="secondary">{affinityLabel(item.affinity)}</Badge></div><div><div className="mb-1 flex justify-between text-xs text-muted-foreground"><span>Affinity</span><span>{item.affinity}</span></div><div className="h-1.5 rounded-full bg-muted"><div className={cn("h-1.5 rounded-full", affinityTone(item.affinity))} style={{ width: `${Math.max(4, Math.min(item.affinity, 100))}%` }} /></div></div></div>) : <div className="p-6 text-center text-sm text-muted-foreground">No relationship profiles yet.</div>}</div></div></CardContent></Card>;
}
