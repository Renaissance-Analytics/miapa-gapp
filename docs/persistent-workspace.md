# Persistent workspace

The conversation panel remains mounted while the User Panel changes work views,
resizes, or switches between mobile panels. Account setup remains the existing
server-persisted guided conversation. Its label distinguishes it from a connected
model. General ACP conversation streaming, cancellation and recovery require the
provider/session work tracked in P06 and A01; this contribution does not enable it.

The published Fancy `ResizablePanels` owns pointer and keyboard gestures. A
completed gesture saves an integer number of basis points, from 2200 to 4500,
through `/api/workspace/layout/`. The default is 2800. The server owns the user,
installation, opaque layout identity and revision. Every active installation role
may save its own presentation. This grants no financial permissions.

Writes require CSRF, the loaded layout identity and an exact expected revision.
Conflicts preserve the winner. An uncertain acknowledgement disables further
writes until the person explicitly reloads the saved layout. Controls also wait
for the initial read and an in-flight save. A principal change resets the local
controller and discards its late responses. Saved widths restore from the server
after reload, sign-in and in a fresh browser profile; localStorage is not used.

The User Panel renders published `fancy-screens` schemas. Client facts and the
workspace guide are registered views, with no page navigation. Each mounted
principal has a private `MicroMcpServer` and published `registerScreensBridge`
adapter. No network relay or public tool endpoint is opened. A01 must connect this
host to a currently authorized ACP session before a live model can operate it.

The host accepts only `Maipa.Stack`, `Maipa.ClientFacts`,
`Maipa.WorkspaceGuide` and `Maipa.Note`. Schemas have unique stable node IDs,
bounded depth and node counts, at most one fact surface and no executable props,
URLs, identity hints or data bindings. Facts load through the existing server
authorization boundary; model-provided data cannot select an owner. Notes are
escaped text and explicitly unverified commentary. There are at most eight work
views, including the two protected builtins. Dynamic compositions are session
state; durable custom-view authoring/pinning belongs to A07.

Fancy's schema component registry is global, so validation happens before a
schema reaches `Screen`; the registry is not an authorization boundary. The
bridge's optional undo/history agent ID is bound to its mounted host. All tools,
including automatically registered undo tools, are withdrawn on disposal. Invalid
operations preserve the current catalog.
