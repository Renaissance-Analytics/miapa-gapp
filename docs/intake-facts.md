# Engagements and dated client facts

An engagement owns one or more named entities. At this stage its creating
workspace member owns all its records. Current administrators and preparers can
create and record their own facts; reviewers and viewers can read their own
records. Administrator status does not reveal another member's engagements.
Explicit entity sharing and detailed finance policies belong to P03.

The authenticated dynamic work area uses the installed published Fancy Select,
DatePicker, Input, Button and Card. It shows every fact as known or unknown,
with separate controls for legal form and tax classification. A recorded contact
does not grant application access. No client record is saved in localStorage.

The catalog includes legal form, tax classification, tax elections, ownership,
reporting periods, currency, reporting basis, jurisdictions, industry, source
systems, records quality, records completeness, authorized contacts and
engagement scope. A known boolean false is retained as false. Unknown requires
null; an empty known collection means none recorded. LLC does not populate any
tax assertion. These are recorded client assertions, not tax/legal conclusions.

Each revision has a source reference, location, observation date, effective-from
and optional exclusive effective-until date, server recorder and timestamp.
References are inert text: this task neither fetches them nor claims to verify
or ingest the underlying document. Evidence ingestion and source ownership are
separate E05/E06 work. Ownership percentages remain decimal strings with at most
four decimal places, each in 0–100 and total at most100. Partial lists are
allowed. Currency requires three uppercase letters; that is a format check,
not a claim that a code is currently issued or supported for accounting.

Reads as of a date choose the latest recorded revision per key among assertions
effective on that date. An assertion outside its half-open effective interval
does not apply. Later bounded corrections can override only their own interval;
original applicable evidence remains available outside it. Future assertions do
not silently replace present facts. History preserves all original assertions,
evidence and recorder metadata, with fifty rows per cursor page.

Every write binds the entity's expected revision. A stale write returns409 and
changes nothing. The form keeps ordinary invalid edits for correction; conflict
or uncertain results require explicit reload and review before another save.
No automatic retry can duplicate a fact revision.

Reload facts explicitly refreshes both the engagement list and selected record,
including after a failed first read. It retains the selected entity if still
authorized. List loading is announced as busy; entity creation and selection wait
for the list response to avoid racing stale list state against new records.

The first local-install
contract caps engagements per owner and entities per engagement at100, keeping
complete initial lists bounded; rejected creation leaves no partial entity.

`intake.services.record_conclusion` is an internal seam for later workpapers.
It accepts only exact known, applicable, current fact revisions from the same
authorized entity and captures an as-of date. Recording any assertion for a
depended-on key marks its current dependent conclusions stale in the same
transaction, conservatively including backdated/future corrections. Other keys
remain current. Known false is a valid basis. Revalidation must create a fresh
reviewed conclusion; the API exposes no arbitrary current/stale toggle.

All application writes go through these services. Instance edits/deletes of
fact history are refused and relational/check constraints protect its shape.
Trusted console SQL or bulk ORM writes must not rewrite history; this is not
a claim of database-administrator tamper resistance. Backup, recovery and
full audit/retention controls remain Q01/Q02/P05 work.

Routes are `/api/intake/engagements/`,
`/api/intake/engagements/<uuid>/entities/`,
`/api/intake/entities/<uuid>/`,
`/api/intake/entities/<uuid>/facts/`, and
`/api/intake/entities/<uuid>/facts/<key>/history/`.
Entity reads accept `on=YYYY-MM-DD`; history accepts positive `before` revision
with an exclusive boundary. Writes require real session membership, current
role, CSRF, strict bounded JSON, exact schemas and server-owned installation/
owner scope. Unknown resources return generic404. Invalid schema returns400,
oversize bodies413, stale revisions409 and database unavailability503. Responses
are uncached and never reflect raw errors.

The shared pipeline runs alignment first and the actual browser last. P02 adds
native form/keyboard creation, false/LLC independence, evidence history, actual
conflict and post-logout user isolation cases while retaining every P01 worker,
authentication, theme, mobile, updater and owned-cleanup case. Provider sessions,
finance execution, corpus evaluation and full release acceptance remain disabled
or separately gated as before.
