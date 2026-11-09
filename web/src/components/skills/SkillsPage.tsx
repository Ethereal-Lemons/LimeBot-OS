import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { API_BASE_URL } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/api-error";
import { classifySaveError, saveErrorToastText } from "@/lib/save-error";
import { AlertTriangle, Box, Loader2, RefreshCw, Search, Zap } from 'lucide-react';
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { EmptyState, LoadFailed, LoadingRows, SettingsPage, StatusPill } from "@/components/settings/kit";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

interface DepGroups {
    python?: string[];
    node?: string[];
    binaries?: string[];
}

interface Skill {
    id: string;
    name: string;
    description: string;
    path?: string;
    enabled: boolean;
    active: boolean;
    source_kind?: 'bundled' | 'git-managed' | 'local' | 'legacy-local' | string;
    editable?: boolean;
    deps_ok?: boolean;
    missing_deps?: DepGroups;
    required_deps?: DepGroups;
}

type RowNote = { tone: "bad" | "warn"; title: string; detail?: string };

function formatDeps(deps?: DepGroups): string {
    const source = deps || {};
    const parts: string[] = [];
    if (source.python?.length) parts.push(`python: ${source.python.join(", ")}`);
    if (source.node?.length) parts.push(`node: ${source.node.join(", ")}`);
    if (source.binaries?.length) parts.push(`binaries: ${source.binaries.join(", ")}`);
    return parts.join(" | ");
}

/** Plain-language failure for a row: transport problems read differently from server rejections. */
function describeFailure(error: unknown, subject: string, fallback: string): { title: string; detail?: string; toast: string } {
    if (axios.isAxiosError(error)) {
        const info = classifySaveError(error, subject);
        const server = info.category !== "network" && info.category !== "timeout" && info.detail !== info.title;
        return { title: info.title, detail: server ? info.detail : info.hint, toast: saveErrorToastText(info) };
    }
    const message = getApiErrorMessage(error, fallback);
    return { title: message, toast: message };
}

export function SkillsPage() {
    const [skills, setSkills] = useState<Skill[]>([]);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<unknown>(null);
    const [refreshing, setRefreshing] = useState(false);
    const [search, setSearch] = useState("");
    const [installingSkillId, setInstallingSkillId] = useState<string | null>(null);
    const [togglingIds, setTogglingIds] = useState<string[]>([]);
    const [notes, setNotes] = useState<Record<string, RowNote>>({});

    const setNote = (id: string, note: RowNote | null) =>
        setNotes((current) => {
            const next = { ...current };
            if (note) next[id] = note;
            else delete next[id];
            return next;
        });

    const loadSkills = useCallback(async (): Promise<boolean> => {
        try {
            const res = await axios.get(`${API_BASE_URL}/api/skills`);
            setSkills(res.data.skills || []);
            setLoadError(null);
            return true;
        } catch (err) {
            console.error("Failed to load skills:", err);
            setLoadError(err);
            return false;
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void loadSkills();
    }, [loadSkills]);

    const refresh = async () => {
        setRefreshing(true);
        const ok = await loadSkills();
        setRefreshing(false);
        if (!ok) toast.error("Could not refresh the skills list.");
    };

    const retryLoad = () => {
        setLoading(true);
        setLoadError(null);
        void loadSkills();
    };

    const toggleSkill = async (skill: Skill, enable: boolean) => {
        setNote(skill.id, null);
        if (skill.deps_ok === false && enable) {
            toast.error(`Install dependencies for ${skill.name} before enabling it.`);
            setNote(skill.id, { tone: "warn", title: "Install the missing dependencies first", detail: "This skill cannot be enabled until they are installed." });
            return;
        }
        setTogglingIds((current) => [...current, skill.id]);
        // Optimistic update
        setSkills((current) => current.map((s) => s.id === skill.id ? {
            ...s,
            enabled: enable,
            active: enable && s.deps_ok !== false,
        } : s));

        try {
            const res = await axios.post(`${API_BASE_URL}/api/skills/${skill.id}/toggle`, { enable });
            if (res.data?.status !== "success") {
                throw new Error(res.data?.message || "Failed to toggle skill");
            }
            toast.success(enable ? `${skill.name} enabled` : `${skill.name} disabled`);
            // The backend restarts, so we might lose connection briefly
        } catch (err) {
            console.error("Failed to toggle skill:", err);
            // Revert on error
            setSkills((current) => current.map((s) => s.id === skill.id ? {
                ...s,
                enabled: skill.enabled,
                active: skill.active,
            } : s));
            const failure = describeFailure(err, skill.name, `Failed to ${enable ? "enable" : "disable"} ${skill.name}`);
            setNote(skill.id, { tone: "bad", title: `Could not ${enable ? "enable" : "disable"} ${skill.name}`, detail: failure.detail || failure.title });
            toast.error(`Could not ${enable ? "enable" : "disable"} ${skill.name}. ${failure.toast}`);
        } finally {
            setTogglingIds((current) => current.filter((id) => id !== skill.id));
        }
    };

    const installDeps = async (skill: Skill) => {
        setInstallingSkillId(skill.id);
        setNote(skill.id, null);
        try {
            const res = await axios.post(`${API_BASE_URL}/api/skills/${skill.id}/deps`);
            const status = res.data?.status;
            if (status === "success") {
                toast.success(`Dependencies installed for ${skill.name}`);
            } else if (status === "partial") {
                toast.warning(`Some dependencies for ${skill.name} are still missing`);
                setNote(skill.id, { tone: "warn", title: "Some dependencies are still missing", detail: res.data?.message || "Check what is left below, then install it manually." });
            } else if (status === "error") {
                const message = res.data?.message || `Failed to install dependencies for ${skill.name}`;
                toast.error(message);
                setNote(skill.id, { tone: "bad", title: "The install did not finish", detail: message });
            } else {
                throw new Error(res.data?.message || "Failed to install dependencies");
            }
            await loadSkills();
        } catch (error: unknown) {
            console.error("Failed to install skill deps:", error);
            const failure = describeFailure(error, skill.name, `Failed to install dependencies for ${skill.name}`);
            setNote(skill.id, { tone: "bad", title: "Could not install dependencies", detail: failure.detail || failure.title });
            toast.error(failure.toast);
        } finally {
            setInstallingSkillId(null);
        }
    };

    const query = search.trim().toLowerCase();
    const filteredSkills = skills.filter(skill =>
        skill.name.toLowerCase().includes(query) ||
        (skill.description || "").toLowerCase().includes(query)
    );
    const enabledCount = skills.filter((skill) => skill.enabled).length;

    const renderRow = (skill: Skill) => {
        const missingDeps = skill.deps_ok === false;
        const missingSummary = missingDeps ? formatDeps(skill.missing_deps) : "";
        const disableEnable = missingDeps && !skill.enabled;
        const requiredSummary = formatDeps(skill.required_deps);
        const note = notes[skill.id];
        const installing = installingSkillId === skill.id;

        return (
            <li key={skill.id} className="flex items-start gap-3 py-4">
                <span className={cn(
                    "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition-colors",
                    skill.active ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground",
                )}>
                    <Zap className="h-4 w-4" />
                </span>
                <div className="min-w-0 flex-1 space-y-1.5">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <h3 className="break-all text-sm font-semibold text-foreground">{skill.name}</h3>
                        {skill.active ? (
                            <StatusPill tone="ok">Active</StatusPill>
                        ) : skill.enabled ? (
                            <StatusPill tone="warn">Needs dependencies</StatusPill>
                        ) : (
                            <StatusPill tone="muted">Disabled</StatusPill>
                        )}
                        <span className="font-mono text-[11px] text-muted-foreground">
                            {skill.source_kind || 'local'}{skill.editable ? ' · editable' : ' · read-only'}
                        </span>
                    </div>
                    {skill.description && (
                        <p className="line-clamp-3 text-sm leading-relaxed text-muted-foreground">{skill.description}</p>
                    )}
                    {requiredSummary && (
                        <p className="break-words font-mono text-[11px] text-muted-foreground/80">
                            <span className="mr-1.5 uppercase tracking-wide">Requires</span>
                            {requiredSummary}
                        </p>
                    )}
                    {missingDeps && (
                        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-l-2 border-red-500/60 py-0.5 pl-3">
                            <div className="min-w-0 text-xs">
                                <p className="font-medium text-foreground">Dependencies are missing</p>
                                <p className="break-words font-mono text-[11px] text-muted-foreground">{missingSummary}</p>
                            </div>
                            <Button
                                size="sm"
                                variant="outline"
                                className="h-7 shrink-0 gap-1.5 text-xs"
                                disabled={installing}
                                onClick={() => installDeps(skill)}
                            >
                                {installing && <Loader2 className="h-3 w-3 animate-spin" />}
                                {installing ? "Installing..." : "Install Deps"}
                            </Button>
                        </div>
                    )}
                    {note && (
                        <p
                            role={note.tone === "bad" ? "alert" : "status"}
                            className={cn("flex items-start gap-1.5 text-xs", note.tone === "bad" ? "text-red-500" : "text-amber-500")}
                        >
                            <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
                            <span className="min-w-0 break-words">
                                <span className="font-medium">{note.title}.</span>
                                {note.detail && note.detail !== note.title ? <span className="text-muted-foreground"> {note.detail}</span> : null}
                            </span>
                        </p>
                    )}
                </div>
                <Switch
                    aria-label={`Enable ${skill.name}`}
                    className="mt-1 shrink-0"
                    checked={skill.enabled}
                    disabled={disableEnable || togglingIds.includes(skill.id)}
                    onCheckedChange={(checked) => toggleSkill(skill, checked)}
                />
            </li>
        );
    };

    const refreshButton = (
        <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => void refresh()} disabled={loading || refreshing}>
            <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
            <span className="hidden sm:inline">Refresh</span>
        </Button>
    );

    let body;
    if (loading) {
        body = <LoadingRows rows={5} className="mt-6" label="Loading skills" />;
    } else if (loadError && skills.length === 0) {
        body = <LoadFailed error={loadError} subject="your skills" onRetry={retryLoad} />;
    } else if (skills.length === 0) {
        body = (
            <EmptyState
                icon={Box}
                title="No skills installed yet"
                description={<>Skills add capabilities such as documents, scraping or chat integrations. Install one from the command line with <span className="font-mono">npm run lime-bot skill install &lt;url&gt;</span>.</>}
                action={<Button variant="outline" size="sm" className="gap-1.5" onClick={() => void refresh()}><RefreshCw className="h-3.5 w-3.5" /> Check again</Button>}
            />
        );
    } else {
        body = (
            <div className="mt-4">
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 pb-3">
                    <div className="relative w-full sm:w-72">
                        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                        <Input
                            aria-label="Search skills"
                            placeholder="Search skills..."
                            className="h-9 pl-9"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                        />
                    </div>
                    <p className="text-xs text-muted-foreground">
                        {skills.length} installed · {enabledCount} enabled
                    </p>
                </div>
                {filteredSkills.length > 0 ? (
                    <ul className="divide-y divide-border/50 border-y border-border/50">
                        {filteredSkills.map(renderRow)}
                    </ul>
                ) : (
                    <EmptyState icon={Search} title={`No skills match "${search}"`} description="Try a different word, or clear the search to see everything installed." />
                )}
            </div>
        );
    }

    return (
        <SettingsPage
            title="Skills"
            icon={Box}
            description="Manage LimeBot's core capabilities and ClawHub extensions. Changing a skill restarts the backend briefly."
            width="max-w-5xl"
            actions={refreshButton}
        >
            {body}
        </SettingsPage>
    );
}
