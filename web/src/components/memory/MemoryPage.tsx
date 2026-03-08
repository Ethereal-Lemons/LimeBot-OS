import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Brain, Trash2, KeyRound, Search, ArrowUpDown, RefreshCw, AlertTriangle, Settings } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
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
import { toast } from "sonner";

type SortOrder = "newest" | "oldest";

interface MemoryPageProps {
    onNavigate?: (view: string) => void;
}

export function MemoryPage({ onNavigate }: MemoryPageProps) {
    const [memories, setMemories] = useState<any[]>([]);
    const [enabled, setEnabled] = useState<boolean | null>(null);
    const [mode, setMode] = useState<"vector" | "grep_fallback">("vector");
    const [readOnly, setReadOnly] = useState(false);
    const [notice, setNotice] = useState("");
    const [loading, setLoading] = useState(true);

    const [searchQuery, setSearchQuery] = useState("");
    const [selectedCategory, setSelectedCategory] = useState("all");
    const [sortOrder, setSortOrder] = useState<SortOrder>("newest");
    const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

    const fetchMemories = async () => {
        try {
            setLoading(true);
            const res = await axios.get(`${API_BASE_URL}/api/memory`);
            setEnabled(res.data.enabled);
            setMode((res.data.mode || (res.data.enabled ? "vector" : "grep_fallback")) as "vector" | "grep_fallback");
            setReadOnly(Boolean(res.data.read_only));
            setNotice(res.data.notice || "");
            setMemories(res.data.memories || []);
        } catch (err) {
            console.error("Failed to fetch memories:", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchMemories();
    }, []);

    const requestDelete = (id: string) => {
        if (readOnly) {
            toast.warning("Memory Explorer is read-only while vector memory is offline.");
            return;
        }
        setPendingDeleteId(id);
    };

    const handleDelete = async () => {
        if (!pendingDeleteId) return;
        try {
            await axios.delete(`${API_BASE_URL}/api/memory/${pendingDeleteId}`);
            setMemories(prev => prev.filter(m => m.id !== pendingDeleteId));
            toast.success("Memory deleted");
        } catch (err) {
            console.error("Failed to delete memory:", err);
            toast.error("Failed to delete memory");
        } finally {
            setPendingDeleteId(null);
        }
    };

    const categories = useMemo(() => {
        const cats = new Set<string>();
        memories.forEach(m => cats.add(m.category || "General"));
        return Array.from(cats).sort();
    }, [memories]);

    const filteredMemories = useMemo(() => {
        let result = [...memories];

        if (searchQuery.trim()) {
            const q = searchQuery.toLowerCase();
            result = result.filter(m =>
                (m.text || "").toLowerCase().includes(q) ||
                (m.category || "").toLowerCase().includes(q) ||
                (m.path || "").toLowerCase().includes(q)
            );
        }

        if (selectedCategory !== "all") {
            result = result.filter(m => (m.category || "General") === selectedCategory);
        }

        result.sort((a, b) => {
            const ta = new Date(a.timestamp || 0).getTime();
            const tb = new Date(b.timestamp || 0).getTime();
            return sortOrder === "newest" ? tb - ta : ta - tb;
        });

        return result;
    }, [memories, searchQuery, selectedCategory, sortOrder]);

    const usingFallback = mode === "grep_fallback";
    const canDelete = !readOnly && enabled === true;

    if (loading) {
        return (
            <div className="flex items-center justify-center h-full">
                <RefreshCw className="w-8 h-8 animate-spin text-primary" />
            </div>
        );
    }

    if (enabled === false && memories.length === 0) {
        return (
            <div className="h-full p-8 flex items-center justify-center">
                <Card className="max-w-md w-full border-yellow-500/30 bg-yellow-500/5">
                    <CardHeader className="text-center">
                        <div className="mx-auto w-12 h-12 bg-yellow-500/20 text-yellow-500 rounded-full flex items-center justify-center mb-4">
                            <KeyRound className="w-6 h-6" />
                        </div>
                        <CardTitle className="text-xl">Memory System Disabled</CardTitle>
                        <CardDescription className="pt-2 text-yellow-500/80">
                            Your vector storage subsystem is currently offline.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="text-sm text-muted-foreground text-center space-y-4">
                        <p className="text-yellow-400 font-semibold">
                            {notice || "Using grep as fallback."}
                        </p>
                        <p>
                            To unlock advanced Long-Term Memory capabilities, LimeBot requires an active <strong>Embedding Model</strong>.
                        </p>
                        <p>
                            Please ensure you have configured a valid <code className="bg-muted px-1 rounded">GEMINI_API_KEY</code> or <code className="bg-muted px-1 rounded">OPENAI_API_KEY</code> within your System Settings or <code className="bg-muted px-1 rounded">.env</code> file.
                        </p>
                        <div className="flex flex-col sm:flex-row gap-2 justify-center">
                            <Button onClick={() => onNavigate?.('config')} className="gap-2">
                                <Settings className="h-4 w-4" />
                                Open Configuration
                            </Button>
                            <Button variant="outline" onClick={fetchMemories}>
                                <RefreshCw className="h-4 w-4 mr-2" />
                                Retry
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            </div>
        );
    }

    return (
        <div className="h-full overflow-y-auto">
            <div className="space-y-6 p-8">
                <div className="sticky top-0 z-20 -mx-8 border-b border-border/60 bg-background/82 px-8 py-4 backdrop-blur-md">
                    <div className="flex items-center justify-between gap-4">
                        <div>
                            <h2 className="text-3xl font-bold tracking-tight flex items-center gap-3">
                                <Brain className="w-8 h-8 text-primary" />
                                Memory Explorer
                            </h2>
                            <p className="text-muted-foreground text-sm">
                                {usingFallback
                                    ? "Using grep as fallback. Memory Explorer is read-only while vectors are offline."
                                    : "View and manage facts the bot has learned about you or your environment."}
                            </p>
                        </div>
                        <div className="bg-primary/10 text-primary px-3 py-1 rounded-full text-xs font-bold font-mono">
                            {filteredMemories.length === memories.length
                                ? `${memories.length} Entries`
                                : `${filteredMemories.length} of ${memories.length} Entries`
                            }
                        </div>
                    </div>

                    {memories.length > 0 && (
                        <div className="mt-4 flex flex-col sm:flex-row gap-3">
                            <div className="relative flex-1">
                                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                                <Input
                                    placeholder="Search memories..."
                                    value={searchQuery}
                                    onChange={e => setSearchQuery(e.target.value)}
                                    className="pl-9"
                                />
                            </div>
                            <Select value={selectedCategory} onValueChange={setSelectedCategory}>
                                <SelectTrigger className="w-full sm:w-[180px]">
                                    <SelectValue placeholder="Category" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="all">All Categories</SelectItem>
                                    {categories.map(cat => (
                                        <SelectItem key={cat} value={cat}>{cat}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <Button
                                variant="outline"
                                size="icon"
                                onClick={() => setSortOrder(s => s === "newest" ? "oldest" : "newest")}
                                title={sortOrder === "newest" ? "Showing newest first" : "Showing oldest first"}
                                className="shrink-0"
                            >
                                <ArrowUpDown className="h-4 w-4" />
                            </Button>
                        </div>
                    )}
                </div>

                {usingFallback && (
                    <div className="rounded-lg border border-yellow-500/30 bg-yellow-500/10 p-3 text-sm text-yellow-500 flex items-start justify-between gap-3">
                        <div className="flex items-start gap-2">
                            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                            <div>
                                <p className="font-semibold">{notice || "Using grep as fallback."}</p>
                                <p className="text-yellow-500/80">Vector memory is offline, so this view is currently read-only.</p>
                            </div>
                        </div>
                        <Button variant="outline" size="sm" onClick={() => onNavigate?.('config')} className="shrink-0 border-yellow-500/30 bg-transparent hover:bg-yellow-500/10">
                            Open Configuration
                        </Button>
                    </div>
                )}

                {filteredMemories.length === 0 && memories.length > 0 ? (
                    <div className="p-12 text-center text-muted-foreground border rounded-lg border-dashed">
                        <Search className="w-12 h-12 mx-auto mb-4 opacity-20" />
                        <p>No memories match your search.</p>
                        <p className="text-xs mt-2 opacity-50">Try a different keyword or clear the filters.</p>
                    </div>
                ) : memories.length === 0 ? (
                    <div className="p-12 text-center text-muted-foreground border rounded-lg border-dashed">
                        <Brain className="w-12 h-12 mx-auto mb-4 opacity-20" />
                        <p>{usingFallback ? "No grep fallback memories found yet." : "No memories have been stored yet."}</p>
                        <p className="text-xs mt-2 opacity-50">
                            {usingFallback ? "Try chatting more so journal entries can be recalled." : "Tell the bot something important to remember!"}
                        </p>
                        <div className="mt-4 flex flex-col sm:flex-row justify-center gap-2">
                            {usingFallback && (
                                <Button onClick={() => onNavigate?.('config')} className="gap-2">
                                    <Settings className="h-4 w-4" />
                                    Fix Memory Setup
                                </Button>
                            )}
                            <Button variant="outline" onClick={fetchMemories}>
                                <RefreshCw className="h-4 w-4 mr-2" />
                                Refresh
                            </Button>
                        </div>
                    </div>
                ) : (
                    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                        {filteredMemories.map(m => (
                            <Card key={m.id} className="group relative overflow-hidden transition-all duration-300 hover:bg-muted/90 hover:border-primary/30 hover:shadow-md">
                                <CardHeader className="p-4 pb-2">
                                    <div className="flex justify-between items-start">
                                        <div className="bg-primary/20 text-primary uppercase tracking-widest text-[10px] font-bold px-2 py-0.5 rounded">
                                            {m.category || "General"}
                                        </div>
                                        {canDelete && (
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity text-destructive hover:bg-destructive/10"
                                                onClick={() => requestDelete(m.id)}
                                                title="Forget this memory"
                                            >
                                                <Trash2 className="h-3 w-3" />
                                            </Button>
                                        )}
                                    </div>
                                </CardHeader>
                                <CardContent className="p-4 pt-0">
                                    <div className="prose prose-sm dark:prose-invert max-w-none break-words leading-snug text-inherit">
                                        <ReactMarkdown
                                            remarkPlugins={[remarkGfm]}
                                            components={{
                                                p: ({ node, ...props }) => <p {...props} className="mb-2 last:mb-0" />,
                                                h1: ({ node, ...props }) => <h1 className="text-sm font-bold mt-2 mb-1" {...props} />,
                                                h2: ({ node, ...props }) => <h2 className="text-xs font-bold mt-1.5 mb-1 opacity-90" {...props} />,
                                                h3: ({ node, ...props }) => <h3 className="text-xs font-bold mt-1 mb-0.5 opacity-80" {...props} />,
                                                ul: ({ node, ...props }) => <ul className="list-disc ml-4 mb-2" {...props} />,
                                                ol: ({ node, ...props }) => <ol className="list-decimal ml-4 mb-2" {...props} />,
                                                li: ({ node, ...props }) => <li className="mb-1" {...props} />,
                                            }}
                                        >
                                            {m.text}
                                        </ReactMarkdown>
                                    </div>
                                    <div className="mt-4 flex items-center justify-between text-[10px] text-muted-foreground">
                                        <span>
                                            Learned {m.timestamp ? formatDistanceToNow(new Date(m.timestamp), { addSuffix: true }) : "recently"}
                                        </span>
                                        {m.source && (
                                            <span className="opacity-70 truncate max-w-[40%]" title={m.source}>
                                                {m.source}
                                            </span>
                                        )}
                                    </div>
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                )}
            </div>

            <AlertDialog open={pendingDeleteId !== null} onOpenChange={(open) => !open && setPendingDeleteId(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete memory?</AlertDialogTitle>
                        <AlertDialogDescription>
                            This entry will be permanently removed from vector memory and won&apos;t be recalled again.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={handleDelete} className="bg-red-600 hover:bg-red-700 text-white">
                            Delete memory
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
