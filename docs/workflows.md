# Deterministic workflow foundation

Maipa uses published Python `fancy-flow` 0.29.0 for execution and
`@particle-academy/fancy-flow` 0.81.0 for the forthcoming shared editor. This
checkpoint provides internal Django services and stores. It does not yet
expose workflow HTTP/MCP tools, run a persistent worker, or present workflow UI.
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

This recovery model relies on **deterministic execution without external
effects**. It does not establish safe replay of payments, postings, network
requests, processes or ambiguous provider outcomes. Those executors must not
be added under this transaction-only contract. Persistent dispatch/workers,
recorded human responses, explicit retry policy, scheduling and full outcome
verification remain A04 work; persistent processes must be managed by Genie.

## Shared authoring boundary

The installed agent-integrations 0.53.0 Flow bridge declares synchronous setters.
A finite public-package probe confirmed that an unresolved asynchronous setter
can receive a successful tool response before persistence. A supported awaited,
atomic node/edge commit/refusal and lifecycle contract has been requested only
from the Fancy package slave. No replacement bridge, deferred-success adapter
or package patch is enabled here. Maipa owns its authorization and persistence;
the package owner owns only reusable package contracts and releases.

The editor and actual in-app build, triage, update and run acceptance follow this
foundation. Package verification, focused tests or this document alone do not
constitute browser/provider acceptance or a delivered release.
