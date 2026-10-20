// Pure backoff helpers for the bridge reconnect loop. No timers, no sockets.

const DEFAULTS = Object.freeze({
  base: 1000,
  cap: 60000,
  jitter: 0.2,
});

function positiveNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function clamp01(value) {
  if (!Number.isFinite(value)) return 0.5;
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
}

/**
 * Exponential backoff with symmetric jitter:
 *   min(cap, base * 2^attempt) * (1 - jitter + 2 * jitter * rand())
 * Returns milliseconds (possibly fractional). Pure when `rand` is injected.
 */
export function nextDelay(attempt, options = {}) {
  const base = positiveNumber(options.base, DEFAULTS.base);
  const cap = positiveNumber(options.cap, DEFAULTS.cap);
  const jitter = clamp01(options.jitter === undefined ? DEFAULTS.jitter : Number(options.jitter));
  const rand = typeof options.rand === 'function' ? options.rand : Math.random;

  const rawAttempt = Number(attempt);
  const safeAttempt = Number.isFinite(rawAttempt) ? Math.max(0, Math.floor(rawAttempt)) : 0;

  const exponential = Math.min(cap, base * 2 ** safeAttempt);
  const factor = 1 - jitter + 2 * jitter * clamp01(Number(rand()));
  return Math.round(exponential * factor);
}

/**
 * Map a Baileys disconnect status to the reconnect policy.
 * - 401 / 403 / 'loggedOut' -> re-pair (stop; owner scans the QR again)
 * - 515 -> restart-now after 1000 ms (restart required, common right after pairing)
 * - anything else -> retry with normal backoff
 */
export function classifyDisconnect(statusCode) {
  if (statusCode === 401 || statusCode === 403 || statusCode === 'loggedOut') {
    return { action: 're-pair', delayMs: null };
  }
  if (statusCode === 515) {
    return { action: 'restart-now', delayMs: 1000 };
  }
  return { action: 'retry', delayMs: null };
}
