// Actual application factory + published driver; synthetic transport, no executable/auth/network.
import assert from 'node:assert/strict';
import { CodexDriver } from './node_modules/@particle-academy/prism-acp/dist/index.js';
import { serveProfile } from './host.mjs';
const profile = JSON.parse(process.env.MAIPA_ACP_PROFILE);
const threadId = '00000000-0000-4000-8000-000000000029';
const host = serveProfile(profile, { input: process.stdin, output: process.stdout,
  constructors: { codex: (options, events) => {
    assert.equal(options.nativeTools, 'none');
    assert.equal(options.model, 'gpt-5.3-codex');
    assert.equal(options.binary, profile.provider_binary);
    assert.equal(options.resumeSessionId, undefined);
    assert.deepEqual(options.mcpServers, []);
    return new CodexDriver(options, events, (_config, callbacks) => ({
      start: async () => {}, close() {},
      // This transport never creates a child; its synthetic tree is empty.
      stop: async () => ({ verified: true, empty: true, survivors: 0 }),
      send(frame) {
        if (frame.id === undefined || !frame.method) return;
        let result;
        switch (frame.method) {
          case 'initialize': result = { userAgent: 'codex_cli_rs/0.163.0 (synthetic transport)' }; break;
          case 'config/read': result = { config: {} }; break;
          case 'thread/start':
            assert.equal(frame.params.model, 'gpt-5.3-codex');
            assert.equal(frame.params.config.features.shell_tool, false);
            assert.equal(frame.params.config.features.multi_agent, false);
            assert.equal(frame.params.config.web_search, 'disabled');
            result = { thread: { id: threadId }, model: 'gpt-5.3-codex' }; break;
          case 'account/rateLimits/read': result = {}; break;
          case 'model/list': result = { data: [], nextCursor: null }; break;
          default: throw Error('Unexpected synthetic restricted transport method');
        }
        queueMicrotask(() => callbacks.onFrame({ jsonrpc: '2.0', id: frame.id, result }));
      },
    }));
  } },
});
await host.closed;
await host.shutdown();
