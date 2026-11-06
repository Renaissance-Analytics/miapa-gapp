# Checks are local only

There is no hosted GitHub Actions workflow for this repository. The owner chose
local-only CI on 2026-11-06, so no source, report or log is processed by a hosted
runner.

`tools/bootstrap.ps1` prepares the approved hash/lock-pinned environment without
package lifecycle scripts. Run `.venv/Scripts/python.exe -m tools.ci` from the
product root. It enforces both alignment gates before application tests and
finishes with an isolated native-browser check of built React + Django.

A pull request merges only after a clean local pass on its exact head commit and
a privacy scan of every commit being pushed. Never commit or upload runtime data,
browser profiles, SQLite databases, raw logs, env files or client records.

See `docs/ci.md` for scope, prerequisites and evidence limits.
