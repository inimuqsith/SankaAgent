// Shared test doubles for the Sanka WhatsApp bridge.
// Keep this file dependency-free: every test module imports these helpers.

export function createFakeClock(startMs = 0) {
  let current = startMs;
  return {
    now() {
      return current;
    },
    advance(ms) {
      current += ms;
      return current;
    },
  };
}

export function createFakeLogger() {
  const records = [];

  const make = (bindings) => {
    const push = (level) => (first, second) => {
      let msg;
      let fields;
      if (typeof first === 'string') {
        msg = first;
        fields = second && typeof second === 'object' ? second : {};
      } else {
        fields = first && typeof first === 'object' ? first : {};
        msg = typeof second === 'string' ? second : undefined;
      }
      records.push({ level, msg, ...bindings, ...fields });
    };
    return {
      records,
      debug: push('debug'),
      info: push('info'),
      warn: push('warn'),
      error: push('error'),
      child(extra = {}) {
        return make({ ...bindings, ...extra });
      },
    };
  };

  return make({});
}

export function createFakeTransport() {
  const sent = [];
  const presenceCalls = [];
  const readCalls = [];
  let connected = true;
  let nextId = 0;

  return {
    sent,
    presenceCalls,
    readCalls,
    get connected() {
      return connected;
    },
    set connected(value) {
      connected = Boolean(value);
    },
    async sendText(chatId, text) {
      const id = `fake-${++nextId}`;
      sent.push({ chatId, text, id });
      return { id };
    },
    async presence(chatId, state) {
      presenceCalls.push({ chatId, state });
    },
    async readMessages(keys) {
      readCalls.push(keys);
    },
    isConnected() {
      return connected;
    },
  };
}

export function jsonResponse(status, body) {
  return { status, body };
}

export function startTestServer(server) {
  return new Promise((resolve, reject) => {
    const onError = (error) => {
      server.off('error', onError);
      reject(error);
    };
    server.once('error', onError);
    server.listen(0, '127.0.0.1', () => {
      server.off('error', onError);
      const address = server.address();
      resolve({
        port: address.port,
        close: () =>
          new Promise((done) => {
            if (typeof server.closeAllConnections === 'function') {
              server.closeAllConnections();
            }
            server.close(() => done());
          }),
      });
    });
  });
}
