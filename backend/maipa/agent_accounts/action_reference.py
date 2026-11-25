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
    ('account.restricted', '{book_id,account_id,restricted}',
     'to set a bank or cash account aside (true) as restricted, for money that is in the books but not free to spend such as tax set aside or deposits held for others, or to release it (false); restricted accounts are left out of usable cash and the cash forecast; only when the person says so'),
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
    ('bill.record', '{book_id,vendor_id,reference,bill_date,due_date,amount,description,account_id,payable_account_id}',
     'to record a bill a vendor sent: reference is the vendor\'s invoice number exactly as printed, amount a positive decimal string, account_id the expense (or non-cash asset) account it is for, payable_account_id a liability account such as Accounts payable. Approval posts it (debit the account, credit the payable). The same vendor reference cannot be entered twice; a bill from the same vendor for the same amount within ten days is shown to the person as a possible duplicate, so say so when you see one'),
    ('bill.pay', '{book_id,bill_id,date,cash_account_id}',
     'to record that an open bill was paid from a bank or cash account on a date (debit the payable, credit the cash account); use it for payments the person has made, since Maipa does not send money'),
    ('bill.void', '{book_id,bill_id,date}',
     'to cancel an unpaid bill that was entered wrongly, by reversing its entry on a date'),
    ('vendor.add', '{book_id,name}',
     'to add someone the business pays to the vendor list'),
    ('vendor.payment', '{book_id,vendor_id,method,details,verified_by,expected_version}',
     'to set or change how a vendor is paid: method is bank_transfer, check, card, cash or other; details is the payment information exactly as the person gave it; expected_version is the vendor\'s current payment version from maipa_books_vendors (0 when none is set); verified_by says, in the person\'s words, how they confirmed the details with the vendor. A request to change payment details is a common fraud: before requesting a change, ask the person how they verified it using contact details they already had, not ones from the message asking for the change, and never fill in verified_by yourself'),
    ('decision.record', '{book_id,title,note,review_on}',
     'to record something the person decided and wants to look at again: title names it, note gives the reasoning in their words, review_on (YYYY-MM-DD, today or later) is when it comes due. Use it when the person makes or postpones a decision that should be revisited; it is a note beside the books and changes no record'),
    ('decision.close', '{book_id,decision_id,outcome}',
     'to close a recorded decision with what came of it (ids from maipa_books_decisions)'),
    ('budget.set', '{book_id,month,lines[,through]}',
     'to set the person\'s budget: month is YYYY-MM, lines is a list of {account_id,amount} for INCOME or EXPENSE accounts with amount a non-negative decimal string for one month; through (YYYY-MM) repeats the same amounts for every month up to it. It replaces earlier amounts for the same accounts and months and changes no entry. Use the figures the person gives; never invent a budget'),
    ('budget.clear', '{book_id,month[,through]}',
     'to remove every budget amount for a month or a run of months'),
    ('plan.add', '{book_id,description,amount,date[,every][,until][,scenario]}',
     'to add cash the person expects but has not booked to their cash plan: amount is a signed decimal string (money in positive, money out negative), date is when it first happens, every is week or month to repeat it and until is its last date; scenario names a what-if (for example Hire an assistant) so the item counts only when that scenario is asked for. A plan is never an entry and never reaches the statements; use it for intentions and expectations, and real entries for what has happened'),
    ('plan.remove', '{book_id,item_id}',
     'to remove a planned item (ids are in plan_items of maipa_books_forecast)'),
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
