import { useState, useEffect } from 'react';
import axios from 'axios';
import { API_BASE_URL } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Key, Bot, ShieldCheck, ArrowRight, CheckCircle2, RefreshCw, Trash, Plus } from 'lucide-react';

const FALLBACK_MODELS = [
    { id: 'gemini/gemini-2.0-flash', name: 'Gemini 2.0 Flash', provider: 'gemini' },
    { id: 'gemini/gemini-1.5-flash', name: 'Gemini 1.5 Flash', provider: 'gemini' },
    { id: 'openai/gpt-4o', name: 'GPT-4o', provider: 'openai' },
    { id: 'openai/gpt-4o-mini', name: 'GPT-4o Mini', provider: 'openai' },
    { id: 'anthropic/claude-3-7-sonnet-20250219', name: 'Claude 3.7 Sonnet', provider: 'anthropic' },
    { id: 'anthropic/claude-3-5-sonnet-20241022', name: 'Claude 3.5 Sonnet', provider: 'anthropic' },
    { id: 'deepseek/deepseek-chat', name: 'DeepSeek V3', provider: 'deepseek' },
    { id: 'xai/grok-2-1212', name: 'Grok 2', provider: 'xai' },
];

export function SetupPage() {
    const [step, setStep] = useState(1);
    const [config, setConfig] = useState({
        LLM_MODEL: 'gemini/gemini-2.0-flash',
        GEMINI_API_KEY: '',
        DISCORD_TOKEN: '',
        ENABLE_WHATSAPP: 'false',
        WHATSAPP_BRIDGE_URL: 'ws://localhost:3000',
        ALLOWED_PATHS: ['./persona', './logs'] as string[],
        ENABLE_DYNAMIC_PERSONALITY: 'false',
        APP_API_KEY: crypto.randomUUID()
    });
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const [availableModels, setAvailableModels] = useState<any[]>(FALLBACK_MODELS);
    const [isLoadingModels, setIsLoadingModels] = useState(false);

    useEffect(() => {
        fetchModels();
    }, []);

    const fetchModels = async (retries = 3) => {
        setIsLoadingModels(true);
        try {
            const res = await axios.get(`${API_BASE_URL}/api/llm/models`);
            if (res.data.models && res.data.models.length > 0) {
                setAvailableModels(res.data.models);
            }
        } catch (err) {
            console.error("Failed to load models:", err);
            if (retries > 0) {
                setTimeout(() => fetchModels(retries - 1), 2000);
            }
        } finally {
            setIsLoadingModels(false);
        }
    };

    const getModelsForProvider = (providerId: string) => {
        return availableModels.filter(m => m.provider === providerId);
    };

    const handleChange = (key: string, value: any) => {
        setConfig(prev => ({ ...prev, [key]: value }));
    };

    const handleSave = async () => {
        setSaving(true);
        setError(null);
        try {
            const res = await axios.post(`${API_BASE_URL}/api/config`, { env: config });
            const data = res.data;
            if (data.error) throw new Error(data.error);

            // Save API Key to LocalStorage for future requests
            if (config.APP_API_KEY) {
                localStorage.setItem('limebot_api_key', config.APP_API_KEY);
            }


            // Trigger Backend Restart
            await axios.post(`${API_BASE_URL}/api/control/restart`);

            // Give it a moment then redirect
            setStep(5);
            setTimeout(() => {
                window.location.href = '/';
            }, 3000);
        } catch (err: any) {
            setError(err.message || "Failed to save configuration.");
        } finally {
            setSaving(false);
        }
    };

    const renderStep = () => {
        switch (step) {
            case 1: // Welcome
                return (
                    <div className="space-y-6 animate-in fade-in duration-500">
                        <div className="text-center space-y-2">
                            <div className="inline-flex items-center justify-center mb-4">
                                <img src="/lime.png" alt="LimeBot logo" className="h-32 w-auto animate-in zoom-in duration-700" />
                            </div>
                            <h2 className="text-3xl font-bold tracking-tight">Welcome to LimeBot</h2>
                            <p className="text-muted-foreground text-lg max-w-md mx-auto">
                                Let's get your elegant agentic bot up and running in just a few steps.
                            </p>
                        </div>
                        <div className="pt-4 border-t border-border/50">
                            <Button onClick={() => setStep(2)} className="w-full h-12 text-lg font-bold group">
                                Start Configuration
                                <ArrowRight className="ml-2 h-5 w-5 group-hover:translate-x-1 transition-transform" />
                            </Button>
                        </div>
                    </div>
                );

            case 2: // LLM Settings
                return (
                    <div className="space-y-6 animate-in slide-in-from-right-4 duration-300">
                        <div className="flex items-center gap-3">
                            <div className="p-2 bg-primary/20 rounded-lg">
                                <Key className="h-6 w-6 text-primary" />
                            </div>
                            <div>
                                <h3 className="text-xl font-bold">LLM Brain</h3>
                                <p className="text-sm text-muted-foreground">Select your AI provider and model.</p>
                            </div>
                        </div>

                        <div className="space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="provider">Provider</Label>
                                <Select
                                    value={config.LLM_MODEL.split('/')[0] || 'gemini'}
                                    onValueChange={(val) => {
                                        // Set default model for the selected provider
                                        const models = getModelsForProvider(val);
                                        if (models.length > 0) {
                                            handleChange('LLM_MODEL', models[0].id);
                                        } else {
                                            handleChange('LLM_MODEL', `${val}/unknown`);
                                        }
                                    }}
                                >
                                    <SelectTrigger id="provider">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="gemini">Google Gemini</SelectItem>
                                        <SelectItem value="openai">OpenAI</SelectItem>
                                        <SelectItem value="anthropic">Anthropic Claude</SelectItem>
                                        <SelectItem value="xai">xAI (Grok)</SelectItem>
                                        <SelectItem value="deepseek">DeepSeek</SelectItem>
                                        <SelectItem value="nvidia">NVIDIA</SelectItem>
                                    </SelectContent>
                                </Select>
                                {isLoadingModels && (
                                    <p className="text-[10px] text-primary animate-pulse flex items-center gap-1 mt-1">
                                        <RefreshCw className="h-2 w-2 animate-spin" />
                                        Updating latest models...
                                    </p>
                                )}
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="model">Model</Label>
                                <Select value={config.LLM_MODEL} onValueChange={(val) => handleChange('LLM_MODEL', val)}>
                                    <SelectTrigger id="model">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {getModelsForProvider(config.LLM_MODEL.split('/')[0]).map((model) => (
                                            <SelectItem key={model.id} value={model.id}>
                                                {model.name}
                                            </SelectItem>
                                        ))}
                                        {getModelsForProvider(config.LLM_MODEL.split('/')[0]).length === 0 && (
                                            <div className="p-2 text-xs text-muted-foreground">Loading models...</div>
                                        )}
                                    </SelectContent>
                                </Select>
                            </div>

                            {/* Dynamic API Key Input */}
                            <div className="space-y-4 animate-in fade-in duration-300">
                                {config.LLM_MODEL.startsWith('gemini') && (

                                    <div className="space-y-2">
                                        <Label htmlFor="gemini_key">Gemini API Key</Label>
                                        <Input
                                            id="gemini_key"
                                            type="password"
                                            placeholder="sk-..."
                                            value={config.GEMINI_API_KEY}
                                            onChange={(e) => handleChange('GEMINI_API_KEY', e.target.value)}
                                            className="bg-background/50"
                                        />
                                    </div>
                                )}
                                {config.LLM_MODEL.startsWith('openai') && (
                                    <div className="space-y-2">
                                        <Label htmlFor="openai_key">OpenAI API Key</Label>
                                        <Input
                                            id="openai_key"
                                            type="password"
                                            placeholder="sk-..."
                                            value={(config as any).OPENAI_API_KEY || ''}
                                            onChange={(e) => handleChange('OPENAI_API_KEY', e.target.value)}
                                            className="bg-background/50"
                                        />
                                    </div>
                                )}
                                {config.LLM_MODEL.startsWith('anthropic') && (
                                    <div className="space-y-2">
                                        <Label htmlFor="anthropic_key">Anthropic API Key</Label>
                                        <Input
                                            id="anthropic_key"
                                            type="password"
                                            placeholder="sk-ant-..."
                                            value={(config as any).ANTHROPIC_API_KEY || ''}
                                            onChange={(e) => handleChange('ANTHROPIC_API_KEY', e.target.value)}
                                            className="bg-background/50"
                                        />
                                    </div>
                                )}
                                {config.LLM_MODEL.startsWith('xai') && (
                                    <div className="space-y-2">
                                        <Label htmlFor="xai_key">xAI API Key</Label>
                                        <Input
                                            id="xai_key"
                                            type="password"
                                            placeholder="xai-..."
                                            value={(config as any).XAI_API_KEY || ''}
                                            onChange={(e) => handleChange('XAI_API_KEY', e.target.value)}
                                            className="bg-background/50"
                                        />
                                    </div>
                                )}
                                {config.LLM_MODEL.startsWith('deepseek') && (
                                    <div className="space-y-2">
                                        <Label htmlFor="deepseek_key">DeepSeek API Key</Label>
                                        <Input
                                            id="deepseek_key"
                                            type="password"
                                            placeholder="sk-..."
                                            value={(config as any).DEEPSEEK_API_KEY || ''}
                                            onChange={(e) => handleChange('DEEPSEEK_API_KEY', e.target.value)}
                                            className="bg-background/50"
                                        />
                                    </div>
                                )}
                                {config.LLM_MODEL.startsWith('nvidia') && (
                                    <div className="space-y-2">
                                        <Label htmlFor="nvidia_key">NVIDIA API Key</Label>
                                        <Input
                                            id="nvidia_key"
                                            type="password"
                                            placeholder="nvapi-..."
                                            value={(config as any).NVIDIA_API_KEY || ''}
                                            onChange={(e) => handleChange('NVIDIA_API_KEY', e.target.value)}
                                            className="bg-background/50"
                                        />
                                    </div>
                                )}
                            </div>
                        </div>

                        <div className="flex gap-3 pt-4 border-t border-border/50">
                            <Button variant="outline" onClick={() => setStep(1)} className="flex-1">Back</Button>
                            <Button
                                onClick={() => setStep(3)}
                                className="flex-1"
                                disabled={
                                    (config.LLM_MODEL.startsWith('gemini') && !config.GEMINI_API_KEY) ||
                                    (config.LLM_MODEL.startsWith('openai') && !(config as any).OPENAI_API_KEY) ||
                                    (config.LLM_MODEL.startsWith('anthropic') && !(config as any).ANTHROPIC_API_KEY) ||
                                    (config.LLM_MODEL.startsWith('xai') && !(config as any).XAI_API_KEY) ||
                                    (config.LLM_MODEL.startsWith('deepseek') && !(config as any).DEEPSEEK_API_KEY)
                                }
                            >
                                Continue
                            </Button>
                        </div>
                    </div>
                );

            case 3: // Channels
                return (
                    <div className="space-y-6 animate-in slide-in-from-right-4 duration-300">
                        <div className="flex items-center gap-3">
                            <div className="p-2 bg-primary/20 rounded-lg">
                                <Bot className="h-6 w-6 text-primary" />
                            </div>
                            <div>
                                <h3 className="text-xl font-bold">Channels</h3>
                                <p className="text-sm text-muted-foreground">Where should LimeBot live?</p>
                            </div>
                        </div>

                        <div className="space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="discord_token">Discord Bot Token (Optional)</Label>
                                <Input
                                    id="discord_token"
                                    type="password"
                                    placeholder="your-bot-token"
                                    value={config.DISCORD_TOKEN}
                                    onChange={(e) => handleChange('DISCORD_TOKEN', e.target.value)}
                                    className="bg-background/50"
                                />
                                <p className="text-[10px] text-muted-foreground italic">You can add this later in settings.</p>
                            </div>

                            <div className="space-y-4 pt-4 border-t border-border/50">
                                <div className="flex items-center justify-between">
                                    <div className="space-y-0.5">
                                        <Label htmlFor="enable_whatsapp">Enable WhatsApp Integration</Label>
                                        <p className="text-xs text-muted-foreground">Connects via a local bridge to your phone.</p>
                                    </div>
                                    <Switch
                                        id="enable_whatsapp"
                                        checked={config.ENABLE_WHATSAPP === 'true'}
                                        onCheckedChange={(checked) => handleChange('ENABLE_WHATSAPP', checked ? 'true' : 'false')}
                                    />
                                </div>

                                {config.ENABLE_WHATSAPP === 'true' && (
                                    <div className="space-y-2 animate-in slide-in-from-top-2 duration-200">
                                        <Label htmlFor="wa_bridge">WhatsApp Bridge URL</Label>
                                        <Input
                                            id="wa_bridge"
                                            value={config.WHATSAPP_BRIDGE_URL}
                                            onChange={(e) => handleChange('WHATSAPP_BRIDGE_URL', e.target.value)}
                                            className="bg-background/50"
                                        />
                                    </div>
                                )}
                            </div>
                        </div>

                        <div className="flex gap-3 pt-4 border-t border-border/50">
                            <Button variant="outline" onClick={() => setStep(2)} className="flex-1">Back</Button>
                            <Button onClick={() => setStep(4)} className="flex-1">Continue</Button>
                        </div>
                    </div>
                );

            case 4: // Security & Finish
                return (
                    <div className="space-y-6 animate-in slide-in-from-right-4 duration-300">
                        <div className="flex items-center gap-3">
                            <div className="p-2 bg-primary/20 rounded-lg">
                                <ShieldCheck className="h-6 w-6 text-primary" />
                            </div>
                            <div>
                                <h3 className="text-xl font-bold">Security & Sandbox</h3>
                                <p className="text-sm text-muted-foreground">Control what the bot can access.</p>
                            </div>
                        </div>

                        <div className="space-y-4">
                            <div className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="allowed_paths">Allowed File Paths</Label>
                                    <div className="space-y-2 max-h-[250px] overflow-y-auto pr-2 custom-scrollbar">
                                        {(config.ALLOWED_PATHS || []).map((path, idx) => (
                                            <div key={idx} className="flex gap-2 isolate">
                                                <Input
                                                    value={path}
                                                    onChange={(e) => {
                                                        const paths = [...(config.ALLOWED_PATHS || [])];
                                                        paths[idx] = e.target.value;
                                                        handleChange("ALLOWED_PATHS", paths as any);
                                                    }}
                                                    placeholder="Absolute path..."
                                                    className="bg-background/50"
                                                />
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    className="text-destructive hover:bg-destructive/10 shrink-0"
                                                    onClick={() => {
                                                        const paths = [...(config.ALLOWED_PATHS || [])];
                                                        paths.splice(idx, 1);
                                                        handleChange("ALLOWED_PATHS", paths as any);
                                                    }}
                                                >
                                                    <Trash className="h-4 w-4" />
                                                </Button>
                                            </div>
                                        ))}
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="w-full flex items-center justify-center gap-2 border-dashed bg-background/50"
                                            onClick={() => {
                                                const paths = [...(config.ALLOWED_PATHS || [])];
                                                paths.push("");
                                                handleChange("ALLOWED_PATHS", paths as any);
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

                                <div className="space-y-2">
                                    <Label htmlFor="app_key">Application Security Key</Label>
                                    <div className="flex gap-2">
                                        <Input
                                            id="app_key"
                                            value={config.APP_API_KEY || ''}
                                            readOnly
                                            className="bg-background/50 font-mono text-xs"
                                        />
                                        <Button
                                            variant="outline"
                                            size="icon"
                                            onClick={() => handleChange('APP_API_KEY', crypto.randomUUID())}
                                            title="Regenerate"
                                        >
                                            <RefreshCw className="h-4 w-4" />
                                        </Button>
                                    </div>
                                    <p className="text-xs text-muted-foreground">
                                        This key secures your connection. It will be saved automatically.
                                    </p>
                                </div>

                                <div className="h-px bg-border my-2" />

                                <div className="flex items-center justify-between">
                                    <div className="space-y-0.5">
                                        <Label htmlFor="dynamic_personality">Adaptive Persona</Label>
                                        <p className="text-[10px] text-muted-foreground">Bot adjusts trust and style based on interaction.</p>
                                    </div>
                                    <Switch
                                        id="dynamic_personality"
                                        checked={config.ENABLE_DYNAMIC_PERSONALITY === 'true'}
                                        onCheckedChange={(checked) => handleChange('ENABLE_DYNAMIC_PERSONALITY', checked ? 'true' : 'false')}
                                    />
                                </div>
                            </div>

                            <div className="bg-primary/5 p-4 rounded-lg border border-primary/10">
                                <p className="text-sm text-center">
                                    By finishing, we will write these settings to your <code>.env</code> file.
                                </p>
                            </div>
                        </div>

                        {error && (
                            <div className="p-3 bg-destructive/10 text-destructive text-sm rounded border border-destructive/20">
                                {error}
                            </div>
                        )}

                        <div className="flex gap-3 pt-4 border-t border-border/50">
                            <Button variant="outline" onClick={() => setStep(3)} className="flex-1" disabled={saving}>Back</Button>
                            <Button onClick={handleSave} className="flex-1 bg-primary hover:bg-primary/90 text-primary-foreground font-bold" disabled={saving}>
                                {saving ? "Saving..." : "Finish Setup"}
                            </Button>
                        </div>
                    </div>
                );

            case 5: // Success
                return (
                    <div className="text-center space-y-6 py-8 animate-in zoom-in duration-500">
                        <div className="inline-flex items-center justify-center p-6 bg-primary/10 rounded-full">
                            <CheckCircle2 className="h-16 w-16 text-primary animate-bounce-subtle" />
                        </div>
                        <div className="space-y-2">
                            <h2 className="text-3xl font-bold text-primary">Setup Complete!</h2>
                            <p className="text-muted-foreground text-lg">
                                LimeBot is now ready to serve you.
                            </p>
                        </div>
                        <p className="text-sm text-muted-foreground pt-4 animate-pulse">
                            Redirecting you to the dashboard...
                        </p>
                    </div>
                );

            default:
                return null;
        }
    };

    return (
        <div className="min-h-screen bg-background flex flex-col items-center justify-center p-4 md:p-8">
            <div className="fixed inset-0 bg-[radial-gradient(circle_at_50%_50%,rgba(50,205,50,0.05),transparent_70%)] pointer-events-none" />

            <Card className="w-full max-w-lg border-2 border-primary/10 shadow-2xl relative overflow-hidden">
                <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-primary/50 to-transparent" />

                <CardHeader className="pb-4">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] font-mono text-muted-foreground tracking-widest uppercase">LimeBot Setup v1.0</span>
                        {step < 5 && (
                            <div className="flex gap-1">
                                {[1, 2, 3, 4].map(i => (
                                    <div key={i} className={`h-1 w-4 rounded-full transition-colors duration-500 ${i <= step ? 'bg-primary' : 'bg-muted'}`} />
                                ))}
                            </div>
                        )}
                    </div>
                </CardHeader>

                <CardContent className="pt-2 min-h-[400px] flex flex-col justify-center">
                    {renderStep()}
                </CardContent>

                <CardFooter className="flex justify-center pb-8 pt-0">
                    <p className="text-[10px] text-muted-foreground opacity-50">
                        &copy; 2026 LimeBot - AI Assistant
                    </p>
                </CardFooter>
            </Card>
        </div>
    );
}
