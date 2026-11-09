import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import axios, { AxiosError, type AxiosResponse } from "axios";
import { API_BASE_URL } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
    Plus,
    Trash2,
    RefreshCcw,
    Cpu,
    ChevronRight,
} from "lucide-react";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogClose,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { classifySaveError, type SaveErrorInfo } from "@/lib/save-error";
import { useUnsaved, useBeforeUnloadGuard } from "@/hooks/useUnsaved";
import { useSaveState } from "@/hooks/useSaveState";
import {
    EmptyState,
    Field,
    invalidInputClass,
    LoadFailed,
    LoadingRows,
    Note,
    SaveBar,
    SaveErrorNote,
    SettingsPage,
    SettingsSection,
    StatusPill,
} from "@/components/settings/kit";

interface McpServerConfig {
    command?: string;
    args?: string[];
    env?: Record<string, string>;
    // HTTP/SSE servers are recorded by the backend; keep those fields untouched.
    url?: string;
    type?: string;
}

interface McpConfig {
    mcpServers: Record<string, McpServerConfig>;
}

type ArgsOrEnv = string[] | Record<string, string>;

function isStringArray(value: unknown): value is string[] {
    return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isStringRecord(value: unknown): value is Record<string, string> {
    return typeof value === "object"
        && value !== null
        && !Array.isArray(value)
        && Object.values(value).every((item) => typeof item === "string");
}

/** The backend answers 200 with `{error}` for a rejected config; surface that as a failed save. */
function rejectedByServer(message: string): AxiosError {
    const response = { data: { detail: message }, status: 422, statusText: "Unprocessable", headers: {}, config: {} } as AxiosResponse;
    return new AxiosError(message, "ERR_BAD_REQUEST", undefined, undefined, response);
}

// Same rule the backend applies when it validates the file.
function isHttpServer(cfg: McpServerConfig): boolean {
    const transport = String(cfg.type ?? "").trim().toLowerCase();
    return transport === "http" || transport === "sse" || Boolean(cfg.url?.trim());
}

function serverProblem(cfg: McpServerConfig): string | null {
    if (isHttpServer(cfg)) {
        return cfg.url?.trim() ? null : "A URL is required for HTTP servers.";
    }
    return cfg.command?.trim() ? null : "A command is required, for example npx, python or node.";
}

function serverSummary(cfg: McpServerConfig): string {
    if (isHttpServer(cfg)) return cfg.url?.trim() || "No URL";
    const parts = [cfg.command?.trim(), ...(cfg.args ?? [])].filter(Boolean);
    return parts.length ? parts.join(" ") : "No command";
}

function statusInfo(raw: string | undefined, isNew: boolean): { tone: "ok" | "bad" | "muted" | "info"; label: string; dot: string; hint?: string } {
    if (isNew) return { tone: "info", label: "Not saved yet", dot: "bg-muted-foreground/40", hint: "Save changes to connect this server." };
    if (raw === "Online") return { tone: "ok", label: "Connected", dot: "bg-primary" };
    if (raw === "Error") {
        return {
            tone: "bad",
            label: "Error",
            dot: "bg-red-500",
            hint: "The server did not start or stopped answering. System Logs has the details.",
        };
    }
    return { tone: "muted", label: raw || "Offline", dot: "bg-muted-foreground/40" };
}

interface JsonFieldProps {
    id: string;
    label: string;
    hint: string;
    shapeMessage: string;
    value: ArgsOrEnv;
    validate: (value: unknown) => value is ArgsOrEnv;
    onChange: (val: ArgsOrEnv) => void;
    onValidity: (valid: boolean) => void;
    placeholder?: string;
}

function JsonField({ id, label, hint, shapeMessage, value, validate, onChange, onValidity, placeholder }: JsonFieldProps) {
    const [draft, setDraft] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const text = draft ?? JSON.stringify(value, null, 2);

    // A collapsed row drops its draft, so it must also stop blocking Save.
    const validityRef = useRef(onValidity);
    useEffect(() => {
        validityRef.current = onValidity;
    });
    useEffect(() => () => validityRef.current(true), []);

    const handleChange = (val: string) => {
        setDraft(val);
        let parsed: unknown;
        try {
            parsed = JSON.parse(val);
        } catch (parseError) {
            setError(`Not valid JSON yet${parseError instanceof Error ? `: ${parseError.message}` : "."}`);
            onValidity(false);
            return;
        }
        if (!validate(parsed)) {
            setError(shapeMessage);
            onValidity(false);
            return;
        }
        setError(null);
        onValidity(true);
        onChange(parsed);
    };

    return (
        <Field label={label} htmlFor={id} hint={hint} error={error}>
            <Textarea
                id={id}
                value={text}
                onChange={(e) => handleChange(e.target.value)}
                onBlur={() => {
                    if (!error) setDraft(null);
                }}
                aria-invalid={Boolean(error)}
                spellCheck={false}
                className={cn("min-h-[96px] resize-y font-mono text-xs", error && invalidInputClass)}
                placeholder={placeholder}
            />
        </Field>
    );
}

export function McpPage() {
    const [config, setConfig] = useState<McpConfig>({ mcpServers: {} });
    const [statuses, setStatuses] = useState<Record<string, string>>({});
    const [isLoading, setIsLoading] = useState(true);
    const [loadError, setLoadError] = useState<unknown>(null);
    const [expanded, setExpanded] = useState<Set<string>>(new Set());
    const [invalidFields, setInvalidFields] = useState<Record<string, true>>({});
    const [resetNonce, setResetNonce] = useState(0);
    const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
    const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
    const [newServerName, setNewServerName] = useState("");
    const [newServerCommand, setNewServerCommand] = useState("python");

    const { dirty, markSaved, restore, ready } = useUnsaved(config);
    const { saving, error: saveError, savedAt, run, clearError } = useSaveState("your MCP server settings");
    useBeforeUnloadGuard(dirty);

    const statusTimer = useRef<number | undefined>(undefined);
    useEffect(() => () => window.clearTimeout(statusTimer.current), []);

    const fetchData = useCallback(async () => {
        setIsLoading(true);
        try {
            const [configRes, statusRes] = await Promise.all([
                axios.get(`${API_BASE_URL}/api/mcp/config`),
                axios.get(`${API_BASE_URL}/api/mcp/status`)
            ]);
            const next: McpConfig = { ...configRes.data, mcpServers: configRes.data?.mcpServers ?? {} };
            setConfig(next);
            markSaved(next);
            setStatuses(statusRes.data.status || {});
            setInvalidFields({});
            setResetNonce((value) => value + 1);
            setLoadError(null);
        } catch (error) {
            console.error("Error fetching MCP data:", error);
            setLoadError(error);
        } finally {
            setIsLoading(false);
        }
    }, [markSaved]);

    const refreshStatuses = useCallback(async () => {
        try {
            const statusRes = await axios.get(`${API_BASE_URL}/api/mcp/status`);
            setStatuses(statusRes.data.status || {});
            setLoadError(null);
        } catch (error) {
            console.error("Error fetching MCP status:", error);
            setLoadError(error);
        }
    }, []);

    useEffect(() => {
        void fetchData();
    }, [fetchData]);

    const handleSave = async () => {
        const snapshot = config;
        const ok = await run(async () => {
            const res = await axios.post(`${API_BASE_URL}/api/mcp/config`, snapshot);
            if (res.data?.error) throw rejectedByServer(String(res.data.error));
        });
        if (ok) {
            markSaved(snapshot);
            window.clearTimeout(statusTimer.current);
            statusTimer.current = window.setTimeout(() => void refreshStatuses(), 2000);
            toast.success("MCP configuration saved");
        }
    };

    const handleDiscard = () => {
        const saved = restore();
        if (saved) {
            setConfig(saved);
            setInvalidFields({});
            setResetNonce((value) => value + 1);
        }
        clearError();
    };

    const handleRefresh = () => {
        // Unsaved edits are never replaced by a refresh; only live status updates.
        if (dirty) void refreshStatuses();
        else void fetchData();
    };

    const newNameError = useMemo(() => {
        const name = newServerName.trim();
        if (name && config.mcpServers[name]) return `A server named "${name}" already exists.`;
        return null;
    }, [newServerName, config.mcpServers]);
    const newCommandError = newServerCommand.trim() ? null : "A command is required, for example npx, python or node.";

    const openAddDialog = () => {
        setNewServerName("");
        setNewServerCommand("python");
        setIsAddDialogOpen(true);
    };

    const addServer = () => {
        const serverName = newServerName.trim();
        if (!serverName || newNameError || newCommandError) return;

        setConfig({
            ...config,
            mcpServers: {
                ...config.mcpServers,
                [serverName]: {
                    command: newServerCommand.trim(),
                    args: [],
                    env: {}
                }
            }
        });
        setExpanded((prev) => new Set(prev).add(serverName));
        setNewServerName("");
        setIsAddDialogOpen(false);
        toast.success("Server added to MCP configuration");
    };

    const updateServer = (name: string, fields: Partial<McpServerConfig>) => {
        setConfig((prev) => ({
            ...prev,
            mcpServers: {
                ...prev.mcpServers,
                [name]: {
                    ...prev.mcpServers[name],
                    ...fields
                }
            }
        }));
    };

    const removeServer = (name: string) => {
        const newServers = { ...config.mcpServers };
        delete newServers[name];
        setConfig({ ...config, mcpServers: newServers });
        setExpanded((prev) => {
            const next = new Set(prev);
            next.delete(name);
            return next;
        });
        setInvalidFields((prev) => Object.fromEntries(Object.entries(prev).filter(([key]) => !key.startsWith(`${name}\u0000`))) as Record<string, true>);
        setDeleteTarget(null);
    };

    const setFieldValidity = useCallback((name: string, field: string, valid: boolean) => {
        const key = `${name}\u0000${field}`;
        setInvalidFields((prev) => {
            if (valid && !prev[key]) return prev;
            if (!valid && prev[key]) return prev;
            if (valid) {
                const next = { ...prev };
                delete next[key];
                return next;
            }
            return { ...prev, [key]: true };
        });
    }, []);

    const toggleExpanded = (name: string) => {
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(name)) next.delete(name);
            else next.add(name);
            return next;
        });
    };

    const serverEntries = Object.entries(config.mcpServers);
    const savedServers = restore()?.mcpServers ?? {};
    const hasInvalid = Object.keys(invalidFields).length > 0 || serverEntries.some(([, cfg]) => serverProblem(cfg) !== null);
    const changeCount = useMemo(() => {
        const saved = restore()?.mcpServers ?? {};
        const names = new Set([...Object.keys(saved), ...Object.keys(config.mcpServers)]);
        return [...names].filter((name) => JSON.stringify(saved[name]) !== JSON.stringify(config.mcpServers[name])).length;
    }, [config.mcpServers, restore]);

    const pageProps = {
        title: "MCP Servers",
        description: "Extend LimeBot with external tools and models.",
        icon: Cpu,
    };

    if (isLoading && !ready) {
        return (
            <SettingsPage {...pageProps}>
                <LoadingRows rows={4} className="py-8" label="Loading MCP servers" />
            </SettingsPage>
        );
    }

    if (loadError && !ready) {
        return (
            <SettingsPage {...pageProps}>
                <LoadFailed error={loadError} subject="your MCP servers" onRetry={() => void fetchData()} />
            </SettingsPage>
        );
    }

    const refreshError: SaveErrorInfo | null = loadError ? classifySaveError(loadError, "your MCP servers") : null;

    return (
        <SettingsPage
            {...pageProps}
            actions={
                <>
                    <Button variant="outline" size="sm" onClick={handleRefresh} disabled={isLoading} className="gap-1.5" title={dirty ? "Refreshes live status; your unsaved edits stay" : "Reload servers and status"}>
                        <RefreshCcw className={cn("h-3.5 w-3.5", isLoading && "animate-spin")} />
                        Refresh
                    </Button>
                    <Button size="sm" onClick={openAddDialog} className="gap-1.5">
                        <Plus className="h-3.5 w-3.5" />
                        Add server
                    </Button>
                </>
            }
            saveBar={
                <SaveBar
                    dirty={dirty}
                    saving={saving}
                    onSave={() => void handleSave()}
                    onDiscard={handleDiscard}
                    error={saveError}
                    onDismissError={clearError}
                    disabled={hasInvalid}
                    savedAt={savedAt}
                    changeCount={changeCount}
                    saveLabel="Save servers"
                    unsavedLabel={hasInvalid ? "Fix the highlighted fields to save" : undefined}
                />
            }
        >
            {refreshError && (
                <SaveErrorNote
                    error={{ ...refreshError, title: refreshError.category === "network" ? "Could not refresh MCP status" : refreshError.title }}
                    onRetry={() => void refreshStatuses()}
                    onDismiss={() => setLoadError(null)}
                    className="mt-4"
                />
            )}

            <SettingsSection
                title="Servers"
                description="Each server runs as its own process. Tools it exposes appear in chat with an mcp_ prefix."
            >
                {serverEntries.length === 0 ? (
                    <EmptyState
                        icon={Cpu}
                        title="No MCP servers yet"
                        description="Connect external Model Context Protocol servers to give the bot additional tools."
                        action={
                            <Button variant="outline" size="sm" onClick={openAddDialog} className="gap-1.5">
                                <Plus className="h-3.5 w-3.5" />
                                Add your first server
                            </Button>
                        }
                        className="py-10"
                    />
                ) : (
                    <ul className="divide-y divide-border/50 border-y border-border/50">
                        {serverEntries.map(([name, cfg]) => {
                            const isOpen = expanded.has(name);
                            const savedCfg = savedServers[name];
                            const status = statusInfo(statuses[name], !savedCfg);
                            const problem = serverProblem(cfg);
                            const edited = Boolean(savedCfg) && JSON.stringify(savedCfg) !== JSON.stringify(cfg);
                            const http = isHttpServer(cfg);
                            return (
                                <li key={name} className="py-2">
                                    <div className="flex items-center gap-1">
                                        <button
                                            type="button"
                                            onClick={() => toggleExpanded(name)}
                                            aria-expanded={isOpen}
                                            className="flex min-w-0 flex-1 items-center gap-3 rounded-md px-1 py-1.5 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                        >
                                            <ChevronRight className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", isOpen && "rotate-90")} />
                                            <span className={cn("h-2 w-2 shrink-0 rounded-full", status.dot)} aria-hidden />
                                            <span className="min-w-0 flex-1">
                                                <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                                                    <span className="truncate font-mono text-sm font-medium text-foreground">{name}</span>
                                                    {http && <StatusPill tone="muted">HTTP</StatusPill>}
                                                    {edited && <StatusPill tone="warn">Edited</StatusPill>}
                                                </span>
                                                <span className={cn("block truncate font-mono text-xs", problem ? "text-red-500" : "text-muted-foreground")}>
                                                    {problem ?? serverSummary(cfg)}
                                                </span>
                                            </span>
                                            <span title={status.hint} className="shrink-0">
                                                <StatusPill tone={status.tone}>{status.label}</StatusPill>
                                            </span>
                                        </button>
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            className="h-8 w-8 shrink-0 text-muted-foreground hover:text-destructive"
                                            onClick={() => setDeleteTarget(name)}
                                            aria-label={`Remove ${name}`}
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>

                                    {isOpen && (
                                        <div className="space-y-4 pb-3 pl-3 pt-3 sm:pl-10">
                                            {(!http || Boolean(cfg.command)) && (
                                                <Field
                                                    label="Command"
                                                    htmlFor={`mcp-${name}-command`}
                                                    hint="The executable used to run the MCP server."
                                                    error={!http ? problem : null}
                                                >
                                                    <Input
                                                        id={`mcp-${name}-command`}
                                                        value={cfg.command ?? ""}
                                                        onChange={(e) => updateServer(name, { command: e.target.value })}
                                                        className={cn("font-mono text-sm", !http && problem && invalidInputClass)}
                                                        placeholder="e.g. npx, python, node"
                                                        aria-invalid={!http && Boolean(problem)}
                                                        spellCheck={false}
                                                    />
                                                </Field>
                                            )}
                                            {(http || cfg.url !== undefined) && (
                                                <Field
                                                    label="URL"
                                                    htmlFor={`mcp-${name}-url`}
                                                    hint="Where the HTTP or SSE server listens."
                                                    error={http ? problem : null}
                                                >
                                                    <Input
                                                        id={`mcp-${name}-url`}
                                                        value={cfg.url ?? ""}
                                                        onChange={(e) => updateServer(name, { url: e.target.value })}
                                                        className={cn("font-mono text-sm", http && problem && invalidInputClass)}
                                                        placeholder="https://example.com/mcp"
                                                        aria-invalid={http && Boolean(problem)}
                                                        spellCheck={false}
                                                    />
                                                </Field>
                                            )}
                                            <JsonField
                                                key={`args-${name}-${resetNonce}`}
                                                id={`mcp-${name}-args`}
                                                label="Arguments"
                                                hint="A JSON array of strings."
                                                shapeMessage='Must be a JSON array of strings, for example ["--port", "7311"].'
                                                value={cfg.args ?? []}
                                                validate={isStringArray}
                                                onChange={(val) => {
                                                    if (isStringArray(val)) updateServer(name, { args: val });
                                                }}
                                                onValidity={(valid) => setFieldValidity(name, "args", valid)}
                                                placeholder='["--some-arg", "value"]'
                                            />
                                            <JsonField
                                                key={`env-${name}-${resetNonce}`}
                                                id={`mcp-${name}-env`}
                                                label="Environment variables"
                                                hint="A JSON object of names and string values."
                                                shapeMessage='Must be a JSON object whose values are all strings, for example {"API_KEY": "..."}.'
                                                value={cfg.env ?? {}}
                                                validate={isStringRecord}
                                                onChange={(val) => {
                                                    if (isStringRecord(val)) updateServer(name, { env: val });
                                                }}
                                                onValidity={(valid) => setFieldValidity(name, "env", valid)}
                                                placeholder='{"API_KEY": "..."}'
                                            />
                                        </div>
                                    )}
                                </li>
                            );
                        })}
                    </ul>
                )}
            </SettingsSection>

            <SettingsSection title="About MCP">
                <Note tone="info" title="Model Context Protocol">
                    MCP is a standard for connecting external tool servers to AI agents.
                    Once a server is connected, LimeBot automatically discovers its available tools and makes them accessible in the toolbox.
                    Connected tools will appear with an <code className="rounded bg-muted px-1 text-[11px]">mcp_</code> prefix during chat.{" "}
                    <a
                        href="https://modelcontextprotocol.io"
                        target="_blank"
                        rel="noreferrer"
                        className="font-medium text-primary hover:underline"
                    >
                        Learn more about MCP &rarr;
                    </a>
                </Note>
            </SettingsSection>

            <Dialog open={isAddDialogOpen} onOpenChange={setIsAddDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle className="tracking-tight">Connect new server</DialogTitle>
                        <DialogDescription>
                            Enter a descriptive name for this MCP server. You can add arguments and environment variables after it is created.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-2">
                        <Field label="Server name" htmlFor="mcp-new-name" error={newNameError}>
                            <Input
                                id="mcp-new-name"
                                placeholder="e.g. storage-mcp"
                                value={newServerName}
                                onChange={(e) => setNewServerName(e.target.value)}
                                onKeyDown={(e) => e.key === 'Enter' && addServer()}
                                className={cn(newNameError && invalidInputClass)}
                                aria-invalid={Boolean(newNameError)}
                                autoFocus
                            />
                        </Field>
                        <Field label="Command" htmlFor="mcp-new-command" error={newServerCommand.trim() ? null : newCommandError}>
                            <Input
                                id="mcp-new-command"
                                placeholder="e.g. npx, python, node"
                                value={newServerCommand}
                                onChange={(e) => setNewServerCommand(e.target.value)}
                                onKeyDown={(e) => e.key === 'Enter' && addServer()}
                                className={cn("font-mono text-sm", !newServerCommand.trim() && invalidInputClass)}
                                spellCheck={false}
                            />
                        </Field>
                    </div>
                    <DialogFooter>
                        <DialogClose asChild>
                            <Button variant="ghost">Cancel</Button>
                        </DialogClose>
                        <Button onClick={addServer} disabled={!newServerName.trim() || Boolean(newNameError) || Boolean(newCommandError)}>Create</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Delete connection?</DialogTitle>
                        <DialogDescription>
                            Are you sure you want to remove the <span className="font-bold text-foreground">{deleteTarget}</span> MCP server? The change applies when you save.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <DialogClose asChild>
                            <Button variant="ghost">Cancel</Button>
                        </DialogClose>
                        <Button variant="destructive" onClick={() => deleteTarget && removeServer(deleteTarget)}>Remove</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </SettingsPage>
    );
}
