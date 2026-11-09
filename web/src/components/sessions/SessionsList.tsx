import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { ChevronRight, CornerDownRight, Layers, Search, SearchX, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { formatAgo, formatCount } from "@/lib/ops-status";
import { getApiErrorMessage } from "@/lib/api-error";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CopyButton } from "@/components/chat/tool-ui";
import {
    EmptyState,
    IconButton,
    PageFrame,
    QuietButton,
    RefreshButton,
    RequestError,
    SkeletonRows,
    StatusGlyph,
} from "@/components/ops/ops-ui";

interface TokenUsage {
    input: number;
    output: number;
    total: number;
}

interface Instance {
    id: string;
    created_at: number;
    last_active: number;
    origin: string;
    model: string;
    profile_id?: string;
    profile_name?: string;
    total_tokens: TokenUsage;
    injected_files: string[];
    history_file: string;
    skills?: string[];
    parent_id?: string;
    task?: string;
}

interface PendingDelete {
    ids: string[];
    title: string;
    description: string;
    action: string;
}

interface Row {
    instance: Instance;
    depth: number;
}

const ZERO_TOKENS: TokenUsage = { input: 0, output: 0, total: 0 };

export function InstancesList({ currentSessionId }: { currentSessionId?: string }) {
    const [instances, setInstances] = useState<Instance[]>([]);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState<unknown>(null);
    const [searchQuery, setSearchQuery] = useState("");
    const [sortOrder, setSortOrder] = useState<"recent" | "oldest">("recent");
    const [selectedIds, setSelectedIds] = useState<string[]>([]);
    const [expandedIds, setExpandedIds] = useState<string[]>([]);
    const [deleting, setDeleting] = useState(false);
    const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);

    const fetchInstances = useCallback((manual = false) => {
        if (manual) setRefreshing(true);
        return axios
            .get(`${API_BASE_URL}/api/instances`)
            .then((res) => {
                setInstances(Array.isArray(res.data) ? (res.data as Instance[]) : []);
                setError(null);
            })
            .catch((err: unknown) => {
                if (!(axios.isAxiosError(err) && err.response?.status === 401)) {
                    console.error("Failed to load instances:", err);
                }
                setError(err);
            })
            .finally(() => {
                setLoading(false);
                setRefreshing(false);
            });
    }, []);

    useEffect(() => {
        void fetchInstances();
    }, [fetchInstances]);

    const runDelete = async (ids: string[]) => {
        if (ids.length === 0) return;
        setDeleting(true);
        try {
            if (ids.length === 1) {
                await axios.delete(`${API_BASE_URL}/api/instances/${ids[0]}`);
            } else {
                await axios.post(`${API_BASE_URL}/api/instances/delete-batch`, { ids });
            }
            setInstances((prev) => prev.filter((item) => !ids.includes(item.id)));
            setSelectedIds((prev) => prev.filter((id) => !ids.includes(id)));
            toast.success(ids.length === 1 ? "Instance deleted" : `${ids.length} instances deleted`);
        } catch (err) {
            console.error("Error deleting instances:", err);
            toast.error("Could not delete", { description: getApiErrorMessage(err, "The backend refused the request.") });
        } finally {
            setDeleting(false);
            setPendingDelete(null);
        }
    };

    const filteredInstances = useMemo(() => {
        const query = searchQuery.trim().toLowerCase();
        return instances
            .filter((inst) => {
                if (!query) return true;
                return (
                    inst.id.toLowerCase().includes(query) ||
                    (inst.origin || "").toLowerCase().includes(query) ||
                    (inst.model || "").toLowerCase().includes(query) ||
                    (inst.profile_name || "").toLowerCase().includes(query) ||
                    (inst.profile_id || "").toLowerCase().includes(query) ||
                    (inst.skills || []).some((skill) => skill.toLowerCase().includes(query))
                );
            })
            .sort((a, b) => (sortOrder === "recent" ? b.last_active - a.last_active : a.last_active - b.last_active));
    }, [instances, searchQuery, sortOrder]);

    // Main sessions first, each followed by its sub-agents; sub-agents whose
    // parent is filtered out or gone still appear, at the end.
    const rows = useMemo<Row[]>(() => {
        const ids = new Set(filteredInstances.map((item) => item.id));
        const result: Row[] = [];
        for (const inst of filteredInstances.filter((item) => !item.parent_id)) {
            result.push({ instance: inst, depth: 0 });
            for (const sub of filteredInstances.filter((item) => item.parent_id === inst.id)) {
                result.push({ instance: sub, depth: 1 });
            }
        }
        for (const orphan of filteredInstances.filter((item) => item.parent_id && !ids.has(item.parent_id))) {
            result.push({ instance: orphan, depth: 0 });
        }
        return result;
    }, [filteredInstances]);

    const visibleIds = rows.map((row) => row.instance.id);
    const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.includes(id));
    const someSelected = selectedIds.length > 0;
    const subAgentCount = instances.filter((item) => item.parent_id).length;

    const toggleSelect = (id: string) => setSelectedIds((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
    const toggleExpanded = (id: string) => setExpandedIds((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
    const toggleAll = () => {
        setSelectedIds((prev) => (allVisibleSelected ? prev.filter((id) => !visibleIds.includes(id)) : Array.from(new Set([...prev, ...visibleIds]))));
    };

    const askDeleteOne = (inst: Instance) =>
        setPendingDelete({
            ids: [inst.id],
            title: "Delete this instance?",
            description: `This permanently deletes the session context and chat log for ${inst.id}. It cannot be undone.`,
            action: "Delete",
        });
    const askDeleteSelected = () =>
        setPendingDelete({
            ids: selectedIds,
            title: `Delete ${selectedIds.length} instance${selectedIds.length === 1 ? "" : "s"}?`,
            description: "This permanently deletes the selected session contexts and their chat logs. It cannot be undone.",
            action: "Delete selected",
        });
    const askWipeAll = () =>
        setPendingDelete({
            ids: instances.map((item) => item.id),
            title: "Delete every instance?",
            description: `This permanently deletes all ${instances.length} instances, including the one you may be chatting in. It cannot be undone.`,
            action: "Delete everything",
        });

    const description = loading
        ? "Loading sessions…"
        : error && instances.length === 0
            ? "Unavailable"
            : `${instances.length - subAgentCount} session${instances.length - subAgentCount === 1 ? "" : "s"}${subAgentCount ? ` · ${subAgentCount} sub-agent${subAgentCount === 1 ? "" : "s"}` : ""}`;

    return (
        <PageFrame
            title="Instances"
            description={description}
            actions={<RefreshButton onClick={() => void fetchInstances(true)} busy={refreshing} />}
            scroll={false}
            maxWidth="max-w-none"
        >
            {/* ── Toolbar ── */}
            <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border/60 px-4 py-2.5 sm:px-6">
                <div className="relative min-w-[12rem] flex-1 sm:max-w-sm">
                    <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
                    <input
                        type="text"
                        aria-label="Search instances"
                        placeholder="Search by id, agent, model or skill"
                        value={searchQuery}
                        onChange={(event) => setSearchQuery(event.target.value)}
                        className="h-7 w-full rounded-md border border-border bg-transparent pl-8 pr-2 text-xs text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                </div>
                <Select
                    value={sortOrder}
                    onValueChange={(value) => {
                        if (value === "recent" || value === "oldest") setSortOrder(value);
                    }}
                >
                    <SelectTrigger aria-label="Sort order" className="h-7 w-[9.5rem] px-2 text-xs">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="recent">Most recent</SelectItem>
                        <SelectItem value="oldest">Oldest active</SelectItem>
                    </SelectContent>
                </Select>

                <div className="ml-auto flex items-center gap-2">
                    {someSelected ? (
                        <>
                            <span className="text-xs text-muted-foreground">{selectedIds.length} selected</span>
                            <QuietButton onClick={() => setSelectedIds([])}>Clear</QuietButton>
                            <QuietButton tone="danger" onClick={askDeleteSelected} disabled={deleting}>
                                <Trash2 />
                                Delete
                            </QuietButton>
                        </>
                    ) : (
                        instances.length > 0 && (
                            <QuietButton tone="danger" onClick={askWipeAll} disabled={deleting}>
                                Delete all
                            </QuietButton>
                        )
                    )}
                </div>
            </div>

            {error && instances.length > 0 ? (
                <div className="shrink-0 px-4 pt-3 sm:px-6">
                    <RequestError error={error} what="instances" onRetry={() => void fetchInstances(true)} retrying={refreshing} stale />
                </div>
            ) : null}

            {/* ── List ── */}
            <div className="min-h-0 flex-1 overflow-y-auto">
                {loading ? (
                    <SkeletonRows rows={6} />
                ) : error && instances.length === 0 ? (
                    <div className="mx-auto max-w-xl px-4 py-8">
                        <RequestError error={error} what="instances" onRetry={() => void fetchInstances(true)} retrying={refreshing} />
                    </div>
                ) : instances.length === 0 ? (
                    <EmptyState icon={Layers} title="No instances yet" hint="An instance is created for each chat, scheduled job and sub-agent. Start a chat or a task and it will appear here." />
                ) : rows.length === 0 ? (
                    <EmptyState
                        icon={SearchX}
                        title="No instances match your search"
                        hint={`${instances.length} instances exist; none match "${searchQuery}".`}
                        action={<QuietButton onClick={() => setSearchQuery("")}>Clear search</QuietButton>}
                    />
                ) : (
                    <div role="table" aria-label="Instances">
                        <div
                            role="row"
                            className="sticky top-0 z-10 flex items-center border-b border-border/60 bg-background/95 py-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80 backdrop-blur"
                        >
                            <div className="flex w-10 shrink-0 justify-center sm:w-12">
                                <input
                                    type="checkbox"
                                    aria-label="Select all visible instances"
                                    checked={allVisibleSelected}
                                    onChange={toggleAll}
                                    className="h-3.5 w-3.5 cursor-pointer accent-primary"
                                />
                            </div>
                            <div className="grid min-w-0 flex-1 gap-3 md:grid-cols-[minmax(0,1fr)_10rem_4.5rem_5.5rem]">
                                <span role="columnheader">Session</span>
                                <span role="columnheader" className="hidden md:block">Model</span>
                                <span role="columnheader" className="hidden text-right md:block">Tokens</span>
                                <span role="columnheader" className="hidden text-right md:block">Active</span>
                            </div>
                            <div className="w-10 shrink-0 sm:w-12" />
                        </div>

                        <ul>
                            {rows.map(({ instance, depth }) => {
                                const isCurrent = currentSessionId !== undefined && (instance.id === currentSessionId || instance.id.includes(currentSessionId));
                                const isSub = !!instance.parent_id;
                                const selected = selectedIds.includes(instance.id);
                                const expanded = expandedIds.includes(instance.id);
                                const tokens = instance.total_tokens ?? ZERO_TOKENS;
                                const agent = instance.profile_name || instance.profile_id || "Default";
                                return (
                                    <li
                                        key={instance.id}
                                        role="row"
                                        className={cn("border-b border-border/50", selected ? "bg-primary/[0.06]" : isCurrent && "bg-primary/[0.03]")}
                                    >
                                        <div className="flex items-center">
                                            <div className="flex w-10 shrink-0 justify-center sm:w-12">
                                                <input
                                                    type="checkbox"
                                                    aria-label={`Select ${instance.id}`}
                                                    checked={selected}
                                                    onChange={() => toggleSelect(instance.id)}
                                                    className="h-3.5 w-3.5 cursor-pointer accent-primary"
                                                />
                                            </div>
                                            <button
                                                type="button"
                                                onClick={() => toggleExpanded(instance.id)}
                                                aria-expanded={expanded}
                                                className="grid min-w-0 flex-1 gap-x-3 gap-y-0.5 py-2.5 text-left transition-colors hover:bg-foreground/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring md:grid-cols-[minmax(0,1fr)_10rem_4.5rem_5.5rem] md:items-center"
                                            >
                                                <span className={cn("flex min-w-0 items-center gap-2", depth > 0 && "pl-4 sm:pl-6")}>
                                                    {depth > 0 && <CornerDownRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" />}
                                                    <span className="flex h-5 w-5 shrink-0 items-center justify-center">
                                                        {instance.task ? <StatusGlyph state="running" /> : <ChevronRight className={cn("h-3.5 w-3.5 text-muted-foreground/70 transition-transform duration-200", expanded && "rotate-90")} />}
                                                    </span>
                                                    <span className="min-w-0">
                                                        <span className="flex min-w-0 items-center gap-2">
                                                            <span className="truncate font-mono text-xs font-medium text-foreground" title={instance.id}>{instance.id}</span>
                                                            {isCurrent && (
                                                                <span className="shrink-0 rounded-full bg-primary px-1.5 py-px text-[10px] font-semibold text-primary-foreground">You</span>
                                                            )}
                                                            {isSub && (
                                                                <span className="shrink-0 rounded-full border border-amber-500/40 px-1.5 py-px text-[10px] font-medium text-amber-500">Sub-agent</span>
                                                            )}
                                                        </span>
                                                        <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                                                            <span className="uppercase">{instance.origin || "unknown"}</span> · {agent}
                                                            <span className="md:hidden"> · {formatCount(tokens.total)} tokens · {formatAgo(instance.last_active)}</span>
                                                        </span>
                                                    </span>
                                                </span>
                                                <span className="hidden truncate font-mono text-[11px] text-muted-foreground md:block" title={instance.model}>{instance.model || "—"}</span>
                                                <span className="hidden text-right font-mono text-[11px] tabular-nums text-foreground md:block" title={`${tokens.total.toLocaleString()} tokens`}>
                                                    {formatCount(tokens.total)}
                                                </span>
                                                <span className="hidden text-right text-[11px] text-muted-foreground md:block" title={new Date(instance.last_active * 1000).toLocaleString()}>
                                                    {formatAgo(instance.last_active)}
                                                </span>
                                            </button>
                                            <div className="flex w-10 shrink-0 justify-center sm:w-12">
                                                <IconButton label={`Delete ${instance.id}`} onClick={() => askDeleteOne(instance)} disabled={deleting} className="h-7 w-7 hover:bg-red-500/10 hover:text-red-500">
                                                    <Trash2 />
                                                </IconButton>
                                            </div>
                                        </div>

                                        {expanded && (
                                            <div className={cn("grid gap-x-8 gap-y-4 pb-4 pr-4 pt-1 text-xs animate-in fade-in slide-in-from-top-1 duration-150 sm:pr-6 md:grid-cols-2", depth > 0 ? "pl-[4.5rem] sm:pl-[5.5rem]" : "pl-[3.25rem] sm:pl-[3.75rem]")}>
                                                {instance.task && (
                                                    <div className="md:col-span-2">
                                                        <Label>Active task</Label>
                                                        <p className="border-l-2 border-primary/40 pl-3 font-mono text-[11px] leading-relaxed text-foreground/85">{instance.task}</p>
                                                    </div>
                                                )}
                                                <div>
                                                    <Label>Skills</Label>
                                                    {instance.skills && instance.skills.length > 0 ? (
                                                        <div className="flex flex-wrap gap-1.5">
                                                            {instance.skills.map((skill) => (
                                                                <span key={skill} className="rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">{skill}</span>
                                                            ))}
                                                        </div>
                                                    ) : (
                                                        <span className="text-muted-foreground">No specific skills loaded.</span>
                                                    )}
                                                </div>
                                                <div>
                                                    <Label>Context files</Label>
                                                    {instance.injected_files.length > 0 ? (
                                                        <div className="flex flex-wrap gap-1.5">
                                                            {instance.injected_files.map((file, index) => (
                                                                <span key={`${file}-${index}`} className="max-w-full truncate rounded-md border border-border px-2 py-0.5 font-mono text-[11px] text-muted-foreground" title={file}>{file}</span>
                                                            ))}
                                                        </div>
                                                    ) : (
                                                        <span className="text-muted-foreground">No files in context.</span>
                                                    )}
                                                </div>
                                                <div>
                                                    <Label>Tokens</Label>
                                                    <div className="font-mono text-[11px] text-muted-foreground">
                                                        <span className="text-sm font-semibold text-foreground">{tokens.total.toLocaleString()}</span> total · {tokens.input.toLocaleString()} in · {tokens.output.toLocaleString()} out
                                                    </div>
                                                    <div className="mt-1 text-[11px] text-muted-foreground md:hidden">Model: <span className="font-mono">{instance.model || "—"}</span></div>
                                                </div>
                                                <div className="min-w-0">
                                                    <Label>Memory log</Label>
                                                    <div className="flex items-center gap-2">
                                                        <span className="min-w-0 truncate font-mono text-[11px] text-muted-foreground" title={instance.history_file}>{instance.history_file}</span>
                                                        <CopyButton text={instance.history_file} label="Copy path" />
                                                    </div>
                                                    <div className="mt-1 text-[11px] text-muted-foreground">Created {formatAgo(instance.created_at)}</div>
                                                </div>
                                            </div>
                                        )}
                                    </li>
                                );
                            })}
                        </ul>
                    </div>
                )}
            </div>

            <AlertDialog open={pendingDelete !== null} onOpenChange={(open) => !open && !deleting && setPendingDelete(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{pendingDelete?.title}</AlertDialogTitle>
                        <AlertDialogDescription>{pendingDelete?.description}</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            disabled={deleting}
                            onClick={(event) => {
                                event.preventDefault();
                                if (pendingDelete) void runDelete(pendingDelete.ids);
                            }}
                            className="bg-red-600 text-white hover:bg-red-700"
                        >
                            {deleting ? "Deleting…" : pendingDelete?.action}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </PageFrame>
    );
}

function Label({ children }: { children: string }) {
    return <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80">{children}</div>;
}
