import fs from 'fs';
import path from 'path';

const STATE_PATHS = [
    '.env',
    'limebot.json',
    'allowed_paths.txt',
    'data/',
    'persona/',
    'logs/',
    'temp/',
];

const RESTORABLE_STATE_PATHS = [
    '.env',
    'limebot.json',
    'allowed_paths.txt',
    'data/',
    'logs/',
    'temp/',
    'persona/SOUL.md',
    'persona/IDENTITY.md',
    'persona/MEMORY.md',
    'persona/MOOD.md',
    'persona/RELATIONSHIPS.md',
    'persona/USER.md',
    'persona/TOOLS.md',
    'persona/HEARTBEAT.md',
    'persona/users/',
    'persona/memory/',
];

function normalizePath(value) {
    return String(value || '')
        .replace(/\\/g, '/')
        .replace(/^\.\//, '')
        .trim();
}

function resolveStateDir(rootDir, stateDir) {
    const configured = String(stateDir || process.env.LIMEBOT_STATE_DIR || '').trim();
    return configured ? path.resolve(configured) : rootDir;
}

export function isRuntimeStatePath(value, { allowUntrackedSkills = true } = {}) {
    const normalized = normalizePath(value);
    if (!normalized) return false;
    if (normalized === '.env' || (normalized.startsWith('.env.') && normalized !== '.env.example')) {
        return true;
    }
    if (STATE_PATHS.filter((prefix) => prefix !== '.env').some((prefix) => prefix.endsWith('/')
        ? normalized === prefix.slice(0, -1) || normalized.startsWith(prefix)
        : normalized === prefix)) {
        return true;
    }
    return allowUntrackedSkills && (normalized === 'skills' || normalized.startsWith('skills/'));
}

function parsePorcelainPath(rawPath) {
    const normalized = normalizePath(rawPath);
    if (!normalized.includes(' -> ')) return normalized;
    return normalized.split(' -> ').pop().trim();
}

export function parsePorcelainStatus(output = '') {
    const changes = [];
    for (const line of String(output || '').split(/\r?\n/)) {
        if (!line || line.length < 3) continue;
        const indexStatus = line[0];
        const worktreeStatus = line[1];
        const rawPath = line.slice(3);
        const pathName = parsePorcelainPath(rawPath);
        changes.push({
            raw: line,
            path: pathName,
            indexStatus,
            worktreeStatus,
            status: `${indexStatus}${worktreeStatus}`,
            untracked: indexStatus === '?' && worktreeStatus === '?',
            runtimeState: isRuntimeStatePath(pathName, {
                allowUntrackedSkills: indexStatus === '?' && worktreeStatus === '?',
            }),
        });
    }
    return changes;
}

export function classifyGitChanges(changes = []) {
    const list = Array.isArray(changes) ? changes : [];
    const codeChanges = list.filter((item) => !item.runtimeState);
    const stateChanges = list.filter((item) => item.runtimeState);
    let kind = 'clean';
    if (codeChanges.length > 0) kind = 'code-dirty';
    else if (stateChanges.length > 0) kind = 'state-only';
    return {
        kind,
        clean: kind === 'clean',
        stateOnly: kind === 'state-only',
        codeDirty: kind === 'code-dirty',
        changes: list,
        stateChanges,
        codeChanges,
    };
}

export async function inspectWorktree({ runGit }) {
    const result = await runGit(['status', '--porcelain=v1', '--untracked-files=all']);
    if (result.code !== 0) {
        return { available: false, error: result.stderr || 'git status failed', kind: 'unknown' };
    }
    return { available: true, ...classifyGitChanges(parsePorcelainStatus(result.stdout)) };
}

async function readBranchSnapshot(runGit) {
    const inRepo = await runGit(['rev-parse', '--is-inside-work-tree']);
    if (inRepo.code !== 0 || inRepo.stdout !== 'true') {
        return { ok: false, error: 'The current directory is not a Git worktree.' };
    }
    const head = await runGit(['rev-parse', 'HEAD']);
    const branch = await runGit(['symbolic-ref', '--quiet', '--short', 'HEAD']);
    if (head.code !== 0 || !head.stdout || branch.code !== 0 || !branch.stdout) {
        return { ok: false, error: 'Updates require a checked-out local branch.' };
    }
    const branchName = branch.stdout.trim();
    const remote = await runGit(['config', '--get', `branch.${branchName}.remote`]);
    const merge = await runGit(['config', '--get', `branch.${branchName}.merge`]);
    const remoteName = remote.code === 0 && remote.stdout ? remote.stdout.trim() : 'origin';
    const mergeRef = merge.code === 0 && merge.stdout
        ? merge.stdout.trim()
        : `refs/heads/${branchName}`;
    const remoteBranch = mergeRef.startsWith('refs/heads/')
        ? mergeRef.slice('refs/heads/'.length)
        : mergeRef;
    return {
        ok: true,
        head: head.stdout.trim().toLowerCase(),
        branchName,
        remoteName,
        remoteBranch,
    };
}

function copyIfPresent(fsImpl, source, destination) {
    if (!fsImpl.existsSync(source)) return false;
    fsImpl.mkdirSync(path.dirname(destination), { recursive: true });
    if (typeof fsImpl.cpSync === 'function') {
        fsImpl.cpSync(source, destination, { recursive: true, force: true });
    } else {
        const stat = fsImpl.statSync(source);
        if (stat.isDirectory()) {
            fsImpl.mkdirSync(destination, { recursive: true });
            for (const entry of fsImpl.readdirSync(source)) {
                copyIfPresent(fsImpl, path.join(source, entry), path.join(destination, entry));
            }
        } else {
            fsImpl.copyFileSync(source, destination);
        }
    }
    return true;
}

export function createStateBackup({ rootDir, stateDir, fsImpl = fs, now = new Date(), label = 'update' }) {
    const stamp = now.toISOString().replace(/[:.]/g, '-');
    const backupDir = path.join(rootDir, '.limebot-update-backups', `${stamp}-${label}`);
    const runtimeStateDir = resolveStateDir(rootDir, stateDir);
    const copied = [];
    for (const relative of STATE_PATHS) {
        const cleanRelative = relative.replace(/\/$/, '');
        if (copyIfPresent(
            fsImpl,
            path.join(runtimeStateDir, cleanRelative),
            path.join(backupDir, cleanRelative),
        )) copied.push(cleanRelative);
    }
    // Custom skills are untracked state.  Copy the directory as a whole; this
    // is intentionally conservative and keeps an update from losing local
    // extensions even if Git later learns about one of their filenames.
    if (copyIfPresent(fsImpl, path.join(runtimeStateDir, 'skills'), path.join(backupDir, 'skills'))) {
        copied.push('skills/');
    }
    fsImpl.mkdirSync(backupDir, { recursive: true });
    fsImpl.writeFileSync(
        path.join(backupDir, 'manifest.json'),
        JSON.stringify({ createdAt: now.toISOString(), copied }, null, 2),
        'utf-8',
    );
    return { backupDir, copied, stateDir: runtimeStateDir };
}

function restoreStateBackup({ rootDir, stateDir, backupDir, fsImpl = fs }) {
    const runtimeStateDir = resolveStateDir(rootDir, stateDir);
    for (const relative of RESTORABLE_STATE_PATHS) {
        const cleanRelative = relative.replace(/\/$/, '');
        copyIfPresent(
            fsImpl,
            path.join(backupDir, cleanRelative),
            path.join(runtimeStateDir, cleanRelative),
        );
    }
}

function writeRecord(rootDir, record, fsImpl = fs) {
    const target = path.join(rootDir, 'data', 'update-last.json');
    fsImpl.mkdirSync(path.dirname(target), { recursive: true });
    const temporary = `${target}.tmp-${process.pid}`;
    fsImpl.writeFileSync(temporary, JSON.stringify(record, null, 2), 'utf-8');
    fsImpl.renameSync(temporary, target);
}

function readRecord(rootDir, fsImpl = fs) {
    const target = path.join(rootDir, 'data', 'update-last.json');
    try {
        return JSON.parse(fsImpl.readFileSync(target, 'utf-8'));
    } catch {
        return null;
    }
}

export async function applyUpdate({ runGit, rootDir, stateDir, fsImpl = fs, now = new Date() }) {
    const worktree = await inspectWorktree({ runGit });
    if (!worktree.available) return { ok: false, reason: 'git-unavailable', worktree };
    if (worktree.codeDirty) {
        return {
            ok: false,
            reason: 'code-dirty',
            worktree,
            message: 'Tracked source changes are present. Commit or copy them aside before updating.',
        };
    }

    const snapshot = await readBranchSnapshot(runGit);
    if (!snapshot.ok) return { ok: false, reason: 'branch-unavailable', message: snapshot.error, worktree };

    const fetch = await runGit(
        ['fetch', '--quiet', snapshot.remoteName, snapshot.remoteBranch],
        120000,
    );
    if (fetch.code !== 0) {
        return { ok: false, reason: 'fetch-failed', message: fetch.stderr || 'Git fetch failed.', worktree };
    }
    const remoteHead = await runGit(['rev-parse', 'FETCH_HEAD']);
    const counts = await runGit(['rev-list', '--left-right', '--count', 'HEAD...FETCH_HEAD']);
    if (remoteHead.code !== 0 || counts.code !== 0) {
        return { ok: false, reason: 'compare-failed', message: 'Could not compare the local branch with the remote.', worktree };
    }
    const [ahead, behind] = counts.stdout.split(/\s+/).map((value) => Number.parseInt(value, 10));
    if (!Number.isInteger(ahead) || !Number.isInteger(behind)) {
        return { ok: false, reason: 'compare-failed', message: 'Git returned an invalid ahead/behind count.', worktree };
    }
    if (ahead > 0) {
        return {
            ok: false,
            reason: 'diverged',
            message: `The local branch is ahead by ${ahead} commit${ahead === 1 ? '' : 's'}; automatic updates only fast-forward.`,
            ahead,
            behind,
            worktree,
        };
    }
    if (behind === 0) return { ok: true, updated: false, reason: 'up-to-date', worktree, snapshot };

    const backup = createStateBackup({ rootDir, stateDir, fsImpl, now });
    const merge = await runGit(['merge', '--ff-only', 'FETCH_HEAD'], 120000);
    if (merge.code !== 0) {
        await runGit(['merge', '--abort'], 30000);
        return {
            ok: false,
            reason: 'merge-failed',
            message: merge.stderr || merge.stdout || 'Fast-forward update failed.',
            backup,
            worktree,
        };
    }
    // Fast-forwarding can replace tracked legacy state files such as
    // limebot.json. Restore the snapshot so old installs keep their settings;
    // state-only changes are expected to remain visible to Git afterward.
    restoreStateBackup({ rootDir, stateDir, backupDir: backup.backupDir, fsImpl });
    const newHead = (await runGit(['rev-parse', 'HEAD'])).stdout.toLowerCase();
    const record = {
        updatedAt: now.toISOString(),
        branchName: snapshot.branchName,
        previousHead: snapshot.head,
        newHead,
        backupDir: backup.backupDir,
    };
    writeRecord(rootDir, record, fsImpl);
    return { ok: true, updated: true, reason: 'updated', ahead, behind, backup, record, worktree };
}

export async function rollbackUpdate({ runGit, rootDir, stateDir, fsImpl = fs }) {
    const record = readRecord(rootDir, fsImpl);
    if (!record?.previousHead || !record?.newHead) {
        return { ok: false, reason: 'no-record', message: 'No guarded update is available to roll back.' };
    }
    const worktree = await inspectWorktree({ runGit });
    if (!worktree.available || worktree.codeDirty) {
        return { ok: false, reason: 'dirty-worktree', message: 'Rollback requires no tracked source edits.', worktree };
    }
    const snapshot = await readBranchSnapshot(runGit);
    if (!snapshot.ok || snapshot.head !== record.newHead) {
        return { ok: false, reason: 'head-mismatch', message: 'HEAD changed after the update; refusing to overwrite newer work.' };
    }
    const currentStateBackup = createStateBackup({ rootDir, stateDir, fsImpl, label: 'rollback' });
    const result = await runGit(['reset', '--hard', record.previousHead], 120000);
    if (result.code !== 0) {
        return { ok: false, reason: 'rollback-failed', message: result.stderr || 'Git rollback failed.' };
    }
    restoreStateBackup({ rootDir, stateDir, backupDir: currentStateBackup.backupDir, fsImpl });
    writeRecord(rootDir, { ...record, rolledBackAt: new Date().toISOString() }, fsImpl);
    return { ok: true, reason: 'rolled-back', record };
}
