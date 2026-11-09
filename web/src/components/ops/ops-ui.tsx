import { useState, type ReactNode } from "react";
import { AlertTriangle, ChevronRight, RefreshCw, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { classifyRequestError } from "@/lib/request-error";
import type { Tone } from "@/lib/ops-status";
import type { ToolErrorInfo } from "@/lib/tool-display";
import { CopyButton } from "@/components/chat/tool-ui";

/**
 * Shared pieces for the operations pages (Overview, Task Queue, Logs,
 * Instances). They follow the chat tool rows: hairline borders, no boxed
 * panels, status glyphs and plain-language errors.
 */

export { StatusGlyph } from "@/components/chat/tool-ui";

export type { Tone } from "@/lib/ops-status";

export function StatusDot({ tone, pulse, className }: { tone: Tone; pulse?: boolean; className?: string }) {
    const color =
        tone === "ready" ? "bg-primary" : tone === "warning" ? "bg-amber-500" : tone === "error" ? "bg-red-500" : "bg-muted-foreground/40";
    return (
        <span className={cn("relative inline-flex h-2 w-2 shrink-0", className)} aria-hidden>
            {pulse && <span className={cn("absolute inset-0 animate-ping rounded-full opacity-40", color)} />}
            <span className={cn("relative h-2 w-2 rounded-full", color)} />
        </span>
    );
}

/** Page chrome: one hairline-separated header over a body that fills the rest. */
export function PageFrame({
    title,
    description,
    actions,
    children,
    scroll = true,
    maxWidth = "max-w-6xl",
}: {
    title: string;
    description?: string;
    actions?: ReactNode;
    children: ReactNode;
    /** When false the body does not scroll and the page manages its own panes. */
    scroll?: boolean;
    maxWidth?: string;
}) {
    return (
        <div className="flex h-full min-h-0 flex-col bg-background">
            <header className="shrink-0 border-b border-border/60">
                <div className={cn("mx-auto flex w-full items-center justify-between gap-x-4 gap-y-2 px-4 py-2.5 sm:px-6 md:py-3", maxWidth)}>
                    <div className="min-w-0">
                        {/* The phone header already names the page, so only md and up repeat it. */}
                        <h1 className="hidden truncate text-base font-semibold tracking-tight text-foreground md:block">{title}</h1>
                        {description && <p className="truncate text-xs text-muted-foreground md:mt-0.5">{description}</p>}
                    </div>
                    {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
                </div>
            </header>
            <div className={cn("min-h-0 flex-1", scroll ? "overflow-y-auto" : "flex flex-col overflow-hidden")}>{children}</div>
        </div>
    );
}

export function PageBody({ children, className, maxWidth = "max-w-6xl" }: { children: ReactNode; className?: string; maxWidth?: string }) {
    return <div className={cn("mx-auto w-full px-4 py-5 sm:px-6", maxWidth, className)}>{children}</div>;
}

export function SectionHead({
    title,
    hint,
    action,
    className,
}: {
    title: string;
    hint?: ReactNode;
    action?: ReactNode;
    className?: string;
}) {
    return (
        <div className={cn("flex min-h-[1.75rem] items-center justify-between gap-3", className)}>
            <div className="flex min-w-0 items-baseline gap-2">
                <h2 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/90">{title}</h2>
                {hint && <span className="truncate text-[11px] text-muted-foreground/70">{hint}</span>}
            </div>
            {action}
        </div>
    );
}

/** A label/value line: the replacement for bordered definition lists. */
export function FactRow({ label, children, title }: { label: string; children: ReactNode; title?: string }) {
    return (
        <div className="flex items-baseline justify-between gap-4 border-b border-border/50 py-2 text-sm last:border-b-0" title={title}>
            <dt className="shrink-0 text-muted-foreground">{label}</dt>
            <dd className="min-w-0 truncate text-right font-medium text-foreground">{children}</dd>
        </div>
    );
}

export function IconButton({
    label,
    onClick,
    disabled,
    children,
    className,
}: {
    label: string;
    onClick?: () => void;
    disabled?: boolean;
    children: ReactNode;
    className?: string;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={disabled}
            aria-label={label}
            title={label}
            className={cn(
                "inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-foreground/[0.07] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:h-4 [&_svg]:w-4",
                className
            )}
        >
            {children}
        </button>
    );
}

/** Small outlined action, used where a full Button would be too loud. */
export function QuietButton({
    onClick,
    disabled,
    tone = "default",
    children,
    className,
    type = "button",
}: {
    onClick?: () => void;
    disabled?: boolean;
    tone?: "default" | "danger" | "primary";
    children: ReactNode;
    className?: string;
    type?: "button" | "submit";
}) {
    return (
        <button
            type={type}
            onClick={onClick}
            disabled={disabled}
            className={cn(
                "inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border px-2.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:h-3.5 [&_svg]:w-3.5 [&_svg]:shrink-0",
                tone === "default" && "border-border text-foreground hover:bg-foreground/[0.07]",
                tone === "primary" && "border-primary bg-primary text-primary-foreground hover:bg-primary/90",
                tone === "danger" && "border-red-500/30 text-red-500 hover:bg-red-500/10",
                className
            )}
        >
            {children}
        </button>
    );
}

export function RefreshButton({ onClick, busy, label = "Refresh" }: { onClick: () => void; busy?: boolean; label?: string }) {
    return (
        <QuietButton onClick={onClick} disabled={busy}>
            <RefreshCw className={cn(busy && "animate-spin")} />
            {label}
        </QuietButton>
    );
}

/** Pill group with a hairline outline; the active choice is filled. */
export function Segmented<T extends string>({
    value,
    options,
    onChange,
    label,
    className,
}: {
    value: T;
    options: Array<{ value: T; label: ReactNode }>;
    onChange: (value: T) => void;
    label: string;
    className?: string;
}) {
    return (
        <div role="tablist" aria-label={label} className={cn("inline-flex rounded-md border border-border p-0.5", className)}>
            {options.map((option) => (
                <button
                    key={option.value}
                    type="button"
                    role="tab"
                    aria-selected={value === option.value}
                    onClick={() => onChange(option.value)}
                    className={cn(
                        "rounded px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        value === option.value ? "bg-primary/15 text-foreground" : "text-muted-foreground hover:text-foreground"
                    )}
                >
                    {option.label}
                </button>
            ))}
        </div>
    );
}

/**
 * A failed request, said the way tool failures are: what happened, what to do,
 * raw details one click away, and a retry when trying again can work.
 */
export function RequestError({
    error,
    info,
    what,
    onRetry,
    retrying,
    stale,
    className,
}: {
    error?: unknown;
    info?: ToolErrorInfo;
    /** Names the data for the 404 message, for example "the task queue". */
    what?: string;
    onRetry?: () => void;
    retrying?: boolean;
    /** True when older data is still on screen below this notice. */
    stale?: boolean;
    className?: string;
}) {
    const resolved = info ?? classifyRequestError(error, what);
    const soft = ["rate_limit", "timeout", "network", "authentication"].includes(resolved.category);
    const [open, setOpen] = useState(false);
    return (
        <div
            role="alert"
            className={cn(
                "rounded-lg border-l-2 py-2.5 pl-3 pr-2.5",
                soft ? "border-amber-400/70 bg-amber-500/[0.06]" : "border-red-400/70 bg-red-500/[0.06]",
                className
            )}
        >
            <div className="flex items-start gap-2.5">
                <AlertTriangle className={cn("mt-0.5 h-4 w-4 shrink-0", soft ? "text-amber-500" : "text-red-500")} />
                <div className="min-w-0 flex-1">
                    <div className="text-sm font-semibold text-foreground">{resolved.title}</div>
                    <div className="mt-0.5 text-xs leading-snug text-muted-foreground">
                        {resolved.hint}
                        {stale && " Showing the last data that loaded."}
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                        {onRetry && (
                            <QuietButton onClick={onRetry} disabled={retrying} className="h-7">
                                <RefreshCw className={cn(retrying && "animate-spin")} />
                                {retrying ? "Retrying…" : "Retry"}
                            </QuietButton>
                        )}
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
                            <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border/60 bg-background/70 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
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

export function EmptyState({
    icon: Icon,
    title,
    hint,
    action,
    className,
}: {
    icon: LucideIcon;
    title: string;
    hint?: string;
    action?: ReactNode;
    className?: string;
}) {
    return (
        <div className={cn("flex flex-col items-center justify-center gap-1 px-6 py-12 text-center", className)}>
            <Icon className="mb-2 h-6 w-6 text-muted-foreground/40" strokeWidth={1.5} />
            <p className="text-sm font-medium text-foreground">{title}</p>
            {hint && <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">{hint}</p>}
            {action && <div className="mt-3">{action}</div>}
        </div>
    );
}

/** Placeholder lines shaped like the rows they stand in for. */
export function SkeletonRows({ rows = 5, className }: { rows?: number; className?: string }) {
    return (
        <div className={cn("divide-y divide-border/50", className)} role="status" aria-label="Loading">
            {Array.from({ length: rows }, (_, index) => (
                <div key={index} className="flex items-center gap-3 px-4 py-3">
                    <div className="h-5 w-5 shrink-0 animate-pulse rounded-full bg-foreground/10" />
                    <div className="min-w-0 flex-1 space-y-1.5">
                        <div className="h-3 animate-pulse rounded bg-foreground/10" style={{ width: `${55 + ((index * 17) % 35)}%` }} />
                        <div className="h-2.5 w-1/3 animate-pulse rounded bg-foreground/[0.06]" />
                    </div>
                </div>
            ))}
        </div>
    );
}
