# Agent approvals

Maipa's in-app agent does the bookkeeping. It never changes records directly:
every change is an **action request** that the person approves or rejects.

## Flow

1. The agent calls `maipa_action_request` with an operation, its exact payload
   and a short plain summary. Related requests can share one `batch_id`.
2. The request is stored as `pending` with a hash of the operation and payload.
   Recording it changes no records.
3. The conversation shows each pending request as an approval card (the
   published Fancy `ChatMessage` proposal card). A posting request shows the
   entry's date, description and lines as read by the server.
4. The person approves or rejects one card, or a whole batch. The decision
   carries the hash of what was shown.
5. On approval the server runs the existing domain service as that person,
   atomically, and records the result. If the target changed, the request ends
   as `failed` with a reason and nothing is half-done. A repeated approval
   returns the original outcome without running again.
6. The app then sends a short note on the person's behalf stating the exact outcomes
   (held until the agent's current reply finishes), so the agent carries on.
   The agent can also read outcomes with `maipa_action_list`. Pending is not done.

## Operations

| Operation | Payload | Runs |
|---|---|---|
| `books.create` | `entity_id`, `currency`, `period_start`, `period_end` | create books with the starter chart |
| `account.create` | `book_id`, `code`, `name`, `category` | add an account |
| `statement.import` | `book_id`, `filename`, `content` (CSV text) | import statement rows as pending transactions |
| `journal.post` | `book_id`, `journal_id`, `payload_hash` | post the exact draft entry |
| `setup.confirm` | `setup_id`, `revision` | save the reviewed account setup |

Statement content is never echoed back; a request shows its size, hash and a
short preview.

## Authority

- Only a signed-in human session can decide: `POST /api/approvals/<id>/decide/`
  and `POST /api/approvals/batch/<id>/decide/`, with CSRF and the actor header.
  There is no agent tool to approve or reject.
- Requests belong to the requesting person. Other users cannot list or decide
  them. Viewers can neither request nor decide.
- Targets are checked against the person's current authority when requested and
  again when executed.
