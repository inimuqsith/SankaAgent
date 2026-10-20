// Inbound deliverer tests: FIFO order, gateway status mapping, retries,
// overflow, the 401 pause, flush, and log hygiene.
//
// Delays are simulated with a fake clock that implements `sleep(ms)` by
// advancing itself, so no test waits for real backoff timers.

import test from 'node:test';
import assert from 'node:assert/strict';

import { createInboundDeliverer } from '../src/deliver.js';
import { createFakeClock, createFakeLogger, jsonResponse } from './helpers.js';

const TOKEN = 'test-token-0123456789';
const URL = 'http://127.0.0.1:8000/v1/inbound';

function makeClock() {
  const clock = createFakeClock(0);
  clock.sleeps = [];
  clock.sleep = async (ms) => {
    clock.sleeps.push(ms);
    clock.advance(ms);
  };
  return clock;
}

function makeEvent(id, overrides = {}) {
  return {
    message_id: id,
    chat_id: '628111111111@s.whatsapp.net',
    sender_id: '628111111111@s.whatsapp.net',
    text: `isi-${id}`,
    is_group: false,
    mentions: [],
    reply_to_bot: false,
    timestamp: 0,
    ...overrides,
  };
}

function makeDeliverer({ responses = [jsonResponse(202, {})], clock, logger, maxQueue, maxAttempts } = {}) {
  const calls = [];
  const post = async (url, body, options) => {
    calls.push({ url, body, options });
    const next = responses.length > 1 ? responses.shift() : responses[0];
    if (next instanceof Error) throw next;
    return next;
  };
  const deliverer = createInboundDeliverer({
    post,
    url: URL,
    token: TOKEN,
    clock: clock ?? makeClock(),
    logger: logger ?? createFakeLogger(),
    maxQueue,
    maxAttempts,
  });
  return { deliverer, calls };
}

test('delivers events in FIFO order with the frozen request shape', async () => {
  const { deliverer, calls } = makeDeliverer();

  deliverer.enqueue(makeEvent('a'));
  deliverer.enqueue(makeEvent('b'));
  deliverer.enqueue(makeEvent('c'));

  assert.equal(await deliverer.flush(1000), true);
  assert.deepEqual(calls.map((call) => call.body.message_id), ['a', 'b', 'c']);
  assert.equal(deliverer.queueDepth(), 0);
  assert.equal(calls[0].url, URL);
  assert.equal(calls[0].options.timeoutMs, 10000);
  assert.equal(calls[0].options.headers['X-Sanka-Token'], TOKEN);
  assert.equal(calls[0].options.headers['Content-Type'], 'application/json');
  assert.equal(deliverer.stats().inbound_delivered, 3);
});

test('treats 202 and 200 as final and logs the 200 reason code only', async () => {
  const logger = createFakeLogger();
  const { deliverer } = makeDeliverer({
    responses: [jsonResponse(200, { status: 'ignored', reason: 'not_mentioned' })],
    logger,
  });

  deliverer.enqueue(makeEvent('ignored'));
  assert.equal(await deliverer.flush(1000), true);

  const ignored = logger.records.find((record) => record.msg === 'inbound_ignored');
  assert.ok(ignored);
  assert.equal(ignored.reason, 'not_mentioned');
  assert.equal(ignored.status, 200);
});

test('retries 429/5xx at 1s, 2s, 4s and then drops the event', async () => {
  const clock = makeClock();
  const { deliverer, calls } = makeDeliverer({
    responses: [jsonResponse(429, {}), jsonResponse(500, {}), jsonResponse(503, {}), jsonResponse(500, {})],
    clock,
  });

  deliverer.enqueue(makeEvent('doomed'));
  assert.equal(await deliverer.flush(1000), true);

  assert.equal(calls.length, 4);
  assert.deepEqual(clock.sleeps, [1000, 2000, 4000]);
  assert.equal(deliverer.stats().inbound_dropped_delivery, 1);
});

test('honours retry_after_ms from the gateway', async () => {
  const clock = makeClock();
  const { deliverer, calls } = makeDeliverer({
    responses: [jsonResponse(429, { retry_after_ms: 5000 }), jsonResponse(202, {})],
    clock,
  });

  deliverer.enqueue(makeEvent('busy'));
  assert.equal(await deliverer.flush(1000), true);

  assert.equal(calls.length, 2);
  assert.deepEqual(clock.sleeps, [5000]);
  assert.equal(deliverer.stats().inbound_delivered, 1);
});

test('drops 400 immediately without retrying', async () => {
  const clock = makeClock();
  const { deliverer, calls } = makeDeliverer({
    responses: [jsonResponse(400, { error: 'bad_request' })],
    clock,
  });

  deliverer.enqueue(makeEvent('bad'));
  assert.equal(await deliverer.flush(1000), true);

  assert.equal(calls.length, 1);
  assert.deepEqual(clock.sleeps, []);
  assert.equal(deliverer.stats().inbound_rejected, 1);
});

test('drops the oldest queued event when the queue overflows', async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const delivered = [];
  const post = async (url, body) => {
    delivered.push(body.message_id);
    await gate;
    return jsonResponse(202, {});
  };
  const deliverer = createInboundDeliverer({
    post,
    url: URL,
    token: TOKEN,
    clock: makeClock(),
    logger: createFakeLogger(),
    maxQueue: 3,
  });

  deliverer.enqueue(makeEvent('e1'));
  // e1 is already in flight; the next four compete for three slots.
  for (const id of ['e2', 'e3', 'e4', 'e5']) deliverer.enqueue(makeEvent(id));

  assert.equal(deliverer.queueDepth(), 3);
  assert.equal(deliverer.stats().inbound_dropped_overflow, 1);

  release();
  assert.equal(await deliverer.flush(1000), true);
  assert.deepEqual(delivered, ['e1', 'e3', 'e4', 'e5']);
});

test('three consecutive 401s pause delivery for 60s', async () => {
  const clock = makeClock();
  const { deliverer } = makeDeliverer({
    responses: [jsonResponse(401, { error: 'unauthorized' })],
    clock,
  });

  deliverer.enqueue(makeEvent('a'));
  deliverer.enqueue(makeEvent('b'));
  deliverer.enqueue(makeEvent('c'));
  assert.equal(await deliverer.flush(1000), true);

  assert.equal(deliverer.deliveryPaused(), true);
  assert.equal(deliverer.stats().delivery_paused, true);
  assert.equal(deliverer.stats().inbound_rejected, 3);

  clock.advance(60_000);
  assert.equal(deliverer.deliveryPaused(), false);
});

test('waits out the pause before delivering the next event', async () => {
  const clock = makeClock();
  let calls = 0;
  const post = async () => {
    calls += 1;
    return calls <= 3 ? jsonResponse(401, {}) : jsonResponse(202, {});
  };
  const deliverer = createInboundDeliverer({
    post,
    url: URL,
    token: TOKEN,
    clock,
    logger: createFakeLogger(),
  });

  for (const id of ['a', 'b', 'c']) deliverer.enqueue(makeEvent(id));
  assert.equal(await deliverer.flush(1000), true);
  assert.equal(deliverer.deliveryPaused(), true);

  deliverer.enqueue(makeEvent('after-pause'));
  assert.equal(await deliverer.flush(1000), true);

  assert.equal(calls, 4);
  assert.ok(clock.sleeps.includes(60_000));
  assert.equal(deliverer.deliveryPaused(), false);
});

test('flush waits for the queue and reports a timeout when it cannot', async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const post = async () => {
    await gate;
    return jsonResponse(202, {});
  };
  const deliverer = createInboundDeliverer({
    post,
    url: URL,
    token: TOKEN,
    clock: makeClock(),
    logger: createFakeLogger(),
  });

  deliverer.enqueue(makeEvent('slow'));
  assert.equal(await deliverer.flush(10), false);

  release();
  assert.equal(await deliverer.flush(1000), true);
});

test('never logs message text, JIDs, or the shared token', async () => {
  const logger = createFakeLogger();
  const clock = makeClock();
  const { deliverer } = makeDeliverer({
    responses: [jsonResponse(500, {}), jsonResponse(202, {})],
    clock,
    logger,
  });

  deliverer.enqueue(
    makeEvent('msg-1', {
      chat_id: '628999888777@s.whatsapp.net',
      text: 'SECRET-TEXT-JANGAN-BOCOR',
    }),
  );
  assert.equal(await deliverer.flush(1000), true);

  const serialized = JSON.stringify(logger.records);
  assert.ok(!serialized.includes('SECRET-TEXT-JANGAN-BOCOR'));
  assert.ok(!serialized.includes('628999888777@s.whatsapp.net'));
  assert.ok(!serialized.includes(TOKEN));
});
