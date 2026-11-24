"""Refresh this install's SQLite file from the database it runs on, so a file backup holds current records.

Only for an install that has opted in to another database. Stop the site first. Prints counts only.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from backend.maipa import datacopy


class Command(BaseCommand):
    help = "Write the current records into the install's maipa.sqlite3 file, verified row by row."

    def handle(self, *args, **options):
        if connections.databases['default']['ENGINE'] == 'django.db.backends.sqlite3':
            raise CommandError('refused: this install already keeps its records in the SQLite file')
        if 'sqlite_file' in connections.databases:
            connections['sqlite_file'].close()
        try:
            counts = datacopy.export_to_sqlite('default', settings.DATA_ROOT / 'maipa.sqlite3')
        except datacopy.CopyRefused as refused:
            raise CommandError(f'refused: {refused.code}') from None
        self.stdout.write(f'exported rows: {sum(counts.values())} in {sum(1 for value in counts.values() if value)} models')
        self.stdout.write('IDENTICAL: the file holds the same rows as the database')
