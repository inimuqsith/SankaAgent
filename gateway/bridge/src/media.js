// Inbound media download and upload (plan-12).
//
// This module is transport-only: it decides nothing about permissions or
// replies. It reads the media descriptor from a Baileys message, downloads the
// bytes, uploads them to the Python gateway, and returns the attachment
// metadata that goes into /v1/inbound. Failures never throw to the caller;
// they become short attachment_errors codes so Python can answer honestly.
import { createHash } from 'node:crypto';

import { downloadMediaMessage } from '@whiskeysockets/baileys';

import { unwrapMessage } from './normalize.js';

const IMAGE_MIMES = new Set(['image/jpeg', 'image/png', 'image/webp']);
const DOCUMENT_MIMES = new Set([
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'text/csv',
  'text/markdown',
  'text/plain',
]);
const EXTENSION_MIMES = new Map([
  ['.pdf', 'application/pdf'],
  ['.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
  ['.csv', 'text/csv'],
  ['.md', 'text/markdown'],
  ['.markdown', 'text/markdown'],
  ['.txt', 'text/plain'],
]);
const IMAGE_EXTENSION_MIMES = new Map([
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.png', 'image/png'],
  ['.webp', 'image/webp'],
]);
const MIME_ALIASES = new Map([
  ['image/jpg', 'image/jpeg'],
  ['text/x-markdown', 'text/markdown'],
  ['text/md', 'text/markdown'],
  ['text/x-csv', 'text/csv'],
  ['application/csv', 'text/csv'],
]);
const MAX_NAME_CHARS = 128;
const DEFAULT_TIMEOUT_MS = 30_000;
const ATTACHMENT_ID = /^[0-9a-f]{32}$/;

function isObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function numeric(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0;
  if (value && typeof value.toNumber === 'function') {
    const converted = value.toNumber();
    return Number.isFinite(converted) ? converted : 0;
  }
  const converted = Number(value);
  return Number.isFinite(converted) ? converted : 0;
}

function sanitizeName(value, fallback) {
  const raw = typeof value === 'string' ? value.normalize('NFKC') : '';
  const cleaned = [...raw]
    .filter((character) => {
      if (character === '/' || character === '\\') return false;
      const code = character.codePointAt(0) ?? 0;
      return code >= 0x20 && code !== 0x7f;
    })
    .join('')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, MAX_NAME_CHARS);
  return cleaned || fallback;
}

function canonicalMediaMime(value, name) {
  let mime = typeof value === 'string' ? value.split(';')[0].trim().toLowerCase() : '';
  mime = MIME_ALIASES.get(mime) ?? mime;
  if (mime === 'application/vnd.ms-excel' && name.toLowerCase().endsWith('.csv')) {
    mime = 'text/csv';
  }
  if (DOCUMENT_MIMES.has(mime) || IMAGE_MIMES.has(mime)) return mime;
  if (mime === '' || mime === 'application/octet-stream') {
    const dot = name.lastIndexOf('.');
    const extension = dot === -1 ? '' : name.slice(dot).toLowerCase();
    return EXTENSION_MIMES.get(extension) ?? IMAGE_EXTENSION_MIMES.get(extension) ?? null;
  }
  return null;
}

/**
 * Describe the supported media in a Baileys message.
 *
 * Returns null for non-media messages and for audio/video/sticker (still
 * caption-only), or an object with `kind: "unsupported"` for a document whose
 * type Sanka cannot read yet.
 */
export function describeMedia(msg) {
  if (!isObject(msg) || !isObject(msg.message)) return null;
  let content;
  try {
    content = unwrapMessage(msg.message);
  } catch {
    return null;
  }
  if (!isObject(content)) return null;

  if (isObject(content.imageMessage)) {
    const image = content.imageMessage;
    const rawMime = typeof image.mimetype === 'string'
      ? image.mimetype.split(';')[0].trim().toLowerCase()
      : '';
    const mime = MIME_ALIASES.get(rawMime) ?? rawMime;
    const descriptor = {
      kind: IMAGE_MIMES.has(mime) ? 'image' : 'unsupported',
      mime,
      name: '',
      size: numeric(image.fileLength),
    };
    return descriptor;
  }

  if (isObject(content.documentMessage)) {
    const document = content.documentMessage;
    const name = sanitizeName(document.fileName, 'dokumen');
    const mime = canonicalMediaMime(document.mimetype, name);
    return {
      kind: mime ? (IMAGE_MIMES.has(mime) ? 'image' : 'document') : 'unsupported',
      mime: mime ?? (typeof document.mimetype === 'string' ? document.mimetype : ''),
      name,
      size: numeric(document.fileLength),
    };
  }

  return null;
}

function withTimeout(promise, timeoutMs) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('timeout')), timeoutMs);
    if (typeof timer.unref === 'function') timer.unref();
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function statusError(status) {
  if (status === 413) return 'too_large';
  if (status === 415) return 'unsupported';
  return 'upload_failed';
}

/**
 * Create the media pipeline used by the socket manager.
 *
 * @param {object} options
 * @param {(msg: object) => Promise<Buffer>} [options.download]
 * @param {(buffer: Buffer, metadata: object) => Promise<{status:number, body?:any}>} options.upload
 * @param {number} options.maxBytes
 * @param {object} [options.logger]
 * @param {number} [options.timeoutMs]
 */
export function createMediaPipeline({
  download = (msg) => downloadMediaMessage(msg, 'buffer', {}),
  upload,
  maxBytes,
  logger,
  timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) {
  if (typeof download !== 'function') throw new TypeError('media: download must be a function');
  if (typeof upload !== 'function') throw new TypeError('media: upload must be a function');
  if (!Number.isInteger(maxBytes) || maxBytes < 1) {
    throw new TypeError('media: maxBytes must be a positive integer');
  }

  async function prepare(msg, event) {
    const descriptor = describeMedia(msg);
    if (!descriptor) return {};
    if (descriptor.kind === 'unsupported') {
      return { errors: ['unsupported'] };
    }
    if (descriptor.size > maxBytes) {
      return { errors: ['too_large'] };
    }

    let buffer;
    try {
      buffer = await withTimeout(download(msg), timeoutMs);
    } catch {
      logger?.warn?.({ kind: descriptor.kind, code: 'download_failed' }, 'attachment_download_failed');
      return { errors: ['download_failed'] };
    }
    if (!Buffer.isBuffer(buffer) || buffer.length === 0) {
      return { errors: ['download_failed'] };
    }
    if (buffer.length > maxBytes) {
      return { errors: ['too_large'] };
    }

    const sha256 = createHash('sha256').update(buffer).digest('hex');
    let response;
    try {
      response = await upload(buffer, {
        chatId: event.chat_id,
        senderId: event.sender_id,
        messageId: event.message_id,
        kind: descriptor.kind,
        mime: descriptor.mime,
        name: descriptor.name,
        sha256,
      });
    } catch {
      logger?.warn?.({ kind: descriptor.kind, code: 'upload_failed' }, 'attachment_upload_failed');
      return { errors: ['upload_failed'] };
    }

    if (response?.status !== 201) {
      const code = statusError(response?.status);
      logger?.warn?.(
        { kind: descriptor.kind, status: response?.status, code },
        'attachment_upload_rejected',
      );
      return { errors: [code] };
    }
    const attachmentId = response?.body?.attachment_id;
    if (typeof attachmentId !== 'string' || !ATTACHMENT_ID.test(attachmentId)) {
      return { errors: ['upload_failed'] };
    }

    return {
      attachments: [
        {
          attachment_id: attachmentId,
          kind: descriptor.kind,
          mime: descriptor.mime,
          name: descriptor.name,
          size: buffer.length,
        },
      ],
    };
  }

  return { prepare };
}
