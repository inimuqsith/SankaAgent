// Upload one attachment to the Python gateway (plan-12).
//
// The response body is parsed only to obtain the attachment id. Logs never
// include the filename, MIME, bytes, or token; the caller logs short codes.

const UPLOAD_TIMEOUT_MS = 30_000;

/**
 * @param {object} options
 * @param {string} options.url gateway /v1/attachments URL
 * @param {string} options.token shared gateway token
 * @param {Buffer} options.buffer media bytes
 * @param {string} options.chatId
 * @param {string} options.senderId
 * @param {string} options.messageId
 * @param {string} options.kind image|document
 * @param {string} options.mime
 * @param {string} [options.name]
 * @param {string} options.sha256 lowercase hex
 * @param {typeof fetch} [options.fetchImpl]
 * @param {number} [options.timeoutMs]
 * @returns {Promise<{status:number, body?:any}>}
 */
export async function uploadAttachment({
  url,
  token,
  buffer,
  chatId,
  senderId,
  messageId,
  kind,
  mime,
  name = '',
  sha256,
  fetchImpl = fetch,
  timeoutMs = UPLOAD_TIMEOUT_MS,
} = {}) {
  if (typeof url !== 'string' || url === '') throw new TypeError('upload: url is required');
  if (typeof token !== 'string' || token === '') throw new TypeError('upload: token is required');
  if (!Buffer.isBuffer(buffer) || buffer.length === 0) {
    throw new TypeError('upload: buffer is required');
  }
  if (typeof fetchImpl !== 'function') throw new TypeError('upload: fetch is required');

  const filename = Buffer.from(name, 'utf8').toString('base64url');
  const headers = {
    'X-Sanka-Token': token,
    'Content-Type': 'application/octet-stream',
    'X-Sanka-Chat-Id': chatId,
    'X-Sanka-Sender-Id': senderId,
    'X-Sanka-Message-Id': messageId,
    'X-Sanka-Kind': kind,
    'X-Sanka-Mimetype': mime,
    'X-Sanka-Filename-B64': filename,
    'X-Sanka-Sha256': sha256,
  };

  const response = await fetchImpl(url, {
    method: 'POST',
    headers,
    body: buffer,
    signal: AbortSignal.timeout(timeoutMs),
  });
  const raw = await response.text();
  let body = null;
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      body = null;
    }
  }
  return { status: response.status, body };
}
