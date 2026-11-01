# Deterministic workflow foundation

Maipa uses published Python `fancy-flow` 0.30.1 for execution and
`@particle-academy/fancy-flow` 0.82.1 for the shared editor. Django owns scoped
storage and site-owned dispatch, with narrow human-session endpoints for pending
questions and recorded responses. The dynamic `Maipa.Workflow` component and
scoped native tools share the same authoring draft. Native execution controls
are not exposed by this authoring increment.
Chat remains the only fixed view. A04/A05 and their integrated acceptance remain
unfinished.

The published `manual_trigger`, `transform`, `output`, `DurableApproval` and
`DurableUserInput` executors are bound. The host never installs the engine's
unrelated offline fake clients or framework-free human pass-through executors.
Transform uses the package's dot-path/template semantics, not JavaScript
evaluation or accounting arithmetic. Network, terminal, provider, storage,
subflow and other unadmitted kinds are refused. Financial effects still require the
proposal/approval system and independently verified outcomes.

## Ownership and immutable runs

Definitions belong to one current installation member and optionally one of
that member's owned entities. Administrator status does not grant access to
another person's definitions, seeds, checkpoints or outputs. The service reads
current database membership, role, active-user and entity ownership on each
operation; cached user flags are insufficient.

Saving a definition checks its expected revision and appends an immutable
`DefinitionVersion`. A run references that exact version and retains an immutable
seed and canonical SHA256 identities. Editing the definition cannot edit a run.
The originating owner, installation and optional entity are captured at dispatch
and remain immutable independently of definition metadata. Transferring a
definition cannot expose its old run through a read or duplicate dispatch, and
cannot transfer execution authority. Native runs also retain their original
account, session, UI lease, registered agent principal and dispatch grant. The
internal native adapter is described below; no workflow transport exists yet.
Reusing a dispatch key returns its original run only for the same version and
seed. A conflicting delivery refuses. Dispatch keys are bounded opaque local
identities, never credentials or provider idempotency claims.

Graphs pass the actual published GraphPolicy and strict importer, plus bounded
host admission checks. Refused/empty graphs, unsupported kinds, invalid config,
duplicate handles, invalid ports and cycles cannot become runnable definitions.
The current limits are 30 nodes, 60 edges, 64 KiB schema and 32 KiB seed or
individual node output. Per-owner definitions and per-definition revisions/runs
also have small-install quotas. No raw package error text is stored in diagnostics.

## Checkpoints and recovery

Fancy's `Coordinator` and `Frontier` make all execution/routing decisions. The
Django `NodeClaimStore` adapter supplies atomic unique `(run, node)` claims and
checkpoints. A same-owner claim reentry retains its first-attempt time; a
different owner or a completed node cannot claim it again. Stored ports come
from actual engine outputs. The application contains no alternate graph runner.

One deterministic node executes inside a database transaction, serialized with
the installation's role-administration write boundary. The expected run step
fences duplicate/stale worker deliveries. The completed checkpoint and step
increment commit together. A terminated transaction rolls both back; a fresh
coordinator resumes using committed outputs. Tests include separate Python
workers and actual process termination after an uncommitted real node output.

Cancellation checks the current run step and stops future nodes while retaining
accepted checkpoints. A completed, failed or cancelled run does not execute
again. A bounded-output failure records the failed node and a closed diagnostic
without publishing partial output.

## Site-owned worker and revocation

After migration and native-connection recovery, the Genie-managed development
site starts a non-daemon dispatch thread under its existing exclusive installation
lease. A bounded indexed queue query observes run IDs and expected steps; each
node transaction derives current authority from the immutable persisted origin.
Two processes observing the same step cannot execute the next step accidentally.
The thread stops and joins before the launcher releases its lease, so offline
snapshot/recovery cannot overlap accepted work. Do not run a separate persistent
worker in a terminal or beside the site's installation lock.

Lost role, disabled/inactive identity, changed entity ownership or changed
definition bindings records a closed blocked diagnostic without executing another
node. Blocked runs leave the queue. Restored authority alone does not restart
them: the original authorized owner must explicitly resume at the current step,
after schema/seed and scope validation. Accepted checkpoints and first-attempt
clocks remain intact. Queue receipts contain counts only. Unexpected thread
failures stop dispatch and log a fixed message, never raw exceptions or seeds;
owned shutdown surfaces failure. The scoped internal status service reports
starting, running, stopping, failed or not_running from the actual worker thread.
Running requires a successful first queue pass and a valid current installation
lease for the configured storage root. HTTP readiness alone never supplies this
status. Native dispatch refuses while the worker is unavailable; its receipt
records availability observed at admission, not a promise of future execution.

Migration preserves pre-origin run IDs, seeds, graphs and checkpoints without
guessing their original principal from mutable definition metadata. Unverifiable
origins stay null and queued/running legacy records become blocked. The current
services cannot adopt or execute them. Reversing this authority migration requires
offline backup recovery rather than stripping captured origins in place.

This recovery model relies on **deterministic execution without external
effects**. It does not establish safe replay of payments, postings, network
requests, processes or ambiguous provider outcomes. Those executors must not
be added under this transaction-only contract. Scheduling, native-origin
transport and full outcome verification remain A04
work; all persistent serving processes must be managed by Genie.

## Internal native authority

The trusted host can bind an **existing** typed `AgentPrincipal` to its actual
owned account/session/UI lease. This creates neither a principal nor a policy
grant. The binding is append-only, captures the principal's original expiry and
cannot silently move that principal to a new login. Changed role, access epoch,
disabled account, revoked session or changed/expired principal invalidates it.
There is no implicit durable handoff beyond the originating connection.

An owned entity-scoped immutable graph version is registered with an actual
policy resource and captured resource revision/hash. Native dispatch needs a
current explicit agent `workflow.run` grant, independent of any human grant.
The run and original grant/binding evidence commit together. Exact duplicate
delivery must match both inputs and original authority; missing evidence cannot
be repaired by retrying dispatch or falling back to local owner authority.

Every native node rechecks the original grant and current connection/resource
authority inside the deterministic execution transaction. Revocation blocks the
next checkpoint with `native_authority_revoked`; a newly issued grant cannot
adopt the old run. Restoring the same principal requires an explicit native
resume with its own current `workflow.resume` grant and exact run step. Native
inspection requires `workflow.triage` and the original current binding. The
current human owner can still read history and stop work after agent revocation.
Local-owner resume cannot silently change a native run's actor.

The native migration adds evidence without rewriting existing local records.
Downgrading would remove the native marker and could confer local-owner
authority, so reverse migration refuses before removing any evidence. Recover
an older version from an offline backup instead. These internal services and
their real-worker tests do not establish actual provider/tool/UI acceptance.

## Recorded nonfinancial human gates

Both durable human executors pause even when their upstream inputs contain
`approved` or `values`. Admission refuses `autoAnswerFromInput`, notification
credentials and unsupported form configuration. Input gates accept a bounded
list of explicit text, textarea or checkbox fields. Approval responses must be
exact booleans; user-input responses must match the admitted field names/types
and bounds. Graph content never supplies a human response.

The store captures an append-only request containing the original run/version,
node, actual executor input, bounded question/form, parked step and canonical
hash. A waiting run leaves the dispatch queue. Responses are append-only and
record the actual current human owner plus an opaque retry key and answer hash.
Another user, changed authority, cancellation, wrong gate/version/hash/step or a
conflicting retry refuses atomically. An exact acknowledged retry returns its
fixed recorded receipt without advancing a subsequent question or requeueing
completed/cancelled work.

Human-session APIs are `GET /api/workflows/runs/<run>/human/` and
`POST /api/workflows/runs/<run>/gates/<gate>/response/`. The POST requires the
session's CSRF token and only `expected_step`, `definition_revision`,
`request_sha256`, `request_key` and `answer`. Actor/node hints are refused.
These APIs are not registered in any model/native tool set. They acknowledge
response persistence, not active execution or a provider-side outcome.

Recording a response atomically changes only the exact paused checkpoint to a
host-owned `released` state and requeues at the next step. That state is omitted
from the engine's claim view only when valid durable response evidence exists.
A new claim retains its first-attempt clock and increments the gate attempt;
completed checkpoints remain intact. Supported `Submissions.park/record` is
restored from accepted records separately from seeds/config. Actual Fancy
executors and Frontier perform routing, dead-branch skips and continuation; the
host implements no alternative scheduler or approval executor. Empty terminal
port lists and real approval/denial ports are accepted literally.

Original native authority still must hold when a human answers and before every
following native node. A human answer cannot replace a revoked login/grant or
silently create a durable handoff. These gates do not confer financial posting,
payment, filing or report-release authority. The gate migration refuses reverse
schema removal before accepted response evidence can be stripped; use offline
backup recovery. Tests include actual process exit before and after recording a
response, connection races, sequential gates and original checkpoint identities.
Shared visible controls and actual provider/browser acceptance remain A05 work.

## Shared authoring boundary

Owned authoring drafts persist a bounded Fancy editor graph independently of
executable versions. Empty/disconnected graphs and unfinished configuration are
normal draft states. Unknown/effectful kinds, unsafe config fields, duplicate
handles, dangling edges, nonfinite positions and fabricated execution status are
refused. A draft never creates a run, human response, agent principal or grant.

Human-session endpoints are `GET/POST /api/workflows/drafts/`,
`GET/POST /api/workflows/drafts/<id>/` and
`POST /api/workflows/drafts/<id>/publish/`. Every call requires the actual current
owner/preparer or administrator. `X-Maipa-Workflow-Actor` is only an actual-session
consistency guard, never submitted authority. Writes require CSRF and closed
bounded JSON. Creation uses an exact retry UUID/name/entity identity. Graph
writes use current draft revision CAS; stale authors cannot overwrite newer
human work. Native authoring uses a sealed original-account/UI context rechecked
on each call, without manufacturing the typed execution authority required by A04.

Publishing uses the public Python Fancy graph types/exporter and existing full
admission. The published format may omit empty `config`; its documented empty
default is admitted without rewriting the exported schema. Missing required
transform/input configuration still refuses. Actual durable human gates still
park even when their empty config was omitted and inputs contain `approved`.
Publication records an immutable version and fixed receipt, not dispatch. Exact
retry returns the original receipt; a newer external definition edit conflicts
with the next draft publication. Existing runs keep their original snapshots.
The additive draft migration refuses reverse removal when saved drafts exist;
recover from an offline backup.

The installed agent-integrations 0.55.1 public Flow bridge uses its awaited
atomic commit contract. A finite app transport passes complete proposed records
to Django; only a committed current revision is acknowledged. Deleting a node
and its attached edges is one draft transaction. Agent writes require a prior
read of the same visible draft, current original session authority and exact CAS.
Stale, replaced, foreign or revoked drafts refuse. Fixed value-free receipts
identify accepted authoring changes; they never claim execution.

The public controlled `FlowEditor` owns canvas, palette and configuration UI.
Human edits persist through the same draft service. Pending saves preserve newer
typing, conflicts keep the local graph until explicit reload, and actor changes
clear private state. In a narrow panel Add node opens the kit palette and
selecting a node opens its settings. Chat remains mounted. There is no fixed workflow page, replacement
renderer, graph runner or package patch.

The native tool list includes current owned draft discovery, idempotent creation,
public graph reads/edits and immutable publication. It excludes browser Run,
native execution, arbitrary kind discovery and fabricated node status. Run,
cancel, triage and shared human-gate controls still require their separate A05
authority and provider acceptance.

The runtime upgrade retains existing immutable 0.29 graph, seed and history
identities. They remain readable but cannot advance under a different runtime.
An explicit new publication records 0.30.1 as a new immutable version; it does
not relabel or replay an old run. Transform fields use real public typed values
and selected-mode validation. Python and TypeScript native Unicode trimming
edges are not claimed to be universally identical.

The editor and actual in-app build, triage, update and run acceptance follow this
foundation. Package verification, focused tests or this document alone do not
constitute browser/provider acceptance or a delivered release.
