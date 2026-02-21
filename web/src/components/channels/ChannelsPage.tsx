import { useState, useEffect } from 'react';
import axios from 'axios';
import { API_BASE_URL } from "@/lib/api";
import { Save, MessageCircle, AlertCircle, RefreshCw, User, Activity, Monitor } from 'lucide-react';
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { WhatsAppContacts } from "./WhatsAppContacts";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const DiscordIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg viewBox="0 0 127.14 96.36" fill="currentColor" {...props}>
        <path d="M107.7,8.07A105.15,105.15,0,0,0,81.47,0a72.06,72.06,0,0,0-3.36,6.83A97.68,97.68,0,0,0,49,6.83,72.37,72.37,0,0,0,45.64,0,105.89,105.89,0,0,0,19.39,8.09C2.79,32.65-1.71,56.6.54,80.21h0A105.73,105.73,0,0,0,32.71,96.36,77.11,77.11,0,0,0,39.6,85.25a68.42,68.42,0,0,1-10.85-5.18c.91-.66,1.8-1.34,2.66-2a75.57,75.57,0,0,0,64.32,0c.87.71,1.76,1.39,2.66,2a68.68,68.68,0,0,1-10.87,5.19,77,77,0,0,0,6.89,11.1A105.25,105.25,0,0,0,126.6,80.22c1.24-23.28-5.83-47.57-18.9-72.15ZM42.45,65.69C36.18,65.69,31,60,31,53s5-12.74,11.43-12.74S54,46,53.89,53,48.84,65.69,42.45,65.69Zm42.24,0C78.41,65.69,73.25,60,73.25,53s5-12.74,11.44-12.74S96.23,46,96.12,53,91.08,65.69,84.69,65.69Z" />
    </svg>
);

const WhatsAppIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
        <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413Z" />
    </svg>
);

import { QRCodeCanvas } from 'qrcode.react';

interface ConfigState {
    DISCORD_ALLOW_FROM?: string;
    DISCORD_ALLOW_CHANNELS?: string;
    DISCORD_ACTIVITY_TYPE?: string;
    DISCORD_ACTIVITY_TEXT?: string;
    DISCORD_STATUS?: string;
    ENABLE_DISCORD?: string;
    WHATSAPP_ALLOW_FROM?: string;
    ENABLE_WHATSAPP?: string;
    [key: string]: string | undefined;
}

function WhatsAppConnectionSection() {
    const [status, setStatus] = useState<'disconnected' | 'connecting' | 'connected' | 'scanning'>('disconnected');
    const [resetting, setResetting] = useState(false);
    const [qrCode, setQrCode] = useState<string | null>(null);

    // Dialog state
    const [alertDialog, setAlertDialog] = useState<{
        open: boolean;
        title: string;
        description: string;
    }>({
        open: false,
        title: "",
        description: "",
    });

    const showAlert = (title: string, description: string) => {
        setAlertDialog({ open: true, title, description });
    };

    useEffect(() => {
        // Connect to WebSocket to listen for status updates
        // Use relative path or dynamic base for WS
        const wsUrl = API_BASE_URL.replace('http', 'ws');
        const apiKey = localStorage.getItem('limebot_api_key');
        const ws = new WebSocket(`${wsUrl}/ws?api_key=${apiKey || ''}`);

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                // Backend sends QR in metadata.qr
                const qrCode = data.metadata?.qr || data.qr;

                if (data.type === 'whatsapp_qr' || data.metadata?.type === 'whatsapp_qr') {
                    if (qrCode) {
                        setQrCode(qrCode);
                        setStatus('scanning');
                    }
                } else if (data.type === 'whatsapp_status' || data.metadata?.type === 'whatsapp_status') {
                    const newStatus = data.metadata?.status || data.status;
                    if (newStatus === 'connected') {
                        setStatus('connected');
                        setQrCode(null); // Clear QR code on connection
                    } else if (newStatus === 'disconnected') {
                        setStatus('disconnected');
                        setQrCode(null); // Clear QR code on disconnection
                    }
                }
            } catch (e) {
                console.error("Error parsing WS message", e);
            }
        };

        return () => {
            ws.close();
        };
    }, []);

    const handleReset = async () => {
        setResetting(true);
        try {
            const res = await axios.post(`${API_BASE_URL}/api/whatsapp/reset`);
            const data = res.data;
            if (data.status === 'success') {
                setStatus('disconnected');
                showAlert('Session Reset', data.message || 'WhatsApp session has been reset successfully.');
            } else {
                showAlert('Reset Failed', data.message || 'Failed to reset WhatsApp session.');
            }
        } catch (e) {
            showAlert('Error', 'An unexpected error occurred while resetting the session.');
        }
        setResetting(false);
    };

    return (
        <Card className="border-border/50 bg-card/50">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Monitor className="h-5 w-5 text-[#25D366]" />
                    Device Connection
                </CardTitle>
                <CardDescription>
                    WhatsApp connection is managed via the terminal.
                </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col items-center justify-center p-6 min-h-[120px]">
                {status === 'connected' ? (
                    <div className="flex flex-col items-center gap-4 text-green-500">
                        <div className="h-16 w-16 rounded-full bg-green-500/10 flex items-center justify-center">
                            <WhatsAppIcon className="h-8 w-8" />
                        </div>
                        <p className="font-semibold">WhatsApp is Connected</p>
                    </div>
                ) : qrCode ? (
                    <div className="flex flex-col items-center gap-4">
                        <div className="bg-white p-4 rounded shadow-sm">
                            <QRCodeCanvas value={qrCode} size={200} />
                        </div>
                        <p className="text-sm text-muted-foreground">Scan with WhatsApp</p>
                    </div>
                ) : (
                    <div className="text-center text-muted-foreground">
                        <p className="mb-2">Click <strong>Enable</strong> above to start.</p>
                        <p className="text-xs opacity-70">After enabling, the page will refresh and you will see the QR code shortly.</p>
                    </div>
                )}
            </CardContent>
            <div className="px-6 pb-6 flex justify-center">
                <Button
                    variant="outline"
                    size="sm"
                    onClick={handleReset}
                    disabled={resetting}
                    className="text-orange-500 border-orange-500/50 hover:bg-orange-500/10"
                >
                    <RefreshCw className={`h-4 w-4 mr-2 ${resetting ? 'animate-spin' : ''}`} />
                    {resetting ? 'Resetting...' : 'Reset Session'}
                </Button>
            </div>

            <AlertDialog open={alertDialog.open} onOpenChange={(open) => setAlertDialog(prev => ({ ...prev, open }))}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{alertDialog.title}</AlertDialogTitle>
                        <AlertDialogDescription>
                            {alertDialog.description}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogAction>OK</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </Card>
    );
}

export function ChannelsPage() {
    const [config, setConfig] = useState<ConfigState>({});
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [status, setStatus] = useState<{ type: 'success' | 'error', message: string } | null>(null);

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = () => {
        setLoading(true);
        axios.get(`${API_BASE_URL}/api/config`)
            .then(res => {
                if (res.data.env) {
                    setConfig(res.data.env);
                }
                setLoading(false);
            })
            .catch(err => {
                if (err.response?.status !== 401) {
                    console.error("Failed to load config:", err);
                    setStatus({ type: 'error', message: "Failed to load configuration." });
                }
                setLoading(false);
            });
    };

    const handleSave = () => {
        setSaving(true);
        setStatus(null);

        const updateData = {
            DISCORD_ALLOW_FROM: config.DISCORD_ALLOW_FROM,
            DISCORD_ALLOW_CHANNELS: config.DISCORD_ALLOW_CHANNELS,
            DISCORD_ACTIVITY_TYPE: config.DISCORD_ACTIVITY_TYPE,
            DISCORD_STATUS: config.DISCORD_STATUS,
            DISCORD_ACTIVITY_TEXT: config.DISCORD_ACTIVITY_TEXT,
            ENABLE_DISCORD: config.ENABLE_DISCORD,
            DISCORD_TOKEN: config.DISCORD_TOKEN,
            PERSONALITY_WHITELIST: config.PERSONALITY_WHITELIST,
            WHATSAPP_ALLOW_FROM: config.WHATSAPP_ALLOW_FROM,
            ENABLE_WHATSAPP: config.ENABLE_WHATSAPP
        };

        axios.post(`${API_BASE_URL}/api/config`, { env: updateData })
            .then(res => {
                const data = res.data;
                if (data.error) throw new Error(data.error);
                setStatus({ type: 'success', message: "Access rules saved!" });
                setSaving(false);
            })
            .catch(err => {
                console.error("Failed to save config:", err);
                setStatus({ type: 'error', message: "Failed to save configuration." });
                setSaving(false);
            });
    };

    const handleChange = (key: string, value: string) => {
        setConfig(prev => ({ ...prev, [key]: value }));
    };

    if (loading) return <div className="p-8 text-muted-foreground">Loading channel settings...</div>;

    return (
        <div className="h-full overflow-y-auto p-6 md:p-8 bg-background/50">
            <div className="max-w-4xl mx-auto space-y-8">
                <header>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <MessageCircle className="h-6 w-6 text-primary" />
                        Channels & Presence
                    </h1>
                    <p className="text-muted-foreground mt-1">
                        Configure integration status and security for connected platforms.
                    </p>
                </header>

                {status && (
                    <Alert className={status.type === 'success' ? "border-primary/50 bg-primary/10 text-primary" : "border-destructive/50 bg-destructive/10 text-destructive"}>
                        {status.type === 'success' ? <RefreshCw className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
                        <AlertTitle>{status.type === 'success' ? "Saved" : "Error"}</AlertTitle>
                        <AlertDescription>{status.message}</AlertDescription>
                    </Alert>
                )}

                <Tabs defaultValue="discord" className="w-full">
                    <TabsList className="grid w-full grid-cols-2 mb-8">
                        <TabsTrigger value="discord" className="flex items-center gap-2">
                            <DiscordIcon className="h-4 w-4" /> Discord
                        </TabsTrigger>
                        <TabsTrigger value="whatsapp" className="flex items-center gap-2">
                            <WhatsAppIcon className="h-4 w-4" /> WhatsApp
                        </TabsTrigger>
                    </TabsList>

                    {/* DISCORD TAB */}
                    <TabsContent value="discord" className="space-y-6">
                        <Card className="border-border/50 bg-card/50">
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2">
                                    <DiscordIcon className="h-5 w-5 text-[#5865F2]" />
                                    Integration Status
                                </CardTitle>
                                <CardDescription>
                                    Enable or disable the Discord bot.
                                </CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-6">
                                <div className="flex items-center justify-between">
                                    <div className="space-y-1">
                                        <Label htmlFor="enable_discord" className="text-base">Enable Discord Integration</Label>
                                        <p className="text-sm text-muted-foreground">
                                            When enabled, LimeBot will connect to Discord using the configured token.
                                        </p>
                                    </div>
                                    <Switch
                                        id="enable_discord"
                                        checked={config.ENABLE_DISCORD !== 'false'} // Default true if string is missing or not 'false'
                                        onCheckedChange={(checked) => handleChange('ENABLE_DISCORD', checked ? 'true' : 'false')}
                                    />
                                </div>

                                <div className="pt-4 border-t border-border/50 space-y-4">
                                    <div className="grid gap-2">
                                        <Label htmlFor="discord_token">Discord Bot Token</Label>
                                        <Input
                                            id="discord_token"
                                            type="password"
                                            value={config.DISCORD_TOKEN || ""}
                                            onChange={(e) => handleChange("DISCORD_TOKEN", e.target.value)}
                                            placeholder="MT..."
                                            className="font-mono"
                                        />
                                    </div>

                                    <div className="grid gap-2">
                                        <div className="flex items-center gap-2">
                                            <Label htmlFor="personality_whitelist">Personality Whitelist (IDs)</Label>
                                            <span className="text-[10px] font-bold bg-primary/20 text-primary px-1.5 py-0.5 rounded uppercase tracking-wider">Restricted</span>
                                        </div>
                                        <Input
                                            id="personality_whitelist"
                                            value={config.PERSONALITY_WHITELIST || ""}
                                            onChange={(e) => handleChange("PERSONALITY_WHITELIST", e.target.value)}
                                            placeholder="UserID1, UserID2..."
                                            className="font-mono text-sm"
                                        />
                                        <p className="text-[10px] text-muted-foreground">
                                            Comma-separated list of Discord User IDs that can skip identity isolation and access the "Partner/Creator" persona.
                                        </p>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>

                        {config.ENABLE_DISCORD !== 'false' && (
                            <div className="space-y-6 animate-in slide-in-from-top-4 duration-300">
                                <Card className="border-border/50 bg-card/50">
                                    <CardHeader>
                                        <CardTitle className="flex items-center gap-2">
                                            <Activity className="h-5 w-5 text-[#5865F2]" />
                                            Rich Presence
                                        </CardTitle>
                                        <CardDescription>
                                            Customize how LimeBot appears in the Discord user list.
                                        </CardDescription>
                                    </CardHeader>
                                    <CardContent className="space-y-4">
                                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                            <div className="space-y-2">
                                                <Label>Status</Label>
                                                <Select
                                                    value={config.DISCORD_STATUS || "online"}
                                                    onValueChange={(val) => handleChange("DISCORD_STATUS", val)}
                                                >
                                                    <SelectTrigger>
                                                        <SelectValue placeholder="Select status" />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        <SelectItem value="online">Online</SelectItem>
                                                        <SelectItem value="idle">Idle</SelectItem>
                                                        <SelectItem value="dnd">Do Not Disturb</SelectItem>
                                                        <SelectItem value="invisible">Invisible</SelectItem>
                                                    </SelectContent>
                                                </Select>
                                            </div>
                                            <div className="space-y-2">
                                                <Label>Activity Type</Label>
                                                <Select
                                                    value={config.DISCORD_ACTIVITY_TYPE || "playing"}
                                                    onValueChange={(val) => handleChange("DISCORD_ACTIVITY_TYPE", val)}
                                                >
                                                    <SelectTrigger>
                                                        <SelectValue placeholder="Select type" />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        <SelectItem value="playing">Playing</SelectItem>
                                                        <SelectItem value="watching">Watching</SelectItem>
                                                        <SelectItem value="listening">Listening</SelectItem>
                                                        <SelectItem value="competing">Competing</SelectItem>
                                                    </SelectContent>
                                                </Select>
                                            </div>
                                            <div className="space-y-2 md:col-span-1">
                                                <Label>Activity Status</Label>
                                                <Input
                                                    value={config.DISCORD_ACTIVITY_TEXT || ""}
                                                    onChange={(e) => handleChange("DISCORD_ACTIVITY_TEXT", e.target.value)}
                                                    placeholder="e.g. Visual Studio Code"
                                                />
                                            </div>
                                        </div>
                                        <div className="p-3 rounded bg-[#5865F2]/10 border border-[#5865F2]/20 text-xs text-muted-foreground flex items-center gap-2">
                                            <Monitor className="h-4 w-4 text-[#5865F2]" />
                                            Preview: <strong>{config.DISCORD_ACTIVITY_TYPE || "playing"}</strong> {config.DISCORD_ACTIVITY_TEXT || "LimeBot"}
                                        </div>
                                    </CardContent>
                                </Card>

                                <Card className="border-border/50 bg-card/50">
                                    <CardHeader>
                                        <CardTitle className="flex items-center gap-2">
                                            <User className="h-5 w-5 text-[#5865F2]" />
                                            Access Control
                                        </CardTitle>
                                        <CardDescription>
                                            Restrict bot usage to specific users or channels.
                                        </CardDescription>
                                    </CardHeader>
                                    <CardContent className="space-y-6">
                                        <div className="space-y-2">
                                            <Label htmlFor="discord_users">Allowed User IDs</Label>
                                            <Input
                                                id="discord_users"
                                                value={config.DISCORD_ALLOW_FROM || ""}
                                                onChange={(e) => handleChange("DISCORD_ALLOW_FROM", e.target.value)}
                                                placeholder="123456789012345678, 987654321..."
                                                className="font-mono text-sm"
                                            />
                                            <p className="text-xs text-muted-foreground">
                                                Comma-separated list of User IDs. Leave empty to allow everyone.
                                            </p>
                                        </div>

                                        <div className="my-6 h-[1px] bg-border" />

                                        <div className="space-y-2">
                                            <Label htmlFor="discord_channels">Allowed Channel IDs</Label>
                                            <Input
                                                id="discord_channels"
                                                value={config.DISCORD_ALLOW_CHANNELS || ""}
                                                onChange={(e) => handleChange("DISCORD_ALLOW_CHANNELS", e.target.value)}
                                                placeholder="123456789012345678"
                                                className="font-mono text-sm"
                                            />
                                            <p className="text-xs text-muted-foreground">
                                                Comma-separated list of Channel IDs.
                                            </p>
                                        </div>
                                    </CardContent>
                                </Card>
                            </div>
                        )}
                    </TabsContent>

                    {/* WHATSAPP TAB */}
                    <TabsContent value="whatsapp" className="space-y-6">
                        <Card className="border-border/50 bg-card/50">
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2">
                                    <WhatsAppIcon className="h-5 w-5 text-[#25D366]" />
                                    Integration Status
                                </CardTitle>
                                <CardDescription>
                                    Enable or disable the WhatsApp bridge.
                                </CardDescription>
                            </CardHeader>
                            <CardContent className="flex items-center justify-between">
                                <div className="space-y-1">
                                    <Label htmlFor="enable_whatsapp" className="text-base">Enable WhatsApp Integration</Label>
                                    <p className="text-sm text-muted-foreground">
                                        When enabled, LimeBot will attempt to connect to the WhatsApp bridge on startup.
                                    </p>
                                </div>
                                <Switch
                                    id="enable_whatsapp"
                                    checked={config.ENABLE_WHATSAPP === 'true'}
                                    onCheckedChange={(checked) => handleChange('ENABLE_WHATSAPP', checked ? 'true' : 'false')}
                                />
                            </CardContent>
                        </Card>

                        {config.ENABLE_WHATSAPP === 'true' && (
                            <div className="space-y-6 animate-in slide-in-from-top-4 duration-300">
                                <div className="flex items-start gap-3 rounded-lg border border-[#25D366]/30 bg-[#25D366]/5 px-4 py-3">
                                    <span className="mt-0.5 text-[#25D366]">
                                        <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4"><path d="M12 1C5.925 1 1 5.925 1 12s4.925 11 11 11 11-4.925 11-11S18.075 1 12 1zm.5 16.5h-1v-7h1v7zm0-9h-1V7h1v1.5z" /></svg>
                                    </span>
                                    <div>
                                        <p className="text-sm font-semibold text-[#25D366]">Autonomous Mode — Always On</p>
                                        <p className="text-xs text-muted-foreground mt-0.5">
                                            WhatsApp channel always runs in autonomous mode. Tool calls (file writes, commands, etc.) execute immediately without confirmation prompts.
                                        </p>
                                    </div>
                                </div>
                                <WhatsAppConnectionSection />
                                <WhatsAppContacts />
                            </div>
                        )}
                    </TabsContent>
                </Tabs>

                <div className="flex justify-end pt-4 bg-background/95 backdrop-blur sticky bottom-0 p-4 border-t border-border mt-8">
                    <Button onClick={handleSave} disabled={saving} className="bg-primary hover:bg-primary/90 text-primary-foreground font-bold shadow-lg">
                        {saving ? (
                            <>Saving...</>
                        ) : (
                            <>
                                <Save className="mr-2 h-4 w-4" /> Save Configuration
                            </>
                        )}
                    </Button>
                </div>
            </div>
        </div>
    );
}
