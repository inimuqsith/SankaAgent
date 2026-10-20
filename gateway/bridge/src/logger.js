// Structured JSON logger for the Sanka WhatsApp bridge.
//
// Two rules drive this module:
//   1. Message text, captions, tokens and Authorization headers never reach a
//      log sink, not even when a caller passes them as structured fields.
//   2. Raw JIDs/phone numbers never reach a log sink either. Callers that need
//      to correlate a chat across log lines must use maskJid()/hashId().
//
// QR codes and session payloads are secrets too: the redaction list censors the
// usual field names so an accidental `logger.info({ qr })` cannot leak them.
import { createHash } from 'node:crypto';
import pino from 'pino';

export const REDACTED = '[Redacted]';
export const DEFAULT_HASH_LENGTH = 12;

// fast-redact paths. Wildcards cover the two nesting levels that show up in
// practice: `{ headers: { authorization } }` and `{ event: { text } }`.
const REDACT_PATHS = [
  'text',
  'caption',
  'message',
  'token',
  'authorization',
  'qr',
  'session',
  'creds',
  'helloMsg',
  'node',
  'noiseKey',
  'signedIdentityKey',
  'signedPreKey',
  'advSecretKey',
  'registrationId',
  'pairingEphemeralKeyPair',
  'headers.authorization',
  'req.headers.authorization',
  'request.headers.authorization',
  '*.text',
  '*.caption',
  '*.message',
  '*.token',
  '*.authorization',
  '*.qr',
  '*.session',
  '*.creds',
  '*.helloMsg',
  '*.node',
  '*.noiseKey',
  '*.signedIdentityKey',
  '*.signedPreKey',
  '*.advSecretKey',
  '*.registrationId',
  '*.pairingEphemeralKeyPair',
  '*.*.text',
  '*.*.caption',
  '*.*.message',
  '*.*.token',
  '*.*.authorization',
  '*.*.qr',
  '*.*.helloMsg',
  '*.*.node',
  '*.*.creds',
];

/**
 * Deterministic truncated SHA-256 digest, used to correlate ids without
 * exposing them.
 *
 * @param {unknown} value
 * @param {number} [length] hex characters, clamped to 1..64
 * @returns {string}
 */
export function hashId(value, length = DEFAULT_HASH_LENGTH) {
  const size =
    Number.isInteger(length) && length > 0 && length <= 64
      ? length
      : DEFAULT_HASH_LENGTH;
  const input = typeof value === 'string' ? value : String(value);
  return createHash('sha256').update(input, 'utf8').digest('hex').slice(0, size);
}

/**
 * Mask a WhatsApp JID: the number-side is replaced by a 6 hex character hash
 * while the domain is kept so log readers can tell a user JID from a group.
 * The output contains no digit of the original phone number.
 *
 * maskJid('6281234567890@s.whatsapp.net') -> 'a1b2c3@s.whatsapp.net'
 *
 * @param {unknown} jid
 * @returns {string}
 */
export function maskJid(jid) {
  if (typeof jid !== 'string') return 'unknown';
  const value = jid.trim();
  if (value === '') return 'unknown';

  const at = value.lastIndexOf('@');
  if (at <= 0 || at === value.length - 1) {
    // Not a JID (or a malformed one): never echo the raw value.
    return hashId(value, 6);
  }

  const local = value.slice(0, at);
  const domain = value.slice(at + 1);
  return `${hashId(local, 6)}@${domain}`;
}

/**
 * Create the bridge's pino logger.
 *
 * @param {string} [level] pino level name
 * @param {{stream?: NodeJS.WritableStream, base?: object|null}} [options]
 *   `stream` exists so tests can capture JSON lines without touching stdout.
 * @returns {import('pino').Logger}
 */
export function createLogger(level = 'info', options = {}) {
  const { stream, base } = options;

  const pinoOptions = {
    level,
    redact: { paths: REDACT_PATHS, censor: REDACTED },
    timestamp: pino.stdTimeFunctions.isoTime,
    base: base === undefined ? { service: 'sanka-whatsapp-bridge' } : base,
  };

  return stream ? pino(pinoOptions, stream) : pino(pinoOptions);
}
