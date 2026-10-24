import uuid

from django.core.management.base import BaseCommand, CommandError

from backend.maipa.policies import authority


class Command(BaseCommand):
    help = 'Withdraw a recorded grant as the trusted local operator, retaining its history.'

    def add_arguments(self, parser):
        parser.add_argument('--grant', required=True)
        parser.add_argument('--reference', required=True)

    def handle(self, *args, **options):
        try:
            authority.operator_revoke(uuid.UUID(options['grant']), options['reference'])
        except (authority.Denied, ValueError, TypeError):
            raise CommandError('Authority was not revoked: check the grant and withdrawal reference.') from None
        self.stdout.write('Authority revoked')
