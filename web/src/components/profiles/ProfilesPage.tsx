import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { Archive, Bot, Loader2, Plus, RotateCcw, Save, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { getStoredProfileId, type AgentProfile, type AgentProfilePolicy } from "@/lib/agent-profiles";

type PersonaDraft = { soul: string; identity: string; mood: string };
type PersonaSection = keyof PersonaDraft;
type ModelOption = { id: string; name: string; provider?: string };

const emptyPolicy: AgentProfilePolicy = {
    allowed_tools: null,
    allowed_paths: null,
    approval_policy: null,
};

function splitPolicyList(value: string): string[] {
    return Array.from(new Set(value.split(/[\n,;]+/).map((item) => item.trim()).filter(Boolean)));
}

function errorMessage(error: unknown): string {
    if (axios.isAxiosError(error)) {
        const detail = error.response?.data?.detail;
        if (typeof detail === "string") return detail;
        if (error.message) return error.message;
    }
    return "The profile change could not be saved.";
}

export function ProfilesPage({
    onProfilesChanged,
}: {
    onProfilesChanged?: (profiles: AgentProfile[]) => void;
}) {
    const onProfilesChangedRef = useRef(onProfilesChanged);
    useEffect(() => { onProfilesChangedRef.current = onProfilesChanged; }, [onProfilesChanged]);
    const [profiles, setProfiles] = useState<AgentProfile[]>([]);
    const [selectedId, setSelectedId] = useState("default");
    const [loading, setLoading] = useState(true);
    const [showArchived, setShowArchived] = useState(false);
    const [newName, setNewName] = useState("");
    const [creating, setCreating] = useState(false);
    const [savingProfile, setSavingProfile] = useState(false);
    const [savingPersona, setSavingPersona] = useState<PersonaSection | null>(null);
    const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
    const [availableTools, setAvailableTools] = useState<string[]>([]);

    const [displayName, setDisplayName] = useState("");
    const [modelOverride, setModelOverride] = useState("__inherit");
    const [toolsRestricted, setToolsRestricted] = useState(false);
    const [toolsText, setToolsText] = useState("");
    const [pathsRestricted, setPathsRestricted] = useState(false);
    const [pathsText, setPathsText] = useState("");
    const [approvalPolicy, setApprovalPolicy] = useState("inherit");
    const [persona, setPersona] = useState<PersonaDraft>({ soul: "", identity: "", mood: "" });
    const [personaLoading, setPersonaLoading] = useState(false);

    const selectedProfile = useMemo(
        () => profiles.find((profile) => profile.profile_id === selectedId),
        [profiles, selectedId],
    );
    const visibleProfiles = useMemo(
        () => profiles.filter((profile) => showArchived || !profile.archived),
        [profiles, showArchived],
    );

    const refreshProfiles = async (preferredId?: string) => {
        const response = await api.get("/api/profiles", { params: { include_archived: true } });
        const nextProfiles = Array.isArray(response.data?.profiles)
            ? response.data.profiles as AgentProfile[]
            : [];
        setProfiles(nextProfiles);
        onProfilesChangedRef.current?.(nextProfiles);
        const requestedId = preferredId || selectedId;
        const nextSelection = nextProfiles.some((profile) => profile.profile_id === requestedId)
            ? requestedId
            : "default";
        setSelectedId(nextSelection);
    };

    useEffect(() => {
        let mounted = true;
        Promise.all([
            api.get("/api/profiles", { params: { include_archived: true } }),
            api.get("/api/llm/models"),
        ]).then(([profilesResponse, modelsResponse]) => {
            if (!mounted) return;
            const nextProfiles = Array.isArray(profilesResponse.data?.profiles)
                ? profilesResponse.data.profiles as AgentProfile[]
                : [];
            const nextModels = Array.isArray(modelsResponse.data?.models)
                ? modelsResponse.data.models as ModelOption[]
                : [];
            setProfiles(nextProfiles);
            onProfilesChangedRef.current?.(nextProfiles);
            setModelOptions(nextModels);
            setAvailableTools(Array.isArray(profilesResponse.data?.available_tools)
                ? profilesResponse.data.available_tools.map(String)
                : []);
            const stored = getStoredProfileId();
            setSelectedId(
                nextProfiles.some((profile) => profile.profile_id === stored && !profile.archived)
                    ? stored
                    : "default",
            );
        }).catch((error: unknown) => {
            if (mounted) toast.error(errorMessage(error));
        }).finally(() => {
            if (mounted) setLoading(false);
        });
        return () => { mounted = false; };
    }, []);

    useEffect(() => {
        if (!selectedProfile) return;
        const policy = selectedProfile.policy || emptyPolicy;
        setDisplayName(selectedProfile.display_name);
        setModelOverride(selectedProfile.model || "__inherit");
        setToolsRestricted(policy.allowed_tools !== null);
        setToolsText((policy.allowed_tools || []).join("\n"));
        setPathsRestricted(policy.allowed_paths !== null);
        setPathsText((policy.allowed_paths || []).join("\n"));
        setApprovalPolicy(policy.approval_policy || "inherit");

        let mounted = true;
        setPersonaLoading(true);
        api.get(`/api/profiles/${encodeURIComponent(selectedProfile.profile_id)}/persona`)
            .then((response) => {
                if (!mounted) return;
                setPersona({
                    soul: String(response.data?.soul || ""),
                    identity: String(response.data?.identity || ""),
                    mood: String(response.data?.mood || ""),
                });
            })
            .catch((error: unknown) => {
                if (mounted) toast.error(errorMessage(error));
            })
            .finally(() => {
                if (mounted) setPersonaLoading(false);
            });
        return () => { mounted = false; };
    }, [selectedProfile]);

    const createProfile = async () => {
        const name = newName.trim();
        if (!name) return;
        setCreating(true);
        try {
            const response = await api.post("/api/profiles", { display_name: name });
            const created = response.data?.profile as AgentProfile;
            await refreshProfiles(created.profile_id);
            setNewName("");
            toast.success(`${created.display_name} is ready to configure.`);
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setCreating(false);
        }
    };

    const saveProfile = async () => {
        if (!selectedProfile) return;
        const policy: AgentProfilePolicy = {
            allowed_tools: toolsRestricted ? splitPolicyList(toolsText) : null,
            allowed_paths: pathsRestricted ? splitPolicyList(pathsText) : null,
            approval_policy: approvalPolicy === "inherit" ? null : approvalPolicy,
        };
        setSavingProfile(true);
        try {
            await api.patch(`/api/profiles/${encodeURIComponent(selectedProfile.profile_id)}`, {
                display_name: displayName,
                model: modelOverride === "__inherit" ? null : modelOverride,
                policy,
            });
            await refreshProfiles(selectedProfile.profile_id);
            toast.success("Agent settings saved.");
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setSavingProfile(false);
        }
    };

    const savePersonaSection = async (section: PersonaSection) => {
        if (!selectedProfile) return;
        setSavingPersona(section);
        try {
            await api.put(
                `/api/profiles/${encodeURIComponent(selectedProfile.profile_id)}/persona/${section}`,
                { content: persona[section] },
            );
            toast.success(`${section[0].toUpperCase()}${section.slice(1)} saved.`);
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setSavingPersona(null);
        }
    };

    const archiveProfile = async () => {
        if (!selectedProfile || selectedProfile.profile_id === "default") return;
        try {
            await api.post(`/api/profiles/${encodeURIComponent(selectedProfile.profile_id)}/archive`);
            await refreshProfiles("default");
            toast.success(`${selectedProfile.display_name} archived.`);
        } catch (error) {
            toast.error(errorMessage(error));
        }
    };

    const restoreProfile = async () => {
        if (!selectedProfile) return;
        try {
            await api.post(`/api/profiles/${encodeURIComponent(selectedProfile.profile_id)}/unarchive`);
            await refreshProfiles(selectedProfile.profile_id);
            toast.success(`${selectedProfile.display_name} restored.`);
        } catch (error) {
            toast.error(errorMessage(error));
        }
    };

    if (loading) {
        return <div className="flex h-full items-center justify-center"><Loader2 className="h-7 w-7 animate-spin text-primary" /></div>;
    }

    return (
        <div className="h-full overflow-y-auto bg-background/50 p-5 md:p-8">
            <div className="mx-auto max-w-6xl space-y-6">
                <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                    <div>
                        <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-primary">
                            <Bot className="h-4 w-4" /> Agent roster
                        </div>
                        <h1 className="text-2xl font-bold">Your agents, in one place</h1>
                        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                            Each agent keeps its own conversations, persona, and memory. The default agent continues using LimeBot’s existing files and history.
                        </p>
                    </div>
                    <div className="flex gap-2">
                        <Input
                            aria-label="New agent name"
                            className="w-44"
                            placeholder="New agent name"
                            value={newName}
                            onChange={(event) => setNewName(event.target.value)}
                            onKeyDown={(event) => { if (event.key === "Enter") void createProfile(); }}
                        />
                        <Button onClick={() => void createProfile()} disabled={creating || !newName.trim()}>
                            {creating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
                            Create
                        </Button>
                    </div>
                </header>

                <div className="grid gap-5 lg:grid-cols-[250px_minmax(0,1fr)]">
                    <Card className="h-fit border-border/70 bg-card/80">
                        <CardHeader className="pb-3">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-base">Roster</CardTitle>
                                <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => setShowArchived((value) => !value)}>
                                    {showArchived ? "Hide archived" : "Show archived"}
                                </Button>
                            </div>
                            <CardDescription>{visibleProfiles.length} agent{visibleProfiles.length === 1 ? "" : "s"}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-1 px-3 pb-3">
                            {visibleProfiles.map((profile) => (
                                <button
                                    key={profile.profile_id}
                                    type="button"
                                    onClick={() => setSelectedId(profile.profile_id)}
                                    className={`w-full rounded-lg border px-3 py-2.5 text-left transition-colors ${selectedId === profile.profile_id ? "border-primary/40 bg-primary/10" : "border-transparent hover:border-border hover:bg-muted/50"} ${profile.archived ? "opacity-60" : ""}`}
                                >
                                    <span className="flex min-w-0 items-center justify-between gap-2">
                                        <span className="truncate text-sm font-medium">{profile.display_name}</span>
                                        {profile.profile_id === "default" && <Badge variant="outline" className="h-5 px-2 text-[10px]">Default</Badge>}
                                        {profile.archived && <Badge variant="secondary" className="h-5 px-2 text-[10px]">Archived</Badge>}
                                    </span>
                                    <span className="mt-1 block truncate text-[11px] text-muted-foreground">{profile.resolved_model}</span>
                                </button>
                            ))}
                        </CardContent>
                    </Card>

                    {selectedProfile && (
                        <div className="space-y-5">
                            <Card className="border-border/70 bg-card/80">
                                <CardHeader className="pb-4">
                                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                                        <div>
                                            <CardTitle className="text-lg">Agent settings</CardTitle>
                                            <CardDescription className="mt-1">Choose a name, model, and the tools this agent can use. Each agent keeps its own persona, memory, and chat history.</CardDescription>
                                        </div>
                                        {selectedProfile.archived ? (
                                            <Button variant="outline" size="sm" onClick={() => void restoreProfile()}><RotateCcw className="mr-2 h-4 w-4" />Restore</Button>
                                        ) : selectedProfile.profile_id !== "default" ? (
                                            <Button variant="outline" size="sm" onClick={() => void archiveProfile()}><Archive className="mr-2 h-4 w-4" />Archive</Button>
                                        ) : null}
                                    </div>
                                </CardHeader>
                                <CardContent className="grid gap-5 md:grid-cols-2">
                                    <div className="space-y-2">
                                        <Label htmlFor="agent-display-name">Display name</Label>
                                        <Input id="agent-display-name" value={displayName} maxLength={80} disabled={selectedProfile.archived} onChange={(event) => setDisplayName(event.target.value)} />
                                        <p className="text-xs text-muted-foreground">Stable ID: <span className="font-mono">{selectedProfile.profile_id}</span></p>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="agent-model">Model</Label>
                                        <Select value={modelOverride} onValueChange={setModelOverride} disabled={selectedProfile.archived}>
                                            <SelectTrigger id="agent-model"><SelectValue placeholder="Choose a model" /></SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="__inherit">Use global model</SelectItem>
                                                {selectedProfile.model && !modelOptions.some((model) => model.id === selectedProfile.model) && (
                                                    <SelectItem value={selectedProfile.model}>{selectedProfile.model} (custom)</SelectItem>
                                                )}
                                                {modelOptions.map((model) => (
                                                    <SelectItem key={model.id} value={model.id}>{model.name || model.id}</SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                        <p className="text-xs text-muted-foreground">Credentials stay in LimeBot’s existing configuration.</p>
                                    </div>

                                    <div className="space-y-2 md:col-span-2">
                                        <Label htmlFor="profile-approval">Approval policy</Label>
                                        <Select value={approvalPolicy} onValueChange={setApprovalPolicy} disabled={selectedProfile.archived}>
                                            <SelectTrigger id="profile-approval"><SelectValue /></SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="inherit">Follow global policy</SelectItem>
                                                <SelectItem value="manual">Ask before sensitive actions</SelectItem>
                                                <SelectItem value="session">Remember approvals for this chat</SelectItem>
                                                <SelectItem value="review">Require review</SelectItem>
                                                <SelectItem value="autonomous">Autonomous (only if the global policy allows it)</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>

                                    <div className="space-y-3 md:col-span-2">
                                        <div className="flex items-start gap-3 rounded-lg border bg-muted/20 p-3">
                                            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                                            <p className="text-xs leading-relaxed text-muted-foreground">
                                                Agent policies can tighten the global approval rules. Named agents cannot access other agents’ persona or memory. Shell and sub-agent tools use the shared workspace, so they start disabled and require an explicit tool allowlist; path restrictions keep them disabled.
                                            </p>
                                        </div>
                                        <label className="flex items-center gap-2 text-sm font-medium">
                                            <input type="checkbox" checked={toolsRestricted} disabled={selectedProfile.archived} onChange={(event) => setToolsRestricted(event.target.checked)} className="h-4 w-4 accent-primary" />
                                            Restrict available tools
                                        </label>
                                        <Textarea aria-label="Allowed tools" placeholder="One tool name per line, for example read_file" value={toolsText} disabled={!toolsRestricted || selectedProfile.archived} rows={3} onChange={(event) => setToolsText(event.target.value)} />
                                        {availableTools.length > 0 && (
                                            <details className="rounded-md border border-border/60 px-3 py-2 text-xs text-muted-foreground">
                                                <summary className="cursor-pointer font-medium text-foreground">See available tool names</summary>
                                                <p className="mt-2 break-words leading-relaxed">{availableTools.join(", ")}</p>
                                            </details>
                                        )}
                                        <label className="flex items-center gap-2 text-sm font-medium">
                                            <input type="checkbox" checked={pathsRestricted} disabled={selectedProfile.archived} onChange={(event) => setPathsRestricted(event.target.checked)} className="h-4 w-4 accent-primary" />
                                            Restrict filesystem access further
                                        </label>
                                        <Textarea aria-label="Allowed paths" placeholder="One path per line, for example D:\\Projects\\notes" value={pathsText} disabled={!pathsRestricted || selectedProfile.archived} rows={3} onChange={(event) => setPathsText(event.target.value)} />
                                    </div>

                                    <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4 md:col-span-2">
                                        <p className="text-xs text-muted-foreground">Changes apply to the next turn.</p>
                                        <Button onClick={() => void saveProfile()} disabled={savingProfile || selectedProfile.archived || !displayName.trim()}>
                                            {savingProfile ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                            Save settings
                                        </Button>
                                    </div>
                                </CardContent>
                            </Card>

                            <Card className="border-border/70 bg-card/80">
                                <CardHeader>
                                    <CardTitle className="text-lg">Persona</CardTitle>
                                    <CardDescription>Identity, behavior, and mood are stored in this agent’s own persona folder.</CardDescription>
                                </CardHeader>
                                <CardContent className="grid gap-5 xl:grid-cols-2">
                                    <PersonaEditor
                                        section="identity"
                                        title="Identity"
                                        hint="Include **Name:** and **Style:** and at least 50 characters."
                                        value={persona.identity}
                                        disabled={selectedProfile.archived || personaLoading}
                                        saving={savingPersona === "identity"}
                                        onChange={(value) => setPersona((current) => ({ ...current, identity: value }))}
                                        onSave={() => void savePersonaSection("identity")}
                                    />
                                    <PersonaEditor
                                        section="soul"
                                        title="Soul and behavior"
                                        hint="Use at least 100 characters and describe the agent’s personality."
                                        value={persona.soul}
                                        disabled={selectedProfile.archived || personaLoading}
                                        saving={savingPersona === "soul"}
                                        onChange={(value) => setPersona((current) => ({ ...current, soul: value }))}
                                        onSave={() => void savePersonaSection("soul")}
                                    />
                                    <PersonaEditor
                                        section="mood"
                                        title="Current mood"
                                        hint="Optional context that can change without editing the identity."
                                        value={persona.mood}
                                        disabled={selectedProfile.archived || personaLoading}
                                        saving={savingPersona === "mood"}
                                        onChange={(value) => setPersona((current) => ({ ...current, mood: value }))}
                                        onSave={() => void savePersonaSection("mood")}
                                    />
                                    <div className="flex items-start gap-3 rounded-xl border border-primary/15 bg-primary/5 p-4 text-sm text-muted-foreground">
                                        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                                        <p>Persona changes pass LimeBot’s existing validation and backup rules. The default agent stays on the current LimeBot persona.</p>
                                    </div>
                                </CardContent>
                            </Card>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function PersonaEditor({
    section,
    title,
    hint,
    value,
    disabled,
    saving,
    onChange,
    onSave,
}: {
    section: PersonaSection;
    title: string;
    hint: string;
    value: string;
    disabled: boolean;
    saving: boolean;
    onChange: (value: string) => void;
    onSave: () => void;
}) {
    return (
        <section className="space-y-2">
            <Label htmlFor={`persona-${section}`}>{title}</Label>
            <Textarea
                id={`persona-${section}`}
                value={value}
                disabled={disabled}
                rows={section === "mood" ? 4 : 7}
                onChange={(event) => onChange(event.target.value)}
            />
            <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">{hint}</p>
                <Button size="sm" variant="outline" disabled={disabled || saving} onClick={onSave}>
                    {saving ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <Save className="mr-2 h-3.5 w-3.5" />}
                    Save
                </Button>
            </div>
        </section>
    );
}
