import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { classifyGitChanges, parsePorcelainStatus, applyUpdate } from '../bin/updater.js';

test('updater classifies runtime-only changes separately from source changes', () => {
    const changes = parsePorcelainStatus([
        ' M limebot.json',
        '?? persona/memory/2026-07-16.md',
        ' M core/loop.py',
        ' M .env.example',
        '?? skills/my-local-skill/SKILL.md',
    ].join('\n'));
    const classification = classifyGitChanges(changes);
    assert.equal(classification.kind, 'code-dirty');
    assert.deepEqual(classification.stateChanges.map((item) => item.path), [
        'limebot.json',
        'persona/memory/2026-07-16.md',
        'skills/my-local-skill/SKILL.md',
    ]);
    assert.deepEqual(classification.codeChanges.map((item) => item.path), [
        'core/loop.py',
        '.env.example',
    ]);
});

test('updater refuses to overwrite tracked source changes', async () => {
    const runGit = async (args) => {
        if (args[0] === 'status') {
            return { code: 0, stdout: ' M core/loop.py', stderr: '' };
        }
        throw new Error(`unexpected git call: ${args.join(' ')}`);
    };
    const result = await applyUpdate({ runGit, rootDir: process.cwd() });
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'code-dirty');
});

test('updater backs up and restores legacy state around a fast-forward', async () => {
    const rootDir = fs.mkdtempSync(path.join(os.tmpdir(), 'limebot-updater-'));
    fs.writeFileSync(path.join(rootDir, 'limebot.json'), '{"skills":{"enabled":["local"]}}');
    fs.mkdirSync(path.join(rootDir, 'data'), { recursive: true });
    fs.writeFileSync(path.join(rootDir, 'data', 'memory.json'), '{}');

    const calls = [];
    const runGit = async (args) => {
        calls.push(args.join(' '));
        if (args[0] === 'status') return { code: 0, stdout: ' M limebot.json', stderr: '' };
        if (args[0] === 'rev-parse' && args[1] === '--is-inside-work-tree') return { code: 0, stdout: 'true', stderr: '' };
        if (args[0] === 'rev-parse' && args[1] === 'HEAD') return { code: 0, stdout: 'old-head', stderr: '' };
        if (args[0] === 'symbolic-ref') return { code: 0, stdout: 'main', stderr: '' };
        if (args[0] === 'config' && args[2]?.endsWith('.remote')) return { code: 0, stdout: 'origin', stderr: '' };
        if (args[0] === 'config') return { code: 0, stdout: 'refs/heads/main', stderr: '' };
        if (args[0] === 'fetch') return { code: 0, stdout: '', stderr: '' };
        if (args[0] === 'rev-list') return { code: 0, stdout: '0 1', stderr: '' };
        if (args[0] === 'merge') return { code: 0, stdout: '', stderr: '' };
        if (args[0] === 'rev-parse') return { code: 0, stdout: 'new-head', stderr: '' };
        throw new Error(`unexpected git call: ${args.join(' ')}`);
    };

    const result = await applyUpdate({
        runGit,
        rootDir,
        now: new Date('2026-07-16T12:00:00.000Z'),
    });
    assert.equal(result.ok, true);
    assert.equal(result.updated, true);
    assert.equal(JSON.parse(fs.readFileSync(path.join(rootDir, 'limebot.json'))).skills.enabled[0], 'local');
    assert.equal(fs.existsSync(path.join(result.backup.backupDir, 'manifest.json')), true);
    assert.equal(calls.some((call) => call.startsWith('merge --ff-only')), true);
});
