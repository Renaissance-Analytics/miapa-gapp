# Shared bootstrap checks

`tools/bootstrap.ps1` prepares the approved hash/lock-pinned environment without
package lifecycle scripts. `.venv/Scripts/python.exe -m tools.ci` is the identical
local and Actions entrypoint. It enforces both alignment gates before application
tests and finishes with an isolated native-browser check of built React + Django.

The workflow uses an ephemeral Windows2025 runner, full Actions commit pins,
read-only contents permission and no persisted Git credentials or caches. It has
no provider secrets or privileged fork jobs. Only sanitized `report.json`
is uploaded; never upload runtime, browser profiles, SQLite, raw logs or env files.
Fork code still executes as untrusted code on the disposable runner; do not add
secrets, privileged jobs or self-hosted runners to this workflow.

See `docs/ci.md` for exact scope, prerequisites and evidence limits. Hosted
execution remains unverified until the first approved source publication.
