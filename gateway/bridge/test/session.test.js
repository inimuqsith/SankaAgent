import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, describe, test } from 'node:test';
import { hashId } from '../src/logger.js';
import {
  assertOutsideRepo,
  enforcePerms,
  prepareSessionDir,
  SESSION_DIR_MODE,
  SESSION_FILE_MODE,
} from '../src/session.js';
import { createFakeLogger } from './helpers.js';

const REPO_ROOT = path.resolve(import.meta.dirname, '..', '..', '..');
const tempDirs = [];

function makeTempDir(prefix = 'sanka-session-') {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  tempDirs.push(dir);
  return dir;
}

after(() => {
  for (const dir of tempDirs) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

function modeOf(target) {
  return fs.statSync(target).mode & 0o777;
}

describe('assertOutsideRepo', () => {
  test('accepts a directory outside the repository', () => {
    assert.doesNotThrow(() => assertOutsideRepo(makeTempDir(), REPO_ROOT));
  });

  test('rejects the repository root itself', () => {
    assert.throws(() => assertOutsideRepo(REPO_ROOT, REPO_ROOT), /move/i);
  });

  test('rejects ./sessions from inside the repository', () => {
    const previous = process.cwd();
    process.chdir(REPO_ROOT);
    try {
      assert.throws(() => assertOutsideRepo('./sessions', REPO_ROOT), /move/i);
    } finally {
      process.chdir(previous);
    }
  });

  test('rejects gateway/bridge/session from inside the repository', () => {
    const previous = process.cwd();
    process.chdir(REPO_ROOT);
    try {
      assert.throws(() => assertOutsideRepo('gateway/bridge/session', REPO_ROOT), /move/i);
    } finally {
      process.chdir(previous);
    }
  });

  test('rejects nested paths inside the repository', () => {
    assert.throws(
      () => assertOutsideRepo(path.join(REPO_ROOT, 'gateway', 'bridge', 'session'), REPO_ROOT),
      /move/i,
    );
  });

  test('does not reject a sibling directory sharing the repo name prefix', () => {
    const sibling = `${REPO_ROOT}-backup`;
    assert.doesNotThrow(() => assertOutsideRepo(sibling, REPO_ROOT));
  });
});

describe('prepareSessionDir', () => {
  test('creates a new directory as 0700 and returns frozen metadata', () => {
    const parent = makeTempDir();
    const dir = path.join(parent, 'sessions');

    const metadata = prepareSessionDir(dir);

    assert.equal(modeOf(dir), SESSION_DIR_MODE);
    assert.equal(metadata.created, true);
    assert.equal(metadata.dirHash, hashId(path.resolve(dir), 12));
    assert.equal(metadata.dirPermFixed, false);
    assert.equal(metadata.filesScanned, 0);
    assert.equal(metadata.filesFixed, 0);
    assert.ok(Object.isFrozen(metadata));
  });

  test('creates intermediate directories with restrictive modes', () => {
    const parent = makeTempDir();
    const dir = path.join(parent, 'nested', 'deeper', 'sessions');

    prepareSessionDir(dir);

    assert.equal(modeOf(dir), SESSION_DIR_MODE);
    assert.equal(modeOf(path.join(parent, 'nested')), SESSION_DIR_MODE);
  });

  test('fixes a loose 0755 directory to 0700 and logs a hash only', () => {
    const dir = makeTempDir();
    fs.chmodSync(dir, 0o755);
    const logger = createFakeLogger();

    const metadata = prepareSessionDir(dir, { logger });

    assert.equal(modeOf(dir), SESSION_DIR_MODE);
    assert.equal(metadata.created, false);
    assert.equal(metadata.dirPermFixed, true);

    const fixed = logger.records.find((record) => record.event === 'session_perms_fixed');
    assert.ok(fixed, 'session_perms_fixed was not logged');
    assert.equal(fixed.level, 'warn');
    assert.equal(fixed.session_hash, hashId(path.resolve(dir), 12));
    assert.ok(!JSON.stringify(fixed).includes(dir), 'log record leaked the directory path');
  });

  test('fixes a 0644 JSON file to 0600', () => {
    const dir = makeTempDir();
    const creds = path.join(dir, 'creds.json');
    fs.writeFileSync(creds, '{"me":{}}\n', { mode: 0o644 });
    fs.chmodSync(creds, 0o644);

    const metadata = prepareSessionDir(dir);

    assert.equal(modeOf(creds), SESSION_FILE_MODE);
    assert.equal(metadata.filesScanned, 1);
    assert.equal(metadata.filesFixed, 1);
  });

  test('leaves a strict 0600 JSON file untouched', () => {
    const dir = makeTempDir();
    const creds = path.join(dir, 'creds.json');
    fs.writeFileSync(creds, '{}', { mode: 0o600 });
    fs.chmodSync(creds, 0o600);
    const before = fs.statSync(creds).mtimeMs;

    const metadata = prepareSessionDir(dir);

    assert.equal(modeOf(creds), SESSION_FILE_MODE);
    assert.equal(metadata.filesFixed, 0);
    assert.equal(fs.statSync(creds).mtimeMs, before);
  });

  test('never parses session file contents', () => {
    const dir = makeTempDir();
    fs.writeFileSync(path.join(dir, 'creds.json'), 'not-json-at-all\x00', { mode: 0o644 });
    fs.chmodSync(path.join(dir, 'creds.json'), 0o644);

    assert.doesNotThrow(() => prepareSessionDir(dir));
  });

  test('ignores non-JSON files and directories when fixing modes', () => {
    const dir = makeTempDir();
    const nested = path.join(dir, 'subdir');
    fs.mkdirSync(nested, { mode: 0o700 });
    fs.writeFileSync(path.join(dir, 'notes.txt'), 'text', { mode: 0o644 });
    fs.chmodSync(path.join(dir, 'notes.txt'), 0o644);

    const metadata = prepareSessionDir(dir);

    assert.equal(metadata.filesScanned, 0);
    assert.equal(metadata.filesFixed, 0);
    assert.equal(modeOf(path.join(dir, 'notes.txt')), 0o644);
  });

  test('rejects relative paths', () => {
    assert.throws(() => prepareSessionDir('./sessions'), /absolute/i);
  });

  test('rejects a regular file in place of the directory', () => {
    const dir = makeTempDir();
    const file = path.join(dir, 'not-a-dir');
    fs.writeFileSync(file, 'x');

    assert.throws(() => prepareSessionDir(file), /not a directory/i);
  });
});

describe('enforcePerms', () => {
  test('rejects a missing directory', () => {
    const dir = path.join(makeTempDir(), 'missing');

    assert.throws(() => enforcePerms(dir), /does not exist/i);
  });

  test('rejects a symlinked directory', () => {
    const target = makeTempDir();
    const linkParent = makeTempDir();
    const link = path.join(linkParent, 'link');
    fs.symlinkSync(target, link, 'dir');

    assert.throws(() => enforcePerms(link), /real directory/i);
  });

  test('logs a file-permission fix with a hash and no filename', () => {
    const dir = makeTempDir();
    const creds = path.join(dir, 'app-state-sync-key-AAAA.json');
    fs.writeFileSync(creds, '{}', { mode: 0o644 });
    fs.chmodSync(creds, 0o644);
    const logger = createFakeLogger();

    enforcePerms(dir, { logger });

    const fixed = logger.records.find(
      (record) => record.event === 'session_file_perms_fixed',
    );
    assert.ok(fixed, 'session_file_perms_fixed was not logged');
    assert.equal(fixed.files_fixed, 1);
    assert.ok(!JSON.stringify(fixed).includes('app-state-sync-key-AAAA'));
  });
});
