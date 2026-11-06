# Fine-grained shared components

The provider-neutral `maipa_ui_catalog` advertises supported registered components
and their closed props. Existing scoped present/update tools compose them; personal
view tools save their definitions. The only fixed view remains persistent chat.

- `Maipa.Heading`: `props: {text}` with up to120 characters, rendered by Fancy Heading.
- `Maipa.Callout`: `props: {text, tone}` with up to2000 characters and tone
  `info`, `attention` or `neutral`. Fancy Callout and ContentRenderer show commentary,
  not a verified financial conclusion or a successful-write receipt.
- `Maipa.ClientTable`: `props: {columns}` with an ordered, unique nonempty subset
  of `name` and `entity_count`. One per composition. Fancy Table displays current
  client names/entity counts supplied only by the existing authorized catalog service.

No arbitrary attributes, rows, callbacks, actor/entity IDs, code, URL or selector
bindings are accepted. Definitions persist column selection rather than private
record snapshots. Fresh login/reopen fetches current records using fresh service
authority; no old native lease is restored. Reload clears previous rows immediately,
and a failed/denied read leaves no stale rows. Disposed read generations cannot
publish late data. Column order comes from controlled schema and stable row handles
come from service IDs. Sorting, selection, row actions and financial edits are not
enabled by this read-only component.

The public Fancy5.70.1 Heading/Callout/Table/ContentRenderer and Screens0.7.1 are
used directly. No dependency, package implementation, auth, or provider policy changes
are required. Human/native finite-peer integration checks do not establish actual
model acceptance; that is a separate required gate.
