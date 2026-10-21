import test from 'node:test';
import assert from 'node:assert/strict';

import { normalizeMessage } from '../src/normalize.js';

const NOW_S = 1_700_000_000;
const NOW_MS = NOW_S * 1000;

function makeCtx(overrides = {}) {
  return {
    botJids: new Set(['628111@s.whatsapp.net', '777@lid']),
    resolvePn: () => null,
    nowMs: () => NOW_MS,
    ...overrides,
  };
}

function makeMessage({ key = {}, message, messageTimestamp = NOW_S, ...rest } = {}) {
  return {
    key: {
      remoteJid: '628123@s.whatsapp.net',
      fromMe: false,
      id: '3EB0ABCDEF',
      ...key,
    },
    message,
    messageTimestamp,
    ...rest,
  };
}

function baseEvent(overrides = {}) {
  return {
    message_id: '3EB0ABCDEF',
    chat_id: '628123@s.whatsapp.net',
    sender_id: '628123@s.whatsapp.net',
    sender_name: '',
    text: '',
    is_group: false,
    mentions: [],
    mention_names: [],
    reply_to_bot: false,
    quoted_text: '',
    quoted_sender_name: '',
    timestamp: NOW_S,
    ...overrides,
  };
}

test('normalizes a DM conversation with all contract fields', () => {
  const msg = makeMessage({ message: { conversation: 'halo sanka' } });
  assert.deepEqual(normalizeMessage(msg, makeCtx()), baseEvent({ text: 'halo sanka' }));
});

test('only the documented contract fields are emitted', () => {
  const event = normalizeMessage(makeMessage({ message: { conversation: 'x' } }), makeCtx());
  assert.deepEqual(Object.keys(event), [
    'message_id',
    'chat_id',
    'sender_id',
    'sender_name',
    'text',
    'is_group',
    'mentions',
    'mention_names',
    'reply_to_bot',
    'quoted_text',
    'quoted_sender_name',
    'timestamp',
  ]);
});

test('normalizes a group message using participant as sender', () => {
  const msg = makeMessage({
    key: {
      remoteJid: '120363000000000000@g.us',
      participant: '628456:12@s.whatsapp.net',
    },
    message: { extendedTextMessage: { text: 'pagi semua' } },
  });

  assert.deepEqual(
    normalizeMessage(msg, makeCtx()),
    baseEvent({
      chat_id: '120363000000000000@g.us',
      sender_id: '628456@s.whatsapp.net',
      text: 'pagi semua',
      is_group: true,
    }),
  );
});

test('strips device suffix, duplicate server, and c.us from chat_id', () => {
  const device = normalizeMessage(
    makeMessage({ key: { remoteJid: '628123:7@s.whatsapp.net' }, message: { conversation: 'a' } }),
    makeCtx(),
  );
  assert.equal(device.chat_id, '628123@s.whatsapp.net');

  const duplicate = normalizeMessage(
    makeMessage({ key: { remoteJid: '628123@s.whatsapp.net@s.whatsapp.net' }, message: { conversation: 'a' } }),
    makeCtx(),
  );
  assert.equal(duplicate.chat_id, '628123@s.whatsapp.net');

  const legacy = normalizeMessage(
    makeMessage({ key: { remoteJid: '628123@c.us' }, message: { conversation: 'a' } }),
    makeCtx(),
  );
  assert.equal(legacy.chat_id, '628123@s.whatsapp.net');
});

test('dedupes mentions, caps at 32, and preserves @sanka in the text', () => {
  const rawMentions = [];
  for (let i = 0; i < 40; i += 1) {
    rawMentions.push(`6280${String(i).padStart(2, '0')}:3@s.whatsapp.net`);
  }
  rawMentions.splice(3, 0, '628003@s.whatsapp.net');
  rawMentions.push('', '@s.whatsapp.net', 'not-a-jid');

  const msg = makeMessage({
    key: { remoteJid: '120363000000000000@g.us', participant: '628456@s.whatsapp.net' },
    message: {
      extendedTextMessage: {
        text: 'halo @sanka cek ini',
        contextInfo: { mentionedJid: rawMentions },
      },
    },
  });

  const event = normalizeMessage(msg, makeCtx());
  assert.equal(event.text, 'halo @sanka cek ini');
  assert.equal(event.mentions.length, 32);
  assert.equal(new Set(event.mentions).size, 32);
  assert.equal(event.mentions[0], '628000@s.whatsapp.net');
  assert.equal(event.mentions[31], '628031@s.whatsapp.net');
  assert.ok(event.mentions.every((jid) => jid.endsWith('@s.whatsapp.net')));
});

test('collects mentions from media contextInfo and messageContextInfo', () => {
  const msg = makeMessage({
    message: {
      imageMessage: { caption: 'lihat', contextInfo: { mentionedJid: ['628999:1@s.whatsapp.net'] } },
      messageContextInfo: { mentionedJid: ['629000@s.whatsapp.net', '628999@s.whatsapp.net'] },
    },
  });

  const event = normalizeMessage(msg, makeCtx());
  assert.deepEqual(event.mentions, ['628999@s.whatsapp.net', '629000@s.whatsapp.net']);
});

test('reply_to_bot is true for a quoted bot PN and a quoted bot LID', () => {
  const pnReply = makeMessage({
    message: {
      extendedTextMessage: {
        text: 'jawab',
        contextInfo: { participant: '628111:5@s.whatsapp.net' },
      },
    },
  });
  assert.equal(normalizeMessage(pnReply, makeCtx()).reply_to_bot, true);

  const lidReply = makeMessage({
    message: {
      extendedTextMessage: {
        text: 'jawab',
        contextInfo: { participant: '777@lid' },
      },
    },
  });
  assert.equal(normalizeMessage(lidReply, makeCtx()).reply_to_bot, true);
});

test('reply_to_bot is true when contextInfo.remoteJid is the bot', () => {
  const msg = makeMessage({
    message: {
      extendedTextMessage: {
        text: 'catatan sendiri',
        contextInfo: { remoteJid: '628111@s.whatsapp.net' },
      },
    },
  });
  assert.equal(normalizeMessage(msg, makeCtx()).reply_to_bot, true);
});

test('reply_to_bot is false for a quoted other user or missing context', () => {
  const other = makeMessage({
    message: {
      extendedTextMessage: {
        text: 'jawab',
        contextInfo: { participant: '628999@s.whatsapp.net', remoteJid: '628999@s.whatsapp.net' },
      },
    },
  });
  assert.equal(normalizeMessage(other, makeCtx()).reply_to_bot, false);

  const plain = makeMessage({ message: { conversation: 'biasa' } });
  assert.equal(normalizeMessage(plain, makeCtx()).reply_to_bot, false);
});

test('unwraps ephemeral, view-once, extension, document caption, and device-sent wrappers', () => {
  const ephemeral = makeMessage({
    message: { ephemeralMessage: { message: { conversation: 'ephemeral' } } },
  });
  assert.equal(normalizeMessage(ephemeral, makeCtx()).text, 'ephemeral');

  const viewOnce = makeMessage({
    message: { viewOnceMessage: { message: { imageMessage: { caption: 'view once' } } } },
  });
  assert.equal(normalizeMessage(viewOnce, makeCtx()).text, 'view once');

  const viewOnceV2 = makeMessage({
    message: { viewOnceMessageV2: { message: { videoMessage: { caption: 'view once v2' } } } },
  });
  assert.equal(normalizeMessage(viewOnceV2, makeCtx()).text, 'view once v2');

  const extension = makeMessage({
    message: { viewOnceMessageV2Extension: { message: { imageMessage: { caption: 'extension' } } } },
  });
  assert.equal(normalizeMessage(extension, makeCtx()).text, 'extension');

  const document = makeMessage({
    message: { documentWithCaptionMessage: { message: { documentMessage: { caption: 'document' } } } },
  });
  assert.equal(normalizeMessage(document, makeCtx()).text, 'document');

  const deviceSent = makeMessage({
    message: { deviceSentMessage: { destinationJid: '628123@s.whatsapp.net', message: { conversation: 'sent' } } },
  });
  assert.equal(normalizeMessage(deviceSent, makeCtx()).text, 'sent');
});

test('unwraps at most four wrappers and rejects deeper nesting', () => {
  const four = makeMessage({
    message: {
      ephemeralMessage: {
        message: {
          viewOnceMessage: {
            message: {
              viewOnceMessageV2: {
                message: {
                  documentWithCaptionMessage: {
                    message: { documentMessage: { caption: 'deep but allowed' } },
                  },
                },
              },
            },
          },
        },
      },
    },
  });
  assert.equal(normalizeMessage(four, makeCtx()).text, 'deep but allowed');

  const five = makeMessage({
    message: {
      ephemeralMessage: {
        message: {
          viewOnceMessage: {
            message: {
              viewOnceMessageV2: {
                message: {
                  documentWithCaptionMessage: {
                    message: {
                      deviceSentMessage: { message: { conversation: 'too deep' } },
                    },
                  },
                },
              },
            },
          },
        },
      },
    },
  });
  assert.equal(normalizeMessage(five, makeCtx()), null);
});

test('media without a caption produces empty text, not a synthetic marker', () => {
  const image = makeMessage({ message: { imageMessage: { mimetype: 'image/jpeg' } } });
  const event = normalizeMessage(image, makeCtx());
  assert.equal(event.text, '');
  assert.equal(normalizeMessage(image, makeCtx()).message_id, '3EB0ABCDEF');
});

test('drops reactions, protocol messages, poll updates, and sender-key distributions', () => {
  const dropped = [
    { reactionMessage: { text: '❤', key: { id: 'x' } } },
    { protocolMessage: { type: 14 } },
    { pollUpdateMessage: { pollCreationMessageKey: { id: 'x' } } },
    { senderKeyDistributionMessage: { groupId: 'x' } },
  ];
  for (const message of dropped) {
    assert.equal(normalizeMessage(makeMessage({ message }), makeCtx()), null);
  }
});

test('drops fromMe, broadcast, newsletter, group without participant, null message, and stub types', () => {
  assert.equal(
    normalizeMessage(makeMessage({ key: { fromMe: true }, message: { conversation: 'echo' } }), makeCtx()),
    null,
  );
  assert.equal(
    normalizeMessage(
      makeMessage({ key: { remoteJid: 'status@broadcast' }, message: { conversation: 'status' } }),
      makeCtx(),
    ),
    null,
  );
  assert.equal(
    normalizeMessage(
      makeMessage({ key: { remoteJid: '120363000000000000@newsletter' }, message: { conversation: 'news' } }),
      makeCtx(),
    ),
    null,
  );
  assert.equal(
    normalizeMessage(
      makeMessage({ key: { remoteJid: '120363000000000000@g.us' }, message: { conversation: 'group' } }),
      makeCtx(),
    ),
    null,
  );
  assert.equal(normalizeMessage(makeMessage({ message: null }), makeCtx()), null);
  assert.equal(normalizeMessage(makeMessage({ message: undefined }), makeCtx()), null);
  assert.equal(
    normalizeMessage(makeMessage({ message: { conversation: 'stub' }, messageStubType: 2 }), makeCtx()),
    null,
  );
});

test('LID senders resolve to PN when possible and fall back to the LID otherwise', () => {
  const groupMessage = () =>
    makeMessage({
      key: { remoteJid: '120363000000000000@g.us', participant: '456@lid' },
      message: { conversation: 'hai' },
    });

  const resolved = normalizeMessage(groupMessage(), makeCtx({ resolvePn: () => '628456@s.whatsapp.net' }));
  assert.equal(resolved.sender_id, '628456@s.whatsapp.net');

  const unresolved = normalizeMessage(groupMessage(), makeCtx({ resolvePn: () => null }));
  assert.equal(unresolved.sender_id, '456@lid');

  const throwing = normalizeMessage(
    groupMessage(),
    makeCtx({
      resolvePn: () => {
        throw new Error('lookup failed');
      },
    }),
  );
  assert.equal(throwing.sender_id, '456@lid');

  const sameLid = normalizeMessage(groupMessage(), makeCtx({ resolvePn: () => '456@lid' }));
  assert.equal(sameLid.sender_id, '456@lid');

  const dm = makeMessage({ key: { remoteJid: '456@lid' }, message: { conversation: 'dm' } });
  assert.equal(normalizeMessage(dm, makeCtx({ resolvePn: () => '628456@s.whatsapp.net' })).sender_id, '628456@s.whatsapp.net');
});

test('omits a stale or unparsable timestamp and keeps one within 24 hours', () => {
  const fresh = normalizeMessage(
    makeMessage({ message: { conversation: 'x' }, messageTimestamp: NOW_S - 3600 }),
    makeCtx(),
  );
  assert.equal(fresh.timestamp, NOW_S - 3600);

  const old = normalizeMessage(
    makeMessage({ message: { conversation: 'x' }, messageTimestamp: NOW_S - 86401 }),
    makeCtx(),
  );
  assert.equal('timestamp' in old, false);

  const future = normalizeMessage(
    makeMessage({ message: { conversation: 'x' }, messageTimestamp: NOW_S + 86401 }),
    makeCtx(),
  );
  assert.equal('timestamp' in future, false);

  const garbage = normalizeMessage(
    makeMessage({ message: { conversation: 'x' }, messageTimestamp: 'not-a-number' }),
    makeCtx(),
  );
  assert.equal('timestamp' in garbage, false);

  const longLike = normalizeMessage(
    makeMessage({ message: { conversation: 'x' }, messageTimestamp: { toNumber: () => NOW_S } }),
    makeCtx(),
  );
  assert.equal(longLike.timestamp, NOW_S);

  const clockThrows = normalizeMessage(
    makeMessage({ message: { conversation: 'x' } }),
    makeCtx({
      nowMs: () => {
        throw new Error('clock');
      },
    }),
  );
  assert.equal('timestamp' in clockThrows, false);
});

test('returns null for missing, empty, non-string, or longer-than-200 message ids', () => {
  assert.equal(normalizeMessage(makeMessage({ key: { id: '' }, message: { conversation: 'x' } }), makeCtx()), null);
  assert.equal(normalizeMessage(makeMessage({ key: { id: '   ' }, message: { conversation: 'x' } }), makeCtx()), null);
  assert.equal(normalizeMessage(makeMessage({ key: { id: 123 }, message: { conversation: 'x' } }), makeCtx()), null);
  assert.equal(normalizeMessage(makeMessage({ key: { id: undefined }, message: { conversation: 'x' } }), makeCtx()), null);
  assert.equal(
    normalizeMessage(makeMessage({ key: { id: 'a'.repeat(201) }, message: { conversation: 'x' } }), makeCtx()),
    null,
  );

  const exactly200 = normalizeMessage(
    makeMessage({ key: { id: 'b'.repeat(200) }, message: { conversation: 'x' } }),
    makeCtx(),
  );
  assert.equal(exactly200.message_id, 'b'.repeat(200));
});

test('never throws on undefined or garbage input', () => {
  const inputs = [
    undefined,
    null,
    0,
    'garbage',
    [],
    {},
    { key: {} },
    { key: { remoteJid: 'garbage' }, message: { conversation: 'x' } },
    { key: { remoteJid: '1@s.whatsapp.net' } },
    { key: { remoteJid: '1@s.whatsapp.net' }, message: 'not-an-object' },
    { key: { remoteJid: '1@s.whatsapp.net' }, message: { conversation: 'x' }, messageTimestamp: { toNumber: 1 } },
    { key: { remoteJid: '1@g.us', participant: 'x@lid' }, message: { ephemeralMessage: { message: null } } },
  ];

  for (const input of inputs) {
    assert.doesNotThrow(() => normalizeMessage(input, makeCtx()));
  }
  assert.equal(normalizeMessage({ key: { remoteJid: '1@s.whatsapp.net' }, message: { conversation: 'ok' } }), null);
  assert.equal(
    normalizeMessage(
      { key: { remoteJid: '1@s.whatsapp.net', id: 'id-1' }, message: { conversation: 'ok' }, messageTimestamp: NOW_S },
      undefined,
    ).text,
    'ok',
  );
});

test('sanitizes sender_name from pushName', () => {
  const msg = makeMessage({
    message: { conversation: 'halo' },
    pushName: '  Budi\n\tSantoso  ',
  });

  assert.equal(normalizeMessage(msg, makeCtx()).sender_name, 'Budi Santoso');
});

test('includes quoted text and display name', () => {
  const msg = makeMessage({
    key: {
      remoteJid: '120363000000000000@g.us',
      participant: '628123@s.whatsapp.net',
    },
    message: {
      extendedTextMessage: {
        text: 'iya, setuju',
        contextInfo: {
          participant: '628456@s.whatsapp.net',
          quotedMessage: { conversation: 'rapat jam 10 ya' },
        },
      },
    },
  });

  const event = normalizeMessage(
    msg,
    makeCtx({ resolveName: (jid) => (jid === '628456@s.whatsapp.net' ? 'Budi' : '') }),
  );

  assert.equal(event.quoted_text, 'rapat jam 10 ya');
  assert.equal(event.quoted_sender_name, 'Budi');
});

test('includes mention display names', () => {
  const msg = makeMessage({
    key: {
      remoteJid: '120363000000000000@g.us',
      participant: '628123@s.whatsapp.net',
    },
    message: {
      extendedTextMessage: {
        text: '@Budi lihat ini',
        contextInfo: { mentionedJid: ['628456@s.whatsapp.net'] },
      },
    },
  });

  const event = normalizeMessage(
    msg,
    makeCtx({ resolveName: () => 'Budi' }),
  );

  assert.deepEqual(event.mentions, ['628456@s.whatsapp.net']);
  assert.deepEqual(event.mention_names, ['Budi']);
});
