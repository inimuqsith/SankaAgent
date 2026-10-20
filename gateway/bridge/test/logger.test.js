import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { createLogger, hashId, maskJid, REDACTED } from '../src/logger.js';

const PHONE_JID = '6281234567890@s.whatsapp.net';

function captureStream() {
  const chunks = [];
  return {
    chunks,
    write(chunk) {
      chunks.push(String(chunk));
      return true;
    },
    parsed() {
      return chunks
        .join('')
        .split('\n')
        .filter((line) => line.trim() !== '')
        .map((line) => JSON.parse(line));
    },
  };
}

describe('hashId', () => {
  test('is deterministic and hex encoded', () => {
    const first = hashId(PHONE_JID);
    const second = hashId(PHONE_JID);

    assert.equal(first, second);
    assert.match(first, /^[0-9a-f]{12}$/);
    assert.notEqual(first, hashId('6281234567891@s.whatsapp.net'));
  });

  test('honours an explicit length without exceeding 64 hex characters', () => {
    assert.match(hashId('value', 6), /^[0-9a-f]{6}$/);
    assert.match(hashId('value', 999), /^[0-9a-f]{12}$/);
    assert.match(hashId('value', 64), /^[0-9a-f]{64}$/);
  });
});

describe('maskJid', () => {
  test('is deterministic and keeps only the domain', () => {
    const first = maskJid(PHONE_JID);
    const second = maskJid(PHONE_JID);

    assert.equal(first, second);
    assert.match(first, /^[0-9a-f]{6}@s\.whatsapp\.net$/);
    assert.ok(!first.includes('6281234567890'));
    assert.ok(!first.includes('628123'));
  });

  test('masks group, LID, and device-suffixed JIDs', () => {
    assert.match(maskJid('120363000000000000@g.us'), /^[0-9a-f]{6}@g\.us$/);
    assert.match(maskJid('998877665544332211@lid'), /^[0-9a-f]{6}@lid$/);
    assert.match(
      maskJid('6281234567890:4@s.whatsapp.net'),
      /^[0-9a-f]{6}@s\.whatsapp\.net$/,
    );
  });

  test('hashes malformed values instead of echoing them', () => {
    assert.match(maskJid('6281234567890'), /^[0-9a-f]{6}$/);
    assert.equal(maskJid(''), 'unknown');
    assert.equal(maskJid(undefined), 'unknown');
    assert.equal(maskJid(null), 'unknown');
  });

  test('different phone numbers produce different masks', () => {
    assert.notEqual(maskJid('6281111111111@s.whatsapp.net'), maskJid('6282222222222@s.whatsapp.net'));
  });
});

describe('createLogger redaction', () => {
  test('censors text, caption, message, token, and authorization', () => {
    const stream = captureStream();
    const logger = createLogger('debug', { stream });

    logger.info(
      {
        text: 'pesan rahasia',
        caption: 'caption rahasia',
        message: 'isi pesan',
        token: 'tok_secret_value',
        authorization: 'Bearer top-secret',
        headers: { authorization: 'Bearer nested-secret' },
      },
      'inbound_received',
    );

    const [record] = stream.parsed();
    assert.equal(record.msg, 'inbound_received');
    assert.equal(record.text, REDACTED);
    assert.equal(record.caption, REDACTED);
    assert.equal(record.message, REDACTED);
    assert.equal(record.token, REDACTED);
    assert.equal(record.authorization, REDACTED);
    assert.equal(record.headers.authorization, REDACTED);

    const raw = stream.chunks.join('');
    for (const secret of [
      'pesan rahasia',
      'caption rahasia',
      'isi pesan',
      'tok_secret_value',
      'Bearer top-secret',
      'Bearer nested-secret',
    ]) {
      assert.ok(!raw.includes(secret), `log output leaked ${secret}`);
    }
  });

  test('censors QR and session payloads', () => {
    const stream = captureStream();
    const logger = createLogger('info', { stream });

    logger.info({ qr: '2@abcDEF1234567890-session-payload', session: { creds: 'secret' } }, 'pairing');

    const raw = stream.chunks.join('');
    assert.ok(!raw.includes('2@abcDEF1234567890-session-payload'));
    assert.ok(!raw.includes('secret'));
    assert.equal(stream.parsed()[0].qr, REDACTED);
  });

  test('censors Baileys handshake and key material', () => {
    const stream = captureStream();
    const logger = createLogger('info', { stream });

    logger.info(
      {
        helloMsg: { clientHello: { ephemeral: 'ephemeral-secret' } },
        node: { devicePairingData: { eKey: 'pairing-secret' } },
        pairingEphemeralKeyPair: { private: 'private-secret' },
      },
      'connected to WA',
    );

    const raw = stream.chunks.join('');
    for (const secret of ['ephemeral-secret', 'pairing-secret', 'private-secret']) {
      assert.ok(!raw.includes(secret), `log output leaked ${secret}`);
    }
  });

  test('keeps metadata fields such as message ids intact', () => {
    const stream = captureStream();
    const logger = createLogger('info', { stream });

    logger.info({ message_id: 'ABC123', chat_hash: 'deadbeef', status: 202 }, 'delivered');

    const [record] = stream.parsed();
    assert.equal(record.message_id, 'ABC123');
    assert.equal(record.chat_hash, 'deadbeef');
    assert.equal(record.status, 202);
  });

  test('logs JSON at the configured level', () => {
    const stream = captureStream();
    const logger = createLogger('warn', { stream });

    logger.info({ event: 'ignored' }, 'should_not_appear');
    logger.warn({ event: 'kept' }, 'appears');

    const records = stream.parsed();
    assert.equal(records.length, 1);
    assert.equal(records[0].msg, 'appears');
    assert.equal(records[0].level, 40);
    assert.match(records[0].time, /^\d{4}-\d{2}-\d{2}T/);
  });
});
