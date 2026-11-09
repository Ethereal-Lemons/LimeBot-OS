import { useState, type ReactNode } from "react";
import {
    AlertTriangle,
    Check,
    ChevronRight,
    Inbox,
    Info,
    Loader2,
    RefreshCw,
    RotateCcw,
    Save,
    X,
    type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { classifySaveError, type SaveErrorInfo } from "@/lib/save-error";

/**
 * Shared building blocks for settings-style pages: a page scaffold with one
 * sticky save bar, grouped sections without card chrome, labelled fields with
 * inline errors, and friendly empty / loading / failed states.
 */

// ── page scaffold ───────────────────────────────────────────────────────────

export function SettingsPage({
    title,
    description,
    icon: Icon,
    actions,
    saveBar,
    width = "max-w-4xl",
    children,
}: {
    title: string;
    description?: ReactNode;
    icon?: LucideIcon;
    /** Page level controls shown beside the title (refresh, new item). */
    actions?: ReactNode;
    saveBar?: ReactNode;
    width?: string;
    children: ReactNode;
}) {
    return (
        <div className="h-full overflow-y-auto bg-background/40">
            <div className={cn("mx-auto w-full px-4 pt-6 sm:px-6 md:px-8", width, saveBar ? "pb-6" : "pb-12")}>
                <header className="mb-2 flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
                    <div className="min-w-0">
                        <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight text-foreground">
                            {Icon && <Icon className="h-5 w-5 shrink-0 text-primary" />}
                            {title}
                        </h1>
                        {description && <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{description}</p>}
                    </div>
                    {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
                </header>
                {children}
            </div>
            {saveBar}
        </div>
    );
}

// ── sections and fields ─────────────────────────────────────────────────────

export function SettingsSection({
    title,
    description,
    icon: Icon,
    badge,
    tone = "default",
    children,
}: {
    title: string;
    description?: ReactNode;
    icon?: LucideIcon;
    badge?: ReactNode;
    tone?: "default" | "warn";
    children: ReactNode;
}) {
    return (
        <section className="grid gap-x-10 gap-y-4 border-t border-border/50 py-7 first:border-t-0 lg:grid-cols-[15rem_minmax(0,1fr)]">
            <div className="space-y-1.5">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                    {Icon && <Icon className={cn("h-4 w-4 shrink-0", tone === "warn" ? "text-amber-500" : "text-muted-foreground")} />}
                    {title}
                </h2>
                {description && <p className="text-xs leading-relaxed text-muted-foreground">{description}</p>}
                {badge && <div className="pt-1">{badge}</div>}
            </div>
            <div className="min-w-0 space-y-5">{children}</div>
        </section>
    );
}

export function Field({
    label,
    htmlFor,
    hint,
    error,
    optional,
    inline,
    badge,
    children,
    className,
}: {
    label: ReactNode;
    htmlFor?: string;
    hint?: ReactNode;
    error?: string | null;
    optional?: boolean;
    /** Control sits to the right of the label (switches, short numbers). */
    inline?: boolean;
    badge?: ReactNode;
    children: ReactNode;
    className?: string;
}) {
    const labelNode = (
        <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
                <Label htmlFor={htmlFor} className="text-sm font-medium">
                    {label}
                </Label>
                {optional && <span className="text-[11px] text-muted-foreground">Optional</span>}
                {badge}
            </div>
            {hint && <p className="text-xs leading-relaxed text-muted-foreground">{hint}</p>}
        </div>
    );
    if (inline) {
        return (
            <div className={cn("space-y-1.5", className)}>
                <div className="flex items-center justify-between gap-6">
                    {labelNode}
                    <div className="shrink-0">{children}</div>
                </div>
                <FieldError message={error} />
            </div>
        );
    }
    return (
        <div className={cn("space-y-2", className)}>
            {labelNode}
            {children}
            <FieldError message={error} />
        </div>
    );
}

export function FieldError({ message, id }: { message?: string | null; id?: string }) {
    if (!message) return null;
    return (
        <p id={id} role="alert" className="flex items-start gap-1.5 text-xs text-red-500">
            <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
            {message}
        </p>
    );
}

/** Border colour for an input that failed validation. Add to the input's className. */
export const invalidInputClass = "border-red-500/60 focus-visible:ring-red-500/40";

// ── small status pieces ─────────────────────────────────────────────────────

type Tone = "ok" | "warn" | "bad" | "muted" | "info";

const PILL_TONE: Record<Tone, string> = {
    ok: "bg-primary/10 text-primary",
    warn: "bg-amber-500/10 text-amber-500",
    bad: "bg-red-500/10 text-red-500",
    muted: "bg-muted text-muted-foreground",
    info: "bg-secondary text-secondary-foreground",
};

export function StatusPill({ tone = "muted", icon: Icon, children, className }: { tone?: Tone; icon?: LucideIcon; children: ReactNode; className?: string }) {
    return (
        <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium", PILL_TONE[tone], className)}>
            {Icon && <Icon className="h-3 w-3" />}
            {children}
        </span>
    );
}

const NOTE_TONE: Record<"info" | "warn" | "bad", { bar: string; icon: string; Icon: LucideIcon }> = {
    info: { bar: "border-primary/50", icon: "text-primary", Icon: Info },
    warn: { bar: "border-amber-500/60", icon: "text-amber-500", Icon: AlertTriangle },
    bad: { bar: "border-red-500/60", icon: "text-red-500", Icon: AlertTriangle },
};

/** A quiet note with an accent edge instead of a boxed alert. */
export function Note({ tone = "info", title, children, className }: { tone?: "info" | "warn" | "bad"; title?: ReactNode; children?: ReactNode; className?: string }) {
    const { bar, icon, Icon } = NOTE_TONE[tone];
    return (
        <div role={tone === "bad" ? "alert" : "note"} className={cn("flex gap-2.5 border-l-2 py-0.5 pl-3 text-sm", bar, className)}>
            <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", icon)} />
            <div className="min-w-0 space-y-0.5">
                {title && <p className="font-medium text-foreground">{title}</p>}
                {children && <div className="text-xs leading-relaxed text-muted-foreground">{children}</div>}
            </div>
        </div>
    );
}

// ── save bar and failed-save note ───────────────────────────────────────────

export function SaveErrorNote({ error, onRetry, onDismiss, className }: { error: SaveErrorInfo; onRetry?: () => void; onDismiss?: () => void; className?: string }) {
    const [open, setOpen] = useState(false);
    const showDetail = error.detail && error.detail !== error.title;
    return (
        <div role="alert" className={cn("border-l-2 border-red-500/70 pl-3 text-sm", className)}>
            <div className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
                <div className="min-w-0 flex-1 space-y-0.5">
                    <p className="font-medium text-foreground">{error.title}</p>
                    <p className="text-xs leading-relaxed text-muted-foreground">{error.hint}</p>
                    {showDetail && (
                        <div className="pt-0.5">
                            <button
                                type="button"
                                onClick={() => setOpen((value) => !value)}
                                className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                                aria-expanded={open}
                            >
                                <ChevronRight className={cn("h-3 w-3 transition-transform", open && "rotate-90")} />
                                Details
                            </button>
                            {open && (
                                <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 p-2 font-mono text-[11px] text-muted-foreground">
                                    {error.detail}
                                </pre>
                            )}
                        </div>
                    )}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                    {error.retryable && onRetry && (
                        <Button type="button" variant="ghost" size="sm" className="h-7 gap-1 px-2 text-xs" onClick={onRetry}>
                            <RefreshCw className="h-3 w-3" /> Try again
                        </Button>
                    )}
                    {onDismiss && (
                        <Button type="button" variant="ghost" size="icon" className="h-7 w-7" onClick={onDismiss} aria-label="Dismiss">
                            <X className="h-3.5 w-3.5" />
                        </Button>
                    )}
                </div>
            </div>
        </div>
    );
}

/**
 * The page's single save action. Sticks to the bottom of the scroll area,
 * shows whether anything is unsaved, and carries the classified failure.
 */
export function SaveBar({
    dirty,
    saving,
    onSave,
    onDiscard,
    error,
    onDismissError,
    disabled,
    savedAt,
    changeCount,
    saveLabel = "Save changes",
    unsavedLabel,
}: {
    dirty: boolean;
    saving: boolean;
    onSave: () => void;
    onDiscard?: () => void;
    error?: SaveErrorInfo | null;
    onDismissError?: () => void;
    /** True when a field is invalid; the button explains rather than failing silently. */
    disabled?: boolean;
    savedAt?: number | null;
    changeCount?: number;
    saveLabel?: string;
    unsavedLabel?: string;
}) {
    const recentlySaved = !dirty && !saving && !error && savedAt;
    return (
        <div className="sticky bottom-0 z-20 border-t border-border/60 bg-background/90 backdrop-blur-md" data-testid="save-bar">
            <div className="mx-auto w-full max-w-4xl px-4 py-2.5 sm:px-6 md:px-8">
                {error && <SaveErrorNote error={error} onRetry={onSave} onDismiss={onDismissError} className="mb-2.5" />}
                <div className="flex items-center justify-between gap-3">
                    <p className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
                        {saving ? (
                            <>
                                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Saving…
                            </>
                        ) : dirty ? (
                            <>
                                <span className="h-2 w-2 shrink-0 rounded-full bg-amber-500" />
                                <span className="truncate text-foreground">
                                    {unsavedLabel || "Unsaved changes"}
                                    {changeCount ? <span className="text-muted-foreground"> · {changeCount}</span> : null}
                                </span>
                            </>
                        ) : (
                            <>
                                <Check className="h-3.5 w-3.5 text-primary" />
                                <span className="truncate">{recentlySaved ? "Saved" : "All changes saved"}</span>
                            </>
                        )}
                    </p>
                    <div className="flex shrink-0 items-center gap-2">
                        {onDiscard && (
                            <Button type="button" variant="ghost" size="sm" onClick={onDiscard} disabled={!dirty || saving} className="gap-1.5">
                                <RotateCcw className="h-3.5 w-3.5" />
                                <span className="hidden sm:inline">Discard</span>
                            </Button>
                        )}
                        <Button type="button" size="sm" onClick={onSave} disabled={!dirty || saving || disabled} className="gap-1.5">
                            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                            {saveLabel}
                        </Button>
                    </div>
                </div>
            </div>
        </div>
    );
}

// ── empty, loading, failed ──────────────────────────────────────────────────

export function EmptyState({ icon: Icon = Inbox, title, description, action, className }: { icon?: LucideIcon; title: string; description?: ReactNode; action?: ReactNode; className?: string }) {
    return (
        <div className={cn("flex flex-col items-center gap-2 px-4 py-12 text-center", className)}>
            <span className="flex h-10 w-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
                <Icon className="h-5 w-5" />
            </span>
            <p className="text-sm font-medium text-foreground">{title}</p>
            {description && <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">{description}</p>}
            {action && <div className="pt-2">{action}</div>}
        </div>
    );
}

export function LoadingRows({ rows = 4, className, label = "Loading" }: { rows?: number; className?: string; label?: string }) {
    return (
        <div className={cn("space-y-3", className)} role="status" aria-label={label}>
            {Array.from({ length: rows }).map((_, index) => (
                <div key={index} className="flex items-center gap-3">
                    <div className="h-8 w-8 shrink-0 animate-pulse rounded-lg bg-muted/70" />
                    <div className="flex-1 space-y-1.5">
                        <div className="h-3 animate-pulse rounded bg-muted/70" style={{ width: `${55 + ((index * 17) % 35)}%` }} />
                        <div className="h-2.5 w-2/5 animate-pulse rounded bg-muted/50" />
                    </div>
                </div>
            ))}
        </div>
    );
}

/** Shown in place of a page whose first load failed. Retry reruns the load. */
export function LoadFailed({ error, subject, onRetry }: { error: unknown; subject: string; onRetry: () => void }) {
    const info = classifySaveError(error, subject);
    const title = info.category === "network" ? `Could not load ${subject}` : info.title;
    return (
        <div className="mx-auto max-w-md px-4 py-16">
            <SaveErrorNote error={{ ...info, title, retryable: false }} />
            <Button type="button" variant="outline" size="sm" className="mt-4 gap-1.5" onClick={onRetry}>
                <RefreshCw className="h-3.5 w-3.5" /> Try again
            </Button>
        </div>
    );
}

// ── tabs ────────────────────────────────────────────────────────────────────

export function SettingsTabs({
    value,
    onValueChange,
    tabs,
    className,
}: {
    value: string;
    onValueChange: (value: string) => void;
    tabs: Array<{ value: string; label: string; short?: string; icon?: LucideIcon; content: ReactNode; badge?: ReactNode }>;
    className?: string;
}) {
    return (
        <Tabs value={value} onValueChange={onValueChange} className={className}>
            <TabsList className="mt-3 flex h-auto w-full justify-start gap-1 overflow-x-auto rounded-none border-b border-border/60 bg-transparent p-0">
                {tabs.map(({ value: tabValue, label, short, icon: Icon, badge }) => (
                    <TabsTrigger
                        key={tabValue}
                        value={tabValue}
                        className="-mb-px gap-2 rounded-none border-b-2 border-transparent bg-transparent px-3 py-2.5 text-muted-foreground shadow-none data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none"
                    >
                        {Icon && <Icon className="h-4 w-4" />}
                        <span className="hidden sm:inline">{label}</span>
                        <span className="sm:hidden">{short || label}</span>
                        {badge}
                    </TabsTrigger>
                ))}
            </TabsList>
            {tabs.map(({ value: tabValue, content }) => (
                <TabsContent key={tabValue} value={tabValue} className="mt-0">
                    {content}
                </TabsContent>
            ))}
        </Tabs>
    );
}
