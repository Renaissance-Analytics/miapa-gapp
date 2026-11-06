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
It is shown as explained steps: every step says what it is for, finished steps show
the answer recorded so far, and the current step can be answered directly. Setup is
saved on the server and resumes after reload, logout or restart. It collects the
user's display name, an explicitly new or owned existing client, currency,
reporting basis (including unknown) and the effective date. A review and the
person's explicit **Confirm and save setup** precede real changes. New
engagements/entities and append-only facts use the same private intake services
and evidence/revision rules as the work area. Existing facts changed during setup
cause a conflict; reload and restart the draft to select current facts. Unknown
transport results require reconciliation before another write. A stale duplicate
confirmation never creates another engagement.

When an agent provider is connected, the real in-app agent guides setup in the
conversation. Its scoped `maipa_setup_read` tool returns the current step, its
question and explanation, the person's step path and valid choices;
`maipa_setup_record` records only answers the person gave in conversation (stored
as agent-recorded), restarts the unsaved draft or reopens deferred setup when
asked. It cannot confirm the review: only the human button saves client records.
Invalid answers return a refusal for the agent to explain; stale revisions conflict.
The panel reloads after each agent turn so recorded answers appear beside the chat.
Without a connected provider the panel says plainly that it is a form, not an agent.
Each reply also names its server-created setup identity. An old browser tab
cannot apply its answer to a different account after the session changes, even
when their revision counters happen to match.

Users may defer client setup and resume it here later. Reviewers/viewers can
save their own display name but cannot create financial records or grant roles.
**Open saved client facts** refreshes the work panel by explicit user choice.
No personal or financial setup transcript is stored in browser localStorage.

The panel itself is not a model and never presents itself as one. Agent connection
uses the provider's own supported sign-in; credentials are never part of setup.
