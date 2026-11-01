# Deterministic workflow foundation

Maipa uses published Python `fancy-flow` 0.29.0 for execution and
`@particle-academy/fancy-flow` 0.81.0 for the forthcoming shared editor. This
checkpoint provides internal Django services, stores and site-owned dispatch.
It does not yet expose workflow HTTP/MCP tools or present workflow UI.
Chat remains the only fixed view. A04/A05 and their integrated acceptance remain
unfinished.

Only the published `manual_trigger`, `transform` and `output` executors are
bound. The host never installs the engine's unrelated offline fake clients.
Transform uses the package's dot-path/template semantics, not JavaScript
evaluation or accounting arithmetic. Network, terminal, provider, storage,
subflow and human gate kinds are refused. Financial effects still require the
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
be added under this transaction-only contract. Recorded human responses,
scheduling, native-origin transport and full outcome verification remain A04
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

## Shared authoring boundary

The installed agent-integrations 0.53.0 Flow bridge declares synchronous setters.
A finite public-package probe confirmed that an unresolved asynchronous setter
can receive a successful tool response before persistence. A supported awaited,
atomic node/edge commit/refusal contract shipped in0.54.0 and was independently
verified in scratch, but is not adopted here. Its existing run/cancel controls
still report success before async acceptance or after disposal. Release0.55.0
was independently verified in scratch with awaited/refusable controls and
lifecycle fencing:35public cases pass, but human-declined run activity still
fails its acceptance case. It is not installed. A package-only reproducer is
with the Fancy slave; optional graph validation and application acceptance remain
open. No replacement bridge, deferred-success adapter
or package patch is enabled here. Maipa owns its authorization and persistence;
the package owner owns only reusable package contracts and releases.

The editor and actual in-app build, triage, update and run acceptance follow this
foundation. Package verification, focused tests or this document alone do not
constitute browser/provider acceptance or a delivered release.
