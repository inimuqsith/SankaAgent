import test from 'node:test';
import assert from 'node:assert/strict';

import { nextDelay, classifyDisconnect } from '../src/backoff.js';

const mid = () => 0.5;

test('backoff follows 1s, 2s, 4s with neutral jitter', () => {
  assert.equal(nextDelay(0, { rand: mid }), 1000);
  assert.equal(nextDelay(1, { rand: mid }), 2000);
  assert.equal(nextDelay(2, { rand: mid }), 4000);
  assert.equal(nextDelay(3, { rand: mid }), 8000);
});

test('backoff caps at 60 seconds by default', () => {
  assert.equal(nextDelay(6, { rand: mid }), 60000);
  assert.equal(nextDelay(10, { rand: mid }), 60000);
  assert.equal(nextDelay(50, { rand: mid }), 60000);
});

test('jitter stays within the 0.8-1.2 bounds', () => {
  assert.equal(nextDelay(0, { rand: () => 0 }), 800);
  assert.equal(nextDelay(0, { rand: () => 1 }), 1200);
  assert.equal(nextDelay(2, { rand: () => 0 }), 3200);
  assert.equal(nextDelay(2, { rand: () => 1 }), 4800);

  for (let i = 0; i <= 10; i += 1) {
    const delay = nextDelay(4, { rand: () => i / 10 });
    assert.ok(delay >= 12800 && delay <= 19200, `delay ${delay} outside bounds`);
  }
});

test('jitter can be disabled and base/cap overridden', () => {
  assert.equal(nextDelay(3, { jitter: 0, rand: () => 0.9 }), 8000);
  assert.equal(nextDelay(0, { base: 250, cap: 1000, jitter: 0 }), 250);
  assert.equal(nextDelay(1, { base: 250, cap: 1000, jitter: 0 }), 500);
  assert.equal(nextDelay(5, { base: 250, cap: 1000, jitter: 0 }), 1000);
  assert.equal(nextDelay(2, { base: 100, cap: 300, jitter: 0 }), 300);
});

test('backoff is pure and tolerates bad attempts or rand values', () => {
  assert.equal(nextDelay(2, { rand: mid }), nextDelay(2, { rand: mid }));
  assert.equal(nextDelay(-1, { rand: mid }), 1000);
  assert.equal(nextDelay(Number.NaN, { rand: mid }), 1000);
  assert.equal(nextDelay('2', { rand: mid }), 4000);
  assert.equal(nextDelay(0, { rand: () => Number.NaN }), 1000);
  assert.equal(nextDelay(0, { rand: () => -5 }), 800);
  assert.equal(nextDelay(0, { rand: () => 5 }), 1200);
});

test('classifyDisconnect treats 401, 403, and loggedOut as re-pair', () => {
  for (const code of [401, 403, 'loggedOut']) {
    assert.deepEqual(classifyDisconnect(code), { action: 're-pair', delayMs: null });
  }
});

test('classifyDisconnect restarts immediately for 515', () => {
  assert.deepEqual(classifyDisconnect(515), { action: 'restart-now', delayMs: 1000 });
});

test('classifyDisconnect retries for anything else', () => {
  for (const code of [undefined, null, 0, 408, 500, 503, 'connectionClosed', 'unknown']) {
    assert.deepEqual(classifyDisconnect(code), { action: 'retry', delayMs: null });
  }
});
