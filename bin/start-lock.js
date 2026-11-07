import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export function isProcessAlive(pid) {
    if (!Number.isSafeInteger(pid) || pid < 1) return false;
    try {
        process.kill(pid, 0);
        return true;
    } catch (error) {
        return error?.code === 'EPERM';
    }
}

export function readStartLock(lockPath, expectedRootDir = null) {
    try {
        const lock = JSON.parse(fs.readFileSync(lockPath, 'utf8'));
        if (
            !Number.isSafeInteger(lock.pid) || lock.pid < 1 ||
            typeof lock.token !== 'string' || typeof lock.rootDir !== 'string'
        ) {
            return null;
        }
        lock.rootDir = path.resolve(lock.rootDir);
        if (expectedRootDir && lock.rootDir !== path.resolve(expectedRootDir)) return null;
        return lock;
    } catch {
        return null;
    }
}

export function acquireStartLock({ lockPath, rootDir, pid = process.pid, isPidAlive = isProcessAlive }) {
    fs.mkdirSync(path.dirname(lockPath), { recursive: true });
    const token = crypto.randomUUID();
    const lock = {
        pid,
        rootDir: path.resolve(rootDir),
        startedAt: new Date().toISOString(),
        token,
    };

    for (let attempt = 0; attempt < 2; attempt++) {
        let fd;
        try {
            fd = fs.openSync(lockPath, 'wx', 0o600);
            fs.writeFileSync(fd, JSON.stringify(lock));
            fs.fsyncSync(fd);
            fs.closeSync(fd);
            fd = undefined;
            return {
                acquired: true,
                ownerPid: pid,
                release() {
                    const current = readStartLock(lockPath, rootDir);
                    if (current?.token !== token) return false;
                    try {
                        fs.unlinkSync(lockPath);
                        return true;
                    } catch (error) {
                        if (error?.code === 'ENOENT') return false;
                        throw error;
                    }
                },
            };
        } catch (error) {
            if (fd !== undefined) {
                try { fs.closeSync(fd); } catch { /* already closed */ }
                try { fs.unlinkSync(lockPath); } catch { /* another process replaced it */ }
            }
            if (error?.code !== 'EEXIST') throw error;
        }

        const existing = readStartLock(lockPath, rootDir);
        if (!existing) {
            return { acquired: false, ownerPid: null, reason: 'starting' };
        }
        if (isPidAlive(existing.pid)) {
            return { acquired: false, ownerPid: existing.pid, reason: 'running' };
        }

        // Re-read before removing a dead owner's lock so we never remove a newer one.
        const current = readStartLock(lockPath, rootDir);
        if (current?.token !== existing.token) {
            return {
                acquired: false,
                ownerPid: current?.pid ?? null,
                reason: current ? 'running' : 'starting',
            };
        }
        try {
            fs.unlinkSync(lockPath);
        } catch (error) {
            if (error?.code !== 'ENOENT') throw error;
        }
    }

    const current = readStartLock(lockPath, rootDir);
    return {
        acquired: false,
        ownerPid: current?.pid ?? null,
        reason: current ? 'running' : 'starting',
    };
}
