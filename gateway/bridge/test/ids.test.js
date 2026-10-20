import test from 'node:test';
import assert from 'node:assert/strict';

import { createBoundedMap } from '../src/ids.js';
import { createFakeClock } from './helpers.js';

test('stores, reads, checks, and counts entries', () => {
  const map = createBoundedMap({ ttlMs: 1000, max: 10, clock: createFakeClock() });

  assert.equal(map.size, 0);
  assert.equal(map.has('a'), false);
  assert.equal(map.get('a'), undefined);

  map.set('a', 1);
  map.set('b', { value: 2 });

  assert.equal(map.get('a'), 1);
  assert.deepEqual(map.get('b'), { value: 2 });
  assert.equal(map.has('a'), true);
  assert.equal(map.size, 2);

  assert.equal(map.delete('a'), true);
  assert.equal(map.has('a'), false);
  assert.equal(map.delete('a'), false);
  assert.equal(map.size, 1);

  map.clear();
  assert.equal(map.size, 0);
});

test('expires entries lazily after the TTL', () => {
  const clock = createFakeClock(1000);
  const map = createBoundedMap({ ttlMs: 500, max: 10, clock });

  map.set('a', 'value');
  clock.advance(499);
  assert.equal(map.has('a'), true);
  assert.equal(map.get('a'), 'value');

  clock.advance(1);
  assert.equal(map.has('a'), false);
  assert.equal(map.get('a'), undefined);
  assert.equal(map.size, 0);
});

test('expiry is measured per entry from its own set time', () => {
  const clock = createFakeClock(0);
  const map = createBoundedMap({ ttlMs: 100, max: 10, clock });

  map.set('a', 1);
  clock.advance(60);
  map.set('b', 2);

  clock.advance(50);
  assert.equal(map.has('a'), false);
  assert.equal(map.has('b'), true);

  clock.advance(50);
  assert.equal(map.has('b'), false);
});

test('evicts the oldest entry when max is exceeded', () => {
  const clock = createFakeClock(0);
  const map = createBoundedMap({ ttlMs: 10_000, max: 2, clock });

  map.set('a', 1);
  map.set('b', 2);
  map.set('c', 3);

  assert.equal(map.get('a'), undefined);
  assert.equal(map.get('b'), 2);
  assert.equal(map.get('c'), 3);
  assert.equal(map.size, 2);

  map.set('d', 4);
  assert.equal(map.has('b'), false);
  assert.equal(map.has('c'), true);
  assert.equal(map.has('d'), true);
});

test('updating an existing key refreshes it as the newest entry', () => {
  const clock = createFakeClock(0);
  const map = createBoundedMap({ ttlMs: 10_000, max: 2, clock });

  map.set('a', 1);
  map.set('b', 2);
  map.set('a', 10);
  map.set('c', 3);

  assert.equal(map.get('a'), 10);
  assert.equal(map.get('b'), undefined);
  assert.equal(map.get('c'), 3);
  assert.equal(map.size, 2);
});

test('expired entries do not count against max', () => {
  const clock = createFakeClock(0);
  const map = createBoundedMap({ ttlMs: 100, max: 2, clock });

  map.set('old', 1);
  clock.advance(200);
  map.set('b', 2);
  map.set('c', 3);

  assert.equal(map.has('old'), false);
  assert.equal(map.get('b'), 2);
  assert.equal(map.get('c'), 3);
  assert.equal(map.size, 2);
});

test('accepts a function clock and supports undefined values', () => {
  let current = 0;
  const map = createBoundedMap({ ttlMs: 50, max: 5, clock: () => current });

  map.set('nothing', undefined);
  assert.equal(map.has('nothing'), true);
  assert.equal(map.get('nothing'), undefined);

  current = 50;
  assert.equal(map.has('nothing'), false);
});

test('without an explicit clock uses wall time deterministically enough for a fresh entry', () => {
  const map = createBoundedMap({ ttlMs: 60_000, max: 5 });
  map.set('a', 1);
  assert.equal(map.get('a'), 1);
  assert.equal(map.size, 1);
});
