# Allowed runtime subset

The user expressly allowed Django while retaining the published Fancy/Prism
package policy. The initial backend uses no other independent product packages.

| Distribution | Version | Role |
|---|---|---|
| Django | 5.2.18 | Explicitly allowed framework |
| asgiref | 3.13.0 | Required Django dependency |
| sqlparse | 0.6.0 | Required Django dependency |
| tzdata | 2026.5 | Django dependency on Windows only |

`requirements.txt` contains exact wheel hashes independently checked against
primary PyPI artifacts in the envelope's package verification. A Windows isolated
install and Django consumer checks passed before application adoption. Linux
markers were inspected, but Linux execution remains open. Python standard-library
SQLite, unittest, AST/JSON/hash tooling and subprocess are platform capabilities.

The frontend now uses react-fancy5.60.0, React/ReactDOM19.3.1 and Tailwind4.3.3
with the official Fancy React starter's declared Vite6/React-plugin5/TypeScript/
Tailwind-plugin/type declarations. Exact roots and registry SHA512 closure are
locked in `frontend/package-lock.json`:166non-root entries including platform
optionals and6WASI entries bundled under their parent's integrity. Existing
verified runtime versions/integrities are preserved. Windows npm ci installed89
packages with lifecycle scripts disabled; npm audit reported0knownadvisories.
Typecheck/tests and the actual native Vite/Tailwind build pass without running
install scripts. No browser/test framework was added; tests use Node built-ins
and the already-declared TypeScript AST parser. Linux/native platform execution
remains unverified. These are facts about the scoped build, not full security
or release acceptance.

Browser update tracking adds exact published `fancy-app-update`0.7.0 and
`fancy-whats-new`0.1.0. Registry discovery, npm tarball SHA512 and public exports
were checked before installation. The updater has no runtime dependencies;
What's new depends only on that updater. Existing React19 and Fancy5.60 peers
satisfy both packages. All164previous lock entries keep their versions and
integrities; only these two packages were added, with install scripts disabled.

The app's ACP transport now uses exact published npm
`@particle-academy/prism-acp`0.24.1 in its own `backend/agent/package-lock.json`,
consumed by Maipa's Python/Node bootstrap. That separate lock contains just one
runtime distribution, no runtime dependencies/peers, and no install scripts.
Its132027-byte tarball matches the independently verified registry SHA512 and
all53installed published files match the archive. Node>=22 is required.
The real Windows entry initializes after preparing the published supervisor
with .NET Framework's compiler. Actual provider CLI distributions/accounts are
not installed or exercised by these checks; their acceptance remains open.
The frontend lock and all existing versions/integrities are unchanged.
PHP's Packagist release is
unrelated. No standalone Uvicorn, psycopg, Playwright or rejected infrastructure
proposal was adopted. No packages were patched, vendored or forked.

P04 adds published `fancy-screens`0.7.1 and `agent-integrations`0.47.1,
their shared `fancy-doc-commons`0.4.0 / `fancy-auto-common`0.2.0 closure,
and the screen package's `zustand`5.0.16 peer. All five tarballs match primary
npm registry SHA512 identities; all 24/330/9/18/52 installed published files,
respectively, match their archives. No install lifecycle scripts ran. The
registry/API contracts and relevant screen/MCP/undo runtime entries were reviewed
before adoption. No optional unrelated surface peers were installed. Existing
166 lock entries retain their versions, URLs and integrities; npm only reclassifies
the already-installed React types/csstype from dev to devOptional for the peer
closure. The resulting lock has 171 non-root entries. Public npm audit reports
zero known advisories, which does not establish complete package safety.

The app imports the scoped MCP and screens bridge entries. It does not import
the broad agent-integrations root, open a relay, or grant filesystem/network
capabilities. Only the existing approved React/Fancy peers execute. The
host-side schema and per-mount tool checks are described in
[persistent-workspace.md](persistent-workspace.md).
