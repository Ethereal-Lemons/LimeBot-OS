export function classifyVenv(venvDir, venvPython, { fs }) {
    if (!fs.existsSync(venvDir)) return 'missing';
    return fs.existsSync(venvPython) ? 'valid' : 'incomplete';
}

export function readRecentUpdateCache(
    cachePath,
    { fs, now = () => Date.now(), ttlMs },
) {
    try {
        if (!fs.existsSync(cachePath)) return null;
        const cached = JSON.parse(fs.readFileSync(cachePath, 'utf8'));
        if (!cached || typeof cached !== 'object' || !Number.isFinite(cached.checkedAt)) {
            return null;
        }
        const age = now() - cached.checkedAt;
        return age >= 0 && age < ttlMs ? cached : null;
    } catch {
        return null;
    }
}

export function shouldDiscoverUpdates({ quickMode }) {
    return !quickMode;
}

export function startupWaitTarget({ backendOnly, frontendOnly }) {
    if (frontendOnly) return 'none';
    return backendOnly ? 'readiness' : 'liveness';
}

export function startBackgroundUpdateDiscovery({ discover, onStatus, onError }) {
    return Promise.resolve()
        .then(discover)
        .then((status) => onStatus?.(status))
        .catch((error) => onError?.(error));
}

export function runVenvPip(venvPython, args, { run }) {
    return run(venvPython, ['-m', 'pip', ...args]);
}

export async function ensureVenvExecutable({
    venvDir,
    venvPython,
    systemPython,
    fs,
    now = () => Date.now(),
    create,
    validate,
}) {
    const initialState = classifyVenv(venvDir, venvPython, { fs });
    if (initialState === 'valid') {
        await validate(venvPython);
        return { state: initialState, repaired: false, preservedPath: null };
    }

    const preservedPath = initialState === 'incomplete'
        ? `${venvDir}.incomplete-${now()}`
        : null;
    const repairCommand = `${systemPython} -m venv "${venvDir}"`;

    if (preservedPath) fs.renameSync(venvDir, preservedPath);
    try {
        await create(systemPython, venvDir);
        if (!fs.existsSync(venvPython)) {
            throw new Error(`virtual environment executable was not created at ${venvPython}`);
        }
        await validate(venvPython);
        return { state: initialState, repaired: true, preservedPath };
    } catch (cause) {
        if (fs.existsSync(venvDir)) fs.rmSync(venvDir, { recursive: true, force: true });
        if (preservedPath && fs.existsSync(preservedPath)) {
            fs.renameSync(preservedPath, venvDir);
        }
        const detail = cause?.message ? ` ${cause.message}` : '';
        const preservation = preservedPath
            ? 'The previous directory was preserved. '
            : '';
        throw new Error(
            `Could not create a usable virtual environment at ${venvDir}.${detail}\n` +
            `${preservation}Repair: ${repairCommand}`,
            { cause },
        );
    }
}
