/** Types and pure helpers for the Channels page. No React in here. */

export interface ConfigState {
    DISCORD_ALLOW_FROM?: string;
    DISCORD_ALLOW_CHANNELS?: string;
    DISCORD_ACTIVITY_TYPE?: string;
    DISCORD_ACTIVITY_TEXT?: string;
    DISCORD_STATUS?: string;
    ENABLE_DISCORD?: string;
    ENABLE_TELEGRAM?: string;
    TELEGRAM_API_BASE?: string;
    TELEGRAM_ALLOW_FROM?: string;
    TELEGRAM_ALLOW_CHATS?: string;
    TELEGRAM_POLL_TIMEOUT?: string;
    PERSONALITY_WHITELIST?: string;
    WHATSAPP_ALLOW_FROM?: string;
    ENABLE_WHATSAPP?: string;
    [key: string]: string | undefined;
}

export interface StyleOverride {
    tone?: string;
    verbosity?: string;
    emoji_usage?: string;
    signature?: string;
    prefix?: string;
    suffix?: string;
    max_length?: number;
}

export interface DiscordUiConfig {
    signature?: string;
    emoji_set?: string[];
    verbosity_limits?: { short?: number; medium?: number; long?: number };
    tone_prefixes?: Record<string, string>;
    style_overrides?: {
        default?: StyleOverride;
        guilds?: Record<string, StyleOverride>;
        channels?: Record<string, StyleOverride>;
    };
    embed_theme?: { default?: string; guilds?: Record<string, string> };
    nickname_templates?: { default?: string; guilds?: Record<string, string> };
    avatar_overrides?: { global?: string };
}

export interface TelegramStatus {
    enabled: boolean;
    status: string;
    connected: boolean;
    username?: string | null;
    bot_id?: number | null;
    display_name?: string | null;
    last_error?: string;
}

export interface PersonaDraft {
    name?: string;
    emoji?: string;
    pfp_url?: string;
    style?: string;
    discord_style?: string;
    telegram_style?: string;
    whatsapp_style?: string;
    web_style?: string;
    reaction_emojis?: string;
    soul_summary?: string;
    catchphrases?: string;
    interests?: string;
    birthday?: string;
    mood?: string;
    enable_dynamic_personality?: boolean;
    relationships?: Array<{ id: string; name: string; affinity: number; level: string }>;
}

export interface StyleOverrideRow {
    id: string;
    target: string;
    tone: string;
    verbosity: string;
    emoji_usage: string;
    signature: string;
    prefix: string;
    max_length: string;
}

export interface ValueOverrideRow {
    id: string;
    target: string;
    value: string;
}

export interface TelegramVoiceDraft {
    tone: string;
    verbosity: string;
    emojiUsage: string;
    signoff: string;
    notes: string;
}

export type SecretDrafts = Record<string, string>;

export const createStyleOverrideRow = (target = "", source?: StyleOverride): StyleOverrideRow => ({
    id: `${target || "row"}-${Math.random().toString(36).slice(2, 8)}`,
    target,
    tone: source?.tone || "friendly",
    verbosity: source?.verbosity || "medium",
    emoji_usage: source?.emoji_usage || "light",
    signature: source?.signature || "",
    prefix: source?.prefix || "",
    max_length: source?.max_length ? String(source.max_length) : "",
});

export const createValueOverrideRow = (target = "", value = ""): ValueOverrideRow => ({
    id: `${target || "row"}-${Math.random().toString(36).slice(2, 8)}`,
    target,
    value,
});

export const objectToStyleRows = (source?: Record<string, StyleOverride>): StyleOverrideRow[] =>
    Object.entries(source || {}).map(([target, value]) => createStyleOverrideRow(target, value));

export const styleRowsToObject = (rows: StyleOverrideRow[]) =>
    rows.reduce<Record<string, StyleOverride>>((acc, row) => {
        const target = row.target.trim();
        if (!target) return acc;
        acc[target] = {
            tone: row.tone,
            verbosity: row.verbosity,
            emoji_usage: row.emoji_usage,
            signature: row.signature.trim() || undefined,
            prefix: row.prefix.trim() || undefined,
            max_length: row.max_length.trim() ? Number(row.max_length) : undefined,
        };
        return acc;
    }, {});

export const objectToValueRows = (source?: Record<string, string>): ValueOverrideRow[] =>
    Object.entries(source || {}).map(([target, value]) => createValueOverrideRow(target, value));

export const valueRowsToObject = (rows: ValueOverrideRow[]) =>
    rows.reduce<Record<string, string>>((acc, row) => {
        const target = row.target.trim();
        const value = row.value.trim();
        if (!target || !value) return acc;
        acc[target] = value;
        return acc;
    }, {});

export const DEFAULT_TELEGRAM_VOICE: TelegramVoiceDraft = {
    tone: "friendly",
    verbosity: "medium",
    emojiUsage: "light",
    signoff: "",
    notes: "",
};

export const DEFAULT_PERSONA_DRAFT: PersonaDraft = {
    name: "",
    emoji: "",
    pfp_url: "",
    style: "",
    discord_style: "",
    telegram_style: "",
    whatsapp_style: "",
    web_style: "",
    reaction_emojis: "",
    soul_summary: "",
    catchphrases: "",
    interests: "",
    birthday: "",
    mood: "",
    enable_dynamic_personality: false,
    relationships: [],
};

export const clonePersonaDraft = (data?: Partial<PersonaDraft>): PersonaDraft =>
    JSON.parse(JSON.stringify({ ...DEFAULT_PERSONA_DRAFT, ...(data || {}) }));

export const buildTelegramStyle = (draft: TelegramVoiceDraft) => {
    const lines = [
        `Tone: ${draft.tone}.`,
        `Verbosity: ${draft.verbosity}.`,
        `Emoji usage: ${draft.emojiUsage}.`,
        "Optimize for fast mobile reading, short paragraphs, and clear next steps.",
    ];
    if (draft.signoff.trim()) {
        lines.push(`Signoff preference: ${draft.signoff.trim()}.`);
    }
    if (draft.notes.trim()) {
        lines.push(draft.notes.trim());
    }
    return lines.join(" ");
};

export const TONES = [
    { value: "neutral", label: "Neutral" },
    { value: "friendly", label: "Friendly" },
    { value: "direct", label: "Direct" },
    { value: "formal", label: "Formal" },
];
export const TELEGRAM_TONES = [
    { value: "friendly", label: "Friendly" },
    { value: "direct", label: "Direct" },
    { value: "formal", label: "Formal" },
    { value: "playful", label: "Playful" },
];
export const VERBOSITIES = [
    { value: "short", label: "Short" },
    { value: "medium", label: "Medium" },
    { value: "long", label: "Long" },
];
export const EMOJI_USAGE = [
    { value: "none", label: "None" },
    { value: "light", label: "Light" },
    { value: "heavy", label: "Heavy" },
];
