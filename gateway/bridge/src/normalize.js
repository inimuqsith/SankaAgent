// Transport-only normalization of a Baileys WAMessage into the inbound /v1
// contract. This module never performs I/O, never logs raw values, and never
// throws: unrecognized or malformed messages yield null and the caller counts
// them as skipped.

const WRAPPER_KEYS = [
  'ephemeralMessage',
  'viewOnceMessage',
  'viewOnceMessageV2',
  'viewOnceMessageV2Extension',
  'documentWithCaptionMessage',
  'deviceSentMessage',
];

const WRAPPERS = new Set(WRAPPER_KEYS);

const DROPPED_CONTENT_KEYS = [
  'reactionMessage',
  'protocolMessage',
  'pollUpdateMessage',
  'senderKeyDistributionMessage',
];

const MEDIA_TEXT_KEYS = ['imageMessage', 'videoMessage', 'documentMessage', 'audioMessage'];

const MAX_UNWRAP_DEPTH = 4;
const MAX_MENTIONS = 32;
const MAX_MESSAGE_ID_LENGTH = 200;
const MAX_TIMESTAMP_SKEW_MS = 24 * 60 * 60 * 1000;

function isObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value) {
  return typeof value === 'string' && value.length > 0;
}

/**
 * Normalize a WhatsApp JID: strip the device suffix (`:12`) and any duplicate
 * server part, and map the legacy `c.us` server to `s.whatsapp.net`.
 * Returns '' for anything that is not a usable `user@server` JID.
 */
export function normalizeJid(value) {
  if (typeof value !== 'string') return '';
  const trimmed = value.trim();
  if (!trimmed) return '';

  const at = trimmed.indexOf('@');
  if (at <= 0) return '';

  const user = trimmed.slice(0, at).split(':')[0].trim();
  if (!user) return '';

  let server = trimmed.slice(at + 1).trim();
  if (!server) return '';
  if (server.includes('@')) server = server.slice(0, server.indexOf('@'));
  if (server.includes(':')) server = server.split(':')[0];
  server = server.trim();
  if (!server) return '';

  if (server === 'c.us') server = 's.whatsapp.net';
  return `${user}@${server}`;
}

function userPart(jid) {
  const normalized = normalizeJid(jid);
  if (!normalized) return null;
  return normalized.slice(0, normalized.indexOf('@'));
}

function readTimestamp(value) {
  if (typeof value === 'number') return value;
  if (typeof value === 'bigint') return Number(value);
  if (typeof value === 'string' && value.trim() !== '') return Number(value);
  if (isObject(value)) {
    if (typeof value.toNumber === 'function') {
      try {
        return Number(value.toNumber());
      } catch {
        // fall through to valueOf
      }
    }
    if (typeof value.valueOf === 'function') {
      try {
        return Number(value.valueOf());
      } catch {
        return Number.NaN;
      }
    }
  }
  return Number.NaN;
}

function unwrapMessage(message) {
  let content = message;

  for (let depth = 0; depth < MAX_UNWRAP_DEPTH; depth += 1) {
    let wrapperKey = null;
    for (const key of WRAPPER_KEYS) {
      if (isObject(content[key])) {
        wrapperKey = key;
        break;
      }
    }
    if (!wrapperKey) return content;

    const inner = content[wrapperKey].message;
    if (!isObject(inner)) return null;
    content = inner;
  }

  // Still wrapped after the maximum depth: treat as unknown.
  for (const key of WRAPPER_KEYS) {
    if (isObject(content[key])) return null;
  }
  return content;
}

function extractText(content) {
  if (isNonEmptyString(content.conversation)) return content.conversation;

  if (isObject(content.extendedTextMessage) && isNonEmptyString(content.extendedTextMessage.text)) {
    return content.extendedTextMessage.text;
  }

  for (const key of MEDIA_TEXT_KEYS) {
    const media = content[key];
    if (isObject(media) && isNonEmptyString(media.caption)) return media.caption;
  }

  return '';
}

// Collect contextInfo objects from the innermost message and its direct
// children (imageMessage.contextInfo etc.). One level is enough: quotedMessage
// inside contextInfo must not contribute mentions or reply data.
function collectContextInfos(content, rootMessage) {
  const infos = [];

  const pushContext = (value) => {
    if (isObject(value) && isObject(value.contextInfo)) infos.push(value.contextInfo);
  };

  pushContext(content);
  for (const [key, value] of Object.entries(content)) {
    if (WRAPPERS.has(key)) continue;
    if (isObject(value)) pushContext(value);
  }

  if (isObject(content.messageContextInfo)) infos.push(content.messageContextInfo);
  if (rootMessage !== content && isObject(rootMessage.messageContextInfo)) {
    infos.push(rootMessage.messageContextInfo);
  }

  return infos;
}

function collectMentions(infos) {
  const seen = new Set();
  const mentions = [];

  for (const info of infos) {
    const listed = info.mentionedJid;
    if (!Array.isArray(listed)) continue;
    for (const raw of listed) {
      const normalized = normalizeJid(raw);
      if (!normalized || seen.has(normalized)) continue;
      seen.add(normalized);
      mentions.push(normalized);
      if (mentions.length >= MAX_MENTIONS) return mentions;
    }
  }

  return mentions;
}

function toBotUsers(botJids) {
  const users = new Set();
  if (!botJids || typeof botJids[Symbol.iterator] !== 'function') return users;
  for (const jid of botJids) {
    const user = userPart(jid);
    if (user) users.add(user);
  }
  return users;
}

function isReplyToBot(infos, botUsers) {
  if (botUsers.size === 0) return false;
  for (const info of infos) {
    const participant = userPart(info.participant);
    if (participant && botUsers.has(participant)) return true;

    const remote = userPart(info.remoteJid);
    if (remote && botUsers.has(remote)) return true;
  }
  return false;
}

function resolveSenderPn(resolvePn, jid) {
  if (typeof resolvePn !== 'function') return null;
  try {
    const resolved = resolvePn(jid);
    if (typeof resolved !== 'string') return null;
    const normalized = normalizeJid(resolved);
    if (!normalized || normalized.endsWith('@lid')) return null;
    return normalized;
  } catch {
    return null;
  }
}

function normalizeUnsafe(msg, ctx) {
  if (!isObject(msg) || !isObject(msg.key)) return null;

  const { key } = msg;
  const remoteRaw = key.remoteJid;
  const remoteJid = normalizeJid(remoteRaw);
  if (!remoteJid) return null;
  if (remoteJid.endsWith('@broadcast') || remoteJid.endsWith('@newsletter')) return null;

  const isGroup = remoteJid.endsWith('@g.us');
  if (isGroup && !isNonEmptyString(key.participant)) return null;

  if (key.fromMe === true) return null;
  if (!isObject(msg.message)) return null;
  if (msg.messageStubType !== undefined && msg.messageStubType !== null) return null;

  const rootMessage = msg.message;
  if (rootMessage.messageStubType !== undefined && rootMessage.messageStubType !== null) return null;

  for (const dropKey of DROPPED_CONTENT_KEYS) {
    if (isObject(rootMessage[dropKey])) return null;
  }

  const content = unwrapMessage(rootMessage);
  if (!isObject(content)) return null;

  if (content.messageStubType !== undefined && content.messageStubType !== null) return null;
  for (const dropKey of DROPPED_CONTENT_KEYS) {
    if (isObject(content[dropKey])) return null;
  }

  const messageId = key.id;
  if (typeof messageId !== 'string' || messageId.trim() === '' || messageId.length > MAX_MESSAGE_ID_LENGTH) {
    return null;
  }

  const senderRaw = isGroup ? key.participant : remoteRaw;
  let senderId = normalizeJid(senderRaw) || remoteJid;
  if (senderId.endsWith('@lid')) {
    const resolved = resolveSenderPn(ctx && ctx.resolvePn, senderId);
    if (resolved) senderId = resolved;
  }

  const infos = collectContextInfos(content, rootMessage);
  const mentions = collectMentions(infos);
  const replyToBot = isReplyToBot(infos, toBotUsers(ctx && ctx.botJids));

  const event = {
    message_id: messageId,
    chat_id: remoteJid,
    sender_id: senderId,
    text: extractText(content),
    is_group: isGroup,
    mentions,
    reply_to_bot: replyToBot,
  };

  const timestamp = readTimestamp(msg.messageTimestamp);
  if (Number.isFinite(timestamp) && timestamp > 0) {
    let nowMs = Number.NaN;
    if (ctx && typeof ctx.nowMs === 'function') {
      try {
        nowMs = Number(ctx.nowMs());
      } catch {
        nowMs = Number.NaN;
      }
    }
    if (Number.isFinite(nowMs) && Math.abs(timestamp * 1000 - nowMs) <= MAX_TIMESTAMP_SKEW_MS) {
      event.timestamp = timestamp;
    }
  }

  return event;
}

/**
 * Normalize a Baileys message into an inbound event, or null when it is not a
 * user turn the bridge should forward. Never throws.
 */
export function normalizeMessage(msg, ctx) {
  try {
    return normalizeUnsafe(msg, ctx);
  } catch {
    return null;
  }
}
