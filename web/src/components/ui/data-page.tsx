import { useEffect, useState, type ButtonHTMLAttributes, type ElementType, type ReactNode } from "react";
import { AlertTriangle, Check, ChevronRight, Loader2, RefreshCw, X, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { classifyRequestError, type RequestErrorInfo } from "@/lib/request-error";
import { StatusGlyph, CopyButton } from "@/components/chat/tool-ui";
import type { RowState } from "@/lib/tool-state";

/**
 * Small building blocks shared by the data pages (Memory, Channels, Cron,
 * Browser sessions). Same visual language as the chat tool rows: hairline
 * borders, quiet type, the status glyph, amber for "needs you", red for "failed".
 */

export function PageFrame({ children, className, width = "max-w-5xl" }: { children: ReactNode; className?: string; width?: string }) {
    return (
        <div className="h-full overflow-y-auto">
            <div className={cn("mx-auto w-full space-y-5 px-4 py-5 sm:px-6 lg:px-8", width, className)}>{children}</div>
        </div>
    );
}

export function PageHeader({
    title,
    description,
    meta,
    actions,
    sticky,
}: {
    title: string;
    description?: ReactNode;
    meta?: ReactNode;
    actions?: ReactNode;
    sticky?: boolean;
}) {
    return (
        <header
            className={cn(
                "flex items-start justify-between gap-3",
                sticky && "sticky top-0 z-20 -mx-4 border-b border-border/60 bg-background/85 px-4 py-3 backdrop-blur-md sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8"
            )}
        >
            <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2.5">
                    <h1 className="text-lg font-semibold leading-tight tracking-tight text-foreground">{title}</h1>
                    {meta}
                </div>
                {description && <p className="mt-0.5 max-w-prose text-xs leading-relaxed text-muted-foreground">{description}</p>}
            </div>
            {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
        </header>
    );
}

/** A quiet count or tag next to a title. */
export function Chip({
    children,
    tone = "muted",
    className,
    title,
}: {
    children: ReactNode;
    tone?: "muted" | "primary" | "amber" | "red";
    className?: string;
    title?: string;
}) {
    const tones = {
        muted: "border-border/70 text-muted-foreground",
        primary: "border-primary/30 text-primary",
        amber: "border-amber-400/40 text-amber-500",
        red: "border-red-400/40 text-red-500",
    } as const;
    return (
        <span title={title} className={cn("inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-medium leading-none", tones[tone], className)}>
            {children}
        </span>
    );
}

const STATE_TONE: Record<RowState, string> = {
    planned: "text-muted-foreground",
    running: "text-primary",
    done: "text-primary",
    approved: "text-primary",
    failed: "text-red-500",
    denied: "text-red-500",
    waiting: "text-amber-500",
};

/** Status glyph plus a one-word label, the same way tool rows show it. */
/** Amber "stale or needs a look" glyph: same size and shape as StatusGlyph. */
export function WarnGlyph({ label = "Needs attention" }: { label?: string }) {
    return (
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-500/15 text-amber-500" aria-label={label}>
            <AlertTriangle className="h-3 w-3" />
        </span>
    );
}

export function StatusPill({ state, label, className }: { state: RowState; label: string; className?: string }) {
    return (
        <span className={cn("inline-flex items-center gap-1.5 text-[11px] font-medium", STATE_TONE[state], className)}>
            <StatusGlyph state={state} />
            {label}
        </span>
    );
}

/** The container for a list of rows: one hairline frame, rows split by hairlines. */
export function RowList({ children, className }: { children: ReactNode; className?: string }) {
    return <div className={cn("divide-y divide-border/50 overflow-hidden rounded-xl border border-border/60 bg-card/50", className)}>{children}</div>;
}

export function SectionLabel({ children, action, className }: { children: ReactNode; action?: ReactNode; className?: string }) {
    return (
        <div className={cn("flex items-center justify-between gap-2", className)}>
            <h2 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80">{children}</h2>
            {action}
        </div>
    );
}

export function EmptyState({
    icon: Icon,
    title,
    hint,
    action,
    className,
}: {
    icon: LucideIcon;
    title: string;
    hint?: ReactNode;
    action?: ReactNode;
    className?: string;
}) {
    return (
        <div className={cn("flex flex-col items-center rounded-xl border border-dashed border-border/70 px-6 py-10 text-center", className)}>
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-muted text-muted-foreground">
                <Icon className="h-4 w-4" />
            </span>
            <p className="mt-3 text-sm font-medium text-foreground">{title}</p>
            {hint && <p className="mt-1 max-w-sm text-xs leading-relaxed text-muted-foreground">{hint}</p>}
            {action && <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div>}
        </div>
    );
}

export function SkeletonRows({ count = 4, className }: { count?: number; className?: string }) {
    return (
        <div className={cn("divide-y divide-border/50 overflow-hidden rounded-xl border border-border/60 bg-card/50", className)} aria-busy="true" aria-label="Loading">
            {Array.from({ length: count }, (_, index) => (
                <div key={index} className="flex items-center gap-3 px-4 py-3.5">
                    <span className="h-5 w-5 shrink-0 animate-pulse rounded-full bg-muted" />
                    <div className="flex-1 space-y-2">
                        <span className="block h-3 animate-pulse rounded bg-muted" style={{ width: `${62 - (index % 3) * 14}%` }} />
                        <span className="block h-2.5 animate-pulse rounded bg-muted/60" style={{ width: `${38 + (index % 2) * 12}%` }} />
                    </div>
                </div>
            ))}
        </div>
    );
}

/** Small ghost icon/text button used for row actions. */
export function RowButton({
    children,
    className,
    tone = "default",
    ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "default" | "danger" | "primary" }) {
    const tones = {
        default: "text-muted-foreground hover:bg-muted hover:text-foreground",
        danger: "text-muted-foreground hover:bg-red-500/10 hover:text-red-500",
        primary: "text-primary hover:bg-primary/10",
    } as const;
    return (
        <button
            type="button"
            {...props}
            className={cn(
                "inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:h-3.5 [&_svg]:w-3.5",
                tones[tone],
                className
            )}
        >
            {children}
        </button>
    );
}

/**
 * Destructive action that asks in place. The first click swaps the button for
 * "Delete? Yes / No"; it disarms itself after a few seconds or on Escape.
 */
export function InlineConfirm({
    label,
    icon: Icon,
    prompt = "Sure?",
    confirmLabel = "Delete",
    onConfirm,
    disabled,
    iconOnly,
    className,
}: {
    label: string;
    icon?: ElementType;
    prompt?: string;
    confirmLabel?: string;
    onConfirm: () => void | Promise<void>;
    disabled?: boolean;
    iconOnly?: boolean;
    className?: string;
}) {
    const [armed, setArmed] = useState(false);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!armed) return;
        const timer = window.setTimeout(() => setArmed(false), 7000);
        return () => window.clearTimeout(timer);
    }, [armed]);

    if (!armed) {
        return (
            <RowButton tone="danger" disabled={disabled} onClick={() => setArmed(true)} aria-label={label} title={label} className={cn(iconOnly && "w-7 px-0 justify-center", className)}>
                {Icon && <Icon />}
                {!iconOnly && label}
            </RowButton>
        );
    }

    return (
        <span
            role="group"
            aria-label={`${label}: confirm`}
            onKeyDown={(event) => {
                if (event.key === "Escape") setArmed(false);
            }}
            className="inline-flex items-center gap-0.5 rounded-md border border-red-400/40 bg-red-500/[0.07] pl-2 animate-in fade-in zoom-in-95 duration-100"
        >
            <span className="text-[11px] text-muted-foreground">{prompt}</span>
            <button
                type="button"
                disabled={busy}
                onClick={async () => {
                    setBusy(true);
                    try {
                        await onConfirm();
                    } finally {
                        setBusy(false);
                        setArmed(false);
                    }
                }}
                className="inline-flex h-7 items-center gap-1 rounded px-2 text-[11px] font-semibold text-red-500 hover:bg-red-500/10 disabled:opacity-60"
            >
                {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
                {confirmLabel}
            </button>
            <button
                type="button"
                autoFocus
                onClick={() => setArmed(false)}
                aria-label="Cancel"
                className="inline-flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
            >
                <X className="h-3.5 w-3.5" />
            </button>
        </span>
    );
}

/**
 * A failed request, in plain language, with a retry button. `info` can be
 * passed directly, or pass the raw `error` and it is classified for you.
 */
export function RequestError({
    error,
    info,
    subject,
    onRetry,
    retrying,
    compact,
    className,
}: {
    error?: unknown;
    info?: RequestErrorInfo;
    subject?: string;
    onRetry?: () => void;
    retrying?: boolean;
    /** Slim banner for "the last refresh failed, showing older data". */
    compact?: boolean;
    className?: string;
}) {
    const resolved = info || classifyRequestError(error, subject);
    const [open, setOpen] = useState(false);
    const soft = resolved.category === "rate_limit" || resolved.category === "timeout" || resolved.category === "network";
    const showRetry = onRetry && (resolved.retryable || resolved.category === "unknown");

    const retryButton = showRetry && (
        <button
            type="button"
            onClick={onRetry}
            disabled={retrying}
            className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-border bg-background px-2.5 text-[11px] font-semibold text-foreground transition-colors hover:bg-muted disabled:opacity-60"
        >
            <RefreshCw className={cn("h-3 w-3", retrying && "animate-spin")} />
            {retrying ? "Retrying…" : "Try again"}
        </button>
    );

    if (compact) {
        return (
            <div role="alert" className={cn("flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border-l-2 border-amber-400/70 bg-amber-500/[0.06] py-2 pl-3 pr-2", className)}>
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" />
                <span className="min-w-0 flex-1 text-xs text-muted-foreground">
                    <span className="font-semibold text-foreground">{resolved.title}.</span> Showing the last data we had.
                </span>
                {retryButton}
            </div>
        );
    }

    return (
        <div
            role="alert"
            className={cn(
                "rounded-xl border-l-2 py-3 pl-4 pr-3",
                soft ? "border-amber-400/70 bg-amber-500/[0.06]" : "border-red-400/70 bg-red-500/[0.06]",
                className
            )}
        >
            <div className="flex items-start gap-2.5">
                <AlertTriangle className={cn("mt-0.5 h-4 w-4 shrink-0", soft ? "text-amber-500" : "text-red-500")} />
                <div className="min-w-0 flex-1">
                    <div className="text-sm font-semibold text-foreground">{resolved.title}</div>
                    <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{resolved.hint}</p>
                    <div className="mt-2.5 flex flex-wrap items-center gap-2">
                        {retryButton}
                        {resolved.detail && (
                            <button
                                type="button"
                                onClick={() => setOpen((value) => !value)}
                                aria-expanded={open}
                                className="inline-flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground"
                            >
                                <ChevronRight className={cn("h-3 w-3 transition-transform", open && "rotate-90")} />
                                Details
                            </button>
                        )}
                    </div>
                    {open && resolved.detail && (
                        <div className="mt-2 space-y-1">
                            <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border/60 bg-background/70 p-2.5 font-mono text-[11px] leading-relaxed text-muted-foreground">
                                {resolved.status ? `HTTP ${resolved.status}\n` : ""}
                                {resolved.detail}
                            </pre>
                            <div className="flex justify-end">
                                <CopyButton text={resolved.detail} label="Copy error" />
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

/** Amber "heads up" line for non-error warnings such as read-only mode. */
export function Notice({ title, children, action, className }: { title: string; children?: ReactNode; action?: ReactNode; className?: string }) {
    return (
        <div className={cn("flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border-l-2 border-amber-400/70 bg-amber-500/[0.06] py-2 pl-3 pr-2", className)}>
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" />
            <div className="min-w-0 flex-1 text-xs leading-relaxed text-muted-foreground">
                <span className="font-semibold text-foreground">{title}</span>
                {children && <> {children}</>}
            </div>
            {action}
        </div>
    );
}
