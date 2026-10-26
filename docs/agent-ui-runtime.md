# Scoped agent UI runtime

Persistent chat is Maipa's only defined view. The in-app provider will compose
contextual UI from a registered, bounded component catalog. Fixed domain pages
are superseded by this user instruction.

The ACP client accepts an optional **trusted backend callback** for `maipa/ui`.
It is bound to the worker's original user/account independently of RPC arguments.
Only that method reaches it; provider permission requests still receive a
cancelled decision and arbitrary filesystem/terminal RPC remains unsupported.
UI work runs alongside the reader so an authorization/service wait cannot hold
streaming, cancellation or the prompt response hostage. At most eight calls
are pending, each has a deadline, and closing the connection cancels all of them.
Errors return fixed text, with no provider/body/credential/path diagnostics.

Acceptance for the transport slice: a synthetic bidirectional peer must prove
exact callback/results, continued streaming during a blocked callback, closed
method routing, denied provider permissions, error/result budgets and bounded
shutdown. This is transport evidence, not a claim of provider login, a real
model turn, or completed A01. Actual scoped login/session/tool/browser acceptance
remains necessary before marking the task complete.

The trusted bootstrap may enable `ui_tools` on an isolated Codex capsule. It
then creates an authenticated Windows LOCAL pipe shared only with its app-owned
MCP proxy. The provider receives that fixed stdio server, with automatic approval
for that server's tools only; native provider permissions remain independently
denied by the ACP client. Client-supplied MCP configuration and a second session
on the same tool-enabled worker refuse. No HTTP listener or loopback exemption
is needed. Claude's generated MCP configuration is unsupported by published
LOCAL mode, so that path refuses until a supported package release supplies it.
Incoming MCP/relay requests remain capped64KiB. Only the MCP reply allowance is
128KiB, because the protocol returns both text and structured state for an
accepted32KiB schema; queued output remains bounded256KiB. A real native test
exercises this valid large reply as well as the original oversized-request case.

`agent_accounts.ui` supplies `maipa_ui_catalog`, `maipa_ui_present`,
`maipa_ui_update`, and `maipa_ui_dismiss`. The model never chooses user/account/
session authority. A trusted session consumer issues an opaque UI lease, bound
to its original private session, role, storage installation and access revision.
Every operation rechecks that binding and current authority. Writes use a real
database transaction and exact revision; stale/invalid operations preserve state.

The catalog contains reusable Stack, Note, ClientFacts and ClientCatalog
components, plus AdminUsers under original/current administrator authority.
Schemas have stable unique handles, at most16nodes/depth4/32KiB, closed props
and no executable code, URLs or model-supplied data selectors. Interactive
components appear at most once per composition. Notes remain unverified
commentary; business reads/writes continue through existing authorized services.

The shared authenticated endpoint is
`/api/agent/accounts/{account}/ui/{lease}/`. Both reads and writes require the
current actor header; writes also require same-origin Django CSRF protection.
HTTP/model callers cannot create a provider session or mint a UI lease. This
contribution does not mount a new page, claim a live model conversation, or
complete the upcoming compact-chat/component browser integration.
