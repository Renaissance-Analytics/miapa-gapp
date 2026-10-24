# Bootstrap verification

The supported check target is Windows x64, Python3.13.15 and Node24.19.0.
An existing native Chrome or Edge installation and the published ACP Windows
supervisor's .NET Framework4 compiler are required. Missing prerequisites fail;
the runner does not install a browser driver, invoke a provider CLI or use a
human browser profile. Other platforms are unverified and fail the entrypoint.

Run `./tools/bootstrap.ps1` in PowerShell to prepare only the approved pinned
closure. Python installs require wheel hashes and npm lifecycle scripts are
disabled. Then run `.venv/Scripts/python.exe -m tools.ci` from the product root.
The same entrypoint runs in GitHub Actions, with the same runtime pins and order:

1. Backend and frontend acceptance-to-test alignment, before application tests.
2. Approved dependency input hashes, installed versions and live public PyPI/npm
   advisory queries. Unavailable checks and any reported advisory fail.
3. Python parsing, Node syntax and strict TypeScript checking.
4. Required backend/domain/security/ACP integration tests, exact static discovery,
   nonempty execution and no runtime skips or expected failures.
5. Required frontend tests, with explicit TAP pass/fail/skip/cancellation counts.
6. Model migration drift against isolated test settings.
7. Native Vite production build and generated build identity.
8. Real browser acceptance against that built frontend and Django backend, last.

The finite browser fixture creates a temporary synthetic database, server-owned
user/session/account metadata, fresh browser profile and an ephemeral loopback
WSGI listener. It checks anonymous/authenticated UI, actual API authority/CSRF,
the exact build, Fancy release controls, theme, keyboard divider and mobile panel
behavior, and a real zero-provider published ACP transport check. It verifies
browser exit, debugger closure and profile removal, then stops the fixture server
and removes its database. It never connects to the person's Genie tab or cookie
store. Production and ongoing local serving remain Genie-managed.

`artifacts/ci/report.json` binds ordered stage evidence to HEAD commit/tree, the
normalized fingerprint of every tracked/nonignored source file, build identity
and runtime/browser versions. Source changes during any stage fail. A clean
commit is required normally; `--allow-dirty` is development evidence only and
sets `publication_candidate:false`. Reports are observations, not attestations
against hostile repository code. Review code and report provenance before use;
never accept a downloaded report alone as current candidate evidence. Raw failure
logs remain local and must not be uploaded. The workflow uploads only report.json.

This gate covers implemented bootstrap contracts. It does not certify unfinished
finance/corpus/agent evaluations, real provider account login/conversations,
worker OS isolation or full MVP/Q05/Q06 release acceptance. Those gates must land
with their features before release. No-known-advisories is not a full security
review; semantic review and an exact-candidate security review remain required.

The Actions SHAs were resolved from the official actions repositories and their
action definitions inspected. They use Node24. The Windows2025 image inventory
lists native browsers and .NET. Local success does not prove hosted success;
the first hosted execution follows approved initial source publication. There
is no publish, deployment, merge, secret or privileged fork-trigger step here.
