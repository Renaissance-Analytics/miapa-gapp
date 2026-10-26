# Local users and access

An enabled administrator opens **Users and access** in the dynamic work area.
The conversation stays mounted and this temporary view does not replace the saved
Client facts/Workspace guide preference. Other roles do not get this navigation;
every API read and write independently checks current server authority.

Administrators can create a local username and confirmed password, assign one of
the four workspace roles, and change a membership's role or enabled status. New
users have ordinary Django flags and their own private setup conversation. Give
login details privately. Passwords are validated and hashed by Django, cleared from
the form, and excluded from responses and audit history. Password reset, deletion,
provider credentials and elevated Django flags are outside these controls.

POST requires CSRF, the original actor header, installation identity and expected
shared access revision. The transaction claims the revision before rechecking
current active administrator membership. It serializes member-limit and last-admin
checks and commits user/membership changes together with their audit event. A
failed check or audit insert rolls everything back. Existing signed-in sessions
use the updated membership on subsequent requests. Self-demotion is permitted only
when another enabled active administrator remains, and loses administration on
the next read. At least one such administrator must remain.

The list is bounded to 200 local memberships; JSON writes to 4096bytes. Read models
contain only public member fields and the last20 safe access events. Events retain
actor/subject IDs, resulting role/enabled state, kind, revision and time. The UI
limits response bytes, checks exact schemas and actor identity, and discards stale
lists on conflicts, denial or unconfirmed writes. Reload explicitly before another
attempt. Audit retention/maintenance remains part of later operational acceptance.

These controls do not grant access to another user's client facts, provider homes,
private session handles or artifacts, and do not change financial authority grants.
P06 remains open for other implemented-subsystem controls and actual scoped Prism
provider/profile/login/session integration. Live provider readiness is not claimed.
