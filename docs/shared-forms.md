# Shared pending client input

When the live agent presents `Maipa.ClientCatalog`, its new-client form uses
the published Fancy Form and controlled Input components. The current human
session and native agent share one private draft through the same authorized
application service. A saved view reopened without a live agent lease keeps
its ordinary human client controls; it acquires no unrelated agent authority.

The published agent-integrations Form bridge exposes discovery and reads,
plus awaited `form_set_value` and atomic `form_set_values`. The native broker
binds the original actor/account/UI lease outside model arguments. A write
uses the agent's last observed revision: a newer human edit refuses the stale
write until the agent explicitly reads the current form. Removed components,
revoked authority and replaced form instances also refuse.

Filling changes pending input only. It creates no client/entity, grants no
authority and submits no financial information. Generic `form_submit` and
`form_focus` are unsupported and return errors. A human's explicit Create
client action retains the existing business-service authorization and catalog
revision checks. Clearing after submission is conditional on the original
pending version, so it cannot erase a newer shared draft.

The browser serializes its writes and retains newer typing while persistence
is pending. A genuine revision conflict preserves local unsent values and
offers explicit reload; it never automatically overwrites the current draft.
A changed authenticated actor or unavailable authority clears the old private
scope, and late responses cannot restore a closed form.

Only an accepted native mutation generates a bounded, value-free activity
receipt. New browsers opt in with `X-Maipa-Agent-Activity: 1`; older clients
retain their existing response shape. The receipt is restricted to the current
live UI lease and displayed once, briefly. Initial load/reload, read operations
and human edits do not invent new native activity. Raw public bridge activity
metadata, which may include field values, is never forwarded or persisted.

This feature uses a finite application-owned Node transport around the exact
published bridge. It does not add a listener, provider SDK, credential access,
native tool capability, package fork or substitute form bridge. Host-mode
provider execution remains local-user execution without OS filesystem
isolation; server-side application authority remains mandatory.
