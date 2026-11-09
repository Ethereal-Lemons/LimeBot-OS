import { useEffect, useState } from "react";
import { ToolCard, type ToolExecution } from "@/components/chat/ToolCard";
import { ToolTimeline } from "@/components/chat/ToolTimeline";
import { applyThemeWithSchedule } from "@/hooks/useTheme";

/**
 * Visual fixtures for the chat tool UI. Open the app with `?ui=gallery`.
 * It needs no backend, so every state can be reviewed and screenshotted.
 */

const base = (over: Partial<ToolExecution>): ToolExecution => ({
    tool: "read_file",
    status: "completed",
    args: {},
    tool_call_id: Math.random().toString(36).slice(2),
    ...over,
});

const ok = base({ tool: "read_file", args: { path: "D:\\Code\\LimeBot-OS\\core\\loop.py" }, result: "def main():\n    return 42\n" });
const running = base({
    tool: "run_command",
    status: "running",
    args: { command: "python -m unittest discover -s . -p test_*.py" },
    logs: ["Collecting tests", "Running test_pricing (3 cases)"],
});
const planned = base({ tool: "web_search", status: "planned", args: { query: "latest python release notes" } });
const waiting = base({
    tool: "edit_file",
    status: "waiting_confirmation",
    conf_id: "conf-1",
    args: { path: "workspace/settings.py" },
    preview: {
        kind: "edit_file",
        summary: "Edit workspace/settings.py (1 change)",
        risk_flags: ["modifies_file"],
        affected_paths: ["workspace/settings.py"],
        diff: "- REQUEST_TIMEOUT = 30\n+ REQUEST_TIMEOUT = 60",
    },
});
const waitingCommand = base({
    tool: "run_command",
    status: "waiting_confirmation",
    conf_id: "conf-2",
    args: { command: "rm -rf build && npm run build" },
    preview: { kind: "run_command", summary: "Run a shell command", risk_flags: ["deletes_files", "shell"], command: "rm -rf build && npm run build" },
});

const errors: ToolExecution[] = [
    base({ tool: "run_command", status: "error", args: { command: "python -c print(1)" }, result: "Error: Tool 'run_command' is disabled in Discord voice conversations. Ask for it in a text channel instead." }),
    base({ tool: "web_search", status: "error", args: { query: "site:forums.example.com warlock nerf" }, result: "Error: web search returned no usable results (no organic results). Answer from what you know and say the live lookup was empty." }),
    base({ tool: "send_voice", status: "error", args: { text: "hola" }, result: "Error: ElevenLabs TTS error (402): subscription_required" }),
    base({ tool: "run_command", status: "error", args: { command: "python manage.py test" }, result: "Error: command failed with exit code 1\nTraceback (most recent call last):\n  File \"manage.py\", line 3, in <module>\n    import django\nModuleNotFoundError: No module named 'django'" }),
    base({ tool: "read_file", status: "error", args: { path: "missing.txt" }, result: "Error: No such file or directory: missing.txt" }),
    base({ tool: "edit_file", status: "error", args: { path: "a.py" }, result: "Error: approval denied by user" }),
];

const longRun: ToolExecution[] = [
    base({ tool: "list_dir", args: { path: "workspace" }, result: "a.py\nb.py\ntest_a.py" }),
    base({ tool: "read_file", args: { path: "workspace/a.py" }, result: "x = 1\n" }),
    base({ tool: "edit_file", args: { path: "workspace/a.py", edits: [{ old_text: "x = 1", new_text: "x = 2" }] }, result: "Applied 1 edit." }),
    base({ tool: "verify_files", args: { paths: ["workspace/a.py"] }, result: '{"status": "passed"}' }),
    base({ tool: "run_command", status: "running", args: { command: "python -m unittest" }, logs: ["Ran 4 tests"] }),
];

const finishedRun: ToolExecution[] = [
    ...longRun.slice(0, 4),
    base({ tool: "run_command", args: { command: "python -m unittest" }, result: "Ran 4 tests\nOK" }),
];

const recoveredRun: ToolExecution[] = [
    base({ tool: "read_file", status: "error", args: { path: "wrong.py" }, result: "Error: No such file or directory: wrong.py" }),
    base({ tool: "list_dir", args: { path: "." }, result: "right.py" }),
    base({ tool: "read_file", args: { path: "right.py" }, result: "ok" }),
];

const bot = { name: "Jennie", avatar: null };

function Block({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
    return (
        <section className="space-y-2">
            <div>
                <h2 className="text-sm font-semibold text-foreground">{title}</h2>
                {note && <p className="text-xs text-muted-foreground">{note}</p>}
            </div>
            <div className="max-w-2xl space-y-1">{children}</div>
        </section>
    );
}

export function UiGallery() {
    const [theme, setTheme] = useState(() => new URLSearchParams(window.location.search).get("theme") || "");
    useEffect(() => {
        applyThemeWithSchedule(theme || "lime");
        document.documentElement.classList.add("dark");
    }, [theme]);
    const confirm = async () => undefined;

    return (
        <div className="min-h-screen bg-background p-6 text-foreground">
            <div className="mx-auto max-w-3xl space-y-8">
                <header className="flex items-center justify-between">
                    <div>
                        <h1 className="text-lg font-semibold">Tool UI gallery</h1>
                        <p className="text-xs text-muted-foreground">Every state of the chat tool components, without a backend.</p>
                    </div>
                    <input
                        value={theme}
                        onChange={(event) => setTheme(event.target.value)}
                        placeholder="theme (e.g. purple)"
                        className="h-8 rounded-md border border-border bg-card px-2 text-xs"
                    />
                </header>

                <Block title="Single calls" note="One row each; expands for details.">
                    <ToolCard execution={ok} />
                    <ToolCard execution={running} />
                    <ToolCard execution={planned} />
                </Block>

                <Block title="Needs approval" note="Opens itself and shows what will change.">
                    <ToolCard execution={waiting} onConfirmSideChannel={confirm} />
                    <ToolCard execution={waitingCommand} onConfirmSideChannel={confirm} />
                </Block>

                <Block title="Errors, in plain language" note="Each failure says what it means and what to do; raw details are one click away.">
                    {errors.map((execution) => (
                        <ToolCard key={execution.tool_call_id} execution={execution} />
                    ))}
                </Block>

                <Block title="Grouped activity" note="Three or more calls collapse into one summary.">
                    <ToolTimeline executions={longRun} botIdentity={bot} turnStatus="running" onConfirmSideChannel={confirm} />
                    <ToolTimeline executions={finishedRun} botIdentity={bot} turnStatus="completed" onConfirmSideChannel={confirm} />
                    <ToolTimeline executions={recoveredRun} botIdentity={bot} turnStatus="completed" onConfirmSideChannel={confirm} />
                    <ToolTimeline executions={[...errors.slice(0, 3)]} botIdentity={bot} turnStatus="failed" onConfirmSideChannel={confirm} />
                </Block>
            </div>
        </div>
    );
}
