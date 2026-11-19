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
| `account.cash` | `book_id`, `account_id`, `cash` (`true` or `false`) | mark or unmark an asset account as a bank account or cash on hand; no amounts change |
| `statement.import` | `book_id`, `filename`, `content` (CSV text), optional `account_id`, optional `opening_balance` with `closing_balance` | import statement rows as pending transactions; with balances the rows must add up from one to the other; for an attached PDF the agent supplies the rows and all three optional fields, and every figure must appear in the document; `account_id` names the marked bank or cash account the statement is from, and entries for its rows must then move that account by the exact amount |
| `journal.post` | `book_id`, `journal_id`, `payload_hash` | post the exact draft entry |
| `journal.reverse` | `book_id`, `journal_id`, `date` | post the exact opposite of a posted entry, linked to it; the original is kept and each entry reverses once |
| `opening.balance` | `book_id`, `balances` (list of `account_id`, `debit`, `credit`), `control` (`debit`, `credit`) | post one opening entry on the first day of the book period; the figures must add up to the control totals, and any difference between debits and credits goes to 3900 Opening balance equity as its own line; one active opening entry per book, reversed to correct |
| `period.close` | `book_id`, `through` | lock the books through a date; entries on or before it can no longer be proposed, posted or reversed into; refused while drafts remain in range |
| `period.reopen` | `book_id`, `through` (earlier date or `null`) | move the lock back or remove it; posted history is never altered |
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

## Files attached in chat

A CSV file (up to 512 KiB, at most three per message) attached in the composer
is uploaded to `POST /api/agent/attachments/` under the person's session before
the message is sent, and the message names each stored file by `attachment_id`.
The agent reads a bounded preview with `maipa_attachment_read`, and may request
`statement.import` with `{book_id, attachment_id}`; the server binds that exact
file's name and content into the request before the person approves it. Files
are private to their owner. Other file types are refused before sending.
