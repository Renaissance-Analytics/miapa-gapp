"""Keep existing launcher guidance compatible with browser-only local setup."""
from django.core.management.base import BaseCommand

from backend.maipa.workspace.bootstrap import required


class Command(BaseCommand):
    help = 'Prepare first-run browser setup on this computer; never create an administrator from command arguments.'

    def handle(self, *args, **options):
        if not required():
            self.stdout.write('This installation is already configured. First-run setup is closed.')
            return
        self.stdout.write('Open Maipa in your browser to create your administrator account, then continue with account setup in the conversation.')
