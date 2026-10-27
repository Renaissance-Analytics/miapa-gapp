// Actual app host and published ClaudeDriver; finite synthetic CLI, no credentials/network/model.
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { ClaudeDriver, prepareProcessTree } from './node_modules/@particle-academy/prism-acp/dist/index.js';
import { serveProfile } from './host.mjs';
const profile = JSON.parse(process.env.MAIPA_ACP_PROFILE);
await prepareProcessTree({ directory: profile.supervisor_directory });
const host = serveProfile(profile, { input: process.stdin, output: process.stdout,
  constructors: { claude(options, events) {
    assert.equal(options.binary, profile.provider_binary);
    assert.equal(options.nativeTools, 'none');
    assert.equal(options.permissionMode, 'dontAsk');
    assert.equal(options.model, undefined);
    assert.equal(options.binaryArgs, undefined);
    assert.equal(options.resumeSessionId, undefined);
    assert.equal(options.parentEnv.CLAUDE_CONFIG_DIR, profile.home);
    assert.equal(options.parentEnv.CODEX_HOME, undefined);
    assert.deepEqual(options.allowedTools, []);
    assert.deepEqual(options.mcpServers, []);
    return new ClaudeDriver({ ...options, binary: process.execPath,
      binaryArgs: [fileURLToPath(new URL('./claude-policy-cli.mjs', import.meta.url)), profile.synthetic_claude_scenario] }, events);
  } },
});
await host.closed;
await host.shutdown();
