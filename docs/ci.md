# Bootstrap verification

The supported check target is Windows x64, Python3.13.15 and Node24.19.0.
An existing native Chrome or Edge installation and the published ACP Windows
supervisor's .NET Framework4 compiler are required. Missing prerequisites fail;
the runner does not install a browser driver, invoke a provider CLI or use a
human browser profile. Other platforms are unverified and fail the entrypoint.

Run `./tools/bootstrap.ps1` in PowerShell to prepare only the approved pinned
closure. Python installs require wheel hashes and npm lifecycle scripts are
disabled. Then run `.venv/Scripts/python.exe -m tools.ci` from the product root
(as a module: `python tools/ci.py` cannot import the `tools` package for the
browser stage). CI is local only; there is no hosted workflow. The stages run in order:

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

The backend suite has a420-second aggregate budget: a measured375-case run
completed in313seconds. The browser parent also has420seconds for three existing
120-second fresh-profile runs plus60seconds of fixture overhead. Individual test,
native startup/stop and browser interaction limits remain unchanged. A timed-out
stage always fails. Its output
is retained only in the ignored private failure log after the owned process tree
is stopped, within the existing15-second stop and10-second drain limits.

Optional browser progress files contain only fixed stage codes, a bounded count
and a cleanup boolean. They help locate a watchdog failure and never replace the
required final browser result or establish verified cleanup by themselves.

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
logs remain local and must never be uploaded or committed.

This gate covers implemented bootstrap contracts. It does not certify unfinished
finance/corpus/agent evaluations, real provider account login/conversations,
worker OS isolation or full MVP/Q05/Q06 release acceptance. Those gates must land
with their features before release. No-known-advisories is not a full security
review; semantic review and an exact-candidate security review remain required.

## Local-only merge gate

The owner chose local CI only (2026-11-06). Hosted GitHub Actions were retired,
so no source, report or log is processed by a hosted runner. Each pull request
merges only after a clean (non `--allow-dirty`) local `python -m tools.ci` pass on
the exact PR head commit, with `publication_candidate:true`, all eight stages
including the final real-browser check. The PR records that commit, tree, source
fingerprint, build identity and stage counts. A privacy scan of every commit to be
pushed must find no personal, client or credential data before any push; the
repository is public.
