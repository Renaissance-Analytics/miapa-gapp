# Provider selection in the conversation

The compact chat control selects a server-registered provider for the current
Maipa user. A first-time user chooses explicitly; connecting never creates a
Codex account implicitly. Existing Codex identities are preserved by migration.
One owned account per provider is retained under the existing uniqueness rule.

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

Only the currently integrated Codex adapter can attempt a live connection in
this build. Claude and additional registered providers preserve their selection
and display unavailable support; they never fall back to Codex. The known Codex
startup/profile issue and real-model acceptance are still open. This app-side
selection change does not claim arbitrary ACP interoperability or complete A01.

Provider-neutral chat/sign-in copy uses the selected label. Sign-in presentation
accepts bounded HTTPS challenges and optional provider codes; the current Codex
adapter still validates its own actual authentication protocol. No other provider
authentication is enabled by broadening the UI's presentation contract.

Changing provider or removing its registration does not reset existing account,
profile, session, storage or transcript identities. A removed registration remains
unavailable. After new provider data exists, recovery to older schemas should use
an intact compatible backup rather than discarding unsupported records.
