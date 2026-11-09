// Mock handlers for the config pages. Register with mock(method, pattern, handler).
import { mock, mockFlags, MockHttpError } from "../mock-api";

type MockSecret = { configured: boolean; masked: string; last4: string };

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
];

const unset = (): MockSecret => ({ configured: false, masked: "", last4: "" });
const stored = (last4: string): MockSecret => ({ configured: true, masked: `••••${last4}`, last4 });

function seedEnv(): Record<string, unknown> {
    if (mockFlags.empty) {
        // A fresh install: nothing chosen yet.
        return { ALLOWED_PATHS: [] };
    }
    return {
        LLM_MODEL: "gemini/gemini-3.8-flash",
        LLM_BASE_URL: "",
        LLM_PROXY_URL: "",
        ENABLE_DYNAMIC_PERSONALITY: "true",
        VIDEO_WHISPER_ENABLED: "false",
        APPROVAL_POLICY_PROFILE: "manual",
        MAX_ITERATIONS: "40",
        COMMAND_TIMEOUT: "300.0",
        RUN_COMMAND_MAX_SECONDS: "180",
        STALL_TIMEOUT: "30",
        ALLOW_UNSAFE_COMMANDS: "false",
        WEB_PORT: "8000",
        BROWSER_MODE: "isolated",
        BROWSER_CHANNEL: "",
        BROWSER_CDP_URL: "",
        BROWSER_USER_DATA_DIR: "",
        BROWSER_PROFILE_DIRECTORY: "",
        ALLOWED_PATHS: ["D:\\Code\\LimeBot-OS", "D:\\Code\\scratch"],
    };
}

function seedSecrets(): Record<string, MockSecret> {
    const secrets: Record<string, MockSecret> = Object.fromEntries(SECRET_KEYS.map((key) => [key, unset()]));
    if (!mockFlags.empty) {
        secrets.APP_API_KEY = stored("a91c");
        secrets.GEMINI_API_KEY = stored("x4Qz");
        secrets.OPENAI_API_KEY = stored("7pLm");
        secrets.ANTHROPIC_API_KEY = stored("9fR2");
    }
    return secrets;
}

let env: Record<string, unknown> | null = null;
let secrets: Record<string, MockSecret> | null = null;

function ensureState() {
    // Seeded lazily so ?empty=1 is read once the page actually asks.
    if (!env || !secrets) {
        env = seedEnv();
        secrets = seedSecrets();
    }
    return { env, secrets };
}

mock("GET", /^\/api\/config$/, () => {
    const state = ensureState();
    return { env: { ...state.env }, secrets: { ...state.secrets } };
});

mock("POST", /^\/api\/config$/, ({ data }) => {
    const state = ensureState();
    const body = (data || {}) as { env?: Record<string, unknown>; clear_secrets?: string[] };
    const incoming = body.env || {};

    const port = Number(incoming.WEB_PORT);
    if (incoming.WEB_PORT !== undefined && (!Number.isInteger(port) || port < 1 || port > 65535)) {
        throw new MockHttpError(422, { detail: "WEB_PORT must be a whole number between 1 and 65535" });
    }
    const iterations = Number(incoming.MAX_ITERATIONS);
    if (incoming.MAX_ITERATIONS !== undefined && (!Number.isInteger(iterations) || iterations < 1 || iterations > 200)) {
        throw new MockHttpError(422, { detail: "MAX_ITERATIONS must be between 1 and 200" });
    }

    const next: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(incoming)) {
        if (SECRET_KEYS.includes(key)) {
            if (typeof value === "string" && value.trim()) {
                state.secrets[key] = stored(value.trim().slice(-4));
            }
            continue;
        }
        next[key] = value;
    }
    for (const key of body.clear_secrets || []) {
        if (SECRET_KEYS.includes(key)) state.secrets[key] = unset();
    }
    env = next;
    return { status: "ok" };
});

mock("GET", /^\/api\/llm\/models$/, () => {
    if (mockFlags.empty) return { models: [] };
    const provider = (id: string, label: string, ids: string[]) =>
        ids.map((model) => ({ id: `${id}/${model}`, name: label ? `${label} ${model}` : model, provider: id }));
    return {
        models: [
            ...provider("gemini", "", [
                "gemini-3.8-flash",
                "gemini-3.7-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.1-pro-preview",
                "gemini-2.5-flash",
                "gemini-2.0-flash-lite",
            ]),
            ...provider("openai", "", ["gpt-6.1-sol", "gpt-6-astra", "gpt-5.6-luna", "gpt-4o", "gpt-4o-mini", "o3-mini"]),
            ...provider("anthropic", "", [
                "claude-opus-5",
                "claude-sonnet-5",
                "claude-haiku-4-5-20251001",
                "claude-3-7-sonnet-latest",
            ]),
            ...provider("deepseek", "", ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat"]),
            ...provider("xai", "", ["grok-4.7", "grok-4.20", "grok-4.3"]),
            ...provider("moonshot", "", ["kimi-k3", "kimi-k2.6"]),
            ...provider("qwen", "", ["qwen3.5-plus", "qwen3.5-flash"]),
            ...provider("nvidia", "", ["deepseek-ai/deepseek-v4-flash-0731", "openai/gpt-oss-120b"]),
            ...provider("openrouter", "", ["openai/gpt-6.1-sol", "anthropic/claude-opus-5.5", "google/gemini-3.8-flash"]),
        ],
    };
});

mock("GET", /^\/api\/setup\/tailscale$/, () => {
    if (mockFlags.empty) {
        return { interfaces: [{ ip: "127.0.0.1", is_tailscale: false }] };
    }
    return {
        interfaces: [
            { ip: "127.0.0.1", is_tailscale: false },
            { ip: "192.168.1.42", is_tailscale: false },
            { ip: "100.101.102.103", is_tailscale: true },
        ],
    };
});
