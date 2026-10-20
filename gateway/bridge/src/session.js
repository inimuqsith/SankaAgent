// File-system safety for the Baileys multi-file auth state.
//
// The session directory is equivalent to the password of the linked WhatsApp
// device: copying it is enough to send messages as Sanka. Therefore:
//   * the process umask is tightened as soon as this module is loaded, before
//     any Baileys write can happen;
//   * directories are 0700, JSON files are 0600;
//   * file contents are never read here (no logging, no health checks);
//   * a directory inside the repository is rejected outright.
import fs from 'node:fs';
import path from 'node:path';
import { hashId } from './logger.js';

// Must run before Baileys touches disk. main.js also sets it as its first
// statement, which is harmless and keeps the entrypoint obvious.
process.umask(0o077);

export const SESSION_DIR_MODE = 0o700;
export const SESSION_FILE_MODE = 0o600;

const HASH_LENGTH = 12;

function hashDir(dir) {
  return hashId(path.resolve(dir), HASH_LENGTH);
}

/**
 * Reject a session directory that is the repository root or lives inside it.
 * Callers should pass a realpath-resolved directory so symlinks cannot smuggle
 * the repository back in.
 *
 * @param {string} dir
 * @param {string} repoRoot
 * @param {string} [label] used verbatim in the error, e.g. the env var name
 * @returns {void}
 */
export function assertOutsideRepo(dir, repoRoot, label = 'WhatsApp session directory') {
  const resolvedDir = path.resolve(dir);
  const resolvedRoot = path.resolve(repoRoot);

  if (resolvedDir === resolvedRoot || resolvedDir.startsWith(resolvedRoot + path.sep)) {
    throw new Error(
      `${label} must not be the repository root or inside it; ` +
        'move the WhatsApp session directory outside the repository ' +
        '(for example under ~/.local/share/sanka) and retry',
    );
  }
}

/**
 * Fix permissions on an existing session directory and its JSON files.
 * Contents are never read. Metadata only contains hashes and counts so it is
 * safe to log.
 *
 * @param {string} dir
 * @param {{logger?: {warn?: Function}}} [options]
 * @returns {Readonly<{dirHash: string, dirPermFixed: boolean,
 *   filesScanned: number, filesFixed: number}>}
 */
export function enforcePerms(dir, options = {}) {
  const { logger } = options;
  const resolved = path.resolve(dir);
  const dirHash = hashDir(resolved);

  let stats;
  try {
    stats = fs.lstatSync(resolved);
  } catch (error) {
    if (error?.code === 'ENOENT') {
      throw new Error('session directory does not exist');
    }
    throw new Error(`session directory could not be inspected (${error?.code ?? 'error'})`);
  }

  if (!stats.isDirectory() || stats.isSymbolicLink()) {
    throw new Error('session directory must be a real directory, not a file or symlink');
  }

  let dirPermFixed = false;
  const dirMode = stats.mode & 0o777;
  if ((dirMode & 0o077) !== 0) {
    try {
      fs.chmodSync(resolved, SESSION_DIR_MODE);
    } catch (error) {
      throw new Error(`session directory permissions could not be fixed (${error?.code ?? 'error'})`);
    }
    dirPermFixed = true;
    logger?.warn?.(
      { event: 'session_perms_fixed', session_hash: dirHash },
      'session_perms_fixed',
    );
  }

  let entries;
  try {
    entries = fs.readdirSync(resolved, { withFileTypes: true });
  } catch (error) {
    throw new Error(`session directory could not be listed (${error?.code ?? 'error'})`);
  }

  let filesScanned = 0;
  let filesFixed = 0;

  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith('.json')) continue;
    filesScanned += 1;

    const filePath = path.join(resolved, entry.name);
    let fileStats;
    try {
      fileStats = fs.lstatSync(filePath);
    } catch (error) {
      throw new Error(`session file could not be inspected (${error?.code ?? 'error'})`);
    }
    if (!fileStats.isFile()) continue;

    const fileMode = fileStats.mode & 0o777;
    if ((fileMode & 0o077) !== 0) {
      try {
        fs.chmodSync(filePath, SESSION_FILE_MODE);
      } catch (error) {
        throw new Error(`session file permissions could not be fixed (${error?.code ?? 'error'})`);
      }
      filesFixed += 1;
    }
  }

  if (filesFixed > 0) {
    logger?.warn?.(
      { event: 'session_file_perms_fixed', session_hash: dirHash, files_fixed: filesFixed },
      'session_file_perms_fixed',
    );
  }

  return Object.freeze({ dirHash, dirPermFixed, filesScanned, filesFixed });
}

/**
 * Create the session directory if needed (0700) and enforce the permissions of
 * the directory and its JSON files. Safe to call on every start.
 *
 * @param {string} dir absolute path outside the repository
 * @param {{logger?: {warn?: Function}}} [options]
 * @returns {Readonly<{created: boolean, dirHash: string,
 *   dirPermFixed: boolean, filesScanned: number, filesFixed: number}>}
 */
export function prepareSessionDir(dir, options = {}) {
  const resolved = path.resolve(dir);

  if (!path.isAbsolute(dir)) {
    throw new Error('session directory must be an absolute path');
  }

  let created = false;
  try {
    const stats = fs.statSync(resolved);
    if (!stats.isDirectory()) {
      throw new Error('session path exists and is not a directory');
    }
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
    try {
      fs.mkdirSync(resolved, { recursive: true, mode: SESSION_DIR_MODE });
      // mkdir honours the umask; chmod makes 0700 unconditional.
      fs.chmodSync(resolved, SESSION_DIR_MODE);
    } catch (mkdirError) {
      throw new Error(`session directory could not be created (${mkdirError?.code ?? 'error'})`);
    }
    created = true;
  }

  const metadata = enforcePerms(resolved, options);
  return Object.freeze({ created, ...metadata });
}
