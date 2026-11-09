import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { API_BASE_URL } from "@/lib/api";
import { AlertTriangle, BrainCircuit, CheckCircle2, Circle, Cpu, Globe, Key, Loader2, Plus, PlugZap, RefreshCw, Settings, ShieldCheck, Trash, User } from 'lucide-react';
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectSeparator, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { DEFAULT_MODEL_BY_PROVIDER, getAdditionalModels, getInitialModelForProvider, getModelProvider, getRecommendedModels, PROVIDER_LABELS, type LlmModelOption } from "@/lib/llm-models";
import { type ConfigApiResponse, type ConfigSecretsMap, getSecretInfo, getSecretPlaceholder } from "@/lib/config-secrets";
import { classifySaveError } from "@/lib/save-error";
import { useBeforeUnloadGuard, useUnsaved } from "@/hooks/useUnsaved";
import { useSaveState } from "@/hooks/useSaveState";
import {
    Field,
    FieldError,
    LoadFailed,
    LoadingRows,
    Note,
    SaveBar,
    SettingsPage,
    SettingsSection,
    SettingsTabs,
    StatusPill,
    invalidInputClass,
} from "@/components/settings/kit";
import { cn } from "@/lib/utils";

type ConfigValue = string | string[] | undefined;

interface ConfigState {
    LLM_MODEL?: string;
    ALLOWED_PATHS?: string[];
    AUTONOMOUS_MODE?: string;
    APPROVAL_POLICY_PROFILE?: 'manual' | 'session' | 'autonomous' | 'review';
    MAX_ITERATIONS?: string;
    WEB_PORT?: string;
    LLM_PROXY_URL?: string;
    BROWSER_MODE?: string;
    BROWSER_CHANNEL?: string;
    BROWSER_CDP_URL?: string;
    BROWSER_USER_DATA_DIR?: string;
    BROWSER_PROFILE_DIRECTORY?: string;
    VIDEO_WHISPER_ENABLED?: string;
    [key: string]: ConfigValue;
}

type SecretDrafts = Record<string, string>;

/** Everything the user can edit on this page; the unit of "unsaved". */
interface FormSnapshot {
    config: ConfigState;
    secretDrafts: SecretDrafts;
    clearedSecrets: string[];
}

const SECRET_KEYS = [
    "APP_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "NVIDIA_API_KEY",
    "DASHSCOPE_API_KEY",
    "ELEVENLABS_API_KEY",
] as const;

const DEFAULT_CUSTOM_MODEL = "ollama/llama3";

type SecretKey = typeof SECRET_KEYS[number];

const AI_PROVIDER_SECRETS: Array<{ key: SecretKey; label: string; placeholder: string; note: string }> = [
    { key: "OPENAI_API_KEY", label: "OpenAI", placeholder: "sk-...", note: "GPT models, image generation, and optional Whisper" },
    { key: "GEMINI_API_KEY", label: "Google Gemini", placeholder: "AIza...", note: "Gemini chat and embeddings" },
    { key: "ANTHROPIC_API_KEY", label: "Anthropic", placeholder: "sk-ant-...", note: "Claude models" },
    { key: "OPENROUTER_API_KEY", label: "OpenRouter", placeholder: "sk-or-...", note: "Multi-provider model routing" },
    { key: "XAI_API_KEY", label: "xAI", placeholder: "xai-...", note: "Grok models" },
    { key: "DEEPSEEK_API_KEY", label: "DeepSeek", placeholder: "sk-...", note: "DeepSeek models" },
    { key: "MOONSHOT_API_KEY", label: "Moonshot / Kimi", placeholder: "sk-...", note: "Kimi and Moonshot models" },
    { key: "NVIDIA_API_KEY", label: "NVIDIA NIM", placeholder: "nvapi-...", note: "NVIDIA-hosted models" },
    { key: "DASHSCOPE_API_KEY", label: "Qwen / DashScope", placeholder: "sk-...", note: "Alibaba Qwen models" },
];

const PROVIDER_SECRET_KEYS: Record<string, SecretKey> = {
    openai: "OPENAI_API_KEY",
    gemini: "GEMINI_API_KEY",
    anthropic: "ANTHROPIC_API_KEY",
    openrouter: "OPENROUTER_API_KEY",
    xai: "XAI_API_KEY",
    deepseek: "DEEPSEEK_API_KEY",
    moonshot: "MOONSHOT_API_KEY",
    nvidia: "NVIDIA_API_KEY",
    qwen: "DASHSCOPE_API_KEY",
};

// ── validation ──────────────────────────────────────────────────────────────

interface NumberRule {
    min?: number;
    max?: number;
    integer?: boolean;
}

const NUMBER_RULES: Record<string, NumberRule> = {
    MAX_ITERATIONS: { min: 1, max: 200, integer: true },
    WEB_PORT: { min: 1, max: 65535, integer: true },
    RUN_COMMAND_MAX_SECONDS: { min: 10, max: 3600 },
    COMMAND_TIMEOUT: { min: 0 },
    STALL_TIMEOUT: { min: 0 },
};

const URL_RULES: Record<string, RegExp> = {
    LLM_BASE_URL: /^https?:\/\/\S+$/i,
    LLM_PROXY_URL: /^https?:\/\/\S+$/i,
    BROWSER_CDP_URL: /^(?:https?|wss?):\/\/\S+$/i,
};

function checkNumber(raw: ConfigValue, rule: NumberRule): string | null {
    const text = String(raw ?? "").trim();
    const value = Number(text);
    if (!text || !Number.isFinite(value)) return "Enter a number.";
    if (rule.integer && !Number.isInteger(value)) return "Use a whole number.";
    if (rule.min !== undefined && rule.max !== undefined && (value < rule.min || value > rule.max)) {
        return `Use a value from ${rule.min} to ${rule.max}.`;
    }
    if (rule.min !== undefined && value < rule.min) return `Use ${rule.min} or more.`;
    if (rule.max !== undefined && value > rule.max) return `Use ${rule.max} or less.`;
    return null;
}

function getAllowedPaths(config: ConfigState): string[] {
    if (Array.isArray(config.ALLOWED_PATHS)) return config.ALLOWED_PATHS;
    if (typeof config.ALLOWED_PATHS === "string") return (config.ALLOWED_PATHS as string).split(",").filter(Boolean);
    return [];
}

/**
 * Only values the user has changed can block a save: a value that is already
 * stored (even an odd one) is never something this page newly rejects.
 */
function validateConfig(config: ConfigState, saved: ConfigState | undefined): Record<string, string> {
    const errors: Record<string, string> = {};
    const unchanged = (key: string) => saved !== undefined && String(saved[key] ?? "") === String(config[key] ?? "");
    for (const [key, rule] of Object.entries(NUMBER_RULES)) {
        if (config[key] === undefined || unchanged(key)) continue;
        const problem = checkNumber(config[key], rule);
        if (problem) errors[key] = problem;
    }
    for (const [key, pattern] of Object.entries(URL_RULES)) {
        const text = String(config[key] ?? "").trim();
        if (!text || unchanged(key)) continue;
        if (!pattern.test(text)) {
            errors[key] = key === "BROWSER_CDP_URL"
                ? "Use an address like http://127.0.0.1:9222."
                : "Use a full address starting with http:// or https://.";
        }
    }
    getAllowedPaths(config).forEach((path, index) => {
        if (!path.trim()) errors[`ALLOWED_PATHS:${index}`] = "Enter a path, or remove this row.";
    });
    return errors;
}

const SECURITY_FIELDS = new Set([...Object.keys(NUMBER_RULES), "BROWSER_CDP_URL"]);

function countConfigChanges(saved: ConfigState, current: ConfigState): number {
    let count = 0;
    for (const key of new Set([...Object.keys(saved), ...Object.keys(current)])) {
        if (JSON.stringify(saved[key]) !== JSON.stringify(current[key])) count += 1;
    }
    return count;
}

function ErrorCountBadge({ count }: { count: number }) {
    return (
        <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500/15 px-1 text-[10px] font-medium text-red-500" title={`${count} value${count === 1 ? "" : "s"} to fix`}>
            {count}
        </span>
    );
}

// ── page ────────────────────────────────────────────────────────────────────

export function ConfigPage() {
    const [config, setConfig] = useState<ConfigState>({});
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<unknown>(null);
    const [secretMeta, setSecretMeta] = useState<ConfigSecretsMap>({});
    const [secretDrafts, setSecretDrafts] = useState<SecretDrafts>({});
    const [clearedSecrets, setClearedSecrets] = useState<string[]>([]);
    const [tab, setTab] = useState("ai");

    const [availableModels, setAvailableModels] = useState<LlmModelOption[]>([]);
    const [modelsLoading, setModelsLoading] = useState(false);
    const [modelsError, setModelsError] = useState<string | null>(null);
    const [showAllModels, setShowAllModels] = useState(false);
    const [showAllProviderKeys, setShowAllProviderKeys] = useState(false);
    const [blankPathsTouched, setBlankPathsTouched] = useState(false);

    const form = useMemo<FormSnapshot>(() => ({ config, secretDrafts, clearedSecrets }), [config, secretDrafts, clearedSecrets]);
    const { dirty, markSaved, restore } = useUnsaved<FormSnapshot>(form);
    const { saving, error: saveError, savedAt, run, clearError } = useSaveState("your settings");
    useBeforeUnloadGuard(dirty);

    const fetchModels = useCallback(async () => {
        try {
            setModelsLoading(true);
            setModelsError(null);
            const res = await axios.get(`${API_BASE_URL}/api/llm/models`);
            if (res.data.models) setAvailableModels(res.data.models);
        } catch (err) {
            console.error("Failed to load models:", err);
            setModelsError(classifySaveError(err, "the model list").title);
        } finally {
            setModelsLoading(false);
        }
    }, []);

    const fetchConfig = useCallback(async () => {
        setLoading(true);
        setLoadError(null);
        try {
            const res = await axios.get<ConfigApiResponse<ConfigState>>(`${API_BASE_URL}/api/config`);
            const nextEnv = res.data.env || {};
            setConfig(nextEnv);
            setSecretMeta(res.data.secrets || {});
            setSecretDrafts({});
            setClearedSecrets([]);
            markSaved({ config: nextEnv, secretDrafts: {}, clearedSecrets: [] });
        } catch (err) {
            console.error("Failed to load settings:", err);
            setLoadError(err);
        } finally {
            setLoading(false);
        }
    }, [markSaved]);

    useEffect(() => {
        void fetchConfig();
        void fetchModels();
    }, [fetchConfig, fetchModels]);

    const saved = useMemo(() => restore()?.config, [restore]);
    const errors = useMemo(() => validateConfig(config, saved), [config, saved]);
    const errorKeys = Object.keys(errors);
    const invalidCount = errorKeys.length;
    const securityErrorCount = errorKeys.filter((key) => SECURITY_FIELDS.has(key) || key.startsWith("ALLOWED_PATHS:")).length;
    const aiErrorCount = invalidCount - securityErrorCount;

    const changeCount = useMemo(() => {
        const base = restore();
        if (!base) return 0;
        return countConfigChanges(base.config, config)
            + Object.values(secretDrafts).filter((value) => value !== "").length
            + clearedSecrets.length;
    }, [restore, config, secretDrafts, clearedSecrets]);

    const getFilteredModels = () => {
        return availableModels.filter(model => {
            if (model.provider === 'gemini') return !!secretDrafts.GEMINI_API_KEY || getSecretInfo(secretMeta, 'GEMINI_API_KEY').configured;
            if (model.provider === 'openai') return !!secretDrafts.OPENAI_API_KEY || getSecretInfo(secretMeta, 'OPENAI_API_KEY').configured;
            if (model.provider === 'openrouter') return !!secretDrafts.OPENROUTER_API_KEY || getSecretInfo(secretMeta, 'OPENROUTER_API_KEY').configured;
            if (model.provider === 'anthropic') return !!secretDrafts.ANTHROPIC_API_KEY || getSecretInfo(secretMeta, 'ANTHROPIC_API_KEY').configured;
            if (model.provider === 'xai') return !!secretDrafts.XAI_API_KEY || getSecretInfo(secretMeta, 'XAI_API_KEY').configured;
            if (model.provider === 'deepseek') return !!secretDrafts.DEEPSEEK_API_KEY || getSecretInfo(secretMeta, 'DEEPSEEK_API_KEY').configured;
            if (model.provider === 'moonshot') return !!secretDrafts.MOONSHOT_API_KEY || getSecretInfo(secretMeta, 'MOONSHOT_API_KEY').configured;
            if (model.provider === 'nvidia') return !!secretDrafts.NVIDIA_API_KEY || getSecretInfo(secretMeta, 'NVIDIA_API_KEY').configured;
            if (model.provider === 'qwen') return !!secretDrafts.DASHSCOPE_API_KEY || getSecretInfo(secretMeta, 'DASHSCOPE_API_KEY').configured;
            return true;
        });
    };

    const handleSave = async () => {
        if (invalidCount > 0) return;
        const normalizedModel = String(config.LLM_MODEL || "").trim() || DEFAULT_CUSTOM_MODEL;

        const ok = await run(async () => {
            const secretUpdates = Object.fromEntries(
                Object.entries(secretDrafts).filter(([, value]) => value.trim() !== "")
            );
            const res = await axios.post(`${API_BASE_URL}/api/config`, {
                env: {
                    ...config,
                    LLM_MODEL: normalizedModel,
                    ...secretUpdates,
                },
                clear_secrets: clearedSecrets,
            });
            if (res.data.error) throw new Error(res.data.error);
            if (secretDrafts.APP_API_KEY?.trim()) {
                localStorage.setItem("limebot_api_key", secretDrafts.APP_API_KEY.trim());
                axios.defaults.headers.common["X-API-Key"] = secretDrafts.APP_API_KEY.trim();
            }
            setSecretMeta((prev) => {
                const next = { ...prev };
                for (const key of SECRET_KEYS) {
                    if (clearedSecrets.includes(key)) {
                        next[key] = { configured: false, masked: "", last4: "" };
                    } else if (secretDrafts[key]?.trim()) {
                        const val = secretDrafts[key].trim();
                        next[key] = {
                            configured: true,
                            masked: `••••${val.slice(-4)}`,
                            last4: val.slice(-4),
                        };
                    }
                }
                return next;
            });
            setSecretDrafts({});
            setClearedSecrets([]);
        });
        if (ok) markSaved({ config, secretDrafts: {}, clearedSecrets: [] });
    };

    const handleDiscard = () => {
        const base = restore();
        if (!base) return;
        setConfig(base.config);
        setSecretDrafts(base.secretDrafts);
        setClearedSecrets(base.clearedSecrets);
        setBlankPathsTouched(false);
        clearError();
    };

    const handleChange = (key: string, value: ConfigValue) => {
        setConfig(prev => ({ ...prev, [key]: value }));
    };

    const setDraft = (key: string, value: string) => {
        setSecretDrafts(prev => {
            const next = { ...prev };
            if (value === "") delete next[key];
            else next[key] = value;
            return next;
        });
    };

    const handleSecretChange = (key: string, value: string) => {
        setDraft(key, value);
        setClearedSecrets(prev => prev.filter((item) => item !== key));
    };

    const markSecretForClear = (key: string) => {
        setDraft(key, "");
        setClearedSecrets(prev => prev.includes(key) ? prev : [...prev, key]);
    };

    const isSecretConfigured = (key: SecretKey) => (
        !clearedSecrets.includes(key)
        && (!!secretDrafts[key]?.trim() || getSecretInfo(secretMeta, key).configured)
    );

    // ── small renderers ─────────────────────────────────────────────────────

    const renderSecretRow = (key: SecretKey, label: string, placeholder: string, note?: string) => {
        const info = getSecretInfo(secretMeta, key);
        const isCleared = clearedSecrets.includes(key);
        const configured = isSecretConfigured(key);
        return (
            <div key={key} className="space-y-2 py-3.5 first:pt-0 last:pb-0">
                <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                        <label htmlFor={key.toLowerCase()} className="text-sm font-medium">{label}</label>
                        {note && <p className="text-xs leading-relaxed text-muted-foreground">{note}</p>}
                    </div>
                    <StatusPill tone={isCleared ? 'warn' : configured ? 'ok' : 'muted'} icon={configured && !isCleared ? CheckCircle2 : Circle}>
                        {isCleared ? 'Clear on save' : configured ? 'Connected' : 'Optional'}
                    </StatusPill>
                </div>
                <div className="flex gap-2">
                    <Input
                        id={key.toLowerCase()}
                        type="password"
                        autoComplete="off"
                        value={secretDrafts[key] || ""}
                        onChange={(e) => handleSecretChange(key, e.target.value)}
                        placeholder={getSecretPlaceholder(secretMeta, key, placeholder)}
                    />
                    <Button
                        type="button"
                        variant="outline"
                        size="icon"
                        className="shrink-0"
                        onClick={() => markSecretForClear(key)}
                        title={`Clear ${label}`}
                        aria-label={`Clear ${label}`}
                        disabled={!configured && !secretDrafts[key]}
                    >
                        <Trash className="h-4 w-4" />
                    </Button>
                </div>
                {info.configured && !isCleared && (
                    <p className="text-[11px] text-muted-foreground">Stored securely as {info.masked}</p>
                )}
            </div>
        );
    };

    const numberField = (
        key: string,
        id: string,
        label: string,
        hint: React.ReactNode,
        fallback: string,
        extra?: { min?: string; max?: string },
    ) => (
        <Field label={label} htmlFor={id} hint={hint} error={errors[key]} inline>
            <Input
                id={id}
                type="number"
                min={extra?.min}
                max={extra?.max}
                value={config[key] !== undefined ? String(config[key]) : fallback}
                onChange={(e) => handleChange(key, e.target.value)}
                aria-invalid={!!errors[key]}
                className={cn("w-24 font-mono text-center", errors[key] && invalidInputClass)}
            />
        </Field>
    );

    const urlField = (key: string, id: string, label: string, hint: React.ReactNode, placeholder: string, optional = false) => (
        <Field label={label} htmlFor={id} hint={hint} error={errors[key]} optional={optional}>
            <Input
                id={id}
                value={String(config[key] ?? "")}
                onChange={(e) => handleChange(key, e.target.value)}
                placeholder={placeholder}
                aria-invalid={!!errors[key]}
                className={cn("font-mono text-sm", errors[key] && invalidInputClass)}
            />
        </Field>
    );

    const pageHeader = {
        title: "Settings",
        icon: Settings,
        description: "Connect providers, enable capabilities, and define LimeBot's safety boundaries.",
    };

    if (loading && !dirty && Object.keys(config).length === 0) {
        return (
            <SettingsPage {...pageHeader}>
                <LoadingRows rows={6} className="mt-8" label="Loading settings" />
            </SettingsPage>
        );
    }

    if (loadError && Object.keys(config).length === 0) {
        return (
            <SettingsPage {...pageHeader}>
                <LoadFailed error={loadError} subject="settings" onRetry={() => void fetchConfig()} />
            </SettingsPage>
        );
    }

    // ── derived model picker state ──────────────────────────────────────────

    const filteredModels = getFilteredModels();
    const knownModel = filteredModels.find((model) => model.id === config.LLM_MODEL);
    const selectedProvider = knownModel?.provider || getModelProvider(config.LLM_MODEL);
    const providerOptions = Object.keys(PROVIDER_LABELS).filter((provider) => provider !== "custom");
    const recommendedModels = selectedProvider === 'custom'
        ? []
        : getRecommendedModels(filteredModels, selectedProvider);
    const additionalModels = selectedProvider === 'custom'
        ? []
        : getAdditionalModels(filteredModels, selectedProvider);
    const selectedHiddenModel = additionalModels.find((model) => model.id === config.LLM_MODEL);
    const recommendedDisplayModels = selectedHiddenModel
        ? [selectedHiddenModel, ...recommendedModels]
        : recommendedModels;
    const additionalDisplayModels = selectedHiddenModel
        ? additionalModels.filter((model) => model.id !== selectedHiddenModel.id)
        : additionalModels;
    const connectedAiProviders = AI_PROVIDER_SECRETS.filter(({ key }) => isSecretConfigured(key)).length;
    const dashboardProtected = isSecretConfigured("APP_API_KEY");

    const selectedKey = PROVIDER_SECRET_KEYS[selectedProvider];
    const visibleProviders = showAllProviderKeys
        ? AI_PROVIDER_SECRETS
        : AI_PROVIDER_SECRETS.filter(({ key }) => key === selectedKey || isSecretConfigured(key));
    const hiddenCount = AI_PROVIDER_SECRETS.length - visibleProviders.length;

    const pathsArr = getAllowedPaths(config);
    const customModelVisible = selectedProvider === "custom" || !filteredModels.find(m => m.id === config.LLM_MODEL) || config.LLM_MODEL?.startsWith("ollama/");

    // ── tabs ────────────────────────────────────────────────────────────────

    const aiTab = (
        <div>
            <SettingsSection
                icon={Cpu}
                title="Model"
                description="The provider and model LimeBot thinks with."
            >
                <Field
                    label="Provider"
                    htmlFor="provider"
                    hint="Start with a short recommended list per provider. Expand only when you need niche or experimental models."
                >
                    <Select
                        value={selectedProvider}
                        onValueChange={(value) => {
                            setShowAllModels(false);
                            if (value === "custom") {
                                handleChange("LLM_MODEL", config.LLM_MODEL?.trim() || DEFAULT_CUSTOM_MODEL);
                                return;
                            }
                            handleChange(
                                "LLM_MODEL",
                                getInitialModelForProvider(filteredModels, value) || DEFAULT_MODEL_BY_PROVIDER[value],
                            );
                        }}
                    >
                        <SelectTrigger id="provider">
                            <SelectValue placeholder="Select Provider" />
                        </SelectTrigger>
                        <SelectContent>
                            {providerOptions.map((provider) => (
                                <SelectItem key={provider} value={provider}>
                                    {PROVIDER_LABELS[provider] || provider}
                                </SelectItem>
                            ))}
                            <SelectSeparator />
                            <SelectItem value="custom">{PROVIDER_LABELS.custom}</SelectItem>
                        </SelectContent>
                    </Select>
                </Field>

                <Field label="LLM model" htmlFor="model">
                    <Select
                        disabled={modelsLoading}
                        value={
                            modelsLoading
                                ? "loading"
                                : selectedProvider === "custom"
                                    ? "custom"
                                    : filteredModels.find(m => m.id === config.LLM_MODEL)
                                    ? config.LLM_MODEL
                                    : "custom"
                        }
                        onValueChange={(val) => {
                            if (val === "loading") return;
                            if (val === "custom") {
                                const isCurrentlyKnown = filteredModels.find(m => m.id === config.LLM_MODEL);
                                if (isCurrentlyKnown) {
                                    handleChange("LLM_MODEL", "");
                                }
                            } else {
                                handleChange("LLM_MODEL", val);
                            }
                        }}
                    >
                        <SelectTrigger id="model">
                            <SelectValue placeholder={modelsLoading ? "Scanning Models..." : "Select Model"} />
                        </SelectTrigger>
                        <SelectContent>
                            {modelsLoading ? (
                                <SelectItem value="loading" disabled className="text-muted-foreground flex items-center gap-2">
                                    <RefreshCw className="h-3 w-3 animate-spin" /> Scanning Models...
                                </SelectItem>
                            ) : (
                                <>
                                    <SelectItem value="custom" className="font-semibold text-primary">
                                        Custom / Local Model
                                    </SelectItem>
                                    {recommendedDisplayModels.length > 0 && (
                                        <>
                                            <SelectSeparator />
                                            <SelectGroup>
                                                <SelectLabel>Recommended</SelectLabel>
                                                {recommendedDisplayModels.map((model) => (
                                                    <SelectItem key={model.id} value={model.id}>
                                                        {model.name}
                                                    </SelectItem>
                                                ))}
                                            </SelectGroup>
                                        </>
                                    )}
                                    {showAllModels && additionalDisplayModels.length > 0 && (
                                        <>
                                            <SelectSeparator />
                                            <SelectGroup>
                                                <SelectLabel>All {PROVIDER_LABELS[selectedProvider] || selectedProvider} Models</SelectLabel>
                                                {additionalDisplayModels.map((model) => (
                                                    <SelectItem key={model.id} value={model.id}>
                                                        {model.name}
                                                    </SelectItem>
                                                ))}
                                            </SelectGroup>
                                        </>
                                    )}
                                </>
                            )}
                        </SelectContent>
                    </Select>

                    {modelsLoading && (
                        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                            <Loader2 className="h-3 w-3 animate-spin" /> Loading the model list
                        </p>
                    )}

                    {modelsError && !modelsLoading && (
                        <p role="status" className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-amber-500">
                            <AlertTriangle className="h-3 w-3 shrink-0" />
                            <span>
                                Could not load the model list ({modelsError.toLowerCase()}). You can still type a model name under Custom.
                            </span>
                            <button
                                type="button"
                                onClick={() => void fetchModels()}
                                className="font-medium underline underline-offset-2 hover:text-foreground"
                            >
                                Try again
                            </button>
                        </p>
                    )}

                    {selectedProvider !== 'custom' && additionalDisplayModels.length > 0 && (
                        <div className="flex flex-wrap items-center justify-between gap-x-3 text-xs text-muted-foreground">
                            <span>
                                Showing {recommendedDisplayModels.length} recommended model{recommendedDisplayModels.length === 1 ? '' : 's'} first.
                            </span>
                            <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                className="h-7 px-2 text-xs"
                                onClick={() => setShowAllModels((prev) => !prev)}
                            >
                                {showAllModels
                                    ? 'Show recommended only'
                                    : `Show all ${recommendedDisplayModels.length + additionalDisplayModels.length} models`}
                            </Button>
                        </div>
                    )}

                    {customModelVisible && (
                        <div className="space-y-1.5 pt-1 animate-in fade-in slide-in-from-top-2">
                            <label htmlFor="custom_model" className="text-xs text-muted-foreground">Custom model name (e.g. ollama/llama3)</label>
                            <Input
                                id="custom_model"
                                value={config.LLM_MODEL || ""}
                                onChange={(e) => handleChange("LLM_MODEL", e.target.value)}
                                placeholder="provider/model-name"
                                className="font-mono text-sm"
                            />
                        </div>
                    )}
                </Field>

                {urlField(
                    "LLM_BASE_URL",
                    "base_url",
                    "LLM base URL",
                    "Required for local models (Ollama, LocalAI). Leave empty for cloud providers.",
                    "e.g. http://localhost:11434",
                    true,
                )}

                {urlField(
                    "LLM_PROXY_URL",
                    "llm_proxy_url",
                    "LLM proxy / gateway URL",
                    "Routes all LLM traffic through external security or caching middleware. Overrides default provider endpoints.",
                    "http://localhost:8080/v1 (e.g., Open Guardian, AI Gateway)",
                    true,
                )}
            </SettingsSection>

            <SettingsSection
                icon={Key}
                title="Provider keys"
                description="Add only the providers you use. Keys stay redacted after saving."
                badge={<StatusPill tone={connectedAiProviders > 0 ? 'ok' : 'muted'}>{connectedAiProviders} of {AI_PROVIDER_SECRETS.length} connected</StatusPill>}
            >
                {visibleProviders.length > 0 ? (
                    <div className="divide-y divide-border/40">
                        {visibleProviders.map(({ key, label, placeholder, note }) => renderSecretRow(key, label, placeholder, note))}
                    </div>
                ) : (
                    <Note>The selected local/custom provider does not need a cloud API key.</Note>
                )}
                {(hiddenCount > 0 || showAllProviderKeys) && (
                    <Button type="button" variant="ghost" size="sm" className="-ml-2 h-8 px-2 text-xs text-muted-foreground" onClick={() => setShowAllProviderKeys((value) => !value)}>
                        {showAllProviderKeys ? 'Hide unused providers' : `Show ${hiddenCount} other providers`}
                    </Button>
                )}
            </SettingsSection>

            <SettingsSection
                icon={User}
                title="Personalization"
                description="Configure how the bot interacts with users."
            >
                <Field
                    label="Adaptive persona"
                    htmlFor="dynamic_personality"
                    hint="Allows the bot to learn from your conversations, adjusting its tone and relationship level dynamically based on interactions."
                    badge={<StatusPill tone="info">Experimental</StatusPill>}
                    inline
                >
                    <Switch
                        id="dynamic_personality"
                        checked={config.ENABLE_DYNAMIC_PERSONALITY === 'true'}
                        onCheckedChange={(checked) => handleChange('ENABLE_DYNAMIC_PERSONALITY', checked ? 'true' : 'false')}
                    />
                </Field>
            </SettingsSection>
        </div>
    );

    const capabilitiesTab = (
        <div>
            <div className="pt-6">
                <Note title="Connect only what LimeBot should use">
                    Every service below is optional. Channel tokens remain under Channels &amp; Presence so connection settings stay with their channel.
                </Note>
            </div>

            <SettingsSection
                icon={Globe}
                title="Web search"
                description="Live search is host-owned: one web_search tool for web, news, and images. Playwright may fetch the page internally; the model never drives a search engine."
            >
                <p className="text-sm text-muted-foreground">
                    Install the browser once from the LimeBot folder. After that, <code className="font-mono text-xs">web_search</code> works without a separate search API key.
                </p>
                <div className="space-y-2">
                    <code className="block break-all rounded-md bg-muted/60 px-3 py-2 font-mono text-xs">npm run lime-bot setup -- --recommended</code>
                    <p className="text-xs text-muted-foreground">
                        Or: <span className="break-all font-mono">npm run lime-bot feature install browser &amp;&amp; npm run install-browser</span>
                    </p>
                </div>
            </SettingsSection>

            <SettingsSection
                icon={PlugZap}
                title="Video understanding"
                description="Optional transcription fallback for videos without captions."
                badge={
                    <StatusPill tone={isSecretConfigured("OPENAI_API_KEY") ? 'ok' : 'muted'}>
                        {isSecretConfigured("OPENAI_API_KEY") ? 'OpenAI connected' : 'Uses captions without a key'}
                    </StatusPill>
                }
            >
                <Field
                    label="Use OpenAI Whisper"
                    htmlFor="video_whisper_enabled"
                    hint="When enabled, caption-less video audio is uploaded to OpenAI for transcription. Uses the existing OpenAI API key and is disabled by default."
                    inline
                >
                    <Switch
                        id="video_whisper_enabled"
                        checked={config.VIDEO_WHISPER_ENABLED === 'true'}
                        onCheckedChange={(checked) => handleChange('VIDEO_WHISPER_ENABLED', checked ? 'true' : 'false')}
                    />
                </Field>
            </SettingsSection>

            <SettingsSection
                icon={Key}
                title="Voice & audio"
                description="Text-to-speech key. Manage voices and playback in the Voice tab."
            >
                <div>{renderSecretRow("ELEVENLABS_API_KEY", "ElevenLabs", "sk_...", "Text-to-speech voices and playable audio replies")}</div>
            </SettingsSection>
        </div>
    );

    const securityTab = (
        <div>
            <SettingsSection
                icon={ShieldCheck}
                tone={dashboardProtected ? 'default' : 'warn'}
                title="Dashboard access"
                description="Protects the LimeBot dashboard and API. This is not an AI provider credential."
                badge={
                    <StatusPill tone={dashboardProtected ? 'ok' : 'warn'}>
                        {dashboardProtected ? 'Protected' : 'Not configured'}
                    </StatusPill>
                }
            >
                <Field label="Access key" htmlFor="app_api_key">
                    <div className="flex gap-2">
                        <Input
                            id="app_api_key"
                            type="password"
                            autoComplete="off"
                            value={secretDrafts.APP_API_KEY || ""}
                            onChange={(e) => handleSecretChange("APP_API_KEY", e.target.value)}
                            placeholder={getSecretPlaceholder(secretMeta, 'APP_API_KEY', 'Generate or enter an access key')}
                        />
                        <Button type="button" variant="outline" size="icon" className="shrink-0" onClick={() => handleSecretChange("APP_API_KEY", crypto.randomUUID())} title="Generate new key" aria-label="Generate new key">
                            <RefreshCw className="h-4 w-4" />
                        </Button>
                        <Button type="button" variant="outline" size="icon" className="shrink-0" onClick={() => markSecretForClear("APP_API_KEY")} title="Clear access key" aria-label="Clear access key" disabled={!dashboardProtected}>
                            <Trash className="h-4 w-4" />
                        </Button>
                    </div>
                    {clearedSecrets.includes("APP_API_KEY") && <p className="text-xs text-amber-500">The access key will be removed when you save.</p>}
                </Field>
                {!dashboardProtected && (
                    <Note tone="warn" title="Anyone who can reach this address can use the dashboard">
                        Set an access key before exposing LimeBot beyond this computer.
                    </Note>
                )}
                <p className="text-xs text-muted-foreground">
                    Changing this key signs clients out. Save it somewhere secure before restarting LimeBot.
                </p>
            </SettingsSection>

            <SettingsSection
                icon={Cpu}
                title="Safety & execution"
                description="Choose how sensitive tools are reviewed before LimeBot runs them."
            >
                <Field
                    label="Sensitive tool policy"
                    htmlFor="approval_policy"
                    hint="Review ignores session approvals. Autonomous still obeys hard path and command safety checks."
                >
                    <Select
                        value={config.APPROVAL_POLICY_PROFILE || (config.AUTONOMOUS_MODE === 'true' ? 'autonomous' : 'manual')}
                        onValueChange={(value) => handleChange('APPROVAL_POLICY_PROFILE', value)}
                    >
                        <SelectTrigger id="approval_policy">
                            <SelectValue placeholder="Select an approval policy" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="manual">Manual - confirm sensitive actions</SelectItem>
                            <SelectItem value="session">Session - remember explicit approvals</SelectItem>
                            <SelectItem value="review">Review - confirm every sensitive action</SelectItem>
                            <SelectItem value="autonomous">Autonomous - bypass confirmations</SelectItem>
                        </SelectContent>
                    </Select>
                </Field>

                {numberField(
                    "MAX_ITERATIONS",
                    "max_iterations",
                    "Max tool interactions",
                    "The maximum number of tools the bot can run autonomously in a single response before forcefully stopping. Prevents infinite loops.",
                    "30",
                    { min: "1", max: "200" },
                )}

                {numberField(
                    "COMMAND_TIMEOUT",
                    "command_timeout",
                    "Command timeout (seconds)",
                    "The configured duration for a command. Set to 0 to use the hard safety cap below; commands never wait forever.",
                    "300.0",
                    { min: "0" },
                )}

                {numberField(
                    "RUN_COMMAND_MAX_SECONDS",
                    "run_command_max_seconds",
                    "One-shot safety cap (seconds)",
                    "Absolute maximum for run_command, including when Command Timeout is 0. This prevents a server or prompt from leaving the chat stuck.",
                    "180",
                    { min: "10", max: "3600" },
                )}

                {numberField(
                    "STALL_TIMEOUT",
                    "stall_timeout",
                    "Stall detection timeout (seconds)",
                    "If a command produces no output for this many seconds, it's assumed to be waiting for interactive input and gets killed. Set to 0 to disable stall detection.",
                    "30",
                    { min: "0" },
                )}

                <Field
                    label={<span className={config.ALLOW_UNSAFE_COMMANDS === 'true' ? "text-amber-500" : undefined}>Allow unsafe commands</span>}
                    htmlFor="allow_unsafe_commands"
                    hint={
                        <>
                            Allows shell operators such as redirects ({'>'}), semicolons, and pipes, and also permits privileged commands like <code className="font-mono">sudo</code>, <code className="font-mono">chmod</code>, and <code className="font-mono">chown</code>. Useful for advanced workflows, but it reduces sandboxing.
                        </>
                    }
                    inline
                >
                    <Switch
                        id="allow_unsafe_commands"
                        checked={config.ALLOW_UNSAFE_COMMANDS === 'true'}
                        onCheckedChange={(checked) => handleChange('ALLOW_UNSAFE_COMMANDS', checked ? 'true' : 'false')}
                    />
                </Field>
            </SettingsSection>

            <SettingsSection
                icon={Globe}
                title="Browser runtime"
                description="Control whether the browser tool uses isolated LimeBot profiles, a shared LimeBot profile, your system browser profile, or a live browser session over CDP."
            >
                <Field
                    label="Browser mode"
                    htmlFor="browser_mode"
                    hint={
                        <>
                            <code className="font-mono">isolated</code> keeps one browser profile per chat. <code className="font-mono">shared</code> reuses one LimeBot profile. <code className="font-mono">system</code> launches against your real Chrome/Edge profile. <code className="font-mono">attach</code> connects to an already-running browser with remote debugging enabled.
                        </>
                    }
                >
                    <Select
                        value={config.BROWSER_MODE || "isolated"}
                        onValueChange={(value) => handleChange("BROWSER_MODE", value)}
                    >
                        <SelectTrigger id="browser_mode">
                            <SelectValue placeholder="Select browser mode" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="isolated">isolated</SelectItem>
                            <SelectItem value="shared">shared</SelectItem>
                            <SelectItem value="system">system</SelectItem>
                            <SelectItem value="attach">attach</SelectItem>
                        </SelectContent>
                    </Select>
                </Field>

                <Field
                    label="Browser channel"
                    htmlFor="browser_channel"
                    hint={<>Optional. Recommended for <code className="font-mono">system</code> mode so LimeBot opens the same browser family as your real profile.</>}
                >
                    <Input
                        id="browser_channel"
                        value={config.BROWSER_CHANNEL || ""}
                        onChange={(e) => handleChange("BROWSER_CHANNEL", e.target.value)}
                        placeholder="chrome, msedge, or chromium"
                        className="font-mono text-sm"
                    />
                </Field>

                {urlField(
                    "BROWSER_CDP_URL",
                    "browser_cdp_url",
                    "CDP URL",
                    <>Used by <code className="font-mono">attach</code> mode. Start Chrome or Edge with <code className="font-mono">--remote-debugging-port=9222</code> to reuse the live logged-in session.</>,
                    "http://127.0.0.1:9222",
                )}

                <Field
                    label="Browser user data dir"
                    htmlFor="browser_user_data_dir"
                    hint={<>Optional override for <code className="font-mono">system</code> mode. Leave empty to auto-detect a local Chrome/Edge/Chromium profile.</>}
                >
                    <Input
                        id="browser_user_data_dir"
                        value={config.BROWSER_USER_DATA_DIR || ""}
                        onChange={(e) => handleChange("BROWSER_USER_DATA_DIR", e.target.value)}
                        placeholder="C:\\Users\\you\\AppData\\Local\\Google\\Chrome\\User Data"
                        className="font-mono text-sm"
                    />
                </Field>

                <Field
                    label="Profile directory"
                    htmlFor="browser_profile_directory"
                    hint="Optional sub-profile inside the browser user data directory. Useful when your login lives in a non-default Chrome/Edge profile."
                >
                    <Input
                        id="browser_profile_directory"
                        value={config.BROWSER_PROFILE_DIRECTORY || ""}
                        onChange={(e) => handleChange("BROWSER_PROFILE_DIRECTORY", e.target.value)}
                        placeholder="Default or Profile 1"
                        className="font-mono text-sm"
                    />
                </Field>
            </SettingsSection>

            <SettingsSection
                icon={Settings}
                title="Workspace access"
                description="Control file access and permissions."
            >
                <Field label="Allowed file paths" htmlFor="allowed_paths_0" hint="Absolute paths the bot is allowed to access and edit.">
                    <div className="space-y-2">
                        {pathsArr.length === 0 && (
                            <p className="text-xs text-muted-foreground">No extra paths. LimeBot can only reach its own project folder.</p>
                        )}
                        {pathsArr.map((path, idx) => {
                            const rowError = errors[`ALLOWED_PATHS:${idx}`];
                            const showError = !!rowError && blankPathsTouched;
                            return (
                                <div key={idx} className="space-y-1">
                                    <div className="flex gap-2 isolate">
                                        <Input
                                            id={`allowed_paths_${idx}`}
                                            value={path}
                                            onChange={(e) => {
                                                const paths = [...pathsArr];
                                                paths[idx] = e.target.value;
                                                handleChange("ALLOWED_PATHS", paths);
                                            }}
                                            onBlur={() => { if (!path.trim()) setBlankPathsTouched(true); }}
                                            placeholder="Absolute path..."
                                            aria-invalid={showError}
                                            className={cn("font-mono text-sm", showError && invalidInputClass)}
                                        />
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            className="shrink-0 text-muted-foreground hover:bg-red-500/10 hover:text-red-500"
                                            title="Remove path"
                                            aria-label="Remove path"
                                            onClick={() => {
                                                const paths = [...pathsArr];
                                                paths.splice(idx, 1);
                                                handleChange("ALLOWED_PATHS", paths);
                                            }}
                                        >
                                            <Trash className="h-4 w-4" />
                                        </Button>
                                    </div>
                                    {showError && <FieldError message={rowError} />}
                                </div>
                            );
                        })}
                        <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="-ml-2 h-8 gap-1.5 px-2 text-xs text-muted-foreground"
                            onClick={() => handleChange("ALLOWED_PATHS", [...pathsArr, ""])}
                        >
                            <Plus className="h-3.5 w-3.5" />
                            Add path
                        </Button>
                    </div>
                </Field>
            </SettingsSection>

            <SettingsSection
                icon={Globe}
                title="Remote access"
                description="The port LimeBot listens on and the addresses it can be reached at."
            >
                {numberField(
                    "WEB_PORT",
                    "web_port",
                    "Web server port",
                    "The port used by the web server and API. Changing this will require a restart.",
                    "8000",
                    { min: "1", max: "65535" },
                )}
                <RemoteStatus port={config.WEB_PORT || "8000"} />
            </SettingsSection>
        </div>
    );

    const securityBadge = securityErrorCount > 0
        ? <ErrorCountBadge count={securityErrorCount} />
        : !dashboardProtected
            ? <span className="h-1.5 w-1.5 rounded-full bg-amber-500" title="Needs attention: the dashboard has no access key"><span className="sr-only">Needs attention</span></span>
            : undefined;

    return (
        <SettingsPage
            {...pageHeader}
            saveBar={
                <SaveBar
                    dirty={dirty}
                    saving={saving}
                    onSave={() => void handleSave()}
                    onDiscard={handleDiscard}
                    error={saveError}
                    onDismissError={clearError}
                    disabled={invalidCount > 0}
                    savedAt={savedAt}
                    changeCount={changeCount}
                    unsavedLabel={invalidCount > 0 ? `Fix ${invalidCount} highlighted value${invalidCount === 1 ? '' : 's'} to save` : undefined}
                />
            }
        >
            <SettingsTabs
                value={tab}
                onValueChange={setTab}
                tabs={[
                    {
                        value: "ai",
                        label: "AI & Models",
                        short: "AI",
                        icon: BrainCircuit,
                        badge: aiErrorCount > 0 ? <ErrorCountBadge count={aiErrorCount} /> : undefined,
                        content: aiTab,
                    },
                    { value: "capabilities", label: "Capabilities", short: "Tools", icon: PlugZap, content: capabilitiesTab },
                    {
                        value: "security",
                        label: "Security & Runtime",
                        short: "Safety",
                        icon: ShieldCheck,
                        badge: securityBadge,
                        content: securityTab,
                    },
                ]}
            />
        </SettingsPage>
    );
}

type NetworkInterface = {
    ip: string;
    is_tailscale: boolean;
};

type RemoteNetworkStatus = {
    error?: string;
    interfaces?: NetworkInterface[];
};

function RemoteStatus({ port }: { port: string }) {
    const [status, setStatus] = useState<RemoteNetworkStatus | null>(null);
    const [loading, setLoading] = useState(true);
    const [attempt, setAttempt] = useState(0);

    useEffect(() => {
        let cancelled = false;
        axios.get(`${API_BASE_URL}/api/setup/tailscale`)
            .then(res => { if (!cancelled) setStatus(res.data); })
            .catch(err => {
                console.error("Failed to check network:", err);
                if (!cancelled) setStatus(null);
            })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [attempt]);

    if (loading) {
        return (
            <p className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Scanning network...
            </p>
        );
    }

    if (!status || status.error) {
        return (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                <span className="flex items-center gap-1.5 text-amber-500">
                    <AlertTriangle className="h-3.5 w-3.5" /> Network check failed.
                </span>
                <button type="button" onClick={() => { setLoading(true); setAttempt((n) => n + 1); }} className="font-medium text-muted-foreground underline underline-offset-2 hover:text-foreground">
                    Try again
                </button>
            </div>
        );
    }

    const tailscale = status.interfaces?.find((item) => item.is_tailscale);
    const lan = status.interfaces?.find((item) => !item.is_tailscale && item.ip !== '127.0.0.1');

    return (
        <div className="space-y-3">
            <h3 className="text-sm font-medium">Network interfaces</h3>
            <div className="divide-y divide-border/40">
                {tailscale ? (
                    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2 first:pt-0">
                        <span className="flex items-center gap-2 text-sm text-primary">
                            <span className="relative flex h-2 w-2">
                                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75"></span>
                                <span className="relative inline-flex h-2 w-2 rounded-full bg-primary"></span>
                            </span>
                            Tailscale active
                        </span>
                        <code className="max-w-full cursor-text select-all break-all rounded bg-muted/60 px-2 py-1 font-mono text-xs">
                            http://{tailscale.ip}:{port}
                        </code>
                    </div>
                ) : (
                    <div className="py-2 first:pt-0">
                        <p className="text-sm text-amber-500">Tailscale not detected</p>
                        <p className="text-xs text-muted-foreground">Install Tailscale to access this bot securely from anywhere.</p>
                    </div>
                )}

                {lan && (
                    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2 last:pb-0">
                        <span className="text-sm text-muted-foreground">LAN access</span>
                        <code className="max-w-full cursor-text select-all break-all rounded bg-muted/60 px-2 py-1 font-mono text-xs">
                            http://{lan.ip}:{port}
                        </code>
                    </div>
                )}
            </div>
        </div>
    );
}
