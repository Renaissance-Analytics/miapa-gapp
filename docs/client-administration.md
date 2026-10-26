# Client setup administration

Client setup is a fixed optional Fancy screen beside the persistent conversation.
An enabled administrator or preparer can manage their own client/engagement
records and entity display names. Installation administration does not grant
access to another user's private client records. The existing Client facts
surface still owns financial setup, evidence and history.

The new `/api/intake/catalog/` API serves one original owner's bounded metadata
catalog: up to100engagement summaries and100entities for one selected owned
engagement. `engagement_id` is an optional canonical UUID query; unknown/foreign
selections cannot disclose another record. Replies contain original actor,
installation and catalog identity, current revision and up to20safe metadata
events. Names/facts/provider state are not copied into the event trail.

POST supports only `create_engagement`, `create_entity`, `rename_engagement` and
`rename_entity`. Each command includes its exact original identity, installation
and expected revision, with the mounted actor in `X-Maipa-Client-Actor`, and uses
fresh same-origin CSRF. Display renames keep entity IDs, fact revisions, evidence,
dependent conclusions and financial authority intact. They do not establish a
new legal identity, financial approval or provider readiness.

`ClientCatalogState` binds one user to the original installation. The database
revision claim, record changes and immutable safe event share a transaction.
Existing intake/chat creation uses that same guard/event path while retaining
the original API/return shape. Its changes therefore invalidate stale setup
screens too. Conflicts, exhausted revisions, wrong origin/state/actor, foreign
targets, quota failures and audit failures cannot partially commit. SQLite
contention may return a safe unavailable response; it never means a saved change.
Metadata transactions take their write boundary before catalog reads, preventing
two deferred readers from both failing a shared-cache lock upgrade. The initial
no-op update does not advance the revision or create an event; fresh authority
and the exact original revision claim remain required for the actual change.
Existing installations start their new metadata event trail at revision0 rather
than fabricating events for earlier records.

The client bounds and validates snapshots/receipts, duplicates, selected-page
counts, scalar types and exact identities/actions/targets/next revisions. It
sends one supported write and does not retry an unconfirmed result. The controlled
view synchronously locks pending writes, discards late results after principal or
component disposal, clears forms/list after uncertainty/conflict and requires an
explicit reload. Human and agent navigation use the published shared screens
bridge. The `client-setup` ID and private schema type are reserved; arbitrary
compositions cannot create/replace/mutate that administrative screen.

Selecting this optional screen leaves saved Client facts/Workspace guide,
appearance, panel width, Account setup and conversation state intact. The role
discovery request only controls its visible navigation; every server read/write
checks current authorization independently. Fancy Development update18 describes
these client controls and explicitly retains unfinished provider sign-in/live
conversation work. No new dependency or generic finance/model administration is
introduced.
