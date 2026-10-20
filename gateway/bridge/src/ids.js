// In-memory bounded map used for inbound dedupe, outbound idempotency, and the
// LID -> PN cache. Expiry is lazy and eviction is oldest-first.

function readClock(clock) {
  if (typeof clock === 'function') return Number(clock());
  if (clock && typeof clock.now === 'function') return Number(clock.now());
  return Date.now();
}

/**
 * createBoundedMap({ttlMs, max, clock}) -> { get, set, has, delete, clear, size }
 *
 * - Entries older than `ttlMs` are removed on access (get/has/size/set).
 * - When `max` is exceeded, the oldest inserted entry is evicted.
 * - `clock` may be a function returning ms or an object with now().
 */
export function createBoundedMap(options = {}) {
  const ttlMs = Number.isFinite(Number(options.ttlMs)) && Number(options.ttlMs) > 0
    ? Number(options.ttlMs)
    : Number.POSITIVE_INFINITY;
  const max = Number.isInteger(Number(options.max)) && Number(options.max) > 0
    ? Number(options.max)
    : Number.POSITIVE_INFINITY;
  const clock = options.clock;

  const entries = new Map();

  const now = () => {
    const value = readClock(clock);
    return Number.isFinite(value) ? value : Date.now();
  };

  const isExpired = (entry, current) => entry.expiresAt <= current;

  const pruneExpired = () => {
    const current = now();
    for (const [key, entry] of entries) {
      if (isExpired(entry, current)) entries.delete(key);
    }
  };

  return {
    get(key) {
      const entry = entries.get(key);
      if (!entry) return undefined;
      if (isExpired(entry, now())) {
        entries.delete(key);
        return undefined;
      }
      return entry.value;
    },

    set(key, value) {
      // Re-insert so an updated key becomes the newest entry.
      entries.delete(key);
      const current = now();
      pruneExpired();
      entries.set(key, { value, expiresAt: current + ttlMs });

      while (entries.size > max) {
        const oldest = entries.keys().next();
        if (oldest.done) break;
        entries.delete(oldest.value);
      }
      return this;
    },

    has(key) {
      const entry = entries.get(key);
      if (!entry) return false;
      if (isExpired(entry, now())) {
        entries.delete(key);
        return false;
      }
      return true;
    },

    delete(key) {
      return entries.delete(key);
    },

    clear() {
      entries.clear();
    },

    get size() {
      pruneExpired();
      return entries.size;
    },
  };
}
