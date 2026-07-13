import assert from "node:assert/strict";
import test from "node:test";

import { classifyStreamDelta, shouldRenderRichMarkdown } from "../src/lib/stream-rendering.js";

test("streaming content always uses the literal plain-text path", () => {
    assert.equal(shouldRenderRichMarkdown(true), false);
    const partial = "```ts\nconst link = '[literal](https://example.com)'\n\n";
    assert.equal(partial, "```ts\nconst link = '[literal](https://example.com)'\n\n");
});

test("completed content switches once to the rich renderer", () => {
    assert.equal(shouldRenderRichMarkdown(false), true);
    assert.equal(shouldRenderRichMarkdown(undefined), true);
});

test("first thinking and answer chunks render immediately, then batch", () => {
    const empty = { key: null, contentRendered: false, thinkingRendered: false };
    const firstThinking = classifyStreamDelta(empty, "turn-1", "", "Thinking");
    assert.equal(firstThinking.immediate, true);
    const moreThinking = classifyStreamDelta(firstThinking.next, "turn-1", "", " more");
    assert.equal(moreThinking.immediate, false);
    const firstAnswer = classifyStreamDelta(moreThinking.next, "turn-1", "Hello", "");
    assert.equal(firstAnswer.immediate, true);
    const moreAnswer = classifyStreamDelta(firstAnswer.next, "turn-1", " world", "");
    assert.equal(moreAnswer.immediate, false);
    assert.equal(classifyStreamDelta(moreAnswer.next, "turn-2", "New", "").immediate, true);
});
