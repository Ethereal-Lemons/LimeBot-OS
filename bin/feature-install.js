import fs from 'fs';
import path from 'path';

export const FEATURE_DEFINITIONS = Object.freeze({
    browser: Object.freeze({ kind: 'python', manifest: 'requirements-browser.txt' }),
    memory: Object.freeze({ kind: 'python', manifest: 'requirements-memory.txt' }),
    documents: Object.freeze({ kind: 'python', manifest: 'requirements-documents.txt' }),
    mcp: Object.freeze({ kind: 'python', manifest: 'requirements-mcp.txt' }),
    video: Object.freeze({
        kind: 'python',
        manifest: 'requirements-video.txt',
        requiredBinaries: Object.freeze(['ffmpeg', 'ffprobe']),
    }),
    whatsapp: Object.freeze({ kind: 'node', workspace: 'bridge' }),
    extension: Object.freeze({ kind: 'node', workspace: 'extension' }),
});

export const FULL_FEATURE_INSTALL_ORDER = Object.freeze([
    'browser',
    'memory',
    'documents',
    'mcp',
    'video',
    'whatsapp',
    'extension',
]);

export function getVideoBinaryInstallInstructions(platform = process.platform) {
    if (platform === 'win32') return 'winget install --id Gyan.FFmpeg -e';
    if (platform === 'darwin') return 'brew install ffmpeg';
    return 'sudo apt install ffmpeg  # Debian/Ubuntu\n  sudo dnf install ffmpeg  # Fedora/RHEL';
}

export function getVideoReadinessState({ pythonDependenciesReady, binariesReady }) {
    if (!pythonDependenciesReady) return 'python-dependencies-missing';
    if (!binariesReady) return 'ffmpeg-missing';
    return 'ready';
}

export function getFeatureDefinition(name) {
    return FEATURE_DEFINITIONS[String(name || '').toLowerCase()] || null;
}

export function getFeatureInstallSpec(name, { rootDir, venvPython }) {
    const definition = getFeatureDefinition(name);
    if (!definition) throw new Error(`Unknown feature '${name}'.`);
    if (definition.kind === 'python') {
        return {
            kind: 'python',
            command: venvPython,
            args: ['-m', 'pip', 'install', '-r', path.join(rootDir, definition.manifest)],
            cwd: rootDir,
            manifestPath: path.join(rootDir, definition.manifest),
        };
    }
    return {
        kind: 'node',
        command: 'npm',
        args: ['install', '--include-workspace-root=false', '--workspace', definition.workspace],
        cwd: rootDir,
        // npm resolution is lockfile-defined; any workspace dependency change
        // updates this fingerprint even when package.json itself is unchanged.
        manifestPath: path.join(rootDir, 'package-lock.json'),
        featureManifestPath: path.join(rootDir, definition.workspace, 'package.json'),
        workspace: definition.workspace,
    };
}

export function getCoreNpmInstallSpec({ clean }) {
    return {
        command: 'npm',
        args: [clean ? 'ci' : 'install', '--include-workspace-root', '--workspace', 'web'],
    };
}

export function getRequestedFeatures(name) {
    const normalized = String(name || '').toLowerCase();
    if (normalized === 'all') return [...FULL_FEATURE_INSTALL_ORDER];
    if (getFeatureDefinition(normalized)) return [normalized];
    throw new Error(`Unknown feature '${name}'.`);
}

export async function installRequestedFeatureSet(
    name,
    { installFeature, ensureBrowser },
) {
    const requested = getRequestedFeatures(name);
    for (const feature of requested) {
        await installFeature(feature);
    }
    if (String(name || '').toLowerCase() === 'all') {
        await ensureBrowser();
    }
    return requested;
}

export function getDependencySpawnSpec(
    command,
    args,
    { platform = process.platform, comspec = process.env.ComSpec || 'cmd.exe' } = {},
) {
    const executable = String(command || '');
    const commandArgs = Array.isArray(args) ? [...args] : [];
    if (platform === 'win32' && /\.(?:cmd|bat)$/i.test(executable)) {
        return {
            command: comspec,
            args: ['/d', '/s', '/c', executable, ...commandArgs],
        };
    }
    return { command: executable, args: commandArgs };
}

export async function settleDependencyLanes(lanes) {
    const entries = Object.entries(lanes).filter(([, lane]) => typeof lane === 'function');
    const settled = await Promise.allSettled(entries.map(([, lane]) => lane()));
    const successes = {};
    const failures = {};
    settled.forEach((result, index) => {
        const name = entries[index][0];
        if (result.status === 'fulfilled') successes[name] = result.value;
        else failures[name] = result.reason;
    });
    return { successes, failures };
}

export async function installFeatureThen({ install, next }) {
    await install();
    return next();
}

export function watchConfigFile({ directory, filename = '.env', debounceMs = 500, onChange, watch = fs.watch }) {
    let timer = null;
    const watcher = watch(directory, (_event, changed) => {
        if (changed && path.basename(String(changed)) !== filename) return;
        clearTimeout(timer);
        timer = setTimeout(onChange, debounceMs);
    });
    return {
        close() {
            clearTimeout(timer);
            watcher.close();
        },
    };
}
