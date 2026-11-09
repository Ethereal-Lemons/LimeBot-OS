import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ArrowUpDown, Brain, KeyRound, RefreshCw, Search, Settings, Trash2 } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { requestFailureMessage } from "@/lib/request-error";
import {
    Chip,
    EmptyState,
    InlineConfirm,
    Notice,
    PageFrame,
    PageHeader,
    RequestError,
    RowButton,
    RowList,
    SkeletonRows,
    StatusPill,
} from "@/components/ui/data-page";

type SortOrder = "newest" | "oldest";

export interface MemoryEntry {
    id: string;
    text: string;
    category?: string;
    path?: string;
    timestamp?: string | number;
    source?: string;
}

interface MemoryPageProps {
    onNavigate?: (view: string) => void;
}

function learnedAgo(timestamp: MemoryEntry["timestamp"]): string {
    if (!timestamp) return "recently";
    const date = new Date(timestamp);
    return Number.isNaN(date.getTime()) ? "recently" : formatDistanceToNow(date, { addSuffix: true });
}

const CLAMP_PX = 120; // matches max-h-[7.5rem]

function ClampedMarkdown({ text }: { text: string }) {
    const ref = useRef<HTMLDivElement>(null);
    const [expanded, setExpanded] = useState(false);
    const [overflows, setOverflows] = useState(false);

    useLayoutEffect(() => {
        const node = ref.current;
        if (!node) return;
        const measure = () => setOverflows(node.scrollHeight > CLAMP_PX + 1);
        measure();
        const observer = new ResizeObserver(measure);
        observer.observe(node);
        return () => observer.disconnect();
    }, [text]);

    return (
        <div>
            <div
                ref={ref}
                className={cn(
                    "relative break-words text-[13px] leading-relaxed text-foreground/90",
                    !expanded && "max-h-[7.5rem] overflow-hidden",
                    !expanded && overflows && "[mask-image:linear-gradient(to_bottom,black_70%,transparent)]"
                )}
            >
                <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                        p: ({ node, ...props }) => {
                            void node;
                            return <p {...props} className="mb-2 last:mb-0" />;
                        },
                        h1: ({ node, ...props }) => {
                            void node;
                            return <h3 className="mb-1 mt-2 text-[13px] font-semibold first:mt-0" {...props} />;
                        },
                        h2: ({ node, ...props }) => {
                            void node;
                            return <h3 className="mb-1 mt-2 text-[13px] font-semibold first:mt-0" {...props} />;
                        },
                        h3: ({ node, ...props }) => {
                            void node;
                            return <h4 className="mb-0.5 mt-1.5 text-xs font-semibold first:mt-0" {...props} />;
                        },
                        ul: ({ node, ...props }) => {
                            void node;
                            return <ul className="mb-2 ml-4 list-disc last:mb-0" {...props} />;
                        },
                        ol: ({ node, ...props }) => {
                            void node;
                            return <ol className="mb-2 ml-4 list-decimal last:mb-0" {...props} />;
                        },
                        li: ({ node, ...props }) => {
                            void node;
                            return <li className="mb-0.5" {...props} />;
                        },
                        code: ({ node, ...props }) => {
                            void node;
                            return <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]" {...props} />;
                        },
                    }}
                >
                    {text}
                </ReactMarkdown>
            </div>
            {(overflows || expanded) && (
                <button
                    type="button"
                    onClick={() => setExpanded((value) => !value)}
                    className="mt-1 text-[11px] font-medium text-muted-foreground hover:text-foreground"
                >
                    {expanded ? "Show less" : "Show more"}
                </button>
            )}
        </div>
    );
}

function MemoryRow({ entry, canDelete, onDelete }: { entry: MemoryEntry; canDelete: boolean; onDelete: (id: string) => Promise<void> }) {
    return (
        <article className="group px-4 py-3.5 transition-colors hover:bg-muted/30 focus-within:bg-muted/30">
            <div className="mb-1.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                <Chip>{entry.category || "General"}</Chip>
                <span>Learned {learnedAgo(entry.timestamp)}</span>
                {entry.source && (
                    <span className="hidden min-w-0 max-w-[40%] truncate font-mono text-[10px] opacity-70 sm:inline" title={entry.source}>
                        {entry.source}
                    </span>
                )}
                {canDelete && (
                    <span className="ml-auto opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
                        <InlineConfirm
                            iconOnly
                            icon={Trash2}
                            label="Forget this memory"
                            prompt="Forget it?"
                            confirmLabel="Forget"
                            onConfirm={() => onDelete(entry.id)}
                        />
                    </span>
                )}
            </div>
            <ClampedMarkdown text={entry.text} />
        </article>
    );
}

export function MemoryPage({ onNavigate }: MemoryPageProps) {
    const [memories, setMemories] = useState<MemoryEntry[]>([]);
    const [enabled, setEnabled] = useState<boolean | null>(null);
    const [mode, setMode] = useState<"vector" | "grep_fallback">("vector");
    const [readOnly, setReadOnly] = useState(false);
    const [notice, setNotice] = useState("");
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<unknown>(null);

    const [searchQuery, setSearchQuery] = useState("");
    const [selectedCategory, setSelectedCategory] = useState("all");
    const [sortOrder, setSortOrder] = useState<SortOrder>("newest");

    const fetchMemories = useCallback(async () => {
        setLoading(true);
        try {
            const res = await axios.get(`${API_BASE_URL}/api/memory`);
            setEnabled(res.data.enabled);
            setMode((res.data.mode || (res.data.enabled ? "vector" : "grep_fallback")) as "vector" | "grep_fallback");
            setReadOnly(Boolean(res.data.read_only));
            setNotice(res.data.notice || "");
            setMemories(res.data.memories || []);
            setLoadError(null);
        } catch (err) {
            console.error("Failed to fetch memories:", err);
            setLoadError(err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void fetchMemories();
    }, [fetchMemories]);

    const handleDelete = async (id: string) => {
        try {
            await axios.delete(`${API_BASE_URL}/api/memory/${id}`);
            setMemories((prev) => prev.filter((m) => m.id !== id));
            toast.success("Memory forgotten");
        } catch (err) {
            console.error("Failed to delete memory:", err);
            toast.error(requestFailureMessage("Could not delete the memory", err));
        }
    };

    const categories = useMemo(() => {
        const cats = new Set<string>();
        memories.forEach((m) => cats.add(m.category || "General"));
        return Array.from(cats).sort();
    }, [memories]);

    const filteredMemories = useMemo(() => {
        let result = [...memories];
        if (searchQuery.trim()) {
            const q = searchQuery.toLowerCase();
            result = result.filter(
                (m) => (m.text || "").toLowerCase().includes(q) || (m.category || "").toLowerCase().includes(q) || (m.path || "").toLowerCase().includes(q)
            );
        }
        if (selectedCategory !== "all") {
            result = result.filter((m) => (m.category || "General") === selectedCategory);
        }
        result.sort((a, b) => {
            const ta = new Date(a.timestamp || 0).getTime() || 0;
            const tb = new Date(b.timestamp || 0).getTime() || 0;
            return sortOrder === "newest" ? tb - ta : ta - tb;
        });
        return result;
    }, [memories, searchQuery, selectedCategory, sortOrder]);

    const usingFallback = mode === "grep_fallback";
    const canDelete = !readOnly && enabled === true;
    const filtered = searchQuery.trim() !== "" || selectedCategory !== "all";
    const hasData = enabled !== null;

    const refreshButton = (
        <RowButton onClick={() => void fetchMemories()} disabled={loading} aria-label="Refresh memories" title="Refresh">
            <RefreshCw className={cn(loading && "animate-spin")} />
            <span className="hidden sm:inline">Refresh</span>
        </RowButton>
    );

    // Nothing loaded yet: skeleton first, then a full error with retry.
    if (!hasData) {
        return (
            <PageFrame width="max-w-3xl">
                <PageHeader title="Memory" description="What LimeBot has learned about you and your setup." actions={refreshButton} />
                {loading ? (
                    <SkeletonRows count={5} />
                ) : (
                    <RequestError error={loadError} subject="memory" onRetry={() => void fetchMemories()} retrying={loading} />
                )}
            </PageFrame>
        );
    }

    if (enabled === false && memories.length === 0) {
        return (
            <PageFrame width="max-w-3xl">
                <PageHeader title="Memory" description="What LimeBot has learned about you and your setup." actions={refreshButton} />
                <EmptyState
                    icon={KeyRound}
                    title="Vector memory is off"
                    hint={
                        <>
                            {notice || "LimeBot is using its Markdown memory files instead."} Long-term recall needs an embedding model: add a{" "}
                            <code className="rounded bg-muted px-1 font-mono text-[11px]">GEMINI_API_KEY</code> or{" "}
                            <code className="rounded bg-muted px-1 font-mono text-[11px]">OPENAI_API_KEY</code> in Configuration.
                        </>
                    }
                    action={
                        <>
                            <Button size="sm" onClick={() => onNavigate?.("config")} className="gap-2">
                                <Settings className="h-4 w-4" />
                                Open Configuration
                            </Button>
                            <Button size="sm" variant="outline" onClick={() => void fetchMemories()} disabled={loading}>
                                <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
                                Check again
                            </Button>
                        </>
                    }
                />
            </PageFrame>
        );
    }

    return (
        <PageFrame width="max-w-3xl">
            <PageHeader
                sticky
                title="Memory"
                description={
                    usingFallback
                        ? "Read-only view of the Markdown memory files."
                        : "What LimeBot has learned about you and your setup. Hover an entry to forget it."
                }
                meta={
                    <>
                        <Chip>{filtered ? `${filteredMemories.length} of ${memories.length}` : `${memories.length} ${memories.length === 1 ? "entry" : "entries"}`}</Chip>
                        <StatusPill state={usingFallback ? "waiting" : "done"} label={usingFallback ? "Markdown fallback" : "Vector index"} className="hidden sm:inline-flex" />
                    </>
                }
                actions={refreshButton}
            />

            {loadError !== null && <RequestError compact error={loadError} subject="memory" onRetry={() => void fetchMemories()} retrying={loading} />}

            {usingFallback && (
                <Notice
                    title={notice || "Using Markdown memory files as the fallback."}
                    action={
                        <RowButton onClick={() => onNavigate?.("config")} className="border border-border">
                            <Settings /> Open Configuration
                        </RowButton>
                    }
                >
                    Vector entries are unavailable or not indexed yet, so entries cannot be deleted here.
                </Notice>
            )}

            {memories.length > 0 && (
                <div className="flex flex-col gap-2 sm:flex-row">
                    <div className="relative flex-1">
                        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                        <Input
                            placeholder="Search memories"
                            aria-label="Search memories"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            className="h-9 pl-8 text-sm"
                        />
                    </div>
                    <div className="flex gap-2">
                        <Select value={selectedCategory} onValueChange={setSelectedCategory}>
                            <SelectTrigger className="h-9 flex-1 sm:w-[160px] sm:flex-none" aria-label="Category">
                                <SelectValue placeholder="Category" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All categories</SelectItem>
                                {categories.map((cat) => (
                                    <SelectItem key={cat} value={cat}>
                                        {cat}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <Button
                            variant="outline"
                            size="icon"
                            onClick={() => setSortOrder((s) => (s === "newest" ? "oldest" : "newest"))}
                            title={sortOrder === "newest" ? "Newest first (click to flip)" : "Oldest first (click to flip)"}
                            aria-label="Flip sort order"
                            className="h-9 w-9 shrink-0"
                        >
                            <ArrowUpDown className="h-4 w-4" />
                        </Button>
                    </div>
                </div>
            )}

            {memories.length === 0 ? (
                <EmptyState
                    icon={Brain}
                    title={usingFallback ? "No Markdown memories yet" : "Nothing remembered yet"}
                    hint={usingFallback ? "Keep chatting and journal entries will show up here." : "Tell LimeBot something worth remembering and it will appear here."}
                    action={
                        usingFallback ? (
                            <Button size="sm" onClick={() => onNavigate?.("config")} className="gap-2">
                                <Settings className="h-4 w-4" />
                                Fix memory setup
                            </Button>
                        ) : undefined
                    }
                />
            ) : filteredMemories.length === 0 ? (
                <EmptyState
                    icon={Search}
                    title="No memories match"
                    hint="Try a different keyword or clear the filters."
                    action={
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                                setSearchQuery("");
                                setSelectedCategory("all");
                            }}
                        >
                            Clear filters
                        </Button>
                    }
                />
            ) : (
                <RowList>
                    {filteredMemories.map((m) => (
                        <MemoryRow key={m.id} entry={m} canDelete={canDelete} onDelete={handleDelete} />
                    ))}
                </RowList>
            )}
        </PageFrame>
    );
}
