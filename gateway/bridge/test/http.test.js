// HTTP bridge tests: loopback-only host checks, shared token, body limits,
// four routes, and no request content in error responses or logs.

import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';

import { createServer } from '../src/http.js';
import { createFakeLogger, startTestServer } from './helpers.js';

const TOKEN = 'test-token-0123456789';
const CHAT_ID = '628123456789@s.whatsapp.net';
const SECRET_TEXT = 'rahasia-jangan-bocor';

function createFakeOutbound(overrides = {}) {
  const calls = { send: [], typing: [], read: [] };
  const outbound = {
    calls,
    async send(payload) {
      calls.send.push(payload);
      if (overrides.send) return overrides.send(payload);
      return { status: 200, body: { message_id: 'wamid-1' } };
    },
    async typing(payload) {
      calls.typing.push(payload);
      if (overrides.typing) return overrides.typing(payload);
      return { status: 204 };
    },
    async read(payload) {
      calls.read.push(payload);
      if (overrides.read) return overrides.read(payload);
      return { status: 204 };
    },
  };
  return outbound;
}

function request(port, options = {}) {
  const {
    method = 'GET',
    path = '/',
    host = `127.0.0.1:${port}`,
    token,
    body,
    contentType,
    extraHeaders = {},
  } = options;

  return new Promise((resolve, reject) => {
    const headers = { host, connection: 'close', ...extraHeaders };
    if (token !== undefined) headers['x-sanka-token'] = token;

    let payload;
    if (body !== undefined) {
      payload = typeof body === 'string' ? body : JSON.stringify(body);
      headers['content-type'] = contentType ?? 'application/json';
      headers['content-length'] = Buffer.byteLength(payload);
    } else if (contentType !== undefined) {
      headers['content-type'] = contentType;
    }

    const agent = new http.Agent({ keepAlive: false, setHost: false });
    const req = http.request(
      { host: '127.0.0.1', port, method, path, headers, agent },
      (res) => {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          let json;
          try {
            json = text === '' ? undefined : JSON.parse(text);
          } catch {
            json = undefined;
          }
          resolve({ status: res.statusCode, headers: res.headers, text, json });
        });
      },
    );
    req.on('error', reject);
    req.end(payload);
  });
}

async function setup(t, options = {}) {
  const logger = createFakeLogger();
  const outbound = createFakeOutbound(options.outbound);
  const status =
    options.status ??
    (() => ({
      connected: true,
      queue_depth: 2,
      delivery_paused: false,
      uptime_s: 12.5,
      bridge_version: '0.1.0',
      baileys: '7.0.0-rc13',
    }));

  const server = createServer({ token: TOKEN, outbound, status, logger });
  const handle = await startTestServer(server);
  t.after(() => handle.close());

  return {
    server,
    handle,
    outbound,
    logger,
    request: (opts) => request(handle.port, opts),
  };
}

test('server configures transport timeouts and binds only loopback', async (t) => {
  const { server } = await setup(t);
  assert.equal(server.requestTimeout, 15000);
  assert.equal(server.headersTimeout, 10000);
  assert.equal(server.keepAliveTimeout, 5000);
  assert.equal(server.address().address, '127.0.0.1');

  const other = createServer({ token: TOKEN, outbound: createFakeOutbound() });
  assert.throws(() => other.listen(0, '0.0.0.0'), /loopback/);
  assert.equal(other.listening, false);
  await new Promise((resolve) => other.listen(0, resolve));
  assert.equal(other.address().address, '127.0.0.1');
  await new Promise((resolve) => other.close(resolve));
});

test('GET /healthz needs no token and exposes only metadata', async (t) => {
  const { request, outbound } = await setup(t, {
    status: () => ({
      connected: true,
      queue_depth: 3,
      delivery_paused: false,
      uptime_s: 42,
      bridge_version: '0.1.0',
      baileys: '7.0.0-rc13',
      session_dir: '/home/someone/.local/share/sanka',
    }),
  });

  const res = await request({ path: '/healthz' });
  assert.equal(res.status, 200);
  assert.deepEqual(res.json, {
    status: 'ok',
    connected: true,
    queue_depth: 3,
    delivery_paused: false,
    uptime_s: 42,
    bridge_version: '0.1.0',
    baileys: '7.0.0-rc13',
  });
  assert.equal(res.text.includes('@s.whatsapp.net'), false);
  assert.equal(res.text.includes('/home/'), false);
  assert.equal(outbound.calls.send.length + outbound.calls.typing.length + outbound.calls.read.length, 0);
});

test('missing or wrong X-Sanka-Token is 401 unauthorized', async (t) => {
  const { request, outbound } = await setup(t);
  const payload = { chat_id: CHAT_ID, text: 'halo', idempotency_key: 'key-abcdefgh' };

  const missing = await request({ method: 'POST', path: '/v1/send', body: payload });
  assert.equal(missing.status, 401);
  assert.deepEqual(missing.json, { error: 'unauthorized' });

  const wrong = await request({ method: 'POST', path: '/v1/send', token: `${TOKEN}x`, body: payload });
  assert.equal(wrong.status, 401);
  assert.deepEqual(wrong.json, { error: 'unauthorized' });

  assert.equal(outbound.calls.send.length, 0);
});

test('non-loopback Host header is 400', async (t) => {
  const { request } = await setup(t);

  const evil = await request({
    method: 'POST',
    path: '/v1/send',
    token: TOKEN,
    host: 'evil.example.com',
    body: { chat_id: CHAT_ID, text: 'halo', idempotency_key: 'key-abcdefgh' },
  });
  assert.equal(evil.status, 400);

  const rebinding = await request({ path: '/healthz', host: '127.0.0.1.evil.example.com' });
  assert.equal(rebinding.status, 400);

  const localhost = await request({
    method: 'POST',
    path: '/v1/typing',
    token: TOKEN,
    host: 'localhost:1234',
    body: { chat_id: CHAT_ID, state: 'composing' },
  });
  assert.equal(localhost.status, 204);

  const ipv6 = await request({ path: '/healthz', host: '[::1]:3000' });
  assert.equal(ipv6.status, 200);
});

test('body larger than 64 KiB is 413', async (t) => {
  const { request, outbound } = await setup(t);
  const huge = 'a'.repeat(64 * 1024 + 1);
  const res = await request({
    method: 'POST',
    path: '/v1/send',
    token: TOKEN,
    body: { chat_id: CHAT_ID, text: huge, idempotency_key: 'key-abcdefgh' },
  });
  assert.equal(res.status, 413);
  assert.deepEqual(res.json, { error: 'payload_too_large' });
  assert.equal(outbound.calls.send.length, 0);
});

test('non-JSON content type is 415', async (t) => {
  const { request } = await setup(t);
  const res = await request({
    method: 'POST',
    path: '/v1/send',
    token: TOKEN,
    body: 'chat_id=1',
    contentType: 'application/x-www-form-urlencoded',
  });
  assert.equal(res.status, 415);
  assert.deepEqual(res.json, { error: 'unsupported_media_type' });
});

test('malformed JSON is 400', async (t) => {
  const { request } = await setup(t);
  const res = await request({
    method: 'POST',
    path: '/v1/send',
    token: TOKEN,
    body: '{"chat_id": ',
    contentType: 'application/json',
  });
  assert.equal(res.status, 400);
  assert.equal(res.json.error, 'invalid_json');
});

test('unknown route is 404 and wrong method is 405', async (t) => {
  const { request } = await setup(t);

  const notFound = await request({ method: 'POST', path: '/v1/nope', token: TOKEN, body: {} });
  assert.equal(notFound.status, 404);
  assert.deepEqual(notFound.json, { error: 'not_found' });

  const wrongMethod = await request({ method: 'GET', path: '/v1/send', token: TOKEN });
  assert.equal(wrongMethod.status, 405);
  assert.equal(wrongMethod.headers.allow, 'POST');

  const healthPost = await request({ method: 'POST', path: '/healthz', token: TOKEN, body: {} });
  assert.equal(healthPost.status, 405);
  assert.equal(healthPost.headers.allow, 'GET');
});

test('POST /v1/send forwards the payload and returns message_id', async (t) => {
  const { request, outbound } = await setup(t);
  const payload = { chat_id: CHAT_ID, text: 'halo', idempotency_key: 'key-abcdefgh' };

  const res = await request({ method: 'POST', path: '/v1/send', token: TOKEN, body: payload });
  assert.equal(res.status, 200);
  assert.deepEqual(res.json, { message_id: 'wamid-1' });
  assert.equal(outbound.calls.send.length, 1);
  assert.deepEqual(outbound.calls.send[0], payload);
});

test('POST /v1/typing and /v1/read forward and answer 204 without a body', async (t) => {
  const { request, outbound } = await setup(t);
  const typing = { chat_id: CHAT_ID, state: 'composing' };
  const read = { chat_id: CHAT_ID, message_id: 'ABC123' };

  const typingRes = await request({ method: 'POST', path: '/v1/typing', token: TOKEN, body: typing });
  assert.equal(typingRes.status, 204);
  assert.equal(typingRes.text, '');
  assert.equal(typingRes.headers['content-type'], undefined);

  const readRes = await request({ method: 'POST', path: '/v1/read', token: TOKEN, body: read });
  assert.equal(readRes.status, 204);
  assert.equal(readRes.text, '');

  assert.deepEqual(outbound.calls.typing, [typing]);
  assert.deepEqual(outbound.calls.read, [read]);
});

test('outbound error status and body pass through unchanged', async (t) => {
  const { request } = await setup(t, {
    outbound: {
      send: () => ({ status: 409, body: { error: 'duplicate' } }),
    },
  });

  const res = await request({
    method: 'POST',
    path: '/v1/send',
    token: TOKEN,
    body: { chat_id: CHAT_ID, text: 'halo', idempotency_key: 'key-abcdefgh' },
  });
  assert.equal(res.status, 409);
  assert.deepEqual(res.json, { error: 'duplicate' });
});

test('exceptions become 500 internal_error without echoing input', async (t) => {
  const { request, logger } = await setup(t, {
    outbound: {
      send: () => {
        throw new Error(`send failed for ${CHAT_ID} body ${SECRET_TEXT}`);
      },
    },
  });

  const res = await request({
    method: 'POST',
    path: '/v1/send',
    token: TOKEN,
    body: { chat_id: CHAT_ID, text: SECRET_TEXT, idempotency_key: 'key-abcdefgh' },
  });
  assert.equal(res.status, 500);
  assert.deepEqual(res.json, { error: 'internal_error' });
  assert.equal(res.text.includes(CHAT_ID), false);
  assert.equal(res.text.includes(SECRET_TEXT), false);

  const logged = JSON.stringify(logger.records);
  assert.equal(logged.includes(CHAT_ID), false);
  assert.equal(logged.includes(SECRET_TEXT), false);
  assert.equal(logged.includes(TOKEN), false);
});
