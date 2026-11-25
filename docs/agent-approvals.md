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
| `statement.account` | `book_id`, `import_id`, `account_id` | tie a statement imported without a bank account to a marked bank or cash account; refused if an entry made from its rows does not move that account by the row amount; no entries or amounts change |
| `statement.balances` | `book_id`, `import_id`, `opening_balance`, `closing_balance` | add the stated balances to a statement imported without them; its rows must add up from one to the other |
| `reconciliation.record` | `book_id`, `import_id` | record that a statement was reviewed and agrees with the books; refused while any difference remains; one record per statement, later reported as changed if the figures move |
| `statement.match` | `book_id`, `row_id`, `journal_id` | settle a pending statement row with an existing entry that moves that row's bank account by exactly its amount, within ten days, and whose side is not already taken by another row; no amounts change |
| `statement.duplicate` | `book_id`, `row_id`, `of_row_id` | settle a pending statement row as a repeat of a row from another statement of the same bank account with the same date and amount; no entry is made; two rows of one statement are never duplicates |
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

## Released statements

`report.release {book_id, from, to, title}` records the income statement, balance sheet and cash flow of a range whose
end date is in a closed month, exactly as they stand when the person approves. Releases are numbered per book and are
append-only. `maipa_books_releases` lists them and marks each `unchanged` (the books still give the same figures) and
`latest` (no newer release of the same range); with `release_id` it returns the kept statements and each total that
differs today. A release is refused for an open period, for books with integrity findings, and when the latest release
of the same range already has the same figures.

## Keeping the operation list whole

Providers cut a tool description at about 4,000 characters. The `maipa_action_request` description therefore lists only
each operation and its exact payload fields; the rules of each operation are appended to the agent instructions. Both
come from `backend/maipa/agent_accounts/action_reference.py`. Tests require every server operation to appear with
exactly the fields the server enforces and every tool description to stay under 3,500 characters. A request with the
wrong payload shape is refused with `expected_payload`, the fields that were expected.

## Entity groups

`group.create {name, book_ids}` saves a named group of two or more of the person's own books in one currency;
`group.remove {group_id}` removes it. Neither changes a book. `maipa_books_groups` lists groups and, with a group and a
date range, returns the combined income statement, balance sheet and cash totals: every row and total carries one
amount per member book and their sum. Accounts combine only when code and name agree.

`group.eliminate {group_id, account_ids}` sets the complete list of member-book accounts that hold dealings between
the members (an empty list clears it). The report then carries `consolidated`: the totals without those accounts, each
removed amount, and `income_difference` and `balance_difference`. `eliminated` is true only when accounts are approved
and both differences are zero; a dealing recorded by one member only shows up as a difference, never as a silent
adjustment. The side-by-side figures are never changed.

## Usable cash and the 13-week forecast

`account.restricted {book_id, account_id, restricted}` sets a bank or cash account aside, or releases it. A restricted
account stays in the books and statements but is left out of usable cash. `maipa_books_forecast {book_id, from}`
returns usable cash before the start date and for each of the next 13 weeks. It uses only what the books hold: posted
entries dated in a week, drafts awaiting approval, and recurring entries not yet drafted (a drafted occurrence counts
once, as its draft). Unposted items dated before the start are late and land in week 1. Entries that move no usable
cash, such as an unpaid bill, are not items. Nothing outside the books is forecast, and the agent is told to say so.

## Cash plan and scenarios

`plan.add {book_id, description, amount, date[, every][, until][, scenario]}` adds cash the person expects but has not
booked; `plan.remove {book_id, item_id}` removes it. A planned item is not an entry: it changes no balance, no book
revision and no statement. `maipa_books_forecast` keeps its books-only weeks exactly as they are and adds `plan_items`,
`scenarios` and `plan`: the same 13 weeks with the approved plan on top (`planned_in`, `planned_out`, `closing`,
`items`, its own `lowest`). Items with a scenario name count only when that scenario is passed, on top of the plan.
Repeats fall every 7 days, or on the same day of each month with month ends clamped, up to `until`.

## Budgets and variance

`budget.set {book_id, month, lines[, through]}` saves the person's budget: one non-negative amount per income or
expense account for a month (`YYYY-MM`), optionally the same amounts for every month through another. It replaces
earlier amounts for the same accounts and months. `budget.clear {book_id, month[, through]}` removes every amount for
those months. A budget is not an entry: it changes no balance, book revision or statement.

`maipa_books_budget {book_id, from, to}` compares posted entries with the budget for whole months. Each income or
expense account with a budget or activity has `budget` (null when unbudgeted, never assumed zero), `budget_months`,
`actual`, `variance` (actual minus budget), `percent` of budget (two decimals, half up; null when the budget is zero)
and `favourable` (income at or above budget, expense at or below). Totals cover budgeted accounts; unbudgeted amounts
are listed separately, and `net_actual` equals the income statement's net income.

## Decision follow-up

`decision.record {book_id, title, note, review_on}` records something the person decided and wants to look at again,
with their reasoning and a review date (today up to ten years ahead; a request approved after its review date fails).
`decision.close {book_id, decision_id, outcome}` closes it with what came of it. `maipa_books_decisions {book_id,
as_of}` lists them: due ones first (review date reached, not closed), then open ones by review date, then closed ones,
with a count of those due. A decision is a note beside the books and changes no entry, balance, revision or statement.

## Vendors and payment details

`vendor.add {book_id, name}` adds someone the business pays. `vendor.payment {book_id, vendor_id, method, details,
verified_by, expected_version}` sets or changes how that vendor is paid. A change is prepared against the version it
replaces: if the current version differs from `expected_version` at request or approval time it fails, so a change is
never applied over details the person did not see. `verified_by` (at least ten characters, in the person's words)
records how the details were confirmed with the vendor. Versions are append-only.

The exact details are stored locally. Everywhere they are read back (`maipa_books_vendors`, request lists, the
"now" row of a change card) runs of five or more digits keep only their last four. The pending card shows the new
details in full so the person can check them. The change card warns that such requests are a common fraud.

## Bills

`bill.record` posts a vendor bill (debit the expense or non-cash asset account, credit a liability account) through
the normal journal path, source kind `bill`, and keeps the bill as open. `bill.pay` records a payment the person made
(debit the payable, credit a bank or cash account; source kind `billpay`) and marks it paid. `bill.void` cancels an
open bill by reversing its entry. Period locks apply to every date.

Duplicates: the same vendor reference (compared without case, spaces or punctuation) is refused while the earlier bill
is not void. A bill from the same vendor for the same amount within ten days is allowed but listed on the approval card
as a possible duplicate. `maipa_books_payables {book_id, as_of}` lists open bills, overdue first, with their total
beside the posted balance of the payable accounts; any difference is reported, never adjusted.

The summary of a `vendor.payment` request is stored with long digit runs masked, like the details themselves.

## Expense claims

`claim.record {book_id, claimant, date, lines, payable_account_id}` records costs someone paid personally: each line
`{date, description, amount, account_id[, receipt]}` is debited to its expense (or non-cash asset) account and the
total credited to a liability account for what is owed to people; source kind `claim`. `claim.reimburse` records the
payment back from a bank or cash account (source kind `claimpay`); `claim.void` reverses an open claim's entry. Only the
person's name is stored.

A receipt number (compared without case, spaces or punctuation) can be claimed once, by anyone, while the earlier claim
is not void, and only once within a claim. A line from the same person with the same date and amount as an earlier
line is allowed but listed on the card as a possible duplicate. `maipa_books_claims {book_id}` lists open claims by
person with what is owed to each.
