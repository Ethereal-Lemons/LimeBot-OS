import { execFile } from 'child_process';

const WINDOWS_PATH_REGISTRY_KEYS = Object.freeze([
    'HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment',
    'HKCU\\Environment',
]);

export function parseWindowsRegistryPath(output) {
    for (const line of String(output || '').split(/\r?\n/)) {
        const match = line.match(/^\s*Path\s+REG_(?:EXPAND_)?SZ\s+(.*?)\s*$/i);
        if (match) return match[1];
    }
    return '';
}

export function expandWindowsEnvironmentVariables(value, env = process.env) {
    const lookup = new Map(Object.entries(env).map(([key, item]) => [key.toLowerCase(), String(item)]));
    return String(value || '').replace(/%([^%]+)%/g, (token, name) => lookup.get(name.toLowerCase()) || token);
}

export function mergeWindowsPathValues(current, ...persistedValues) {
    const result = [];
    const seen = new Set();
    for (const value of [current, ...persistedValues]) {
        for (const entry of String(value || '').split(';')) {
            const trimmed = entry.trim();
            if (!trimmed) continue;
            const normalized = trimmed.replace(/[\\/]+$/, '').toLowerCase();
            if (seen.has(normalized)) continue;
            seen.add(normalized);
            result.push(trimmed);
        }
    }
    return result.join(';');
}

function queryRegistryPath(key, execFileImpl) {
    return new Promise((resolve) => {
        execFileImpl(
            'reg.exe',
            ['query', key, '/v', 'Path'],
            { windowsHide: true, encoding: 'utf8' },
            (error, stdout) => resolve(error ? '' : parseWindowsRegistryPath(stdout)),
        );
    });
}

/**
 * Merge persisted Windows PATH values into this process.
 *
 * A terminal opened before an installer updates PATH keeps a stale environment.
 * Refreshing here lets feature checks and child processes see newly installed
 * tools without asking users to close their terminal or reboot.
 */
export async function refreshWindowsProcessPath({
    platform = process.platform,
    env = process.env,
    execFileImpl = execFile,
} = {}) {
    const existingKey = Object.keys(env).find((key) => key.toLowerCase() === 'path') || 'PATH';
    if (platform !== 'win32') return String(env[existingKey] || '');

    const persisted = await Promise.all(
        WINDOWS_PATH_REGISTRY_KEYS.map((key) => queryRegistryPath(key, execFileImpl)),
    );
    const expanded = persisted.map((value) => expandWindowsEnvironmentVariables(value, env));
    env[existingKey] = mergeWindowsPathValues(env[existingKey], ...expanded);
    return env[existingKey];
}
