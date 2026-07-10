import assert from "node:assert/strict";
import test from "node:test";

import { shouldRenderRichMarkdown } from "../src/lib/stream-rendering.js";

test("streaming content always uses the literal plain-text path", () => {
    assert.equal(shouldRenderRichMarkdown(true), false);
    const partial = "```ts\nconst link = '[literal](https://example.com)'\n\n";
    assert.equal(partial, "```ts\nconst link = '[literal](https://example.com)'\n\n");
});

test("completed content switches once to the rich renderer", () => {
    assert.equal(shouldRenderRichMarkdown(false), true);
    assert.equal(shouldRenderRichMarkdown(undefined), true);
});
