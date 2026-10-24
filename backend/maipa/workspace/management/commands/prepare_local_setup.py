"""Prepare a local operator code without printing its contents or granting membership."""
from django.conf import settings
from django.core.management.base import BaseCommand

from backend.maipa.workspace.bootstrap import prepare_local_setup, required


class Command(BaseCommand):
    help = 'Prepare first-run browser setup on this computer; never create an administrator from command arguments.'

    def handle(self, *args, **options):
        if not required():
            self.stdout.write('This installation is already configured. First-run setup is closed.')
            return
        prepare_local_setup()
        self.stdout.write(f'Open Maipa in your browser. The private local setup code is in {settings.LOCAL_SETUP_CODE_FILE}.')
