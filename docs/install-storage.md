# Local installation storage

The verified local launcher owns one data directory, separate from replaceable
application source. On Windows its default is `%LOCALAPPDATA%/Maipa/data`.
`MAIPA_DATA_DIR` can select another absolute physical directory outside source.
Relative paths, source ancestors/descendants, reparse points and hardlinked files
refuse. Neither HTTP requests, preferences nor ACP workers select this path.
The settings import resolves configuration without creating or reading secrets.
When service environment variables are scrubbed, Windows defaults come from the
launch identity's OS environment rather than ambient home-directory variables.

The local launcher retains one authoritative signing key. A supplied
`MAIPA_SECRET_KEY` is saved privately on a new install and must match an existing
saved key. Local keys contain 50–256 printable Unicode characters, stored as exact UTF-8;
ordinary Django punctuation is supported and control characters are refused.
A missing key on an initialized install fails closed instead of generating a
replacement that would invalidate saved sessions. Restore the private key from
the verified snapshot before restarting. Production serving remains K01.

Before writing private bytes, the launcher applies a protected owner/LocalSystem
DACL with child inheritance to the directory. Keys and database/setup files also
receive protected file DACLs before use. POSIX mode handling exists, but actual
install maintenance is Windows-only until separately verified. Directory ACLs
do not isolate people who share the same OS account; Django membership and exact
record ownership remain the application boundary. Host administrators/operators
are trusted.

| State | Storage and boundary |
|---|---|
| Users, membership, intake, policies, onboarding, pane widths, web sessions | `maipa.sqlite3`, normal Django migrations and existing owner/revision guards |
| Install format identity | Private `installation.json`, explicit format/version and storage UUID |
| Development signing key | Private `development-key`, never served or logged |
| Optional legacy setup-code file | Private `local-setup-code`, preserved by migration/backup but unused by onboarding |
| Reserved durable provider/session/artifact files | Private `providers/`, `sessions/`, `artifacts/`; no provider readiness follows from directory existence |
| Transport-check capsules | `workers/`, ephemeral and excluded from snapshots |
| Launcher/maintenance exclusion | Private `maintenance.lock`, actual OS lock, held for the entire launcher lifetime |

Durable file names use server-owned ASCII keys. A snapshot refuses unknown root
state instead of silently omitting it. Actual provider homes, ACP session records,
artifact authorization, persisted appearance and approved view state still need
their implementing P05/P06/A01 slices. Current setup messages and pane widths
retain their existing server persistence. Custom authored/pinned views remain A07.

## Offline administrator maintenance

Stop the Genie-managed site before maintenance. Run these finite commands in a
Genie terminal from the product repository. Source/destination paths below are
illustrative; a destination must not already exist, including an empty directory.
The command has no overwrite/force mode and is not exposed through HTTP or agents.

```powershell
.venv\Scripts\python.exe -m backend.install backup --source C:\Private\Maipa --destination D:\PrivateBackups\Maipa-2026-10-24
.venv\Scripts\python.exe -m backend.install restore --source D:\PrivateBackups\Maipa-2026-10-24 --destination C:\Private\RecoveredMaipa
```

Backup acquires the real install lock and a SQLite IMMEDIATE write reservation,
then uses [Python's SQLite backup API](https://docs.python.org/3.13/library/sqlite3.html#sqlite3.Connection.backup)
through a separate read connection. This includes committed WAL content; copying
only the primary database file is insufficient. See the [SQLite backup contract](https://www.sqlite.org/backup.html).
All SQLite sidecars are checked before SQLite opens a filename.

Files are written into private sibling staging, flushed and checked. The manifest
has exact path/size/SHA256 entries and an explicit version; it contains no raw
credentials or records. Restore rejects unknown/duplicate fields, extra files,
unsafe names, unsupported versions, links, invalid keys, SQLite integrity or
foreign-key failures and file mismatches. Successful publication is a Windows
rename into an absent destination. Errors do not print paths, records, tokens or
raw OS/SQLite diagnostics. Owned staging cleanup must succeed.

A snapshot contains private database records and credential files. Its private
ACLs and hashes provide local access restriction and corruption detection;
snapshots are not encrypted exports or authenticated imports from untrusted
people. Keep them on storage that supports the required ACLs. Restore is an
operator-authorized return to the snapshot's point in time, including its users,
roles and sessions. Review recovered authority before exposing the recovered
site. Full MVP retention, revoked-authority cutover and disaster/power-failure
recovery remain Q02 before financial execution ships. This foundation has no
automatic online backup, finance execution or provider-login claim.

## Existing checkout-local installs

The launcher refuses to start a different install while the checkout still has
legacy database/key files without a verified binding. With the old site stopped:

```powershell
.venv\Scripts\python.exe -m backend.install adopt-legacy --source C:\Code\miapa-gapp --destination C:\Private\Maipa
```

This adopts the known pre-P05 `runtime/maipa.sqlite3`, signing key and optional
legacy setup-code file, keeps the original files, and protects the retained sensitive files.
Browser onboarding uses only the administrator's chosen username and password;
the optional legacy file is neither read nor required by setup.
Legacy diagnostics and transport-check capsules remain in the checkout.
Unexpected legacy provider/session/artifact directories refuse. The private
`runtime/install-location.json` binds that checkout to the adopted directory and
storage UUID; startup requires the same `MAIPA_DATA_DIR`. A clean replacement
checkout needs only the same data directory. Repeating adoption cannot replace
the existing target or create a second installation. Do not remove the original
files until recovery has been independently checked.

The old pre-P05 launcher has no maintenance lock. It must be stopped explicitly;
SQLite's writer exclusion alone cannot prove that an idle old server is stopped.
New launchers hold the actual install lock throughout migration and serving.

Tests use owned synthetic installs, actual migrations, separate processes,
signed web sessions, real WAL records, native locks/hardlinks/junctions and
independent Windows DACL inspection. They never inspect the user's real key,
setup code, provider files or private corpus. The final native-browser fixture
uses this same isolated data-root/settings preparation, including real scoped
zero-provider transport checks.
