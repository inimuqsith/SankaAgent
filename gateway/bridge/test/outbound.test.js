// Outbound tests: validation, idempotency (done vs in-flight), pacing, the
// global window, connection and transport failures, and typing/read.
//
// Pacing waits are simulated: the fake clock's `sleep(ms)` advances the clock
// immediately, so spacing assertions stay deterministic without real delays.

import test from 'node:test';
import assert from 'node:assert/strict';

import { createOutbound } from '../src/outbound.js';
import { createFakeClock, createFakeLogger, createFakeTransport } from './helpers.js';

const CHAT = '628123456789@s.whatsapp.net';
const OTHER = '628987654321@s.whatsapp.net';
const GROUP = '120363000000000001@g.us';
const KEY = 'message-1:0';

function makeClock() {
  const clock = createFakeClock(0);
  clock.sleeps = [];
  clock.sleep = async (ms) => {
    clock.sleeps.push(ms);
    clock.advance(ms);
  };
  return clock;
}

function makeOutbound({ clock, logger, transport, limits } = {}) {
  const resolvedClock = clock ?? makeClock();
  const resolvedTransport = transport ?? createFakeTransport();
  const outbound = createOutbound({
    transport: resolvedTransport,
    clock: resolvedClock,
    logger: logger ?? createFakeLogger(),
    limits: { outboundPerMinute: 20, readReceipts: false, ...limits },
  });
  return { outbound, clock: resolvedClock, transport: resolvedTransport };
}

function sendPayload(overrides = {}) {
  return { chat_id: CHAT, text: 'halo', idempotency_key: KEY, ...overrides };
}

test('a completed idempotency key answers 409 duplicate', async () => {
  const { outbound, transport } = makeOutbound();

  const first = await outbound.send(sendPayload());
  assert.equal(first.status, 200);
  assert.match(first.body.message_id, /^fake-/);

  const second = await outbound.send(sendPayload());
  assert.equal(second.status, 409);
  assert.deepEqual(second.body, { error: 'duplicate' });
  assert.equal(transport.sent.length, 1);
});

test('an in-flight key is awaited, not sent twice', async () => {
  const transport = createFakeTransport();
  let calls = 0;
  let resolveSend;
  transport.sendText = () => {
    calls += 1;
    return new Promise((resolve) => {
      resolveSend = resolve;
    });
  };
  const { outbound } = makeOutbound({ transport });

  const first = outbound.send(sendPayload());
  const second = outbound.send(sendPayload());
  assert.equal(calls, 1);

  resolveSend({ id: 'wamid-one' });
  const [a, b] = await Promise.all([first, second]);

  assert.equal(calls, 1);
  assert.deepEqual(a, b);
  assert.equal(a.status, 200);
  assert.equal(a.body.message_id, 'wamid-one');
});

test('keeps at least 1200 ms between sends to the same chat', async () => {
  const { outbound, transport, clock } = makeOutbound();
  const times = [];
  const sendText = transport.sendText.bind(transport);
  transport.sendText = async (chatId, text) => {
    times.push(clock.now());
    return sendText(chatId, text);
  };

  await outbound.send(sendPayload({ idempotency_key: 'message-1:0' }));
  await outbound.send(sendPayload({ idempotency_key: 'message-1:1' }));

  assert.equal(times.length, 2);
  assert.ok(times[1] - times[0] >= 1200, `spacing was ${times[1] - times[0]}ms`);
  assert.deepEqual(clock.sleeps, [1200]);
});

test('a second chat is not blocked by the first chat pacing', async () => {
  const { outbound, transport, clock } = makeOutbound();
  const times = [];
  const sendText = transport.sendText.bind(transport);
  transport.sendText = async (chatId, text) => {
    times.push(clock.now());
    return sendText(chatId, text);
  };

  await outbound.send(sendPayload({ idempotency_key: 'message-1:0' }));
  await outbound.send(sendPayload({ chat_id: GROUP, idempotency_key: 'message-2:0' }));

  assert.deepEqual(times, [0, 0]);
  assert.deepEqual(clock.sleeps, []);
  assert.equal(transport.sent.length, 2);
});

test('enforces the global 20 sends per minute window', async () => {
  const { outbound, transport } = makeOutbound();

  for (let index = 0; index < 20; index += 1) {
    const result = await outbound.send({
      chat_id: `6280000000${String(index).padStart(2, '0')}@s.whatsapp.net`,
      text: 'halo',
      idempotency_key: `message-${index}:0`,
    });
    assert.equal(result.status, 200);
  }

  const limited = await outbound.send({
    chat_id: '629999999999@s.whatsapp.net',
    text: 'halo',
    idempotency_key: 'message-20:0',
  });

  assert.equal(limited.status, 429);
  assert.equal(limited.body.error, 'rate_limited');
  assert.ok(limited.body.retry_after_ms > 30_000);
  assert.equal(transport.sent.length, 20);
});

test('returns 429 retry_after_ms when the wait would exceed 30s', async () => {
  const { outbound, transport } = makeOutbound({ limits: { outboundPerMinute: 1 } });

  assert.equal((await outbound.send(sendPayload({ idempotency_key: 'message-1:0' }))).status, 200);
  const limited = await outbound.send({
    chat_id: OTHER,
    text: 'halo',
    idempotency_key: 'message-1:1',
  });

  assert.equal(limited.status, 429);
  assert.equal(limited.body.retry_after_ms, 60_000);
  assert.equal(transport.sent.length, 1);
});

test('answers 503 while the socket is not connected and releases the key', async () => {
  const { outbound, transport } = makeOutbound();
  transport.connected = false;

  const offline = await outbound.send(sendPayload());
  assert.equal(offline.status, 503);
  assert.equal(offline.body.error, 'not_connected');
  assert.ok(offline.body.retry_after_ms >= 1000);
  assert.ok(offline.body.retry_after_ms <= 5000);
  assert.equal(transport.sent.length, 0);

  transport.connected = true;
  const retried = await outbound.send(sendPayload());
  assert.equal(retried.status, 200);
  assert.equal(transport.sent.length, 1);
});

test('a send_failed 502 removes the key so Python may retry', async () => {
  const transport = createFakeTransport();
  let attempts = 0;
  transport.sendText = async () => {
    attempts += 1;
    if (attempts === 1) throw new Error('boom');
    return { id: 'wamid-retry' };
  };
  const { outbound } = makeOutbound({ transport });

  const failed = await outbound.send(sendPayload());
  assert.equal(failed.status, 502);
  assert.equal(failed.body.error, 'send_failed');

  const retried = await outbound.send(sendPayload());
  assert.equal(retried.status, 200);
  assert.equal(retried.body.message_id, 'wamid-retry');
  assert.equal(attempts, 2);
});

test('rejects invalid payloads without truncating text', async () => {
  const { outbound, transport } = makeOutbound();

  assert.equal((await outbound.send(sendPayload({ chat_id: 'abc' }))).status, 400);
  assert.equal((await outbound.send(sendPayload({ text: '   ' }))).status, 400);
  assert.equal((await outbound.send(sendPayload({ idempotency_key: 'short' }))).status, 400);
  assert.equal(
    (await outbound.send(sendPayload({ text: 'a'.repeat(4097), idempotency_key: 'message-2:0' }))).status,
    400,
  );

  const longest = 'a'.repeat(4096);
  const accepted = await outbound.send(
    sendPayload({ text: longest, idempotency_key: 'message-3:0' }),
  );
  assert.equal(accepted.status, 200);
  assert.equal(transport.sent[0].text, longest);
  assert.equal(transport.sent.length, 1);
});

test('typing maps composing/paused to presence and rejects other states', async () => {
  const { outbound, transport } = makeOutbound();

  assert.equal((await outbound.typing({ chat_id: CHAT, state: 'paused' })).status, 204);
  assert.equal((await outbound.typing({ chat_id: CHAT, state: 'composing' })).status, 204);
  assert.deepEqual(transport.presenceCalls, [
    { chatId: CHAT, state: 'paused' },
    { chatId: CHAT, state: 'composing' },
  ]);

  assert.equal((await outbound.typing({ chat_id: CHAT, state: 'recording' })).status, 400);
  assert.equal((await outbound.typing({ chat_id: 'nope', state: 'paused' })).status, 400);
});

test('a failing presence call still answers 204', async () => {
  const transport = createFakeTransport();
  transport.presence = async () => {
    throw new Error('not connected');
  };
  const { outbound } = makeOutbound({ transport });

  assert.equal((await outbound.typing({ chat_id: CHAT, state: 'paused' })).status, 204);
});

test('read is a no-op without read receipts and calls readMessages otherwise', async () => {
  const off = makeOutbound();
  assert.equal((await off.outbound.read({ chat_id: CHAT, message_id: 'ABC' })).status, 204);
  assert.equal(off.transport.readCalls.length, 0);

  const on = makeOutbound({ limits: { readReceipts: true } });
  assert.equal((await on.outbound.read({ chat_id: CHAT, message_id: 'ABC' })).status, 204);
  assert.deepEqual(on.transport.readCalls, [
    [{ remoteJid: CHAT, id: 'ABC', fromMe: false }],
  ]);

  assert.equal((await on.outbound.read({ chat_id: CHAT, message_id: '' })).status, 400);
});

test('a failing read receipt is logged, not surfaced', async () => {
  const transport = createFakeTransport();
  transport.readMessages = async () => {
    throw new Error('unavailable');
  };
  const { outbound } = makeOutbound({ transport, limits: { readReceipts: true } });

  assert.equal((await outbound.read({ chat_id: CHAT, message_id: 'ABC' })).status, 204);
});

test('never logs message text or JIDs', async () => {
  const logger = createFakeLogger();
  const { outbound } = makeOutbound({ logger });

  await outbound.send(
    sendPayload({ text: 'SECRET-OUTBOUND-TEXT', idempotency_key: 'message-9:0' }),
  );

  const serialized = JSON.stringify(logger.records);
  assert.ok(!serialized.includes('SECRET-OUTBOUND-TEXT'));
  assert.ok(!serialized.includes(CHAT));
});
