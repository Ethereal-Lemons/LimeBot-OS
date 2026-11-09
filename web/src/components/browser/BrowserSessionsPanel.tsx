import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { Globe, Lock, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { requestFailureMessage } from "@/lib/request-error";
import {
    Chip,
    EmptyState,
    InlineConfirm,
    PageFrame,
    PageHeader,
    RequestError,
    RowButton,
    RowList,
    SkeletonRows,
    WarnGlyph,
} from "@/components/ui/data-page";
import { StatusGlyph } from "@/components/chat/tool-ui";

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

const MODE_HINT: Record<BrowserSession["mode"], string> = {
    isolated: "Own throwaway profile",
    shared: "Shared profile",
    system: "Your system browser profile",
    attach: "Attached over CDP",
};

function lastSeen(ts: number): string {
    const seconds = Math.max(0, Date.now() / 1000 - ts);
    if (seconds < 10) return "just now";
    if (seconds < 60) return `${Math.floor(seconds)}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return new Date(ts * 1000).toLocaleDateString();
}

function SessionRow({ session, onEnd }: { session: BrowserSession; onEnd: (id: string) => Promise<void> }) {
    const protectedSession = session.mode === "system";
    return (
        <div className="flex flex-col gap-2 px-4 py-3.5 sm:flex-row sm:items-center sm:gap-4">
            <div className="flex min-w-0 flex-1 items-start gap-3">
                <span className="mt-0.5">
                    {session.is_live ? <StatusGlyph state="done" /> : <WarnGlyph label="Connection lost" />}
                </span>
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="min-w-0 truncate font-mono text-[13px] font-medium text-foreground">{session.session_id}</span>
                        <Chip title={MODE_HINT[session.mode]}>{session.mode}</Chip>
                        {session.metadata.headless === false && <Chip tone="primary">Visible window</Chip>}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
                        <span className={cn(!session.is_live && "text-amber-500")}>
                            {session.is_live ? "Live" : "Connection lost"} · seen {lastSeen(session.last_active)}
                        </span>
                        {session.metadata.channel && <span>via {session.metadata.channel}</span>}
                        {session.metadata.cdp_url && <span className="min-w-0 truncate font-mono text-[10px]">{session.metadata.cdp_url}</span>}
                    </div>
                </div>
            </div>
            <div className="flex shrink-0 items-center justify-end pl-8 sm:pl-0">
                {protectedSession ? (
                    <span className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground" title="System profiles cannot be removed from here">
                        <Lock className="h-3 w-3" /> Protected
                    </span>
                ) : (
                    <InlineConfirm
                        label="End session"
                        icon={Trash2}
                        prompt="Closes the browser."
                        confirmLabel="End it"
                        onConfirm={() => onEnd(session.session_id)}
                    />
                )}
            </div>
        </div>
    );
}

export function BrowserSessionsPanel() {
    const [sessions, setSessions] = useState<BrowserSession[]>([]);
    const [loading, setLoading] = useState(true);
    const [loaded, setLoaded] = useState(false);
    const [loadError, setLoadError] = useState<unknown>(null);
    const [autoRefresh, setAutoRefresh] = useState(true);

    const fetchData = useCallback(async () => {
        try {
            const res = await axios.get(`${API_BASE_URL}/api/browser/sessions`);
            setSessions(res.data.sessions || []);
            setLoaded(true);
            setLoadError(null);
        } catch (error) {
            console.error("Failed to fetch browser sessions:", error);
            setLoadError(error);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void fetchData();
        if (!autoRefresh) return;
        const id = window.setInterval(() => void fetchData(), 3000);
        return () => window.clearInterval(id);
    }, [autoRefresh, fetchData]);

    const handleEnd = async (sessionId: string) => {
        try {
            await axios.delete(`${API_BASE_URL}/api/browser/sessions/${sessionId}`);
            toast.success(`Ended ${sessionId}`);
            void fetchData();
        } catch (error) {
            console.error("Failed to delete session:", error);
            toast.error(requestFailureMessage("Could not end the session (it may be protected)", error));
        }
    };

    const liveCount = sessions.filter((session) => session.is_live).length;

    return (
        <PageFrame width="max-w-4xl">
            <PageHeader
                title="Browser sessions"
                description="Browser profiles LimeBot is driving or attached to."
                meta={
                    loaded && sessions.length > 0 ? (
                        <>
                            <Chip>{sessions.length}</Chip>
                            {liveCount < sessions.length && <Chip tone="amber">{sessions.length - liveCount} lost</Chip>}
                        </>
                    ) : undefined
                }
                actions={
                    <>
                        <label className="flex cursor-pointer items-center gap-2 pr-1 text-[11px] text-muted-foreground">
                            <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} aria-label="Auto-refresh every 3 seconds" className="h-4 w-7 [&>span]:h-3 [&>span]:w-3 [&>span]:data-[state=checked]:translate-x-3" />
                            <span className="hidden sm:inline">Auto-refresh</span>
                        </label>
                        <RowButton onClick={() => void fetchData()} disabled={loading} aria-label="Refresh sessions" title="Refresh">
                            <RefreshCw className={cn(loading && "animate-spin")} />
                        </RowButton>
                    </>
                }
            />

            {!loaded && loading && <SkeletonRows count={3} />}

            {!loaded && !loading && <RequestError error={loadError} subject="browser sessions" onRetry={() => void fetchData()} retrying={loading} />}

            {loaded && loadError !== null && <RequestError compact error={loadError} subject="browser sessions" onRetry={() => void fetchData()} retrying={loading} />}

            {loaded &&
                (sessions.length === 0 ? (
                    <EmptyState
                        icon={Globe}
                        title="No active browser sessions"
                        hint="A session appears here when LimeBot opens a page or you attach a browser. This list refreshes on its own."
                    />
                ) : (
                    <RowList>
                        {sessions.map((session) => (
                            <SessionRow key={session.session_id} session={session} onEnd={handleEnd} />
                        ))}
                    </RowList>
                ))}
        </PageFrame>
    );
}
