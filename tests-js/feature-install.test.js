import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';

import {
    FEATURE_DEFINITIONS,
    FULL_FEATURE_INSTALL_ORDER,
    getCoreNpmInstallSpec,
    getDependencySpawnSpec,
    getFeatureInstallSpec,
    getRequestedFeatures,
    installRequestedFeatureSet,
    installFeatureThen,
    settleDependencyLanes,
    watchConfigFile,
} from '../bin/feature-install.js';

test('feature mapping is closed and uses argv arrays', () => {
    const rootDir = path.resolve('fixture');
    assert.deepEqual(Object.keys(FEATURE_DEFINITIONS), [
        'browser', 'memory', 'documents', 'mcp', 'whatsapp', 'extension',
    ]);
    const browser = getFeatureInstallSpec('browser', { rootDir, venvPython: 'python' });
    assert.equal(browser.command, 'python');
    assert.deepEqual(browser.args.slice(0, 4), ['-m', 'pip', 'install', '-r']);
    const whatsapp = getFeatureInstallSpec('whatsapp', { rootDir, venvPython: 'python' });
    assert.deepEqual(whatsapp.args, [
        'install', '--include-workspace-root=false', '--workspace', 'bridge',
    ]);
    assert.throws(() => getFeatureInstallSpec('whatsapp; rm -rf', { rootDir, venvPython: 'python' }));
});

test('core npm clean and reconcile commands select only root and web', () => {
    assert.deepEqual(getCoreNpmInstallSpec({ clean: true }).args,
        ['ci', '--include-workspace-root', '--workspace', 'web']);
    assert.deepEqual(getCoreNpmInstallSpec({ clean: false }).args,
        ['install', '--include-workspace-root', '--workspace', 'web']);
});

test('all expands to every optional feature in a stable retry order', () => {
    assert.deepEqual(getRequestedFeatures('all'), [...FULL_FEATURE_INSTALL_ORDER]);
    assert.deepEqual(getRequestedFeatures('memory'), ['memory']);
    assert.throws(() => getRequestedFeatures('all; rm -rf'), /Unknown feature/);
    assert.deepEqual(FULL_FEATURE_INSTALL_ORDER, Object.keys(FEATURE_DEFINITIONS));
});

test('all installs every profile before launch-verifying Chromium', async () => {
    const events = [];
    await installRequestedFeatureSet('all', {
        installFeature: async (feature) => events.push(feature),
        ensureBrowser: async () => events.push('chromium'),
    });
    assert.deepEqual(events, [...FULL_FEATURE_INSTALL_ORDER, 'chromium']);

    events.length = 0;
    await installRequestedFeatureSet('memory', {
        installFeature: async (feature) => events.push(feature),
        ensureBrowser: async () => events.push('chromium'),
    });
    assert.deepEqual(events, ['memory']);
});

test('Windows command shims run through ComSpec without enabling shell mode', () => {
    assert.deepEqual(
        getDependencySpawnSpec('npm.cmd', ['install', '--workspace', 'web'], {
            platform: 'win32',
            comspec: 'C:\\Windows\\System32\\cmd.exe',
        }),
        {
            command: 'C:\\Windows\\System32\\cmd.exe',
            args: ['/d', '/s', '/c', 'npm.cmd', 'install', '--workspace', 'web'],
        },
    );
    assert.deepEqual(
        getDependencySpawnSpec('/usr/bin/npm', ['install'], { platform: 'linux' }),
        { command: '/usr/bin/npm', args: ['install'] },
    );
});

test('dependency lanes run concurrently and report both failures', async () => {
    let release;
    const barrier = new Promise((resolve) => { release = resolve; });
    const started = [];
    const running = settleDependencyLanes({
        npm: async () => { started.push('npm'); await barrier; throw new Error('npm failed'); },
        python: async () => { started.push('python'); await barrier; throw new Error('python failed'); },
    });
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(started.sort(), ['npm', 'python']);
    release();
    const result = await running;
    assert.deepEqual(Object.keys(result.failures).sort(), ['npm', 'python']);
});

test('successful and failed lanes remain distinguishable for one state write', async () => {
    const result = await settleDependencyLanes({
        npm: async () => 'npm-fingerprint',
        python: async () => { throw new Error('pip failed'); },
    });
    assert.deepEqual(result.successes, { npm: 'npm-fingerprint' });
    assert.match(result.failures.python.message, /pip failed/);
});

test('a failed optional install never starts its process', async () => {
    let started = false;
    await assert.rejects(() => installFeatureThen({
        install: async () => { throw new Error('registry unavailable'); },
        next: async () => { started = true; },
    }), /registry unavailable/);
    assert.equal(started, false);
});

test('directory watcher observes first .env creation and coalesces changes', async () => {
    let listener;
    let closed = false;
    let changes = 0;
    const handle = watchConfigFile({
        directory: 'fixture',
        debounceMs: 5,
        onChange: () => { changes += 1; },
        watch: (_directory, callback) => {
            listener = callback;
            return { close: () => { closed = true; } };
        },
    });
    listener('rename', '.env');
    listener('change', '.env');
    listener('change', 'other.txt');
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(changes, 1);
    handle.close();
    assert.equal(closed, true);
});
