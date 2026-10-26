# Account transport checks

The scaffold binds Django's authenticated user to server-owned agent account
metadata and an actual check of the installed Python/Node/Prism transport.
It stores no provider credentials and never starts a provider CLI. The account
metadata is not a claim that the person has logged in or has provider access.

`GET /api/agent/accounts/` returns only the signed-in active user's enabled
accounts, their labels/provider and `setup_required` status, plus the exact
installed runtime version and `provider_sessions_enabled: false`. The current
runtime is the locked, published Prism ACP0.28.0. Missing/broken runtime metadata
returns a generic503. Headers/query parameters cannot select another user.

`POST /api/agent/accounts/<uuid>/transport-checks/` accepts only an empty JSON
object with valid same-origin CSRF. The server selects account/user identity,
executable and temporary workspace/home/supervisor paths. It uses the actual
application client and host, verifies initialization, explicitly requests a
zero-provider shutdown verdict and waits for a clean exit. It does not call
`session/new`, authenticate, load provider history or make a model request.

The response is201 with a check ID, `status: transport_verified` and
`provider_ready: false`. A safe failed outcome is stored and returns generic503;
raw paths, stdout/stderr, protocol errors and credentials are never returned.
The body limit is1024bytes. An atomic account cooldown permits at most one check
per30seconds, returning429/Retry-After before starting overlapping work.
Each user can have at most one metadata record per supported provider.

`GET` on the same check-history URL returns up to20recent records, only when
both the current account and original check requester belong to the signed-in
user. Reassigning account metadata cannot reveal prior requesters' history.
Foreign, disabled and missing accounts share a generic404. Responses are
no-store and vary on the session cookie; no request accepts a client user ID,
home, command, environment, workspace, model policy or MCP configuration.

Metadata creation remains a trusted backend/admin responsibility; these routes
cannot create or modify accounts. They are a bootstrap transport surface, not
Agent Settings or a disconnected provider composer. The new Django migrations
add constrained account/check tables and are applied by the existing local
development entrypoint on restart. Test databases/check directories are isolated;
tests use synthetic users, no real credentials and no provider CLI/model calls.

K05 covers this reproducible Python/TypeScript/ACP scaffold and transport binding.
The existing P01 task owns full login/roles and OS/account isolation; P06 owns
provider profile provisioning/Agent Settings; A01 owns live subscription
conversations, authorized streaming and durable provider/session recovery.
Their actual CLI, login, entitlement, tool authority and live shutdown tests stay
required before exposing provider sessions. Full Q05 and exact-candidate source/
shipping approval remain separate gates.
