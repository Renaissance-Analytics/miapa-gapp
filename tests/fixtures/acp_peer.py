"""Synthetic bidirectional wire peer. Never a provider or product fallback."""
import json
import os
import sys
import time

mode = sys.argv[1]
pending = []


def send(value):
    sys.stdout.write(json.dumps(value) + "\r\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": 1}})
    elif method == "echo":
        pending.append(message)
        if len(pending) == 2:
            for item in reversed(pending):
                send({"jsonrpc": "2.0", "id": item["id"], "result": item["params"]})
            pending.clear()
    elif method == "session/new":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"sessionId": "acp-local-1", "received": message["params"]}})
    elif method == "session/prompt":
        prompt_id = message["id"]
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "foreign-session", "update": {"content": "must-not-route"}}})
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "acp-local-1", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "synthetic reply"}}}})
        send({"jsonrpc": "2.0", "id": "permission-1", "method": "session/request_permission", "params": {"sessionId": "acp-local-1", "options": [{"optionId": "allow", "kind": "allow_once"}]}})
        send({"jsonrpc": "2.0", "id": "client-unknown", "method": "terminal/create", "params": {"command": "do-not-run"}})
    elif message.get("id") == "permission-1" and "result" in message:
        permission = message["result"]
    elif message.get("id") == "client-unknown" and "error" in message:
        send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn", "permission": permission, "unsupported": message["error"]["code"]}})
    elif method == "break":
        if mode == "malformed":
            sys.stdout.write("private-invalid-json\n")
        elif mode == "oversized":
            sys.stdout.write("x" * 5000 + "\n")
        elif mode == "hang":
            time.sleep(30)
        elif mode == "duplicate":
            sys.stdout.write('{"jsonrpc":"2.0","id":' + str(message["id"]) + ',"result":1,"result":2}\n')
        elif mode == "bool-id":
            send({"jsonrpc": "2.0", "id": True, "result": {}})
        elif mode == "unknown-id":
            send({"jsonrpc": "2.0", "id": 99999, "result": {}})
        else:
            sys.stderr.write("synthetic-private-diagnostic")
            sys.stderr.flush()
            sys.exit(7)
        sys.stdout.flush()
    elif method == "inspect-environment":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"inherited": sorted(name for name in os.environ if name in {"OPENAI_API_KEY", "NODE_OPTIONS", "PYTHONPATH", "MAIPA_SECRET_KEY"})}})
    elif method == "maipa/shutdown":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"verified": True, "survivors": 0, "sessions": 0}})
