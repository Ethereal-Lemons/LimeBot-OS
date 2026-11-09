import assert from "node:assert/strict";
import test from "node:test";

import {
    classifyToolError,
    describeTool,
    formatArgs,
    isErrorResult,
    splitDependencyNotice,
    stripErrorPrefix,
} from "../src/lib/tool-display.js";

test("describeTool gives a short label and a readable target", () => {
    assert.deepEqual(describeTool("read_file", { path: "D:\\Code\\app\\main.py" }), {
        label: "Read file",
        target: "main.py",
        icon: "file",
    });
    const command = describeTool("run_command", { command: "python -m unittest   test_x" });
    assert.equal(command.label, "Run command");
    assert.equal(command.target, "python -m unittest test_x");
    assert.equal(describeTool("verify_files", { paths: ["a/b.py", "c.py", "d.py"] }).target, "b.py +2");
    assert.equal(describeTool("mcp_genko_get_items", {}).label, "Get items");
});

test("describeTool clips very long targets", () => {
    const target = describeTool("web_search", { query: "x".repeat(300) }).target;
    assert.ok(target.length <= 80);
    assert.ok(target.endsWith("…"));
});

test("classifyToolError recognises the failures LimeBot produces", () => {
    const cases: Array<[string, string]> = [
        ["Error: Tool 'run_command' is disabled in Discord voice conversations.", "policy"],
        ["Error: only python, pytest and read-only git commands are allowed here", "policy"],
        ["Error: ElevenLabs STT error (401): missing the permission speech_to_text", "authentication"],
        ["Error: ElevenLabs TTS error (402): subscription_required", "rate_limit"],
        ["Error: command timed out after 180s", "timeout"],
        ["Error: web search returned no usable results (no organic results).", "empty"],
        ["Error: No such file or directory: workspace/x.py", "not_found"],
        ["Error: missing required argument 'path'", "invalid_arguments"],
        ["Error: Skill 'vm-lab' disabled because dependencies are missing: qemu", "dependency"],
        ["Error: connection refused", "network"],
        ["Error: command failed with exit code 1", "command_failed"],
        ["Error: approval denied by user", "denied"],
        ["Error: something odd", "unknown"],
    ];
    for (const [message, expected] of cases) {
        assert.equal(classifyToolError(message).category, expected, message);
    }
});

test("classifyToolError strips the Error prefix and marks retryable causes", () => {
    const info = classifyToolError("Error: rate limit reached");
    assert.equal(info.detail, "rate limit reached");
    assert.equal(info.retryable, true);
    assert.equal(classifyToolError("Error: outside allowed paths").retryable, false);
});

test("isErrorResult only flags results that start with an error marker", () => {
    assert.equal(isErrorResult("Error: nope"), true);
    assert.equal(isErrorResult("Failed - nope"), true);
    assert.equal(isErrorResult("42 lines, no errors found"), false);
    assert.equal(isErrorResult(undefined), false);
    assert.equal(stripErrorPrefix("Error: x"), "x");
});

test("splitDependencyNotice separates the marker from the result", () => {
    assert.deepEqual(splitDependencyNotice("ran\n[SKILL_DEPS_MISSING] pip install x"), {
        cleaned: "ran",
        notice: "pip install x",
    });
    assert.deepEqual(splitDependencyNotice("plain"), { notice: "", cleaned: "plain" });
});

test("formatArgs clips long strings instead of dumping them", () => {
    const text = formatArgs({ content: "y".repeat(1000), path: "a.txt" }, 50);
    assert.ok(text.includes("a.txt"));
    assert.ok(text.includes("1000 chars"));
    assert.ok(text.length < 300);
});
