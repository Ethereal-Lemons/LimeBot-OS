import { useEffect, useState, useRef } from "react";
import axios from "axios";
import { Terminal, RefreshCw, Trash2, Search, ArrowDown } from "lucide-react";
import { cn } from "@/lib/utils";

export function LogsPage() {
    const [logs, setLogs] = useState<string[]>([]);
    const [loading, setLoading] = useState(true);
    const [autoScroll, setAutoScroll] = useState(true);
    const [filter, setFilter] = useState("");
    const bottomRef = useRef<HTMLDivElement>(null);

    const fetchLogs = () => {
        axios.get('http://localhost:8000/api/logs?lines=200')
            .then(res => {
                if (res.data.logs) {
                    setLogs(res.data.logs);
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

    const filteredLogs = logs.filter(log =>
        log.toLowerCase().includes(filter.toLowerCase())
    );

    return (
        <div className="flex flex-col h-full bg-[#0c0c0c] text-green-500 font-mono text-xs md:text-sm">
            {/* Toolbar */}
            <div className="flex items-center justify-between p-2 border-b border-white/10 bg-black/50">
                <div className="flex items-center gap-2">
                    <Terminal className="w-4 h-4 text-green-600" />
                    <span className="font-bold tracking-tight">System Logs</span>
                    {loading && <RefreshCw className="w-3 h-3 animate-spin text-muted-foreground" />}
                </div>
                <div className="flex items-center gap-2">
                    <div className="relative">
                        <Search className="absolute left-2 top-1.5 w-3 h-3 text-muted-foreground" />
                        <input
                            type="text"
                            placeholder="Filter logs..."
                            value={filter}
                            onChange={(e) => setFilter(e.target.value)}
                            className="bg-white/5 border border-white/10 rounded-md py-1 pl-7 pr-2 text-xs text-foreground focus:outline-none focus:border-green-500/50 w-32 md:w-64"
                        />
                    </div>
                    <button
                        onClick={() => setAutoScroll(!autoScroll)}
                        className={cn(
                            "p-1.5 rounded-md transition-colors",
                            autoScroll ? "bg-green-500/20 text-green-400" : "text-muted-foreground hover:bg-white/5"
                        )}
                        title="Auto-scroll"
                    >
                        <ArrowDown className="w-4 h-4" />
                    </button>
                    <button
                        onClick={() => {
                            if (!confirm("Are you sure you want to clear the system logs?")) return;
                            axios.post('http://localhost:8000/api/control/clear-logs')
                                .then(() => setLogs([]))
                                .catch(err => console.error("Failed to clear logs:", err));
                        }}
                        className="p-1.5 text-muted-foreground hover:text-red-400 hover:bg-red-500/10 rounded-md transition-colors"
                        title="Clear logs"
                    >
                        <Trash2 className="w-4 h-4" />
                    </button>
                </div>
            </div>

            {/* Logs Area */}
            <div className="flex-1 overflow-y-auto p-4 space-y-1 scrollbar-thin scrollbar-thumb-white/10 scrollbar-track-transparent">
                {filteredLogs.length === 0 ? (
                    <div className="text-muted-foreground italic opacity-50">No logs found.</div>
                ) : (
                    filteredLogs.map((log, i) => (
                        <div key={i} className="break-words whitespace-pre-wrap hover:bg-white/5 px-1 rounded-sm">
                            <span className="opacity-50 select-none mr-2">{i + 1}</span>
                            {/* Basic highlighting for log levels */}
                            {log.includes("INFO") ? (
                                <span className="text-blue-400">INFO</span>
                            ) : log.includes("WARNING") ? (
                                <span className="text-yellow-400">WARN</span>
                            ) : log.includes("ERROR") ? (
                                <span className="text-red-400">ERR </span>
                            ) : null}
                            {' '}
                            {log.replace(/INFO|WARNING|ERROR|CRITICAL/g, '')}
                        </div>
                    ))
                )}
                <div ref={bottomRef} />
            </div>
        </div>
    );
}
