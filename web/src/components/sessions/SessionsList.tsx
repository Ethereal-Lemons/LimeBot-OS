import { useEffect, useState } from 'react';
import axios from 'axios';
import {
    Activity,
    Bot,
    Clock,
    Code,
    Database,
    Hash,
    MessageSquare,
    Terminal,
    Trash2,
    Zap,
    RefreshCw
} from 'lucide-react';
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
    AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Search } from 'lucide-react';

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
    total_tokens: TokenUsage;
    injected_files: string[];
    history_file: string;
    skills?: string[];
    parent_id?: string;
    task?: string;
}

export function InstancesList({ currentSessionId }: { currentSessionId?: string }) {
    const [instances, setInstances] = useState<Instance[]>([]);
    const [loading, setLoading] = useState(true);
    const [searchQuery, setSearchQuery] = useState("");
    const [sortOrder, setSortOrder] = useState<"recent" | "oldest">("recent");
    const [selectedIds, setSelectedIds] = useState<string[]>([]);
    const [batchDeleting, setBatchDeleting] = useState(false);

    const fetchInstances = () => {
        setLoading(true);
        axios.get('http://localhost:8000/api/instances')
            .then(res => {
                setInstances(res.data as Instance[]);
                setLoading(false);
            })
            .catch(err => {
                if (err.response?.status !== 401) {
                    console.error("Failed to load instances:", err);
                }
                setLoading(false);
            });
    };

    useEffect(() => {
        fetchInstances();
    }, []);

    const handleDelete = (id: string) => {
        axios.delete(`http://localhost:8000/api/instances/${id}`)
            .then(() => {
                setInstances(prev => prev.filter(i => i.id !== id));
                setSelectedIds(prev => prev.filter(sid => sid !== id));
            })
            .catch(err => console.error("Error deleting instance:", err));
    };

    const handleDeleteBatch = (ids: string[]) => {
        if (ids.length === 0) return;
        setBatchDeleting(true);
        axios.post(`http://localhost:8000/api/instances/delete-batch`, { ids })
            .then(() => {
                setInstances(prev => prev.filter(i => !ids.includes(i.id)));
                setSelectedIds(prev => prev.filter(sid => !ids.includes(sid)));
                setBatchDeleting(false);
            })
            .catch(err => {
                console.error("Error deleting batch:", err);
                setBatchDeleting(false);
            });
    };

    const filteredInstances = instances
        .filter(inst => {
            if (!searchQuery) return true;
            const query = searchQuery.toLowerCase();
            return (
                inst.id.toLowerCase().includes(query) ||
                inst.origin.toLowerCase().includes(query) ||
                inst.model.toLowerCase().includes(query) ||
                (inst.skills && inst.skills.some(s => s.toLowerCase().includes(query)))
            );
        })
        .sort((a, b) => {
            if (sortOrder === "recent") return b.last_active - a.last_active;
            return a.last_active - b.last_active;
        });

    const toggleSelect = (id: string) => {
        setSelectedIds(prev =>
            prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]
        );
    };

    const handleSelectAll = () => {
        const allFilteredIds = filteredInstances.map(i => i.id);
        const allSelected = allFilteredIds.every(id => selectedIds.includes(id));
        if (allSelected) {
            setSelectedIds(prev => prev.filter(id => !allFilteredIds.includes(id)));
        } else {
            setSelectedIds(prev => Array.from(new Set([...prev, ...allFilteredIds])));
        }
    };

    if (loading && instances.length === 0) {
        return (
            <div className="flex items-center justify-center h-full">
                <RefreshCw className="w-8 h-8 animate-spin text-primary" />
            </div>
        );
    }

    return (
        <div className="h-full overflow-y-auto p-6 md:p-8 bg-background/50">
            <div className="max-w-6xl mx-auto space-y-6">
                <header className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div>
                        <h1 className="text-2xl font-bold flex items-center gap-2">
                            <Terminal className="h-6 w-6 text-primary" />
                            Instances Dashboard
                        </h1>
                        <p className="text-muted-foreground mt-1">
                            Manage active agent contexts, skills, and memory.
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <Button variant="outline" size="sm" onClick={fetchInstances} disabled={loading} className="w-full md:w-auto">
                            Refresh
                        </Button>
                    </div>
                </header>

                <div className="flex flex-col space-y-4">
                    {/* Controls Row */}
                    <div className="flex flex-col md:flex-row gap-4">
                        <div className="relative flex-1">
                            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                            <Input
                                placeholder="Search instances..."
                                className="pl-9"
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                            />
                        </div>
                        <Select value={sortOrder} onValueChange={(v: any) => setSortOrder(v)}>
                            <SelectTrigger className="w-full md:w-[180px]">
                                <SelectValue placeholder="Sort by" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="recent">Most Recent</SelectItem>
                                <SelectItem value="oldest">Oldest Active</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>

                    {/* Bulk Selection Toolbar */}
                    {(instances.length > 0 || filteredInstances.length > 0) && (
                        <div className="flex flex-wrap items-center gap-3 p-3 bg-muted/40 border border-dashed rounded-lg animate-in fade-in slide-in-from-top-2 duration-200">
                            <div className="flex items-center gap-2 pr-4 border-r border-border/50">
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={handleSelectAll}
                                    className="text-xs h-8 px-2"
                                >
                                    {filteredInstances.length > 0 && filteredInstances.every(id => selectedIds.includes(id.id)) ? 'Deselect Page' : 'Select Page'}
                                </Button>
                                {selectedIds.length > 0 && (
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={() => setSelectedIds([])}
                                        className="text-xs h-8 px-2 text-muted-foreground"
                                    >
                                        Clear ({selectedIds.length})
                                    </Button>
                                )}
                            </div>

                            <div className="flex flex-wrap items-center gap-2">
                                {/* Bulk Delete Selected */}
                                <AlertDialog>
                                    <AlertDialogTrigger asChild>
                                        <Button
                                            variant="destructive"
                                            size="sm"
                                            disabled={selectedIds.length === 0 || batchDeleting}
                                            className="h-8 gap-2"
                                        >
                                            <Trash2 className="h-3.5 w-3.5" />
                                            Delete Selected
                                        </Button>
                                    </AlertDialogTrigger>
                                    <AlertDialogContent>
                                        <AlertDialogHeader>
                                            <AlertDialogTitle>Delete {selectedIds.length} instances?</AlertDialogTitle>
                                            <AlertDialogDescription>
                                                This will permanently delete {selectedIds.length} session contexts and their logs.
                                                This action cannot be undone.
                                            </AlertDialogDescription>
                                        </AlertDialogHeader>
                                        <AlertDialogFooter>
                                            <AlertDialogCancel>Cancel</AlertDialogCancel>
                                            <AlertDialogAction
                                                onClick={() => handleDeleteBatch(selectedIds)}
                                                className="bg-red-600 hover:bg-red-700 text-white"
                                            >
                                                Delete All selected
                                            </AlertDialogAction>
                                        </AlertDialogFooter>
                                    </AlertDialogContent>
                                </AlertDialog>

                                {/* WIPER: Wipe All */}
                                <AlertDialog>
                                    <AlertDialogTrigger asChild>
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            disabled={instances.length === 0 || batchDeleting}
                                            className="h-8 text-red-500 border-red-500/20 hover:bg-red-500/10 gap-2"
                                        >
                                            <Zap className="h-3.5 w-3.5" />
                                            Wipe All
                                        </Button>
                                    </AlertDialogTrigger>
                                    <AlertDialogContent>
                                        <AlertDialogHeader>
                                            <AlertDialogTitle className="text-red-500">Wipe All Instances?</AlertDialogTitle>
                                            <AlertDialogDescription>
                                                Are you absolutely sure? This will delete <span className="font-bold text-foreground">{instances.length}</span> instances.
                                                Everything will be cleared.
                                            </AlertDialogDescription>
                                        </AlertDialogHeader>
                                        <AlertDialogFooter>
                                            <AlertDialogCancel>Cancel</AlertDialogCancel>
                                            <AlertDialogAction
                                                onClick={() => handleDeleteBatch(instances.map(i => i.id))}
                                                className="bg-red-600 hover:bg-red-700 text-white"
                                            >
                                                Yes, Wipe Everything
                                            </AlertDialogAction>
                                        </AlertDialogFooter>
                                    </AlertDialogContent>
                                </AlertDialog>
                            </div>

                            {selectedIds.length > 0 && (
                                <div className="ml-auto text-[11px] font-medium text-muted-foreground uppercase tracking-wider px-2 py-1 bg-background/50 rounded border border-border">
                                    {selectedIds.length} Selected
                                </div>
                            )}
                        </div>
                    )}
                </div>

                <div className="grid gap-4">
                    {/* First, show main sessions */}
                    {filteredInstances.filter(i => !i.parent_id).map(inst => (
                        <div key={inst.id} className="space-y-4">
                            <InstanceCard
                                instance={inst}
                                isCurrent={currentSessionId !== undefined && (inst.id === currentSessionId || inst.id.includes(currentSessionId))}
                                onDelete={() => handleDelete(inst.id)}
                                isSelected={selectedIds.includes(inst.id)}
                                onSelect={() => toggleSelect(inst.id)}
                            />

                            {/* Then, show its sub-agents nested below it */}
                            {filteredInstances.filter(sub => sub.parent_id === inst.id).map(sub => (
                                <div key={sub.id} className="ml-8 pl-8 border-l-2 border-primary/20">
                                    <InstanceCard
                                        instance={sub}
                                        isSubAgent
                                        onDelete={() => handleDelete(sub.id)}
                                        isSelected={selectedIds.includes(sub.id)}
                                        onSelect={() => toggleSelect(sub.id)}
                                    />
                                </div>
                            ))}
                        </div>
                    ))}

                    {/* Show orphaned sub-agents if any */}
                    {filteredInstances.filter(i => i.parent_id && !filteredInstances.some(p => p.id === i.parent_id)).map(sub => (
                        <InstanceCard
                            key={sub.id}
                            instance={sub}
                            isSubAgent
                            onDelete={() => handleDelete(sub.id)}
                            isSelected={selectedIds.includes(sub.id)}
                            onSelect={() => toggleSelect(sub.id)}
                        />
                    ))}

                    {filteredInstances.length === 0 && (
                        <div className="p-12 text-center text-muted-foreground border border-dashed rounded-xl">
                            {instances.length === 0
                                ? "No active instances found. Start a chat/task to create one."
                                : "No instances match your search filters."}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function InstanceCard({
    instance,
    onDelete,
    isCurrent,
    isSubAgent,
    isSelected,
    onSelect
}: {
    instance: Instance,
    onDelete: () => void,
    isCurrent?: boolean,
    isSubAgent?: boolean,
    isSelected: boolean,
    onSelect: () => void
}) {
    return (
        <div className={cn(
            "bg-card border rounded-xl p-6 shadow-sm hover:shadow-md transition-all relative group",
            isCurrent ? "border-primary/40 ring-1 ring-primary/20" : "border-border",
            isSubAgent && "bg-muted/30 border-dashed",
            isSelected && "border-primary bg-primary/5 ring-1 ring-primary/30"
        )}>

            {/* Selection Checkbox (Floating) */}
            <div
                className={cn(
                    "absolute -left-3 top-1/2 -translate-y-1/2 z-10 p-1 bg-background border rounded-lg cursor-pointer transition-all hover:scale-110",
                    isSelected ? "border-primary text-primary" : "border-border text-transparent hover:text-muted-foreground"
                )}
                onClick={(e) => {
                    e.stopPropagation();
                    onSelect();
                }}
            >
                <div className={cn(
                    "h-4 w-4 rounded flex items-center justify-center transition-colors",
                    isSelected ? "bg-primary" : "bg-muted"
                )}>
                    {isSelected && <Zap className="h-3 w-3 text-primary-foreground fill-current" />}
                </div>
            </div>

            {/* Context ID Header */}
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6 pb-6 border-b border-border/50">
                <div className="space-y-1">
                    <div className="flex items-center gap-2 text-sm text-primary font-mono mb-1">
                        <Hash className="h-4 w-4" />
                        <span className="break-all font-bold">{instance.id}</span>
                        {isCurrent && (
                            <span className="ml-2 bg-primary text-primary-foreground text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wide">
                                You
                            </span>
                        )}
                        {isSubAgent && (
                            <span className="ml-2 bg-amber-500/10 text-amber-500 border border-amber-500/20 text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wide flex items-center gap-1">
                                <Zap className="h-3 w-3" /> Sub-Agent
                            </span>
                        )}
                    </div>
                    <div className="flex items-center gap-2 text-muted-foreground text-xs">
                        <Activity className="h-3 w-3" />
                        Context: <span className="text-foreground font-medium uppercase">{instance.origin || "Unknown"}</span>
                    </div>
                </div>

                <div className="flex items-center gap-4 text-xs text-muted-foreground">
                    <div className="flex items-center gap-1.5 bg-muted/50 px-3 py-1.5 rounded-full">
                        <Clock className="h-3 w-3" />
                        Last Active: {new Date(instance.last_active * 1000).toLocaleString()}
                    </div>
                    <div className="flex items-center gap-1.5 bg-muted/50 px-3 py-1.5 rounded-full">
                        <Bot className="h-3 w-3" />
                        Model: <span className="text-foreground">{instance.model}</span>
                    </div>
                    <AlertDialog>
                        <AlertDialogTrigger asChild>
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-muted-foreground hover:text-red-500 hover:bg-red-500/10"
                                title="Terminate Instance"
                            >
                                <Trash2 className="h-4 w-4" />
                            </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                            <AlertDialogHeader>
                                <AlertDialogTitle>Terminate Instance?</AlertDialogTitle>
                                <AlertDialogDescription>
                                    This will permanently delete the session context and chat logs for
                                    <span className="font-mono text-foreground"> {instance.id}</span>.
                                    This action cannot be undone.
                                </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                                <AlertDialogCancel>Cancel</AlertDialogCancel>
                                <AlertDialogAction onClick={onDelete} className="bg-red-600 hover:bg-red-700 text-white">
                                    Terminate
                                </AlertDialogAction>
                            </AlertDialogFooter>
                        </AlertDialogContent>
                    </AlertDialog>
                </div>
            </div>

            {instance.task && (
                <div className="mb-6 p-4 bg-zinc-950/50 rounded-xl border border-primary/10 shadow-inner">
                    <div className="text-[10px] uppercase text-primary/70 font-black mb-2 flex items-center gap-1.5 tracking-widest">
                        <Terminal className="h-3 w-3" /> Active Task
                    </div>
                    <p className="text-xs text-slate-300 font-mono italic leading-relaxed">
                        "{instance.task}"
                    </p>
                </div>
            )}

            <div className="grid md:grid-cols-2 gap-6">

                {/* Left Col: Capabilities */}
                <div className="space-y-4">
                    {/* Active Skills */}
                    <div className="space-y-2">
                        <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                            <Zap className="h-3 w-3" />
                            Active Skills
                        </h3>
                        <div className="flex flex-wrap gap-2">
                            {(instance.skills && instance.skills.length > 0) ? (
                                instance.skills.map(skill => (
                                    <div key={skill} className="flex items-center gap-1.5 bg-primary/10 border border-primary/20 text-primary px-2 py-1 rounded text-xs font-medium">
                                        {skill}
                                    </div>
                                ))
                            ) : (
                                <span className="text-xs text-muted-foreground italic pl-1">No specific skills loaded.</span>
                            )}
                        </div>
                    </div>

                    {/* Injected Files */}
                    <div className="space-y-2">
                        <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                            <Code className="h-3 w-3" />
                            Context Files
                        </h3>
                        <div className="flex flex-wrap gap-2">
                            {instance.injected_files.length > 0 ? (
                                instance.injected_files.map((file, i) => (
                                    <span key={i} className="text-[10px] px-2 py-1 rounded-md bg-white/5 border border-white/10 font-mono text-zinc-400">
                                        {file}
                                    </span>
                                ))
                            ) : (
                                <span className="text-xs text-muted-foreground italic pl-1">No files in context.</span>
                            )}
                        </div>
                    </div>
                </div>

                {/* Right Col: Stats & Storage */}
                <div className="space-y-4">
                    {/* Tokens */}
                    <div className="bg-zinc-950/50 rounded-lg p-3 border border-border/50">
                        <div className="text-[10px] uppercase text-muted-foreground mb-2 flex justify-between">
                            <span>Token Usage</span>
                            <span className="text-zinc-500 font-mono">Total</span>
                        </div>
                        <div className="flex items-end gap-1">
                            <span className="text-2xl font-bold text-foreground font-mono">{instance.total_tokens.total.toLocaleString()}</span>
                            <span className="text-xs text-muted-foreground mb-1 font-mono">tokens</span>
                        </div>
                        <div className="grid grid-cols-2 gap-2 mt-2 pt-2 border-t border-white/5">
                            <div className="text-xs text-muted-foreground font-mono">In: <span className="text-zinc-400">{instance.total_tokens.input.toLocaleString()}</span></div>
                            <div className="text-xs text-muted-foreground font-mono">Out: <span className="text-zinc-400">{instance.total_tokens.output.toLocaleString()}</span></div>
                        </div>
                    </div>

                    {/* Memory Log */}
                    <div className="space-y-1">
                        <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                            <Database className="h-3 w-3" />
                            Memory Log
                        </h3>
                        <div className="group/file flex items-center gap-2 text-[10px] text-muted-foreground font-mono bg-black/20 p-2 rounded break-all border border-white/5 hover:border-primary/30 transition-colors">
                            <MessageSquare className="h-3 w-3 shrink-0 text-primary/50" />
                            <span className="truncate">{instance.history_file}</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
