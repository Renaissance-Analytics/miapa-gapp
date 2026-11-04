# Maipa agent and shared shell

Chat is the only fixed view. The authenticated Maipa conversation fills the
window until the agent presents a registered component. Human controls and agent
tools use the same server composition and revision. Closing a component returns
its space to chat without replacing the composer or losing its draft.

The app uses published Fancy screens/components, Prism ACP and its supervised
process lifecycle. Maipa owns the authenticated consumer, storage, UI schema,
scope checks and HTTP/SSE adapters. The finite catalog currently supplies notes,
client facts, client metadata controls and administrator-only user controls.
There are no fixed domain tabs. Fancy Flow belongs to the next workflow slice.

Each saved conversation retains its original user, provider account,
installation and storage identity. Account reassignment cannot adopt an old
transcript. Every worker operation rechecks the original Django session, active
membership, role, account ownership and storage binding. A changed role,
password, expired session or logout ends its worker. An unrelated access epoch
requires a newly authorized UI lease; the old lease remains invalid.

Connection setup selects a registered provider for the current Maipa user and
lets its native CLI use the existing machine subscription. This supersedes
new-login-first onboarding. Published Prism0.33.1 supports this host-mode path;
Maipa does not read or copy provider tokens/auth files. The Maipa account and
its application data/tools remain bound to the authenticated application user.
General provider discovery and multiple external-account identity negotiation
await the separate published generic ACP contract; they are not inferred from
a successful built-in Claude connection.

The current integrated native adapters are Codex and Claude subscription login. Each exact
public native artifact is pinned by SRI and all file digests. First connection
downloads it into a reproducible public-code cache beside installed dependencies,
outside durable user-data backups. The live worker runs as the local OS user
under a verified kill-on-close job; it has no AppContainer filesystem boundary.
The CLI alone resolves its existing subscription sign-in. A confirmed signed-out
account can use transient provider consent outside saved messages, model prompts
and logs. Maipa-owned profiles retain original identity, private application
storage and locking; they do not replace the machine authentication home.

Before serving HTTP after a restart, the local launcher holds its native
installation lock and reacquires all original profile locks. It validates
original installation/storage/account bindings and rejects active leases,
links, hardlinks, foreign layouts and overbudget temporary capsules. Abandoned
staged executables and their observed descendants must also pass a five-second
read-only native image/handle exit check; recovery never kills a discovered PID
or logs its image path. Ownership locks alone are not a process-stop verdict.
Abandoned
app-generated worker directories lose their retired container grants on every
entry, then move with their payload intact into private runtime-recovery
storage; provider homes and credentials are not opened, moved or reset. The old
session/UI authority is revoked, live phases become offline, partial assistant
replies become interrupted, and messages/queued requests/account identity remain.
The launcher retains its installation lock until shutdown. Kernel job closure
stops descendants if that server dies. This is a pre-HTTP host operation, with
no chat, HTTP or model-tool recovery endpoint and no stale-heartbeat-only reset.

The Codex adapter requests sign-in only after the pinned CLI explicitly confirms
that it is signed out. Configuration failures, unexpected status output, key-based
credentials and unverified process cleanup produce a closed connection error;
they never claim subscription readiness or ask the user to repeat consent. Raw
provider status output is not sent to the browser or retained in logs.
Failed workers record only an allowlisted runtime stage and fixed error category
for local troubleshooting. Unknown stages are replaced with `unknown`; exception
messages, provider output, account identifiers and private paths are excluded.

ACP session allocation is earlier than native startup. The current Codex adapter
keeps chat connecting and withholds the session/UI lease until the published
driver's startup promise has settled without protocol or exit failure and has a
native session ID. An early ID alone is insufficient: model validation can still
refuse startup. The private host exposes only `starting`, `ready` or `unavailable`,
with a server-owned startup deadline and responsive disconnect; no native ID or
provider error is returned by that status operation. These adapter checks do not
establish authentication, a successful model turn or generic ACP interoperability.

Messages and accepted text deltas are durable. Stop preserves an interrupted
reply; disconnect ends the supervised process tree. Reload can reconnect to a
live worker. A new worker explicitly reconstructs the owned saved transcript;
this is not a claim to resume an unacknowledged native thread.

Current acceptance boundary: the deterministic ACP peer is registered only by
the dedicated test fixture. It exercises the real consumer, authenticated HTTP,
streaming and UI services. It is not a production fallback or evidence of a real
model turn. Actual native device consent and clean stop are separately verified.
The published Prism ACP0.33.1 policy fixes Codex native sessions to CLI0.163.0,
gpt-5.3-codex and `nativeTools: 'none'`. Only fresh sessions and the supplied
Maipa UI MCP server are admitted. The published driver refuses unsupported
CLI/model/configuration, resume and announced unsupported reroutes. Its three
read-only MCP resource helpers remain scoped to supplied servers; Maipa exposes
no filesystem resources. An account that cannot use that measured restricted
model is unavailable; the default model cannot silently enable native exec.

Actual native startup and verified stop are established separately from model
turns. The production chat worker also passes three actual Claude subscription
model turns with synthetic Maipa data: present a note, update it after a human
edit at the new shared revision, then dismiss it. Stable handles, saved messages,
the original account and zero-survivor cleanup are verified. Original-install
browser acceptance and generic ACP interoperability remain open. A serving
model change that Codex does not announce cannot be detected by the driver.
An allowed UI call before a later reroute notice still requires current server
authority and cannot be undone by subsequent provider shutdown.

The supported Codex startup policy applies plugin restrictions before native launch and verifies effective configuration before creating a thread. A fresh contained0.163.0 session passes native thread acknowledgment, 35-second idle/profile-budget validation, zero-survivor stop and profile-privacy cleanup. This verifies the startup correction without human credentials or a model turn; actual authenticated model/UI acceptance and generic-client integration remain separate. No other provider is silently routed to Codex.

Claude uses the official native win32-x64 CLI2.1.311 as the local OS user, in
explicit host mode. The CLI resolves its own existing machine subscription;
Maipa never reads, copies or forwards its credential files. Exact native JSON
status confirming `claude.ai` subscription authentication reports signed in.
The original Maipa account/profile/storage/transcript identities remain bound
to their owner; the private Maipa profile is not substituted for the CLI's
machine authentication home. A provider login is offered only after confirmed
native sign-out, not on each Maipa onboarding or server restart.
The pinned native client emits `https://claude.com/cai/oauth/authorize` for
subscription consent. The private account controller and ACP consumer validate
that exact HTTPS host/path pair; the previous `claude.ai/oauth/authorize` pair
remains supported. Crossed host/path pairs, foreign hosts, userinfo and fragments
are refused. Consent query data stays transient and never enters saved chat.
Its native `auth login --claudeai` link remains transient. When the provider
shows a callback code, a private masked control forwards it directly to native
stdin; it never enters chat, saved drafts, process arguments or application logs.
The code is cleared on submission, provider/phase changes and logout. There is
no credential copying or application credential import.

Claude startup uses the public `modelState()` promise with a nonempty model list
and sticky protocol/exit failure. Unlike Codex, its native ID and catalog are
announced after the first user prompt. The public driver validates the exact CLI
version before forwarding that prompt and refuses a later unsupported catalog.
The app requests `nativeTools: 'none'`, fresh sessions, supplied UI MCP only,
`dontAsk` and an exact allowlist of its four UI tools. Execution/filesystem/web
tools and inherited settings/hooks/MCP are excluded by the published contract;
`EndConversation` is its documented residual control helper. All UI calls still
require current original account/user/session/role and composition revision.

The live host worker retains OS-resolved local-user startup directories and a
closed environment excluding API keys, NODE_OPTIONS and unrelated settings.
It starts suspended, joins a kill-on-close Windows job, verifies its host token
and job membership, then resumes. Stop observes zero remaining processes.
This is process lifetime containment, not AppContainer filesystem isolation;
the CLI has ordinary local-user OS authority. No container credential lease or
whole-home ACL grant is used. Temporary isolated transport probes retain their
separate AppContainer boundary. Actual native startup/discovery and
cleanup are distinct from authenticated model/tool acceptance, which remains
open. Generic ACP adapters/discovery await their separate published contract.

The supplied MCP server accepts the native-captured `2025-11-25` protocol for
its existing tools-only capability. It retains the same bounded LOCAL transport,
nonce, request schema and authority; no resources, sampling or tasks are added.
It handles the required empty `notifications/initialized` acknowledgment and
canonicalizes an omitted `tools/list` params object to `{}`. Unknown fields,
null params and tool-call arguments retain their existing strict validation.
Native Claude tool calls attach `_meta.progressToken` and
`_meta["claudecode/toolUseId"]`. The adapter accepts only those bounded scalar
correlation fields and discards them before forwarding the same canonical
`name`/`arguments` request. Unknown metadata, extra fields and invalid values
remain refused; no metadata supplies account, role or revision authority.
See the public [MCP lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
and [tools contract](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).


Chat uses the published Fancy VirtualList with stable message IDs and controlled
following. Message spacing is padding inside an app-owned row, so it contributes
to the measured height; external collapsing margins must not separate rows.
Published react-fancy6.6.1 uses the current laid-out viewport when observing
scroll and keeps following through viewport growth and width reflow.
Human upward scrolling still pauses it; Back to latest resumes. Real
browser acceptance checks the actual newest row and physical scroll bottom during
streaming, short-window resize, panel dismissal/reflow and expansion, alongside
composer/draft retention. These are separate from provider execution evidence.
