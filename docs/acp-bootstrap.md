# ACP bootstrap boundary

`backend/maipa/acp.py` is Maipa's Python-standard-library ACP client. It launches
an absolute trusted executable without a shell, with a fixed workspace and an
immutable application-user/provider-account scope. The Node entry
`backend/agent/host.mjs` consumes the released Prism ACP0.24.1 `serve`,
`ClaudeDriver` and `CodexDriver` APIs. No PHP, separate model SDK, patched
distribution or compatibility normalizer is involved.

Install its one-package runtime with `npm ci --ignore-scripts --prefix
backend/agent` before the normal alignment-first backend check. Integration
checks require Node22+; Windows needs .NET Framework's `csc.exe` to prepare the
published process supervisor. The real entry is tested with no provider session.
Another test runs the actual Python client, product host and published
CodexDriver over an explicitly synthetic in-memory Codex transport. It proves
the application protocol path, session ownership and standard chunk mapping;
it does not prove CLI availability, login, model access or live provider output.

Trusted backend code supplies `MAIPA_ACP_PROFILE` with `user_id`, `account_id`,
`provider` (`codex` or `claude`), and existing absolute `workspace`, `home` and
`supervisor_directory` paths. The Python client refuses a profile whose owner,
account or workspace differs from its bound scope. Browser/model input must not
construct the scope, launch command, profile, cwd, environment or MCP config.
Provider home selection never changes the backend's global environment.

The host forwards only a small OS environment set plus the selected home, uses
subscription credentials through the unmodified provider CLI, and rejects a
different cwd, explicit MCP entries and provider resume requests. Codex starts
with read-only sandbox/untrusted approvals; Claude starts in plan mode with
the listed mutation tools denied. These provider policies and home directories
are not OS data isolation or an exclusive MCP allowlist. Inherited provider
settings/plugins/MCP still need acceptance. No HTTP route or composer can start
provider sessions. Authenticated transport checks only initialize and stop the
host with zero providers; see [account transport](agent-account-transport.md).
The actual zero-provider transport now runs under the app-owned
[Windows boundary](windows-worker-isolation.md). Provider provisioning and
actual provider CLI/network/IPC acceptance inside that boundary remain required
before exposing provider sessions to application users.

Each client worker belongs to one user/account. Application session handles are
random and separate from ACP-local IDs. Owned new/prompt/cancel/update methods
check scope before sending or returning data. The low-level `_request` method
is internal protocol plumbing, not a web RPC gateway. These checks do not
authenticate a caller: future HTTP handlers must derive current identity and
role/entity authority from the server session on every operation. Resume is
deliberately absent until provider identity/profile ownership and durable
transcripts exist.

The reader runs concurrently with prompt requests, correlates replies by exact
integer IDs and rejects malformed, duplicate or foreign response IDs. It answers
permission requests with cancellation and unsupported client methods with
method-not-found. It never executes delegated filesystem/terminal requests.
Incoming and outgoing frames have a Maipa budget of1MiB, at most64pending
requests,32sessions and1024queued updates within the byte budget. This is an
application budget; the published Prism line limit is64,000,000bytes. A deadline
or transport failure rejects pending work and closes the worker; no prompt or
uncertain financial action is automatically retried. Raw provider stderr and
protocol errors are discarded rather than returned to the browser.

The application-only `maipa/shutdown` request stops tracked drivers through their
published `stop()` methods and reports the resulting verification/survivor count.
An unavailable verdict stays unverified; stdin EOF or process exit does not
create a successful report. The zero-provider entry test proves only its empty
case. Live provider process-tree shutdown, cancellation and recovery remain
required acceptance work, along with live account provisioning, authenticated
conversation HTTP/UI streaming, durable transcripts, human approval records and finance-tool
authority. Those are Maipa responsibilities.
