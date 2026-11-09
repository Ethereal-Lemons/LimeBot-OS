// Mock handlers for the agents pages (profiles, skills, subagents).
// Register with mock(method, pattern, handler). State lives in memory and
// resets on reload. `&empty=1` hides skills and subagents and keeps only `default`.
import { mock, mockFlags, MockHttpError } from "../mock-api";
import type { AgentProfile } from "@/lib/agent-profiles";

const now = () => Math.floor(Date.now() / 1000);

// ── profiles ────────────────────────────────────────────────────────────────

const GLOBAL_MODEL = "gemini/gemini-2.5-flash";

const AVAILABLE_TOOLS = [
    "analyze_video",
    "browser_act",
    "browser_extract",
    "browser_navigate",
    "calculate",
    "cron_add",
    "cron_list",
    "edit_file",
    "list_dir",
    "memory_save",
    "memory_search",
    "read_file",
    "run_command",
    "send_media",
    "spawn_agent",
    "verify_files",
    "web_search",
    "write_file",
];

function makeProfile(partial: Partial<AgentProfile> & { profile_id: string; display_name: string }): AgentProfile {
    const model = partial.model ?? null;
    return {
        archived: false,
        policy: { allowed_tools: null, allowed_paths: null, approval_policy: null },
        created_at: now() - 86400 * 14,
        updated_at: now() - 3600,
        ...partial,
        model,
        resolved_model: model || GLOBAL_MODEL,
    };
}

const profiles: AgentProfile[] = [
    makeProfile({ profile_id: "default", display_name: "Jennie" }),
    makeProfile({
        profile_id: "research-buddy",
        display_name: "Research Buddy",
        model: "anthropic/claude-sonnet-4-5",
        policy: {
            allowed_tools: ["read_file", "list_dir", "web_search", "browser_navigate", "browser_extract", "memory_search"],
            allowed_paths: ["D:\\Projects\\notes"],
            approval_policy: "manual",
        },
    }),
    makeProfile({ profile_id: "night-owl", display_name: "Night Owl", archived: true }),
];

const SOUL_TEXT =
    "# Soul\n\nCurious, patient, and a little dry. Prefers short answers first and offers to go deeper. Never pretends to know something it does not, and says so plainly when a task is outside its tools.";
const IDENTITY_TEXT = "# Identity\n\n**Name:** Research Buddy\n**Style:** Calm, precise, cites where information came from.\n**Emoji:** 🔎";

const personas: Record<string, { soul: string; identity: string; mood: string }> = {
    default: {
        soul: SOUL_TEXT.replace("# Soul", "# Soul (Jennie)"),
        identity: "# Identity\n\n**Name:** Jennie\n**Style:** Warm, playful, concise.",
        mood: "Focused and upbeat.",
    },
    "research-buddy": { soul: SOUL_TEXT, identity: IDENTITY_TEXT, mood: "" },
    "night-owl": { soul: "", identity: "", mood: "" },
};

const slugify = (text: string) =>
    text
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");

const visibleProfiles = () => (mockFlags.empty ? profiles.filter((profile) => profile.profile_id === "default") : profiles);

const findProfile = (id: string) => {
    const profile = visibleProfiles().find((item) => item.profile_id === id);
    if (!profile) throw new MockHttpError(404, { detail: `unknown profile: ${id}` });
    return profile;
};

mock("GET", /^\/api\/profiles$/, ({ params }) => {
    const includeArchived = String(params.include_archived) === "true";
    return {
        profiles: visibleProfiles().filter((profile) => includeArchived || !profile.archived),
        default_model: GLOBAL_MODEL,
        available_tools: AVAILABLE_TOOLS,
    };
});

mock("POST", /^\/api\/profiles$/, ({ data }) => {
    const body = (data || {}) as { display_name?: unknown };
    if (typeof body.display_name !== "string") throw new MockHttpError(422, { detail: "display_name is required" });
    const name = body.display_name.trim();
    if (!name) throw new MockHttpError(422, { detail: "display name cannot be empty" });
    const id = slugify(name) || "agent";
    if (profiles.some((profile) => profile.profile_id === id)) {
        throw new MockHttpError(409, { detail: `profile already exists: ${id}` });
    }
    const profile = makeProfile({ profile_id: id, display_name: name, created_at: now(), updated_at: now() });
    profiles.push(profile);
    personas[id] = { soul: "", identity: "", mood: "" };
    return { profile };
});

mock("PATCH", /^\/api\/profiles\/([^/]+)$/, ({ match, data }) => {
    const profile = findProfile(decodeURIComponent(match[1]));
    const body = (data || {}) as Partial<AgentProfile> & { policy?: AgentProfile["policy"] };
    if (typeof body.display_name === "string") {
        if (!body.display_name.trim()) throw new MockHttpError(422, { detail: "display name cannot be empty" });
        profile.display_name = body.display_name.trim();
    }
    if (body.model !== undefined) {
        profile.model = body.model;
        profile.resolved_model = body.model || GLOBAL_MODEL;
    }
    if (body.policy) {
        profile.policy = body.policy;
    }
    profile.updated_at = now();
    return { profile };
});

mock("POST", /^\/api\/profiles\/([^/]+)\/archive$/, ({ match }) => {
    const id = decodeURIComponent(match[1]);
    if (id === "default") throw new MockHttpError(422, { detail: "the default profile cannot be archived" });
    const profile = findProfile(id);
    profile.archived = true;
    return { profile };
});

mock("POST", /^\/api\/profiles\/([^/]+)\/unarchive$/, ({ match }) => {
    const profile = findProfile(decodeURIComponent(match[1]));
    profile.archived = false;
    return { profile };
});

mock("GET", /^\/api\/profiles\/([^/]+)\/persona$/, ({ match }) => {
    const profile = findProfile(decodeURIComponent(match[1]));
    return { profile_id: profile.profile_id, ...(personas[profile.profile_id] || { soul: "", identity: "", mood: "" }) };
});

mock("PUT", /^\/api\/profiles\/([^/]+)\/persona\/(soul|identity|mood)$/, ({ match, data }) => {
    const profile = findProfile(decodeURIComponent(match[1]));
    const section = match[2] as "soul" | "identity" | "mood";
    const content = String((data as { content?: unknown })?.content ?? "");
    if (content.length > 20000) throw new MockHttpError(413, { detail: "persona content exceeds 20000 characters" });
    const trimmed = content.trim();
    const valid =
        section === "mood" ||
        (section === "soul" && trimmed.length >= 100) ||
        (section === "identity" && trimmed.length >= 50 && /\*\*Name:\*\*/.test(trimmed) && /\*\*Style:\*\*/.test(trimmed));
    if (!valid) throw new MockHttpError(422, { detail: `${section} content failed validation` });
    personas[profile.profile_id] = { ...(personas[profile.profile_id] || { soul: "", identity: "", mood: "" }), [section]: content };
    return { status: "success", profile_id: profile.profile_id, section };
});

// ── skills ──────────────────────────────────────────────────────────────────

interface MockSkill {
    id: string;
    name: string;
    description: string;
    path: string;
    enabled: boolean;
    active: boolean;
    source_kind: string;
    editable: boolean;
    deps_ok: boolean;
    missing_deps: { python?: string[]; node?: string[]; binaries?: string[] };
    required_deps: { python?: string[]; node?: string[]; binaries?: string[] };
}

const skills: MockSkill[] = [
    {
        id: "browser",
        name: "browser",
        description: "Drive a real browser: open pages, click, type, scroll and read what is on screen. Used when a page needs interaction rather than a plain search.",
        path: "skills/browser",
        enabled: true,
        active: true,
        source_kind: "bundled",
        editable: false,
        deps_ok: true,
        missing_deps: {},
        required_deps: { python: ["playwright"] },
    },
    {
        id: "filesystem",
        name: "filesystem",
        description: "Read, write and search files inside the allowed folders.",
        path: "skills/filesystem",
        enabled: true,
        active: true,
        source_kind: "bundled",
        editable: false,
        deps_ok: true,
        missing_deps: {},
        required_deps: {},
    },
    {
        id: "discord",
        name: "discord",
        description: "Send messages, embeds and DMs, and list the channels the bot can see.",
        path: "skills/discord",
        enabled: true,
        active: true,
        source_kind: "bundled",
        editable: false,
        deps_ok: true,
        missing_deps: {},
        required_deps: { python: ["discord.py"] },
    },
    {
        id: "docx-creator",
        name: "docx-creator",
        description: "Create formatted Word documents from an outline, with headings, tables and images.",
        path: "skills/docx-creator",
        enabled: false,
        active: false,
        source_kind: "git-managed",
        editable: true,
        deps_ok: false,
        missing_deps: { python: ["python-docx"] },
        required_deps: { python: ["python-docx", "lxml"] },
    },
    {
        id: "scrapling",
        name: "scrapling",
        description: "Fetch pages that block ordinary scrapers, then extract structured data from them.",
        path: "skills/scrapling",
        enabled: false,
        active: false,
        source_kind: "git-managed",
        editable: true,
        deps_ok: false,
        missing_deps: { python: ["scrapling"], node: ["playwright"] },
        required_deps: { python: ["scrapling"], node: ["playwright"] },
    },
    {
        id: "vm-lab",
        name: "vm-lab",
        description: "Create and manage disposable QEMU/KVM virtual machines for testing.",
        path: "skills/vm-lab",
        enabled: true,
        active: false,
        source_kind: "local",
        editable: true,
        deps_ok: false,
        missing_deps: { binaries: ["qemu-system-x86_64", "qemu-img"] },
        required_deps: { binaries: ["qemu-system-x86_64", "qemu-img"] },
    },
    {
        id: "weather",
        name: "weather",
        description: "Look up the current weather and a short forecast for a city.",
        path: "skills/weather",
        enabled: false,
        active: false,
        source_kind: "legacy-local",
        editable: true,
        deps_ok: true,
        missing_deps: {},
        required_deps: {},
    },
];

const visibleSkills = () => (mockFlags.empty ? [] : skills);

const findSkill = (id: string) => {
    const skill = skills.find((item) => item.id === id);
    if (!skill) throw new MockHttpError(404, { detail: `Skill not found: ${id}` });
    return skill;
};

mock("GET", /^\/api\/skills$/, () => ({ skills: visibleSkills() }));

mock("POST", /^\/api\/skills\/([^/]+)\/toggle$/, ({ match, data }) => {
    const skill = findSkill(decodeURIComponent(match[1]));
    const enable = Boolean((data as { enable?: unknown })?.enable);
    if (enable && !skill.deps_ok) {
        return { status: "error", message: `Cannot enable ${skill.name}: dependencies are missing` };
    }
    skill.enabled = enable;
    skill.active = enable && skill.deps_ok;
    return { status: "success", message: `${skill.name} ${enable ? "enabled" : "disabled"}` };
});

mock("POST", /^\/api\/skills\/([^/]+)\/deps$/, ({ match }) => {
    const skill = findSkill(decodeURIComponent(match[1]));
    if (skill.id === "scrapling") {
        return { status: "error", message: "npm install playwright failed: EACCES permission denied, mkdir '/usr/lib/node_modules'" };
    }
    if (skill.id === "vm-lab") {
        // System binaries cannot be installed by the app, so this stays incomplete.
        return { status: "partial", message: "qemu-img is still missing" };
    }
    skill.deps_ok = true;
    skill.missing_deps = {};
    skill.active = skill.enabled;
    return { status: "success", message: "Dependencies installed" };
});

// ── subagents ───────────────────────────────────────────────────────────────

interface MockSubagent {
    id: string;
    name: string;
    description: string;
    prompt: string;
    tools: string[] | null;
    disallowed_tools: string[];
    model: string;
    max_turns: number | null;
    background: boolean;
    filename: string;
    location: string;
    location_label: string;
    path: string;
    active: boolean;
    shadowed_by: string | null;
    builtin: boolean;
}

const LOCATIONS = [
    { value: "project_limebot", label: "Project (.limebot/agents)", path: ".limebot/agents" },
    { value: "project_claude", label: "Project (.claude/agents)", path: ".claude/agents" },
    { value: "user_limebot", label: "Personal (~/.limebot/agents)", path: "~/.limebot/agents" },
    { value: "user_claude", label: "Personal (~/.claude/agents)", path: "~/.claude/agents" },
];

const BUILTIN_LABEL = "Built-in";

let subagents: MockSubagent[] = [
    {
        id: "builtin:reviewer",
        name: "reviewer",
        description: "Review a change for correctness bugs, regressions and missing tests. Read-only.",
        prompt: "You are a careful code reviewer. Read the diff, trace each changed path, and report concrete findings with file and line. Do not edit files.",
        tools: ["read_file", "list_dir", "run_command"],
        disallowed_tools: ["write_file", "edit_file", "delete_file"],
        model: "inherit",
        max_turns: 10,
        background: false,
        filename: "reviewer",
        location: "builtin",
        location_label: BUILTIN_LABEL,
        path: "(built-in)",
        active: true,
        shadowed_by: null,
        builtin: true,
    },
    {
        id: "builtin:verifier",
        name: "verifier",
        description: "Run the tests and checks that prove a change works and report what passed and failed.",
        prompt: "Run the project's verification commands, read the failures, and report the exact output that matters.",
        tools: null,
        disallowed_tools: [],
        model: "inherit",
        max_turns: 8,
        background: false,
        filename: "verifier",
        location: "builtin",
        location_label: BUILTIN_LABEL,
        path: "(built-in)",
        active: true,
        shadowed_by: null,
        builtin: true,
    },
    {
        id: "project_limebot:code-reviewer",
        name: "code-reviewer",
        description: "Review code for bugs, regressions, and missing tests. Use for auth and billing changes.",
        prompt: "Focus on concrete findings first. Keep the report concise and actionable.\n\nCheck input validation, error handling and test coverage before style.",
        tools: ["read_file", "list_dir", "web_search"],
        disallowed_tools: ["delete_file"],
        model: "inherit",
        max_turns: 8,
        background: false,
        filename: "code-reviewer",
        location: "project_limebot",
        location_label: "Project (.limebot/agents)",
        path: ".limebot/agents/code-reviewer.md",
        active: true,
        shadowed_by: null,
        builtin: false,
    },
    {
        id: "user_claude:researcher",
        name: "researcher",
        description: "Gather sources on a topic and return a short, cited summary.",
        prompt: "Search broadly, open the best sources, and return a summary with links. Say what you could not verify.",
        tools: null,
        disallowed_tools: [],
        model: "gemini/gemini-2.5-flash",
        max_turns: null,
        background: true,
        filename: "researcher",
        location: "user_claude",
        location_label: "Personal (~/.claude/agents)",
        path: "~/.claude/agents/researcher.md",
        active: true,
        shadowed_by: null,
        builtin: false,
    },
    {
        id: "project_claude:reviewer",
        name: "reviewer",
        description: "Project flavour of the reviewer that also checks the changelog.",
        prompt: "Review the change and confirm the changelog mentions it.",
        tools: ["read_file"],
        disallowed_tools: [],
        model: "inherit",
        max_turns: null,
        background: false,
        filename: "reviewer",
        location: "project_claude",
        location_label: "Project (.claude/agents)",
        path: ".claude/agents/reviewer.md",
        active: false,
        shadowed_by: "builtin:reviewer",
        builtin: false,
    },
];

const slugFilename = (name: string) => (name.trim().replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^[-_]+|[-_]+$/g, "") || "subagent").toLowerCase();

const subagentPayload = () => ({
    subagents: mockFlags.empty ? [] : subagents,
    location_options: LOCATIONS,
    default_selection: "auto",
    selection_options: [],
});

function saveSubagent(data: unknown, existingId?: string) {
    const body = (data || {}) as Record<string, unknown>;
    const name = String(body.name ?? "").trim();
    const description = String(body.description ?? "").trim();
    const prompt = String(body.prompt ?? "").trim();
    if (!name) throw new MockHttpError(400, { detail: "Subagent name is required" });
    if (!description) throw new MockHttpError(400, { detail: "Subagent description is required" });
    if (!prompt) throw new MockHttpError(400, { detail: "Subagent prompt is required" });

    const location = LOCATIONS.find((option) => option.value === body.location) || LOCATIONS[0];
    const filename = slugFilename(name);
    const id = `${location.value}:${filename}`;
    if (existingId && existingId !== id && subagents.some((item) => item.id === id)) {
        throw new MockHttpError(400, { detail: `Subagent file already exists: ${filename}.md` });
    }
    const maxTurns = Number.parseInt(String(body.max_turns ?? ""), 10);
    const next: MockSubagent = {
        id,
        name,
        description,
        prompt,
        tools: Array.isArray(body.tools) && body.tools.length ? (body.tools as string[]) : null,
        disallowed_tools: Array.isArray(body.disallowed_tools) ? (body.disallowed_tools as string[]) : [],
        model: String(body.model || "inherit"),
        max_turns: Number.isFinite(maxTurns) && maxTurns > 0 ? maxTurns : null,
        background: Boolean(body.background),
        filename,
        location: location.value,
        location_label: location.label,
        path: `${location.path}/${filename}.md`,
        active: true,
        shadowed_by: null,
        builtin: false,
    };
    subagents = subagents.filter((item) => item.id !== existingId && item.id !== id);
    subagents.push(next);
    return next;
}

mock("GET", /^\/api\/subagents$/, () => subagentPayload());

mock("POST", /^\/api\/subagents$/, ({ data }) => {
    const saved = saveSubagent(data);
    return { status: "success", subagent: saved, ...subagentPayload() };
});

mock("PUT", /^\/api\/subagents\/(.+)$/, ({ match, data }) => {
    const id = decodeURIComponent(match[1]);
    const existing = subagents.find((item) => item.id === id);
    if (!existing) throw new MockHttpError(400, { detail: "Invalid subagent id" });
    if (existing.builtin) throw new MockHttpError(400, { detail: "Built-in subagents cannot be edited directly" });
    const saved = saveSubagent(data, id);
    return { status: "success", subagent: saved, ...subagentPayload() };
});

mock("DELETE", /^\/api\/subagents\/(.+)$/, ({ match }) => {
    const id = decodeURIComponent(match[1]);
    const existing = subagents.find((item) => item.id === id);
    if (!existing) throw new MockHttpError(400, { detail: "Invalid subagent id" });
    if (existing.builtin) throw new MockHttpError(400, { detail: "Built-in subagents cannot be deleted" });
    subagents = subagents.filter((item) => item.id !== id);
    return { status: "success", ...subagentPayload() };
});
