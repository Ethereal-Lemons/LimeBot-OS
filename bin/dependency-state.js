import crypto from 'crypto';
import fs from 'fs';
import path from 'path';

export const DEPENDENCY_STATE_SCHEMA = 2;

function hashFile(filePath) {
    if (!fs.existsSync(filePath)) return null;
    return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function runtimeMajor(version) {
    const match = String(version || '').match(/v?(\d+)/);
    return match ? Number(match[1]) : null;
}

function pythonMajorMinor(version) {
    if (!version) return null;
    if (typeof version === 'object' && Number.isInteger(version.major) && Number.isInteger(version.minor)) {
        return `${version.major}.${version.minor}`;
    }
    const match = String(version).match(/(\d+)\.(\d+)/);
    return match ? `${match[1]}.${match[2]}` : null;
}

function normalizeRuntimePath(value) {
    const resolved = path.resolve(String(value || ''));
    return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
}

export function createDependencyState() {
    return { schemaVersion: DEPENDENCY_STATE_SCHEMA, npm: null, python: null, features: {} };
}

export function buildNpmFingerprint({ lockfilePath, nodeVersion = process.version, profile = 'core' }) {
    return {
        manifestHash: hashFile(lockfilePath),
        nodeMajor: runtimeMajor(nodeVersion),
        profile,
    };
}

export function buildPythonFingerprint({ requirementsPath, venvPython, pythonVersion, profile = 'core' }) {
    return {
        manifestHash: hashFile(requirementsPath),
        pythonMajorMinor: pythonMajorMinor(pythonVersion),
        venvPython: normalizeRuntimePath(venvPython),
        profile,
    };
}

export function loadDependencyState(filePath) {
    try {
        const parsed = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
        if (!parsed || typeof parsed !== 'object') return null;
        // Schema 1 installed every workspace and every Python extra. Force a
        // one-time core refresh rather than treating that broad install as a
        // valid profile, while preserving a valid JSON state file.
        if (parsed.schemaVersion === 1) return createDependencyState();
        if (parsed.schemaVersion !== DEPENDENCY_STATE_SCHEMA) return null;
        return {
            schemaVersion: DEPENDENCY_STATE_SCHEMA,
            npm: parsed.npm && typeof parsed.npm === 'object' ? parsed.npm : null,
            python: parsed.python && typeof parsed.python === 'object' ? parsed.python : null,
            features: parsed.features && typeof parsed.features === 'object' ? parsed.features : {},
        };
    } catch {
        return null;
    }
}

export function writeDependencyStateAtomic(filePath, state) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    const tempPath = `${filePath}.${process.pid}.${crypto.randomBytes(6).toString('hex')}.tmp`;
    try {
        fs.writeFileSync(tempPath, `${JSON.stringify(state, null, 2)}\n`, 'utf-8');
        fs.renameSync(tempPath, filePath);
    } finally {
        if (fs.existsSync(tempPath)) fs.unlinkSync(tempPath);
    }
}

export function evaluateDependencyState(kind, stored, current, sentinels = []) {
    if (!current?.manifestHash) {
        return { installRequired: true, reason: `${kind} manifest missing` };
    }
    const missingSentinel = sentinels.find((sentinel) => !fs.existsSync(sentinel));
    if (missingSentinel) {
        return { installRequired: true, reason: `${kind} dependencies missing` };
    }
    if (!stored) {
        return { installRequired: true, reason: `${kind} state not recorded` };
    }
    if (stored.manifestHash !== current.manifestHash) {
        return { installRequired: true, reason: `${kind} manifest changed` };
    }
    if (stored.profile !== current.profile) {
        return { installRequired: true, reason: `${kind} install profile changed` };
    }

    if (kind === 'npm' && stored.nodeMajor !== current.nodeMajor) {
        return { installRequired: true, reason: 'Node.js major version changed' };
    }
    if (kind === 'python') {
        if (stored.pythonMajorMinor !== current.pythonMajorMinor) {
            return { installRequired: true, reason: 'Python version changed' };
        }
        if (stored.venvPython !== current.venvPython) {
            return { installRequired: true, reason: 'Python environment changed' };
        }
    }

    return { installRequired: false, reason: `${kind} dependencies unchanged` };
}

export function recordSuccessfulInstall(state, kind, fingerprint) {
    const next = state?.schemaVersion === DEPENDENCY_STATE_SCHEMA
        ? { ...state }
        : createDependencyState();
    next[kind] = fingerprint;
    next.features = { ...(next.features || {}) };
    return next;
}

export function recordFeatureInstall(state, feature, fingerprint) {
    const next = state?.schemaVersion === DEPENDENCY_STATE_SCHEMA
        ? { ...state, features: { ...(state.features || {}) } }
        : createDependencyState();
    next.features[feature] = fingerprint;
    return next;
}

export function clearFeatures(state, features) {
    const next = state?.schemaVersion === DEPENDENCY_STATE_SCHEMA
        ? { ...state, features: { ...(state.features || {}) } }
        : createDependencyState();
    for (const feature of features) delete next.features[feature];
    return next;
}

export function isFeatureCurrent(state, feature, fingerprint, sentinels = []) {
    if (sentinels.some((sentinel) => !fs.existsSync(sentinel))) return false;
    const stored = state?.features?.[feature];
    return Boolean(stored && stored.manifestHash === fingerprint.manifestHash
        && stored.runtime === fingerprint.runtime);
}

export function buildFeatureFingerprint({ manifestPath, runtime }) {
    return { manifestHash: hashFile(manifestPath), runtime: String(runtime || '') };
}
