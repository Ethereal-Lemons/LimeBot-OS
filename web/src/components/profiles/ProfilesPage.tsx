import { useEffect, useMemo, useRef, useState } from "react";
import { Archive, Bot, Loader2, Plus, RotateCcw, Save } from "lucide-react";
import { toast } from "sonner";

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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
    Field,
    FieldError,
    LoadFailed,
    LoadingRows,
    Note,
    SaveBar,
    SaveErrorNote,
    SettingsPage,
    SettingsSection,
    StatusPill,
    invalidInputClass,
} from "@/components/settings/kit";
import { useBeforeUnloadGuard, useUnsaved } from "@/hooks/useUnsaved";
import { useSaveState } from "@/hooks/useSaveState";
import { api } from "@/lib/api";
import { classifySaveError, saveErrorToastText, type SaveErrorInfo } from "@/lib/save-error";
import { getStoredProfileId, type AgentProfile, type AgentProfilePolicy } from "@/lib/agent-profiles";
import { cn } from "@/lib/utils";

type PersonaDraft = { soul: string; identity: string; mood: string };
type PersonaSection = keyof PersonaDraft;
type ModelOption = { id: string; name: string; provider?: string };

type ProfileDraft = {
    displayName: string;
    modelOverride: string;
    toolsRestricted: boolean;
    toolsText: string;
    pathsRestricted: boolean;
    pathsText: string;
    approvalPolicy: string;
};

const emptyPolicy: AgentProfilePolicy = {
    allowed_tools: null,
    allowed_paths: null,
    approval_policy: null,
};

const blankDraft: ProfileDraft = {
    displayName: "",
    modelOverride: "__inherit",
    toolsRestricted: false,
    toolsText: "",
    pathsRestricted: false,
    pathsText: "",
    approvalPolicy: "inherit",
};

const blankPersona: PersonaDraft = { soul: "", identity: "", mood: "" };
const PERSONA_LIMIT = 20000;

function splitPolicyList(value: string): string[] {
    return Array.from(new Set(value.split(/[\n,;]+/).map((item) => item.trim()).filter(Boolean)));
}

function draftFromProfile(profile: AgentProfile): ProfileDraft {
    const policy = profile.policy || emptyPolicy;
    return {
        displayName: profile.display_name,
        modelOverride: profile.model || "__inherit",
        toolsRestricted: policy.allowed_tools !== null,
        toolsText: (policy.allowed_tools || []).join("\n"),
        pathsRestricted: policy.allowed_paths !== null,
        pathsText: (policy.allowed_paths || []).join("\n"),
        approvalPolicy: policy.approval_policy || "inherit",
    };
}

function countChanges(current: ProfileDraft, baseline: ProfileDraft | undefined): number {
    if (!baseline) return 0;
    return (Object.keys(current) as Array<keyof ProfileDraft>).filter((key) => current[key] !== baseline[key]).length;
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
    const [loadError, setLoadError] = useState<unknown>(null);
    const [loadNonce, setLoadNonce] = useState(0);
    const [showArchived, setShowArchived] = useState(false);
    const [newName, setNewName] = useState("");
    const [newError, setNewError] = useState<string | null>(null);
    const [creating, setCreating] = useState(false);
    const [acting, setActing] = useState(false);
    const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
    const [modelsFailed, setModelsFailed] = useState(false);
    const [availableTools, setAvailableTools] = useState<string[]>([]);
    const [confirm, setConfirm] = useState<{ action: () => void } | null>(null);

    const [draft, setDraft] = useState<ProfileDraft>(blankDraft);
    const [attempted, setAttempted] = useState(false);
    const [nameTouched, setNameTouched] = useState(false);
    const { dirty, markSaved, restore } = useUnsaved(draft);
    const settingsSave = useSaveState();

    const [persona, setPersona] = useState<PersonaDraft>(blankPersona);
    const [personaBase, setPersonaBase] = useState<PersonaDraft>(blankPersona);
    const [personaLoading, setPersonaLoading] = useState(false);
    const [personaError, setPersonaError] = useState<SaveErrorInfo | null>(null);
    const [personaNonce, setPersonaNonce] = useState(0);
    const soulSave = useSaveState();
    const identitySave = useSaveState();
    const moodSave = useSaveState();
    const personaSaves = { soul: soulSave, identity: identitySave, mood: moodSave };

    const selectedProfile = useMemo(
        () => profiles.find((profile) => profile.profile_id === selectedId),
        [profiles, selectedId],
    );
    const selectedProfileId = selectedProfile?.profile_id;
    const visibleProfiles = useMemo(
        () => profiles.filter((profile) => showArchived || !profile.archived),
        [profiles, showArchived],
    );
    const archivedCount = profiles.filter((profile) => profile.archived).length;
    const onlyDefault = profiles.every((profile) => profile.profile_id === "default");

    const personaDirtyFor = (section: PersonaSection) => persona[section] !== personaBase[section];
    const personaDirty = personaDirtyFor("soul") || personaDirtyFor("identity") || personaDirtyFor("mood");
    const anyDirty = Boolean(selectedProfile && !selectedProfile.archived && (dirty || personaDirty));
    useBeforeUnloadGuard(anyDirty);

    /** Runs the action now, or after the person agrees to drop unsaved edits. */
    const guard = (action: () => void) => {
        if (anyDirty) setConfirm({ action });
        else action();
    };

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
        setLoading(true);
        setLoadError(null);
        Promise.all([
            api.get("/api/profiles", { params: { include_archived: true } }),
            api.get("/api/llm/models").catch((error: unknown) => {
                console.error("Failed to load models:", error);
                return null;
            }),
        ]).then(([profilesResponse, modelsResponse]) => {
            if (!mounted) return;
            const nextProfiles = Array.isArray(profilesResponse.data?.profiles)
                ? profilesResponse.data.profiles as AgentProfile[]
                : [];
            const nextModels = Array.isArray(modelsResponse?.data?.models)
                ? modelsResponse.data.models as ModelOption[]
                : [];
            setProfiles(nextProfiles);
            onProfilesChangedRef.current?.(nextProfiles);
            setModelOptions(nextModels);
            setModelsFailed(modelsResponse === null);
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
            console.error("Failed to load agents:", error);
            if (mounted) setLoadError(error);
        }).finally(() => {
            if (mounted) setLoading(false);
        });
        return () => { mounted = false; };
    }, [loadNonce]);

    const { clearError: clearSettingsError } = settingsSave;
    useEffect(() => {
        if (!selectedProfile) return;
        const next = draftFromProfile(selectedProfile);
        setDraft(next);
        markSaved(next);
        setAttempted(false);
        setNameTouched(false);
        clearSettingsError();
    }, [selectedProfile, markSaved, clearSettingsError]);

    useEffect(() => {
        if (!selectedProfileId) return;
        let mounted = true;
        setPersonaLoading(true);
        setPersonaError(null);
        api.get(`/api/profiles/${encodeURIComponent(selectedProfileId)}/persona`)
            .then((response) => {
                if (!mounted) return;
                const next = {
                    soul: String(response.data?.soul || ""),
                    identity: String(response.data?.identity || ""),
                    mood: String(response.data?.mood || ""),
                };
                setPersona(next);
                setPersonaBase(next);
            })
            .catch((error: unknown) => {
                console.error("Failed to load persona:", error);
                if (mounted) setPersonaError(classifySaveError(error, "this agent's persona"));
            })
            .finally(() => {
                if (mounted) setPersonaLoading(false);
            });
        return () => { mounted = false; };
    }, [selectedProfileId, personaNonce]);

    const createProfile = async () => {
        const name = newName.trim();
        if (!name) {
            setNewError("Give the new agent a name.");
            return;
        }
        setCreating(true);
        setNewError(null);
        try {
            const response = await api.post("/api/profiles", { display_name: name });
            const created = response.data?.profile as AgentProfile;
            await refreshProfiles(created.profile_id);
            setNewName("");
            toast.success(`${created.display_name} is ready to configure.`);
        } catch (error) {
            console.error("Failed to create agent:", error);
            const info = classifySaveError(error, "the new agent");
            const detail = info.category === "network" || info.category === "timeout" ? info.title : saveErrorToastText(info);
            setNewError(detail);
            toast.error(saveErrorToastText(info));
        } finally {
            setCreating(false);
        }
    };

    const saveProfile = async () => {
        if (!selectedProfile) return;
        if (!draft.displayName.trim()) {
            setAttempted(true);
            document.getElementById("agent-display-name")?.focus();
            return;
        }
        const policy: AgentProfilePolicy = {
            allowed_tools: draft.toolsRestricted ? splitPolicyList(draft.toolsText) : null,
            allowed_paths: draft.pathsRestricted ? splitPolicyList(draft.pathsText) : null,
            approval_policy: draft.approvalPolicy === "inherit" ? null : draft.approvalPolicy,
        };
        const ok = await settingsSave.run(async () => {
            await api.patch(`/api/profiles/${encodeURIComponent(selectedProfile.profile_id)}`, {
                display_name: draft.displayName,
                model: draft.modelOverride === "__inherit" ? null : draft.modelOverride,
                policy,
            });
            await refreshProfiles(selectedProfile.profile_id);
        });
        if (ok) toast.success("Agent settings saved.");
    };

    const discardSettings = () => {
        const base = restore();
        if (base) setDraft(base);
        setAttempted(false);
        setNameTouched(false);
        clearSettingsError();
    };

    const savePersonaSection = async (section: PersonaSection) => {
        if (!selectedProfile) return;
        const content = persona[section];
        const ok = await personaSaves[section].run(async () => {
            await api.put(
                `/api/profiles/${encodeURIComponent(selectedProfile.profile_id)}/persona/${section}`,
                { content },
            );
        });
        if (ok) {
            setPersonaBase((current) => ({ ...current, [section]: content }));
            toast.success(`${section[0].toUpperCase()}${section.slice(1)} saved.`);
        }
    };

    const archiveProfile = async () => {
        if (!selectedProfile || selectedProfile.profile_id === "default") return;
        setActing(true);
        try {
            await api.post(`/api/profiles/${encodeURIComponent(selectedProfile.profile_id)}/archive`);
            await refreshProfiles("default");
            toast.success(`${selectedProfile.display_name} archived.`);
        } catch (error) {
            toast.error(saveErrorToastText(classifySaveError(error, "this agent")));
        } finally {
            setActing(false);
        }
    };

    const restoreProfile = async () => {
        if (!selectedProfile) return;
        setActing(true);
        try {
            await api.post(`/api/profiles/${encodeURIComponent(selectedProfile.profile_id)}/unarchive`);
            await refreshProfiles(selectedProfile.profile_id);
            toast.success(`${selectedProfile.display_name} restored.`);
        } catch (error) {
            toast.error(saveErrorToastText(classifySaveError(error, "this agent")));
        } finally {
            setActing(false);
        }
    };

    const setField = <K extends keyof ProfileDraft>(key: K, value: ProfileDraft[K]) =>
        setDraft((current) => ({ ...current, [key]: value }));

    if (loading) {
        return (
            <SettingsPage title="Agents" icon={Bot} width="max-w-5xl">
                <LoadingRows rows={5} className="mt-6" label="Loading agents" />
            </SettingsPage>
        );
    }

    if (loadError) {
        return (
            <SettingsPage title="Agents" icon={Bot} width="max-w-5xl">
                <LoadFailed error={loadError} subject="your agents" onRetry={() => setLoadNonce((value) => value + 1)} />
            </SettingsPage>
        );
    }

    const archived = Boolean(selectedProfile?.archived);
    const nameError = (attempted || nameTouched) && !draft.displayName.trim() ? "An agent needs a name." : null;
    const toolList = splitPolicyList(draft.toolsText);
    const pathList = splitPolicyList(draft.pathsText);
    const unknownTools = availableTools.length > 0 ? toolList.filter((tool) => !availableTools.includes(tool)) : [];

    const archivedToggle = (archivedCount > 0 || showArchived) && (
        <button
            type="button"
            onClick={() => setShowArchived((value) => !value)}
            className="shrink-0 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
            {showArchived ? "Hide archived" : `Show archived (${archivedCount})`}
        </button>
    );

    const newAgentForm = (idSuffix: string) => (
        <form
            onSubmit={(event) => {
                event.preventDefault();
                guard(() => void createProfile());
            }}
            className="space-y-1.5"
            noValidate
        >
            <Label htmlFor={`new-agent-${idSuffix}`} className="text-xs font-medium text-muted-foreground">New agent</Label>
            <div className="flex gap-2">
                <Input
                    id={`new-agent-${idSuffix}`}
                    aria-label="New agent name"
                    className={cn("h-9 min-w-0", newError && invalidInputClass)}
                    placeholder="Name"
                    maxLength={80}
                    value={newName}
                    aria-invalid={Boolean(newError)}
                    onChange={(event) => { setNewName(event.target.value); if (newError) setNewError(null); }}
                />
                <Button type="submit" size="sm" className="h-9 shrink-0 gap-1.5" disabled={creating}>
                    {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                    Create
                </Button>
            </div>
            <FieldError message={newError} />
        </form>
    );

    const onlyDefaultNote = onlyDefault && (
        <Note title="Just the default agent">
            Create another agent to give it its own persona, model and tool limits.
        </Note>
    );

    const roster = (
        <aside className="hidden space-y-5 lg:sticky lg:top-6 lg:block lg:self-start" aria-label="Agent roster">
            <div>
                <div className="mb-2 flex items-center justify-between gap-2">
                    <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Roster <span className="font-normal normal-case">· {visibleProfiles.length}</span>
                    </h2>
                    {archivedToggle}
                </div>
                <ul className="border-l border-border/50">
                    {visibleProfiles.map((profile) => {
                        const selected = selectedId === profile.profile_id;
                        return (
                            <li key={profile.profile_id}>
                                <button
                                    type="button"
                                    aria-current={selected ? "true" : undefined}
                                    onClick={() => { if (!selected) guard(() => setSelectedId(profile.profile_id)); }}
                                    className={cn(
                                        "-ml-px block w-full border-l-2 px-3 py-2 text-left transition-colors",
                                        selected ? "border-primary bg-muted/60" : "border-transparent hover:bg-muted/40",
                                        profile.archived && "opacity-60",
                                    )}
                                >
                                    <span className="flex min-w-0 items-center justify-between gap-2">
                                        <span className={cn("truncate text-sm", selected ? "font-semibold text-foreground" : "font-medium text-foreground/90")}>{profile.display_name}</span>
                                        {profile.profile_id === "default" && <StatusPill tone="muted">Default</StatusPill>}
                                        {profile.archived && <StatusPill tone="muted">Archived</StatusPill>}
                                    </span>
                                    <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">{profile.resolved_model}</span>
                                </button>
                            </li>
                        );
                    })}
                </ul>
            </div>
            {onlyDefaultNote}
            {newAgentForm("roster")}
        </aside>
    );

    const compactRoster = (
        <div className="space-y-4 lg:hidden">
            <div className="-mx-4 flex items-center gap-2 overflow-x-auto px-4 pb-1" role="tablist" aria-label="Agents">
                {visibleProfiles.map((profile) => {
                    const selected = selectedId === profile.profile_id;
                    return (
                        <button
                            key={profile.profile_id}
                            type="button"
                            role="tab"
                            aria-selected={selected}
                            onClick={() => { if (!selected) guard(() => setSelectedId(profile.profile_id)); }}
                            className={cn(
                                "shrink-0 rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                                selected ? "border-primary/50 bg-primary/10 text-primary" : "border-border/60 text-muted-foreground hover:text-foreground",
                                profile.archived && "opacity-60",
                            )}
                        >
                            {profile.display_name}
                            {profile.archived ? " (archived)" : ""}
                        </button>
                    );
                })}
                {archivedToggle}
            </div>
            {onlyDefaultNote}
            {newAgentForm("compact")}
        </div>
    );

    const selectedSaveBar = selectedProfile && !archived ? (
        <SaveBar
            dirty={dirty}
            saving={settingsSave.saving}
            onSave={() => void saveProfile()}
            onDiscard={discardSettings}
            error={settingsSave.error}
            onDismissError={settingsSave.clearError}
            savedAt={settingsSave.savedAt}
            changeCount={countChanges(draft, restore())}
            saveLabel="Save settings"
        />
    ) : undefined;

    return (
        <SettingsPage
            title="Agents"
            icon={Bot}
            description="Each agent keeps its own conversations, persona, and memory. The default agent continues using LimeBot’s existing files and history."
            width="max-w-5xl"
            saveBar={selectedSaveBar}
        >
            <div className="mt-4 grid gap-x-10 gap-y-6 lg:grid-cols-[13rem_minmax(0,1fr)]">
                {roster}
                <div className="min-w-0">
                    {compactRoster}
                    {selectedProfile && (
                        <div className="mt-6 lg:mt-0">
                            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border/50 pb-4">
                                <div className="min-w-0">
                                    <h2 className="flex flex-wrap items-center gap-2 text-lg font-semibold text-foreground">
                                        <span className="truncate">{selectedProfile.display_name}</span>
                                        {selectedProfile.profile_id === "default" && <StatusPill tone="muted">Default</StatusPill>}
                                        {archived && <StatusPill tone="warn">Archived</StatusPill>}
                                    </h2>
                                    <p className="mt-0.5 text-xs text-muted-foreground">
                                        Stable ID: <span className="font-mono">{selectedProfile.profile_id}</span>
                                    </p>
                                </div>
                                {archived ? (
                                    <Button variant="outline" size="sm" disabled={acting} onClick={() => void restoreProfile()} className="gap-1.5">
                                        {acting ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
                                        Restore
                                    </Button>
                                ) : selectedProfile.profile_id !== "default" ? (
                                    <Button variant="outline" size="sm" disabled={acting} onClick={() => guard(() => void archiveProfile())} className="gap-1.5">
                                        {acting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Archive className="h-4 w-4" />}
                                        Archive
                                    </Button>
                                ) : null}
                            </div>

                            {archived && (
                                <Note tone="warn" title="This agent is archived" className="mt-4">
                                    Its history and persona are kept. Restore it to make changes.
                                </Note>
                            )}

                            <div>
                                <SettingsSection title="Identity" description="How this agent is named in the roster and in chat.">
                                    <Field
                                        label="Display name"
                                        htmlFor="agent-display-name"
                                        error={nameError}
                                    >
                                        <Input
                                            id="agent-display-name"
                                            value={draft.displayName}
                                            maxLength={80}
                                            disabled={archived}
                                            aria-invalid={Boolean(nameError)}
                                            className={cn(nameError && invalidInputClass)}
                                            onBlur={() => setNameTouched(true)}
                                            onChange={(event) => setField("displayName", event.target.value)}
                                        />
                                    </Field>
                                </SettingsSection>

                                <SettingsSection title="Model" description="Credentials stay in LimeBot’s existing configuration.">
                                    <Field
                                        label="Model"
                                        htmlFor="agent-model"
                                        hint={
                                            draft.modelOverride === "__inherit"
                                                ? `Follows the global model, currently ${selectedProfile.resolved_model}.`
                                                : modelsFailed
                                                    ? "The model list could not be loaded, but the current choice is kept."
                                                    : undefined
                                        }
                                    >
                                        <Select value={draft.modelOverride} onValueChange={(value) => setField("modelOverride", value)} disabled={archived}>
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
                                    </Field>
                                </SettingsSection>

                                <SettingsSection
                                    title="Access policy"
                                    description="Agent policies can tighten the global rules but never loosen them. Changes apply to the next turn."
                                >
                                    <Note title="Shell and sub-agent tools start disabled">
                                        Named agents cannot access other agents’ persona or memory. Shell and sub-agent tools use the shared workspace, so they require an explicit tool allowlist; path restrictions keep them disabled.
                                    </Note>

                                    <Field label="Approval policy" htmlFor="profile-approval">
                                        <Select value={draft.approvalPolicy} onValueChange={(value) => setField("approvalPolicy", value)} disabled={archived}>
                                            <SelectTrigger id="profile-approval"><SelectValue /></SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="inherit">Follow global policy</SelectItem>
                                                <SelectItem value="manual">Ask before sensitive actions</SelectItem>
                                                <SelectItem value="session">Remember approvals for this chat</SelectItem>
                                                <SelectItem value="review">Require review</SelectItem>
                                                <SelectItem value="autonomous">Autonomous (only if the global policy allows it)</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </Field>

                                    <div className="space-y-3">
                                        <Field
                                            label="Restrict available tools"
                                            htmlFor="agent-tools-restricted"
                                            inline
                                            hint={draft.toolsRestricted ? "Only the tools listed below are available." : "Off: every tool the global settings allow."}
                                        >
                                            <Switch
                                                id="agent-tools-restricted"
                                                checked={draft.toolsRestricted}
                                                disabled={archived}
                                                onCheckedChange={(checked) => setField("toolsRestricted", checked)}
                                            />
                                        </Field>
                                        {draft.toolsRestricted && (
                                            <div className="space-y-2">
                                                <Textarea
                                                    aria-label="Allowed tools"
                                                    placeholder="One tool name per line, for example read_file"
                                                    value={draft.toolsText}
                                                    disabled={archived}
                                                    rows={4}
                                                    className="font-mono text-xs"
                                                    onChange={(event) => setField("toolsText", event.target.value)}
                                                />
                                                {toolList.length === 0 && (
                                                    <Note tone="warn" title="No tools listed">
                                                        With the restriction on and an empty list, this agent gets no optional tools at all.
                                                    </Note>
                                                )}
                                                {unknownTools.length > 0 && (
                                                    <p className="text-xs text-amber-500">
                                                        Not in the current tool list: <span className="font-mono">{unknownTools.join(", ")}</span>. Check the spelling.
                                                    </p>
                                                )}
                                                {availableTools.length > 0 && (
                                                    <details className="text-xs text-muted-foreground">
                                                        <summary className="cursor-pointer font-medium text-foreground">See available tool names</summary>
                                                        <p className="mt-2 break-words font-mono leading-relaxed">{availableTools.join(", ")}</p>
                                                    </details>
                                                )}
                                            </div>
                                        )}
                                    </div>

                                    <div className="space-y-3">
                                        <Field
                                            label="Restrict filesystem access further"
                                            htmlFor="agent-paths-restricted"
                                            inline
                                            hint={draft.pathsRestricted ? "Only the folders listed below, within the global allowed paths." : "Off: the global allowed paths apply."}
                                        >
                                            <Switch
                                                id="agent-paths-restricted"
                                                checked={draft.pathsRestricted}
                                                disabled={archived}
                                                onCheckedChange={(checked) => setField("pathsRestricted", checked)}
                                            />
                                        </Field>
                                        {draft.pathsRestricted && (
                                            <div className="space-y-2">
                                                <Textarea
                                                    aria-label="Allowed paths"
                                                    placeholder="One path per line, for example D:\\Projects\\notes"
                                                    value={draft.pathsText}
                                                    disabled={archived}
                                                    rows={4}
                                                    className="font-mono text-xs"
                                                    onChange={(event) => setField("pathsText", event.target.value)}
                                                />
                                                {pathList.length === 0 && (
                                                    <Note tone="warn" title="No paths listed">
                                                        With the restriction on and an empty list, this agent cannot read or write any files.
                                                    </Note>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                </SettingsSection>

                                <SettingsSection
                                    title="Persona"
                                    description="Identity, behavior, and mood are stored in this agent’s own persona folder. Each part saves on its own and passes LimeBot’s validation and backup rules."
                                >
                                    {personaLoading ? (
                                        <LoadingRows rows={3} label="Loading persona" />
                                    ) : personaError ? (
                                        <SaveErrorNote
                                            error={{ ...personaError, title: personaError.category === "network" ? "Could not load this agent’s persona" : personaError.title }}
                                            onRetry={() => setPersonaNonce((value) => value + 1)}
                                        />
                                    ) : (
                                        <div className="divide-y divide-border/50 [&>*]:py-5 [&>*:first-child]:pt-0 [&>*:last-child]:pb-0">
                                            <PersonaEditor
                                                section="identity"
                                                title="Identity"
                                                hint="Include **Name:** and **Style:** and at least 50 characters."
                                                minLength={50}
                                                value={persona.identity}
                                                saved={personaBase.identity}
                                                disabled={archived}
                                                state={identitySave}
                                                onChange={(value) => setPersona((current) => ({ ...current, identity: value }))}
                                                onSave={() => void savePersonaSection("identity")}
                                            />
                                            <PersonaEditor
                                                section="soul"
                                                title="Soul and behavior"
                                                hint="Use at least 100 characters and describe the agent’s personality."
                                                minLength={100}
                                                value={persona.soul}
                                                saved={personaBase.soul}
                                                disabled={archived}
                                                state={soulSave}
                                                onChange={(value) => setPersona((current) => ({ ...current, soul: value }))}
                                                onSave={() => void savePersonaSection("soul")}
                                            />
                                            <PersonaEditor
                                                section="mood"
                                                title="Current mood"
                                                hint="Optional context that can change without editing the identity."
                                                value={persona.mood}
                                                saved={personaBase.mood}
                                                disabled={archived}
                                                state={moodSave}
                                                onChange={(value) => setPersona((current) => ({ ...current, mood: value }))}
                                                onSave={() => void savePersonaSection("mood")}
                                            />
                                        </div>
                                    )}
                                    {selectedProfile.profile_id === "default" && (
                                        <p className="text-xs text-muted-foreground">The default agent stays on the current LimeBot persona.</p>
                                    )}
                                </SettingsSection>
                            </div>
                        </div>
                    )}
                </div>
            </div>

            <AlertDialog open={confirm !== null} onOpenChange={(open) => { if (!open) setConfirm(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
                        <AlertDialogDescription>
                            {selectedProfile?.display_name || "This agent"} has edits that are not saved yet. Continuing will throw them away.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Keep editing</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={() => {
                                const pending = confirm;
                                setConfirm(null);
                                pending?.action();
                            }}
                        >
                            Discard and continue
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </SettingsPage>
    );
}

function PersonaEditor({
    section,
    title,
    hint,
    minLength,
    value,
    saved,
    disabled,
    state,
    onChange,
    onSave,
}: {
    section: PersonaSection;
    title: string;
    hint: string;
    minLength?: number;
    value: string;
    saved: string;
    disabled: boolean;
    state: ReturnType<typeof useSaveState>;
    onChange: (value: string) => void;
    onSave: () => void;
}) {
    const dirty = value !== saved;
    const length = value.trim().length;
    const tooLong = value.length > PERSONA_LIMIT;
    const short = Boolean(minLength) && dirty && length <= (minLength || 0);
    return (
        <section className="space-y-2" aria-label={title}>
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                    <Label htmlFor={`persona-${section}`} className="text-sm font-medium">{title}</Label>
                    {dirty && (
                        <span className="flex items-center gap-1.5 text-[11px] text-foreground">
                            <span className="h-1.5 w-1.5 rounded-full bg-amber-500" /> Unsaved
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-1.5">
                    {dirty && (
                        <Button type="button" size="sm" variant="ghost" className="h-8 gap-1.5 px-2" disabled={state.saving} onClick={() => onChange(saved)}>
                            <RotateCcw className="h-3.5 w-3.5" />
                            Discard
                        </Button>
                    )}
                    <Button
                        type="button"
                        size="sm"
                        variant={dirty ? "default" : "outline"}
                        className="h-8 gap-1.5"
                        disabled={disabled || state.saving || tooLong}
                        onClick={onSave}
                    >
                        {state.saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                        Save
                    </Button>
                </div>
            </div>
            <Textarea
                id={`persona-${section}`}
                value={value}
                disabled={disabled}
                rows={section === "mood" ? 4 : 7}
                aria-invalid={tooLong}
                className={cn(tooLong && invalidInputClass)}
                onChange={(event) => onChange(event.target.value)}
            />
            <p className={cn("text-xs", short ? "text-amber-500" : "text-muted-foreground")}>
                {hint}
                {short ? ` Currently ${length} characters.` : ""}
            </p>
            <FieldError message={tooLong ? `Persona text is limited to ${PERSONA_LIMIT.toLocaleString()} characters (now ${value.length.toLocaleString()}).` : null} />
            {state.error && <SaveErrorNote error={state.error} onRetry={onSave} onDismiss={state.clearError} />}
        </section>
    );
}
