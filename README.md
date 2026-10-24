# Maipa

My AI Professional Accountant. Product repository spelling: `miapa-gapp`.

This local candidate contains a Python/Django API foundation and a TypeScript
Fancy workspace shell in the approved Calm ledger design, with System/Light/Dark
appearance. Both backend and frontend have acceptance-alignment gates. Published
Prism ACP transport now has a scoped Python/Node bootstrap; live provider account
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
  `401`. Responses are private and cannot be cached.
- `POST /api/session/logout/`: authenticated, CSRF-protected session invalidation.
- Errors use `{"error":{"code":"..."}}`; unknown path/host details are not reflected.

Authenticated account metadata and a zero-provider transport check now bridge
the Django session to the actual Python/Node ACP runtime. Accounts are server-owned;
browser requests cannot supply a provider home, command, identity or MCP config.
Checks and history retain the original requester and never claim provider login.
See [account transport checks](docs/agent-account-transport.md).

The session endpoint never accepts a user identity from a query/header. User
provisioning/login UI, roles/entity authority, finance operations and live provider
sessions are separate unfinished work. No finance/provider execution is exposed.

## Development serving

Serving requires a private random `MAIPA_SECRET_KEY` of at least 50 characters.
Do not put it in tracked files. Configure exact `MAIPA_ALLOWED_HOSTS` names;
wildcards are rejected. `MAIPA_LOCAL_DEV=1` explicitly allows local HTTP cookie
settings and defaults the host list to loopback names. `DEBUG` remains false.

Create the ignored `runtime/` directory before local migrations. SQLite uses the
Python standard library and is local development storage; production database,
listener, TLS/proxy, backup and isolation acceptance remain open. Test settings
use only an in-memory database and a fixed test key; never serve with them.

The Genie-managed development site is **https://maipa.gen/**. Its explicit local
entrypoint is `.venv/Scripts/python.exe backend/devserver.py {port}`. It creates a
private random development key in ignored `runtime/` without printing it,
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

The backend includes an application-owned Python ACP client and a Node host
using exact published Prism ACP0.24.1. Immutable user/account scope, fixed
workspace/home profiles, opaque application session handles and bounded streams
are tested. Permission requests currently cancel; no approval grant is invented.
The product entry initializes successfully; a synthetic transport under the
published CodexDriver tests the session/stream round trip. These tests use no
provider login, credentials or model calls. Provider provisioning, OS/account
isolation, durable transcripts/resume, live turn/shutdown and conversation HTTP/UI
integration
remain open. See [ACP bootstrap](docs/acp-bootstrap.md).

Backend and future frontend share this repository. Application code, adapters,
authorization, persistence and tests belong here; package owners work only on
their released packages. No PHP backend or PHP ACP package is used.

The local backend checks are a scoped check, not the complete CI/release pipeline.
Design approval and Windows frontend build/typecheck are verified. Complete
browser/ACP/finance/admin acceptance, production hardening and exact-candidate
shipping approval remain open. The public empty GitHub repository exists; source
publication, hosted CI, the submodule pin and clean remote clone remain pending.
No source has been published.
