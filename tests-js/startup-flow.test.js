import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import os from 'os';
import path from 'path';

import {
    classifyVenv,
    ensureVenvExecutable,
    readRecentUpdateCache,
    runVenvPip,
    shouldDiscoverUpdates,
    startBackgroundUpdateDiscovery,
    startupWaitTarget,
} from '../bin/startup-flow.js';

function tempDir(t) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'limebot-startup-'));
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    return dir;
}

test('recent update cache handles fresh, expired, malformed, and missing files', (t) => {
    const dir = tempDir(t);
    const cache = path.join(dir, 'cache.json');
    const options = { fs, now: () => 10_000, ttlMs: 1_000 };
    assert.equal(readRecentUpdateCache(cache, options), null);
    fs.writeFileSync(cache, '{bad');
    assert.equal(readRecentUpdateCache(cache, options), null);
    fs.writeFileSync(cache, JSON.stringify({ checkedAt: 8_999 }));
    assert.equal(readRecentUpdateCache(cache, options), null);
    fs.writeFileSync(cache, JSON.stringify({ checkedAt: 9_001, hasUpdate: false }));
    assert.equal(readRecentUpdateCache(cache, options).hasUpdate, false);
});

test('venv classification distinguishes missing, incomplete, and valid', (t) => {
    const dir = tempDir(t);
    const venv = path.join(dir, '.venv');
    const python = path.join(venv, 'Scripts', 'python.exe');
    assert.equal(classifyVenv(venv, python, { fs }), 'missing');
    fs.mkdirSync(venv);
    assert.equal(classifyVenv(venv, python, { fs }), 'incomplete');
    fs.mkdirSync(path.dirname(python));
    fs.writeFileSync(python, 'exe');
    assert.equal(classifyVenv(venv, python, { fs }), 'valid');
});

test('startup mode selects discovery and liveness behavior', () => {
    assert.equal(shouldDiscoverUpdates({ quickMode: false }), true);
    assert.equal(shouldDiscoverUpdates({ quickMode: true }), false);
    assert.equal(startupWaitTarget({ backendOnly: false, frontendOnly: false }), 'liveness');
    assert.equal(startupWaitTarget({ backendOnly: true, frontendOnly: false }), 'readiness');
    assert.equal(startupWaitTarget({ backendOnly: false, frontendOnly: true }), 'none');
});

test('background discovery returns before a blocked remote resolves and handles rejection', async () => {
    let release;
    const blocked = new Promise((resolve) => { release = resolve; });
    let reported = null;
    const task = startBackgroundUpdateDiscovery({
        discover: () => blocked,
        onStatus: (status) => { reported = status; },
    });
    await Promise.resolve();
    assert.equal(reported, null);
    release({ hasUpdate: true });
    await task;
    assert.deepEqual(reported, { hasUpdate: true });

    let handled = false;
    await startBackgroundUpdateDiscovery({
        discover: async () => { throw new Error('offline'); },
        onError: () => { handled = true; },
    });
    assert.equal(handled, true);
});

test('incomplete venv is preserved before create and validation uses venv Python', async (t) => {
    const dir = tempDir(t);
    const venv = path.join(dir, '.venv');
    const python = path.join(venv, 'Scripts', 'python.exe');
    fs.mkdirSync(venv);
    fs.writeFileSync(path.join(venv, 'old.txt'), 'old');
    const events = [];

    const result = await ensureVenvExecutable({
        venvDir: venv,
        venvPython: python,
        systemPython: 'py -3.14',
        fs,
        now: () => 123,
        create: async (command, target) => {
            events.push(['create', command, fs.existsSync(`${venv}.incomplete-123`)]);
            fs.mkdirSync(path.dirname(path.join(target, 'Scripts', 'python.exe')), { recursive: true });
            fs.writeFileSync(path.join(target, 'Scripts', 'python.exe'), 'exe');
        },
        validate: async (command) => events.push(['validate', command]),
    });

    assert.equal(result.preservedPath, `${venv}.incomplete-123`);
    assert.deepEqual(events, [
        ['create', 'py -3.14', true],
        ['validate', python],
    ]);
    await runVenvPip(python, ['install', '-r', 'requirements.txt'], {
        run: async (command, args) => events.push(['pip', command, args]),
    });
    assert.deepEqual(events.at(-1), [
        'pip', python, ['-m', 'pip', 'install', '-r', 'requirements.txt'],
    ]);
    assert.equal(fs.existsSync(path.join(result.preservedPath, 'old.txt')), true);
});

test('failed venv repair removes only the new partial and restores preserved directory', async (t) => {
    const dir = tempDir(t);
    const venv = path.join(dir, '.venv');
    const python = path.join(venv, 'bin', 'python');
    fs.mkdirSync(venv);
    fs.writeFileSync(path.join(venv, 'keep.txt'), 'keep');

    await assert.rejects(() => ensureVenvExecutable({
        venvDir: venv,
        venvPython: python,
        systemPython: '/usr/bin/python3',
        fs,
        now: () => 456,
        create: async (_command, target) => {
            fs.mkdirSync(target);
            fs.writeFileSync(path.join(target, 'partial.txt'), 'partial');
            throw new Error('create failed');
        },
        validate: async () => { },
    }), /Repair: \/usr\/bin\/python3 -m venv/);

    assert.equal(fs.readFileSync(path.join(venv, 'keep.txt'), 'utf8'), 'keep');
    assert.equal(fs.existsSync(path.join(venv, 'partial.txt')), false);
    assert.equal(fs.existsSync(`${venv}.incomplete-456`), false);
});
