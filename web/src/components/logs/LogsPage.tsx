import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { getApiErrorStatus } from "@/lib/api-error";
import { ArrowDown, ChevronDown, ChevronRight, RefreshCw, ScrollText, Search, SearchX, Trash2, X } from "lucide-react";
import { cn } from "@/lib/utils";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CopyButton } from "@/components/chat/tool-ui";
import {
    EmptyState,
    IconButton,
    PageFrame,
    QuietButton,
    RequestError,
    Segmented,
    SkeletonRows,
    StatusDot,
} from "@/components/ops/ops-ui";
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
const LINE_COUNT_OPTIONS = [100, 200, 500] as const;

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

type LevelGroup = "error" | "warning" | "info" | "debug";

const LEVEL_GROUPS: Array<{ key: LevelGroup; label: string; active: string; text: string }> = [
    { key: "error", label: "Errors", active: "border-red-500/40 bg-red-500/10 text-red-500", text: "text-red-500" },
    { key: "warning", label: "Warnings", active: "border-amber-500/40 bg-amber-500/10 text-amber-500", text: "text-amber-500" },
    { key: "info", label: "Info", active: "border-primary/40 bg-primary/10 text-primary", text: "text-muted-foreground" },
    { key: "debug", label: "Debug", active: "border-border bg-foreground/10 text-foreground", text: "text-muted-foreground/60" },
];

function levelGroup(level?: string): LevelGroup | null {
    switch (level) {
        case "ERROR":
        case "CRITICAL":
            return "error";
        case "WARNING":
            return "warning";
        case "INFO":
            return "info";
        case "DEBUG":
            return "debug";
        default:
            return null;
    }
}

function levelShort(level?: string) {
    if (!level) return "LOG";
    if (level === "WARNING") return "WARN";
    if (level === "CRITICAL") return "CRIT";
    return level;
}

function formatClock(value?: string) {
    if (!value) return "";
    return value.split(" ")[1]?.split(".")[0] || value;
}


export function LogsPage() {
    const [logs, setLogs] = useState<ParsedLog[]>([]);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState<unknown>(null);
    const [autoScroll, setAutoScroll] = useState(true);
    const [live, setLive] = useState(true);
    const [compact, setCompact] = useState(false);
    const [filter, setFilter] = useState("");
    const [selectedLevels, setSelectedLevels] = useState<LevelGroup[]>([]);
    const [selectedContext, setSelectedContext] = useState<string>("all");
    const [lineCount, setLineCount] = useState<number>(200);
    const [clearDialogOpen, setClearDialogOpen] = useState(false);
    const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
    const [lastRefreshAt, setLastRefreshAt] = useState<Date | null>(null);
    const [pendingNewCount, setPendingNewCount] = useState(0);

    const scrollContainerRef = useRef<HTMLDivElement>(null);
    const autoScrollRef = useRef(autoScroll);
    const programmaticScroll = useRef(false);

    useEffect(() => {
        autoScrollRef.current = autoScroll;
    }, [autoScroll]);

    const fetchLogs = useCallback(
        async (options?: { silent?: boolean }) => {
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
                setError(null);
            } catch (err: unknown) {
                if (getApiErrorStatus(err) !== 401) {
                    console.error("Failed to fetch logs:", err);
                }
                setError(err);
            } finally {
                setLoading(false);
                if (!silent) setRefreshing(false);
            }
        },
        [lineCount]
    );

    useEffect(() => {
        void fetchLogs();
    }, [fetchLogs]);

    useEffect(() => {
        if (!live) return;
        const interval = window.setInterval(() => {
            void fetchLogs({ silent: true });
        }, 2000);
        return () => window.clearInterval(interval);
    }, [live, fetchLogs]);

    const scrollToLatest = useCallback((smooth: boolean) => {
        const container = scrollContainerRef.current;
        if (!container) return;
        programmaticScroll.current = true;
        container.scrollTo({ top: container.scrollHeight, behavior: smooth ? "smooth" : "auto" });
        window.setTimeout(() => {
            programmaticScroll.current = false;
        }, smooth ? 400 : 50);
    }, []);

    useEffect(() => {
        if (autoScroll) {
            scrollToLatest(true);
            setPendingNewCount(0);
        }
    }, [logs, autoScroll, scrollToLatest]);

    const handleScroll = () => {
        const container = scrollContainerRef.current;
        if (!container || programmaticScroll.current) return;
        const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 50;

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
            .map(([context, count]) => ({ context, count }));
    }, [logs]);

    useEffect(() => {
        if (selectedContext !== "all" && !contexts.some((item) => item.context === selectedContext)) {
            setSelectedContext("all");
        }
    }, [contexts, selectedContext]);

    const filteredLogs = useMemo(() => {
        const needle = filter.toLowerCase();
        return logs.filter((log) => {
            if (needle && !log.raw.toLowerCase().includes(needle)) return false;
            if (selectedContext !== "all" && log.context !== selectedContext) return false;
            if (selectedLevels.length > 0) {
                const group = levelGroup(log.level);
                if (!group || !selectedLevels.includes(group)) return false;
            }
            return true;
        });
    }, [logs, filter, selectedContext, selectedLevels]);

    const counts = useMemo(() => {
        const byGroup: Record<LevelGroup, number> = { error: 0, warning: 0, info: 0, debug: 0 };
        for (const log of logs) {
            const group = levelGroup(log.level);
            if (group) byGroup[group] += 1;
        }
        return byGroup;
    }, [logs]);

    const toggleLevel = (level: LevelGroup) => {
        setSelectedLevels((current) => (current.includes(level) ? current.filter((item) => item !== level) : [...current, level]));
    };

    const clearFilters = () => {
        setFilter("");
        setSelectedLevels([]);
        setSelectedContext("all");
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

    const hasActiveFilters = Boolean(filter) || selectedLevels.length > 0 || selectedContext !== "all";

    const actions = (
        <>
            <IconButton label="Refresh logs" onClick={() => void fetchLogs()} disabled={refreshing}>
                <RefreshCw className={cn(refreshing && "animate-spin")} />
            </IconButton>
            <IconButton label="Clear logs" onClick={() => setClearDialogOpen(true)} className="hover:bg-red-500/10 hover:text-red-500">
                <Trash2 />
            </IconButton>
        </>
    );

    const description = loading
        ? "Loading…"
        : error && logs.length === 0
            ? "Unavailable"
            : `${filteredLogs.length === logs.length ? logs.length : `${filteredLogs.length} of ${logs.length}`} lines${lastRefreshAt ? ` · updated ${lastRefreshAt.toLocaleTimeString()}` : ""}`;

    return (
        <PageFrame title="Logs" description={description} actions={actions} scroll={false} maxWidth="max-w-none">
            {/* ── Toolbar ── */}
            <div className="shrink-0 space-y-2 border-b border-border/60 px-4 py-2.5 sm:px-6">
                <div className="flex flex-wrap items-center gap-2">
                    <div className="relative min-w-[12rem] flex-1">
                        <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
                        <input
                            type="text"
                            aria-label="Search logs"
                            placeholder="Search messages, stack traces, sources"
                            value={filter}
                            onChange={(event) => setFilter(event.target.value)}
                            className="h-7 w-full rounded-md border border-border bg-transparent pl-8 pr-7 text-xs text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:ring-2 focus:ring-ring"
                        />
                        {filter && (
                            <button
                                type="button"
                                onClick={() => setFilter("")}
                                aria-label="Clear search"
                                className="absolute right-1.5 top-1.5 rounded text-muted-foreground hover:text-foreground"
                            >
                                <X className="h-3.5 w-3.5" />
                            </button>
                        )}
                    </div>

                    <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Filter by level">
                        {LEVEL_GROUPS.map((group) => {
                            const selected = selectedLevels.includes(group.key);
                            return (
                                <button
                                    key={group.key}
                                    type="button"
                                    aria-pressed={selected}
                                    onClick={() => toggleLevel(group.key)}
                                    className={cn(
                                        "inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                                        selected ? group.active : "border-border text-muted-foreground hover:bg-foreground/[0.07] hover:text-foreground"
                                    )}
                                >
                                    {group.label}
                                    <span className={cn("tabular-nums", !selected && counts[group.key] > 0 && group.key === "error" && "text-red-500", !selected && counts[group.key] > 0 && group.key === "warning" && "text-amber-500")}>
                                        {counts[group.key]}
                                    </span>
                                </button>
                            );
                        })}
                    </div>
                </div>

                <div className="-mx-4 flex items-center gap-x-4 gap-y-2 overflow-x-auto px-4 text-xs text-muted-foreground sm:mx-0 sm:flex-wrap sm:overflow-visible sm:px-0 [&>*]:shrink-0">
                    <label className="flex cursor-pointer select-none items-center gap-1.5">
                        <Switch checked={live} onCheckedChange={setLive} aria-label="Live" className="h-4 w-7 [&>span]:h-3 [&>span]:w-3 data-[state=checked]:[&>span]:translate-x-3" />
                        <StatusDot tone={live && !error ? "ready" : "neutral"} pulse={live && !error} />
                        <span className={live ? "text-foreground" : ""}>Live</span>
                    </label>
                    <label className="flex cursor-pointer select-none items-center gap-1.5">
                        <Switch checked={autoScroll} onCheckedChange={(value) => { setAutoScroll(value); if (value) scrollToLatest(true); }} aria-label="Follow latest" className="h-4 w-7 [&>span]:h-3 [&>span]:w-3 data-[state=checked]:[&>span]:translate-x-3" />
                        <span className={autoScroll ? "text-foreground" : ""}>Follow</span>
                    </label>
                    <label className="flex cursor-pointer select-none items-center gap-1.5">
                        <Switch checked={compact} onCheckedChange={setCompact} aria-label="Compact rows" className="h-4 w-7 [&>span]:h-3 [&>span]:w-3 data-[state=checked]:[&>span]:translate-x-3" />
                        <span className={compact ? "text-foreground" : ""}>Compact</span>
                    </label>

                    <Segmented
                        label="Lines to load"
                        value={String(lineCount)}
                        onChange={(value) => setLineCount(Number(value))}
                        options={LINE_COUNT_OPTIONS.map((count) => ({ value: String(count), label: count }))}
                        className="[&_button]:py-0.5"
                    />

                    {contexts.length > 1 && (
                        <Select value={selectedContext} onValueChange={setSelectedContext}>
                            <SelectTrigger aria-label="Filter by source" className="h-7 w-48 gap-2 px-2 text-xs sm:w-60">
                                <SelectValue placeholder="All sources" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All sources</SelectItem>
                                {contexts.slice(0, 40).map(({ context, count }) => (
                                    <SelectItem key={context} value={context} className="font-mono text-xs">
                                        {context} · {count}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    )}

                    {hasActiveFilters && (
                        <button type="button" onClick={clearFilters} className="font-medium text-primary hover:underline">
                            Clear filters
                        </button>
                    )}
                </div>
            </div>

            {error && logs.length > 0 ? (
                <div className="shrink-0 px-4 pt-3 sm:px-6">
                    <RequestError error={error} what="the logs" onRetry={() => void fetchLogs()} retrying={refreshing} stale />
                </div>
            ) : null}

            {/* ── Stream ── */}
            <div className="relative min-h-0 flex-1">
                <div ref={scrollContainerRef} onScroll={handleScroll} className="h-full overflow-y-auto font-mono text-xs selection:bg-primary/30">
                    {loading ? (
                        <SkeletonRows rows={8} />
                    ) : error && logs.length === 0 ? (
                        <div className="mx-auto max-w-xl px-4 py-8">
                            <RequestError error={error} what="the logs" onRetry={() => void fetchLogs()} retrying={refreshing} />
                        </div>
                    ) : logs.length === 0 ? (
                        <EmptyState icon={ScrollText} title="No logs yet" hint="Output from the backend appears here as it runs. After clearing, new events show up on the next refresh." className="font-sans" />
                    ) : filteredLogs.length === 0 ? (
                        <EmptyState
                            icon={SearchX}
                            title="No lines match these filters"
                            hint={`${logs.length} lines are loaded; none match the current search, level or source.`}
                            action={<QuietButton onClick={clearFilters}>Clear filters</QuietButton>}
                            className="font-sans"
                        />
                    ) : (
                        <>
                            <div className="sticky top-0 z-10 hidden items-center gap-3 border-b border-border/60 bg-background/95 px-4 py-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80 backdrop-blur md:flex sm:px-6">
                                <span className="w-4 shrink-0" />
                                <span className="w-[4.5rem] shrink-0">Time</span>
                                <span className="w-9 shrink-0">Level</span>
                                <span className="w-44 shrink-0">Source</span>
                                <span className="min-w-0 flex-1">Message</span>
                            </div>
                            <ul>
                                {filteredLogs.map((log) => {
                                    const expanded = expandedIds.has(log.id);
                                    const hasDetails = log.continuationCount > 0 || log.message.length > 160;
                                    const group = levelGroup(log.level);
                                    const firstLine = log.message.split("\n")[0];
                                    return (
                                        <li
                                            key={log.id}
                                            className={cn(
                                                "border-b border-border/30 border-l-2",
                                                group === "error" ? "border-l-red-500/70 bg-red-500/[0.05]" : group === "warning" ? "border-l-amber-500/60 bg-amber-500/[0.04]" : "border-l-transparent"
                                            )}
                                        >
                                            <button
                                                type="button"
                                                onClick={() => hasDetails && toggleExpanded(log.id)}
                                                aria-expanded={hasDetails ? expanded : undefined}
                                                className={cn(
                                                    "group flex w-full items-baseline gap-2 px-3 text-left sm:px-5 md:gap-3",
                                                    compact ? "py-0.5" : "py-1.5",
                                                    hasDetails ? "cursor-pointer hover:bg-foreground/[0.05]" : "cursor-default"
                                                )}
                                            >
                                                <span className="flex w-4 shrink-0 justify-center text-muted-foreground/60 group-hover:text-muted-foreground">
                                                    {hasDetails ? expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" /> : null}
                                                </span>
                                                <span className="hidden w-[4.5rem] shrink-0 whitespace-nowrap text-[11px] text-muted-foreground md:inline">{formatClock(log.timestamp)}</span>
                                                <span
                                                    className={cn(
                                                        "w-9 shrink-0 text-[10px] font-bold uppercase",
                                                        LEVEL_GROUPS.find((item) => item.key === group)?.text ?? "text-muted-foreground"
                                                    )}
                                                >
                                                    {levelShort(log.level)}
                                                </span>
                                                <span className="hidden w-44 shrink-0 truncate text-[11px] text-muted-foreground md:inline" title={log.context}>
                                                    {log.context}
                                                </span>
                                                <span
                                                    className={cn(
                                                        "min-w-0 flex-1 break-words leading-relaxed",
                                                        group === "debug" ? "text-muted-foreground" : "text-foreground",
                                                        !expanded && (compact || log.continuationCount > 0) && "truncate"
                                                    )}
                                                >
                                                    {firstLine}
                                                    {!expanded && log.continuationCount > 0 && (
                                                        <span className="ml-2 text-[10px] text-muted-foreground">+{log.continuationCount} lines</span>
                                                    )}
                                                </span>
                                            </button>
                                            {expanded && (
                                                <div className="space-y-1 px-3 pb-2.5 pl-[2.1rem] sm:pl-[2.6rem] md:pl-[3.4rem]">
                                                    <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border/60 bg-card/60 p-2.5 text-[11px] leading-relaxed text-muted-foreground">
                                                        {log.message}
                                                    </pre>
                                                    <div className="flex items-center justify-between font-sans text-[10px] text-muted-foreground">
                                                        <span className="truncate">{log.timestamp} {log.context}</span>
                                                        <CopyButton text={log.raw} label="Copy entry" />
                                                    </div>
                                                </div>
                                            )}
                                        </li>
                                    );
                                })}
                            </ul>
                        </>
                    )}
                </div>

                {!autoScroll && filteredLogs.length > 0 && (
                    <div className="pointer-events-none absolute inset-x-0 bottom-4 flex justify-center animate-in fade-in slide-in-from-bottom-2 duration-200">
                        <button
                            type="button"
                            onClick={() => {
                                setAutoScroll(true);
                                setPendingNewCount(0);
                                scrollToLatest(true);
                            }}
                            className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 font-sans text-xs font-medium text-foreground shadow-lg transition-colors hover:bg-foreground/[0.07]"
                        >
                            <ArrowDown className="h-3.5 w-3.5" />
                            {pendingNewCount > 0 ? `${pendingNewCount} new line${pendingNewCount === 1 ? "" : "s"}` : "Jump to latest"}
                        </button>
                    </div>
                )}
            </div>

            <AlertDialog open={clearDialogOpen} onOpenChange={setClearDialogOpen}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Clear system logs?</AlertDialogTitle>
                        <AlertDialogDescription>This removes the current log output from the dashboard until new events arrive.</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={clearLogs} className="bg-red-600 text-white hover:bg-red-700">
                            Clear logs
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </PageFrame>
    );
}
