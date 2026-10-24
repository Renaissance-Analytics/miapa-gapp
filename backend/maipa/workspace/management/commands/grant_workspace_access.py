"""Operator console only. Never registered as an HTTP or agent tool."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from backend.maipa.workspace.models import Installation, Membership, ROLES


class Command(BaseCommand):
    help = "Grant an explicit workspace role to an existing active password user."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--role", required=True, choices=ROLES)

    def handle(self, *args, **options):
        if options["role"] not in ROLES:
            raise CommandError("Unknown workspace role")
        with transaction.atomic():
            user = get_user_model().objects.filter(username=options["username"], is_active=True).first()
            if user is None or not user.has_usable_password():
                raise CommandError("An existing active password user is required")
            installation, _ = Installation.objects.get_or_create(slot=1)
            Membership.objects.update_or_create(user=user, defaults={"installation": installation, "role": options["role"], "enabled": True})
        if options["verbosity"]:
            self.stdout.write("Workspace access granted.")
