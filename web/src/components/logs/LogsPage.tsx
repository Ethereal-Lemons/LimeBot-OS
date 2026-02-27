import { useEffect, useState, useRef } from "react";
import axios from "axios";
import { Terminal, RefreshCw, Trash2, Search, ArrowDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

interface ParsedLog {
    raw: string;
    timestamp?: string;
    level?: 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL' | 'DEBUG' | string;
    context?: string;
    message: string;
}


const parseLogLine = (line: string): ParsedLog => {

    const loguruMatch = line.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+\|\s+([A-Z]+)\s+\|\s+([^:]+:[^:]+:\d+)\s+-\s+(.*)$/);
    if (loguruMatch) {
        return {
            raw: line,
            timestamp: loguruMatch[1],
            level: loguruMatch[2].trim(),
            context: loguruMatch[3],
            message: loguruMatch[4]
        };
    }


    const uvicornMatch = line.match(/^(INFO|WARNING|ERROR|CRITICAL):\s+(.*)$/);
    if (uvicornMatch) {
        return {
            raw: line,
            level: uvicornMatch[1],
            message: uvicornMatch[2]
        };
    }


    let levelMatch = null;
    ['INFO', 'WARNING', 'ERROR', 'CRITICAL', 'DEBUG'].forEach(lvl => {
        if (line.includes(lvl)) levelMatch = lvl;
    });

    return {
        raw: line,
        level: levelMatch || undefined,
        message: line
    };
};

export function LogsPage() {
    const [logs, setLogs] = useState<ParsedLog[]>([]);
    const [loading, setLoading] = useState(true);
    const [autoScroll, setAutoScroll] = useState(true);
    const [filter, setFilter] = useState("");

    // Auto-scroll ref
    const bottomRef = useRef<HTMLDivElement>(null);
    const scrollContainerRef = useRef<HTMLDivElement>(null);

    const fetchLogs = () => {
        axios.get('http://localhost:8000/api/logs?lines=200')
            .then(res => {
                if (res.data.logs) {
                    const parsed = (res.data.logs as string[]).map(parseLogLine);
                    setLogs(parsed);
                }
                setLoading(false);
            })
            .catch(err => {
                if (err.response?.status !== 401) {
                    console.error("Failed to fetch logs:", err);
                }
                setLoading(false);
            });
    };

    useEffect(() => {
        fetchLogs();
        const interval = setInterval(fetchLogs, 2000); // Poll every 2s
        return () => clearInterval(interval);
    }, []);


    useEffect(() => {
        if (autoScroll && bottomRef.current) {
            bottomRef.current.scrollIntoView({ behavior: "smooth" });
        }
    }, [logs, autoScroll]);


    const handleScroll = () => {
        if (!scrollContainerRef.current) return;

        const { scrollTop, scrollHeight, clientHeight } = scrollContainerRef.current;
        const isNearBottom = scrollHeight - scrollTop - clientHeight < 50;

        if (!isNearBottom && autoScroll) {
            setAutoScroll(false);
        } else if (isNearBottom && !autoScroll) {
            setAutoScroll(true);
        }
    };

    const filteredLogs = logs.filter(log =>
        log.raw.toLowerCase().includes(filter.toLowerCase())
    );

    const getLevelBadgeStyles = (level?: string) => {
        switch (level) {
            case 'INFO': return "bg-blue-500/10 text-blue-500 border-blue-500/20";
            case 'WARNING': return "bg-yellow-500/10 text-yellow-500 border-yellow-500/20";
            case 'ERROR':
            case 'CRITICAL': return "bg-red-500/10 text-red-500 border-red-500/20";
            case 'DEBUG': return "bg-zinc-500/10 text-zinc-400 border-zinc-500/20";
            default: return "bg-white/5 text-muted-foreground border-white/10";
        }
    };

    return (
        <div className="flex flex-col h-full bg-background rounded-2xl overflow-hidden shadow-2xl relative">
            {/* Toolbar */}
            <div className="flex items-center justify-between p-3 border-b border-border bg-card/80 backdrop-blur-md z-10 shrink-0">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-primary/10 rounded-lg">
                        <Terminal className="w-5 h-5 text-primary" />
                    </div>
                    <div>
                        <h2 className="font-bold tracking-tight text-foreground leading-none">System Logs</h2>
                        <span className="text-xs text-muted-foreground flex items-center gap-1 mt-1">
                            Live server stream {loading && <RefreshCw className="w-3 h-3 animate-spin" />}
                        </span>
                    </div>
                </div>

                <div className="flex items-center gap-2 pr-2">
                    <div className="relative">
                        <Search className="absolute left-2.5 top-2 w-4 h-4 text-muted-foreground" />
                        <input
                            type="text"
                            placeholder="Filter logs..."
                            value={filter}
                            onChange={(e) => setFilter(e.target.value)}
                            className="bg-background border border-border rounded-lg py-1.5 pl-9 pr-3 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary w-40 md:w-64 transition-all"
                        />
                    </div>
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => {
                            if (!confirm("Are you sure you want to clear the system logs?")) return;
                            axios.post('http://localhost:8000/api/control/clear-logs')
                                .then(() => setLogs([]))
                                .catch(err => console.error("Failed to clear logs:", err));
                        }}
                        className="text-muted-foreground hover:text-red-400 hover:bg-red-500/10"
                        title="Clear logs"
                    >
                        <Trash2 className="w-4 h-4" />
                    </Button>
                </div>
            </div>

            {/* Logs Area */}
            <div
                ref={scrollContainerRef}
                onScroll={handleScroll}
                className="flex-1 overflow-y-auto p-4 md:p-6 bg-[#0c0c0c] font-mono text-xs md:text-sm selection:bg-primary/30"
            >
                {filteredLogs.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-muted-foreground/50 opacity-80">
                        <Terminal className="w-12 h-12 mb-3 opacity-20" />
                        <p className="font-medium tracking-wide">No system logs available.</p>
                        <p className="text-xs max-w-[250px] text-center mt-2 opacity-60">
                            Waiting for active server events or incoming connections...
                        </p>
                    </div>
                ) : (
                    <div className="space-y-1.5">
                        {filteredLogs.map((log, i) => (
                            <div key={i} className="group flex flex-col md:flex-row md:items-start gap-1 md:gap-3 hover:bg-white/[0.03] p-1.5 rounded-md transition-colors border border-transparent hover:border-white/5">
                                <div className="flex items-center gap-2 md:gap-3 shrink-0">
                                    <span className="text-white/20 select-none w-6 text-right text-[10px] hidden md:inline-block pt-0.5">
                                        {i + 1}
                                    </span>
                                    {log.timestamp && (
                                        <span className="text-white/40 text-[11px] md:text-xs pt-0.5 whitespace-nowrap">
                                            {log.timestamp.split(' ')[1] || log.timestamp}
                                        </span>
                                    )}
                                    <Badge variant="outline" className={cn("text-[9px] font-bold px-1.5 py-0 h-4 border leading-none rounded uppercase w-auto md:w-16 justify-center shrink-0", getLevelBadgeStyles(log.level))}>
                                        {log.level || 'LOG'}
                                    </Badge>
                                </div>
                                <div className="flex flex-col flex-1 min-w-0">
                                    {log.context && (
                                        <span className="text-white/30 text-[10px] font-medium leading-none mb-1 hidden md:block">
                                            [{log.context}]
                                        </span>
                                    )}
                                    <span className="text-white/80 break-words whitespace-pre-wrap leading-relaxed">
                                        {log.message}
                                    </span>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
                <div ref={bottomRef} className="h-4" />
            </div>

            {/* Floating Auto-Scroll Resume Button */}
            {!autoScroll && logs.length > 0 && (
                <div className="absolute bottom-6 right-8 animate-in slide-in-from-bottom-4 fade-in duration-200">
                    <Button
                        size="sm"
                        onClick={() => {
                            setAutoScroll(true);
                            bottomRef.current?.scrollIntoView({ behavior: "smooth" });
                        }}
                        className="shadow-xl rounded-full bg-card/90 backdrop-blur border border-border text-foreground hover:bg-muted font-medium"
                    >
                        <ArrowDown className="w-4 h-4 mr-2" />
                        Resume scrolling
                    </Button>
                </div>
            )}
        </div>
    );
}
