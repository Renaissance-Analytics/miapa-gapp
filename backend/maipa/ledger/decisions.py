"""Decision follow-up.

A decision is a note the person approved recording beside one set of books: what was decided, why, and the date to
look at it again. It becomes due on that date and stays due until the person approves closing it with what came of
it. It is not an entry and changes no balance, book revision or statement.
"""
from datetime import timedelta
import uuid

from django.db import transaction
from django.utils import timezone

from .models import Decision
from .services import day, fields, guarded, owned, require, text

MAX_OPEN = 100
MAX_LISTED = 200


def today():
    return timezone.localdate()


def record_check(book, value):
    """A titled decision with its reasoning and a review date from today up to ten years ahead. Read-only."""
    fields(value, ('title', 'note', 'review_on'))
    title, note, review_on = text(value['title'], 200), text(value['note'], 1000), day(value['review_on'])
    require(review_on >= today(), 'decision_review_in_past')
    require(review_on <= today() + timedelta(days=3653), 'decision_review_too_far')
    require(book.decisions.filter(closed_on=None).count() < MAX_OPEN, 'decision_limit')
    return {'title': title, 'note': note, 'review_on': review_on}


def describe(book, value):
    checked = record_check(book, value)
    return {'books': book.entity.name, 'title': checked['title'], 'note': checked['note'], 'review_on': checked['review_on'].isoformat(),
            'decided_on': today().isoformat(), 'current': True}


@guarded
@transaction.atomic
def record(user, book_id, value):
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    row = Decision.objects.create(book=book, created_by=user, decided_on=today(), **record_check(book, value))
    return {'decision_id': str(row.pk)}


def _decision(book, decision_id):
    try:
        wanted = uuid.UUID(decision_id) if type(decision_id) is str else None
    except ValueError:
        wanted = None
    row = book.decisions.filter(pk=wanted).first() if wanted else None
    require(row is not None and str(wanted) == decision_id, 'decision_not_found')
    return row


def close_check(book, value):
    fields(value, ('decision_id', 'outcome'))
    row, outcome = _decision(book, value['decision_id']), text(value['outcome'], 1000)
    require(row.closed_on is None, 'decision_already_closed')
    return row, outcome


def describe_close(book, value):
    row, outcome = close_check(book, value)
    return {'books': book.entity.name, 'title': row.title, 'review_on': row.review_on.isoformat(), 'outcome': outcome, 'current': True}


@guarded
def owner_book(user, book_id):
    user, book = owned(user, book_id, True)
    require(book.created_by_id == user.pk, 'ledger_confirmation_denied')
    return book


@guarded
@transaction.atomic
def close(user, book_id, value):
    row, outcome = close_check(owner_book(user, book_id), value)
    row.closed_on, row.outcome = today(), outcome
    row.save(update_fields=('closed_on', 'outcome'))
    return {'decision_id': str(row.pk), 'closed': True}


@guarded
@transaction.atomic
def listing(user, book_id, value):
    """Decisions for these books as of a date: those due for review first, then open ones by review date, then closed ones. Read-only."""
    _, book = owned(user, book_id)
    fields(value, ('as_of',))
    as_of = day(value['as_of'])
    rows = list(book.decisions.order_by('review_on', 'created_at')[:MAX_LISTED])

    def state(row):
        return 'closed' if row.closed_on else 'due' if row.review_on <= as_of else 'open'
    order = {'due': 0, 'open': 1, 'closed': 2}
    rows.sort(key=lambda row: (order[state(row)], -row.closed_on.toordinal() if row.closed_on else row.review_on.toordinal()))
    shown = [{'id': str(row.pk), 'title': row.title, 'note': row.note, 'decided_on': row.decided_on.isoformat(), 'review_on': row.review_on.isoformat(),
              'outcome': row.outcome, 'closed_on': row.closed_on.isoformat() if row.closed_on else None, 'state': state(row)} for row in rows]
    return {'book_id': str(book.pk), 'as_of': as_of.isoformat(), 'due': sum(1 for item in shown if item['state'] == 'due'), 'decisions': shown}
