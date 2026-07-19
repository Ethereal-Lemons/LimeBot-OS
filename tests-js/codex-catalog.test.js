import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveCodexModel } from '../scripts/codex-chat.mjs';
import {
    getCodexAuthStatus,
    resolveCodexApiKey,
} from '../scripts/codex-oauth.mjs';

test('Codex catalog exposes the GPT-5.6 named variants', () => {
    for (const [id, name] of [
        ['gpt-5.6-sol', 'GPT-5.6 Sol'],
        ['gpt-5.6-luna', 'GPT-5.6 Luna'],
        ['gpt-5.6-terra', 'GPT-5.6 Terra'],
    ]) {
        assert.equal(resolveCodexModel(id)?.name, name);
    }
});

test('Codex OAuth helper remains importable with the current pi-ai API', () => {
    assert.equal(typeof getCodexAuthStatus, 'function');
    assert.equal(typeof resolveCodexApiKey, 'function');
});
