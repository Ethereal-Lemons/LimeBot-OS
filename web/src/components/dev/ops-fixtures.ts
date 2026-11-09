import axios, { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from "axios";

/**
 * Fake backend for the operations pages, used only by `?ui=ops`. It replaces
 * the axios adapter, so the real pages run unchanged against canned data.
 *
 * state: data | empty | loading | error | error401 | error500
 */

export type OpsState = "data" | "empty" | "loading" | "error" | "error401" | "error500";

const now = () => Date.now() / 1000;
const ago = (seconds: number) => now() - seconds;

function stats(empty: boolean) {
    return {
        uptime: 93784,
        gateway_url: "ws://localhost:8000/ws/client",
        channels: empty
            ? []
            : [
                  { name: "web", type: "WebSocket dashboard", status: "Connected" },
                  { name: "discord", type: "Bot account", status: "Connected" },
                  { name: "whatsapp", type: "whatsapp-web.js bridge", status: "Disconnected" },
                  { name: "telegram", type: "Bot account", status: "Error" },
              ],
        sessions: empty ? 0 : 7,
        sessions_count: empty ? 0 : 7,
        instances_count: empty ? 0 : 9,
        subagents_count: empty ? 0 : 2,
        cron_status: "Running",
    };
}

function tasks(empty: boolean) {
    if (empty) return { tasks: [] };
    return {
        tasks: [
            { task_id: "a1b2c3d4-0001", type: "message", status: "running", summary: "Refactor the retry loop in core/llm_call_service.py and run the unit tests", channel: "web", created_at: ago(42), updated_at: ago(3), metadata: { profile_name: "Jennie" } },
            { task_id: "a1b2c3d4-0002", type: "browser", status: "waiting", summary: "Open the Python release page and read the active releases table", channel: "discord", created_at: ago(95), updated_at: ago(60), metadata: { profile_id: "research" } },
            { task_id: "a1b2c3d4-0003", type: "job", status: "queued", summary: "Morning greeting", channel: "cron", created_at: ago(4), updated_at: ago(4), metadata: {} },
            { task_id: "a1b2c3d4-0004", type: "subagent", status: "retrying", summary: "Review the diff for the changeset and report anything risky", channel: "web", created_at: ago(310), updated_at: ago(20), metadata: { profile_name: "Reviewer" } },
            { task_id: "a1b2c3d4-0005", type: "message", status: "completed", summary: "Summarise yesterday's journal into long-term memory", channel: "web", created_at: ago(3600), completed_at: ago(3552), updated_at: ago(3552), metadata: { profile_name: "Jennie" } },
            { task_id: "a1b2c3d4-0006", type: "message", status: "failed", summary: "Generate a cover image for the release notes", channel: "discord", created_at: ago(7300), completed_at: ago(7290), updated_at: ago(7290), error: "ElevenLabs TTS error (402): subscription_required", metadata: { profile_name: "Jennie" } },
            { task_id: "a1b2c3d4-0007", type: "job", status: "failed", summary: "Weekly dependency audit", channel: "cron", created_at: ago(90000), completed_at: ago(89940), updated_at: ago(89940), error: "command failed with exit code 1\nTraceback (most recent call last):\n  File \"audit.py\", line 3, in <module>\n    import pip_audit\nModuleNotFoundError: No module named 'pip_audit'", metadata: {} },
            { task_id: "a1b2c3d4-0008", type: "message", status: "cancelled", summary: "Plan the trip to Antigua", channel: "web", created_at: ago(172800), completed_at: ago(172790), updated_at: ago(172790), metadata: { profile_name: "Jennie" } },
        ],
    };
}

function deliveries(empty: boolean) {
    if (empty) return { deliveries: [] };
    return {
        deliveries: [
            { delivery_id: "d-0001-aaaa", channel: "discord", chat_id: "1203", target: "#general", message_kind: "text", status: "sending", attempts: 1, preview: "Done. The tests pass and I verified the two files I changed.", created_at: ago(6) },
            { delivery_id: "d-0002-bbbb", channel: "whatsapp", chat_id: "5021", target: "+502 5555 0102", message_kind: "voice", status: "retrying", attempts: 3, preview: "voice note, 14s", created_at: ago(120), last_error: "Connection refused: bridge not reachable on ws://localhost:3001" },
            { delivery_id: "d-0003-cccc", channel: "web", chat_id: "dash", target: "dashboard", message_kind: "text", status: "sent", attempts: 1, preview: "Good morning. Two things are waiting for your approval.", created_at: ago(5000), sent_at: ago(4999) },
            { delivery_id: "d-0004-dddd", channel: "discord", chat_id: "1203", target: "#general", message_kind: "embed", status: "failed", attempts: 4, preview: "Release notes for 0.9.2", created_at: ago(9000), last_error: "Missing Permissions (403): the bot cannot embed links in this channel" },
        ],
    };
}

const LOG_LINES = [
    "2026-11-08 09:12:01.104 | INFO     | core.loop:start:212 - AgentLoop started (model=gemini/gemini-2.5-flash)",
    "2026-11-08 09:12:01.388 | INFO     | channels.web:startup:88 - Web channel listening on 0.0.0.0:8000",
    "2026-11-08 09:12:02.001 | DEBUG    | core.vectors:warm:41 - Embedding provider ready (gemini-embedding-001)",
    "2026-11-08 09:12:09.554 | INFO     | channels.discord:on_ready:130 - Logged in as Jennie#0042",
    "2026-11-08 09:12:15.870 | WARNING  | channels.whatsapp:connect:77 - Bridge not reachable on ws://localhost:3001, retrying in 5s",
    "2026-11-08 09:12:21.012 | INFO     | core.loop:_process_message:540 - Turn started (session=web_dash, tools=22)",
    "2026-11-08 09:12:22.331 | DEBUG    | core.tool_runtime:execute:118 - read_file path=core/loop.py cached=False",
    "2026-11-08 09:12:23.907 | ERROR    | core.tools:run_command:931 - Command failed: python -m unittest (exit code 1)",
    "Traceback (most recent call last):",
    "  File \"/app/core/tools.py\", line 928, in run_command",
    "    proc = await self._start(command)",
    "  File \"/app/core/command_execution_service.py\", line 204, in _start",
    "    raise RuntimeError(\"command failed with exit code 1\")",
    "RuntimeError: command failed with exit code 1",
    "2026-11-08 09:12:24.002 | INFO     | core.recovery_controller:classify:77 - Recovery queued (category=command_failed)",
    "2026-11-08 09:12:31.640 | INFO     | core.loop:_process_message:812 - Turn finished in 10.6s (iterations=4)",
    "2026-11-08 09:12:35.220 | WARNING  | core.llm_call_service:retry:156 - Provider 429, backing off 2.0s",
    "INFO:     127.0.0.1:51512 - \"GET /api/stats HTTP/1.1\" 200 OK",
    "2026-11-08 09:12:40.118 | CRITICAL | core.job_queue:heartbeat:64 - Lease lost for job j-93 (heartbeat missed twice)",
    "2026-11-08 09:12:44.870 | INFO     | core.scheduler:tick:98 - Next job in 3h 12m",
];

function instances(empty: boolean) {
    if (empty) return [];
    const base = (over: Record<string, unknown>) => ({
        created_at: ago(8000),
        last_active: ago(90),
        origin: "web",
        model: "gemini/gemini-2.5-flash",
        total_tokens: { input: 18200, output: 5400, total: 23600 },
        injected_files: [],
        history_file: "persona/sessions/web_dash.json",
        ...over,
    });
    return [
        base({ id: "web_dash", last_active: ago(8), profile_name: "Jennie", skills: ["browser", "filesystem", "docx-creator"], injected_files: ["persona/SOUL.md", "persona/IDENTITY.md", "persona/users/luis.md"], history_file: "persona/sessions/web_dash.json", task: "Refactor the retry loop in core/llm_call_service.py and run the unit tests" }),
        base({ id: "web_dash_sub_1", parent_id: "web_dash", origin: "subagent", profile_name: "Reviewer", last_active: ago(40), total_tokens: { input: 4100, output: 900, total: 5000 }, task: "Review the diff for the changeset", history_file: "persona/sessions/web_dash_sub_1.json", skills: ["reviewer"] }),
        base({ id: "web_dash_sub_2", parent_id: "web_dash", origin: "subagent", profile_name: "Explorer", last_active: ago(700), total_tokens: { input: 2100, output: 300, total: 2400 }, history_file: "persona/sessions/web_dash_sub_2.json" }),
        base({ id: "discord_1203_88", origin: "discord", last_active: ago(1900), model: "openai/gpt-4o-mini", profile_id: "research", total_tokens: { input: 912000, output: 61000, total: 973000 }, history_file: "persona/sessions/discord_1203_88.json", skills: ["scrapling"] }),
        base({ id: "cron_morning_greeting", origin: "cron", last_active: ago(86400 * 2), total_tokens: { input: 800, output: 120, total: 920 }, history_file: "persona/sessions/cron_morning_greeting.json" }),
        base({ id: "orphan_sub_9", parent_id: "gone_session", origin: "subagent", last_active: ago(86400 * 4), total_tokens: { input: 0, output: 0, total: 0 }, history_file: "persona/sessions/orphan_sub_9.json" }),
    ];
}

function respond(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
    return { data, status, statusText: "OK", headers: {}, config };
}

function failure(config: InternalAxiosRequestConfig, status: number | null, detail = ""): AxiosError {
    if (status === null) return new AxiosError("Network Error", "ERR_NETWORK", config);
    const response: AxiosResponse = { data: { detail }, status, statusText: "", headers: {}, config };
    return new AxiosError(`Request failed with status code ${status}`, "ERR_BAD_RESPONSE", config, null, response);
}

export function installOpsFixtures(state: OpsState) {
    const empty = state === "empty";
    axios.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
        const url = String(config.url || "");
        const method = (config.method || "get").toLowerCase();

        if (state === "loading") return new Promise<AxiosResponse>(() => undefined);
        if (state === "error") throw failure(config, null);
        if (state === "error401") throw failure(config, 401, "Invalid or missing API key");
        if (state === "error500") throw failure(config, 500, "Internal server error: job queue unavailable");

        if (method !== "get") return respond(config, { ok: true, message: "Done." });
        if (url.includes("/api/stats")) return respond(config, stats(empty));
        if (url.includes("/api/llm/health")) return respond(config, { status: "Healthy", latency_ms: 842, model: "gemini/gemini-2.5-flash" });
        if (url.includes("/api/identity")) return respond(config, { name: "Jennie", avatar: null });
        if (url.includes("/api/config")) return respond(config, { env: { LLM_MODEL: "gemini/gemini-2.5-flash" }, secrets: { APP_API_KEY: { masked: "••••a1f3" } } });
        if (url.includes("/api/memory")) return respond(config, { enabled: true, mode: "vector" });
        if (url.includes("/api/subagents")) return respond(config, { default_selection: "auto", selection_options: [{ value: "auto", label: "Auto" }] });
        if (url.includes("/api/setup/status")) return respond(config, empty ? { persona_ready: false, persona_missing: ["SOUL.md", "IDENTITY.md"] } : { persona_ready: true });
        if (url.includes("/api/persona")) {
            return respond(config, empty
                ? {}
                : { mood: "Curious and a little caffeinated", enable_dynamic_personality: true, relationships: [{ name: "Luis", affinity: 82, level: "Close friend" }], web_style: "warm", discord_style: "playful" });
        }
        if (url.includes("/api/tasks")) return respond(config, tasks(empty));
        if (url.includes("/api/deliveries")) return respond(config, deliveries(empty));
        if (url.includes("/api/logs")) return respond(config, { logs: empty ? ["No logs found."] : LOG_LINES });
        if (url.includes("/api/instances")) return respond(config, instances(empty));
        throw failure(config, 404, `No fixture for ${url}`);
    };
}
