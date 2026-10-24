# Maipa

My AI Professional Accountant. Product repository spelling: `miapa-gapp`.

This initial local candidate contains a Python/Django API foundation and an
acceptance-to-test alignment gate. The TypeScript Fancy interface and published
Prism ACP integration are planned; neither is implemented here yet. This is not
a usable accounting product or a release candidate.

## Check the backend

Use Python 3.13 in an isolated environment. The only runtime distributions are
the expressly allowed Django and its required dependencies, pinned by wheel hash.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.txt
.venv\Scripts\python.exe -m tools.check_backend
```

The last command runs alignment **first**, then Django system checks, migrations
against an isolated in-memory test database and the required tests. Zero tests,
skips and expected failures are failures. It refuses non-test settings. No model
calls, credentials, client records, private corpus or external services are used.
On Linux, use `.venv/bin/python`; Linux execution is not yet verified.

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
- `GET /api/session/`: Django-authenticated user ID and masked CSRF token; otherwise
  `401`. Responses are private and cannot be cached.
- `POST /api/session/logout/`: authenticated, CSRF-protected session invalidation.
- Errors use `{"error":{"code":"..."}}`; unknown path/host details are not reflected.

The session endpoint never accepts a user identity from a query/header. User
provisioning/login UI, roles/entity authority, finance operations and provider
sessions are separate unfinished work. No finance/provider capability is exposed.

## Development serving

Serving requires a private random `MAIPA_SECRET_KEY` of at least 50 characters.
Do not put it in tracked files. Configure exact `MAIPA_ALLOWED_HOSTS` names;
wildcards are rejected. `MAIPA_LOCAL_DEV=1` explicitly allows local HTTP cookie
settings and defaults the host list to loopback names. `DEBUG` remains false.

Create the ignored `runtime/` directory before local migrations. SQLite uses the
Python standard library and is local development storage; production database,
listener, TLS/proxy, backup and isolation acceptance remain open. Test settings
use only an in-memory database and a fixed test key; never serve with them.

Use a **Genie-managed site/process** for a persistent development server, with
the command `.venv/Scripts/python.exe backend/manage.py runserver 127.0.0.1:{port} --noreload`.
Configure private environment values through the host. Django's development
server is not a production listener. No site has been provisioned by this candidate.

## Delivery boundaries

Backend and future frontend share this repository. Application code, adapters,
authorization, persistence and tests belong here; package owners work only on
their released packages. No PHP backend or PHP ACP package is used.

The local backend checks are a scoped check, not the complete CI/release pipeline.
Frontend design approval, verified Fancy tooling, actual browser verification,
ACP integration, production hardening and exact-candidate shipping approval
remain open. Hosted CI, public remote/submodule pin and clean remote clone also
remain pending. No source has been published.
