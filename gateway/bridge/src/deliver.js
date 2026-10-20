// Inbound delivery to the Python gateway.
//
// One FIFO worker keeps the order of received messages. Delivery statuses are
// mapped exactly as the frozen `/v1/inbound` contract says: 202/200 are final,
// 400/401/403/404/413/415 drop the event, 429/5xx/timeout/network are retried
// with 1s/2s/4s (or `retry_after_ms`), and three consecutive 401s pause the
// queue for 60 seconds instead of hot-looping against a wrong token.
//
// Log records carry metadata only: msg_id, chat_kind, text_len, attempt, and
// status. Message text, JIDs, and the shared token never reach the logger.

const RETRY_BASE_MS = 1000;
const RETRY_CAP_MS = 30_000;
const PAUSE_MS = 60_000;
const POST_TIMEOUT_MS = 10_000;
const AUTH_FAILURES_BEFORE_PAUSE = 3;

const DROP_STATUSES = new Set([400, 401, 403, 404, 413, 415]);

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

function eventKind(event) {
  return event && event.is_group === true ? 'group' : 'dm';
}

function eventFields(event) {
  const text = event && typeof event.text === 'string' ? event.text : '';
  return {
    msg_id: typeof event?.message_id === 'string' ? event.message_id : undefined,
    chat_kind: eventKind(event),
    text_len: text.length,
  };
}

function safeReason(value) {
  // Gateway reasons are short codes (`blank_text`, `not_mentioned`, ...).
  // Anything else is dropped so message content can never leak into logs.
  if (typeof value !== 'string') return undefined;
  return /^[a-z0-9_]{1,40}$/i.test(value) ? value : undefined;
}

function retryAfterMs(result) {
  const body = result?.body;
  let value = body && typeof body === 'object' ? body.retry_after_ms : undefined;
  let fromHeader = false;

  if (value === undefined && result?.headers && typeof result.headers === 'object') {
    value = result.headers['retry-after'] ?? result.headers['Retry-After'];
    fromHeader = value !== undefined;
  }
  if (typeof value === 'string' && /^\d+$/.test(value.trim())) value = Number(value.trim());
  if (!Number.isFinite(value) || value <= 0) return null;
  const millis = fromHeader && value < 1000 ? value * 1000 : value;
  return Math.min(millis, RETRY_CAP_MS);
}

/**
 * createInboundDeliverer({post, url, token, clock, logger, maxQueue, maxAttempts})
 *
 * @param {(url: string, body: object, options: object) => Promise<{status:number, body?:any}>} post
 * @returns {{enqueue: (event: object) => void, flush: (timeoutMs?: number) => Promise<boolean>,
 *   queueDepth: () => number, deliveryPaused: () => boolean, stats: () => object}}
 */
export function createInboundDeliverer({
  post,
  url,
  token,
  clock,
  logger,
  maxQueue = 500,
  maxAttempts = 3,
} = {}) {
  if (typeof post !== 'function') throw new TypeError('deliver: post must be a function');
  if (typeof url !== 'string' || url === '') throw new TypeError('deliver: url is required');
  if (!Number.isInteger(maxQueue) || maxQueue < 1) {
    throw new TypeError('deliver: maxQueue must be a positive integer');
  }
  if (!Number.isInteger(maxAttempts) || maxAttempts < 0) {
    throw new TypeError('deliver: maxAttempts must be a non-negative integer');
  }

  const sleep = makeSleep(clock);
  const queue = [];
  const idleWaiters = [];
  const counters = {
    delivered: 0,
    rejected: 0,
    droppedOverflow: 0,
    droppedDelivery: 0,
    retries: 0,
  };

  let running = false;
  let paused = false;
  let pauseUntil = 0;
  let consecutive401 = 0;

  // pino takes `(fields, msg)`; the shared test logger takes `(msg, fields)`.
  const pinoStyle = Boolean(logger && typeof logger.levels === 'object' && logger.levels !== null);
  const log = (level, msg, fields) => {
    try {
      const method = logger?.[level];
      if (typeof method !== 'function') return;
      if (pinoStyle) method.call(logger, { event: msg, ...fields }, msg);
      else method.call(logger, msg, fields);
    } catch {
      // Logging must never break delivery.
    }
  };

  const isPaused = () => {
    if (!paused) return false;
    if (resolveNow(clock) >= pauseUntil) {
      paused = false;
      return false;
    }
    return true;
  };

  function notifyIdle() {
    while (idleWaiters.length > 0) {
      const waiter = idleWaiters.shift();
      if (waiter.settled) continue;
      waiter.settled = true;
      clearTimeout(waiter.timer);
      waiter.resolve(true);
    }
  }

  async function deliverOne(event) {
    const fields = eventFields(event);
    let attempt = 0;

    for (;;) {
      let result;
      try {
        result = await post(url, event, {
          headers: {
            'X-Sanka-Token': token,
            'Content-Type': 'application/json',
          },
          timeoutMs: POST_TIMEOUT_MS,
        });
      } catch {
        // Connection reset, timeout, or a malformed response: retryable.
        result = { status: 0 };
      }

      const status = Number(result?.status);
      if (status === 202 || status === 200) {
        consecutive401 = 0;
        counters.delivered += 1;
        if (status === 200) {
          const reason = safeReason(result?.body?.reason) ?? safeReason(result?.body?.status);
          log('info', 'inbound_ignored', { ...fields, status, reason });
        } else {
          log('debug', 'inbound_delivered', { ...fields, status });
        }
        return;
      }

      if (DROP_STATUSES.has(status)) {
        consecutive401 = status === 401 ? consecutive401 + 1 : 0;
        counters.rejected += 1;
        log('warn', 'inbound_rejected', { ...fields, status });
        if (consecutive401 >= AUTH_FAILURES_BEFORE_PAUSE) {
          consecutive401 = 0;
          paused = true;
          pauseUntil = resolveNow(clock) + PAUSE_MS;
          log('error', 'delivery_paused', {
            ...fields,
            reason: 'auth_failed',
            retry_after_ms: PAUSE_MS,
          });
        }
        return;
      }

      const retryable = status === 429 || (status >= 500 && status <= 599) || status === 0;
      if (!retryable) {
        // Unknown non-success status: dropping is safer than a hot loop.
        consecutive401 = 0;
        counters.rejected += 1;
        log('warn', 'inbound_rejected', { ...fields, status });
        return;
      }

      consecutive401 = 0;
      if (attempt >= maxAttempts) {
        counters.droppedDelivery += 1;
        log('warn', 'inbound_dropped_delivery', {
          ...fields,
          attempts: attempt + 1,
          status: status || 'network_error',
        });
        return;
      }

      attempt += 1;
      counters.retries += 1;
      const requested = retryAfterMs(result);
      const delayMs = requested ?? RETRY_BASE_MS * 2 ** (attempt - 1);
      log('warn', 'inbound_retry', {
        ...fields,
        attempt,
        status: status || 'network_error',
        delay_ms: delayMs,
      });
      await sleep(delayMs);
    }
  }

  async function drain() {
    try {
      while (queue.length > 0) {
        if (isPaused()) {
          const waitMs = Math.max(0, pauseUntil - resolveNow(clock));
          log('warn', 'delivery_waiting', { reason: 'paused', retry_after_ms: waitMs });
          await sleep(waitMs);
          paused = false;
        }
        const event = queue.shift();
        await deliverOne(event);
      }
    } finally {
      running = false;
      if (queue.length > 0) {
        running = true;
        void drain();
      } else {
        notifyIdle();
      }
    }
  }

  function enqueue(event) {
    if (event === null || typeof event !== 'object') return false;
    queue.push(event);
    if (queue.length > maxQueue) {
      queue.shift();
      counters.droppedOverflow += 1;
      log('warn', 'inbound_dropped_overflow', { queue_depth: queue.length });
    }
    if (!running) {
      running = true;
      void drain();
    }
    return true;
  }

  function flush(timeoutMs = 5000) {
    if (!running && queue.length === 0) return Promise.resolve(true);
    return new Promise((resolve) => {
      const waiter = { resolve, settled: false, timer: null };
      waiter.timer = setTimeout(() => {
        if (waiter.settled) return;
        waiter.settled = true;
        const index = idleWaiters.indexOf(waiter);
        if (index >= 0) idleWaiters.splice(index, 1);
        resolve(false);
      }, Math.max(0, Number(timeoutMs) || 0));
      idleWaiters.push(waiter);
    });
  }

  function queueDepth() {
    return queue.length;
  }

  function deliveryPaused() {
    return isPaused();
  }

  function stats() {
    return {
      queue_depth: queue.length,
      delivery_paused: isPaused(),
      inbound_delivered: counters.delivered,
      inbound_rejected: counters.rejected,
      inbound_dropped_overflow: counters.droppedOverflow,
      inbound_dropped_delivery: counters.droppedDelivery,
      inbound_retries: counters.retries,
    };
  }

  return { enqueue, flush, queueDepth, deliveryPaused, stats };
}
