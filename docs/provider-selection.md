# Provider selection in the conversation

Connection setup is intended to discover and reuse supported subscription
accounts already signed in on this machine, as Genie does. Bind the chosen
machine identity explicitly to the current Maipa user; ask for a provider login
only when that account actually needs one. This supersedes the earlier separate
private-login default. Native adapters run in explicit host mode as the local
OS user, using published Prism0.33.1 subscription mode and the CLI's own sign-in.
Maipa does not read/copy credentials or share them into a container. The host
worker has verified job-based lifetime containment, without OS file isolation.
Only the supplied authorized UI tools are available to the model under the
published nativeTools:none policy. Codex requires an account-available model in
its measured restriction matrix; a default model exposing exec is refused.
Discovery alone does not supply an authenticated runtime or account authority.

The compact chat control selects a server-registered provider for the current
Maipa user. A first-time user chooses explicitly; connecting never creates a
Codex account implicitly. Existing Codex identities are preserved by migration.
Current storage retains one owned account per provider under its existing
uniqueness rule. Additional discovered accounts require a supported identity
contract and a migration that preserves those original records; do not fabricate
external account identifiers or copy credentials to approximate that support.

Selection is saved separately from each account's conversation revision. Every
browser mutation carries the exact selection revision, preventing another tab's
old draft or control request from reaching a newly selected account. Switching
requires a stopped worker; original transcripts, profile/session references and
UI authority remain bound to their original account. In-memory unsent drafts are
kept separately per account while switching, and do not enter browser storage.

`ACP_PROVIDER_LABELS` is an optional trusted Django setting mapping additional
registration IDs to display labels. IDs use lowercase letters/digits/hyphens,
start with a letter and contain at most64characters. Labels are printable,
nonempty and at most80characters; up to30additional registrations are accepted.
It cannot configure executable paths, commands, credentials, model capabilities
or tool permissions. Native discovery and generic runtime integration await the
published Prism client contract. A label does not establish runtime availability.

Codex and Claude have separate integrated native adapters in this build, with
their own pinned executable, profile and authentication/startup contracts.
Additional registrations preserve their selection and display unavailable
support until an actual adapter is supplied; they never fall back to Codex.
The historical original Codex status failure remains unconfirmed. Actual
authenticated model/UI acceptance and generic ACP interoperability remain open.

Provider-neutral chat/sign-in copy uses the selected label. Sign-in presentation
accepts bounded HTTPS challenges, optional outbound provider codes and an optional
private callback control. Each adapter validates its own authentication protocol.
Claude callback input is transient and sent only to the current owned native
login process; it never becomes a saved message or ordinary chat draft.

Changing provider or removing its registration does not reset existing account,
profile, session, storage or transcript identities. A removed registration remains
unavailable. After new provider data exists, recovery to older schemas should use
an intact compatible backup rather than discarding unsupported records.
