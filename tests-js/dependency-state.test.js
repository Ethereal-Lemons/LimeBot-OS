import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import os from 'os';
import path from 'path';

import {
    buildNpmFingerprint,
    buildPythonFingerprint,
    createDependencyState,
    evaluateDependencyState,
    loadDependencyState,
    recordSuccessfulInstall,
    writeDependencyStateAtomic,
} from '../bin/dependency-state.js';

function fixture() {
    return fs.mkdtempSync(path.join(os.tmpdir(), 'limebot-deps-'));
}

test('npm fingerprint is stable and changes with the lockfile', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const lockfile = path.join(dir, 'package-lock.json');
    fs.writeFileSync(lockfile, '{"lockfileVersion":3}');
    const first = buildNpmFingerprint({ lockfilePath: lockfile, nodeVersion: 'v22.1.0' });
    const second = buildNpmFingerprint({ lockfilePath: lockfile, nodeVersion: 'v22.9.0' });
    assert.deepEqual(first, second);

    fs.writeFileSync(lockfile, '{"lockfileVersion":3,"changed":true}');
    const changed = buildNpmFingerprint({ lockfilePath: lockfile, nodeVersion: 'v22.1.0' });
    assert.notEqual(first.manifestHash, changed.manifestHash);
});

test('python fingerprint tracks requirements, version, and venv', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const requirements = path.join(dir, 'requirements.txt');
    fs.writeFileSync(requirements, 'fastapi>=0.109\n');
    const fingerprint = buildPythonFingerprint({
        requirementsPath: requirements,
        venvPython: path.join(dir, '.venv', 'python'),
        pythonVersion: { major: 3, minor: 14 },
    });
    assert.equal(fingerprint.pythonMajorMinor, '3.14');
    assert.match(fingerprint.venvPython, /\.venv/);
});

test('missing sentinels invalidate otherwise matching state', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const lockfile = path.join(dir, 'package-lock.json');
    fs.writeFileSync(lockfile, '{}');
    const current = buildNpmFingerprint({ lockfilePath: lockfile, nodeVersion: 'v22' });
    const result = evaluateDependencyState('npm', current, current, [path.join(dir, 'node_modules')]);
    assert.equal(result.installRequired, true);
    assert.equal(result.reason, 'npm dependencies missing');
});

test('matching fingerprints and sentinels skip installation', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const lockfile = path.join(dir, 'package-lock.json');
    const sentinel = path.join(dir, 'node_modules');
    fs.writeFileSync(lockfile, '{}');
    fs.mkdirSync(sentinel);
    const current = buildNpmFingerprint({ lockfilePath: lockfile, nodeVersion: 'v22' });
    assert.deepEqual(evaluateDependencyState('npm', current, current, [sentinel]), {
        installRequired: false,
        reason: 'npm dependencies unchanged',
    });
});

test('hoisted workspaces do not require per-workspace node_modules folders', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const lockfile = path.join(dir, 'package-lock.json');
    const rootModules = path.join(dir, 'node_modules');
    const hiddenLockfile = path.join(rootModules, '.package-lock.json');
    fs.writeFileSync(lockfile, '{}');
    fs.mkdirSync(rootModules);
    fs.writeFileSync(hiddenLockfile, '{}');
    const current = buildNpmFingerprint({ lockfilePath: lockfile, nodeVersion: 'v22' });

    assert.equal(
        evaluateDependencyState('npm', current, current, [rootModules, hiddenLockfile]).installRequired,
        false,
    );
    assert.equal(fs.existsSync(path.join(dir, 'bridge', 'node_modules')), false);
});

test('runtime and manifest changes report specific invalidation reasons', () => {
    const npmCurrent = { manifestHash: 'a', nodeMajor: 22 };
    assert.equal(
        evaluateDependencyState('npm', { manifestHash: 'a', nodeMajor: 20 }, npmCurrent).reason,
        'Node.js major version changed',
    );
    const pythonCurrent = { manifestHash: 'a', pythonMajorMinor: '3.14', venvPython: '/venv/python' };
    assert.equal(
        evaluateDependencyState(
            'python',
            { ...pythonCurrent, pythonMajorMinor: '3.13' },
            pythonCurrent,
        ).reason,
        'Python version changed',
    );
    assert.equal(
        evaluateDependencyState('python', { ...pythonCurrent, manifestHash: 'b' }, pythonCurrent).reason,
        'python manifest changed',
    );
});

test('state loading rejects missing, malformed, and old schemas', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const statePath = path.join(dir, 'state.json');
    assert.equal(loadDependencyState(statePath), null);
    fs.writeFileSync(statePath, 'not-json');
    assert.equal(loadDependencyState(statePath), null);
    fs.writeFileSync(statePath, '{"schemaVersion":999}');
    assert.equal(loadDependencyState(statePath), null);
});

test('successful state writes atomically and preserves the other ecosystem', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const statePath = path.join(dir, 'state.json');
    const npmFingerprint = { manifestHash: 'npm', nodeMajor: 22 };
    const pythonFingerprint = {
        manifestHash: 'python',
        pythonMajorMinor: '3.14',
        venvPython: '/venv/python',
    };
    let state = recordSuccessfulInstall(createDependencyState(), 'npm', npmFingerprint);
    state = recordSuccessfulInstall(state, 'python', pythonFingerprint);
    writeDependencyStateAtomic(statePath, state);

    assert.deepEqual(loadDependencyState(statePath), state);
    const replaced = recordSuccessfulInstall(state, 'npm', {
        manifestHash: 'npm-new',
        nodeMajor: 24,
    });
    writeDependencyStateAtomic(statePath, replaced);
    assert.deepEqual(loadDependencyState(statePath), replaced);
    assert.equal(fs.readdirSync(dir).some((name) => name.endsWith('.tmp')), false);
});
