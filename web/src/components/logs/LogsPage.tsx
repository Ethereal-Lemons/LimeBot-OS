import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import {
    Terminal,
    RefreshCw,
    Trash2,
    Search,
    ArrowDown,
    Play,
    ChevronDown,
    ChevronUp,
    Clock3,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
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

interface ParsedLog {
    id: string;
    raw: string;
    timestamp?: string;
    level?: "INFO" | "WARNING" | "ERROR" | "CRITICAL" | "DEBUG" | string;
    context?: string;
    message: string;
    continuationCount: number;
}

const LOG_LEVELS = ["ERROR", "CRITICAL", "WARNING", "INFO", "DEBUG"] as const;
const LINE_COUNT_OPTIONS = [100, 200, 500];

const loguruRegex =
    /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+\|\s+([A-Z]+)\s+\|\s+([^:]+:[^:]+:\d+)\s+-\s+(.*)$/;
const uvicornRegex = /^(INFO|WARNING|ERROR|CRITICAL):\s+(.*)$/;

function parseSingleLogLine(line: string, idSeed: number): ParsedLog {
    const loguruMatch = line.match(loguruRegex);
    if (loguruMatch) {
        return {
            id: `log-${idSeed}`,
            raw: line,
            timestamp: loguruMatch[1],
            level: loguruMatch[2].trim(),
            context: loguruMatch[3],
            message: loguruMatch[4],
            continuationCount: 0,
        };
    }

    const uvicornMatch = line.match(uvicornRegex);
    if (uvicornMatch) {
        return {
            id: `log-${idSeed}`,
            raw: line,
            level: uvicornMatch[1],
            message: uvicornMatch[2],
            continuationCount: 0,
        };
    }

    let detectedLevel: string | undefined;
    for (const level of LOG_LEVELS) {
        if (line.includes(level)) detectedLevel = level;
    }

    return {
        id: `log-${idSeed}`,
        raw: line,
        level: detectedLevel,
        message: line,
        continuationCount: 0,
    };
}

function isNewLogBoundary(line: string) {
    return loguruRegex.test(line) || uvicornRegex.test(line);
}

function parseLogLines(lines: string[]): ParsedLog[] {
    const parsed: ParsedLog[] = [];

    for (const rawLine of lines) {
        const line = rawLine ?? "";

        if (parsed.length === 0 || isNewLogBoundary(line)) {
            parsed.push(parseSingleLogLine(line, parsed.length));
            continue;
        }

        const current = parsed[parsed.length - 1];
        current.raw = `${current.raw}\n${line}`;
        current.message = `${current.message}\n${line}`;
        current.continuationCount += 1;
    }

    return parsed.map((log, index) => ({ ...log, id: `log-${index}` }));
}

function levelTone(level?: string) {
    switch (level) {
        case "INFO":
            return "border-blue-500/30 bg-blue-500/10 text-blue-400";
        case "WARNING":
            return "border-amber-500/30 bg-amber-500/10 text-amber-400";
        case "ERROR":
        case "CRITICAL":
            return "border-red-500/30 bg-red-500/10 text-red-400";
        case "DEBUG":
            return "border-zinc-500/30 bg-zinc-500/10 text-zinc-300";
        default:
            return "border-white/10 bg-white/5 text-muted-foreground";
    }
}

function formatClock(value?: string) {
    if (!value) return "Unknown";
    return value.split(" ")[1] || value;
}

export function LogsPage() {
    const [logs, setLogs] = useState<ParsedLog[]>([]);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [autoScroll, setAutoScroll] = useState(true);
    const [live, setLive] = useState(true);
    const [compact, setCompact] = useState(false);
    const [filter, setFilter] = useState("");
    const [selectedLevels, setSelectedLevels] = useState<string[]>([]);
    const [selectedContext, setSelectedContext] = useState<string>("all");
    const [errorsOnly, setErrorsOnly] = useState(false);
    const [lineCount, setLineCount] = useState(200);
    const [clearDialogOpen, setClearDialogOpen] = useState(false);
    const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
    const [lastRefreshAt, setLastRefreshAt] = useState<Date | null>(null);
    const [pendingNewCount, setPendingNewCount] = useState(0);

    const bottomRef = useRef<HTMLDivElement>(null);
    const scrollContainerRef = useRef<HTMLDivElement>(null);
    const autoScrollRef = useRef(autoScroll);

    useEffect(() => {
        autoScrollRef.current = autoScroll;
    }, [autoScroll]);

    const fetchLogs = async (options?: { silent?: boolean }) => {
        const silent = options?.silent ?? false;
        if (!silent) setRefreshing(true);

        try {
            const res = await axios.get(`${API_BASE_URL}/api/logs?lines=${lineCount}`);
            const rawLogs = Array.isArray(res.data.logs) ? (res.data.logs as string[]) : [];
            const hasNoLogsSentinel = rawLogs.length === 1 && rawLogs[0] === "No logs found.";
            const nextLogs = hasNoLogsSentinel ? [] : parseLogLines(rawLogs);

            setLogs((prev) => {
                if (!autoScrollRef.current && prev.length > 0 && nextLogs.length > 0) {
                    const previousLast = prev[prev.length - 1];
                    const lastIndex = nextLogs.findIndex(
                        (entry) =>
                            entry.raw === previousLast.raw &&
                            entry.timestamp === previousLast.timestamp &&
                            entry.level === previousLast.level
                    );
                    const addedCount = lastIndex >= 0 ? Math.max(0, nextLogs.length - lastIndex - 1) : nextLogs.length;
                    if (addedCount > 0) {
                        setPendingNewCount((count) => count + addedCount);
                    }
                } else if (autoScrollRef.current) {
                    setPendingNewCount(0);
                }

                return nextLogs;
            });

            setLastRefreshAt(new Date());
            setLoading(false);
        } catch (err: any) {
            if (err.response?.status !== 401) {
                console.error("Failed to fetch logs:", err);
            }
            setLoading(false);
        } finally {
            if (!silent) setRefreshing(false);
        }
    };

    useEffect(() => {
        void fetchLogs();
    }, [lineCount]);

    useEffect(() => {
        if (!live) return;
        const interval = window.setInterval(() => {
            void fetchLogs({ silent: true });
        }, 2000);
        return () => window.clearInterval(interval);
    }, [live, lineCount]);

    useEffect(() => {
        if (autoScroll && bottomRef.current) {
            bottomRef.current.scrollIntoView({ behavior: "smooth" });
            setPendingNewCount(0);
        }
    }, [logs, autoScroll]);

    const handleScroll = () => {
        if (!scrollContainerRef.current) return;
        const { scrollTop, scrollHeight, clientHeight } = scrollContainerRef.current;
        const nearBottom = scrollHeight - scrollTop - clientHeight < 50;

        if (!nearBottom && autoScroll) {
            setAutoScroll(false);
        } else if (nearBottom && !autoScroll) {
            setAutoScroll(true);
            setPendingNewCount(0);
        }
    };

    const contexts = useMemo(() => {
        const counts = new Map<string, number>();
        for (const log of logs) {
            if (!log.context) continue;
            counts.set(log.context, (counts.get(log.context) || 0) + 1);
        }
        return [...counts.entries()]
            .sort((a, b) => b[1] - a[1])
            .map(([context]) => context);
    }, [logs]);

    useEffect(() => {
        if (selectedContext !== "all" && !contexts.includes(selectedContext)) {
            setSelectedContext("all");
        }
    }, [contexts, selectedContext]);

    const filteredLogs = useMemo(() => {
        return logs.filter((log) => {
            if (filter && !log.raw.toLowerCase().includes(filter.toLowerCase())) return false;
            if (selectedContext !== "all" && log.context !== selectedContext) return false;
            if (errorsOnly && !["ERROR", "CRITICAL"].includes(log.level || "")) return false;
            if (selectedLevels.length > 0 && !selectedLevels.includes(log.level || "")) return false;
            return true;
        });
    }, [logs, filter, selectedContext, errorsOnly, selectedLevels]);

    const stats = useMemo(() => {
        const byLevel = {
            ERROR: 0,
            WARNING: 0,
            INFO: 0,
            DEBUG: 0,
            CRITICAL: 0,
        };

        for (const log of logs) {
            if (log.level && log.level in byLevel) {
                byLevel[log.level as keyof typeof byLevel] += 1;
            }
        }

        return {
            total: logs.length,
            visible: filteredLogs.length,
            errors: byLevel.ERROR + byLevel.CRITICAL,
            warnings: byLevel.WARNING,
            infos: byLevel.INFO,
            debugs: byLevel.DEBUG,
        };
    }, [logs, filteredLogs.length]);

    const toggleLevel = (level: string) => {
        setSelectedLevels((current) =>
            current.includes(level)
                ? current.filter((item) => item !== level)
                : [...current, level]
        );
    };

    const clearFilters = () => {
        setFilter("");
        setSelectedLevels([]);
        setSelectedContext("all");
        setErrorsOnly(false);
    };

    const toggleExpanded = (id: string) => {
        setExpandedIds((current) => {
            const next = new Set(current);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    const clearLogs = async () => {
        try {
            await axios.post(`${API_BASE_URL}/api/control/clear-logs`);
            setLogs([]);
            setExpandedIds(new Set());
            setPendingNewCount(0);
            toast.success("System logs cleared");
        } catch (err) {
            console.error("Failed to clear logs:", err);
            toast.error("Failed to clear system logs");
        } finally {
            setClearDialogOpen(false);
        }
    };

    const hasActiveFilters =
        Boolean(filter) ||
        selectedLevels.length > 0 ||
        selectedContext !== "all" ||
        errorsOnly;

    return (
        <div className="relative flex h-full flex-col overflow-hidden bg-[#0d0f12]">
            {/* ── Header ── */}
            <div className="shrink-0 border-b border-white/8 bg-[#111318]/95 backdrop-blur">
                {/* Title + search + actions */}
                <div className="flex items-center gap-3 px-4 py-2.5">
                    <div className="flex items-center gap-2 text-primary">
                        <Terminal className="h-4 w-4" />
                        <span className="text-sm font-bold tracking-tight text-foreground">System Logs</span>
                    </div>

                    {/* Search */}
                    <div className="relative flex-1">
                        <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
                        <input
                            type="text"
                            placeholder="Search logs, stack traces, contexts..."
                            value={filter}
                            onChange={(e) => setFilter(e.target.value)}
                            className="w-full rounded-md border border-white/10 bg-white/5 py-1.5 pl-8 pr-3 text-xs text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-primary/50"
                        />
                    </div>

                    <Button variant="outline" size="sm" onClick={() => void fetchLogs()} className="h-7 gap-1.5 border-white/10 bg-white/5 px-2.5 text-xs hover:bg-white/10">
                        <RefreshCw className={cn("h-3 w-3", refreshing && "animate-spin")} /> Refresh
                    </Button>
                    <Button variant="ghost" size="icon" onClick={() => setClearDialogOpen(true)} className="h-7 w-7 text-muted-foreground hover:bg-red-500/10 hover:text-red-400" title="Clear logs">
                        <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                </div>

                {/* Controls row */}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-white/5 px-4 py-2">
                    {/* Line count */}
                    <div className="flex items-center gap-1">
                        {LINE_COUNT_OPTIONS.map((count) => (
                            <button
                                key={count}
                                onClick={() => setLineCount(count)}
                                className={cn(
                                    "rounded px-2 py-0.5 text-[11px] font-medium transition-colors",
                                    lineCount === count
                                        ? "bg-primary/20 text-primary"
                                        : "text-muted-foreground hover:text-foreground"
                                )}
                            >
                                {count}
                            </button>
                        ))}
                        <span className="text-[11px] text-muted-foreground/50">lines</span>
                    </div>

                    <div className="h-3 w-px bg-white/10" />

                    {/* Level toggles */}
                    <div className="flex items-center gap-1">
                        <span className="text-[10px] uppercase tracking-widest text-muted-foreground/50 mr-1">Level</span>
                        {LOG_LEVELS.map((level) => (
                            <button
                                key={level}
                                onClick={() => toggleLevel(level)}
                                className={cn(
                                    "rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide transition-colors border",
                                    selectedLevels.includes(level)
                                        ? levelTone(level)
                                        : "border-white/10 text-muted-foreground/60 hover:border-white/20 hover:text-muted-foreground"
                                )}
                            >
                                {level}
                            </button>
                        ))}
                    </div>

                    <div className="h-3 w-px bg-white/10" />

                    {/* Toggles */}
                    <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
                        {[
                            { label: "Live", value: live, set: setLive },
                            { label: "Auto-scroll", value: autoScroll, set: setAutoScroll },
                            { label: "Compact", value: compact, set: setCompact },
                            { label: "Errors only", value: errorsOnly, set: setErrorsOnly },
                        ].map(({ label, value, set }) => (
                            <label key={label} className="flex cursor-pointer items-center gap-1.5 select-none">
                                <Switch checked={value} onCheckedChange={set} className="h-4 w-7 [&>span]:h-3 [&>span]:w-3" />
                                <span className={value ? "text-foreground" : ""}>{label}</span>
                            </label>
                        ))}
                    </div>

                    <div className="ml-auto flex items-center gap-3 text-[11px] text-muted-foreground/60">
                        {/* Stats inline */}
                        <span><span className="text-foreground font-medium">{stats.visible}</span>/{stats.total} lines</span>
                        {stats.errors > 0 && <span className="text-red-400"><span className="font-bold">{stats.errors}</span> errors</span>}
                        {stats.warnings > 0 && <span className="text-amber-400"><span className="font-bold">{stats.warnings}</span> warn</span>}
                        {pendingNewCount > 0 && <span className="text-primary"><span className="font-bold">{pendingNewCount}</span> new</span>}
                        <span className="flex items-center gap-1">
                            <Clock3 className="h-3 w-3" />
                            {lastRefreshAt ? lastRefreshAt.toLocaleTimeString() : "—"}
                            {(refreshing || loading) && <RefreshCw className="h-3 w-3 animate-spin ml-1" />}
                        </span>
                        {hasActiveFilters && (
                            <button onClick={clearFilters} className="rounded px-1.5 py-0.5 text-[10px] border border-white/10 text-muted-foreground hover:text-foreground hover:border-white/20">
                                Clear filters
                            </button>
                        )}
                    </div>
                </div>

                {/* Context filter pills — only if there's more than one context */}
                {contexts.length > 1 && (
                    <div className="flex flex-wrap items-center gap-1 border-t border-white/5 px-4 py-1.5">
                        <span className="text-[10px] uppercase tracking-widest text-muted-foreground/50 mr-1">Context</span>
                        <button
                            onClick={() => setSelectedContext("all")}
                            className={cn(
                                "rounded px-2 py-0.5 text-[10px] font-mono border transition-colors",
                                selectedContext === "all"
                                    ? "border-primary/40 bg-primary/10 text-primary"
                                    : "border-white/10 text-muted-foreground/70 hover:border-white/20"
                            )}
                        >
                            All
                        </button>
                        {contexts.slice(0, 12).map((ctx) => (
                            <button
                                key={ctx}
                                onClick={() => setSelectedContext(ctx)}
                                className={cn(
                                    "max-w-[220px] truncate rounded px-2 py-0.5 text-[10px] font-mono border transition-colors",
                                    selectedContext === ctx
                                        ? "border-primary/40 bg-primary/10 text-primary"
                                        : "border-white/10 text-muted-foreground/70 hover:border-white/20"
                                )}
                                title={ctx}
                            >
                                {ctx}
                            </button>
                        ))}
                    </div>
                )}
            </div>

            {/* ── Log stream ── */}
            <div
                ref={scrollContainerRef}
                onScroll={handleScroll}
                className="flex-1 overflow-y-auto px-0 py-1 font-mono text-xs selection:bg-primary/30"
            >
                {loading ? (
                    <div className="flex h-full flex-col items-center justify-center text-muted-foreground/50">
                        <RefreshCw className="mb-3 h-8 w-8 animate-spin" />
                        <p className="text-xs">Loading logs…</p>
                    </div>
                ) : logs.length === 0 ? (
                    <div className="flex h-full flex-col items-center justify-center text-center text-muted-foreground/40">
                        <Terminal className="mb-3 h-10 w-10 opacity-20" />
                        <p className="text-xs font-medium">No system logs yet.</p>
                    </div>
                ) : filteredLogs.length === 0 ? (
                    <div className="flex h-full flex-col items-center justify-center text-center text-muted-foreground/40">
                        <Search className="mb-3 h-8 w-8 opacity-30" />
                        <p className="text-xs font-medium">No logs match the current filters.</p>
                        <button onClick={clearFilters} className="mt-3 rounded border border-white/10 px-3 py-1 text-[11px] hover:border-white/20 hover:text-muted-foreground">Clear filters</button>
                    </div>
                ) : (
                    <div>
                        {filteredLogs.map((log, index) => {
                            const expanded = expandedIds.has(log.id);
                            const hasDetails = log.continuationCount > 0;
                            const firstLine = log.message.split("\n")[0];

                            // Level color for the message text
                            const msgColor =
                                log.level === "ERROR" || log.level === "CRITICAL"
                                    ? "text-red-300"
                                    : log.level === "WARNING"
                                        ? "text-amber-200"
                                        : log.level === "DEBUG"
                                            ? "text-zinc-400"
                                            : "text-zinc-100";

                            const levelColor =
                                log.level === "ERROR" || log.level === "CRITICAL"
                                    ? "text-red-400"
                                    : log.level === "WARNING"
                                        ? "text-amber-400"
                                        : log.level === "DEBUG"
                                            ? "text-zinc-500"
                                            : "text-blue-400";

                            const borderColor =
                                log.level === "ERROR" || log.level === "CRITICAL"
                                    ? "border-l-red-500/70"
                                    : log.level === "WARNING"
                                        ? "border-l-amber-500/50"
                                        : log.level === "DEBUG"
                                            ? "border-l-zinc-600/50"
                                            : "border-l-transparent";

                            return (
                                <button
                                    key={log.id}
                                    type="button"
                                    onClick={() => hasDetails && toggleExpanded(log.id)}
                                    className={cn(
                                        "group w-full border-l-2 text-left transition-colors",
                                        borderColor,
                                        compact ? "px-4 py-0.5" : "px-4 py-1.5",
                                        hasDetails ? "cursor-pointer hover:bg-white/[0.03]" : "cursor-default",
                                        index % 2 === 1 && "bg-white/[0.015]"
                                    )}
                                >
                                    <div className="flex items-baseline gap-2 md:gap-3">
                                        {/* Line number */}
                                        <span className="hidden w-8 shrink-0 text-right text-[10px] text-zinc-600 md:inline-block">
                                            {index + 1}
                                        </span>

                                        {/* Timestamp */}
                                        {log.timestamp && (
                                            <span className="shrink-0 whitespace-nowrap text-[10px] text-zinc-500">
                                                {formatClock(log.timestamp)}
                                            </span>
                                        )}

                                        {/* Level badge */}
                                        <span className={cn("shrink-0 text-[10px] font-bold uppercase w-[52px]", levelColor)}>
                                            {log.level || "LOG"}
                                        </span>

                                        {/* Context */}
                                        {log.context && (
                                            <span className="hidden shrink-0 max-w-[200px] truncate text-[10px] text-zinc-500 md:inline-block" title={log.context}>
                                                {log.context}
                                            </span>
                                        )}

                                        {/* Message */}
                                        <span className={cn("min-w-0 flex-1 leading-relaxed break-words", msgColor, compact && !expanded && "truncate")}>
                                            {expanded ? (
                                                <span className="whitespace-pre-wrap">{log.message}</span>
                                            ) : (
                                                <>
                                                    {firstLine}
                                                    {log.continuationCount > 0 && (
                                                        <span className="ml-2 text-[10px] text-zinc-600 group-hover:text-zinc-400">
                                                            [{log.continuationCount} more lines]
                                                        </span>
                                                    )}
                                                </>
                                            )}
                                        </span>

                                        {/* Expand indicator */}
                                        {hasDetails && (
                                            <span className="shrink-0 text-zinc-600 group-hover:text-zinc-400">
                                                {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                                            </span>
                                        )}
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                )}

                <div ref={bottomRef} className="h-4" />
            </div>

            {/* ── Resume scrolling button ── */}
            {!autoScroll && logs.length > 0 && (
                <div className="absolute bottom-6 right-6 animate-in fade-in slide-in-from-bottom-4 duration-200">
                    <Button
                        size="sm"
                        onClick={() => {
                            setAutoScroll(true);
                            setPendingNewCount(0);
                            bottomRef.current?.scrollIntoView({ behavior: "smooth" });
                        }}
                        className="rounded-full border border-white/10 bg-[#111318]/90 text-xs font-medium text-foreground shadow-xl backdrop-blur hover:bg-[#1a1d24]"
                    >
                        {live ? <ArrowDown className="mr-1.5 h-3.5 w-3.5" /> : <Play className="mr-1.5 h-3.5 w-3.5" />}
                        {pendingNewCount > 0 ? `↓ ${pendingNewCount} new` : "Resume scrolling"}
                    </Button>
                </div>
            )}

            <AlertDialog open={clearDialogOpen} onOpenChange={setClearDialogOpen}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Clear system logs?</AlertDialogTitle>
                        <AlertDialogDescription>
                            This removes the current log output from the dashboard until new events arrive.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={clearLogs} className="bg-red-600 text-white hover:bg-red-700">
                            Clear logs
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}

