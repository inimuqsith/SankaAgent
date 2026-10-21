import test from 'node:test';
import assert from 'node:assert/strict';

import { createMediaPipeline, describeMedia } from '../src/media.js';
import { uploadAttachment } from '../src/upload.js';
import { createFakeLogger } from './helpers.js';

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR42mP8z8Dwn4GBgYGJAQoAHgQCAZ7F0XQAAAAASUVORK5CYII=',
  'base64',
);
const ID = 'a'.repeat(32);
const EVENT = {
  message_id: 'msg-1',
  chat_id: '628111111111@s.whatsapp.net',
  sender_id: '628111111111@s.whatsapp.net',
};

function message(content) {
  return { key: { id: 'msg-1' }, message: content };
}

test('describes supported image and document media', () => {
  assert.deepEqual(
    describeMedia(
      message({
        imageMessage: { mimetype: 'image/png', fileLength: 5 },
      }),
    ),
    { kind: 'image', mime: 'image/png', name: '', size: 5 },
  );

  assert.deepEqual(
    describeMedia(
      message({
        documentMessage: {
          mimetype: 'text/markdown',
          fileName: 'catatan.md',
          fileLength: 12,
        },
      }),
    ),
    { kind: 'document', mime: 'text/markdown', name: 'catatan.md', size: 12 },
  );
});

test('unwraps documentWithCaptionMessage and rejects unsupported documents', () => {
  const descriptor = describeMedia(
    message({
      documentWithCaptionMessage: {
        message: {
          documentMessage: {
            mimetype: 'application/msword',
            fileName: 'laporan.doc',
            fileLength: 10,
          },
        },
      },
    }),
  );
  assert.equal(descriptor.kind, 'unsupported');
  assert.equal(descriptor.name, 'laporan.doc');
});

test('treats an image sent as a document as an image', () => {
  assert.deepEqual(
    describeMedia(
      message({
        documentMessage: {
          mimetype: 'image/jpeg',
          fileName: 'foto.jpg',
          fileLength: 42,
        },
      }),
    ),
    { kind: 'image', mime: 'image/jpeg', name: 'foto.jpg', size: 42 },
  );
});

test('ignores audio and non-media messages', () => {
  assert.equal(describeMedia(message({ audioMessage: { mimetype: 'audio/ogg' } })), null);
  assert.equal(describeMedia(message({ conversation: 'halo' })), null);
});

test('uploads a downloaded image and returns attachment metadata', async () => {
  const calls = [];
  const pipeline = createMediaPipeline({
    download: async () => PNG,
    upload: async (buffer, metadata) => {
      calls.push({ buffer, metadata });
      return { status: 201, body: { attachment_id: ID } };
    },
    maxBytes: 1024,
  });

  const prepared = await pipeline.prepare(
    message({ imageMessage: { mimetype: 'image/png', fileLength: PNG.length } }),
    EVENT,
  );

  assert.equal(prepared.errors, undefined);
  assert.deepEqual(prepared.attachments, [
    {
      attachment_id: ID,
      kind: 'image',
      mime: 'image/png',
      name: '',
      size: PNG.length,
    },
  ]);
  assert.equal(calls[0].metadata.chatId, EVENT.chat_id);
  assert.equal(calls[0].metadata.messageId, EVENT.message_id);
  assert.match(calls[0].metadata.sha256, /^[0-9a-f]{64}$/);
});

test('download and upload failures become short codes', async () => {
  const downloadFailure = createMediaPipeline({
    download: async () => {
      throw new Error('nope');
    },
    upload: async () => ({ status: 201, body: { attachment_id: ID } }),
    maxBytes: 1024,
  });
  assert.deepEqual(
    await downloadFailure.prepare(
      message({ imageMessage: { mimetype: 'image/png' } }),
      EVENT,
    ),
    { errors: ['download_failed'] },
  );

  const uploadFailure = createMediaPipeline({
    download: async () => PNG,
    upload: async () => ({ status: 500, body: {} }),
    maxBytes: 1024,
  });
  assert.deepEqual(
    await uploadFailure.prepare(
      message({ imageMessage: { mimetype: 'image/png' } }),
      EVENT,
    ),
    { errors: ['upload_failed'] },
  );
});

test('oversize media is not downloaded', async () => {
  let downloaded = false;
  const pipeline = createMediaPipeline({
    download: async () => {
      downloaded = true;
      return PNG;
    },
    upload: async () => ({ status: 201, body: { attachment_id: ID } }),
    maxBytes: 10,
  });
  const prepared = await pipeline.prepare(
    message({ imageMessage: { mimetype: 'image/png', fileLength: 100 } }),
    EVENT,
  );
  assert.deepEqual(prepared, { errors: ['too_large'] });
  assert.equal(downloaded, false);
});

test('gateway rejection codes are mapped without leaking content', async () => {
  const logger = createFakeLogger();
  const pipeline = createMediaPipeline({
    download: async () => PNG,
    upload: async () => ({ status: 413, body: {} }),
    maxBytes: 1024,
    logger,
  });
  assert.deepEqual(
    await pipeline.prepare(
      message({ imageMessage: { mimetype: 'image/png' } }),
      EVENT,
    ),
    { errors: ['too_large'] },
  );
  const logged = JSON.stringify(logger.records);
  assert.ok(!logged.includes('iVBOR'));
});

test('uploadAttachment sends binary metadata headers', async () => {
  const seen = [];
  const result = await uploadAttachment({
    url: 'http://127.0.0.1:8099/v1/attachments',
    token: 'test-token',
    buffer: PNG,
    chatId: EVENT.chat_id,
    senderId: EVENT.sender_id,
    messageId: EVENT.message_id,
    kind: 'image',
    mime: 'image/png',
    name: 'foto.png',
    sha256: 'b'.repeat(64),
    fetchImpl: async (url, options) => {
      seen.push({ url, options });
      return { status: 201, text: async () => JSON.stringify({ attachment_id: ID }) };
    },
  });

  assert.equal(result.status, 201);
  const headers = seen[0].options.headers;
  assert.equal(headers['X-Sanka-Token'], 'test-token');
  assert.equal(headers['Content-Type'], 'application/octet-stream');
  assert.equal(headers['X-Sanka-Chat-Id'], EVENT.chat_id);
  assert.equal(
    Buffer.from(headers['X-Sanka-Filename-B64'], 'base64url').toString('utf8'),
    'foto.png',
  );
  assert.equal(seen[0].options.body, PNG);
});
