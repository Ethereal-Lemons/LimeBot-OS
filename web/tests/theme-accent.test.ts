import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

function sourceFiles(directory: string): string[] {
    return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
        const target = path.join(directory, entry.name);
        if (entry.isDirectory()) return sourceFiles(target);
        return entry.name.endsWith(".tsx") ? [target] : [];
    });
}

test("application status accents use theme tokens instead of fixed AI green or cyan", () => {
    const componentRoot = path.resolve("src/components");
    const fixedAccent = /\b(?:text|bg|border)-(?:emerald|green|cyan|teal|lime)-\d+/;
    const offenders = sourceFiles(componentRoot)
        .filter((file) => !file.endsWith("AppearancePage.tsx"))
        .filter((file) => fixedAccent.test(fs.readFileSync(file, "utf8")))
        .map((file) => path.relative(componentRoot, file));
    assert.deepEqual(offenders, []);
});

test("every named theme declares its own primary accent", () => {
    const css = fs.readFileSync(path.resolve("src/index.css"), "utf8");
    const themeBlocks = new Map<string, string[]>();
    for (const match of css.matchAll(/\[data-theme='([^']+)'\]\s*\{([^}]*)\}/g)) {
        const blocks = themeBlocks.get(match[1]) || [];
        blocks.push(match[2]);
        themeBlocks.set(match[1], blocks);
    }
    assert.ok(themeBlocks.size >= 20, "expected the predefined theme registry");
    for (const [theme, blocks] of themeBlocks) {
        assert.ok(blocks.some((block) => block.includes("--primary:")), `${theme} must define --primary`);
    }
});
