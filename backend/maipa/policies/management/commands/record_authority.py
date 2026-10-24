"""Explicit local operator provenance; never an HTTP or in-app agent tool."""
import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from backend.maipa.policies import authority


class Command(BaseCommand):
    help = 'Record explicit entity authority from a trusted local operator reference. No authority is inferred.'

    def add_arguments(self, parser):
        for flag in ('entity', 'username', 'action', 'duty', 'valid-from', 'expires', 'reference'):
            parser.add_argument('--'+flag, required=True)
        parser.add_argument('--limit')
        parser.add_argument('--currency')
        parser.add_argument('--agent')
        parser.add_argument('--can-delegate', action='store_true')

    def handle(self, *args, **options):
        try:
            user = get_user_model().objects.filter(username=options['username'], is_active=True).first()
            authority.require(user is not None)
            row = authority.provision_root(entity_id=uuid.UUID(options['entity']), subject_id=user.pk,
                action=options['action'], duty=options['duty'], starts_at=authority._date(options['valid_from']),
                expires_at=authority._date(options['expires']), limit=options['limit'], currency=options['currency'],
                evidence=options['reference'], can_delegate=options['can_delegate'],
                agent_id=uuid.UUID(options['agent']) if options['agent'] else None)
        except (authority.Denied, ValueError, TypeError):
            raise CommandError('Authority was not recorded: check the explicit principal, scope, provenance and bounds.') from None
        self.stdout.write('Recorded authority '+str(row.pk))
