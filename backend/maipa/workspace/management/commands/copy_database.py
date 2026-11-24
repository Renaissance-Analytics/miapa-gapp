"""Copy every record from one configured database to another empty one, or compare two databases.

Prints model names and row counts only: never a record value or a connection setting.
"""
from django.core.management.base import BaseCommand, CommandError

from backend.maipa import datacopy


class Command(BaseCommand):
    help = 'Copy all records between two configured databases and verify them row by row.'

    def add_arguments(self, parser):
        parser.add_argument('--source', required=True)
        parser.add_argument('--target', required=True)
        parser.add_argument('--verify-only', action='store_true', help='Compare the two databases and change nothing.')

    def handle(self, *args, source, target, verify_only, **options):
        try:
            if not verify_only:
                counts = datacopy.copy(source, target)
                for label in sorted(counts):
                    if counts[label]:
                        self.stdout.write(f'{label}: {counts[label]}')
                self.stdout.write(f'copied rows: {sum(counts.values())} in {sum(1 for value in counts.values() if value)} models')
            differences = datacopy.compare(source, target)
        except datacopy.CopyRefused as refused:
            raise CommandError(f'refused: {refused.code}') from None
        if differences:
            for label in sorted(differences):
                item = differences[label]
                self.stdout.write(f'DIFFERENT {label}: source {item["source_rows"]} rows, target {item["target_rows"]} rows')
            raise CommandError(f'databases differ in {len(differences)} models')
        self.stdout.write('IDENTICAL: every model has the same rows in both databases')
