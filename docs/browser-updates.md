# Browser update tracking

Maipa mounts the published `@particle-academy/fancy-app-update` detector once
in its workspace and uses `@particle-academy/fancy-whats-new` for release cards.
The footer shows the installed release name, build and **What's new** button. Newer deployed
builds offer **Refresh Maipa**; refresh is always the person's choice. There is
no automatic reload.

The detector checks `/api/version/` every 30 seconds and when the browser tab
regains focus. Requests are same-origin, uncached, have a five-second timeout
and refuse redirects. Failed checks do not announce an update. The endpoint
returns only the public build fingerprint, with `Cache-Control: no-store`;
missing or malformed build metadata produces a generic unavailable response.

Vite computes one SHA256 identity from the backend and frontend source, release
notes and dependency/configuration inputs. That same value is embedded in the
bundle and emitted to `frontend/dist/build-info.json`, which Django reads for
the version endpoint. Rebuilding identical inputs retains the identity. Backend,
frontend and note changes produce a new identity on the next build. Runtime
storage, credentials, installed modules and generated files are excluded.
Linked source paths are refused. Development HMR disables update detection;
the Genie-managed site serves the built bundle with detection enabled.

Release cards come from `frontend/src/releases.json` in the installed bundle,
so they describe the version the browser actually has. Keep groups newest first
and retain immutable release IDs. Add a new group for each meaningful delivered
change, describing only implemented behavior; do not present the whole roadmap
as finished. The build validates nonempty, distinct release groups and text cards.
The first visit shows the newest group; later loads show missed groups. The
button reopens the latest cards. Fancy owns selection and its seen cursor.

The seen cursor uses a separate browser-local cosmetic key for each authenticated
principal and the signed-out workspace. It is not authorization, server-saved
preference storage or cross-device synchronization. No release card contains
account, client, provider or finance data. Account/session resolution must finish
before a seen cursor is used.

Run the normal alignment-first backend/frontend checks, rebuild, and verify the
real browser against the version endpoint before claiming the update is live.
This development-site update does not publish source, approve a release, or
complete the still-open ACP/accounting/authorization/production gates.
