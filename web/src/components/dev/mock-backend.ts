import axios, { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from "axios";
import { api } from "@/lib/api";

/**
 * Tiny in-memory stand-in for the LimeBot backend, used only by `?ui=pages`.
 * It is installed as the axios adapter, so the real pages run their real
 * request code. Scenarios: ok | empty | loading | network | server.
 */

export type Scenario = "ok" | "empty" | "loading" | "network" | "server";

const now = () => Math.floor(Date.now() / 1000);
const iso = (secondsAgo: number) => new Date(Date.now() - secondsAgo * 1000).toISOString();

const LONG_MEMORY = `## Release checklist

The user ships LimeBot from \`main\` on Fridays. Before tagging:

- run \`npm --prefix web run build\`
- run the Python test suite
- check the changelog entry exists
- confirm the Docker image builds on a clean clone

They prefer short summaries over long ones, and want risks named before a deploy, not after.`;

function fixtures(scenario: Scenario) {
    const empty = scenario === "empty";
    return {
        memories: empty
            ? []
            : [
                  { id: "m1", category: "Preferences", text: "Prefers concise answers and plain language. Dislikes emoji in technical replies.", timestamp: iso(3600 * 3), source: "chat" },
                  { id: "m2", category: "Projects", text: LONG_MEMORY, timestamp: iso(86400 * 2), source: "journal/2026-11-06.md" },
                  { id: "m3", category: "People", text: "Lucía is the QA lead. Send her test evidence as a table, not screenshots.", timestamp: iso(86400 * 5), source: "chat" },
                  { id: "m4", category: "Preferences", text: "Works from Guatemala (UTC-6). Morning reminders should land at 08:30 local time.", timestamp: iso(86400 * 9) },
                  { id: "m5", category: "Projects", text: "The browser companion extension targets Chrome, Edge and Opera GX. Opera GX opens in a tab.", timestamp: iso(86400 * 14), source: "chat" },
              ],
        jobs: empty
            ? []
            : [
                  { id: "a1f3c2", trigger: now() + 12 * 60 + 5, payload: "Remind me to send the QA report to Lucía", active: true, context: { channel: "web", chat_id: "dashboard" }, created_at: now() - 600 },
                  { id: "b77e90", trigger: now() + 3 * 3600 + 1200, payload: "Morning brief: calendar, open PRs and anything blocked", active: true, cron_expr: "0 9 * * 1-5", tz_offset: -360, context: { channel: "discord", chat_id: "1048213377120" }, created_at: now() - 86400 * 3 },
                  { id: "c04d11", trigger: now() + 86400 * 2 + 3600, payload: "Weekly cleanup of the downloads folder", active: true, cron_expr: "30 18 * * 5", tz_offset: -360, context: { channel: "web", chat_id: "dashboard" }, created_at: now() - 86400 * 9 },
                  { id: "d9a2b6", trigger: now() + 7200, payload: "Check the staging deploy and post the result in the team channel", active: false, cron_expr: "*/15 * * * *", tz_offset: -360, context: { channel: "whatsapp", chat_id: "50255501234@c.us" }, created_at: now() - 86400 },
              ],
        sessions: empty
            ? []
            : [
                  { session_id: "default", mode: "isolated", last_active: now() - 4, is_live: true, metadata: { channel: "chromium", headless: true } },
                  { session_id: "research-tab", mode: "shared", last_active: now() - 95, is_live: true, metadata: { channel: "chromium", headless: false } },
                  { session_id: "my-chrome", mode: "attach", last_active: now() - 3600 * 5, is_live: false, metadata: { cdp_url: "http://127.0.0.1:9222" } },
                  { session_id: "system", mode: "system", last_active: now() - 30, is_live: true, metadata: { channel: "msedge" } },
              ],
        contacts: empty
            ? { allowed: [], pending: [], blocked: [], identities: {} }
            : {
                  pending: ["50255501234@c.us", "14155550123@c.us"],
                  allowed: ["50255509876@c.us", "34600123456@c.us", "447911123456@c.us"],
                  blocked: ["12025550199@c.us"],
                  identities: {
                      "50255501234@c.us": { push_name: "Marcos (QA)" },
                      "14155550123@c.us": { push_name: "Unknown caller" },
                      "50255509876@c.us": { push_name: "Lucía", verified_name: "Lucía Ramírez" },
                      "34600123456@c.us": { push_name: "Pedro" },
                      "447911123456@c.us": { push_name: "Pedro" },
                      "12025550199@c.us": { push_name: "Promo Deals" },
                  },
              },
    };
}

export function installMockBackend(scenario: Scenario) {
    const data = fixtures(scenario);
    const env = {
        ENABLE_DISCORD: "true",
        ENABLE_TELEGRAM: "true",
        ENABLE_WHATSAPP: "true",
        DISCORD_ALLOW_FROM: "123456789012345678",
        DISCORD_STATUS: "online",
        DISCORD_ACTIVITY_TYPE: "watching",
        DISCORD_ACTIVITY_TEXT: "the build",
        TELEGRAM_POLL_TIMEOUT: "30",
    };
    const secrets = {
        DISCORD_TOKEN: { configured: true, masked: "••••a1b2", last4: "a1b2" },
        TELEGRAM_BOT_TOKEN: { configured: scenario !== "empty", masked: "••••9fK", last4: "9fK" },
    };

    const fail = (config: InternalAxiosRequestConfig, status?: number, detail = "sqlite3.OperationalError: database is locked"): Promise<never> => {
        const error = new AxiosError(
            status ? `Request failed with status code ${status}` : "Network Error",
            status ? "ERR_BAD_RESPONSE" : "ERR_NETWORK",
            config,
            undefined,
            status
                ? { status, statusText: "", headers: {}, config, data: { detail } }
                : undefined
        );
        return Promise.reject(error);
    };

    const adapter: AxiosAdapter = async (config) => {
        const method = (config.method || "get").toLowerCase();
        const url = (config.url || "").replace(/^https?:\/\/[^/]+/, "").split("?")[0];
        const respond = (body: unknown, status = 200) => Promise.resolve({ data: body, status, statusText: "OK", headers: {}, config });

        if (scenario === "loading") return new Promise(() => undefined);
        await new Promise((resolve) => setTimeout(resolve, 180));
        if (scenario === "network") return fail(config);
        if (scenario === "server" && method === "get") return fail(config, 500);

        // Memory
        if (url === "/api/memory" && method === "get") return respond({ enabled: true, mode: "vector", read_only: false, memories: data.memories });
        if (url.startsWith("/api/memory/") && method === "delete") {
            const id = url.split("/").pop();
            data.memories = data.memories.filter((m) => m.id !== id);
            return respond({ status: "success" });
        }
        // Cron
        if (url === "/api/cron/jobs" && method === "get") return respond(data.jobs);
        if (url === "/api/cron/jobs" && method === "post") {
            const body = JSON.parse(String(config.data || "{}"));
            if (String(body.cron_expr || "").includes("bad")) return fail(config, 422, "invalid cron expression: expected 5 fields");
            data.jobs = [...data.jobs, { id: Math.random().toString(16).slice(2, 8), trigger: now() + 300, payload: body.message, active: true, cron_expr: body.cron_expr, tz_offset: body.tz_offset, context: body.context, created_at: now() }];
            return respond({ status: "success" });
        }
        if (url.startsWith("/api/cron/jobs/") && method === "delete") {
            const id = url.split("/").pop();
            data.jobs = data.jobs.filter((job) => job.id !== id);
            return respond({ status: "success" });
        }
        if (url.startsWith("/api/cron/jobs/") && method === "patch") {
            const id = url.split("/").pop();
            const body = JSON.parse(String(config.data || "{}"));
            data.jobs = data.jobs.map((job) => (job.id === id ? { ...job, active: body.active } : job));
            return respond({ status: "success" });
        }
        // Browser sessions
        if (url === "/api/browser/sessions" && method === "get") return respond({ sessions: data.sessions });
        if (url.startsWith("/api/browser/sessions/") && method === "delete") {
            const id = decodeURIComponent(url.split("/").pop() || "");
            data.sessions = data.sessions.filter((s) => s.session_id !== id);
            return respond({ status: "success" });
        }
        // Channels
        if (url === "/api/config" && method === "get") return respond({ env, secrets });
        if (url === "/api/config" && method === "post") return respond({ status: "success" });
        if (url === "/api/discord/config" && method === "get")
            return respond({
                discord: {
                    signature: "LimeBot",
                    emoji_set: ["🍋", "✨"],
                    style_overrides: { default: { tone: "friendly", verbosity: "medium", emoji_usage: "light" }, guilds: { "998877665544": { tone: "formal", verbosity: "short", emoji_usage: "none", signature: "Ops" } }, channels: {} },
                    embed_theme: { default: "#57F287", guilds: {} },
                    nickname_templates: { default: "", guilds: {} },
                },
            });
        if (url === "/api/discord/config" && method === "post") return respond({ status: "success" });
        if (url === "/api/persona" && method === "get") return respond({ name: "Jennie", telegram_style: "" });
        if (url === "/api/persona" && method === "put") return respond({ status: "success" });
        if (url === "/api/telegram/status") return respond({ enabled: true, status: "ok", connected: true, username: "limebot_bot", bot_id: 7001234567, display_name: "LimeBot" });
        if (url === "/api/whatsapp/status") return respond(scenario === "empty" ? { status: "disconnected" } : { status: "scanning", qr: "2@limebot-gallery-fixture-not-a-real-pairing-code" });
        if (url === "/api/whatsapp/reset") return respond({ status: "success", message: "Session reset. A new QR code will appear in a few seconds." });
        if (url === "/api/whatsapp/contacts" && method === "get") return respond(data.contacts);
        if (url.startsWith("/api/whatsapp/contacts/") && method === "post") {
            const action = url.split("/").pop();
            const { chat_id } = JSON.parse(String(config.data || "{}"));
            const c = data.contacts;
            const strip = (list: string[]) => list.filter((id) => id !== chat_id);
            c.pending = strip(c.pending);
            c.allowed = strip(c.allowed);
            c.blocked = strip(c.blocked);
            if (action === "approve") c.allowed = [...c.allowed, chat_id];
            else if (action === "deny") c.blocked = [...c.blocked, chat_id];
            else if (action === "unallow") c.pending = [...c.pending, chat_id];
            return respond({ status: "success", contacts: c });
        }
        return respond({});
    };

    axios.defaults.adapter = adapter;
    api.defaults.adapter = adapter;
}
