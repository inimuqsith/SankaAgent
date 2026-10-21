import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, describe, test } from 'node:test';
import {
  ConfigError,
  DEFAULT_INBOUND_URL,
  DEFAULT_SESSION_DIR,
  loadConfig,
  REPO_ROOT,
} from '../src/config.js';

const TOKEN = '0123456789abcdef'; // exactly 16 characters
const tempDirs = [];

function makeTempDir(prefix = 'sanka-config-') {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  tempDirs.push(dir);
  return dir;
}

after(() => {
  for (const dir of tempDirs) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

function baseEnv(overrides = {}) {
  return {
    SANKA_GATEWAY_TOKEN: TOKEN,
    SANKA_BRIDGE_SESSION_DIR: makeTempDir(),
    ...overrides,
  };
}

function captureError(fn) {
  try {
    fn();
  } catch (error) {
    return error;
  }
  return undefined;
}

function assertConfigError(env, pattern) {
  const error = captureError(() => loadConfig(env));
  assert.ok(error, 'expected loadConfig to throw');
  assert.match(error.message, pattern);
  assert.equal(error.name, 'ConfigError');
  assert.match(error.env, /^SANKA_/);
  return error;
}

describe('loadConfig defaults', () => {
  test('applies the documented defaults with only the token and a temp session dir', () => {
    const sessionDir = makeTempDir();
    const config = loadConfig({
      SANKA_GATEWAY_TOKEN: TOKEN,
      SANKA_BRIDGE_SESSION_DIR: sessionDir,
    });

    assert.equal(config.port, 3000);
    assert.equal(config.host, '127.0.0.1');
    assert.equal(config.token, TOKEN);
    assert.equal(config.inboundUrl, DEFAULT_INBOUND_URL);
    assert.equal(config.attachmentUrl, 'http://127.0.0.1:8099/v1/attachments');
    assert.equal(config.attachmentMaxBytes, 25_000_000);
    assert.equal(config.sessionDir, fs.realpathSync.native(sessionDir));
    assert.equal(config.logLevel, 'info');
    assert.equal(config.outboundPerMinute, 20);
    assert.equal(config.readReceipts, false);
    assert.equal(config.pairOnly, false);
    assert.equal(config.repoRoot, REPO_ROOT);
  });

  test('default session directory is absolute and outside the repository', () => {
    const config = loadConfig({ SANKA_GATEWAY_TOKEN: TOKEN });

    assert.equal(config.sessionDir, DEFAULT_SESSION_DIR);
    assert.ok(path.isAbsolute(config.sessionDir));
    assert.ok(!config.sessionDir.startsWith(config.repoRoot + path.sep));
  });

  test('repoRoot is derived from the module, not process.cwd()', () => {
    const config = loadConfig(baseEnv());
    assert.equal(config.repoRoot, path.resolve(import.meta.dirname, '..', '..', '..'));
  });

  test('returns a frozen object', () => {
    const config = loadConfig(baseEnv());

    assert.ok(Object.isFrozen(config));
    assert.throws(() => {
      config.port = 9999;
    }, TypeError);
  });
});

describe('loadConfig full environment', () => {
  test('parses every override', () => {
    const sessionDir = makeTempDir();
    const config = loadConfig({
      SANKA_GATEWAY_TOKEN: 'token-abcdefghijkl',
      SANKA_INBOUND_URL: 'http://localhost:8099/v1/inbound',
      SANKA_BRIDGE_PORT: '4111',
      SANKA_BRIDGE_SESSION_DIR: sessionDir,
      SANKA_BRIDGE_OUTBOUND_PER_MINUTE: '30',
      SANKA_BRIDGE_READ_RECEIPTS: '1',
      SANKA_BRIDGE_LOG_LEVEL: 'debug',
      SANKA_BRIDGE_PAIR_ONLY: '1',
      SANKA_ATTACHMENT_MAX_BYTES: '123456',
    });

    assert.equal(config.port, 4111);
    assert.equal(config.token, 'token-abcdefghijkl');
    assert.equal(config.inboundUrl, 'http://localhost:8099/v1/inbound');
    assert.equal(config.attachmentUrl, 'http://localhost:8099/v1/attachments');
    assert.equal(config.attachmentMaxBytes, 123456);
    assert.equal(config.sessionDir, fs.realpathSync.native(sessionDir));
    assert.equal(config.outboundPerMinute, 30);
    assert.equal(config.readReceipts, true);
    assert.equal(config.logLevel, 'debug');
    assert.equal(config.pairOnly, true);
  });

  test('resolves a symlinked session directory to its real path', () => {
    const target = makeTempDir();
    const linkParent = makeTempDir();
    const link = path.join(linkParent, 'session-link');
    fs.symlinkSync(target, link, 'dir');

    const config = loadConfig({
      SANKA_GATEWAY_TOKEN: TOKEN,
      SANKA_BRIDGE_SESSION_DIR: link,
    });

    assert.equal(config.sessionDir, fs.realpathSync.native(target));
  });

  test('normalizes ~ at the start of the session path', () => {
    const config = loadConfig({
      SANKA_GATEWAY_TOKEN: TOKEN,
      SANKA_BRIDGE_SESSION_DIR: '~/.local/share/sanka/test-whatsapp-session',
    });

    assert.equal(
      config.sessionDir,
      path.join(os.homedir(), '.local', 'share', 'sanka', 'test-whatsapp-session'),
    );
  });
});

describe('loadConfig token validation', () => {
  test('rejects a missing token', () => {
    assertConfigError(
      { SANKA_BRIDGE_SESSION_DIR: makeTempDir() },
      /SANKA_GATEWAY_TOKEN/,
    );
  });

  test('rejects an empty token', () => {
    assertConfigError(
      { SANKA_GATEWAY_TOKEN: '', SANKA_BRIDGE_SESSION_DIR: makeTempDir() },
      /SANKA_GATEWAY_TOKEN/,
    );
  });

  test('rejects a token shorter than 16 characters', () => {
    assertConfigError(
      {
        SANKA_GATEWAY_TOKEN: 'fifteen-chars!!',
        SANKA_BRIDGE_SESSION_DIR: makeTempDir(),
      },
      /SANKA_GATEWAY_TOKEN.*16/,
    );
  });

  test('does not echo the token in the error message', () => {
    const secret = 'short-secret';
    const error = captureError(() =>
      loadConfig({
        SANKA_GATEWAY_TOKEN: secret,
        SANKA_BRIDGE_SESSION_DIR: makeTempDir(),
      }),
    );
    assert.ok(error, 'expected loadConfig to throw');
    assert.match(error.message, /SANKA_GATEWAY_TOKEN/);
    assert.ok(!error.message.includes(secret));
  });
});

describe('loadConfig inbound URL validation', () => {
  test('accepts loopback 127.0.0.1 and localhost', () => {
    for (const url of [
      'http://127.0.0.1:8099/v1/inbound',
      'http://localhost:8099/v1/inbound',
    ]) {
      const config = loadConfig(baseEnv({ SANKA_INBOUND_URL: url }));
      assert.equal(config.inboundUrl, url);
    }
  });

  test('rejects a non-loopback host', () => {
    assertConfigError(
      baseEnv({ SANKA_INBOUND_URL: 'http://192.168.1.10:8099/v1/inbound' }),
      /SANKA_INBOUND_URL/,
    );
  });

  test('rejects https and non-URL values', () => {
    for (const value of ['https://127.0.0.1:8099/v1/inbound', 'not a url', 'ftp://localhost/x']) {
      assertConfigError(baseEnv({ SANKA_INBOUND_URL: value }), /SANKA_INBOUND_URL/);
    }
  });

  test('rejects embedded credentials', () => {
    assertConfigError(
      baseEnv({ SANKA_INBOUND_URL: 'http://user:pass@127.0.0.1:8099/v1/inbound' }),
      /SANKA_INBOUND_URL/,
    );
  });
});

describe('loadConfig numeric validation', () => {
  test('rejects invalid ports with the env name', () => {
    for (const value of ['0', '65536', '-1', 'abc', '3000.5']) {
      assertConfigError(baseEnv({ SANKA_BRIDGE_PORT: value }), /SANKA_BRIDGE_PORT/);
    }
  });

  test('accepts the port boundaries', () => {
    assert.equal(
      loadConfig(baseEnv({ SANKA_BRIDGE_PORT: '1' })).port,
      1,
    );
    assert.equal(
      loadConfig(baseEnv({ SANKA_BRIDGE_PORT: '65535' })).port,
      65535,
    );
  });

  test('rejects outboundPerMinute outside 1..60', () => {
    for (const value of ['0', '61', '-5', 'twenty']) {
      assertConfigError(
        baseEnv({ SANKA_BRIDGE_OUTBOUND_PER_MINUTE: value }),
        /SANKA_BRIDGE_OUTBOUND_PER_MINUTE/,
      );
    }
  });

  test('accepts outboundPerMinute boundaries', () => {
    assert.equal(
      loadConfig(baseEnv({ SANKA_BRIDGE_OUTBOUND_PER_MINUTE: '1' })).outboundPerMinute,
      1,
    );
    assert.equal(
      loadConfig(baseEnv({ SANKA_BRIDGE_OUTBOUND_PER_MINUTE: '60' })).outboundPerMinute,
      60,
    );
  });
});

describe('loadConfig booleans and log level', () => {
  test('rejects values other than 0/1 with the env name', () => {
    for (const [name, value] of [
      ['SANKA_BRIDGE_READ_RECEIPTS', 'true'],
      ['SANKA_BRIDGE_READ_RECEIPTS', '2'],
      ['SANKA_BRIDGE_PAIR_ONLY', 'yes'],
      ['SANKA_BRIDGE_PAIR_ONLY', '01'],
    ]) {
      assertConfigError(baseEnv({ [name]: value }), new RegExp(name));
    }
  });

  test('accepts 0 and 1 explicitly', () => {
    const config = loadConfig(
      baseEnv({ SANKA_BRIDGE_READ_RECEIPTS: '0', SANKA_BRIDGE_PAIR_ONLY: '0' }),
    );
    assert.equal(config.readReceipts, false);
    assert.equal(config.pairOnly, false);
  });

  test('rejects an unknown log level', () => {
    assertConfigError(
      baseEnv({ SANKA_BRIDGE_LOG_LEVEL: 'verbose' }),
      /SANKA_BRIDGE_LOG_LEVEL/,
    );
  });
});

describe('loadConfig session directory safety', () => {
  test('rejects relative session paths with the env name', () => {
    for (const value of ['./sessions', 'gateway/bridge/session', 'sessions']) {
      assertConfigError(
        baseEnv({ SANKA_BRIDGE_SESSION_DIR: value }),
        /SANKA_BRIDGE_SESSION_DIR/,
      );
    }
  });

  test('rejects the repository root', () => {
    assertConfigError(
      baseEnv({ SANKA_BRIDGE_SESSION_DIR: REPO_ROOT }),
      /SANKA_BRIDGE_SESSION_DIR.*move/s,
    );
  });

  test('rejects a directory inside the repository and tells the owner to move it', () => {
    const error = assertConfigError(
      baseEnv({ SANKA_BRIDGE_SESSION_DIR: path.join(REPO_ROOT, 'gateway', 'bridge', 'session') }),
      /SANKA_BRIDGE_SESSION_DIR/,
    );
    assert.match(error.message, /move/i);
    assert.ok(error instanceof ConfigError);
  });

  test('rejects a repository path even when spelled through a symlink', () => {
    const linkParent = makeTempDir();
    const link = path.join(linkParent, 'repo-link');
    fs.symlinkSync(REPO_ROOT, link, 'dir');

    assertConfigError(
      baseEnv({ SANKA_BRIDGE_SESSION_DIR: path.join(link, 'gateway', 'bridge', 'session') }),
      /SANKA_BRIDGE_SESSION_DIR/,
    );
  });
});
