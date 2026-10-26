# Local onboarding

Open the locally installed Maipa in your browser. On first open, choose an
administrator username, enter a strong password of at least 12 characters,
confirm it and select **Create administrator**. No setup code, file lookup or
terminal command is needed. The launcher migrates a new local database before
serving the application.

Credentials go only to the
CSRF-protected same-origin setup endpoint; they are not part of the chat. There
is no default password. Success creates the installation, hashed user and explicit
administrator membership atomically, then rotates the authenticated session.
Password and confirmation fields clear after submission. The next step is
**Account setup** in the persistent conversation panel.

First-run creation closes after one successful claim. It also stays unavailable
for legacy databases containing an installation or any user; disabled/deleted
administrators do not reopen it. The singleton compare-and-swap rejects duplicate
claims; concurrent SQLite write contention returns a retryable unavailable result.
Existing console-provisioned members continue to use the normal sign-in form.
Legacy setup-code files are unused and never act as recovery passwords. The old
`prepare_local_setup` management command remains compatible with existing launchers;
it displays browser guidance and creates no files or users.

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
