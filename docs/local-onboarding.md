# Local onboarding

After migrating a pristine database, the local launcher prepares a random,
one-time code in the ignored `runtime/local-setup-code` file. On Windows the file
has a protected ACL for the launcher identity and LocalSystem; on Unix it is mode
0600. The file's contents are never returned by an HTTP route or printed to logs.
A future packaged launcher must run the same preparation after migration; other
trusted launchers can use `python backend/manage.py prepare_local_setup` with their
normal server settings. The current Genie development entrypoint does this
automatically.

On first open, Maipa offers **Create local administrator**. Read the local file
on the installation's computer, paste the code, choose a username and enter a
strong matching password of at least 12 characters. Credentials go only to the
CSRF-protected same-origin setup endpoint; they are not part of the chat. There
is no default password. Success creates the installation, hashed user and explicit
administrator membership atomically, then rotates the authenticated session.
Passwords, codes and confirmation fields clear after submission.

First-run creation closes after one successful claim. It also stays unavailable
for legacy databases containing an installation or any user; disabled/deleted
administrators do not reopen it. The singleton compare-and-swap rejects duplicate
claims; concurrent SQLite write contention returns a retryable unavailable result.
Existing console-provisioned members continue to use the normal sign-in form.
The residual code file is inert after closure and is never a recovery password.

Each signed-in user has **Account setup** in the persistent Conversation panel.
The setup guide saves accepted answers to the server and resumes after reload,
logout or restart. It collects the user's display name, an explicitly new or
owned existing client, currency, reporting basis (including unknown) and the
effective date. A review and explicit confirmation precede real changes. New
engagements/entities and append-only facts use the same private intake services
and evidence/revision rules as the work area. Existing facts changed during setup
cause a conflict; reload and restart the draft to select current facts. Unknown
transport results require reconciliation before another write. A stale duplicate
confirmation never creates another engagement.
Each reply also names its server-created setup identity. An old browser tab
cannot apply its answer to a different account after the session changes, even
when their revision counters happen to match.

Users may defer client setup and resume it here later. Reviewers/viewers can
save their own display name but cannot create financial records or grant roles.
**Open saved client facts** refreshes the work panel by explicit user choice.
No personal or financial setup transcript is stored in browser localStorage.

This is a deterministic account setup guide, labeled as such. It does not claim
to be a live model conversation or connect an ACP subscription. Agent connection
still shows setup required. Full provider provisioning, live chat and persistent
general conversations remain P06/A01/P04 work in the same plan.
