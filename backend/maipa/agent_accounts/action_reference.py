"""Every approval operation the agent can request: its exact payload fields and its rules.

The provider cuts a tool description at about 4,000 characters, so the tool description carries only the
operation names and fields; the rules travel in the agent instructions. Tests keep both complete and short.
"""

# Longest tool description any provider is trusted to deliver whole.
DESCRIPTION_LIMIT = 3500

OPERATIONS = (
    ('books.create', '{entity_id,currency,period_start,period_end}',
     ''),
    ('account.create', '{book_id,code,name,category}',
     ''),
    ('account.cash', '{book_id,account_id,cash}',
     "to mark (true) or unmark (false) an ASSET account as a bank account or cash on hand; maipa_books_read shows each account's cash flag"),
    ('statement.import', '{book_id,filename,content[,account_id][,opening_balance,closing_balance]}',
     '(CSV text with columns date,description,amount[,reference]); give account_id, the marked bank or cash account the statement is from, whenever it is a bank statement; give both balances when the statement prints them, and the import is then refused unless the rows add up from one to the other'),
    ('statement.account', '{book_id,import_id,account_id}',
     'to tie a statement that was imported WITHOUT a bank account to the marked bank or cash account it came from (import_id is on each row of maipa_books_imports); refused if an entry made from its rows does not move that account by the row amount; no entries or amounts change'),
    ('statement.balances', '{book_id,import_id,opening_balance,closing_balance}',
     'to add the printed opening and closing balance to a statement imported without them, so it can be reconciled; refused unless its rows add up from one to the other'),
    ('reconciliation.record', '{book_id,import_id}',
     'to record that the person reviewed a statement and it agrees with the books; refused while maipa_books_reconcile reports any difference'),
    ('statement.match', '{book_id,row_id,journal_id}',
     "to settle a pending statement row with an entry that already records the same movement on that row's bank account (the second side of a transfer), so it is not booked twice; no amounts change"),
    ('statement.duplicate', '{book_id,row_id,of_row_id}',
     'to settle a pending statement row that repeats a row of ANOTHER statement of the same bank account (same date and amount), so it is not booked again; two rows of one statement are never duplicates'),
    ('journal.post', '{book_id,journal_id,payload_hash}',
     'for a draft entry you proposed'),
    ('accrual.post', '{book_id,date,description,lines,reverse_on}',
     'for an adjusting entry that belongs to one period only (for example an expense incurred in March but billed in April): one approval posts the entry on date and its exact opposite on reverse_on, a later date, usually the first day of the next month; both dates must be in open months'),
    ('workflow.schedule', '{draft_id,definition_revision,every,hour[,weekday][,day][,seed]}',
     "to start a published workflow by itself on a timetable: every is day, week (with weekday 0=Monday..6=Sunday) or month (with day 1-31, the last day in shorter months); hour is 0-23 in UTC, so convert from the person's local time and say the UTC hour; definition_revision is the published version from maipa_workflow_list, which the schedule keeps running even after later edits; seed is the optional input object each run starts with"),
    ('workflow.unschedule', '{schedule_id}',
     'to stop one (ids from maipa_workflow_list)'),
    ('schedule.create', '{book_id,description,lines,day,starts[,ends]}',
     'to save a recurring entry the person asked for (the same balanced lines, as in a journal entry, drafted for each month on day 1-31, moved to the last day of shorter months, from starts until ends or the period end); it posts nothing: due occurrences are drafted with maipa_books_prepare_due and each still needs journal.post'),
    ('schedule.stop', '{book_id,schedule_id}',
     'to stop one; its drafts and posted entries are not touched'),
    ('journal.discard', '{book_id,journal_id}',
     'to discard a DRAFT entry that should not be posted (wrong, or the person declined it); nothing was posted so no amounts change, the record is kept, and a statement row it came from becomes pending again so you can propose the corrected entry'),
    ('journal.reverse', '{book_id,journal_id,date}',
     'to undo a POSTED entry by posting its exact opposite on that date (each entry reverses once; the original is kept)'),
    ('opening.balance', '{book_id,balances,control}',
     'to record starting balances on the first day of the book period: balances is a list of {account_id,debit,credit} with exactly one side above zero per account (not 3900), and control is {debit,credit}, the totals of those figures as stated in the source records; any difference between debits and credits goes to 3900 Opening balance equity and is shown to the person; one opening entry per book, reverse it to correct it'),
    ('period.close', '{book_id,through}',
     'to lock the books through a date (no drafts may remain on or before it)'),
    ('group.create', '{name,book_ids}',
     'to save a named group of two or more of the person\'s books in the same currency so they can be read together; it changes no book'),
    ('group.eliminate', '{group_id,account_ids}',
     'to set which accounts of the member books hold dealings between the grouped businesses (amounts they owe each other, loans, investments in each other, sales and charges between them); account_ids is the complete list and replaces the earlier one, an empty list clears it. Those accounts are left out of the consolidated totals. Ask the person which accounts these are; never guess'),
    ('group.remove', '{group_id}',
     'to remove a group (ids from maipa_books_groups); the books and their entries are not touched'),
    ('report.release', '{book_id,from,to,title}',
     'to release the statements of a range whose end date is in a closed month: the income statement, balance sheet and cash flow are kept exactly as approved and numbered; if the books later change, the release is shown as out of date and a new release can be requested'),
    ('period.reopen', '{book_id,through}',
     'to move the lock back to an earlier date, or through null to reopen fully'),
    ('setup.confirm', '{setup_id,revision}',
     'when account setup is at the review step'),
)


def fields(operation):
    """The payload fields of an operation as written for the agent, or None."""
    return next((signature for name, signature, _ in OPERATIONS if name == operation), None)


def description():
    return ('Ask the person to approve one exact change to their records; you do the bookkeeping work this way. '
            'Operations and their exact payload fields ([..] optional): '
            + '; '.join(f'{name} {signature}' for name, signature, _ in OPERATIONS)
            + '. The rules of each operation are in your instructions under "Approval operations"; read them before the first use. '
            'A refused request names the fields it expected. '
            'Nothing changes until the person approves in the app. Write a short plain summary of what will change (amounts, accounts). '
            'Use one batch_id (a UUID you choose) for related requests so they can be approved together. You cannot approve.')


def instructions():
    return ('\nApproval operations (request each with maipa_action_request; fields in braces are the exact payload, [..] optional):\n'
            + '\n'.join(f'{name} {signature}' + (f': {detail}.' if detail else '.') for name, signature, detail in OPERATIONS) + '\n')
