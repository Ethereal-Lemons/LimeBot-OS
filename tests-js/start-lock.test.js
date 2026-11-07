import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { acquireStartLock, readStartLock } from '../bin/start-lock.js';

function fixture() {
    return fs.mkdtempSync(path.join(os.tmpdir(), 'limebot-start-lock-'));
}

test('only one supervisor can own a project start lock', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const lockPath = path.join(dir, 'data', 'cli-start.lock');
    const first = acquireStartLock({ lockPath, rootDir: dir, pid: 101 });

    assert.equal(first.acquired, true);
    assert.equal(readStartLock(lockPath).pid, 101);

    const second = acquireStartLock({
        lockPath,
        rootDir: dir,
        pid: 202,
        isPidAlive: (pid) => pid === 101,
    });
    assert.deepEqual(second, { acquired: false, ownerPid: 101, reason: 'running' });
    assert.equal(readStartLock(lockPath).pid, 101);
});

test('a lock left by a dead supervisor can be reclaimed', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const lockPath = path.join(dir, 'cli-start.lock');
    const stale = acquireStartLock({ lockPath, rootDir: dir, pid: 101 });
    stale.release();
    fs.writeFileSync(lockPath, JSON.stringify({
        pid: 101,
        rootDir: dir,
        token: 'stale-owner',
    }));

    const next = acquireStartLock({
        lockPath,
        rootDir: dir,
        pid: 202,
        isPidAlive: () => false,
    });

    assert.equal(next.acquired, true);
    assert.equal(readStartLock(lockPath).pid, 202);
    next.release();
});

test('releasing a lock does not remove a replacement owner', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const lockPath = path.join(dir, 'cli-start.lock');
    const first = acquireStartLock({ lockPath, rootDir: dir, pid: 101 });
    fs.writeFileSync(lockPath, JSON.stringify({
        pid: 202,
        rootDir: dir,
        token: 'replacement-owner',
    }));

    assert.equal(first.release(), false);
    assert.equal(readStartLock(lockPath).pid, 202);
});

test('an incomplete lock is treated as an in-progress start', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const lockPath = path.join(dir, 'cli-start.lock');
    fs.writeFileSync(lockPath, '{');

    const result = acquireStartLock({ lockPath, rootDir: dir, pid: 202 });

    assert.deepEqual(result, { acquired: false, ownerPid: null, reason: 'starting' });
    assert.equal(fs.readFileSync(lockPath, 'utf8'), '{');
});
