import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import os from 'os';
import path from 'path';

import { cleanupStoppedTaskState } from '../bin/task-state.js';

function fixture() {
    return fs.mkdtempSync(path.join(os.tmpdir(), 'limebot-task-state-'));
}

function readJson(filePath) {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

test('stop cleanup cancels active tasks and moves them to history', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const tasksPath = path.join(dir, 'tasks.json');
    fs.writeFileSync(
        tasksPath,
        JSON.stringify({
            active: [
                {
                    task_id: 'task-1',
                    status: 'running',
                    summary: 'web: stuck setup interview',
                    created_at: 10,
                    updated_at: 11,
                    completed_at: 0,
                },
            ],
            history: [{ task_id: 'old-task', status: 'completed' }],
            workspaces: [],
        })
    );

    const result = cleanupStoppedTaskState({ tasksPath, now: 123 });
    assert.deepEqual(result, {
        cleanedTasks: 1,
        cleanedWorkspaces: 0,
        changed: true,
    });

    const saved = readJson(tasksPath);
    assert.deepEqual(saved.active, []);
    assert.equal(saved.history.at(-1).task_id, 'task-1');
    assert.equal(saved.history.at(-1).status, 'cancelled');
    assert.equal(saved.history.at(-1).completed_at, 123);
});

test('stop cleanup cancels active workspaces without changing completed ones', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const tasksPath = path.join(dir, 'tasks.json');
    fs.writeFileSync(
        tasksPath,
        JSON.stringify({
            active: [],
            history: [],
            workspaces: [
                { workspace_id: 'workspace-1', status: 'running', completed_at: 0 },
                { workspace_id: 'workspace-2', status: 'completed', completed_at: 22 },
            ],
        })
    );

    const result = cleanupStoppedTaskState({ tasksPath, now: 456 });
    assert.equal(result.cleanedWorkspaces, 1);

    const saved = readJson(tasksPath);
    assert.equal(saved.workspaces[0].status, 'cancelled');
    assert.equal(saved.workspaces[0].completed_at, 456);
    assert.equal(saved.workspaces[1].status, 'completed');
    assert.equal(saved.workspaces[1].completed_at, 22);
});

test('stop cleanup ignores missing task state', () => {
    const result = cleanupStoppedTaskState({
        tasksPath: path.join(os.tmpdir(), `missing-${Date.now()}`, 'tasks.json'),
    });
    assert.deepEqual(result, {
        cleanedTasks: 0,
        cleanedWorkspaces: 0,
        changed: false,
    });
});

test('stop cleanup preserves bounded task history', (t) => {
    const dir = fixture();
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const tasksPath = path.join(dir, 'tasks.json');
    fs.writeFileSync(
        tasksPath,
        JSON.stringify({
            active: [{ task_id: 'new-task', status: 'running' }],
            history: Array.from({ length: 500 }, (_, index) => ({
                task_id: `old-${index}`,
                status: 'completed',
            })),
            workspaces: [],
        })
    );

    cleanupStoppedTaskState({ tasksPath, now: 789 });

    const saved = readJson(tasksPath);
    assert.equal(saved.history.length, 500);
    assert.equal(saved.history[0].task_id, 'old-1');
    assert.equal(saved.history.at(-1).task_id, 'new-task');
});
