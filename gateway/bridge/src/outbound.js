// Outbound sends to WhatsApp.
//
// The HTTP layer hands validated-ish JSON here; this module owns the real
// validation, idempotency, pacing, and transport errors. Every method returns
// `{status, body}` and never throws, so `http.js` can map the result directly.
//
// Idempotency: `<message_id>:<chunk>` keys are kept for 15 minutes. A finished
// key answers 409 (`duplicate`); an in-flight key makes the retry await the
// very same promise, so a retry can never produce a second WhatsApp message.
//
// Pacing: at least 1.200 ms between sends to one chat (independent queues) and
// a rolling 60 s window of `limits.outboundPerMinute` sends globally. When the
// computed wait would exceed 30 s the request is told to retry (429) instead
// of being held open.
//
// Logs never contain message text, JIDs, or tokens.

import { createBoundedMap } from './ids.js';

const CHAT_ID_PATTERN = /^[^@\s]+@(s\.whatsapp\.net|g\.us|lid)$/;
const MAX_TEXT_LENGTH = 4096;
const MIN_KEY_LENGTH = 8;
const MAX_KEY_LENGTH = 300;
const MIN_SPACING_MS = 1200;
const GLOBAL_WINDOW_MS = 60_000;
const MAX_TOTAL_WAIT_MS = 30_000;
const SEND_TIMEOUT_MS = 20_000;
const IDEMPOTENCY_TTL_MS = 15 * 60 * 1000;
const IDEMPOTENCY_MAX = 2000;
const NOT_CONNECTED_RETRY_MS = 1000;

const TIMED_OUT = Symbol('send_timeout');

function resolveNow(clock) {
  if (clock && typeof clock.now === 'function') return Number(clock.now());
  return Date.now();
}

function makeSleep(clock) {
  return (ms) => {
    if (!(ms > 0)) return Promise.resolve();
    if (clock && typeof clock.sleep === 'function') return clock.sleep(ms);
    return new Promise((resolve) => {
      const timer = setTimeout(resolve, ms);
      if (typeof timer.unref === 'function') timer.unref();
    });
  };
}

function withTimeout(promise, ms) {
  let timer;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => resolve(TIMED_OUT), ms);
    if (typeof timer.unref === 'function') timer.unref();
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function chatKind(chatId) {
  return typeof chatId === 'string' && chatId.endsWith('@g.us') ? 'group' : 'dm';
}

function isSocketFailure(error) {
  if (!error) return false;
  if (error.isSocketFailure === true) return true;
  const message = typeof error.message === 'string' ? error.message : '';
  return /not connected|connection (closed|lost)|socket|timed out/i.test(message);
}

function normalizeChatId(value) {
  return typeof value === 'string' && CHAT_ID_PATTERN.test(value) ? value : null;
}

function validateSend(request) {
  const chatId = normalizeChatId(request?.chat_id);
  if (!chatId) return 'chat_id';
  const text = request?.text;
  if (typeof text !== 'string' || text.trim() === '') return 'text';
  if (text.length > MAX_TEXT_LENGTH) return 'text';
  const key = request?.idempotency_key;
  if (typeof key !== 'string') return 'idempotency_key';
  if (key.length < MIN_KEY_LENGTH || key.length > MAX_KEY_LENGTH) return 'idempotency_key';
  return null;
}

function normalizeRequest(payload, second, third) {
  if (payload !== null && typeof payload === 'object' && !Array.isArray(payload)) {
    return payload;
  }
  return { chat_id: payload, text: second, idempotency_key: third };
}

/**
 * createOutbound({transport, clock, logger, limits})
 *
 * transport: { sendText(chatId, text), presence(chatId, state),
 *              readMessages(keys), isConnected() }
 * limits:    { outboundPerMinute = 20, readReceipts = false }
 */
export function createOutbound({ transport, clock, logger, limits } = {}) {
  if (transport === null || typeof transport !== 'object') {
    throw new TypeError('outbound: transport is required');
  }
  if (typeof transport.sendText !== 'function') {
    throw new TypeError('outbound: transport.sendText must be a function');
  }

  const sleep = makeSleep(clock);
  const perMinute = Number.isInteger(limits?.outboundPerMinute) && limits.outboundPerMinute > 0
    ? limits.outboundPerMinute
    : 20;
  const readReceipts = limits?.readReceipts === true;

  const idempotency = createBoundedMap({
    ttlMs: IDEMPOTENCY_TTL_MS,
    max: IDEMPOTENCY_MAX,
    clock,
  });
  const chatNextAt = new Map();
  /** Reserved/actual send times, ascending, within one global window. */
  const sendTimes = [];

  // pino takes `(fields, msg)`; the shared test logger takes `(msg, fields)`.
  const pinoStyle = Boolean(logger && typeof logger.levels === 'object' && logger.levels !== null);
  const log = (level, msg, fields) => {
    try {
      const method = logger?.[level];
      if (typeof method !== 'function') return;
      if (pinoStyle) method.call(logger, { event: msg, ...fields }, msg);
      else method.call(logger, msg, fields);
    } catch {
      // Logging must never break a send.
    }
  };

  const isConnected = () => {
    try {
      return typeof transport.isConnected === 'function' ? transport.isConnected() === true : true;
    } catch {
      return false;
    }
  };

  function releaseKey(key) {
    if (typeof idempotency.delete === 'function') idempotency.delete(key);
    else idempotency.set(key, { state: 'failed' });
  }

  function pruneSendTimes(now) {
    const cutoff = now - GLOBAL_WINDOW_MS;
    let drop = 0;
    while (drop < sendTimes.length && sendTimes[drop] <= cutoff) drop += 1;
    if (drop > 0) sendTimes.splice(0, drop);
  }

  /**
   * Reserve a send slot for `chatId`. Returns `{ok:false, retryAfterMs}` when
   * the total wait exceeds the 30 s budget.
   */
  function reserve(chatId, now) {
    let at = Math.max(now, chatNextAt.get(chatId) ?? 0);

    for (;;) {
      const inWindow = sendTimes.filter((time) => time > at - GLOBAL_WINDOW_MS && time <= at);
      if (inWindow.length < perMinute) break;
      at = inWindow[inWindow.length - perMinute] + GLOBAL_WINDOW_MS;
    }

    const waitMs = at - now;
    if (waitMs > MAX_TOTAL_WAIT_MS) {
      return { ok: false, retryAfterMs: Math.ceil(waitMs) };
    }

    pruneSendTimes(now);
    sendTimes.push(at);
    sendTimes.sort((a, b) => a - b);
    chatNextAt.set(chatId, at + MIN_SPACING_MS);

    // Keep the pacing map bounded on a long-running daemon.
    if (chatNextAt.size > 500) {
      const cutoff = now - GLOBAL_WINDOW_MS;
      for (const [id, next] of chatNextAt) {
        if (next <= cutoff) chatNextAt.delete(id);
      }
    }
    return { ok: true, waitMs, at };
  }

  async function executeSend(request, key, entry) {
    const chatId = request.chat_id;
    const base = { chat_kind: chatKind(chatId), text_len: request.text.length };

    if (!isConnected()) {
      log('warn', 'outbound_not_connected', base);
      return {
        status: 503,
        body: { error: 'not_connected', retry_after_ms: NOT_CONNECTED_RETRY_MS },
      };
    }

    const slot = reserve(chatId, resolveNow(clock));
    if (!slot.ok) {
      log('warn', 'outbound_rate_limited', {
        ...base,
        retry_after_ms: slot.retryAfterMs,
      });
      return {
        status: 429,
        body: { error: 'rate_limited', retry_after_ms: slot.retryAfterMs },
      };
    }

    if (slot.waitMs > 0) {
      log('debug', 'outbound_paced', { ...base, wait_ms: slot.waitMs });
      await sleep(slot.waitMs);
    }

    if (!isConnected()) {
      log('warn', 'outbound_not_connected', base);
      return {
        status: 503,
        body: { error: 'not_connected', retry_after_ms: NOT_CONNECTED_RETRY_MS },
      };
    }

    let sendPromise;
    try {
      sendPromise = Promise.resolve(transport.sendText(chatId, request.text));
    } catch (error) {
      if (isSocketFailure(error)) {
        log('warn', 'outbound_socket_failed', base);
        return {
          status: 503,
          body: { error: 'not_connected', retry_after_ms: NOT_CONNECTED_RETRY_MS },
        };
      }
      log('warn', 'outbound_send_failed', { ...base, reason: 'send_failed' });
      return { status: 502, body: { error: 'send_failed' } };
    }

    // A late completion still settles the key, even if the HTTP request
    // already timed out: the id is kept so a retry answers 409, not a resend.
    sendPromise.then(
      (result) => {
        if (result && typeof result.id === 'string' && result.id !== '') {
          entry.state = 'done';
          entry.messageId = result.id;
        } else {
          entry.state = 'failed';
          releaseKey(key);
        }
      },
      () => {
        entry.state = 'failed';
        releaseKey(key);
      },
    );

    let outcome;
    try {
      outcome = await withTimeout(sendPromise, SEND_TIMEOUT_MS);
    } catch (error) {
      if (isSocketFailure(error)) {
        log('warn', 'outbound_socket_failed', base);
        return {
          status: 503,
          body: { error: 'not_connected', retry_after_ms: NOT_CONNECTED_RETRY_MS },
        };
      }
      log('warn', 'outbound_send_failed', { ...base, reason: 'send_failed' });
      return { status: 502, body: { error: 'send_failed' } };
    }

    if (outcome === TIMED_OUT) {
      // Keep the key: the handlers above still update it when the send settles.
      log('warn', 'outbound_send_timeout', base);
      return { status: 504, body: { error: 'send_timeout' } };
    }

    if (!outcome || typeof outcome.id !== 'string' || outcome.id === '') {
      log('warn', 'outbound_send_failed', { ...base, reason: 'missing_id' });
      return { status: 502, body: { error: 'send_failed' } };
    }

    entry.state = 'done';
    entry.messageId = outcome.id;
    log('info', 'outbound_sent', { ...base, message_id: outcome.id });
    return { status: 200, body: { message_id: outcome.id } };
  }

  async function send(payload, text, idempotencyKey) {
    const request = normalizeRequest(payload, text, idempotencyKey);
    const invalid = validateSend(request);
    if (invalid) {
      return { status: 400, body: { error: 'invalid_request', field: invalid } };
    }

    const key = request.idempotency_key;
    const existing = idempotency.get(key);
    if (existing && existing.state === 'failed') {
      releaseKey(key);
    } else if (existing) {
      if (existing.state === 'done') {
        return { status: 409, body: { error: 'duplicate' } };
      }
      return existing.promise;
    }

    const entry = { state: 'inflight', messageId: null, promise: null };
    const run = executeSend(request, key, entry);
    entry.promise = run.then(
      (result) => {
        if (result.status === 200) {
          entry.state = 'done';
          entry.messageId = result.body.message_id;
        } else if (result.status !== 504) {
          // 504 keeps the key for the late-completion handlers.
          entry.state = 'failed';
          releaseKey(key);
        }
        return result;
      },
      (error) => {
        entry.state = 'failed';
        releaseKey(key);
        log('warn', 'outbound_send_failed', {
          chat_kind: chatKind(request.chat_id),
          text_len: request.text.length,
          reason: error?.code ?? error?.name ?? 'unexpected',
        });
        return { status: 502, body: { error: 'send_failed' } };
      },
    );
    idempotency.set(key, entry);
    return entry.promise;
  }

  async function typing(payload, stateArg) {
    const request = payload !== null && typeof payload === 'object' && !Array.isArray(payload)
      ? payload
      : { chat_id: payload, state: stateArg };
    const chatId = normalizeChatId(request?.chat_id);
    if (!chatId) return { status: 400, body: { error: 'invalid_request' } };
    if (request.state !== 'composing' && request.state !== 'paused') {
      return { status: 400, body: { error: 'invalid_request' } };
    }
    try {
      await transport.presence?.(chatId, request.state);
    } catch {
      // A missing typing indicator must never fail the turn.
      log('debug', 'outbound_presence_failed', { chat_kind: chatKind(chatId) });
    }
    return { status: 204, body: null };
  }

  async function read(payload, messageArg) {
    const request = payload !== null && typeof payload === 'object' && !Array.isArray(payload)
      ? payload
      : { chat_id: payload, message_id: messageArg };
    const chatId = normalizeChatId(request?.chat_id);
    if (!chatId) return { status: 400, body: { error: 'invalid_request' } };
    const messageId = request?.message_id;
    if (typeof messageId !== 'string' || messageId === '' || messageId.length > 200) {
      return { status: 400, body: { error: 'invalid_request' } };
    }
    if (readReceipts) {
      try {
        await transport.readMessages?.([
          { remoteJid: chatId, id: messageId, fromMe: false },
        ]);
      } catch {
        log('debug', 'outbound_read_receipt_failed', { chat_kind: chatKind(chatId) });
      }
    }
    return { status: 204, body: null };
  }

  return { send, typing, read };
}
