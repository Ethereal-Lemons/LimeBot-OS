import assert from "node:assert/strict";
import test from "node:test";

import { classifyMarkdownCode } from "../src/lib/markdown-code.js";

test("classifies inline code without changing its whitespace", () => {
    assert.deepEqual(classifyMarkdownCode(undefined, " hello "), {
        kind: "inline",
        language: "text",
        value: " hello ",
    });
});

test("classifies a fenced language and removes only its parser newline", () => {
    assert.deepEqual(classifyMarkdownCode("language-typescript", "  const x = 1;\n"), {
        kind: "block",
        language: "typescript",
        value: "  const x = 1;",
    });
});

test("classifies a fenced block without a language", () => {
    assert.deepEqual(classifyMarkdownCode(undefined, "plain code\n"), {
        kind: "block",
        language: "text",
        value: "plain code",
    });
});

test("keeps an empty code node safe and synchronous", () => {
    assert.deepEqual(classifyMarkdownCode(undefined, ""), {
        kind: "inline",
        language: "text",
        value: "",
    });
});
