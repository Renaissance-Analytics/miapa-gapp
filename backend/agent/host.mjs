import { readFileSync, lstatSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  serve, ClaudeDriver, CodexDriver, prepareProcessTree,
  CLAUDE_DRIVER_CAPABILITIES, CODEX_DRIVER_CAPABILITIES,
} from "@particle-academy/prism-acp";

const packageVersion = JSON.parse(readFileSync(new URL("./node_modules/@particle-academy/prism-acp/package.json", import.meta.url), "utf8")).version;
if (packageVersion !== "0.24.1") throw new Error("Unexpected ACP runtime");

export function validateProfile(value) {
  if (!value || !["claude", "codex"].includes(value.provider) ||
      ["user_id", "account_id"].some(key => typeof value[key] !== "string" || !value[key].trim() || value[key].length > 200)) {
    throw new Error("Invalid worker profile");
  }
  const result = { provider: value.provider, user_id: value.user_id, account_id: value.account_id };
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
export function serveProfile(value, { input, output, constructors = {} }) {
  const profile = validateProfile(value);
  const drivers = [];
  let closing = false, shutdownPromise;
  const parentEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    ["PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"].includes(key.toUpperCase())));
  Object.assign(parentEnv, { HOME: profile.home, USERPROFILE: profile.home,
    [profile.provider === "codex" ? "CODEX_HOME" : "CLAUDE_CONFIG_DIR"]: profile.home });
  const served = serve({
    input, output,
    agentInfo: { name: "maipa-prism-acp", title: "Maipa ACP host", version: packageVersion },
    driverCapabilities: profile.provider === "codex" ? CODEX_DRIVER_CAPABILITIES : CLAUDE_DRIVER_CAPABILITIES,
    driverFactory(options, events) {
      if (closing || options.cwd !== profile.workspace || options.resumeSessionId || options.mcpServers?.length) {
        throw new Error("Worker session refused");
      }
      const configured = { ...options, cwd: profile.workspace, parentEnv,
        credentials: { mode: "subscription" } };
      const driver = profile.provider === "codex"
        ? (constructors.codex ?? ((config, callbacks) => new CodexDriver(config, callbacks)))({
            ...configured, approvalPolicy: "untrusted", sandbox: "read-only" }, events)
        : (constructors.claude ?? ((config, callbacks) => new ClaudeDriver(config, callbacks)))({
            ...configured, permissionMode: "plan",
            disallowedTools: ["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"] }, events);
      drivers.push(driver);
      return driver;
    },
    // Provider stderr/protocol detail never goes to the application/browser.
    onStderr() {}, onProtocolError() {},
  });
  async function shutdown() {
    closing = true;
    shutdownPromise ??= (async () => {
      const results = await Promise.allSettled(drivers.map(driver => driver.stop({ graceMs: 1000 })));
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
  return { ...served, shutdown };
}

async function main() {
  const raw = process.env.MAIPA_ACP_PROFILE;
  if (!raw || Buffer.byteLength(raw) > 16384) throw new Error("Missing worker profile");
  const profile = validateProfile(JSON.parse(raw));
  // This prepares/verifies the published Windows supervisor before a provider
  // may start. It is lifetime containment, not an OS data-access boundary.
  await prepareProcessTree({ directory: profile.supervisor_directory });
  const host = serveProfile(profile, { input: process.stdin, output: process.stdout });
  await host.closed;
  const verdict = await host.shutdown();
  if (!verdict.verified) process.exitCode = 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(() => { process.stderr.write("ACP host unavailable\n"); process.exitCode = 1; });
}
