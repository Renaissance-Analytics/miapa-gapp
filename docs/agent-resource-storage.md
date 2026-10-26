# Private agent resource storage

P05 supplies internal trusted-host services in `agent_accounts.references`. It
does not connect a provider, create a protocol session, authenticate a native CLI,
resume a conversation, or expose provider handles through HTTP/model tools.
P06 and A01 own those real consumers and their additional runtime acceptance.

`record_session(actor, account_id, private_handle)` accepts an acknowledged
protocol handle from the trusted application host and returns a new application
UUID. The private database mapping retains the original user, account, provider,
installation and private-storage identity. `resolve_session` requires that
authenticated actor and account independently of the reference and returns a
host-only `SessionHandle`. Its representation omits the private value. A raw
provider handle, path or URL cannot select a saved reference. Handles are bounded
to 4096 UTF-8 bytes and printable text. Duplicate handles for an account/provider
are refused, including after revocation; they never adopt or replace history.

Every operation reads current database user/account/membership authority. Only
active enabled administrators/preparers may use their own account resources.
Account reassignment/provider changes, disabled accounts/users/memberships,
role downgrades, installation/storage mismatches and reference revocation refuse.
Saved objects and caller-supplied owner hints cannot override this policy.
`revoke_session` permanently closes that application's reference, including its
artifact operations. It preserves private evidence; it does not send a provider
cancel request or perform a retention/deletion decision.

`store_artifact(actor, account_id, session_id, payload)` accepts immutable bytes,
at most 1 MiB, and returns only an `ArtifactReceipt` with UUID, size and SHA256.
The server alone selects `DATA_ROOT/artifacts/<server UUID hex>/payload`.
Directories/files receive the existing native private-storage protection before
payload writes. Exclusive creation refuses collisions without touching existing
content. Bytes are flushed/fsynced before database publication, with current
session authority rechecked before the receipt row commits. Failures return one
fixed error; cleanup removes only newly created physical identities, without
recursive deletion or following replacements. Unreferenced private files confer
no read authority. Q02 retains abrupt crash/orphan reconciliation and retention.

`read_artifact` requires the actor/account/session/artifact tuple. It refuses
symlinks/reparse points, hardlinks, wrong physical identity, missing files,
oversized/truncated/changed payloads and digest mismatch. It bounds the read and
checks authority again before returning bytes. Artifact content has no update or
public-file endpoint. The operating system's protected install root is the host
boundary; this service does not claim to sandbox other programs running as the
same privileged operating-system user.

The existing offline backup/restore copies the real database mappings and private
payloads together with the storage identity. Separate-process recovery acceptance
resolves original UUIDs to the exact synthetic handle and binary bytes, rejects
another user and revoked membership, and verifies restored native file privacy.
All earlier signing-key, account setup, pane-width, appearance, lock, failure and
recovery acceptance remains required. Real credentials are never included in public
source, browser release notes, browser fixture input or CI evidence artifacts.

Legacy adoption retains the existing pre-storage account/database records and
creates a new private storage identity. A database copied without modern storage
metadata/payloads must not acquire modern reference authority under that identity.
The legacy fixture explicitly verifies denial; complete modern backup/restore
requires the original storage identity and exact reference contents. No automatic
reference rebinding or filesystem-path fallback is permitted.

The browser check seeds a real synthetic private reference/file through these
services, proves its bytes exist, then confirms browser requests for session/file
URLs return404. The only values passed to that browser are opaque app UUIDs.
Failure reports may include a fixed first-run chat checkpoint from an explicit
allowlist. Raw answers, DOM text, paths, error stacks and credentials remain in
local diagnostics only. The original PR10 setup_chat failure remains unexplained;
the diagnostic change improves future evidence and does not claim a root-cause fix.
