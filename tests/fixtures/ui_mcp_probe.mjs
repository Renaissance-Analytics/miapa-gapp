// Real MCP child/LOCAL pipe bytes and native stop verdict; synthetic tool data.
// This intentionally starts no vendor CLI, authentication or model call.
import { createUiRelay, rpcStreams } from './ui-mcp.mjs';
import { launchProvider, prepareProcessTree } from '@particle-academy/prism-acp';
import { dirname } from 'node:path';
const profile = JSON.parse(process.env.MAIPA_ACP_PROFILE);
// A fresh isolated worker must use its explicitly prepared read-only supervisor.
// Make the unrelated default cache unavailable, even on a warm development host.
process.env.LOCALAPPDATA = dirname(process.execPath);
await prepareProcessTree({ directory: profile.supervisor_directory });
const mode = process.argv[2];
const calls = [], verdicts = [];
const relay = await createUiRelay(async request => {
  calls.push(request);
  if (mode === 'errors') throw Error('synthetic-private-token-and-path');
  return request.method === 'tools/list'
    ? { tools: [{ name: 'synthetic_ui', description: 'Synthetic fixture', inputSchema: { type: 'object' } }] }
    : mode === 'large' ? { id: 'synthetic-ui-lease', revision: 1, composition: { title: 'Large valid composition',
      schema: { id: 'large', type: 'Maipa.Stack', children: Array.from({ length: 15 }, (_, i) => ({
        id: `note-${i}`, type: 'Maipa.Note', props: { text: '"'.repeat(1000) },
      })) } } } : { accepted: true, revision: 1 };
});

async function child(config, operation) {
  const provider = launchProvider(config.command, config.args, {
    cwd: profile.workspace,
    env: { ...process.env, HOME: profile.home, USERPROFILE: profile.home, CODEX_HOME: profile.home },
    windowsStdio: 'local-pipes', startupTimeoutMs: 5000, ioTimeoutMs: 5000,
  });
  await provider.started;
  const channel = rpcStreams(provider.io.stdout, provider.io.stdin, { readLimit: 131_072 });
  provider.io.stderr.resume();
  try {
    await operation(channel.peer);
  } finally {
    channel.close();
    verdicts.push(await provider.stop({ graceMs: 1000 }));
    await provider.closed;
  }
}

const results = {};
try {
  await child(relay.mcp, async peer => {
    results.initialize = await peer.request('initialize', { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'synthetic', version: '1' } });
    if (mode === 'normal' || mode === 'large') {
      results.list = await peer.request('tools/list', {});
      results.call = await peer.request('tools/call', { name: 'synthetic_ui', arguments: { revision: 0 } });
    } else {
      try { await peer.request('tools/list', {}); } catch (error) { results.error = error.message; }
      try { await peer.request('tools/call', { name: 'synthetic_ui', arguments: { excessive: 'x'.repeat(70_000) } }); }
      catch (error) { results.budget = error.message; }
    }
  });
  const wrong = { ...relay.mcp, args: [...relay.mcp.args.slice(0, -1), '0'.repeat(64)] };
  await child(wrong, async peer => {
    try { await peer.request('tools/list', {}); }
    catch (error) { results.foreign = error.message; }
  });
} finally {
  await relay.close();
}
process.stdout.write(JSON.stringify({ calls, results, verdicts }) + '\n');
