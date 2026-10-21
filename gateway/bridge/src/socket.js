import makeWASocket, {
  fetchLatestBaileysVersion,
  jidNormalizedUser,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys';
import qrcode from 'qrcode-terminal';

import { classifyDisconnect, nextDelay } from './backoff.js';
import { createBoundedMap } from './ids.js';
import { hashId } from './logger.js';
import { normalizeMessage } from './normalize.js';

const VERSION_TIMEOUT_MS = 10_000;
const OPEN_RESET_MS = 60_000;

function errorStatusCode(error) {
  return (
    error?.output?.statusCode ??
    error?.statusCode ??
    error?.data?.statusCode ??
    undefined
  );
}

function withTimeout(promise, timeoutMs) {
  let timer;
  return Promise.race([
    promise.finally(() => clearTimeout(timer)),
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('timeout')), timeoutMs);
      timer.unref?.();
    }),
  ]);
}

export function createSocketManager({ config, deliverer, logger, media = null }) {
  const startedAt = Date.now();
  const botJids = new Set();
  const lidCache = createBoundedMap({
    ttlMs: 60 * 60 * 1000,
    max: 2_000,
    clock: { now: () => Date.now() },
  });
  const dedupe = createBoundedMap({
    ttlMs: 10 * 60 * 1000,
    max: 2_000,
    clock: { now: () => Date.now() },
  });
  const nameCache = createBoundedMap({
    ttlMs: 24 * 60 * 60 * 1000,
    max: 2_000,
    clock: { now: () => Date.now() },
  });

  let sock = null;
  let starting = false;
  let shuttingDown = false;
  let connected = false;
  let reconnectTimer = null;
  let openResetTimer = null;
  let attempt = 0;
  let cachedVersion = null;
  let credsDegraded = false;

  function childLogger() {
    if (typeof logger.child !== 'function') {
      return logger;
    }
    // Baileys logs connection handshakes (helloMsg, key material) at info.
    // Keep our own logger level but never let the library emit those payloads.
    return logger.child({ module: 'baileys' }, { level: 'warn' });
  }

  async function resolveVersion() {
    if (cachedVersion) {
      return cachedVersion;
    }
    try {
      const { version } = await withTimeout(
        fetchLatestBaileysVersion(),
        VERSION_TIMEOUT_MS,
      );
      cachedVersion = version;
      return version;
    } catch {
      logger.warn('baileys_version_fetch_failed');
      return cachedVersion ?? undefined;
    }
  }

  function scheduleReconnect(delayMs) {
    if (shuttingDown) {
      return;
    }
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      void connect();
    }, delayMs);
    reconnectTimer.unref?.();
  }

  function handleQr(qr) {
    if (!process.stdout.isTTY) {
      logger.error('qr_requires_tty');
      process.exit(1);
    }
    qrcode.generate(qr, { small: true });
    logger.info('qr_displayed');
  }

  function refreshBotJids() {
    botJids.clear();
    const user = sock?.user;
    if (!user) {
      return;
    }
    for (const value of [user.id, user.lid]) {
      if (!value) {
        continue;
      }
      try {
        botJids.add(jidNormalizedUser(value));
      } catch {
        // Ignore malformed JIDs from the library; compare by user part later.
      }
    }
  }

  async function resolveLid(jid) {
    if (!jid?.endsWith?.('@lid')) {
      return null;
    }
    const cached = lidCache.get(jid);
    if (cached) {
      return cached;
    }
    try {
      const pn = await sock?.signalRepository?.lidMapping?.getPNForLID?.(jid);
      if (pn) {
        lidCache.set(jid, pn);
        return pn;
      }
    } catch {
      logger.debug({ jid: hashId(jid) }, 'lid_unresolved');
    }
    return null;
  }

  async function onMessagesUpsert({ messages, type }) {
    if (!Array.isArray(messages)) {
      return;
    }
    logger.debug(
      { upsert_type: type ?? 'unknown', count: messages.length },
      'messages_upsert',
    );
    if (type !== 'notify') {
      return;
    }
    for (const msg of messages) {
      try {
        const candidate =
          msg?.key?.participant ?? msg?.key?.remoteJid ?? '';
        if (candidate.endsWith('@lid')) {
          await resolveLid(candidate);
        }
        const event = normalizeMessage(msg, {
          botJids,
          resolvePn: (jid) => lidCache.get(jid) ?? null,
          resolveName: (jid) => nameCache.get(jid) ?? '',
          nowMs: () => Date.now(),
        });
        if (!event) {
          logger.debug(
            { chat_kind: msg?.key?.remoteJid?.endsWith?.('@g.us') ? 'group' : 'dm' },
            'inbound_skipped',
          );
          continue;
        }
        if (event.sender_name) {
          nameCache.set(event.sender_id, event.sender_name);
        }
        logger.debug(
          {
            chat_kind: event.is_group ? 'group' : 'dm',
            text_len: event.text.length,
            mentions: event.mentions.length,
            reply_to_bot: event.reply_to_bot,
          },
          'inbound_normalized',
        );
        if (dedupe.has(event.message_id)) {
          logger.debug(
            { msg_id: hashId(event.message_id) },
            'inbound_duplicate',
          );
          continue;
        }
        dedupe.set(event.message_id, true);
        if (media) {
          try {
            const prepared = await media.prepare(msg, event);
            if (Array.isArray(prepared?.attachments) && prepared.attachments.length > 0) {
              event.attachments = prepared.attachments;
            }
            if (Array.isArray(prepared?.errors) && prepared.errors.length > 0) {
              event.attachment_errors = prepared.errors;
            }
          } catch {
            logger.warn({ code: 'prepare_failed' }, 'attachment_prepare_failed');
          }
        }
        deliverer.enqueue(event);
      } catch (error) {
        logger.warn(
          { error: error?.name ?? 'error' },
          'inbound_normalize_failed',
        );
      }
    }
  }

  async function connect() {
    if (shuttingDown || starting) {
      return;
    }
    starting = true;
    try {
      const { state, saveCreds } = await useMultiFileAuthState(
        config.sessionDir,
      );
      const version = await resolveVersion();
      const options = {
        auth: state,
        logger: childLogger(),
        printQRInTerminal: false,
        syncFullHistory: false,
        markOnlineOnConnect: false,
        browser: ['Sanka', 'Chrome', '120.0'],
        getMessage: async () => ({ conversation: '' }),
      };
      if (version) {
        options.version = version;
      }
      sock = makeWASocket(options);
      sock.ev.on('creds.update', async () => {
        try {
          await saveCreds();
        } catch {
          credsDegraded = true;
          logger.error('creds_save_failed');
        }
      });
      sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        if (qr) {
          handleQr(qr);
        }
        if (connection === 'open') {
          connected = true;
          attempt = 0;
          refreshBotJids();
          logger.info('connection_open');
          if (openResetTimer) {
            clearTimeout(openResetTimer);
          }
          openResetTimer = setTimeout(() => {
            attempt = 0;
            openResetTimer = null;
          }, OPEN_RESET_MS);
          openResetTimer.unref?.();
          if (config.pairOnly) {
            logger.info('pairing_complete');
            void stop().finally(() => process.exit(0));
          }
        }
        if (connection === 'close') {
          connected = false;
          if (openResetTimer) {
            clearTimeout(openResetTimer);
            openResetTimer = null;
          }
          if (shuttingDown) {
            return;
          }
          const statusCode = errorStatusCode(lastDisconnect?.error);
          const { action, delayMs: fixedDelay } =
            classifyDisconnect(statusCode);
          if (action === 're-pair') {
            logger.error('logged_out');
            process.exit(2);
          }
          const delayMs =
            action === 'restart-now'
              ? (fixedDelay ?? 1_000)
              : nextDelay(attempt, { rand: Math.random });
          if (action === 'retry') {
            attempt += 1;
          }
          logger.warn(
            {
              reason: action,
              attempt,
              delay_ms: delayMs,
            },
            'connection_closed',
          );
          scheduleReconnect(delayMs);
        }
      });
      sock.ev.on('messages.upsert', onMessagesUpsert);
    } catch (error) {
      logger.error(
        { error: error?.name ?? 'error' },
        'socket_start_failed',
      );
      scheduleReconnect(nextDelay(attempt, { rand: Math.random }));
      attempt += 1;
    } finally {
      starting = false;
    }
  }

  async function start() {
    if (shuttingDown || starting || sock) {
      return;
    }
    await connect();
  }

  async function stop() {
    shuttingDown = true;
    connected = false;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (openResetTimer) {
      clearTimeout(openResetTimer);
      openResetTimer = null;
    }
    const current = sock;
    sock = null;
    if (current) {
      try {
        current.ev.removeAllListeners('connection.update');
        current.ev.removeAllListeners('messages.upsert');
        current.ev.removeAllListeners('creds.update');
      } catch {
        // Listener removal is best-effort during shutdown.
      }
      try {
        current.end(new Error('shutdown'));
      } catch {
        // The socket may already be closed.
      }
    }
  }

  function health() {
    return {
      connected,
      uptime_s: Math.floor((Date.now() - startedAt) / 1000),
      degraded: credsDegraded,
    };
  }

  async function sendText(chatId, text) {
    if (!sock) {
      throw new Error('not_connected');
    }
    const sent = await sock.sendMessage(chatId, { text });
    return { id: sent?.key?.id ?? null };
  }

  async function presence(chatId, state) {
    if (!sock) {
      throw new Error('not_connected');
    }
    await sock.sendPresenceUpdate(state, chatId);
  }

  async function readMessages(keys) {
    if (!sock) {
      throw new Error('not_connected');
    }
    await sock.readMessages(keys);
  }

  return {
    start,
    stop,
    health,
    sendText,
    presence,
    readMessages,
  };
}
