import { useEffect, useState, useCallback } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Trash2, Plus, RefreshCw, Clock, Calendar, MessageSquare, Repeat } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader,
    AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";

interface Job {
    id: string;
    trigger: number;
    payload: string;
    cron_expr?: string;
    tz_offset?: number;
    context: {
        channel: string;
        chat_id: string;
        sender_id?: string;
    };
    created_at: number;
}

interface AlertState {
    open: boolean;
    title: string;
    description: string;
}

// FIX: pure helper — formats seconds into "2d 4h 3m 12s", no React state needed
function formatCountdown(seconds: number): string {
    if (seconds < 0) return "Executing…";
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return [d && `${d}d`, h && `${h}h`, m && `${m}m`, `${s}s`].filter(Boolean).join(" ");
}

function formatTimestamp(ts: number): string {
    return new Date(ts * 1000).toLocaleString();
}

// FIX: formats tz_offset (minutes) as "UTC+5:30" correctly, including half-hour zones
function formatTzOffset(offsetMinutes: number): string {
    const sign = offsetMinutes >= 0 ? "+" : "-";
    const abs = Math.abs(offsetMinutes);
    const hours = Math.floor(abs / 60);
    const mins = abs % 60;
    return `UTC${sign}${hours}${mins ? `:${String(mins).padStart(2, "0")}` : ""}`;
}

export function CronPage() {
    const [jobs, setJobs] = useState<Job[]>([]);
    const [loading, setLoading] = useState(false);
    // FIX: tick every second so countdowns update live between polling intervals
    const [now, setNow] = useState(() => Date.now() / 1000);

    const [mode, setMode] = useState<"relative" | "cron">("relative");
    const [timeExpr, setTimeExpr] = useState("");
    const [cronExpr, setCronExpr] = useState("");
    const [message, setMessage] = useState("");
    const [channel, setChannel] = useState("web");
    const [chatId, setChatId] = useState("dashboard");
    const [tzOffset, setTzOffset] = useState<string>(String(-new Date().getTimezoneOffset()));
    const [adding, setAdding] = useState(false);

    const [alertDialog, setAlertDialog] = useState<AlertState>({
        open: false, title: "", description: "",
    });

    const showAlert = (title: string, description: string) =>
        setAlertDialog({ open: true, title, description });

    // FIX: wrapped in useCallback so it can be referenced as a stable dep in useEffect
    const fetchJobs = useCallback(() => {
        setLoading(true);
        axios.get(`${API_BASE_URL}/api/cron/jobs`)
            .then(res => {
                // FIX: guard against non-array responses
                setJobs(Array.isArray(res.data) ? res.data : []);
            })
            .catch(err => {
                console.error("Failed to fetch jobs:", err);
            })
            .finally(() => setLoading(false));
    }, []);

    useEffect(() => {
        fetchJobs();
        const pollInterval = setInterval(fetchJobs, 5000);
        // FIX: separate tick timer for live countdown display
        const tickInterval = setInterval(() => setNow(Date.now() / 1000), 1000);
        return () => {
            clearInterval(pollInterval);
            clearInterval(tickInterval);
        };
    }, [fetchJobs]);

    const handleAddJob = (e: React.FormEvent) => {
        e.preventDefault();

        // FIX: client-side guard — the required attribute handles empty strings
        // but this prevents submitting the wrong tab's empty field
        if (mode === "relative" && !timeExpr.trim()) {
            showAlert("Validation Error", "Please enter a time delay (e.g. 5m, 1h).");
            return;
        }
        if (mode === "cron" && !cronExpr.trim()) {
            showAlert("Validation Error", "Please enter a cron expression (e.g. 0 9 * * 1-5).");
            return;
        }

        setAdding(true);
        const payload: Record<string, unknown> = {
            message,
            context: { channel, chat_id: chatId },
            ...(mode === "relative"
                ? { time_expr: timeExpr }
                : { cron_expr: cronExpr, tz_offset: parseInt(tzOffset) }),
        };

        axios.post(`${API_BASE_URL}/api/cron/jobs`, payload)
            .then(() => {
                setTimeExpr("");
                setCronExpr("");
                setMessage("");
                fetchJobs();
            })
            .catch(err => {
                showAlert("Error", "Failed to add job: " + (err.response?.data?.detail || err.message));
            })
            .finally(() => setAdding(false));
    };

    const handleDelete = (id: string) => {
        axios.delete(`${API_BASE_URL}/api/cron/jobs/${id}`)
            .then(() => fetchJobs())
            // FIX: surface delete errors to the user instead of only console.error
            .catch(err => {
                showAlert("Delete Failed", "Could not delete job: " + (err.response?.data?.detail || err.message));
            });
    };

    const browserTzOffset = -new Date().getTimezoneOffset();
    const browserTzLabel = formatTzOffset(browserTzOffset);

    return (
        <div className="p-6 h-full flex flex-col gap-6 overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight">Cron Jobs</h1>
                    <p className="text-muted-foreground">Manage scheduled tasks and reminders.</p>
                </div>
                <Button variant="outline" size="icon" onClick={fetchJobs} disabled={loading}>
                    <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                </Button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 h-full min-h-0">
                {/* ── Create Job ── */}
                <div className="md:col-span-1">
                    <Card>
                        <CardHeader>
                            <CardTitle>Schedule Task</CardTitle>
                            <CardDescription>Add a reminder or repeating job.</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <form onSubmit={handleAddJob} className="flex flex-col gap-4">
                                <Tabs value={mode} onValueChange={(v) => setMode(v as "relative" | "cron")} className="w-full">
                                    <TabsList className="grid w-full grid-cols-2">
                                        <TabsTrigger value="relative">One-time</TabsTrigger>
                                        <TabsTrigger value="cron">Repeating</TabsTrigger>
                                    </TabsList>

                                    <TabsContent value="relative" className="space-y-4 pt-4">
                                        <div className="space-y-2">
                                            <Label htmlFor="time">Time Delay</Label>
                                            <Input
                                                id="time"
                                                placeholder="e.g. 10s, 5m, 2h"
                                                value={timeExpr}
                                                onChange={e => setTimeExpr(e.target.value)}
                                            />
                                            <p className="text-xs text-muted-foreground">
                                                10s = 10 seconds · 5m = 5 minutes · 2h = 2 hours
                                            </p>
                                        </div>
                                    </TabsContent>

                                    <TabsContent value="cron" className="space-y-4 pt-4">
                                        <div className="space-y-2">
                                            <Label htmlFor="cron">Cron Expression</Label>
                                            <Input
                                                id="cron"
                                                placeholder="e.g. 0 9 * * 1-5"
                                                value={cronExpr}
                                                onChange={e => setCronExpr(e.target.value)}
                                            />
                                            <p className="text-xs text-muted-foreground">
                                                Standard cron: min hour dom month dow
                                            </p>
                                        </div>

                                        <div className="space-y-2">
                                            <Label>Timezone</Label>
                                            <Select value={tzOffset} onValueChange={setTzOffset}>
                                                <SelectTrigger>
                                                    <SelectValue placeholder="Select timezone" />
                                                </SelectTrigger>
                                                <SelectContent className="max-h-[300px]">
                                                    {/* FIX: use formatTzOffset for consistent display, including half-hour zones */}
                                                    {Array.from({ length: 27 }, (_, i) => (i - 12) * 60).map(off => (
                                                        <SelectItem key={off} value={String(off)}>
                                                            {formatTzOffset(off)}
                                                        </SelectItem>
                                                    ))}
                                                    <SelectItem value={String(browserTzOffset)}>
                                                        Browser Local ({browserTzLabel})
                                                    </SelectItem>
                                                </SelectContent>
                                            </Select>
                                            <p className="text-[10px] text-muted-foreground">
                                                The bot will schedule relative to this timezone.
                                            </p>
                                        </div>
                                    </TabsContent>
                                </Tabs>

                                <div className="space-y-2">
                                    <Label htmlFor="message">Message</Label>
                                    <Input
                                        id="message"
                                        placeholder="Remind me to…"
                                        value={message}
                                        onChange={e => setMessage(e.target.value)}
                                        required
                                    />
                                </div>

                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <Label>Channel</Label>
                                        <Select value={channel} onValueChange={setChannel}>
                                            <SelectTrigger><SelectValue /></SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="web">Web</SelectItem>
                                                <SelectItem value="discord">Discord</SelectItem>
                                                <SelectItem value="whatsapp">WhatsApp</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="chatId">Chat ID</Label>
                                        <Input
                                            id="chatId"
                                            value={chatId}
                                            onChange={e => setChatId(e.target.value)}
                                            required
                                        />
                                    </div>
                                </div>

                                <Button type="submit" disabled={adding} className="w-full">
                                    {adding
                                        ? "Scheduling…"
                                        : <><Plus className="mr-2 h-4 w-4" /> Schedule Task</>
                                    }
                                </Button>
                            </form>
                        </CardContent>
                    </Card>
                </div>

                {/* ── Jobs List ── */}
                <div className="md:col-span-2 overflow-auto pr-2">
                    {jobs.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-64 border rounded-lg border-dashed text-muted-foreground">
                            <Clock className="h-10 w-10 mb-2 opacity-50" />
                            <p>No pending jobs found.</p>
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {jobs.map(job => (
                                <Card key={job.id} className="overflow-hidden border-l-4 border-l-primary">
                                    <div className="flex items-center p-4 gap-4">
                                        <div className="bg-primary/10 p-3 rounded-full text-primary shrink-0">
                                            {job.cron_expr
                                                ? <Repeat className="h-5 w-5" />
                                                : <Calendar className="h-5 w-5" />
                                            }
                                        </div>

                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2 mb-1 flex-wrap">
                                                <Badge variant="outline" className="font-mono text-[10px] py-0">
                                                    {job.id}
                                                </Badge>
                                                {/* FIX: uses `now` from the tick timer so this updates every second */}
                                                <span className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                                                    <Clock className="h-3 w-3" />
                                                    {formatCountdown(job.trigger - now)}
                                                </span>
                                                <Badge variant="secondary" className="text-[10px] py-0 capitalize flex items-center gap-1">
                                                    <MessageSquare className="h-3 w-3" />
                                                    {job.context.channel}
                                                </Badge>
                                                {job.cron_expr && (
                                                    // FIX: uses formatTzOffset so "UTC+5.5" can't appear
                                                    <Badge variant="outline" className="text-[10px] py-0 text-primary border-primary/20 bg-primary/5">
                                                        {job.cron_expr}
                                                        {job.tz_offset != null && ` (${formatTzOffset(job.tz_offset)})`}
                                                    </Badge>
                                                )}
                                            </div>
                                            <p className="font-medium truncate text-sm">{job.payload}</p>
                                            <p className="text-[10px] text-muted-foreground mt-1">
                                                Next: {formatTimestamp(job.trigger)} · Target: {job.context.chat_id}
                                            </p>
                                        </div>

                                        <div className="shrink-0">
                                            <AlertDialog>
                                                <AlertDialogTrigger asChild>
                                                    <Button
                                                        variant="ghost" size="icon"
                                                        className="h-8 w-8 text-destructive hover:text-destructive/90 hover:bg-destructive/10"
                                                    >
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                </AlertDialogTrigger>
                                                <AlertDialogContent>
                                                    <AlertDialogHeader>
                                                        <AlertDialogTitle>Delete Job?</AlertDialogTitle>
                                                        <AlertDialogDescription>
                                                            This will permanently remove the scheduled job. This action cannot be undone.
                                                        </AlertDialogDescription>
                                                    </AlertDialogHeader>
                                                    <AlertDialogFooter>
                                                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                                                        <AlertDialogAction
                                                            onClick={() => handleDelete(job.id)}
                                                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                                                        >
                                                            Delete
                                                        </AlertDialogAction>
                                                    </AlertDialogFooter>
                                                </AlertDialogContent>
                                            </AlertDialog>
                                        </div>
                                    </div>
                                </Card>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            {/* Global alert dialog for errors / validation */}
            <AlertDialog
                open={alertDialog.open}
                onOpenChange={open => setAlertDialog(prev => ({ ...prev, open }))}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{alertDialog.title}</AlertDialogTitle>
                        <AlertDialogDescription>{alertDialog.description}</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogAction>OK</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}