# Private provider execution profiles

The trusted backend can reserve one `ProviderProfile` for an enabled original
account. Its opaque UUID binds the original installation, storage identity,
user, account and provider. It does not record authentication, readiness or an
available model. There is no generic HTTP, chat, agent-tool or file-read route
for these homes. Reassignment of an account/provider cannot adopt an old profile.

`provision_profile(actor, account_id)` checks current enabled user/membership,
administrator or preparer role and account ownership against the database. It
reserves an immutable binding and creates a private home under the server's
installation storage. Existing directories require an exact original marker;
damaged or incomplete directories are refused rather than repaired or deleted.

`lease_profile(actor, account_id, profile_id)` is a trusted-consumer context.
It acquires the same native nonblocking lock primitive used for installation
maintenance, within this profile's own directory. Competing threads/processes
cannot use the home concurrently. OS process death releases the lock; home
contents remain durable. Every entry rechecks original binding and current
authority. Consumers call `lease.revalidate()` before each real operation.
A closed lease cannot be reused. Private paths do not appear in the lease repr.

`WorkerCapsule(..., profile_lease=lease)` accepts only a live lease matching
its original scope/provider and profile root. Its `isolation_root` encompasses
the leased home and the capsule's temporary read-only code/supervisor and writable
workspace. The capsule removes only its own newly created worker directory.
Temporary zero-provider capsules retain their existing behavior when no lease
is supplied. A real native worker has verified assigned-home access and denied
foreign-home access; the published host initializes and stops with zero provider
sessions in this layout. These checks establish neither login nor a healthy turn.

The lease must enclose the entire worker lifetime: first stop the published
provider tree, then verify the outer OS job is empty, remove its AppContainer
profile and close the temporary capsule, then exit the lease. Releasing a lease
while a consumer still runs violates this trusted backend contract. On lease
exit, validate physical paths and files before restoring private directory DACLs;
AppContainer profile deletion alone leaves its temporary SID grants on retained
files. No cleanup recursively deletes a durable home. Links, hardlinks, altered
markers and trees above4096entries or512MiB fail safely at lease boundaries.
These metadata checks are not a running worker's OS disk quota. Unremoved
capsules after a crash or failed stop refuse a second consumer; explicit offline
recovery remains necessary. Only file metadata and the small app-owned marker
are examined; provider credential bytes are not read.

Actual provider/login/session integration remains unfinished. The locked
Prism ACP0.28.0 supports AppContainer-compatible LOCAL process pipes. Real
native MCP relay tests launch Node children, exchange tool traffic, refuse
foreign capabilities and enforce frame budgets. Child/outer jobs and profile/
capsule cleanup verify empty. These synthetic tool cases resolve the tested
pipe boundary; they do not establish a vendor login or healthy model turn.
Keep the OS boundary and published package intact; no host-user fallback,
copied human login, substituted SDK or package patch is supported.
The later runtime must use the same original assigned home for provider login
and actual acknowledged sessions and verify bounded startup/I/O/cancel/stop.

No new dependency, provider distribution, network capability or credential mode
is adopted by this internal profile service. Agent Settings and resumable account
setup must continue to report actual unfinished provider setup until real login
and session/turn acceptance pass.
