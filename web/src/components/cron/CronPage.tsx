import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { AlertTriangle, CalendarClock, Pause, Play, Plus, RefreshCw, Repeat, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { requestFailureMessage } from "@/lib/request-error";
import { describeCron, formatCountdown, formatTzOffset } from "@/lib/schedule-format";
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
} from "@/components/ui/data-page";
import { StatusGlyph } from "@/components/chat/tool-ui";

interface Job {
    id: string;
    trigger: number;
    payload: string;
    active?: boolean;
    cron_expr?: string;
    tz_offset?: number;
    context: {
        channel: string;
        chat_id: string;
        sender_id?: string;
    };
    created_at: number;
}

type Mode = "relative" | "cron";

const CHANNEL_LABEL: Record<string, string> = { web: "Web", discord: "Discord", whatsapp: "WhatsApp", telegram: "Telegram" };

const formatWhen = (ts: number) => new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

function Segmented({ value, onChange }: { value: Mode; onChange: (mode: Mode) => void }) {
    const options: Array<{ value: Mode; label: string }> = [
        { value: "relative", label: "One-time" },
        { value: "cron", label: "Repeating" },
    ];
    return (
        <div role="tablist" aria-label="Schedule type" className="inline-flex rounded-lg border border-border/70 bg-background/60 p-0.5">
            {options.map((option) => (
                <button
                    key={option.value}
                    type="button"
                    role="tab"
                    aria-selected={value === option.value}
                    onClick={() => onChange(option.value)}
                    className={cn(
                        "rounded-md px-3 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        value === option.value ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground"
                    )}
                >
                    {option.label}
                </button>
            ))}
        </div>
    );
}

function Field({ label, htmlFor, hint, children, className }: { label: string; htmlFor?: string; hint?: string; children: React.ReactNode; className?: string }) {
    return (
        <div className={cn("space-y-1.5", className)}>
            <Label htmlFor={htmlFor} className="text-xs font-medium text-muted-foreground">
                {label}
            </Label>
            {children}
            {hint && <p className="text-[11px] text-muted-foreground/80">{hint}</p>}
        </div>
    );
}

function PausedGlyph() {
    return (
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-500/15 text-amber-500" aria-label="Paused">
            <Pause className="h-3 w-3" />
        </span>
    );
}

function JobRow({
    job,
    now,
    busy,
    onToggle,
    onDelete,
}: {
    job: Job;
    now: number;
    busy: boolean;
    onToggle: (job: Job) => void;
    onDelete: (id: string) => Promise<void>;
}) {
    const paused = job.active === false;
    const remaining = job.trigger - now;
    return (
        <div className={cn("flex flex-col gap-2 px-4 py-3.5 sm:flex-row sm:items-center sm:gap-4", paused && "bg-muted/20")}>
            <div className="flex min-w-0 flex-1 items-start gap-3">
                <span className="mt-0.5">{paused ? <PausedGlyph /> : remaining < 0 ? <StatusGlyph state="running" /> : <StatusGlyph state="planned" />}</span>
                <div className="min-w-0 flex-1">
                    <p className={cn("line-clamp-2 break-words text-sm font-medium", paused ? "text-muted-foreground" : "text-foreground")}>{job.payload}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                        <span className="inline-flex items-center gap-1">
                            {job.cron_expr ? <Repeat className="h-3 w-3" /> : <CalendarClock className="h-3 w-3" />}
                            {job.cron_expr ? describeCron(job.cron_expr) : "One-time"}
                            {job.cron_expr && job.tz_offset != null && <span className="opacity-70">({formatTzOffset(job.tz_offset)})</span>}
                        </span>
                        <span>
                            {CHANNEL_LABEL[job.context.channel] || job.context.channel} · <span className="font-mono text-[10px] normal-case">{job.context.chat_id}</span>
                        </span>
                        <span className="hidden font-mono text-[10px] opacity-60 md:inline" title="Job id">
                            {job.id}
                        </span>
                    </div>
                </div>
            </div>

            <div className="flex shrink-0 items-center justify-between gap-3 pl-8 sm:justify-end sm:pl-0">
                <div className="text-left sm:text-right" title={paused ? undefined : formatWhen(job.trigger)}>
                    {paused ? (
                        <div className="text-xs font-medium text-amber-500">Paused</div>
                    ) : (
                        <div className="text-xs font-medium tabular-nums text-foreground">{formatCountdown(remaining)}</div>
                    )}
                    <div className="text-[10px] text-muted-foreground">{paused ? "Next run frozen" : formatWhen(job.trigger)}</div>
                </div>
                <div className="flex items-center gap-0.5">
                    <RowButton onClick={() => onToggle(job)} disabled={busy} aria-label={paused ? "Resume job" : "Pause job"} title={paused ? "Resume" : "Pause"} className="w-7 justify-center px-0">
                        {paused ? <Play /> : <Pause />}
                    </RowButton>
                    <InlineConfirm iconOnly icon={Trash2} label="Delete job" prompt="Delete?" onConfirm={() => onDelete(job.id)} />
                </div>
            </div>
        </div>
    );
}

export function CronPage() {
    const [jobs, setJobs] = useState<Job[]>([]);
    const [loaded, setLoaded] = useState(false);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<unknown>(null);
    // Tick every second so countdowns move between polls.
    const [now, setNow] = useState(() => Date.now() / 1000);

    const [composerOpen, setComposerOpen] = useState<boolean | null>(null);
    const [mode, setMode] = useState<Mode>("relative");
    const [timeExpr, setTimeExpr] = useState("");
    const [cronExpr, setCronExpr] = useState("");
    const [message, setMessage] = useState("");
    const [channel, setChannel] = useState("web");
    const [chatId, setChatId] = useState("dashboard");
    const [tzOffset, setTzOffset] = useState<string>(String(-new Date().getTimezoneOffset()));
    const [adding, setAdding] = useState(false);
    const [formError, setFormError] = useState<string | null>(null);
    const [jobActionId, setJobActionId] = useState<string | null>(null);

    const fetchJobs = useCallback(() => {
        return axios
            .get(`${API_BASE_URL}/api/cron/jobs`)
            .then((res) => {
                setJobs(Array.isArray(res.data) ? res.data : []);
                setLoaded(true);
                setLoadError(null);
            })
            .catch((err) => {
                console.error("Failed to fetch jobs:", err);
                setLoadError(err);
            })
            .finally(() => setLoading(false));
    }, []);

    useEffect(() => {
        void fetchJobs();
        const pollInterval = setInterval(() => void fetchJobs(), 5000);
        const tickInterval = setInterval(() => setNow(Date.now() / 1000), 1000);
        return () => {
            clearInterval(pollInterval);
            clearInterval(tickInterval);
        };
    }, [fetchJobs]);

    const refresh = () => {
        setLoading(true);
        void fetchJobs();
    };

    const handleAddJob = (e: React.FormEvent) => {
        e.preventDefault();
        setFormError(null);

        if (mode === "relative" && !timeExpr.trim()) {
            setFormError("Enter how long to wait, for example 5m or 1h.");
            return;
        }
        if (mode === "cron" && !cronExpr.trim()) {
            setFormError("Enter a cron expression, for example 0 9 * * 1-5.");
            return;
        }

        setAdding(true);
        const payload: Record<string, unknown> = {
            message,
            context: { channel, chat_id: chatId },
            ...(mode === "relative" ? { time_expr: timeExpr } : { cron_expr: cronExpr, tz_offset: parseInt(tzOffset) }),
        };

        axios
            .post(`${API_BASE_URL}/api/cron/jobs`, payload)
            .then(() => {
                setTimeExpr("");
                setCronExpr("");
                setMessage("");
                setComposerOpen(false);
                toast.success("Job scheduled");
                void fetchJobs();
            })
            .catch((err) => setFormError(requestFailureMessage("Could not schedule the job", err)))
            .finally(() => setAdding(false));
    };

    const handleDelete = async (id: string) => {
        try {
            await axios.delete(`${API_BASE_URL}/api/cron/jobs/${id}`);
            setJobs((prev) => prev.filter((job) => job.id !== id));
            toast.success("Job deleted");
            void fetchJobs();
        } catch (err) {
            toast.error(requestFailureMessage("Could not delete the job", err));
        }
    };

    const handleToggleActive = (job: Job) => {
        const resume = job.active === false;
        setJobActionId(job.id);
        axios
            .patch(`${API_BASE_URL}/api/cron/jobs/${job.id}`, { active: resume })
            .then(() => fetchJobs())
            .catch((err) => toast.error(requestFailureMessage(resume ? "Could not resume the job" : "Could not pause the job", err)))
            .finally(() => setJobActionId(null));
    };

    const browserTzOffset = -new Date().getTimezoneOffset();
    const tzOptions = useMemo(() => {
        const offsets = Array.from({ length: 27 }, (_, i) => (i - 12) * 60);
        if (!offsets.includes(browserTzOffset)) offsets.push(browserTzOffset);
        return offsets.sort((a, b) => a - b);
    }, [browserTzOffset]);

    const sortedJobs = useMemo(
        () => [...jobs].sort((a, b) => Number(a.active === false) - Number(b.active === false) || a.trigger - b.trigger),
        [jobs]
    );
    const pausedCount = jobs.filter((job) => job.active === false).length;
    const showComposer = composerOpen ?? (loaded && jobs.length === 0);

    return (
        <PageFrame width="max-w-4xl">
            <PageHeader
                title="Scheduled jobs"
                description="Reminders and repeating tasks LimeBot runs for you."
                meta={
                    loaded && jobs.length > 0 ? (
                        <>
                            <Chip>{jobs.length - pausedCount} active</Chip>
                            {pausedCount > 0 && <Chip tone="amber">{pausedCount} paused</Chip>}
                        </>
                    ) : undefined
                }
                actions={
                    <>
                        <RowButton onClick={refresh} disabled={loading} aria-label="Refresh jobs" title="Refresh">
                            <RefreshCw className={cn(loading && "animate-spin")} />
                        </RowButton>
                        <Button size="sm" variant={showComposer ? "outline" : "default"} className="h-8 gap-1.5 px-3 text-xs" onClick={() => setComposerOpen(!showComposer)} aria-expanded={showComposer}>
                            {showComposer ? <X className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
                            {showComposer ? "Close" : "New job"}
                        </Button>
                    </>
                }
            />

            {showComposer && (
                <form onSubmit={handleAddJob} className="space-y-4 rounded-xl border border-border/60 bg-card/50 p-4 animate-in fade-in slide-in-from-top-1 duration-150">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <Segmented value={mode} onChange={(next) => { setMode(next); setFormError(null); }} />
                        <span className="text-[11px] text-muted-foreground">
                            {mode === "relative" ? "Runs once, after a delay." : "Runs on a schedule until you delete it."}
                        </span>
                    </div>

                    <div className="grid gap-3 sm:grid-cols-2">
                        {mode === "relative" ? (
                            <Field label="Run in" htmlFor="time" hint="10s, 5m, 2h or 1d">
                                <Input id="time" placeholder="5m" value={timeExpr} onChange={(e) => setTimeExpr(e.target.value)} className="h-9" />
                            </Field>
                        ) : (
                            <>
                                <Field label="Cron expression" htmlFor="cron" hint="minute hour day month weekday, for example 0 9 * * 1-5">
                                    <Input id="cron" placeholder="0 9 * * 1-5" value={cronExpr} onChange={(e) => setCronExpr(e.target.value)} className="h-9 font-mono" />
                                </Field>
                                <Field label="Timezone" hint="The schedule is read in this timezone.">
                                    <Select value={tzOffset} onValueChange={setTzOffset}>
                                        <SelectTrigger className="h-9" aria-label="Timezone">
                                            <SelectValue placeholder="Select timezone" />
                                        </SelectTrigger>
                                        <SelectContent className="max-h-[300px]">
                                            {tzOptions.map((off) => (
                                                <SelectItem key={off} value={String(off)}>
                                                    {formatTzOffset(off)}
                                                    {off === browserTzOffset ? " (this browser)" : ""}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </Field>
                            </>
                        )}
                        <Field label="Message" htmlFor="message" className={mode === "relative" ? "" : "sm:col-span-2"}>
                            <Input id="message" placeholder="Remind me to…" value={message} onChange={(e) => setMessage(e.target.value)} required className="h-9" />
                        </Field>
                        <Field label="Channel">
                            <Select value={channel} onValueChange={setChannel}>
                                <SelectTrigger className="h-9" aria-label="Channel">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="web">Web</SelectItem>
                                    <SelectItem value="discord">Discord</SelectItem>
                                    <SelectItem value="whatsapp">WhatsApp</SelectItem>
                                </SelectContent>
                            </Select>
                        </Field>
                        <Field label="Chat ID" htmlFor="chatId">
                            <Input id="chatId" value={chatId} onChange={(e) => setChatId(e.target.value)} required className="h-9 font-mono" />
                        </Field>
                    </div>

                    {formError && (
                        <div role="alert" className="flex items-start gap-2 rounded-lg border-l-2 border-red-400/70 bg-red-500/[0.06] py-2 pl-3 pr-2 text-xs">
                            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-500" />
                            <span className="text-foreground">{formError}</span>
                        </div>
                    )}

                    <div className="flex justify-end">
                        <Button type="submit" size="sm" disabled={adding} className="h-8 gap-1.5 px-4 text-xs">
                            {adding ? "Scheduling…" : "Schedule job"}
                        </Button>
                    </div>
                </form>
            )}

            {!loaded && loading && <SkeletonRows count={3} />}
            {!loaded && !loading && <RequestError error={loadError} subject="scheduled jobs" onRetry={refresh} retrying={loading} />}
            {loaded && loadError !== null && <RequestError compact error={loadError} subject="scheduled jobs" onRetry={refresh} retrying={loading} />}

            {loaded &&
                (sortedJobs.length === 0 ? (
                    !showComposer && (
                        <EmptyState
                            icon={CalendarClock}
                            title="No scheduled jobs"
                            hint="Ask LimeBot in chat (“remind me in 10 minutes”) or add one here."
                            action={
                                <Button size="sm" onClick={() => setComposerOpen(true)} className="gap-1.5">
                                    <Plus className="h-4 w-4" /> New job
                                </Button>
                            }
                        />
                    )
                ) : (
                    <RowList>
                        {sortedJobs.map((job) => (
                            <JobRow key={job.id} job={job} now={now} busy={jobActionId === job.id} onToggle={handleToggleActive} onDelete={handleDelete} />
                        ))}
                    </RowList>
                ))}
        </PageFrame>
    );
}
