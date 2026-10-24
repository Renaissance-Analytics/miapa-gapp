// Synthetic Codex transport under the actual published CodexDriver and Maipa
// host. It never starts a provider CLI, uses a login, or makes a model call.
import { CodexDriver } from "../../backend/agent/node_modules/@particle-academy/prism-acp/dist/index.js";
import { serveProfile } from "../../backend/agent/host.mjs";
const profile = JSON.parse(process.env.MAIPA_ACP_PROFILE);
const threadId = "00000000-0000-4000-8000-000000000001";
const host = serveProfile(profile, { input: process.stdin, output: process.stdout,
  constructors: { codex: (options, events) => new CodexDriver(options, events, (_config, callbacks) => ({
    start: async () => {}, close() {},
    send(frame) {
      if (frame.id === undefined || !frame.method) return;
      let result;
      switch (frame.method) {
        case "initialize": result = {}; break;
        case "thread/start": result = { thread: { id: threadId } }; break;
        case "account/rateLimits/read": result = {}; break;
        case "model/list": result = { data: [], nextCursor: null }; break;
        case "turn/start": result = { turn: { id: "fixture-turn" } }; break;
        default: throw new Error("Unexpected synthetic transport method");
      }
      queueMicrotask(() => {
        callbacks.onFrame({ jsonrpc: "2.0", id: frame.id, result });
        if (frame.method === "turn/start") {
          callbacks.onFrame({ jsonrpc: "2.0", method: "item/agentMessage/delta", params: { threadId, turnId: "fixture-turn", itemId: "fixture-message", delta: "Synthetic ACP round trip" } });
          callbacks.onFrame({ jsonrpc: "2.0", method: "turn/completed", params: { threadId, turn: { id: "fixture-turn", status: "completed" } } });
        }
      });
    },
  })) },
});
await host.closed;
await host.shutdown();
