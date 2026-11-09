import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { API_BASE_URL } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Volume2, Play, Pause, Download, Loader2, Check, RotateCcw, KeyRound, RefreshCw, Search } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { classifySaveError, type SaveErrorInfo } from "@/lib/save-error";
import { classifyToolError } from "@/lib/tool-display";
import { useUnsaved, useBeforeUnloadGuard } from "@/hooks/useUnsaved";
import { useSaveState } from "@/hooks/useSaveState";
import {
    EmptyState,
    Field,
    LoadFailed,
    LoadingRows,
    SaveBar,
    SaveErrorNote,
    SettingsPage,
    SettingsSection,
    StatusPill,
} from "@/components/settings/kit";

interface VoiceSettings {
    enabled: boolean;
    voice_id: string;
    stability: number;
    similarity_boost: number;
    style: number;
    use_speaker_boost: boolean;
    speed: number;
    model_id: string;
    output_format: string;
    channels: string[];
    send_text_with_audio: boolean;
    voice_name: string;
    listen_mode: 'wake' | 'always';
    voice_follow_up_seconds: number;
}

interface ElevenLabsVoice {
    voice_id: string;
    name: string;
    category: string;
    description: string;
    preview_url: string;
}

interface ElevenLabsTtsModel {
    id: string;
    name: string;
    description: string;
    supportsSpeed: boolean;
    supportsSimilarity: boolean;
    supportsStyle: boolean;
    supportsSpeakerBoost: boolean;
}

const ELEVENLABS_TTS_MODELS = [
    {
        id: 'eleven_v4',
        name: 'Eleven v4',
        description: 'Most expressive speech, with support for 90+ languages.',
        supportsSpeed: false,
        supportsSimilarity: true,
        supportsStyle: false,
        supportsSpeakerBoost: false,
    },
    {
        id: 'eleven_v4_turbo',
        name: 'Eleven v4 Turbo',
        description: 'Expressive, low-latency speech for conversations.',
        supportsSpeed: false,
        supportsSimilarity: true,
        supportsStyle: false,
        supportsSpeakerBoost: false,
    },
    {
        id: 'eleven_v3',
        name: 'Eleven v3',
        description: 'Expressive speech with audio tags in 70+ languages.',
        supportsSpeed: false,
        supportsSimilarity: false,
        supportsStyle: true,
        supportsSpeakerBoost: false,
    },
    {
        id: 'eleven_multilingual_v2',
        name: 'Eleven Multilingual v2',
        description: 'Consistent quality for long-form speech in 29 languages.',
        supportsSpeed: true,
        supportsSimilarity: true,
        supportsStyle: true,
        supportsSpeakerBoost: true,
    },
    {
        id: 'eleven_flash_v2_5',
        name: 'Eleven Flash v2.5',
        description: 'Fast, lower-cost speech in 32 languages.',
        supportsSpeed: true,
        supportsSimilarity: true,
        supportsStyle: true,
        supportsSpeakerBoost: true,
    },
    {
        id: 'eleven_flash_v2',
        name: 'Eleven Flash v2',
        description: 'Fast speech generation for English.',
        supportsSpeed: true,
        supportsSimilarity: true,
        supportsStyle: true,
        supportsSpeakerBoost: true,
    },
] satisfies readonly ElevenLabsTtsModel[];

const OUTPUT_FORMATS = [
    { value: 'mp3_44100_128', label: 'MP3 44.1 kHz (128kbps)' },
    { value: 'mp3_44100_192', label: 'MP3 44.1 kHz (192kbps) - Creator Tier' },
    { value: 'pcm_44100', label: 'PCM 44.1 kHz - Pro Tier' },
    { value: 'ulaw_8000', label: 'u-law 8kHz - Twilio' },
];

const CHANNEL_OPTIONS = [
    { id: 'web', label: 'Web', hint: 'Plays inline in the chat.' },
    { id: 'discord', label: 'Discord', hint: 'Sent as an audio file.' },
    { id: 'whatsapp', label: 'WhatsApp', hint: 'Sent as an audio file.' },
] as const;

function getPreviewErrorMessage(error: unknown): string {
    let message = '';

    if (axios.isAxiosError<unknown>(error)) {
        const payload = error.response?.data;
        if (typeof payload === 'object' && payload !== null && 'detail' in payload) {
            const detail = payload.detail;
            if (typeof detail === 'string') {
                message = detail;
            } else if (
                typeof detail === 'object' && detail !== null && 'message' in detail &&
                typeof detail.message === 'string'
            ) {
                message = detail.message;
            }
        }
        if (!message) message = error.message;
    } else if (error instanceof Error) {
        message = error.message;
    }

    return message.replace(/\s+/g, ' ').trim().slice(0, 320)
        || 'Preview generation failed. Try again.';
}

/**
 * Explains a failed voice request. The dashboard key and the network keep the
 * generic wording; provider text (ElevenLabs 401, 402, 429) is read so a
 * rejected key or a plan limit does not look like a server fault.
 */
function describeVoiceError(error: unknown, subject: string, fallbackTitle: string): SaveErrorInfo {
    const base = classifySaveError(error, subject);
    if (base.category === 'network') {
        return { ...base, hint: 'Check that the backend is running, then try again.' };
    }
    if (base.category === 'timeout') {
        return { ...base, hint: 'The request may still be running. Wait a moment, then try again.' };
    }
    if (base.status === 401 || base.status === 403) return base;

    const detail = getPreviewErrorMessage(error);
    const tool = classifyToolError(detail);
    if (tool.category === 'authentication') {
        return {
            category: 'authentication',
            title: 'ElevenLabs rejected the API key',
            hint: 'Re-enter the key under Settings → Credentials and make sure it is allowed to use text-to-speech.',
            retryable: false,
            detail,
            status: base.status,
        };
    }
    if (tool.category === 'rate_limit') {
        return {
            category: 'rate_limit',
            title: 'ElevenLabs plan or rate limit reached',
            hint: 'Check your ElevenLabs plan and remaining credits, or wait a moment. Some voices and models need a paid plan.',
            retryable: true,
            detail,
            status: base.status,
        };
    }
    return { ...base, title: fallbackTitle, hint: 'The details below may explain it.', retryable: true, detail };
}

const DEFAULT_SETTINGS: VoiceSettings = {
    enabled: false,
    voice_id: '',
    stability: 0.5,
    similarity_boost: 0.75,
    style: 0.0,
    use_speaker_boost: true,
    speed: 1.0,
    model_id: 'eleven_multilingual_v2',
    output_format: 'mp3_44100_128',
    channels: ['web'],
    send_text_with_audio: false,
    voice_name: '',
    listen_mode: 'wake',
    voice_follow_up_seconds: 0
};

type VoicesStatus = 'idle' | 'loading' | 'ready' | 'failed';

export function VoicePage() {
    const [hasKey, setHasKey] = useState(false);
    const [settings, setSettings] = useState<VoiceSettings>(DEFAULT_SETTINGS);
    const [voices, setVoices] = useState<ElevenLabsVoice[]>([]);
    const [voicesStatus, setVoicesStatus] = useState<VoicesStatus>('idle');
    const [voicesError, setVoicesError] = useState<SaveErrorInfo | null>(null);
    const [loadingSettings, setLoadingSettings] = useState(true);
    const [loadError, setLoadError] = useState<unknown>(null);

    const { dirty, markSaved, restore, ready } = useUnsaved(settings);
    const { saving, error: saveError, savedAt, run, clearError } = useSaveState("your voice settings");
    useBeforeUnloadGuard(dirty);

    // Preview state (an action, not part of the saved settings)
    const [previewText, setPreviewText] = useState("Hi, I'm LimeBot, your AI assistant! How is your day going?");
    const [previewUrl, setPreviewUrl] = useState<string | null>(null);
    const [previewError, setPreviewError] = useState<SaveErrorInfo | null>(null);
    const [generatingPreview, setGeneratingPreview] = useState(false);

    const fetchVoices = useCallback(async () => {
        setVoicesStatus('loading');
        setVoicesError(null);
        try {
            const res = await axios.get(`${API_BASE_URL}/api/voice/voices`);
            const loadedVoices: ElevenLabsVoice[] = res.data.voices || [];
            setVoices(loadedVoices);
            setVoicesStatus('ready');

            // Set initial voice if none selected
            setSettings(prev => {
                if (!prev.voice_id && loadedVoices.length > 0) {
                    return { ...prev, voice_id: loadedVoices[0].voice_id };
                }
                return prev;
            });
        } catch (err) {
            console.error("Failed to fetch voices list:", err);
            setVoicesError(describeVoiceError(err, 'your voices', 'Could not load your voices'));
            setVoicesStatus('failed');
        }
    }, []);

    const fetchSettings = useCallback(async () => {
        setLoadingSettings(true);
        setLoadError(null);
        try {
            const res = await axios.get(`${API_BASE_URL}/api/voice/settings`);
            setHasKey(res.data.has_key);

            // Backfill defaults for new properties if missing from database
            const loaded = res.data.settings || {};
            const finalSettings: VoiceSettings = {
                enabled: loaded.enabled ?? false,
                voice_id: loaded.voice_id ?? '',
                stability: loaded.stability ?? 0.5,
                similarity_boost: loaded.similarity_boost ?? 0.75,
                style: loaded.style ?? 0.0,
                use_speaker_boost: loaded.use_speaker_boost ?? true,
                speed: loaded.speed ?? 1.0,
                model_id: loaded.model_id ?? 'eleven_multilingual_v2',
                output_format: loaded.output_format ?? 'mp3_44100_128',
                channels: loaded.channels ?? ['web'],
                send_text_with_audio: loaded.send_text_with_audio ?? false,
                voice_name: loaded.voice_name ?? '',
                listen_mode: loaded.listen_mode === 'always' ? 'always' : 'wake',
                voice_follow_up_seconds: loaded.voice_follow_up_seconds ?? 0
            };

            setSettings(finalSettings);
            markSaved(finalSettings);

            if (res.data.has_key) {
                void fetchVoices();
            }
        } catch (err) {
            console.error("Failed to fetch ElevenLabs settings:", err);
            setLoadError(err);
        } finally {
            setLoadingSettings(false);
        }
    }, [fetchVoices, markSaved]);

    useEffect(() => {
        void fetchSettings();
    }, [fetchSettings]);

    const handleSave = async () => {
        const snapshot = settings;
        const ok = await run(async () => {
            await axios.post(`${API_BASE_URL}/api/voice/settings`, snapshot);
        });
        if (ok) {
            markSaved(snapshot);
            toast.success("Voice settings saved.");
        }
    };

    const handleDiscard = () => {
        const saved = restore();
        if (saved) setSettings(saved);
        clearError();
    };

    const handleGeneratePreview = async () => {
        if (!previewText.trim()) return;
        setGeneratingPreview(true);
        setPreviewUrl(null);
        setPreviewError(null);
        try {
            const res = await axios.post(`${API_BASE_URL}/api/voice/synthesize`, {
                text: previewText,
                voice_id: settings.voice_id,
                stability: settings.stability,
                similarity_boost: settings.similarity_boost,
                style: settings.style,
                use_speaker_boost: settings.use_speaker_boost,
                speed: settings.speed,
                model_id: settings.model_id,
                output_format: settings.output_format
            });
            if (res.data.status === "success" && res.data.url) {
                setPreviewUrl(res.data.url);
                toast.success("Preview generated successfully!");
            }
        } catch (err) {
            console.error("Voice preview failed:", err);
            setPreviewError(describeVoiceError(err, 'the preview', 'Could not generate the preview'));
        } finally {
            setGeneratingPreview(false);
        }
    };

    const toggleChannel = (name: string, on: boolean) => {
        setSettings(prev => ({
            ...prev,
            channels: on
                ? Array.from(new Set([...(prev.channels ?? []), name]))
                : (prev.channels ?? []).filter(c => c !== name),
        }));
    };

    const handleResetValues = () => {
        setSettings(prev => ({
            ...prev,
            stability: 0.5,
            similarity_boost: 0.75,
            style: 0.0,
            use_speaker_boost: true,
            speed: 1.0,
            model_id: 'eleven_multilingual_v2',
            output_format: 'mp3_44100_128'
        }));
        toast.info("Voice settings reset to defaults.");
    };

    const changeCount = useMemo(() => {
        const saved = restore();
        if (!saved) return 0;
        return (Object.keys(settings) as Array<keyof VoiceSettings>)
            .filter((key) => JSON.stringify(settings[key]) !== JSON.stringify(saved[key])).length;
    }, [settings, restore]);

    const activeModel = ELEVENLABS_TTS_MODELS.find(model => model.id === settings.model_id);
    const supportsSpeed = activeModel?.supportsSpeed ?? true;
    const supportsSimilarity = activeModel?.supportsSimilarity ?? true;
    const supportsStyle = activeModel?.supportsStyle ?? true;
    const supportsSpeakerBoost = activeModel?.supportsSpeakerBoost ?? true;
    const unusedControls = [
        !supportsSpeed && 'speed',
        !supportsSimilarity && 'similarity',
        !supportsStyle && 'style exaggeration',
        !supportsSpeakerBoost && 'speaker boost',
    ].filter(Boolean) as string[];

    const pageProps = {
        title: "ElevenLabs Voice",
        description: "Pick a voice, tune how it sounds, and choose where LimeBot speaks.",
        icon: Volume2,
    };

    if (loadingSettings && !ready) {
        return (
            <SettingsPage {...pageProps}>
                <LoadingRows rows={6} className="py-8" label="Loading voice settings" />
            </SettingsPage>
        );
    }

    if (loadError && !ready) {
        return (
            <SettingsPage {...pageProps}>
                <LoadFailed error={loadError} subject="your voice settings" onRetry={() => void fetchSettings()} />
            </SettingsPage>
        );
    }

    return (
        <SettingsPage
            {...pageProps}
            saveBar={hasKey ? (
                <SaveBar
                    dirty={dirty}
                    saving={saving}
                    onSave={() => void handleSave()}
                    onDiscard={handleDiscard}
                    error={saveError}
                    onDismissError={clearError}
                    savedAt={savedAt}
                    changeCount={changeCount}
                    saveLabel="Save voice settings"
                />
            ) : undefined}
        >
            {/* Connection */}
            <SettingsSection
                title="Connection"
                description="Text-to-speech and voice transcription run through ElevenLabs."
            >
                {hasKey ? (
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                        <StatusPill tone="ok" icon={Check}>API key found</StatusPill>
                        <span className="text-xs text-muted-foreground">
                            {voicesStatus === 'ready'
                                ? `${voices.length} ${voices.length === 1 ? 'voice' : 'voices'} available`
                                : voicesStatus === 'loading'
                                    ? 'Checking your voices…'
                                    : voicesStatus === 'failed'
                                        ? 'Voices could not be loaded'
                                        : null}
                        </span>
                        <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-7 gap-1.5 px-2 text-xs"
                            onClick={() => void fetchVoices()}
                            disabled={voicesStatus === 'loading'}
                        >
                            <RefreshCw className={cn("h-3 w-3", voicesStatus === 'loading' && "animate-spin")} />
                            Refresh voices
                        </Button>
                    </div>
                ) : (
                    <EmptyState
                        icon={KeyRound}
                        title="ElevenLabs key not set"
                        description={<>Add your ElevenLabs API key under <strong className="font-medium text-foreground">Settings → Credentials</strong>, then reload this page to turn on text-to-speech.</>}
                        className="items-start px-0 py-2 text-left"
                    />
                )}
            </SettingsSection>

            {/* Delivery */}
            <SettingsSection
                title="Delivery"
                description="Whether LimeBot answers with audio, and where it sends it."
            >
                <div className="divide-y divide-border/50 border-y border-border/50">
                    <ToggleRow
                        id="voice-enabled"
                        label="Enable voice responses"
                        hint="LimeBot synthesizes and sends voice audio for chat replies."
                        checked={settings.enabled}
                        onChange={(val) => setSettings(prev => ({ ...prev, enabled: val }))}
                        disabled={!hasKey}
                    />
                    {CHANNEL_OPTIONS.map((ch) => (
                        <ToggleRow
                            key={ch.id}
                            id={`voice-channel-${ch.id}`}
                            label={`Voice on ${ch.label}`}
                            hint={ch.hint}
                            checked={(settings.channels ?? []).includes(ch.id)}
                            onChange={(val) => toggleChannel(ch.id, val)}
                            disabled={!hasKey}
                        />
                    ))}
                    <ToggleRow
                        id="voice-send-text"
                        label="Also send the text"
                        hint="On Discord and WhatsApp, send the written reply alongside the audio."
                        checked={settings.send_text_with_audio}
                        onChange={(val) => setSettings(prev => ({ ...prev, send_text_with_audio: val }))}
                        disabled={!hasKey}
                    />
                </div>
            </SettingsSection>

            {/* Discord voice conversations */}
            <SettingsSection
                title="Discord voice"
                description="How LimeBot decides what to answer after she joins a voice channel with /voice_join."
            >
                <Field
                    label="Voice name"
                    htmlFor="voice-name"
                    optional
                    hint={<>Say it at the start or end of a sentence to get her attention, for example "Jennie, move us to another channel". Separate nicknames with commas.</>}
                >
                    <Input
                        id="voice-name"
                        value={settings.voice_name}
                        onChange={(e) => setSettings(prev => ({ ...prev, voice_name: e.target.value }))}
                        placeholder="Leave empty to use her persona name"
                        maxLength={120}
                        disabled={!hasKey}
                    />
                </Field>
                <div className="divide-y divide-border/50 border-y border-border/50">
                    <ToggleRow
                        id="voice-wake"
                        label="Only answer when called by name"
                        hint="Turn off to answer everything said in the channel. Audio is still sent to ElevenLabs for transcription to check for the name, then discarded; use /voice_mute for real silence."
                        checked={settings.listen_mode === 'wake'}
                        onChange={(val) => setSettings(prev => ({ ...prev, listen_mode: val ? 'wake' : 'always' }))}
                        disabled={!hasKey}
                    />
                </div>
                {settings.listen_mode === 'wake' && (
                    <Field
                        label="Keep listening after she answers"
                        htmlFor="voice-follow-up"
                        inline
                        hint="0 means she answers only when you say her name. A higher value lets the same person keep talking for that many seconds (0 to 120) without repeating it."
                    >
                        <div className="flex items-center gap-2">
                            <Input
                                id="voice-follow-up"
                                type="number"
                                min={0}
                                max={120}
                                className="h-9 w-20 text-right"
                                value={settings.voice_follow_up_seconds}
                                onChange={(e) => setSettings(prev => ({ ...prev, voice_follow_up_seconds: Math.min(120, Math.max(0, Number(e.target.value) || 0)) }))}
                                disabled={!hasKey}
                            />
                            <span className="text-xs text-muted-foreground">sec</span>
                        </div>
                    </Field>
                )}
            </SettingsSection>

            {/* Voice picker */}
            <SettingsSection
                title="Voice"
                description="The ElevenLabs voice LimeBot speaks with. Use the play button to hear a sample."
            >
                <VoicePicker
                    hasKey={hasKey}
                    voices={voices}
                    status={voicesStatus}
                    error={voicesError}
                    onRetry={() => void fetchVoices()}
                    selectedId={settings.voice_id}
                    onSelect={(id) => setSettings(prev => ({ ...prev, voice_id: id }))}
                />
            </SettingsSection>

            {/* Model and parameters */}
            <SettingsSection
                title="Sound"
                description="Model, output quality and how the voice is shaped."
            >
                <Field label="Model" htmlFor="voice-model" hint={activeModel?.description}>
                    <Select
                        value={settings.model_id}
                        onValueChange={(val) => setSettings(prev => ({ ...prev, model_id: val }))}
                        disabled={!hasKey}
                    >
                        <SelectTrigger id="voice-model" className="w-full">
                            <SelectValue placeholder="Select TTS model" />
                        </SelectTrigger>
                        <SelectContent>
                            {ELEVENLABS_TTS_MODELS.map(model => (
                                <SelectItem key={model.id} value={model.id}>
                                    <span>{model.name}</span>
                                    <span className="ml-2 text-xs text-muted-foreground">
                                        {model.description}
                                    </span>
                                </SelectItem>
                            ))}
                            {!activeModel && settings.model_id && (
                                <SelectItem value={settings.model_id}>
                                    Saved model ({settings.model_id})
                                </SelectItem>
                            )}
                        </SelectContent>
                    </Select>
                </Field>

                <Field label="Output format" htmlFor="voice-format">
                    <Select
                        value={settings.output_format}
                        onValueChange={(val) => setSettings(prev => ({ ...prev, output_format: val }))}
                        disabled={!hasKey}
                    >
                        <SelectTrigger id="voice-format" className="w-full">
                            <SelectValue placeholder="Choose output quality" />
                        </SelectTrigger>
                        <SelectContent>
                            {OUTPUT_FORMATS.map((format) => (
                                <SelectItem key={format.value} value={format.value}>{format.label}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </Field>

                <div className="space-y-5 border-t border-border/50 pt-5">
                    {supportsSpeed && (
                        <ParamSlider
                            id="voice-speed"
                            label="Speed"
                            value={settings.speed}
                            min={0.7}
                            max={1.2}
                            step={0.05}
                            lowLabel="Slower"
                            highLabel="Faster"
                            disabled={!hasKey}
                            onChange={(val) => setSettings(prev => ({ ...prev, speed: val }))}
                        />
                    )}
                    <ParamSlider
                        id="voice-stability"
                        label="Stability"
                        value={settings.stability}
                        min={0}
                        max={1}
                        step={0.05}
                        lowLabel="More variable"
                        highLabel="More stable"
                        disabled={!hasKey}
                        onChange={(val) => setSettings(prev => ({ ...prev, stability: val }))}
                    />
                    {supportsSimilarity && (
                        <ParamSlider
                            id="voice-similarity"
                            label="Similarity"
                            value={settings.similarity_boost}
                            min={0}
                            max={1}
                            step={0.05}
                            lowLabel="Low"
                            highLabel="High"
                            disabled={!hasKey}
                            onChange={(val) => setSettings(prev => ({ ...prev, similarity_boost: val }))}
                        />
                    )}
                    {supportsStyle && (
                        <ParamSlider
                            id="voice-style"
                            label="Style exaggeration"
                            value={settings.style}
                            min={0}
                            max={1}
                            step={0.05}
                            lowLabel="None"
                            highLabel="Exaggerated"
                            disabled={!hasKey}
                            onChange={(val) => setSettings(prev => ({ ...prev, style: val }))}
                        />
                    )}
                    {supportsSpeakerBoost && (
                        <div className="border-y border-border/50">
                            <ToggleRow
                                id="voice-speaker-boost"
                                label="Speaker boost"
                                hint="Recommended for custom or cloned voices."
                                checked={settings.use_speaker_boost}
                                onChange={(val) => setSettings(prev => ({ ...prev, use_speaker_boost: val }))}
                                disabled={!hasKey}
                            />
                        </div>
                    )}
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="min-w-0 text-xs text-muted-foreground">
                            {unusedControls.length > 0
                                ? `${activeModel?.name} does not use ${unusedControls.join(', ')}, so those controls are hidden.`
                                : null}
                        </p>
                        <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={handleResetValues}
                            disabled={!hasKey}
                            className="h-8 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
                        >
                            <RotateCcw className="h-3.5 w-3.5" />
                            Reset values
                        </Button>
                    </div>
                </div>
            </SettingsSection>

            {/* Preview */}
            <SettingsSection
                title="Preview"
                description="Hear the voice with the settings above, including changes you have not saved yet."
            >
                <Field label="Test script" htmlFor="voice-preview-text">
                    <Textarea
                        id="voice-preview-text"
                        value={previewText}
                        onChange={(e) => setPreviewText(e.target.value)}
                        placeholder="Type something for the bot to speak..."
                        rows={3}
                        className="resize-none text-sm"
                        disabled={!hasKey}
                    />
                </Field>
                <div className="flex flex-wrap items-center gap-3">
                    <Button
                        className="gap-2"
                        onClick={() => void handleGeneratePreview()}
                        disabled={generatingPreview || !previewText.trim() || !hasKey}
                    >
                        {generatingPreview ? (
                            <>
                                <Loader2 className="h-4 w-4 animate-spin" />
                                Synthesizing speech...
                            </>
                        ) : (
                            <>
                                <Volume2 className="h-4 w-4" />
                                Generate voice preview
                            </>
                        )}
                    </Button>
                    {!hasKey && <span className="text-xs text-muted-foreground">Add an ElevenLabs key to generate previews.</span>}
                </div>
                {previewError && (
                    <SaveErrorNote
                        error={previewError}
                        onRetry={() => void handleGeneratePreview()}
                        onDismiss={() => setPreviewError(null)}
                    />
                )}
                {previewUrl && <PreviewPlayer key={previewUrl} url={previewUrl} />}
            </SettingsSection>
        </SettingsPage>
    );
}

function ToggleRow({ id, label, hint, checked, onChange, disabled }: {
    id: string;
    label: string;
    hint?: string;
    checked: boolean;
    onChange: (value: boolean) => void;
    disabled?: boolean;
}) {
    return (
        <div className="flex items-center justify-between gap-6 py-3">
            <div className="min-w-0 space-y-0.5">
                <Label htmlFor={id} className="text-sm font-medium">{label}</Label>
                {hint && <p className="text-xs leading-relaxed text-muted-foreground">{hint}</p>}
            </div>
            <Switch id={id} checked={checked} onCheckedChange={onChange} disabled={disabled} className="shrink-0" />
        </div>
    );
}

function ParamSlider({ id, label, value, min, max, step, lowLabel, highLabel, disabled, onChange }: {
    id: string;
    label: string;
    value: number;
    min: number;
    max: number;
    step: number;
    lowLabel: string;
    highLabel: string;
    disabled?: boolean;
    onChange: (value: number) => void;
}) {
    return (
        <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-3">
                <Label htmlFor={id} className="text-sm font-medium">{label}</Label>
                <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs tabular-nums text-foreground">{value.toFixed(2)}</span>
            </div>
            <input
                id={id}
                type="range"
                min={min}
                max={max}
                step={step}
                value={value}
                onChange={(e) => onChange(parseFloat(e.target.value))}
                disabled={disabled}
                className="h-5 w-full cursor-pointer accent-primary disabled:cursor-not-allowed disabled:opacity-50"
            />
            <div className="flex justify-between text-[11px] text-muted-foreground">
                <span>{lowLabel}</span>
                <span>{highLabel}</span>
            </div>
        </div>
    );
}

function VoicePicker({ hasKey, voices, status, error, onRetry, selectedId, onSelect }: {
    hasKey: boolean;
    voices: ElevenLabsVoice[];
    status: VoicesStatus;
    error: SaveErrorInfo | null;
    onRetry: () => void;
    selectedId: string;
    onSelect: (voiceId: string) => void;
}) {
    const [query, setQuery] = useState('');
    const [playingId, setPlayingId] = useState<string | null>(null);
    const audioRef = useRef<HTMLAudioElement | null>(null);

    useEffect(() => () => {
        audioRef.current?.pause();
    }, []);

    const toggleSample = (voice: ElevenLabsVoice) => {
        const wasPlaying = playingId === voice.voice_id;
        audioRef.current?.pause();
        audioRef.current = null;
        if (wasPlaying) {
            setPlayingId(null);
            return;
        }
        const audio = new Audio(voice.preview_url);
        audioRef.current = audio;
        setPlayingId(voice.voice_id);
        audio.addEventListener('ended', () => setPlayingId(current => (current === voice.voice_id ? null : current)));
        audio.play().catch(err => {
            console.error("Voice sample playback failed", err);
            setPlayingId(current => (current === voice.voice_id ? null : current));
        });
    };

    if (!hasKey) {
        return (
            <p className="text-sm text-muted-foreground">
                Your voices appear here once an ElevenLabs key is added.
            </p>
        );
    }

    if (status === 'idle' || status === 'loading') {
        return <LoadingRows rows={4} label="Fetching your ElevenLabs voices" />;
    }

    if (status === 'failed' && error) {
        return <SaveErrorNote error={error} onRetry={onRetry} />;
    }

    if (voices.length === 0) {
        return (
            <EmptyState
                icon={Volume2}
                title="No voices found"
                description="Your ElevenLabs account did not return any voices. Add one in the ElevenLabs voice library, then refresh."
                action={
                    <Button type="button" variant="outline" size="sm" className="gap-1.5" onClick={onRetry}>
                        <RefreshCw className="h-3.5 w-3.5" /> Refresh voices
                    </Button>
                }
                className="px-0 py-6"
            />
        );
    }

    const needle = query.trim().toLowerCase();
    const visible = needle
        ? voices.filter(voice => `${voice.name} ${voice.category} ${voice.description}`.toLowerCase().includes(needle))
        : voices;
    const selectedMissing = Boolean(selectedId) && !voices.some(voice => voice.voice_id === selectedId);

    return (
        <div className="space-y-2">
            {voices.length > 6 && (
                <div className="relative">
                    <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                    <Input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Filter voices"
                        aria-label="Filter voices"
                        className="h-9 pl-8"
                    />
                </div>
            )}
            <ul
                role="radiogroup"
                aria-label="Voice"
                className="max-h-80 divide-y divide-border/40 overflow-y-auto rounded-md border border-border/50"
            >
                {visible.map((voice) => {
                    const selected = voice.voice_id === selectedId;
                    return (
                        <li key={voice.voice_id} className={cn("flex items-stretch", selected && "bg-primary/5")}>
                            <button
                                type="button"
                                role="radio"
                                aria-checked={selected}
                                onClick={() => onSelect(voice.voice_id)}
                                className="flex min-w-0 flex-1 items-start gap-3 px-3 py-2.5 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                            >
                                <span
                                    className={cn(
                                        "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
                                        selected ? "border-primary bg-primary text-primary-foreground" : "border-border"
                                    )}
                                >
                                    {selected && <Check className="h-3 w-3" />}
                                </span>
                                <span className="min-w-0 space-y-0.5">
                                    <span className="flex items-center gap-2">
                                        <span className="truncate text-sm font-medium text-foreground">{voice.name}</span>
                                        <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">{voice.category}</span>
                                    </span>
                                    <span className="line-clamp-2 block text-xs leading-relaxed text-muted-foreground">
                                        {voice.description || "Custom voice cloned or generated from ElevenLabs."}
                                    </span>
                                </span>
                            </button>
                            {voice.preview_url && (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    className="m-1.5 h-8 w-8 shrink-0 self-center text-muted-foreground hover:text-foreground"
                                    onClick={() => toggleSample(voice)}
                                    aria-label={playingId === voice.voice_id ? `Stop sample of ${voice.name}` : `Play sample of ${voice.name}`}
                                >
                                    {playingId === voice.voice_id ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                                </Button>
                            )}
                        </li>
                    );
                })}
                {visible.length === 0 && (
                    <li className="px-3 py-6 text-center text-xs text-muted-foreground">No voices match "{query.trim()}".</li>
                )}
            </ul>
            {selectedMissing && (
                <p className="text-xs text-muted-foreground">
                    The saved voice (<span className="font-mono">{selectedId}</span>) is not in this list. It stays selected until you pick another.
                </p>
            )}
        </div>
    );
}

function PreviewPlayer({ url }: { url: string }) {
    const [isPlaying, setIsPlaying] = useState(false);
    const [failed, setFailed] = useState(false);
    const src = /^(data:|https?:)/.test(url) ? url : `${API_BASE_URL}${url}`;
    const audioRef = useState<HTMLAudioElement | null>(() => new Audio(src))[0];

    useEffect(() => {
        if (!audioRef) return;
        const onEnded = () => setIsPlaying(false);
        const onError = () => {
            setIsPlaying(false);
            setFailed(true);
        };
        audioRef.addEventListener('ended', onEnded);
        audioRef.addEventListener('error', onError);
        return () => {
            audioRef.pause();
            audioRef.removeEventListener('ended', onEnded);
            audioRef.removeEventListener('error', onError);
        };
    }, [audioRef]);

    const togglePlay = () => {
        if (!audioRef) return;
        if (isPlaying) {
            audioRef.pause();
            setIsPlaying(false);
        } else {
            audioRef.play().catch(err => {
                console.error("Playback failed", err);
                setIsPlaying(false);
            });
            setIsPlaying(true);
        }
    };

    return (
        <div className="flex w-full items-center gap-3 rounded-md border border-border/50 bg-muted/30 p-2.5">
            <Button
                variant="outline"
                size="icon"
                onClick={togglePlay}
                disabled={failed}
                aria-label={isPlaying ? "Pause preview" : "Play preview"}
                className="h-8 w-8 shrink-0 rounded-full border-primary/30 text-primary hover:bg-primary/10"
            >
                {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
            </Button>
            <div className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium text-foreground">preview_audio.mp3</span>
                <span className={cn("block text-[11px]", failed ? "text-red-500" : "text-muted-foreground")}>
                    {failed ? "This audio could not be played." : isPlaying ? "Playing" : "Ready to play"}
                </span>
            </div>
            <a
                href={src}
                download="preview_audio.mp3"
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Download preview"
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
                <Download className="h-4 w-4" />
            </a>
        </div>
    );
}
