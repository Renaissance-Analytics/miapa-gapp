# Personal appearance and work-view preferences

Signed-in members save System, Light or Dark appearance and their selected
built-in Client facts / Workspace guide view on the local server. The choices
restore after sign-in and in another browser. Signed-out appearance remains a
separate browser-local cosmetic setting; a signed-in server choice takes priority
over an old cached theme. Custom authored/pinned views remain separate A07 work.

The workspace shows loading and saving state. If another tab has changed the
saved choices, or a save cannot be confirmed, use **Reload saved preferences**
before making another change. Reload reads the authoritative saved choices.
Writes are never automatically replayed. Pane-width saves have an independent
identity and revision, so these choices do not overwrite divider gestures.

`GET /api/workspace/preferences/` initializes the current member's defaults.
`POST` requires exactly `expected_revision`, `theme`, and `active_view`, fresh
CSRF, and the server-created `X-Maipa-Preferences` identity. Strict bounded JSON,
finite enum/database checks and compare-and-swap reject unsupported or stale
requests. Current membership and installation determine ownership on every read
and write. Viewers may change their own presentation without gaining financial
authority. An old tab cannot write a newly signed-in account at a matching revision.

Preferences contain no credentials, provider state, entity/data selectors, paths,
URLs or executable schemas. The existing private published screens bridge and
human work-view controls share the same built-in navigation/save gate. Restoring
an accepted built-in selection does not submit another write. Custom compositions
remain bounded in-memory views; they are not persisted by this endpoint.

The new model uses normal migrations in the private installation SQLite database.
Actual backup/restore acceptance preserves its identity, revision, choices and
owner isolation through separate processes, alongside existing users/sessions,
onboarding, widths and private file permissions. Full service/session/artifact
references and live provider conversations remain their existing plan gates.
