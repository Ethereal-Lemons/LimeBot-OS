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
                                                    handleChange("LLM_MODEL", val)… (truncated 12043 chars)