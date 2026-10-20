// Environment-only configuration for the Sanka WhatsApp bridge.
//
// Node deliberately does not read config/sanka.yaml or dotenv files: the
// supervisor loads config/secrets.env and exports the environment, and this
// module is the single validation surface. Every failure names the env var.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { assertOutsideRepo } from './session.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const DEFAULT_INBOUND_URL = 'http://127.0.0.1:8099/v1/inbound';
export const DEFAULT_PORT = 3000;
export const DEFAULT_OUTBOUND_PER_MINUTE = 20;
export const DEFAULT_LOG_LEVEL = 'info';
export const DEFAULT_SESSION_DIR = path.join(
  os.homedir(),
  '.local',
  'share',
  'sanka',
  'whatsapp-session',
);

const LOG_LEVELS = new Set(['trace', 'debug', 'info', 'warn', 'error', 'fatal', 'silent']);
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost']);

/**
 * Resolve a path to its real location without requiring the leaf to exist.
 * Existing ancestors are resolved with realpath so a symlinked parent cannot
 * smuggle a repository path past the outside-repo check.
 */
function resolveReal(target) {
  const absolute = path.resolve(target);
  try {
    return fs.realpathSync.native(absolute);
  } catch (error) {
    if (error?.code !== 'ENOENT' && error?.code !== 'ENOTDIR') throw error;
    const parent = path.dirname(absolute);
    if (parent === absolute) return absolute;
    return path.join(resolveReal(parent), path.basename(absolute));
  }
}

// Repository root is derived from this file (gateway/bridge/src/config.js),
// never from process.cwd(), so `npm start` works from any directory.
export const REPO_ROOT = resolveReal(path.resolve(HERE, '..', '..', '..'));

export class ConfigError extends Error {
  /**
   * @param {string} env name of the offending environment variable
   * @param {string} message human readable explanation (never contains secrets)
   */
  constructor(env, message) {
    super(message);
    this.name = 'ConfigError';
    this.env = env;
  }
}

function readString(env, name, fallback) {
  const raw = env[name];
  if (raw === undefined || raw === null || raw === '') return fallback;
  if (typeof raw !== 'string') {
    throw new ConfigError(name, `${name} must be a string`);
  }
  return raw;
}

function readInt(env, name, fallback, min, max) {
  const raw = env[name];
  if (raw === undefined || raw === null || raw === '') return fallback;
  if (typeof raw !== 'string' || !/^\d+$/.test(raw)) {
    throw new ConfigError(name, `${name} must be an integer between ${min} and ${max}`);
  }
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < min || value > max) {
    throw new ConfigError(name, `${name} must be between ${min} and ${max}`);
  }
  return value;
}

function readBoolean(env, name, fallback) {
  const raw = env[name];
  if (raw === undefined || raw === null || raw === '') return fallback;
  if (raw === '0') return false;
  if (raw === '1') return true;
  throw new ConfigError(name, `${name} must be "0" or "1"`);
}

function validateInboundUrl(raw) {
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new ConfigError('SANKA_INBOUND_URL', 'SANKA_INBOUND_URL must be a valid URL');
  }

  if (url.protocol !== 'http:') {
    throw new ConfigError(
      'SANKA_INBOUND_URL',
      'SANKA_INBOUND_URL must use http:// (loopback only)',
    );
  }
  if (!LOOPBACK_HOSTS.has(url.hostname)) {
    throw new ConfigError(
      'SANKA_INBOUND_URL',
      'SANKA_INBOUND_URL must point at the loopback host ' +
        '(http://127.0.0.1 or http://localhost)',
    );
  }
  if (url.username !== '' || url.password !== '') {
    throw new ConfigError('SANKA_INBOUND_URL', 'SANKA_INBOUND_URL must not embed credentials');
  }
  return raw;
}

function expandHome(target) {
  if (target === '~') return os.homedir();
  if (target.startsWith(`~${path.sep}`)) {
    return path.join(os.homedir(), target.slice(2));
  }
  return target;
}

function resolveSessionDir(raw) {
  const expanded = expandHome(raw);
  if (!path.isAbsolute(expanded)) {
    throw new ConfigError(
      'SANKA_BRIDGE_SESSION_DIR',
      'SANKA_BRIDGE_SESSION_DIR must be an absolute path outside the repository',
    );
  }

  let resolved;
  try {
    resolved = resolveReal(expanded);
  } catch (error) {
    throw new ConfigError(
      'SANKA_BRIDGE_SESSION_DIR',
      `SANKA_BRIDGE_SESSION_DIR could not be resolved (${error?.code ?? 'error'})`,
    );
  }

  try {
    assertOutsideRepo(resolved, REPO_ROOT, 'SANKA_BRIDGE_SESSION_DIR');
  } catch (error) {
    throw new ConfigError('SANKA_BRIDGE_SESSION_DIR', error.message);
  }

  return resolved;
}

/**
 * Load and validate the bridge configuration.
 *
 * @param {Record<string, string | undefined>} [env] usually process.env
 * @returns {Readonly<{port: number, host: '127.0.0.1', token: string,
 *   inboundUrl: string, sessionDir: string, logLevel: string,
 *   outboundPerMinute: number, readReceipts: boolean, pairOnly: boolean,
 *   repoRoot: string}>}
 * @throws {ConfigError} when an environment value is missing or invalid
 */
export function loadConfig(env = process.env) {
  if (env === null || typeof env !== 'object') {
    throw new TypeError('loadConfig(env) requires an environment object');
  }

  const token = readString(env, 'SANKA_GATEWAY_TOKEN', undefined);
  if (token === undefined) {
    throw new ConfigError(
      'SANKA_GATEWAY_TOKEN',
      'SANKA_GATEWAY_TOKEN is required (at least 16 characters)',
    );
  }
  if (token.length < 16) {
    throw new ConfigError(
      'SANKA_GATEWAY_TOKEN',
      'SANKA_GATEWAY_TOKEN must be at least 16 characters',
    );
  }

  const inboundUrl = validateInboundUrl(
    readString(env, 'SANKA_INBOUND_URL', DEFAULT_INBOUND_URL),
  );

  const port = readInt(env, 'SANKA_BRIDGE_PORT', DEFAULT_PORT, 1, 65535);
  const sessionDir = resolveSessionDir(
    readString(env, 'SANKA_BRIDGE_SESSION_DIR', DEFAULT_SESSION_DIR),
  );

  const logLevel = readString(env, 'SANKA_BRIDGE_LOG_LEVEL', DEFAULT_LOG_LEVEL);
  if (!LOG_LEVELS.has(logLevel)) {
    throw new ConfigError(
      'SANKA_BRIDGE_LOG_LEVEL',
      `SANKA_BRIDGE_LOG_LEVEL must be one of ${[...LOG_LEVELS].join(', ')}`,
    );
  }

  const outboundPerMinute = readInt(
    env,
    'SANKA_BRIDGE_OUTBOUND_PER_MINUTE',
    DEFAULT_OUTBOUND_PER_MINUTE,
    1,
    60,
  );
  const readReceipts = readBoolean(env, 'SANKA_BRIDGE_READ_RECEIPTS', false);
  const pairOnly = readBoolean(env, 'SANKA_BRIDGE_PAIR_ONLY', false);

  return Object.freeze({
    port,
    host: '127.0.0.1',
    token,
    inboundUrl,
    sessionDir,
    logLevel,
    outboundPerMinute,
    readReceipts,
    pairOnly,
    repoRoot: REPO_ROOT,
  });
}
