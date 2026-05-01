import { useEffect, useState, useRef } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { 
    Globe, 
    Trash2, 
    RefreshCw, 
    MonitorPlay, 
    ShieldAlert, 
    CheckCircle2,
    XCircle
} from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";

interface BrowserSession {
    session_id: string;
    mode: "isolated" | "shared" | "system" | "attach";
    last_active: number;
    is_live: boolean;
    metadata: {
        channel?: string;
        cdp_url?: string;
        headless?: boolean;
    };
}

export function BrowserSessionsPanel() {
    const [sessions, setSessions] = useState<BrowserSession[]>([]);
    const [loading, setLoading] = useState(true);
    const [autoRefresh, setAutoRefresh] = useState(true);
    const pollInterval = useRef<number | null>(null);

    const fetchData = async () => {
        try {
            const res = await axios.get(`${API_BASE_URL}/api/browser/sessions`);
            setSessions(res.data.sessions || []);
        } catch (error) {
            console.error("Failed to fetch browser sessions:", error);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchData();
        if (autoRefresh) {
            pollInterval.current = window.setInterval(fetchData, 3000);
        }
        return () => {
            if (pollInterval.current) clearInterval(pollInterval.current);
        };
    }, [autoRefresh]);

    const handleDelete = async (sessionId: string) => {
        if (!confirm(`Are you sure you want to forcibly remove session ${sessionId}? This will close the browser if it's currently running.`)) return;
        
        try {
            await axios.delete(`${API_BASE_URL}/api/browser/sessions/${sessionId}`);
            fetchData();
        } catch (error) {
            console.error("Failed to delete session:", error);
            alert("Failed to delete session. It might be protected.");
        }
    };

    const formatTime = (ts: number) => {
        return new Date(ts * 1000).toLocaleTimeString();
    };

    const getModeColor = (mode: string) => {
        switch (mode) {
            case "isolated": return "bg-blue-500/10 text-blue-500 border-blue-500/20";
            case "shared": return "bg-purple-500/10 text-purple-500 border-purple-500/20";
            case "system": return "bg-emerald-500/10 text-emerald-500 border-emerald-500/20";
            case "attach": return "bg-amber-500/10 text-amber-500 border-amber-500/20";
            default: return "bg-gray-500/10 text-gray-500 border-gray-500/20";
        }
    };

    return (
        <div className="h-full flex flex-col p-6 max-w-7xl mx-auto w-full gap-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Browser Sessions</h1>
                    <p className="text-muted-foreground mt-1">Manage active headless and attached browser profiles.</p>
                </div>
                <div className="flex items-center gap-4">
                    <div className="flex items-center gap-2">
                        <span className="text-sm text-muted-foreground">Auto-refresh</span>
                        <div 
                            className={`w-10 h-5 rounded-full p-1 cursor-pointer transition-colors ${autoRefresh ? 'bg-emerald-500' : 'bg-muted'}`}
                            onClick={() => setAutoRefresh(!autoRefresh)}
                        >
                            <div className={`w-3 h-3 bg-white rounded-full transition-transform ${autoRefresh ? 'translate-x-5' : 'translate-x-0'}`} />
                        </div>
                    </div>
                    <button 
                        onClick={fetchData} 
                        className="p-2 hover:bg-accent rounded-full transition-colors"
                        disabled={loading}
                    >
                        <RefreshCw className={`w-5 h-5 ${loading ? 'animate-spin' : ''}`} />
                    </button>
                </div>
            </div>

            <Card className="flex-1 flex flex-col border-border/50 shadow-sm overflow-hidden min-h-[500px]">
                <CardHeader className="bg-card pb-4 border-b border-border/50">
                    <CardTitle className="flex items-center gap-2">
                        <MonitorPlay className="w-5 h-5 text-indigo-500" />
                        Active Profiles ({sessions.length})
                    </CardTitle>
                </CardHeader>
                <CardContent className="flex-1 p-0 overflow-hidden">
                    <ScrollArea className="h-full w-full">
                        {sessions.length === 0 ? (
                            <div className="h-60 flex flex-col items-center justify-center text-muted-foreground gap-4">
                                <Globe className="w-12 h-12 opacity-20" />
                                <p>No active browser sessions.</p>
                            </div>
                        ) : (
                            <div className="p-4 space-y-4">
                                {sessions.map(session => (
                                    <div key={session.session_id} className="flex flex-col md:flex-row md:items-center justify-between p-4 rounded-xl border border-border bg-card/50 hover:bg-card/80 transition-all gap-4">
                                        <div className="flex items-start gap-4">
                                            <div className="mt-1">
                                                {session.is_live ? (
                                                    <div title="Connection Live">
                                                        <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                                                    </div>
                                                ) : (
                                                    <div title="Connection Lost / Stale">
                                                        <XCircle className="w-5 h-5 text-amber-500" />
                                                    </div>
                                                )}
                                            </div>
                                            <div>
                                                <div className="flex items-center gap-2 mb-1">
                                                    <span className="font-semibold">{session.session_id}</span>
                                                    <Badge variant="outline" className={getModeColor(session.mode)}>
                                                        {session.mode}
                                                    </Badge>
                                                    {session.metadata.headless === false && (
                                                        <Badge variant="outline" className="bg-blue-500/10 text-blue-500 border-blue-500/20">Visible</Badge>
                                                    )}
                                                </div>
                                                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
                                                    <span>Last seen: {formatTime(session.last_active)}</span>
                                                    {session.metadata.cdp_url && <span>CDP: {session.metadata.cdp_url}</span>}
                                                </div>
                                            </div>
                                        </div>
                                        
                                        <div className="flex items-center gap-2">
                                            {session.mode === "system" ? (
                                                <div className="flex items-center text-xs text-amber-500 px-3 py-1.5 bg-amber-500/10 rounded-md border border-amber-500/20" title="System profiles cannot be forcibly removed">
                                                    <ShieldAlert className="w-4 h-4 mr-1.5" />
                                                    Protected
                                                </div>
                                            ) : (
                                                <Button 
                                                    variant="ghost" 
                                                    size="sm" 
                                                    className="text-red-500 hover:text-red-600 hover:bg-red-500/10"
                                                    onClick={() => handleDelete(session.session_id)}
                                                >
                                                    <Trash2 className="w-4 h-4 mr-2" />
                                                    End Session
                                                </Button>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </ScrollArea>
                </CardContent>
            </Card>
        </div>
    );
}
