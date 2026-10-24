"""Only a trusted console operator may grant installation membership."""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from backend.maipa.workspace.models import Installation, Membership


class WorkspaceProvisioningTests(TestCase):
    def test_console_grants_explicit_role_to_existing_password_user(self):
        user = get_user_model().objects.create_user(username="synthetic-provisioned", password="synthetic-test-passphrase")
        call_command("grant_workspace_access", username=user.username, role="administrator", verbosity=0)
        member = Membership.objects.get(user=user)
        self.assertEqual(member.role, "administrator")
        self.assertTrue(member.enabled)
        self.assertEqual(member.installation_id, Installation.objects.get(slot=1).id)
        user.refresh_from_db()
        self.assertTrue(user.check_password("synthetic-test-passphrase"))
        self.assertNotEqual(user.password, "synthetic-test-passphrase")

    def test_unknown_inactive_and_passwordless_users_get_no_membership(self):
        inactive = get_user_model().objects.create_user(username="synthetic-inactive", password="synthetic-test-passphrase", is_active=False)
        passwordless = get_user_model().objects.create_user(username="synthetic-passwordless")
        for name in ("synthetic-missing", inactive.username, passwordless.username):
            with self.assertRaises(CommandError):
                call_command("grant_workspace_access", username=name, role="administrator", verbosity=0)
        self.assertEqual(Installation.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)
