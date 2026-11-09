import { useEffect, type ReactNode } from "react";
import { AlertTriangle, ChevronRight, Plus, Trash2, Undo2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { getSecretInfo, getSecretPlaceholder, type ConfigSecretsMap } from "@/lib/config-secrets";
import { Chip, RowButton, StatusPill } from "@/components/ui/data-page";
import { StatusGlyph } from "@/components/chat/tool-ui";
import type { RowState } from "@/lib/tool-state";
import {
    EMOJI_USAGE,
    TONES,
    VERBOSITIES,
    createStyleOverrideRow,
    createValueOverrideRow,
    type StyleOverrideRow,
    type ValueOverrideRow,
} from "./channel-config";

/** A titled block inside a channel tab. Hairline frame, no card-in-card. */
export function Panel({ title, description, icon, children, className }: { title: string; description?: string; icon?: ReactNode; children: ReactNode; className?: string }) {
    return (
        <section className={cn("rounded-xl border border-border/60 bg-card/50", className)}>
            <div className="border-b border-border/50 px-4 py-3">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                    {icon}
                    {title}
                </h2>
                {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
            </div>
            <div className="space-y-4 p-4">{children}</div>
        </section>
    );
}

export function Field({ label, htmlFor, hint, children, className }: { label: string; htmlFor?: string; hint?: string; children: ReactNode; className?: string }) {
    return (
        <div className={cn("space-y-1.5", className)}>
            <Label htmlFor={htmlFor} className="text-xs font-medium text-muted-foreground">
                {label}
            </Label>
            {children}
            {hint && <p className="text-[11px] leading-snug text-muted-foreground/80">{hint}</p>}
        </div>
    );
}

export function ToggleRow({ id, label, description, checked, onChange }: { id: string; label: string; description: string; checked: boolean; onChange: (checked: boolean) => void }) {
    return (
        <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
                <Label htmlFor={id} className="text-sm font-medium">
                    {label}
                </Label>
                <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
            </div>
            <Switch id={id} checked={checked} onCheckedChange={onChange} />
        </div>
    );
}

export function SelectField({
    label,
    value,
    onChange,
    options,
    className,
}: {
    label: string;
    value: string;
    onChange: (value: string) => void;
    options: Array<{ value: string; label: string }>;
    className?: string;
}) {
    return (
        <Field label={label} className={className}>
            <Select value={value} onValueChange={onChange}>
                <SelectTrigger className="h-9" aria-label={label}>
                    <SelectValue />
                </SelectTrigger>
                <SelectContent>
                    {options.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                            {option.label}
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>
        </Field>
    );
}

/** "Show advanced" row. Looks like a section divider, not a dashed mega-button. */
export function AdvancedToggle({ open, onToggle, label, summary, disabled }: { open: boolean; onToggle: () => void; label: string; summary: string; disabled?: boolean }) {
    return (
        <button
            type="button"
            onClick={onToggle}
            disabled={disabled}
            aria-expanded={open}
            className="flex w-full items-center gap-2 rounded-lg px-1 py-1.5 text-left text-sm font-medium text-foreground transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
            <ChevronRight className={cn("h-4 w-4 text-muted-foreground transition-transform", open && "rotate-90")} />
            {label}
            <span className="ml-auto text-xs font-normal text-muted-foreground">{summary}</span>
        </button>
    );
}

/** A token or other secret. Shows whether one is stored; clearing is staged until Save. */
export function SecretField({
    id,
    label,
    placeholder,
    meta,
    value,
    clearing,
    onChange,
    onClear,
    onUndoClear,
}: {
    id: string;
    label: string;
    placeholder: string;
    meta: ConfigSecretsMap;
    value: string;
    clearing: boolean;
    onChange: (value: string) => void;
    onClear: () => void;
    onUndoClear: () => void;
}) {
    const info = getSecretInfo(meta, id);
    const configured = !clearing && (!!value.trim() || info.configured);
    const state: RowState = clearing ? "waiting" : configured ? "done" : "planned";
    const stateLabel = clearing ? "Clears on save" : value.trim() ? "Will save" : configured ? "Stored" : "Not set";
    return (
        <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
                <Label htmlFor={id.toLowerCase()} className="text-xs font-medium text-muted-foreground">
                    {label}
                </Label>
                <StatusPill state={state} label={stateLabel} />
            </div>
            <div className="flex gap-2">
                <Input
                    id={id.toLowerCase()}
                    type="password"
                    autoComplete="off"
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    placeholder={getSecretPlaceholder(meta, id, placeholder)}
                    className="h-9 font-mono text-sm"
                />
                {clearing ? (
                    <Button type="button" variant="outline" size="sm" className="h-9 gap-1.5 px-3 text-xs" onClick={onUndoClear}>
                        <Undo2 className="h-3.5 w-3.5" /> Undo
                    </Button>
                ) : (
                    <Button type="button" variant="outline" size="icon" className="h-9 w-9 shrink-0" onClick={onClear} title={`Clear ${label}`} aria-label={`Clear ${label}`} disabled={!configured}>
                        <Trash2 className="h-4 w-4" />
                    </Button>
                )}
            </div>
            {info.configured && !clearing && <p className="text-[11px] text-muted-foreground">Type a new value to replace the stored one.</p>}
        </div>
    );
}

/** One-line summary of who can reach the bot. */
export function AccessSummary({ items }: { items: Array<{ label: string; restricted: boolean }> }) {
    return (
        <div className="flex flex-wrap items-center gap-1.5">
            {items.map((item) => (
                <Chip key={item.label} tone={item.restricted ? "amber" : "muted"}>
                    {item.label}
                </Chip>
            ))}
        </div>
    );
}

/** Success clears itself; errors stay until dismissed. */
export function StatusBanner({ status, onDismiss }: { status: { type: "success" | "error"; message: string } | null; onDismiss: () => void }) {
    useEffect(() => {
        if (status?.type !== "success") return;
        const timer = window.setTimeout(onDismiss, 4500);
        return () => window.clearTimeout(timer);
    }, [status, onDismiss]);
    if (!status) return null;
    const error = status.type === "error";
    return (
        <div
            role={error ? "alert" : "status"}
            className={cn(
                "flex items-center gap-2.5 rounded-lg border-l-2 py-2 pl-3 pr-2 text-xs",
                error ? "border-red-400/70 bg-red-500/[0.06]" : "border-primary/60 bg-primary/[0.06]"
            )}
        >
            {error ? <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-red-500" /> : <StatusGlyph state="done" />}
            <span className="min-w-0 flex-1 text-foreground">{status.message}</span>
            <RowButton onClick={onDismiss} aria-label="Dismiss" className="w-7 justify-center px-0">
                <X />
            </RowButton>
        </div>
    );
}

function RowShell({ children, onRemove, removeLabel }: { children: ReactNode; onRemove: () => void; removeLabel: string }) {
    return (
        <div className="relative rounded-lg border border-border/60 bg-background/40 p-3 pr-10">
            {children}
            <RowButton tone="danger" onClick={onRemove} aria-label={removeLabel} title={removeLabel} className="absolute right-1.5 top-1.5 w-7 justify-center px-0">
                <Trash2 />
            </RowButton>
        </div>
    );
}

function EditorHeader({ title, hint, addLabel, onAdd }: { title: string; hint: string; addLabel: string; onAdd: () => void }) {
    return (
        <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
                <Label className="text-xs font-semibold text-foreground">{title}</Label>
                <p className="mt-0.5 text-[11px] text-muted-foreground">{hint}</p>
            </div>
            <Button type="button" variant="outline" size="sm" className="h-7 shrink-0 gap-1 px-2 text-[11px]" onClick={onAdd}>
                <Plus className="h-3 w-3" /> {addLabel}
            </Button>
        </div>
    );
}

const EmptyLine = ({ children }: { children: ReactNode }) => <p className="rounded-lg border border-dashed border-border/70 px-3 py-3 text-center text-xs text-muted-foreground">{children}</p>;

/** Rows of per-guild or per-channel style rules. */
export function StyleOverrideEditor({
    title,
    hint,
    addLabel,
    targetPlaceholder,
    emptyText,
    rows,
    setRows,
}: {
    title: string;
    hint: string;
    addLabel: string;
    targetPlaceholder: string;
    emptyText: string;
    rows: StyleOverrideRow[];
    setRows: (updater: (rows: StyleOverrideRow[]) => StyleOverrideRow[]) => void;
}) {
    const patch = (id: string, change: Partial<StyleOverrideRow>) => setRows((prev) => prev.map((item) => (item.id === id ? { ...item, ...change } : item)));
    return (
        <div className="space-y-2.5">
            <EditorHeader title={title} hint={hint} addLabel={addLabel} onAdd={() => setRows((prev) => [...prev, createStyleOverrideRow()])} />
            {rows.length === 0 && <EmptyLine>{emptyText}</EmptyLine>}
            {rows.map((row) => (
                <RowShell key={row.id} removeLabel="Remove rule" onRemove={() => setRows((prev) => prev.filter((item) => item.id !== row.id))}>
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                        <Input value={row.target} onChange={(e) => patch(row.id, { target: e.target.value })} placeholder={targetPlaceholder} aria-label={targetPlaceholder} className="h-9 font-mono text-sm sm:col-span-2 lg:col-span-1" />
                        <SelectCompact value={row.tone} onChange={(value) => patch(row.id, { tone: value })} options={TONES} label="Tone" />
                        <SelectCompact value={row.verbosity} onChange={(value) => patch(row.id, { verbosity: value })} options={VERBOSITIES} label="Verbosity" />
                        <SelectCompact value={row.emoji_usage} onChange={(value) => patch(row.id, { emoji_usage: value })} options={EMOJI_USAGE} label="Emoji usage" />
                        <Input value={row.signature} onChange={(e) => patch(row.id, { signature: e.target.value })} placeholder="Signature override" aria-label="Signature override" className="h-9 sm:col-span-2" />
                        <Input value={row.prefix} onChange={(e) => patch(row.id, { prefix: e.target.value })} placeholder="Prefix" aria-label="Prefix" className="h-9" />
                        <Input value={row.max_length} onChange={(e) => patch(row.id, { max_length: e.target.value })} placeholder="Max length" aria-label="Max length" type="number" min="1" className="h-9" />
                    </div>
                </RowShell>
            ))}
        </div>
    );
}

function SelectCompact({ value, onChange, options, label }: { value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }>; label: string }) {
    return (
        <Select value={value} onValueChange={onChange}>
            <SelectTrigger className="h-9" aria-label={label}>
                <SelectValue />
            </SelectTrigger>
            <SelectContent>
                {options.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                        {option.label}
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
}

/** Rows of "guild id -> value" pairs (nicknames, embed colours). */
export function ValueOverrideEditor({
    title,
    hint,
    addLabel,
    valuePlaceholder,
    emptyText,
    rows,
    setRows,
}: {
    title: string;
    hint: string;
    addLabel: string;
    valuePlaceholder: string;
    emptyText: string;
    rows: ValueOverrideRow[];
    setRows: (updater: (rows: ValueOverrideRow[]) => ValueOverrideRow[]) => void;
}) {
    const patch = (id: string, change: Partial<ValueOverrideRow>) => setRows((prev) => prev.map((item) => (item.id === id ? { ...item, ...change } : item)));
    return (
        <div className="space-y-2.5">
            <EditorHeader title={title} hint={hint} addLabel={addLabel} onAdd={() => setRows((prev) => [...prev, createValueOverrideRow()])} />
            {rows.length === 0 && <EmptyLine>{emptyText}</EmptyLine>}
            {rows.map((row) => (
                <RowShell key={row.id} removeLabel="Remove override" onRemove={() => setRows((prev) => prev.filter((item) => item.id !== row.id))}>
                    <div className="grid gap-2 sm:grid-cols-[minmax(0,0.9fr)_minmax(0,1.2fr)]">
                        <Input value={row.target} onChange={(e) => patch(row.id, { target: e.target.value })} placeholder="Guild ID" aria-label="Guild ID" className="h-9 font-mono text-sm" />
                        <Input value={row.value} onChange={(e) => patch(row.id, { value: e.target.value })} placeholder={valuePlaceholder} aria-label={valuePlaceholder} className="h-9" />
                    </div>
                </RowShell>
            ))}
        </div>
    );
}
