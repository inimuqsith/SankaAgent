// HTTP bridge for the Sanka WhatsApp gateway.
//
// Transport-only surface: four loopback endpoints, shared-token auth, and a
// hard body limit. No routing framework, no CORS, no static files, and no
// business rules. Error responses never echo request content.

import { createHash, timingSafeEqual } from 'node:crypto';
import { createServer as createHttpServer } from 'node:http';

const MAX_BODY_BYTES = 64 * 1024;
const LOOPBACK_BIND_HOSTS = new Set(['127.0.0.1', 'localhost', '::1']);
const LOOPBACK_HOST_HEADERS = new Set(['127.0.0.1', 'localhost', '[::1]']);

const POST_ROUTES = new Map([
  ['/v1/send', 'send'],
  ['/v1/typing', 'typing'],
  ['/v1/read', 'read'],
]);

function sha256(value) {
  return createHash('sha256').update(value, 'utf8').digest();
}

/**
 * Parse a Host header into its hostname, rejecting anything that is not
 * loopback. Ports are optional but must be numeric when present.
 */
export function isLoopbackHostHeader(value) {
  if (typeof value !== 'string') return false;
  const host = value.trim().toLowerCase();
  if (host === '') return false;

  if (host.startsWith('[')) {
    const end = host.indexOf(']');
    if (end === -1) return false;
    const name = host.slice(0, end + 1);
    const rest = host.slice(end + 1);
    if (rest !== '' && !/^:\d{1,5}$/.test(rest)) return false;
    return LOOPBACK_HOST_HEADERS.has(name);
  }

  const colon = host.lastIndexOf(':');
  if (colon === -1) return LOOPBACK_HOST_HEADERS.has(host);
  const name = host.slice(0, colon);
  const port = host.slice(colon + 1);
  if (!/^\d{1,5}$/.test(port)) return false;
  return LOOPBACK_HOST_HEADERS.has(name);
}

function isJsonContentType(value) {
  if (typeof value !== 'string') return false;
  const [type] = value.split(';');
  return type.trim().toLowerCase() === 'application/json';
}

function sendJson(res, status, body, extraHeaders = {}) {
  const payload = body === undefined ? undefined : JSON.stringify(body);
  const headers = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', ...extraHeaders };
  if (payload !== undefined) {
    headers['Content-Type'] = 'application/json; charset=utf-8';
    headers['Content-Length'] = Buffer.byteLength(payload);
  }
  res.writeHead(status, headers);
  if (payload === undefined) res.end();
  else res.end(payload);
}

function sendEmpty(res, status) {
  res.writeHead(status, {
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  res.end();
}

// Respond without consuming the request body, then let the socket close.
function rejectUnreadBody(req, res, status, body, headers = {}) {
  res.setHeader('Connection', 'close');
  sendJson(res, status, body, headers);
  req.resume();
}

/**
 * Read the request body, enforcing the 64 KiB cap while streaming.
 * Resolves with `{ buffer }`, `{ tooLarge: true }`, or `{ aborted: true }`.
 */
function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    let total = 0;
    let settled = false;
    const settle = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };

    req.on('data', (chunk) => {
      if (settled) return;
      total += chunk.length;
      if (total > MAX_BODY_BYTES) {
        settle({ tooLarge: true });
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => settle({ buffer: Buffer.concat(chunks, total) }));
    req.on('error', () => settle({ aborted: true }));
    req.on('aborted', () => settle({ aborted: true }));
  });
}

/**
 * Force the server's listen host to loopback. The caller still owns the
 * listen call; this only makes a non-loopback host impossible.
 */
function guardListenHost(server) {
  const boundListen = server.listen.bind(server);
  server.listen = function guardedListen(...args) {
    const first = args[0];
    if (first !== null && typeof first === 'object') {
      const options = { ...first };
      const host = options.host ?? '127.0.0.1';
      if (!LOOPBACK_BIND_HOSTS.has(String(host).toLowerCase())) {
        throw new Error('bridge server may only bind to loopback');
      }
      options.host = host;
      args[0] = options;
    } else if (typeof first === 'number') {
      if (typeof args[1] === 'string') {
        if (!LOOPBACK_BIND_HOSTS.has(args[1].toLowerCase())) {
          throw new Error('bridge server may only bind to loopback');
        }
      } else {
        args.splice(1, 0, '127.0.0.1');
      }
    } else {
      throw new Error('bridge server may only listen on a loopback TCP port');
    }
    return boundListen(...args);
  };
  return server;
}

function defaultStatus() {
  return {};
}

/**
 * Create the bridge HTTP server. The caller is responsible for `listen`.
 *
 * @param {object} options
 * @param {string} options.token shared gateway token (X-Sanka-Token)
 * @param {object} options.outbound `{send, typing, read}` each returning `{status, body}`
 * @param {() => object} [options.status] health metadata provider
 * @param {object} [options.logger] pino-style logger
 * @returns {import('node:http').Server}
 */
export function createServer({ token, outbound, status = defaultStatus, logger } = {}) {
  if (typeof token !== 'string' || token === '') {
    throw new Error('createServer requires a non-empty token');
  }
  if (outbound === null || typeof outbound !== 'object') {
    throw new Error('createServer requires an outbound object');
  }
  const expectedTokenDigest = sha256(token);

  const server = createHttpServer((req, res) => {
    handleRequest(req, res).catch(() => {
      if (!res.headersSent) sendJson(res, 500, { error: 'internal_error' });
      else res.destroy();
    });
  });

  server.requestTimeout = 15000;
  server.headersTimeout = 10000;
  server.keepAliveTimeout = 5000;

  async function handleRequest(req, res) {
    const startedAt = process.hrtime.bigint();
    let pathname = '/';
    let method = typeof req.method === 'string' ? req.method.toUpperCase() : '';
    let logged = false;
    const log = (statusCode) => {
      if (logged) return;
      logged = true;
      const durationMs = Number(process.hrtime.bigint() - startedAt) / 1e6;
      logger?.info?.('http_request', {
        method,
        path: pathname,
        status: statusCode,
        duration_ms: Math.round(durationMs * 1000) / 1000,
      });
    };
    const respond = (statusCode, body, headers) => {
      sendJson(res, statusCode, body, headers);
      log(statusCode);
    };
    const respondEmpty = (statusCode) => {
      sendEmpty(res, statusCode);
      log(statusCode);
    };

    // 1. Host header: DNS-rebinding defence.
    if (!isLoopbackHostHeader(req.headers.host)) {
      rejectUnreadBody(req, res, 400, { error: 'bad_host' });
      log(400);
      return;
    }

    try {
      pathname = new URL(req.url ?? '/', 'http://127.0.0.1').pathname;
    } catch {
      respond(400, { error: 'bad_request' });
      return;
    }
    if (pathname.length > 1 && pathname.endsWith('/')) pathname = pathname.slice(0, -1);

    // 2. Route existence, then method.
    const isHealth = pathname === '/healthz';
    const postRoute = POST_ROUTES.get(pathname);
    if (!isHealth && !postRoute) {
      rejectUnreadBody(req, res, 404, { error: 'not_found' });
      log(404);
      return;
    }
    const expectedMethod = isHealth ? 'GET' : 'POST';
    if (method !== expectedMethod) {
      rejectUnreadBody(req, res, 405, { error: 'method_not_allowed' }, { Allow: expectedMethod });
      log(405);
      return;
    }

    // 3. Shared token, constant-time, for every /v1/* endpoint.
    if (postRoute !== undefined) {
      const received = req.headers['x-sanka-token'];
      const candidate = typeof received === 'string' ? received : '';
      if (!timingSafeEqual(sha256(candidate), expectedTokenDigest)) {
        rejectUnreadBody(req, res, 401, { error: 'unauthorized' });
        log(401);
        return;
      }
    }

    if (isHealth) {
      let info;
      try {
        info = await status();
      } catch {
        respond(500, { error: 'internal_error' });
        return;
      }
      const source = info !== null && typeof info === 'object' ? info : {};
      respond(200, {
        status: typeof source.status === 'string' ? source.status : 'ok',
        connected: Boolean(source.connected),
        queue_depth: Number(source.queue_depth ?? 0),
        delivery_paused: Boolean(source.delivery_paused),
        uptime_s: Number(source.uptime_s ?? 0),
        bridge_version: source.bridge_version ?? null,
        baileys: source.baileys ?? null,
      });
      return;
    }

    // 4. Body: JSON only, 64 KiB max.
    if (!isJsonContentType(req.headers['content-type'])) {
      rejectUnreadBody(req, res, 415, { error: 'unsupported_media_type' });
      log(415);
      return;
    }
    const declaredLength = Number(req.headers['content-length']);
    if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) {
      rejectUnreadBody(req, res, 413, { error: 'payload_too_large' });
      log(413);
      return;
    }
    const raw = await readBody(req);
    if (raw.aborted) return;
    if (raw.tooLarge) {
      respond(413, { error: 'payload_too_large' });
      return;
    }

    let payload;
    try {
      payload = JSON.parse(raw.buffer.toString('utf8'));
    } catch {
      respond(400, { error: 'invalid_json' });
      return;
    }
    if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
      respond(400, { error: 'invalid_request' });
      return;
    }

    // 5. Hand off to outbound; it owns validation and all WhatsApp semantics.
    let result;
    try {
      result = await outbound[postRoute](payload);
    } catch {
      logger?.warn?.('outbound_failed', { path: pathname });
      respond(500, { error: 'internal_error' });
      return;
    }
    if (result === null || typeof result !== 'object' || !Number.isInteger(result.status)) {
      logger?.warn?.('outbound_invalid_result', { path: pathname });
      respond(500, { error: 'internal_error' });
      return;
    }
    if (result.status === 204) {
      respondEmpty(204);
      return;
    }
    respond(result.status, result.body);
  }

  return guardListenHost(server);
}
