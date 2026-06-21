import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const cliPath = path.join(rootDir, 'bin', 'cli.js');

test('CLI help exposes the review-only diff command', () => {
  const result = spawnSync(process.execPath, [cliPath, '--help'], {
    cwd: rootDir,
    encoding: 'utf8',
  });

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /review-diff/);
});

test('review-diff writes a redacted artifact without invoking a model', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'limebot-review-'));
  const diffPath = path.join(tempDir, 'change.diff');
  const outputPath = path.join(tempDir, 'review.json');
  fs.writeFileSync(
    diffPath,
    [
      'diff --git a/a.py b/a.py',
      '--- a/a.py',
      '+++ b/a.py',
      '@@ -1 +1 @@',
      '-token = "old"',
      '+token = "ghp_abcdefghijklmnopqrstuvwxyz123456"',
      '',
    ].join('\n'),
  );

  try {
    const result = spawnSync(
      process.execPath,
      [cliPath, 'review-diff', '--diff-file', diffPath, '--output', outputPath],
      { cwd: rootDir, encoding: 'utf8', timeout: 30_000 },
    );
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    const artifact = JSON.parse(fs.readFileSync(outputPath, 'utf8'));
    assert.equal(artifact.mode, 'prompt_only');
    assert.equal(artifact.summary.files, 1);
    assert.doesNotMatch(JSON.stringify(artifact), /ghp_abcdefghijklmnopqrstuvwxyz123456/);
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});
