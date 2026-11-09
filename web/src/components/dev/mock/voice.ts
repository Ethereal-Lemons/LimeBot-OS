// Mock handlers for the voice, MCP and persona pages. Register with mock(method, pattern, handler).
//
// Extra flags on top of the shared ones in ../mock-api:
//   &nokey=1          ElevenLabs key is not configured (voice page shows its empty state)
//   &voices=fail      the voice list request fails with a server error
//   &tts=fail         preview synthesis answers 402 subscription_required
//   &tts=401          preview synthesis answers with a rejected-key provider error
//   &tts=429          preview synthesis answers with a rate limit
import { mock, mockFlags, MockHttpError } from "../mock-api";

const param = (name: string) => new URLSearchParams(window.location.search).get(name);

// ── voice ───────────────────────────────────────────────────────────────────

/** A short valid mono WAV as a data URI, so the preview player has something to play. */
function tinyWav(frequency = 440): string {
    const rate = 8000;
    const samples = 3200;
    const bytes = new Uint8Array(44 + samples);
    const view = new DataView(bytes.buffer);
    const text = (offset: number, value: string) => {
        for (let index = 0; index < value.length; index++) bytes[offset + index] = value.charCodeAt(index);
    };
    text(0, "RIFF");
    view.setUint32(4, 36 + samples, true);
    text(8, "WAVE");
    text(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, rate, true);
    view.setUint32(28, rate, true);
    view.setUint16(32, 1, true);
    view.setUint16(34, 8, true);
    text(36, "data");
    view.setUint32(40, samples, true);
    for (let index = 0; index < samples; index++) {
        const fade = Math.min(1, index / 200, (samples - index) / 400);
        bytes[44 + index] = 128 + Math.round(50 * fade * Math.sin((2 * Math.PI * frequency * index) / rate));
    }
    let binary = "";
    for (let index = 0; index < bytes.length; index++) binary += String.fromCharCode(bytes[index]);
    return `data:audio/wav;base64,${btoa(binary)}`;
}

const VOICES = [
    { voice_id: "rachel", name: "Rachel", category: "premade", description: "Calm, clear American female voice. Good for narration and assistants.", preview_url: tinyWav(392) },
    { voice_id: "adam", name: "Adam", category: "premade", description: "Deep, steady American male voice.", preview_url: tinyWav(220) },
    { voice_id: "bella", name: "Bella", category: "premade", description: "Soft and warm, a little playful.", preview_url: tinyWav(494) },
    { voice_id: "jennie-clone", name: "Jennie (my clone)", category: "cloned", description: "", preview_url: tinyWav(330) },
    { voice_id: "narrator-uk", name: "Narrator UK", category: "professional", description: "British narrator tone with a measured pace and crisp consonants.", preview_url: "" },
    { voice_id: "sage", name: "Sage", category: "generated", description: "Gentle, slightly husky voice designed in the voice lab.", preview_url: tinyWav(277) },
];

const voiceState = {
    settings: {
        enabled: true,
        voice_id: "jennie-clone",
        stability: 0.5,
        similarity_boost: 0.75,
        style: 0.2,
        use_speaker_boost: true,
        speed: 1.0,
        model_id: "eleven_multilingual_v2",
        output_format: "mp3_44100_128",
        channels: ["web", "discord"],
        send_text_with_audio: false,
        voice_name: "Jennie, Jen",
        listen_mode: "wake",
        voice_follow_up_seconds: 20,
    } as Record<string, unknown>,
};

mock("GET", /^\/api\/voice\/settings$/, () => ({
    has_key: param("nokey") !== "1",
    settings: voiceState.settings,
}));

mock("POST", /^\/api\/voice\/settings$/, ({ data }) => {
    voiceState.settings = { ...voiceState.settings, ...(data as Record<string, unknown>) };
    return { status: "success", settings: voiceState.settings };
});

mock("GET", /^\/api\/voice\/voices$/, () => {
    if (param("voices") === "fail") {
        throw new MockHttpError(500, { detail: "ElevenLabs voices error (500): upstream service unavailable" });
    }
    return { voices: mockFlags.empty ? [] : VOICES };
});

mock("POST", /^\/api\/voice\/synthesize$/, ({ data }) => {
    const text = String((data as { text?: string } | undefined)?.text ?? "").trim();
    if (!text) throw new MockHttpError(400, { detail: "Text is required" });
    const tts = param("tts");
    if (tts === "fail") throw new MockHttpError(402, { detail: "ElevenLabs TTS error (402): subscription_required" });
    if (tts === "401") {
        throw new MockHttpError(500, {
            detail: 'ElevenLabs TTS error (401): {"detail":{"status":"missing_permissions","message":"The API key you used is missing the permission text_to_speech to execute this operation."}}',
        });
    }
    if (tts === "429") throw new MockHttpError(500, { detail: "ElevenLabs TTS error (429): too_many_concurrent_requests" });
    return { status: "success", url: tinyWav(360) };
});

// ── MCP ─────────────────────────────────────────────────────────────────────

interface McpServer {
    command?: string;
    args?: string[];
    env?: Record<string, string>;
    url?: string;
    type?: string;
}

const SEED_MCP_STATUS: Record<string, string> = {
    filesystem: "Online",
    github: "Online",
    "memory-graph": "Offline",
    "remote-docs": "Error",
};

let mcpServers: Record<string, McpServer> = {
    filesystem: { command: "npx", args: ["-y", "@modelcontextprotocol/server-filesystem", "D:\\Code"], env: {} },
    github: { command: "npx", args: ["-y", "@modelcontextprotocol/server-github"], env: { GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_mock_token" } },
    "memory-graph": { command: "python", args: ["-m", "memory_graph", "--port", "7311"], env: { MEMORY_DIR: "./data/graph" } },
    "remote-docs": { type: "http", url: "https://docs.example.com/mcp", args: [], env: {} },
};

mock("GET", /^\/api\/mcp\/config$/, () => ({ mcpServers: mockFlags.empty ? {} : mcpServers }));

mock("POST", /^\/api\/mcp\/config$/, ({ data }) => {
    const incoming = (data as { mcpServers?: Record<string, McpServer> } | undefined)?.mcpServers;
    if (!incoming || typeof incoming !== "object") return { error: "Missing 'mcpServers' field." };
    for (const [name, cfg] of Object.entries(incoming)) {
        const http = cfg.type === "http" || cfg.type === "sse" || Boolean(cfg.url?.trim());
        if (http ? !cfg.url?.trim() : !cfg.command?.trim()) {
            return { error: http ? `'${name}.url' must be a non-empty string for HTTP MCP.` : `'${name}.command' must be a non-empty string.` };
        }
    }
    mcpServers = incoming;
    return { status: "success", message: "MCP configuration updated" };
});

mock("GET", /^\/api\/mcp\/status$/, () => ({
    status: Object.fromEntries(Object.keys(mcpServers).map((name) => [name, SEED_MCP_STATUS[name] ?? "Online"])),
}));

// ── persona ─────────────────────────────────────────────────────────────────

const PERSONA_CHANNELS = ["web", "discord", "telegram", "whatsapp"];

const persona: Record<string, unknown> = {
    name: "Jennie",
    emoji: "🍋",
    pfp_url: "",
    style: "Warm, quick-witted and direct. Short sentences, a little teasing, never preachy. Explains the why when it matters.",
    discord_style: "Looser and more playful. Lowercase is fine, light emoji, keep replies under four lines.",
    telegram_style: "",
    whatsapp_style: "Brief and friendly, like texting a friend. No markdown.",
    web_style: "",
    reaction_emojis: "happy: 😊, 😂; sad: 😢; excited: 🎉, 🔥",
    soul_summary:
        "Jennie cares about being useful first and charming second. She is honest about what she does not know, protective of the people she talks with, and curious about how things work.",
    catchphrases: "Let's squeeze it; Fresh take incoming",
    interests: "Coding, 3D art, electronic music",
    birthday: "July 15",
    mood: "Upbeat and a bit caffeinated.",
    enable_dynamic_personality: true,
};

const RELATIONSHIPS = [
    { id: "discord-1042", name: "Mantis", affinity: 86, level: "Close friend" },
    { id: "discord-2290", name: "Rhea", affinity: 54, level: "Friendly" },
    { id: "web-guest", name: "Guest", affinity: 12, level: "Stranger" },
];

mock("GET", /^\/api\/persona$/, () => ({ ...persona, relationships: mockFlags.empty ? [] : RELATIONSHIPS }));

mock("PUT", /^\/api\/persona$/, ({ data }) => {
    const body = (data ?? {}) as Record<string, unknown>;
    if (!String(body.name ?? "").trim()) return { error: "Display name cannot be empty" };
    Object.assign(persona, body);
    delete persona.relationships;
    return { status: "success", message: "Persona updated" };
});

mock("POST", /^\/api\/persona\/preview$/, ({ data }) => {
    const body = (data ?? {}) as { persona?: Record<string, string>; channel?: string; user_message?: string };
    const draft = body.persona ?? {};
    const channel = PERSONA_CHANNELS.includes(String(body.channel)) ? String(body.channel) : "web";
    const override = String(draft[`${channel}_style`] ?? "").trim();
    const web = String(draft.web_style ?? "").trim();
    const base = String(draft.style ?? "").trim();
    let style = base;
    let source = "Base style";
    if (channel === "web") {
        if (web) [style, source] = [web, "Web override"];
    } else if (override) {
        [style, source] = [override, `${channel} override`];
    } else if (web) {
        [style, source] = [web, "Web fallback"];
    }
    const name = String(draft.name ?? "").trim() || "LimeBot";
    return {
        channel,
        model: "gemini/gemini-2.5-flash",
        effective_style: style,
        style_source: source,
        system_prompt_excerpt: `# Identity\nYou are ${name}.\n\n# Style (${source})\n${style || "(empty)"}\n\n# Rules\n- Answer in the persona's voice.\n- Use tools when they help.\n... [truncated]`,
        preview_text:
            param("persona") === "error"
                ? null
                : `Hey, I'm ${name}. ${body.user_message ? "On it: " : ""}I can dig through files, run commands, browse the web and keep track of things for you. Tell me what you're working on and I'll jump in.`,
        error: param("persona") === "error" ? "Provider error (429): rate limit reached for this model, retry in a moment" : null,
    };
});
