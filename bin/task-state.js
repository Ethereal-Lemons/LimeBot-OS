import fs from 'fs';
import path from 'path';

const ACTIVE_STATUSES = new Set(['queued', 'running', 'waiting', 'retrying']);
const MAX_HISTORY = 500;

function writeJsonAtomic(filePath, value) {
    const dir = path.dirname(filePath);
    fs.mkdirSync(dir, { recursive: true });
    const tmpPath = path.join(
        dir,
        `.${path.basename(filePath)}.${process.pid}.${Date.now()}.tmp`
    );
    fs.writeFileSync(tmpPath, JSON.stringify(value, null, 2), 'utf8');
    fs.renameSync(tmpPath, filePath);
}

export function cleanupStoppedTaskState({
    tasksPath,
    now = Date.now() / 1000,
    reason = 'LimeBot stopped before this task completed.',
} = {}) {
    if (!tasksPath) {
        throw new Error('tasksPath is required');
    }
    if (!fs.existsSync(tasksPath)) {
        return { cleanedTasks: 0, cleanedWorkspaces: 0, changed: false };
    }

    const raw = JSON.parse(fs.readFileSync(tasksPath, 'utf8'));
    const active = Array.isArray(raw.active) ? raw.active : [];
    const history = Array.isArray(raw.history) ? raw.history : [];
    const workspaces = Array.isArray(raw.workspaces) ? raw.workspaces : [];

    const cancelledTasks = active.map((task) => ({
        ...task,
        status: 'cancelled',
        error: task?.error || reason,
        updated_at: now,
        completed_at: now,
    }));

    let cleanedWorkspaces = 0;
    const nextWorkspaces = workspaces.map((workspace) => {
        if (!ACTIVE_STATUSES.has(workspace?.status)) {
            return workspace;
        }
        cleanedWorkspaces += 1;
        return {
            ...workspace,
            status: 'cancelled',
            error: workspace?.error || reason,
            updated_at: now,
            completed_at: now,
        };
    });

    if (cancelledTasks.length === 0 && cleanedWorkspaces === 0) {
        return { cleanedTasks: 0, cleanedWorkspaces: 0, changed: false };
    }

    const nextHistory = [...history, ...cancelledTasks].slice(-MAX_HISTORY);
    writeJsonAtomic(tasksPath, {
        ...raw,
        active: [],
        history: nextHistory,
        workspaces: nextWorkspaces,
    });

    return {
        cleanedTasks: cancelledTasks.length,
        cleanedWorkspaces,
        changed: true,
    };
}
