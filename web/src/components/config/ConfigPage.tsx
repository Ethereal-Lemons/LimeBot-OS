import { useState, useEffect } from 'react';
import axios from 'axios';
import { API_BASE_URL } from "@/lib/api";
import { Save, Settings, Key, Cpu, RefreshCw, Globe, Server, User, Trash, Plus } from 'lucide-react';
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";

interface ConfigState {
    OPENAI_API_KEY?: string;
    GEMINI_API_KEY?: string;
    ANTHROPIC_API_KEY?: string;
    XAI_API_KEY?: string;
    DEEPSEEK_API_KEY?: string;
    MISTRAL_API_KEY?: string;
    NVIDIA_API_KEY?: string;
    DISCORD_TOKEN?: string;
    LLM_MODEL?: string;
    ALLOWED_PATHS?: string[];
    APP_API_KEY?: string;
    AUTONOMOUS_MODE?: string;
    MAX_ITERATIONS?: string;
    [key: string]: any;
}


export function ConfigPage() {
    const [config, setConfig] = useState<ConfigState>({});
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [status, setStatus] = useState<{ type: 'success' | 'error', message: string } | null>(null);

    const [availableModels, setAvailableModels] = useState<any[]>([]);

    useEffect(() => {
        fetchConfig();
        fetchModels();
    }, []);

    const fetchModels = async () => {
        try {
            const res = await axios.get(`${API_BASE_URL}/api/llm/models`);
            if (res.data.models) setAvailableModels(res.data.models);
        } catch (err) {
            console.error("Failed to load models:", err);
        }
    };

    const getFilteredModels = () => {
        if (!config) return [];
        return availableModels.filter(model => {
            if (model.provider === 'gemini') return !!config.GEMINI_API_KEY;
            if (model.provider === 'openai') return !!(config as any).OPENAI_API_KEY;
            if (model.provider === 'anthropic') return !!(config as any).ANTHROPIC_API_KEY;
            if (model.provider === 'xai') return !!(config as any).XAI_API_KEY;
            if (model.provider === 'deepseek') return !!(config as any).DEEPSEEK_API_KEY;

            if (model.provider === 'nvidia') return !!(config as any).NVIDIA_API_KEY;
            return true;
        });
    };

    const fetchConfig = async () => {
        setLoading(true);
        // ... existing fetchConfig logic ...
        try {
            const res = await axios.get(`${API_BASE_URL}/api/config`);
            if (res.data.env) setConfig(res.data.env);
        } catch (err) {
            console.error("Failed to load settings:", err);
            setStatus({ type: 'error', message: "Failed to load configuration." });
        } finally {
            setLoading(false);
        }
    };


    // ... existing handleSave ...

    const handleSave = async () => {
        setSaving(true);
        setStatus(null);

        // Validation for Custom Model
        if (!config.LLM_MODEL || config.LLM_MODEL.trim() === "") {
            setStatus({ type: 'error', message: "Model name cannot be empty. Please select a model or enter a custom one." });
            setSaving(false);
            return;
        }

        try {
            const res = await axios.post(`${API_BASE_URL}/api/config`, { env: config });
            if (res.data.error) throw new Error(res.data.error);
            setStatus({ type: 'success', message: "Configuration saved!" });
        } catch (err) {
            console.error("Failed to save config:", err);
            setStatus({ type: 'error', message: "Failed to save configuration." });
        } finally {
            setSaving(false);
        }
    };

    const handleChange = (key: string, value: any) => {
        setConfig(prev => ({ ...prev, [key]: value }));
    };

    if (loading) return <div className="p-8 text-muted-foreground">Loading settings...</div>;

    const filteredModels = getFilteredModels();

    return (
        <div className="h-full overflow-y-auto p-6 md:p-8 bg-background/50">
            <div className="max-w-4xl mx-auto space-y-8">
                <header className="flex items-center justify-between">
                    <div>
                        <h1 className="text-2xl font-bold flex items-center gap-2">
                            <Settings className="h-6 w-6 text-primary" />
                            Configuration
                        </h1>
                        <p className="text-muted-foreground mt-1">
                            Manage your bot's environment variables and model settings.
                        </p>
                    </div>
                    <Button onClick={handleSave} disabled={saving} className="bg-primary hover:bg-primary/90 text-primary-foreground font-bold">
                        {saving ? (
                            <>Saving...</>
                        ) : (
                            <>
                                <Save className="mr-2 h-4 w-4" /> Save Changes
                            </>
                        )}
                    </Button>
                </header>

                {status && (
                    <Alert className={status.type === 'success' ? "border-primary/50 bg-primary/10 text-primary" : "border-destructive/50 bg-destructive/10 text-destructive"}>
                        {status.type === 'success' ? <RefreshCw className="h-4 w-4" /> : <Settings className="h-4 w-4" />}
                        <AlertTitle>{status.type === 'success' ? "Saved" : "Error"}</AlertTitle>
                        <AlertDescription>{status.message}</AlertDescription>
                    </Alert>
                )}

                <Tabs defaultValue="general" className="w-full">
                    <TabsList className="grid w-full grid-cols-3 max-w-[400px]">
                        <TabsTrigger value="general">General</TabsTrigger>
                        <TabsTrigger value="credentials">Credentials</TabsTrigger>
                        <TabsTrigger value="system">System</TabsTrigger>
                    </TabsList>

                    {/* GENERAL SETTINGS */}
                    <TabsContent value="general" className="space-y-4 mt-6">
                        <Card>
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2">
                                    <Cpu className="h-5 w-5" />
                                    Model Settings
                                </CardTitle>
                                <CardDescription>Select and configure the AI model.</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                <div className="grid gap-2">
                                    <Label htmlFor="model">LLM Model</Label>
                                    <div className="flex gap-2">
                                        <Select
                                            value={
                                                filteredModels.find(m => m.id === config.LLM_MODEL)
                                                    ? config.LLM_MODEL
                                                    : "custom"
                                            }
                                            onValueChange={(val) => {
                                                if (val === "custom") {
                                                    handleChange("LLM_MODEL", "");
                                                } else {
                                                    handleChange("LLM_MODEL", val);
                                                }
                                            }}
                                        >
                                            <SelectTrigger id="model" className="flex-1">
                                                <SelectValue placeholder="Select Model" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="custom" className="font-semibold text-primary">
                                                    ✨ Custom / Local Model
                                                </SelectItem>
                                                {filteredModels.map((model) => (
                                                    <SelectItem key={model.id} value={model.id}>
                                                        {model.name}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>

                                    {(!filteredModels.find(m => m.id === config.LLM_MODEL) || config.LLM_MODEL?.startsWith("ollama/")) && (
                                        <div className="pt-2 animate-in fade-in slide-in-from-top-2">
                                            <Label htmlFor="custom_model" className="text-xs text-muted-foreground">Custom Model Name (e.g. ollama/llama3)</Label>
                                            <Input
                                                id="custom_model"
                                                value={config.LLM_MODEL || ""}
                                                onChange={(e) => handleChange("LLM_MODEL", e.target.value)}
                                                placeholder="provider/model-name"
                                                className="mt-1"
                                            />
                                        </div>
                                    )}
                                </div>

                                <div className="grid gap-2">
                                    <Label htmlFor="base_url">LLM Base URL (Optional)</Label>
                                    <Input
                                        id="base_url"
                                        value={config.LLM_BASE_URL || ""}
                                        onChange={(e) => handleChange("LLM_BASE_URL", e.target.value)}
                                        placeholder="e.g. http://localhost:11434"
                                        className="font-mono text-sm"
                                    />
                                    <p className="text-[10px] text-muted-foreground">
                                        Required for local models (Ollama, LocalAI). Defaults to empty for cloud providers.
                                    </p>
                                </div>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2">
                                    <User className="h-5 w-5" />
                                    Personalization
                                </CardTitle>
                                <CardDescription>Configure how the bot interacts with users.</CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="flex items-center justify-between p-4 bg-cyan-500/10 rounded-lg border border-cyan-500/20">
                                    <div className="space-y-0.5">
                                        <div className="flex items-center gap-2">
                                            <Label htmlFor="dynamic_personality">Adaptive Persona</Label>
                                            <span className="text-[10px] font-bold bg-cyan-500 text-white px-1.5 py-0.5 rounded uppercase tracking-wider">Experimental</span>
                                        </div>
                                        <p className="text-xs text-muted-foreground max-w-md">
                                            Allows the bot to learn from your conversations, adjusting its tone and relationship level dynamically based on interactions.
                                        </p>
                                    </div>
                                    <Switch
                                        id="dynamic_personality"
                                        checked={config.ENABLE_DYNAMIC_PERSONALITY === 'true'}
                                        onCheckedChange={(checked) => handleChange('ENABLE_DYNAMIC_PERSONALITY', checked ? 'true' : 'false')}
                                    />
                                </div>
                            </CardContent>
                        </Card>
                    </TabsContent>


                    {/* CREDENTIALS */}
                    <TabsContent value="credentials" className="space-y-4 mt-6">
                        <Card>
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2">
                                    <Key className="h-5 w-5" />
                                    API Keys
                                </CardTitle>
                                <CardDescription>Manage keys for AI providers and external services.</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-6">
                                <div className="grid gap-2 p-4 bg-primary/5 rounded-lg border border-primary/10">
                                    <Label htmlFor="app_api_key" className="text-foreground/90 font-semibold">LimeBot Access Key</Label>
                                    <div className="flex gap-2">
                                        <Input
                                            id="app_api_key"
                                            type="password"
                                            value={config.APP_API_KEY || ""}
                                            onChange={(e) => handleChange("APP_API_KEY", e.target.value)}
                                            placeholder="Secure Key..."
                                            className="bg-background/50 flex-1"
                                        />
                                        <Button
                                            variant="outline"
                                            size="icon"
                                            onClick={() => handleChange("APP_API_KEY", crypto.randomUUID())}
                                            title="Regenerate Key"
                                        >
                                            <RefreshCw className="h-4 w-4" />
                                        </Button>
                                    </div>
                                    <p className="text-[10px] text-muted-foreground">
                                        Used to secure the frontend connection. if changed, you must update the frontend or re-login.
                                    </p>
                                </div>

                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div className="grid gap-4 border p-4 rounded-lg bg-background/30">

                                        <div className="grid gap-2">
                                            <Label htmlFor="gemini_key">Google Gemini API Key</Label>
                                            <Input
                                                id="gemini_key"
                                                type="password"
                                                value={config.GEMINI_API_KEY || ""}
                                                onChange={(e) => handleChange("GEMINI_API_KEY", e.target.value)}
                                                placeholder="sk-..."
                                            />
                                        </div>
                                    </div>
                                    <div className="grid gap-2">
                                        <Label htmlFor="openai_key">OpenAI API Key</Label>
                                        <Input
                                            id="openai_key"
                                            type="password"
                                            value={config.OPENAI_API_KEY || ""}
                                            onChange={(e) => handleChange("OPENAI_API_KEY", e.target.value)}
                                            placeholder="sk-..."
                                        />
                                    </div>
                                    <div className="grid gap-2">
                                        <Label htmlFor="anthropic_key">Anthropic API Key</Label>
                                        <Input
                                            id="anthropic_key"
                                            type="password"
                                            value={config.ANTHROPIC_API_KEY || ""}
                                            onChange={(e) => handleChange("ANTHROPIC_API_KEY", e.target.value)}
                                            placeholder="sk-ant-..."
                                        />
                                    </div>
                                    <div className="grid gap-2">
                                        <Label htmlFor="xai_key">xAI (Grok) API Key</Label>
                                        <Input
                                            id="xai_key"
                                            type="password"
                                            value={config.XAI_API_KEY || ""}
                                            onChange={(e) => handleChange("XAI_API_KEY", e.target.value)}
                                            placeholder="xai-..."
                                        />
                                    </div>
                                    <div className="grid gap-2">
                                        <Label htmlFor="deepseek_key">DeepSeek API Key</Label>
                                        <Input
                                            id="deepseek_key"
                                            type="password"
                                            value={config.DEEPSEEK_API_KEY || ""}
                                            onChange={(e) => handleChange("DEEPSEEK_API_KEY", e.target.value)}
                                            placeholder="sk-..."
                                        />
                                    </div>

                                    <div className="grid gap-2">
                                        <Label htmlFor="nvidia_key">NVIDIA API Key</Label>
                                        <Input
                                            id="nvidia_key"
                                            type="password"
                                            value={config.NVIDIA_API_KEY || ""}
                                            onChange={(e) => handleChange("NVIDIA_API_KEY", e.target.value)}
                                            placeholder="nvapi-..."
                                        />
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    </TabsContent>

                    {/* SYSTEM SETTINGS */}
                    <TabsContent value="system" className="space-y-4 mt-6">
                        <Card>
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2">
                                    <Cpu className="h-5 w-5 text-yellow-500" />
                                    Autonomous Mode
                                </CardTitle>
                                <CardDescription className="text-yellow-500/80">
                                    DANGER: Allow the bot to execute ANY command without confirmation.
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="flex items-center justify-between p-4 bg-yellow-500/10 rounded-lg border border-yellow-500/20">
                                    <div className="space-y-0.5">
                                        <Label htmlFor="autonomous_mode" className="text-yellow-500 font-bold">Enable Full Autonomy</Label>
                                        <p className="text-xs text-muted-foreground mt-1">
                                            Bypasses all confirmation prompts for tools like code execution and file writing.
                                        </p>
                                    </div>
                                    <Switch
                                        id="autonomous_mode"
                                        checked={config.AUTONOMOUS_MODE === 'true'}
                                        onCheckedChange={(checked) => handleChange('AUTONOMOUS_MODE', checked ? 'true' : 'false')}
                                    />
                                </div>
                                <div className="mt-4 grid gap-3 p-4 bg-card/50 rounded-lg border border-border/50">
                                    <div className="space-y-1">
                                        <Label htmlFor="max_iterations" className="font-semibold flex items-center gap-2">
                                            Max Tool Interactions
                                        </Label>
                                        <p className="text-xs text-muted-foreground">
                                            The maximum number of tools the bot can run autonomously in a single response before forcefully stopping. Prevents infinite loops.
                                        </p>
                                    </div>
                                    <div className="flex items-center gap-4">
                                        <Input
                                            id="max_iterations"
                                            type="number"
                                            min="1"
                                            max="200"
                                            value={config.MAX_ITERATIONS || "30"}
                                            onChange={(e) => handleChange("MAX_ITERATIONS", e.target.value)}
                                            className="w-24 font-mono text-center"
                                        />
                                    </div>
                                </div>
                                <div className="mt-4 grid gap-3 p-4 bg-card/50 rounded-lg border border-border/50">
                                    <div className="space-y-1">
                                        <Label htmlFor="command_timeout" className="font-semibold flex items-center gap-2">
                                            Command Timeout (Seconds)
                                        </Label>
                                        <p className="text-xs text-muted-foreground">
                                            The maximum duration a tool (like Discord Voice) can run before being forcefully stopped. Set to 0 for infinite (no timeout).
                                        </p>
                                    </div>
                                    <div className="flex items-center gap-4">
                                        <Input
                                            id="command_timeout"
                                            type="number"
                                            min="0"
                                            value={config.COMMAND_TIMEOUT !== undefined ? config.COMMAND_TIMEOUT : "300.0"}
                                            onChange={(e) => handleChange("COMMAND_TIMEOUT", e.target.value)}
                                            className="w-24 font-mono text-center"
                                        />
                                    </div>
                                </div>
                                <div className="mt-4 grid gap-3 p-4 bg-card/50 rounded-lg border border-border/50">
                                    <div className="space-y-1">
                                        <Label htmlFor="stall_timeout" className="font-semibold flex items-center gap-2">
                                            Stall Detection Timeout (Seconds)
                                        </Label>
                                        <p className="text-xs text-muted-foreground">
                                            If a command produces no output for this many seconds, it's assumed to be waiting for interactive input and gets killed. Set to 0 to disable stall detection.
                                        </p>
                                    </div>
                                    <div className="flex items-center gap-4">
                                        <Input
                                            id="stall_timeout"
                                            type="number"
                                            min="0"
                                            value={config.STALL_TIMEOUT !== undefined ? config.STALL_TIMEOUT : "30"}
                                            onChange={(e) => handleChange("STALL_TIMEOUT", e.target.value)}
                                            className="w-24 font-mono text-center"
                                        />
                                    </div>
                                </div>
                                <div className="mt-4 grid gap-3 p-4 bg-card/50 rounded-lg border border-yellow-500/30">
                                    <div className="flex items-center justify-between">
                                        <div className="space-y-1">
                                            <Label htmlFor="allow_unsafe_commands" className="font-semibold flex items-center gap-2 text-yellow-500">
                                                Allow Unsafe Commands
                                            </Label>
                                            <p className="text-xs text-muted-foreground">
                                                Allow commands with redirects ({'>'}), semicolons, and other shell operators. Useful for piped workflows but reduces sandboxing.
                                            </p>
                                        </div>
                                        <Switch
                                            id="allow_unsafe_commands"
                                            checked={config.ALLOW_UNSAFE_COMMANDS === 'true'}
                                            onCheckedChange={(checked) => handleChange('ALLOW_UNSAFE_COMMANDS', checked ? 'true' : 'false')}
                                        />
                                    </div>
                                </div>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2">
                                    <Settings className="h-5 w-5" />
                                    Workspace Security
                                </CardTitle>
                                <CardDescription>Control file access and permissions.</CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="grid gap-2">
                                    <Label htmlFor="allowed_paths">Allowed File Paths</Label>
                                    <div className="space-y-2 max-h-[300px] overflow-y-auto pr-2 custom-scrollbar">
                                        {(() => {
                                            let pathsArr: string[] = [];
                                            if (Array.isArray(config.ALLOWED_PATHS)) {
                                                pathsArr = config.ALLOWED_PATHS;
                                            } else if (typeof config.ALLOWED_PATHS === "string") {
                                                pathsArr = (config.ALLOWED_PATHS as string).split(",").filter(Boolean);
                                            }

                                            return pathsArr.map((path, idx) => (
                                                <div key={idx} className="flex gap-2 isolate">
                                                    <Input
                                                        value={path}
                                                        onChange={(e) => {
                                                            const paths = [...pathsArr];
                                                            paths[idx] = e.target.value;
                                                            handleChange("ALLOWED_PATHS", paths);
                                                        }}
                                                        placeholder="Absolute path..."
                                                    />
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="text-destructive hover:bg-destructive/10 shrink-0"
                                                        onClick={() => {
                                                            const paths = [...pathsArr];
                                                            paths.splice(idx, 1);
                                                            handleChange("ALLOWED_PATHS", paths);
                                                        }}
                                                    >
                                                        <Trash className="h-4 w-4" />
                                                    </Button>
                                                </div>
                                            ));
                                        })()}
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="w-full flex items-center justify-center gap-2 border-dashed"
                                            onClick={() => {
                                                let pathsArr: string[] = [];
                                                if (Array.isArray(config.ALLOWED_PATHS)) {
                                                    pathsArr = [...config.ALLOWED_PATHS];
                                                } else if (typeof config.ALLOWED_PATHS === "string") {
                                                    pathsArr = (config.ALLOWED_PATHS as string).split(",").filter(Boolean);
                                                }
                                                pathsArr.push("");
                                                handleChange("ALLOWED_PATHS", pathsArr);
                                            }}
                                        >
                                            <Plus className="h-4 w-4" />
                                            Add Path
                                        </Button>
                                    </div>
                                    <p className="text-xs text-muted-foreground mt-2">
                                        Absolute paths the bot is allowed to access and edit.
                                    </p>
                                </div>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2">
                                    <Globe className="h-5 w-5" />
                                    Remote Access
                                </CardTitle>
                                <CardDescription>Network details for connecting to LimeBot.</CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="bg-muted/30 p-4 rounded-lg border border-border/50">
                                    <div className="flex items-start gap-3">
                                        <Server className="h-5 w-5 text-primary mt-1" />
                                        <div className="space-y-2 flex-1">
                                            <h3 className="text-sm font-medium">Network Interfaces</h3>
                                            <div className="space-y-1">
                                                <RemoteStatus />
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    </TabsContent>
                </Tabs>
            </div>
        </div>
    );
}

function RemoteStatus() {
    const [status, setStatus] = useState<any>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        axios.get(`${API_BASE_URL}/api/setup/tailscale`)
            .then(res => setStatus(res.data))
            .catch(err => console.error("Failed to check network:", err))
            .finally(() => setLoading(false));
    }, []);

    if (loading) return <div className="text-xs text-muted-foreground animate-pulse">Scanning network...</div>;

    if (!status || status.error) {
        return <div className="text-xs text-destructive">Network check failed.</div>;
    }

    const tailscale = status.interfaces?.find((i: any) => i.is_tailscale);
    const lan = status.interfaces?.find((i: any) => !i.is_tailscale && i.ip !== '127.0.0.1');

    return (
        <div className="space-y-3">
            {tailscale ? (
                <div className="p-2 bg-emerald-500/10 border border-emerald-500/20 rounded text-sm">
                    <div className="flex items-center gap-2 text-emerald-500 font-medium mb-1">
                        <span className="relative flex h-2 w-2">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                        </span>
                        Tailscale Active
                    </div>
                    <div className="font-mono text-xs select-all cursor-text bg-background/50 p-1 rounded px-2">
                        http://{tailscale.ip}:8000
                    </div>
                </div>
            ) : (
                <div className="p-2 bg-orange-500/10 border border-orange-500/20 rounded text-sm">
                    <div className="text-orange-500 font-medium text-xs mb-1">Tailscale Not Detected</div>
                    <p className="text-[10px] text-muted-foreground">
                        Install Tailscale to access this bot securely from anywhere.
                    </p>
                </div>
            )}

            {lan && (
                <div className="space-y-1">
                    <div className="text-xs text-muted-foreground">LAN Access:</div>
                    <div className="font-mono text-xs select-all cursor-text bg-background/50 p-1 rounded px-2 inline-block">
                        http://{lan.ip}:8000
                    </div>
                </div>
            )}
        </div>
    );
}
