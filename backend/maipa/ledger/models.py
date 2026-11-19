"""Native books, immutable financial proposals and retained source lineage."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

CATEGORIES = ('asset', 'liability', 'equity', 'income', 'expense')


class Book(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.OneToOneField('intake.Entity', on_delete=models.PROTECT)
    currency = models.CharField(max_length=3)
    period_start = models.DateField()
    period_end = models.DateField()
    # Entries dated on or before this day can no longer be proposed, posted or reversed into.
    closed_through = models.DateField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(period_end__gte=models.F('period_start')),
                                               name='ledger_book_period'),
                       models.CheckConstraint(condition=models.Q(revision__lt=2_000_000_000),
                                              name='ledger_book_revision')]


class Account(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    book = models.ForeignKey(Book, related_name='accounts', on_delete=models.PROTECT)
    code = models.CharField(max_length=16)
    name = models.CharField(max_length=128)
    category = models.CharField(max_length=9)
    # A bank account or cash on hand. Only the person's approval changes it.
    cash = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('book', 'code'), name='ledger_account_code'),
                       models.CheckConstraint(condition=models.Q(category__in=CATEGORIES), name='ledger_account_category'),
                       models.CheckConstraint(condition=models.Q(cash=False) | models.Q(category='asset'), name='ledger_account_cash_asset')]


class AppendOnly(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('Ledger evidence is append-only')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Ledger evidence is append-only')


class Journal(AppendOnly):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    book = models.ForeignKey(Book, related_name='journals', on_delete=models.PROTECT)
    operation_id = models.UUIDField()
    sequence = models.PositiveIntegerField()
    request_hash = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64)
    state = models.CharField(max_length=6, default='draft')
    date = models.DateField()
    description = models.CharField(max_length=500)
    source_kind = models.CharField(max_length=8)
    source_reference = models.CharField(max_length=256)
    evidence = models.JSONField()
    total_units = models.BigIntegerField()
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('book', 'operation_id'), name='ledger_journal_operation'),
                       models.UniqueConstraint(fields=('book', 'sequence'), name='ledger_journal_sequence'),
                       models.UniqueConstraint(fields=('book', 'source_kind', 'source_reference'), name='ledger_journal_source'),
                       models.CheckConstraint(condition=models.Q(state__in=('draft', 'posted')), name='ledger_journal_state'),
                       models.CheckConstraint(condition=models.Q(total_units__gt=0, total_units__lte=999999999999999999), name='ledger_journal_total')]


class JournalLine(AppendOnly):
    journal = models.ForeignKey(Journal, related_name='lines', on_delete=models.PROTECT)
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    position = models.PositiveSmallIntegerField()
    debit_units = models.BigIntegerField(default=0)
    credit_units = models.BigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('journal', 'position'), name='ledger_line_position'),
                       models.CheckConstraint(condition=(models.Q(debit_units__gt=0, credit_units=0)
                           | models.Q(credit_units__gt=0, debit_units=0)), name='ledger_line_one_side')]


class LedgerEvent(AppendOnly):
    book = models.ForeignKey(Book, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=24)
    target = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('book', 'revision'), name='ledger_event_revision')]


class PostingReceipt(AppendOnly):
    journal = models.OneToOneField(Journal, primary_key=True, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    observed_revision = models.PositiveIntegerField()
    revision = models.PositiveIntegerField()
    payload_hash = models.CharField(max_length=64)
    at = models.DateTimeField(default=timezone.now)


class SourceImport(AppendOnly):
    """One uploaded statement file. The exact bytes' hash makes re-imports idempotent."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    book = models.ForeignKey(Book, related_name='imports', on_delete=models.PROTECT)
    sha256 = models.CharField(max_length=64)
    filename = models.CharField(max_length=128)
    row_count = models.PositiveIntegerField()
    # The bank or cash account this statement belongs to, when the importer named one.
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT)
    # The statement's own opening and closing balance, when given; the rows then add up from one to the other.
    opening_units = models.BigIntegerField(null=True, blank=True)
    closing_units = models.BigIntegerField(null=True, blank=True)
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('book', 'sha256'), name='ledger_import_file_once')]


class Reconciliation(AppendOnly):
    """A statement the person reviewed and recorded as agreeing with the books, with the figures as they then stood."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    statement = models.OneToOneField('SourceImport', related_name='reconciliation', on_delete=models.PROTECT)
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    as_of = models.DateField()
    closing_units = models.BigIntegerField()
    book_units = models.BigIntegerField()
    unrecorded_units = models.BigIntegerField()
    outstanding_units = models.BigIntegerField()
    figures_hash = models.CharField(max_length=64)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)


class SourceRowMatch(AppendOnly):
    """A statement line settled without an entry of its own: by an entry another line already produced (the second
    side of a transfer), or as a repeat of a line from an overlapping statement."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # A line can be settled again after the entry that settled it is reversed, so this is not one-to-one.
    row = models.ForeignKey('SourceRow', related_name='settlements', on_delete=models.PROTECT)
    journal = models.ForeignKey(Journal, null=True, blank=True, related_name='matches', on_delete=models.PROTECT)
    duplicate_of = models.ForeignKey('SourceRow', null=True, blank=True, related_name='duplicates', on_delete=models.PROTECT)
    matched_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(journal__isnull=False, duplicate_of__isnull=True)
                                              | models.Q(journal__isnull=True, duplicate_of__isnull=False), name='ledger_row_match_one_kind')]


class SourceRow(AppendOnly):
    """One statement line exactly as imported; journals cite it by ID."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_import = models.ForeignKey(SourceImport, related_name='rows', on_delete=models.PROTECT)
    row_number = models.PositiveIntegerField()
    date = models.DateField()
    description = models.CharField(max_length=500)
    amount_units = models.BigIntegerField()
    reference = models.CharField(max_length=128, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('source_import', 'row_number'), name='ledger_import_row_once'),
                       models.CheckConstraint(condition=~models.Q(amount_units=0), name='ledger_import_row_nonzero')]
