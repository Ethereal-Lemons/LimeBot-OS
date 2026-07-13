import test from "node:test";
import assert from "node:assert/strict";

import { compactMarkdownSpacing } from "../src/lib/markdown-spacing.js";

test("compacts blank-line-separated ordered and unordered list items", () => {
    assert.equal(
        compactMarkdownSpacing("1. First\n\n2. Second\n\n3. Third"),
        "1. First\n2. Second\n3. Third",
    );
    assert.equal(compactMarkdownSpacing("- One\n\n- Two"), "- One\n- Two");
});

test("preserves meaningful paragraph breaks", () => {
    assert.equal(
        compactMarkdownSpacing("First paragraph.\n\nSecond paragraph."),
        "First paragraph.\n\nSecond paragraph.",
    );
});
