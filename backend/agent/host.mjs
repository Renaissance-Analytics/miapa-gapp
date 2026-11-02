import { readFileSync, lstatSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  serve, ClaudeDriver, CodexDriver, prepareProcessTree,
  CLAUDE_DRIVER_CAPABILITIES, CODEX_DRIVER_CAPABILITIES, CODEX_NATIVE_TOOLS_NONE,
} from "@particle-academy/prism-acp";

const packageVersion = JSON.parse(readFileSync(new URL("./node_modules/@particle-academy/prism-acp/package.json", import.meta.url), "utf8")).version;
if (packageVersion !== "0.33.1") throw new Error("Unexpected ACP runtime");
if (JSON.stringify(CODEX_NATIVE_TOOLS_NONE.cliVersions) !== '["0.163.0"]'
    || JSON.stringify(CODEX_NATIVE_TOOLS_NONE.models) !== '["gpt-5.3-codex"]') throw Error('Unexpected provider policy');

export function claudeUiTools(enabled) {
  return enabled ? [...['catalog', 'present', 'update', 'dismiss'].map(operation => `mcp__maipa_ui__maipa_ui_${operation}`),
    ...['list', 'save', 'change', 'open'].map(operation => `mcp__maipa_ui__maipa_views_${operation}`),
    ...['list', 'describe', 'get_value', 'get_values', 'set_value', 'set_values', 'focus', 'submit'].map(operation => `mcp__maipa_ui__form_${operation}`),
    ...['get_state','list_nodes','get_node','list_edges','get_node_schema','add_node','update_node','delete_node','connect','disconnect','run','cancel'].map(operation=>`mcp__maipa_ui__flow_${operation}`),
    ...['list','create','publish','runs'].map(operation=>`mcp__maipa_ui__maipa_workflow_${operation}`)] : [];
}

export function validateProfile(value) {
  if (!value || !["claude", "codex"].includes(value.provider) ||
      ["user_id", "account_id"].some(key => typeof value[key] !== "string" || !value[key].trim() || value[key].length > 200)) {
    throw new Error("Invalid worker profile");
  }
  const result = { provider: value.provider, user_id: value.user_id, account_id: value.account_id };
  if (value.execution_mode !== undefined && !['host', 'isolated'].includes(value.execution_mode)) throw Error('Invalid execution mode');
  result.execution_mode = value.execution_mode ?? 'isolated';
  if (value.ui_tools !== undefined && typeof value.ui_tools !== 'boolean') throw new Error('Invalid UI capability');
  result.ui_tools = value.ui_tools === true;
  if (value.provider_binary !== undefined) {
    const entry = value.provider === 'codex' ? './provider/vendor/x86_64-pc-windows-msvc/bin/codex.exe' : './provider/claude.exe';
    const expected = fileURLToPath(new URL(entry, import.meta.url));
    if (value.provider_binary !== expected || lstatSync(expected).isSymbolicLink() || !lstatSync(expected).isFile()) {
      throw new Error('Invalid provider executable');
    }
    result.provider_binary = expected;
  }
  for (const key of ["workspace", "home", "supervisor_directory"]) {
    const path = value[key];
    if (typeof path !== "string" || !isAbsolute(path) || lstatSync(path).isSymbolicLink() || !lstatSync(path).isDirectory()) {
      throw new Error("Invalid worker directory");
    }
    // The trusted Python launcher verifies every physical path component
    // before applying the container ACL. The worker can inspect the assigned
    // leaf, but cannot walk unrelated server/system parent directories.
    result[key] = resolve(path);
  }
  return Object.freeze(result);
}

// The optional constructors are an in-process test seam. CLI/environment input
// cannot replace the published drivers, executable, argv, policy or MCP list.
export function serveProfile(value, { input, output, constructors = {}, uiMcp, account }) {
  const profile = validateProfile(value);
  // Only the trusted bootstrap supplies this application-owned stdio adapter.
  // Exact published0.33.1 supports restricted host-user subscription reuse.
  if (profile.ui_tools !== Boolean(uiMcp)) throw new Error('UI transport unavailable');
  const drivers = [];
  let closing = false, shutdownPromise;
  let nativeState = 'starting', observedReady = false;
  const parentEnv = providerEnvironment(profile);
  if (profile.provider_binary) parentEnv.PATH = `${profile.provider === 'codex' ? fileURLToPath(new URL('./provider/vendor/x86_64-pc-windows-msvc/bin', import.meta.url)) : fileURLToPath(new URL('./provider', import.meta.url))};${process.env.SYSTEMROOT ?? 'C:/Windows'}/System32`;
  const served = serve({
    input, output,
    agentInfo: { name: "maipa-prism-acp", title: "Maipa ACP host", version: packageVersion },
    driverCapabilities: profile.provider === "codex" ? CODEX_DRIVER_CAPABILITIES : CLAUDE_DRIVER_CAPABILITIES,
    driverFactory(options, events) {
      if (closing || options.cwd !== profile.workspace || options.resumeSessionId || options.mcpServers?.length
          || ((uiMcp || profile.provider_binary) && drivers.length > 0)) {
        throw new Error("Worker session refused");
      }
      const configured = { cwd: profile.workspace, parentEnv,
        ...(profile.provider_binary ? { binary: profile.provider_binary,
          ...(profile.provider === 'codex' ? { binaryArgs: profile.execution_mode === 'host' ? ['-c', 'forced_login_method="chatgpt"'] : ['-c', 'forced_login_method="chatgpt"', '-c', 'cli_auth_credentials_store="file"'], model: 'gpt-5.3-codex' } : {}),
          // Only the exact published measured matrix can drive native sessions.
          // Neither browser/model input nor inherited CLI defaults select policy.
          nativeTools: 'none' } : {}),
        mcpServers: uiMcp ? [uiMcp] : [],
        ...(profile.execution_mode === 'host' ? {} : { processTree: { windowsStdio: 'local-pipes', startupTimeoutMs: 10000, ioTimeoutMs: 30000 } }),
        credentials: { mode: "subscription" } };
      const callbacks = profile.provider_binary ? { ...events,
        onProtocolError(...args) {
          nativeState = 'unavailable';
          events.onProtocolError?.(...args);
        },
        onExit(...args) {
          nativeState = 'unavailable';
          events.onExit?.(...args);
        },
      } : events;
      const driver = profile.provider === "codex"
        ? (constructors.codex ?? ((config, callbacks) => new CodexDriver(config, callbacks)))({
            ...configured, approvalPolicy: "untrusted", sandbox: "read-only",
            mcpToolApproval: uiMcp ? { maipa_ui: 'approve' } : {} }, callbacks)
        : (constructors.claude ?? ((config, callbacks) => new ClaudeDriver(config, callbacks)))({
            ...configured, permissionMode: "dontAsk",
            allowedTools: claudeUiTools(Boolean(uiMcp)) }, callbacks);
      drivers.push(driver);
      return driver;
    },
    // Provider stderr/protocol detail never goes to the application/browser.
    onStderr() {}, onProtocolError() {},
  });
  async function shutdown() {
    closing = true;
    nativeState = 'unavailable';
    shutdownPromise ??= (async () => {
      const results = await Promise.allSettled(drivers.map(driver => driver.stop({ graceMs: 1000 })));
      if (account) results.push(...await Promise.allSettled([account.stop()]));
      const known = results.every(item => item.status === "fulfilled" && Number.isInteger(item.value.survivors) && item.value.survivors >= 0);
      return {
        verified: results.every(item => item.status === "fulfilled" && item.value.verified === true && item.value.empty === true && item.value.survivors === 0),
        survivors: known ? results.reduce((sum, item) => sum + item.value.survivors, 0) : null,
        sessions: drivers.length,
      };
    })();
    return shutdownPromise;
  }
  served.peer.handle("maipa/shutdown", shutdown);
  if (profile.provider_binary) {
    served.peer.handle('maipa/native/status', params => {
      if (closing || drivers.length !== 1 || !params || typeof params !== 'object'
          || Array.isArray(params) || Object.keys(params).length) throw Error('Native session unavailable');
      // session/new allocates ACP state before asynchronous native startup. Read
      // the provider's public startup/model promise after start(), remembering failures:
      // the driver catches startup refusals, so settlement alone is insufficient.
      if (!observedReady) {
        observedReady = true;
        const driver = drivers[0];
        const ready = profile.provider === 'claude' ? driver.modelState() : driver.ready;
        if (!ready || typeof ready.then !== 'function') nativeState = 'unavailable';
        else ready.then(models => {
          if (!closing && nativeState !== 'unavailable') {
            // Claude announces its native ID/catalog after the first prompt.
            // Its public control-model promise validates pre-prompt startup.
            nativeState = profile.provider === 'claude'
              ? (Array.isArray(models?.availableModels) && models.availableModels.length > 0 ? 'ready' : 'unavailable')
              : (typeof driver.cliSessionId === 'string' && driver.cliSessionId.trim() ? 'ready' : 'unavailable');
          }
        }, () => { nativeState = 'unavailable'; });
      }
      // This immediate response keeps Stop independent of native startup time.
      // Never expose the native ID, raw driver errors or provider output.
      return { state: nativeState };
    });
  }
  if (account) {
    served.peer.handle('maipa/account/status', async params => {
      if (closing || !params || Object.keys(params).length) throw Error('Account unavailable');
      return account.status();
    });
    served.peer.handle('maipa/account/login', async params => {
      if (closing || drivers.length || !params || Object.keys(params).length) throw Error('Account unavailable');
      return account.start();
    });
    served.peer.handle('maipa/account/authorize', async params => {
      if (closing || drivers.length || profile.provider !== 'claude' || !params || Array.isArray(params)
          || Object.keys(params).length !== 1 || typeof params.code !== 'string') throw Error('Account unavailable');
      return account.authorize(params.code);
    });
  }
  return { ...served, shutdown };
}

export function providerEnvironment(profile) {
  const host = profile.execution_mode === 'host';
  const allowed = ['PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', ...(host ? ['USERPROFILE', 'LOCALAPPDATA', 'APPDATA'] : [])];
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => allowed.includes(key.toUpperCase())));
  if (host) {
    if (!env.USERPROFILE || !isAbsolute(env.USERPROFILE)) throw Error('Machine account environment unavailable');
    env.HOME = env.USERPROFILE;
  } else Object.assign(env, { HOME: profile.home, USERPROFILE: profile.home,
    LOCALAPPDATA: profile.home, APPDATA: profile.home,
    [profile.provider === 'codex' ? 'CODEX_HOME' : 'CLAUDE_CONFIG_DIR']: profile.home });
  return env;
}

async function main() {
  const raw = process.env.MAIPA_ACP_PROFILE;
  if (!raw || Buffer.byteLength(raw) > 16384) throw new Error("Missing worker profile");
  const profile = validateProfile(JSON.parse(raw));
  // This prepares/verifies the published Windows supervisor before a provider
  // may start. It is lifetime containment, not an OS data-access boundary.
  await prepareProcessTree({ directory: profile.supervisor_directory });
  let host, relay;
  try {
    if (profile.ui_tools) {
      const { createUiRelay } = await import('./ui-mcp.mjs');
      relay = await createUiRelay(request => host.peer.request('maipa/ui', request));
    }
    let account;
    if (profile.provider_binary) {
      const { subscriptionAccount } = await import('./account.mjs');
      const env = providerEnvironment(profile);
      env.PATH = `${process.env.SYSTEMROOT ?? 'C:/Windows'}/System32`;
      account = subscriptionAccount(profile, env);
    }
    host = serveProfile(profile, { input: process.stdin, output: process.stdout, uiMcp: relay?.mcp, account });
    await host.closed;
    const verdict = await host.shutdown();
    if (!verdict.verified) process.exitCode = 1;
  } finally {
    await relay?.close();
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(() => { process.stderr.write("ACP host unavailable\n"); process.exitCode = 1; });
}
