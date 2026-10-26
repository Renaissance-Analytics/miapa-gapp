# Maipa

My AI Professional Accountant. Product repository spelling: `miapa-gapp`.

This public repository contains a Python/Django API foundation and a TypeScript
Fancy workspace shell in the approved Calm ledger design, with System/Light/Dark
appearance. Both backend and frontend have acceptance-alignment gates.
Workspace members can sign in and out with server-owned installation membership
and initial roles. Prism ACP transport has a scoped Python/Node bootstrap; live provider account
integration and the accounting workflows remain unfinished. This is
not a usable accounting product or a release candidate.

## Check the backend

Use Python 3.13 in an isolated environment. The only runtime distributions are
the expressly allowed Django and its required dependencies, pinned by wheel hash.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.txt
npm ci --ignore-scripts --prefix backend/agent
.venv\Scripts\python.exe -m tools.check_backend
```

The last command runs alignment **first**, then Django system checks, migrations
against an isolated in-memory test database and the required tests. Zero tests,
skips and expected failures are failures. It refuses non-test settings. No model
calls, credentials, client records, private corpus or external services are used.
On Linux, use `.venv/bin/python`; Linux execution is not yet verified.
Node22 or newer is required for the ACP integration checks. The Windows product
entry check builds/verifies Prism's supervisor in an isolated temporary directory
using .NET Framework's compiler. It initializes without starting a provider CLI.

```powershell
.venv\Scripts\python.exe -m tools.alignment
```

That standalone gate parses declared unittest/Django test classes without
importing application/test modules. It requires explicit positive/negative
scenarios, mapped tests, assertions and review records bound to scenario and
source hashes. Review hashes normalize checkout line endings. They preserve a
recorded review, not a proof of semantic correctness. See [test review](docs/test-review.md).

## Backend contract

- `GET /api/health/`: minimal public liveness, no database/provider readiness claim.
- `GET /api/version/`: public built-bundle fingerprint, uncached; no account data.
- `GET /api/session/`: Django-authenticated user ID and masked CSRF token; otherwise
  `401`, or `403` when workspace membership is unavailable. Responses cannot be cached.
- `GET /api/session/csrf/`: minimal public CSRF bootstrap, uncached.
- `POST /api/session/login/`: bounded strict JSON username/password, same-origin
  CSRF, generic failures, shared five-attempt/15-minute login budget, and real
  Django session/CSRF rotation. An existing signed-in identity cannot be switched.
- `POST /api/session/logout/`: authenticated, CSRF-protected session invalidation.
- `GET /api/workspace/`: the server-selected sole installation and current member
  role/permissions; query/header tenant or role hints cannot change authority.
- Errors use `{"error":{"code":"..."}}`; unknown path/host details are not reflected.

Authenticated account metadata and a zero-provider transport check now bridge
the Django session to the actual Python/Node ACP runtime. Accounts are server-owned;
browser requests cannot supply a provider home, command, identity or MCP config.
Checks and history retain the original requester and never claim provider login.
See [account transport checks](docs/agent-account-transport.md).

The session endpoint never accepts a user identity from a query/header. User
membership is checked on each protected request. Administrator/preparer members
can start their own zero-provider checks; reviewer/viewer members can read their
own account metadata/history. Administrator status does not expose another user's
private provider account. Finance-specific role/entity authority and live provider
sessions remain unfinished. No finance/provider execution is exposed.

In a configured, trusted backend console, create users using Django's interactive
`createsuperuser` command (or another approved operator provisioning path), then
run `backend/manage.py grant_workspace_access --username <name> --role <role>`.
Roles are `administrator`, `preparer`, `reviewer` and `viewer`. This command only
grants membership to an existing active password user; it accepts no password or
HTTP input. Django staff/superuser flags alone do not grant workspace access.
Do not put passwords or the private server signing key in command-line arguments.

## Development serving

Serving requires a private random `MAIPA_SECRET_KEY` of at least 50 characters.
Do not put it in tracked files. Configure exact `MAIPA_ALLOWED_HOSTS` names;
wildcards are rejected. `MAIPA_LOCAL_DEV=1` explicitly allows local HTTP cookie
settings and defaults the host list to loopback names. `DEBUG` remains false.
When that explicit local mode also allows `maipa.gen`, CSRF trusts exactly
`https://maipa.gen` for Genie's HTTPS-to-loopback proxy. It does not trust wildcard
origins or forwarded-protocol headers. Production origin/TLS policy remains K01.

Installation data lives outside application source, under `%LOCALAPPDATA%/Maipa/data`
on Windows or an explicit absolute `MAIPA_DATA_DIR`. The local launcher protects
the directory and database before migration. Existing checkout-local databases
require explicit offline adoption; see [install storage and recovery](docs/install-storage.md).
SQLite uses the Python standard library; production listener, TLS/proxy and full
MVP recovery acceptance remain open. Test settings
use only an in-memory database and a fixed test key; never serve with them.

The Genie-managed development site is **https://maipa.gen/**. Its explicit local
entrypoint is `.venv/Scripts/python.exe backend/devserver.py {port}`. It creates a
private random development key in the protected install directory without printing it,
migrates local SQLite and serves the trusted built frontend beside the API.
It never runs in a terminal. The shell/assets routes refuse non-development
mode; assets use Django's safe-join/static handler and cannot read runtime files.
Django's development server is not a production listener.

## Check the frontend

Use Node22 or newer. Its pinned toolchain comes from the official Fancy React
starter's declared development dependencies, preserving the verified Fancy
runtime versions. No lifecycle scripts are needed for the verified Windows build.

```powershell
cd frontend
npm ci --ignore-scripts
npm run check
```

This runs frontend alignment first, then Node's built-in tests, strict application
type checking and Vite build. Source/scenario hashes preserve recorded semantic
review; they do not prove semantic correctness. Tests cover session transport,
malformed/anonymous/failure states, cosmetic user-scoped keys, shell semantics
and alignment failure fixtures. Static markup tests do not prove browser input,
computed contrast or responsive behavior. The large optional icon chunk remains
a performance follow-up; it is not hidden by raising the warning threshold.

The shell reads the real same-origin Django session. It does not include a
pretend login flow, provider composer, finance records or disconnected settings.
User management, server-saved preferences/split, pinned views and real ACP
conversation behavior remain their implementation tasks. Current theme preference
uses Fancy's local storage API, scoped by the authenticated principal ID.

The footer also shows the installed build and **What's new**. Published Fancy
app-update detection offers a refresh when the built source changes; Fancy
release cards explain the installed changes and remember what this browser has
shown. See [browser updates](docs/browser-updates.md) for the release-note and
build-identity contract. Keep these notes current as application work progresses.

## Delivery boundaries

The shared bootstrap pipeline is now `tools.ci`: see [CI checks](docs/ci.md)
for locked environment preparation, alignment-first stage ordering, candidate
evidence and the final isolated native-browser check. The GitHub workflow calls
this same entrypoint on Windows2025. The approved bootstrap has hosted green
evidence; changed P01 source still needs its own local and hosted candidate checks.

The backend includes an application-owned Python ACP client and a Node host
using exact published Prism ACP0.28.0. Authenticated transport checks now run
inside a verified Windows AppContainer with a job assigned before execution.
Each worker receives an exact-byte, read-only runtime and its own writable
workspace/home. Real worker/descendant tests deny other-user and server files;
deadlines, revocation and server death verify actual process cleanup. See
[Windows worker isolation](docs/windows-worker-isolation.md).
Opaque application session handles and bounded streams are tested. Permission
requests currently cancel; no approval grant is invented.
The product entry initializes successfully; a synthetic transport under the
published CodexDriver tests the session/stream round trip. These tests use no
provider login, credentials or model calls. Provider provisioning, actual provider
CLI/network/IPC compatibility inside the boundary, durable transcripts/resume,
live turn/shutdown and conversation HTTP/UI integration remain open.
See [ACP bootstrap](docs/acp-bootstrap.md).

Backend and future frontend share this repository. Application code, adapters,
authorization, persistence and tests belong here; package owners work only on
their released packages. No PHP backend or PHP ACP package is used.

The local backend checks are a scoped check, not the complete CI/release pipeline.
Design approval and Windows frontend build/typecheck are verified. Complete
browser/ACP/finance/admin acceptance, production hardening and exact-candidate
shipping approval remain open. The approved bootstrap source is merged on main,
with hosted CI, a tracked submodule pin and clean anonymous clone verified.
Current P01 changes require their own exact-green source publication approval.
