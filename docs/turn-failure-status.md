# Account-scoped turn failure status

The public Prism0.33.1 failed `session/prompt` error exposes
`error.data["particle.academy/turn_failure"]` directly, not inside `_meta`.
Maipa accepts this metadata only on a correlated prompt refusal. It retains
only `rate_limit`, `overloaded`, `auth`, `billing` or `other` and an optional
valid integer `resetsAtMs` for rate limits. Unknown or absent provider causes
remain unclassified. No arbitrary reason, RPC message, raw frame, stderr,
prompt, token or credential enters this status object.

The private conversation stores a closed `{kind, resets_at_ms?}` DTO. The host
adds `unclassified` only to describe an incomplete turn without an established
cause. Existing actor/account/installation/session/role authority applies.
Failure state stays with its original account across selection, login and
restart; a different account cannot inherit it. Unknown/corrupt optional stored
status is masked without breaking the conversation. A new accepted send clears
the prior failure; reconnecting does not erase a reported cooldown.

New HTTP/SSE clients opt in with `X-Maipa-Agent-Failures: 1`. Legacy clients
keep their existing response shape. A future reported rate-limit reset refuses
new sends with429 before adding a queued request or starting a worker. An exact
retry of an already acknowledged request remains idempotent and never repeats
that turn. At expiry, a person may submit a new request; nothing is retried
automatically. No reset time is invented when the provider supplies none.

The existing Connection drawer uses public Fancy Callout for details and an
accessible local-time reset. Its body scrolls within available viewport height.
The compact chat status reports the known category; new drafts remain editable
during cooldown and Send stays disabled until the reported reset. The backend
still enforces the limit if a browser sends directly or has a different clock.

The additive agent_accounts0008 migration adds nullable status only. Preview
upgrade must preserve every prior column/value, with failure null for original
rows, plus the expected migration record. No historical provider observation
from a developer fixture may be copied onto a user's account.

Finite peers verify protocol privacy, persistence, worker shutdown, browser
display and send refusal. They do not establish actual provider turn success,
diagnose an unconfirmed Codex failure, or supply generic ACP interoperability.
