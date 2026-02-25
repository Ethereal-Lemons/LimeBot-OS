import { useState, useEffect } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import {
    Card,
    CardHeader,
    CardContent,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
    Plus,
    Save,
    Trash2,
    RefreshCcw,
    Cpu,
    Circle,
    Settings2,
    Terminal,
    Globe,
    AlertCircle
} from "lucide-react";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
    DialogClose,
} from "@/components/ui/dialog";
import {
    Tabs,
    TabsContent,
    TabsList,
    TabsTrigger,
} from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

interface McpServerConfig {
    command: string;
    args: string[];
    env: Record<string, string>;
}

interface McpConfig {
    mcpServers: Record<string, McpServerConfig>;
}

interface JsonFieldProps {
    label: string;
    icon: any;
    value: any;
    onChange: (val: any) => void;
    placeholder?: string;
    isArray?: boolean;
}

function JsonField({ label, icon: Icon, value, onChange, placeholder, isArray }: JsonFieldProps) {
    const [localValue, setLocalValue] = useState(JSON.stringify(value, null, 2));
    const [isValid, setIsValid] = useState(true);

    useEffect(() => {
        // Sync with external value if it changes independently (e.g. on load or save)
        const currentString = JSON.stringify(value, null, 2);
        if (currentString !== localValue && isValid) {
            setLocalValue(currentString);
        }
    }, [value]);

    const handleChange = (val: string) => {
        setLocalValue(val);
        try {
            const parsed = JSON.parse(val);
            const typeMatch = isArray ? Array.isArray(parsed) : (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed));

            if (typeMatch) {
                setIsValid(true);
                onChange(parsed);
            } else {
                setIsValid(false);
            }
        } catch (e) {
            setIsValid(false);
        }
    };

    return (
        <div className="space-y-2">
            <Label className={cn("text-xs font-semibold flex items-center justify-between gap-2", !isValid && "text-destructive")}>
                <span className="flex items-center gap-2">
                    <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                    {label}
                </span>
                {!isValid && <span className="text-[10px] font-medium uppercase tracking-wider animate-pulse">Invalid JSON</span>}
            </Label>
            <Textarea
                value={localValue}
                onChange={(e) => handleChange(e.target.value)}
                className={cn(
                    "font-mono text-sm min-h-[140px] resize-none transition-colors",
                    !isValid ? "border-destructive/50 focus-visible:ring-destructive/20 bg-destructive/5" : "bg-muted/30"
                )}
                placeholder={placeholder}
            />
        </div>
    );
}

export function McpPage() {
    const [config, setConfig] = useState<McpConfig>({ mcpServers: {} });
    const [statuses, setStatuses] = useState<Record<string, string>>({});
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
    const [newServerName, setNewServerName] = useState("");
    const [hasChanges, setHasChanges] = useState(false);

    const fetchData = async () => {
        setIsLoading(true);
        try {
            const [configRes, statusRes] = await Promise.all([
                axios.get(`${API_BASE_URL}/api/mcp/config`),
                axios.get(`${API_BASE_URL}/api/mcp/status`)
            ]);
            setConfig(configRes.data);
            setStatuses(statusRes.data.status || {});
            setHasChanges(false);
        } catch (error) {
            console.error("Error fetching MCP data:", error);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        fetchData();
    }, []);

    const handleSave = async () => {
        setIsSaving(true);
        try {
            await axios.post(`${API_BASE_URL}/api/mcp/config`, config);
            setHasChanges(false);
            setTimeout(fetchData, 2000);
        } catch (error) {
            console.error("Error saving MCP config:", error);
        } finally {
            setIsSaving(false);
        }
    };

    const addServer = () => {
        if (!newServerName.trim()) return;
        if (config.mcpServers[newServerName]) {
            alert("A server with this name already exists.");
            return;
        }

        setConfig({
            ...config,
            mcpServers: {
                ...config.mcpServers,
                [newServerName]: {
                    command: "python",
                    args: [],
                    env: {}
                }
            }
        });
        setNewServerName("");
        setIsAddDialogOpen(false);
        setHasChanges(true);
    };

    const updateServer = (name: string, fields: Partial<McpServerConfig>) => {
        setConfig({
            ...config,
            mcpServers: {
                ...config.mcpServers,
                [name]: {
                    ...config.mcpServers[name],
                    ...fields
                }
            }
        });
        setHasChanges(true);
    };

    const removeServer = (name: string) => {
        const newServers = { ...config.mcpServers };
        delete newServers[name];
        setConfig({ ...config, mcpServers: newServers });
        setHasChanges(true);
    };

    if (isLoading && Object.keys(config.mcpServers).length === 0) {
        return (
            <div className="flex flex-col items-center justify-center p-24 space-y-4">
                <RefreshCcw className="h-10 w-10 animate-spin text-muted-foreground/50" />
                <p className="text-muted-foreground font-medium">Loading MCP Configuration...</p>
            </div>
        );
    }

    return (
        <div className="flex-1 space-y-8 pt-4 pb-12 animate-in fade-in duration-500">
            {/* Header Section */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 px-2">
                <div className="space-y-1">
                    <div className="flex items-center gap-2">
                        <Cpu className="h-5 w-5 text-primary" />
                        <h2 className="text-2xl font-bold tracking-tight">
                            MCP Servers
                        </h2>
                    </div>
                    <p className="text-muted-foreground text-sm">
                        Extend LimeBot with external tools and models.
                    </p>
                </div>

                <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={fetchData} disabled={isLoading}>
                        <RefreshCcw className={cn("h-4 w-4 mr-2", isLoading && "animate-spin")} />
                        Refresh
                    </Button>

                    <Dialog open={isAddDialogOpen} onOpenChange={setIsAddDialogOpen}>
                        <DialogTrigger asChild>
                            <Button size="sm">
                                <Plus className="h-4 w-4 mr-2" />
                                Add Server
                            </Button>
                        </DialogTrigger>
                        <DialogContent>
                            <DialogHeader>
                                <DialogTitle className="tracking-tight">Connect New Server</DialogTitle>
                                <DialogDescription>
                                    Enter a descriptive name for this MCP server.
                                </DialogDescription>
                            </DialogHeader>
                            <div className="py-2 space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="name">Server Name</Label>
                                    <Input
                                        id="name"
                                        placeholder="e.g. storage-mcp"
                                        value={newServerName}
                                        onChange={(e) => setNewServerName(e.target.value)}
                                        onKeyDown={(e) => e.key === 'Enter' && addServer()}
                                        autoFocus
                                    />
                                </div>
                            </div>
                            <DialogFooter>
                                <DialogClose asChild>
                                    <Button variant="ghost">Cancel</Button>
                                </DialogClose>
                                <Button onClick={addServer} disabled={!newServerName.trim()}>Create</Button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>

                    <Button
                        size="sm"
                        onClick={handleSave}
                        disabled={isSaving || !hasChanges}
                        className="relative"
                    >
                        <Save className="h-4 w-4 mr-2" />
                        {isSaving ? "Saving..." : "Save Changes"}
                        {hasChanges && (
                            <span className="absolute -top-1 -right-1 flex h-3 w-3">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
                                <span className="relative inline-flex rounded-full h-3 w-3 bg-primary border-2 border-background"></span>
                            </span>
                        )}
                    </Button>
                </div>
            </div>

            {/* Servers List */}
            <div className="space-y-6">
                {Object.keys(config.mcpServers).length === 0 ? (
                    <Card className="border-dashed bg-muted/20 flex flex-col items-center justify-center p-16 text-center rounded-xl">
                        <Cpu className="h-12 w-12 text-muted-foreground/30 mb-4" />
                        <h3 className="font-semibold text-xl mb-1">No servers connected</h3>
                        <p className="text-muted-foreground text-sm max-w-sm mb-6">
                            Connect external Model Context Protocol servers to provide the bot with additional capabilities.
                        </p>
                        <Button variant="outline" onClick={() => setIsAddDialogOpen(true)}>
                            <Plus className="h-4 w-4 mr-2" />
                            Connect Your First Server
                        </Button>
                    </Card>
                ) : (
                    <div className="grid grid-cols-1 gap-6">
                        {Object.entries(config.mcpServers).map(([name, cfg]: [string, McpServerConfig]) => {
                            const isOnline = statuses[name] === "Online";
                            return (
                                <Card key={name} className="overflow-hidden border bg-card shadow-sm hover:shadow-md transition-shadow">
                                    <CardHeader className="flex flex-row items-center justify-between py-4 bg-muted/30">
                                        <div className="flex items-center gap-3">
                                            <div className="font-bold text-lg tracking-tight px-1 uppercase">
                                                {name}
                                            </div>
                                            <Badge
                                                variant={isOnline ? "outline" : "secondary"}
                                                className={cn(
                                                    "flex gap-1.5 items-center px-2 py-0.5 text-[10px] font-bold uppercase",
                                                    isOnline && "text-green-500 border-green-500/20 bg-green-500/10"
                                                )}
                                            >
                                                <Circle className={cn(
                                                    "h-2 w-2 fill-current",
                                                    isOnline ? 'animate-pulse' : 'text-muted-foreground/30'
                                                )} />
                                                {statuses[name] || "Offline"}
                                            </Badge>
                                        </div>

                                        <Dialog>
                                            <DialogTrigger asChild>
                                                <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-destructive">
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            </DialogTrigger>
                                            <DialogContent>
                                                <DialogHeader>
                                                    <DialogTitle>Delete Connection?</DialogTitle>
                                                    <DialogDescription>
                                                        Are you sure you want to remove the <span className="font-bold text-foreground">{name}</span> MCP server?
                                                    </DialogDescription>
                                                </DialogHeader>
                                                <DialogFooter>
                                                    <DialogClose asChild>
                                                        <Button variant="ghost">Cancel</Button>
                                                    </DialogClose>
                                                    <Button variant="destructive" onClick={() => removeServer(name)}>Remove</Button>
                                                </DialogFooter>
                                            </DialogContent>
                                        </Dialog>
                                    </CardHeader>

                                    <CardContent className="p-0">
                                        <Tabs defaultValue="general" className="w-full">
                                            <div className="px-6 border-b">
                                                <TabsList className="h-10 bg-transparent gap-6 p-0">
                                                    <TabsTrigger
                                                        value="general"
                                                        className="h-10 rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent shadow-none px-1 text-xs font-semibold"
                                                    >
                                                        General
                                                    </TabsTrigger>
                                                    <TabsTrigger
                                                        value="args"
                                                        className="h-10 rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent shadow-none px-1 text-xs font-semibold"
                                                    >
                                                        Arguments
                                                    </TabsTrigger>
                                                    <TabsTrigger
                                                        value="env"
                                                        className="h-10 rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent shadow-none px-1 text-xs font-semibold"
                                                    >
                                                        Environment
                                                    </TabsTrigger>
                                                </TabsList>
                                            </div>

                                            <div className="p-6">
                                                <TabsContent value="general" className="mt-0 space-y-4">
                                                    <div className="space-y-2">
                                                        <Label className="text-xs font-semibold flex items-center gap-2">
                                                            <Terminal className="h-3.5 w-3.5 text-muted-foreground" />
                                                            Command
                                                        </Label>
                                                        <Input
                                                            value={cfg.command}
                                                            onChange={(e) => updateServer(name, { command: e.target.value })}
                                                            className="font-mono text-sm"
                                                            placeholder="e.g. npx, python, node"
                                                        />
                                                        <p className="text-[11px] text-muted-foreground">The executable used to run the MCP server.</p>
                                                    </div>
                                                </TabsContent>

                                                <TabsContent value="args" className="mt-0 space-y-4">
                                                    <JsonField
                                                        label="Arguments (JSON Array)"
                                                        icon={Settings2}
                                                        value={cfg.args}
                                                        isArray
                                                        onChange={(val) => updateServer(name, { args: val })}
                                                        placeholder='["--some-arg", "value"]'
                                                    />
                                                </TabsContent>

                                                <TabsContent value="env" className="mt-0 space-y-4">
                                                    <JsonField
                                                        label="Environment Variables (JSON)"
                                                        icon={Globe}
                                                        value={cfg.env}
                                                        onChange={(val) => updateServer(name, { env: val })}
                                                        placeholder='{"API_KEY": "..."}'
                                                    />
                                                </TabsContent>
                                            </div>
                                        </Tabs>
                                    </CardContent>
                                </Card>
                            );
                        })}
                    </div>
                )}
            </div>

            {/* Information Card */}
            <Card className="bg-muted/10 border-muted">
                <CardContent className="p-6">
                    <div className="flex gap-4 items-start">
                        <AlertCircle className="h-5 w-5 text-muted-foreground mt-0.5 shrink-0" />
                        <div className="text-sm space-y-3">
                            <h4 className="font-bold flex items-center gap-2">
                                Model Context Protocol
                            </h4>
                            <p className="text-muted-foreground leading-relaxed">
                                MCP is a standard for connecting external tool servers to AI agents.
                                Once a server is connected, LimeBot automatically discovers its available tools and makes them accessible in the toolbox.
                                Connected tools will appear with an <code className="bg-muted px-1 rounded text-xs">mcp_</code> prefix during chat.
                            </p>
                            <div className="pt-1">
                                <a
                                    href="https://modelcontextprotocol.io"
                                    target="_blank"
                                    rel="noreferrer"
                                    className="text-primary hover:underline font-semibold"
                                >
                                    Learn more about MCP &rarr;
                                </a>
                            </div>
                        </div>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
