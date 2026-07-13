import test from 'node:test';
import assert from 'node:assert/strict';

import {
    expandWindowsEnvironmentVariables,
    mergeWindowsPathValues,
    parseWindowsRegistryPath,
    refreshWindowsProcessPath,
} from '../bin/windows-path.js';

test('Windows registry PATH output is parsed without locale-dependent headings', () => {
    const output = `HKEY_CURRENT_USER\\Environment\r\n    Path    REG_EXPAND_SZ    %LOCALAPPDATA%\\Tools;C:\\FFmpeg\\bin\r\n`;
    assert.equal(parseWindowsRegistryPath(output), '%LOCALAPPDATA%\\Tools;C:\\FFmpeg\\bin');
});

test('Windows PATH values expand variables and deduplicate case-insensitively', () => {
    const expanded = expandWindowsEnvironmentVariables('%LOCALAPPDATA%\\Tools', {
        LOCALAPPDATA: 'C:\\Users\\lime\\AppData\\Local',
    });
    assert.equal(expanded, 'C:\\Users\\lime\\AppData\\Local\\Tools');
    assert.equal(
        mergeWindowsPathValues('C:\\Windows;C:\\FFmpeg\\bin', 'c:\\ffmpeg\\bin\\;C:\\NewTool'),
        'C:\\Windows;C:\\FFmpeg\\bin;C:\\NewTool',
    );
});

test('Windows PATH refresh merges persisted machine and user values into stale environments', async () => {
    const env = { PATH: 'C:\\Windows', LOCALAPPDATA: 'C:\\Users\\lime\\AppData\\Local' };
    const execFileImpl = (_command, args, _options, callback) => {
        const isUser = args[1] === 'HKCU\\Environment';
        const value = isUser
            ? '%LOCALAPPDATA%\\Microsoft\\WinGet\\Packages\\FFmpeg\\bin'
            : 'C:\\Program Files\\Shared';
        callback(null, `Path    REG_EXPAND_SZ    ${value}\r\n`);
    };

    const refreshed = await refreshWindowsProcessPath({ platform: 'win32', env, execFileImpl });
    assert.equal(
        refreshed,
        'C:\\Windows;C:\\Program Files\\Shared;C:\\Users\\lime\\AppData\\Local\\Microsoft\\WinGet\\Packages\\FFmpeg\\bin',
    );
    assert.equal(env.PATH, refreshed);
});

test('PATH refresh is a no-op outside Windows', async () => {
    const env = { PATH: '/usr/bin' };
    const refreshed = await refreshWindowsProcessPath({
        platform: 'linux',
        env,
        execFileImpl: () => assert.fail('registry should not be queried'),
    });
    assert.equal(refreshed, '/usr/bin');
});
