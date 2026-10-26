// Application MCP adapter using Prism's public JSON-RPC contract. No network
// listener, model-selected executable, browser credential or package patch.
import { createServer, createConnection } from 'node:net';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JsonRpcPeer, RpcError } from '@particle-academy/prism-acp';

const MESSAGE = 'UI tool unavailable';
const unavailable = () => new RpcError(-32000, MESSAGE);
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => record(value) && Object.keys(value).length === keys.length
  && keys.every(key => Object.hasOwn(value, key));

/** A bounded app transport around the published, transport-neutral RPC peer. */
export function rpcStreams(input, output, { readLimit = 65_536, writeLimit = 65_536 } = {}) {
  if (![65_536, 131_072].includes(readLimit) || ![65_536, 131_072].includes(writeLimit)) throw unavailable();
  let buffer = Buffer.alloc(0), ended = false, drainTimer;
  let finish;
  const closed = new Promise(done => { finish = done; });
  const close = () => {
    if (ended) return;
    ended = true;
    clearTimeout(drainTimer);
    peer.fail(unavailable());
    input.destroy();
    output.destroy();
    finish();
  };
  const peer = new JsonRpcPeer({ send: value => {
    let wire;
    try { wire = Buffer.from(JSON.stringify(value) + '\n', 'utf8'); }
    catch { close(); throw unavailable(); }
    if (ended || wire.length > writeLimit || output.writableLength + wire.length > 262_144) {
      close();
      throw unavailable();
    }
    if (!output.write(wire) && !drainTimer) {
      drainTimer = setTimeout(close, 5000);
      drainTimer.unref();
    }
  }, onProtocolError: close });
  output.on('drain', () => { clearTimeout(drainTimer); drainTimer = undefined; });
  input.on('data', chunk => {
    if (ended) return;
    const raw = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    if (buffer.length + raw.length > 262_144) return close();
    buffer = Buffer.concat([buffer, raw]);
    let newline;
    while ((newline = buffer.indexOf(10)) >= 0) {
      if (newline + 1 > readLimit) return close();
      const line = buffer.subarray(0, newline);
      buffer = buffer.subarray(newline + 1);
      try {
        const text = new TextDecoder('utf-8', { fatal: true }).decode(line);
        if (!text.trim()) continue;
        void peer.receive(JSON.parse(text)).catch(close);
      } catch { return close(); }
    }
    if (buffer.length > readLimit) close();
  });
  for (const stream of new Set([input, output])) {
    stream.on('error', close);
    stream.on('close', close);
  }
  input.on('end', close);
  return { peer, close, closed };
}

function checkedRequest(value) {
  if (!exact(value, ['method', 'params'])) throw unavailable();
  if (value.method === 'tools/list' && exact(value.params, [])) return value;
  if (value.method === 'tools/call' && exact(value.params, ['name', 'arguments'])
      && typeof value.params.name === 'string' && value.params.name.length <= 80
      && record(value.params.arguments)) return value;
  throw unavailable();
}

async function deadline(operation, close) {
  let timer;
  try {
    return await Promise.race([operation(), new Promise((_, reject) => {
      timer = setTimeout(() => { close(); reject(unavailable()); }, 30_000);
      timer.unref();
    })]);
  } catch { throw unavailable(); }
  finally { clearTimeout(timer); }
}

/** Both peers run under the SAME AppContainer SID. Python remains the authority. */
export async function createUiRelay(forward) {
  if (process.platform !== 'win32' || typeof forward !== 'function') throw unavailable();
  const nonce = randomBytes(32).toString('hex');
  const path = '\\\\.\\pipe\\LOCAL\\maipa-ui-' + randomBytes(24).toString('hex');
  const channels = new Set();
  let closing = false, pending = 0;
  const server = createServer(socket => {
    if (closing || channels.size >= 4) return socket.destroy();
    const channel = rpcStreams(socket, socket);
    channels.add(channel);
    void channel.closed.then(() => channels.delete(channel));
    channel.peer.handle('maipa/ui', async value => {
      if (closing || pending >= 8 || !exact(value, ['nonce', 'request'])
          || typeof value.nonce !== 'string' || !/^[0-9a-f]{64}$/.test(value.nonce)
          || !timingSafeEqual(Buffer.from(value.nonce, 'hex'), Buffer.from(nonce, 'hex'))) throw unavailable();
      const request = checkedRequest(value.request);
      pending++;
      try {
        return await deadline(() => forward(request), channel.close);
      } catch { throw unavailable(); }
      finally { pending--; }
    });
  });
  server.maxConnections = 4;
  await new Promise((done, reject) => {
    server.once('error', () => reject(unavailable()));
    server.listen(path, done);
  });
  // Runtime failures close this adapter; no error text or request is logged.
  server.on('error', () => { for (const channel of channels) channel.close(); });
  return {
    mcp: Object.freeze({ type: 'stdio', name: 'maipa_ui', command: process.execPath,
      args: Object.freeze(['--preserve-symlinks', '--preserve-symlinks-main', fileURLToPath(import.meta.url), path, nonce]), env: [] }),
    async close() {
      if (closing) return;
      closing = true;
      for (const channel of channels) channel.close();
      await new Promise(done => server.close(done));
    },
  };
}

async function main() {
  const [path, nonce, ...extra] = process.argv.slice(2);
  if (extra.length || !/^\\\\\.\\pipe\\LOCAL\\maipa-ui-[0-9a-f]{48}$/.test(path ?? '')
      || !/^[0-9a-f]{64}$/.test(nonce ?? '')) throw unavailable();
  const socket = createConnection(path);
  const relay = rpcStreams(socket, socket);
  // MCP text and structuredContent both carry the same accepted32KiB state.
  // Only the reply allowance is larger; provider requests stay capped64KiB.
  const mcp = rpcStreams(process.stdin, process.stdout, { writeLimit: 131_072 });
  let pending = 0;
  const closed = () => { relay.close(); mcp.close(); };
  void relay.closed.then(closed);
  void mcp.closed.then(closed);
  mcp.peer.handle('initialize', params => {
    if (!record(params) || !['2024-11-05', '2025-03-26', '2025-06-18'].includes(params.protocolVersion)) throw unavailable();
    return { protocolVersion: params.protocolVersion, capabilities: { tools: {} },
      serverInfo: { name: 'maipa-ui', version: '0.1.0' } };
  });
  mcp.peer.handle('ping', () => ({}));
  for (const method of ['tools/list', 'tools/call']) {
    mcp.peer.handle(method, async params => {
      if (pending >= 8) throw unavailable();
      const request = checkedRequest({ method, params });
      pending++;
      try {
        const result = await deadline(() => relay.peer.request('maipa/ui', { nonce, request }), closed);
        return method === 'tools/list' ? result : {
          content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result, isError: false,
        };
      } catch { throw unavailable(); }
      finally { pending--; }
    });
  }
  await mcp.closed;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(() => { process.stderr.write(MESSAGE + '\n'); process.exitCode = 1; });
}
