import { readFileSync } from 'node:fs';

import { loadConfig } from './config.js';
import { createInboundDeliverer } from './deliver.js';
import { createServer } from './http.js';
import { createLogger } from './logger.js';
import { createMediaPipeline } from './media.js';
import { createOutbound } from './outbound.js';
import { assertOutsideRepo, prepareSessionDir } from './session.js';
import { createSocketManager } from './socket.js';
import { uploadAttachment } from './upload.js';

process.umask(0o077);

function readPackageVersion(path, fallback) {
  try {
    return JSON.parse(readFileSync(path, 'utf8')).version ?? fallback;
  } catch {
    return fallback;
  }
}

const bridgeVersion = readPackageVersion(
  new URL('../package.json', import.meta.url),
  '0.1.0',
);
const baileysVersion = readPackageVersion(
  new URL('../node_modules/@whiskeysockets/baileys/package.json', import.meta.url),
  'unknown',
);

async function post(url, body, { headers, timeoutMs } = {}) {
  const response = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs ?? 10_000),
  });
  const raw = await response.text();
  let parsed = null;
  if (raw) {
    try {
      parsed = JSON.parse(raw);
    } catch {
      parsed = { error: 'invalid_json' };
    }
  }
  return { status: response.status, body: parsed };
}

async function main() {
  let config;
  try {
    config = loadConfig(process.env);
  } catch (error) {
    // The message names the offending env; it never contains a secret value.
    console.error(`sanka-bridge: ${error.message}`);
    process.exit(1);
  }

  const logger = createLogger(config.logLevel);

  try {
    assertOutsideRepo(config.sessionDir, config.repoRoot);
    prepareSessionDir(config.sessionDir, { logger });
  } catch (error) {
    logger.error({ reason: error.message }, 'session_dir_invalid');
    process.exit(1);
  }

  const clock = { now: () => Date.now() };
  const deliverer = createInboundDeliverer({
    post,
    url: config.inboundUrl,
    token: config.token,
    clock,
    logger,
  });
  const media = createMediaPipeline({
    upload: (buffer, metadata) =>
      uploadAttachment({
        url: config.attachmentUrl,
        token: config.token,
        buffer,
        ...metadata,
      }),
    maxBytes: config.attachmentMaxBytes,
    logger,
  });
  const socket = createSocketManager({ config, deliverer, logger, media });
  const outbound = createOutbound({
    transport: {
      sendText: (chatId, text) => socket.sendText(chatId, text),
      presence: (chatId, state) => socket.presence(chatId, state),
      readMessages: (keys) => socket.readMessages(keys),
      isConnected: () => socket.health().connected,
    },
    clock,
    logger,
    limits: {
      outboundPerMinute: config.outboundPerMinute,
      readReceipts: config.readReceipts,
    },
  });

  const status = () => {
    const health = socket.health();
    return {
      connected: health.connected,
      queue_depth: deliverer.queueDepth(),
      delivery_paused: deliverer.deliveryPaused(),
      uptime_s: health.uptime_s,
      bridge_version: bridgeVersion,
      baileys: baileysVersion,
    };
  };

  const server = createServer({
    token: config.token,
    outbound,
    status,
    logger,
  });

  let shuttingDown = false;

  async function shutdown(exitCode) {
    if (shuttingDown) {
      process.exit(130);
    }
    shuttingDown = true;
    const force = setTimeout(() => process.exit(130), 10_000);
    force.unref();
    server.close();
    try {
      await deliverer.flush(5_000);
    } catch {
      // Flush is best-effort during shutdown.
    }
    await socket.stop();
    clearTimeout(force);
    process.exit(exitCode);
  }

  process.on('SIGINT', () => void shutdown(0));
  process.on('SIGTERM', () => void shutdown(0));
  process.on('uncaughtException', (error) => {
    logger.error({ error: error?.name ?? 'error' }, 'uncaught_exception');
    process.exit(1);
  });
  process.on('unhandledRejection', () => {
    logger.error('unhandled_rejection');
    process.exit(1);
  });

  server.on('error', (error) => {
    logger.error({ error: error?.code ?? 'error' }, 'http_listen_failed');
    process.exit(1);
  });

  server.listen(config.port, config.host, () => {
    logger.info(
      {
        port: config.port,
        mode: config.pairOnly ? 'pair' : 'daemon',
      },
      'bridge_started',
    );
    void socket.start();
  });
}

void main();
