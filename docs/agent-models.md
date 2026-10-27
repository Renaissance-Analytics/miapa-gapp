# Provider-reported models

The compact chat control shows models actually advertised by the selected ACP
session. Maipa invents no provider-wide model list. An absent/empty catalog has
no model selector; malformed or duplicate entries cannot become selectable.
Native model IDs and display names are bounded; descriptions and vendor metadata
do not cross into app/browser state. Existing provider-specific restrictions
apply: the restricted Codex driver advertises only its measured permitted model.

Only the browser session owning the current worker receives its model catalog
or can change that model. Each change validates the original active user, role,
account, Django session, provider selection revision and exact conversation
revision. It atomically reserves an idle conversation before sending the public
ACP session/set_model request. Pending input, an active turn, stale revisions,
foreign sessions and unknown model IDs are refused. The selected label changes
only after the provider accepts the request; refusal or an uncertain result stops
the connection rather than claiming the requested model is active.

The ACP acknowledgment confirms the selection for subsequent turns; it does not
independently prove which serving model handled a later provider request.
Model changes preserve chat, drafts, shared UI identity and saved accounting
records. The choice applies to the current live session; reconnecting asks the
provider for its current/default model again. Catalogs are transient, not durable
readiness or account credentials. They disappear when their worker stops and are
never used to bypass native-tool policy. General ACP transport/discovery remains
separate supported-package work.

New clients opt into catalog fields with `X-Maipa-Agent-Models: 1`; ordinary
snapshots and streams preserve the old response shape without that header. This
is format negotiation, not authority. Existing open tabs keep working while the
person chooses when to load the new build through Fancy's updater.
