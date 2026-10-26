"""Owned durable references are authority, not provider readiness or public file access."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import OperationalError, connections
from django.test import Client, TestCase, override_settings

from backend.maipa.agent_accounts.models import AgentAccount
from backend.maipa.storage import prepare_directory, prepare_files
from backend.maipa.workspace.models import Installation, Membership
from tests.test_install_storage import descriptor

PAYLOAD = b'Exact synthetic workpaper bytes\x00\x01\xff'
HANDLE = 'synthetic-provider-session:original/opaque'


class ServiceReferenceTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='maipa-owned-references-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.data = self.base / 'install'
        prepare_directory(self.data)
        prepare_files(self.data)
        configured = override_settings(DATA_ROOT=self.data)
        configured.enable()
        self.addCleanup(configured.disable)
        self.installation = Installation.objects.create()
        self.owner = get_user_model().objects.create_user(username='reference-owner')
        self.other = get_user_model().objects.create_user(username='reference-other')
        self.member = Membership.objects.create(installation=self.installation, user=self.owner, role='preparer')
        Membership.objects.create(installation=self.installation, user=self.other, role='preparer')
        self.account = AgentAccount.objects.create(owner=self.owner, provider='codex', label='Synthetic owned account')
        self.foreign = AgentAccount.objects.create(owner=self.other, provider='codex', label='Synthetic foreign account')

    def api(self):
        from backend.maipa.agent_accounts import references
        return references

    def session(self):
        return self.api().record_session(self.owner, str(self.account.pk), HANDLE)

    def artifact(self):
        session = self.session()
        receipt = self.api().store_artifact(self.owner, str(self.account.pk), session, PAYLOAD)
        return session, receipt

    def refused(self, operation):
        with self.assertRaises(self.api().ReferenceUnavailable) as caught:
            operation()
        self.assertEqual(str(caught.exception), 'Saved agent resource unavailable')
        self.assertIsNone(caught.exception.__cause__)

    def payload_path(self, receipt):
        return self.data / 'artifacts' / uuid.UUID(receipt.artifact_id).hex / 'payload'

    def test_durable_opaque_session_resolves_exact_private_handle_without_disclosure(self):
        reference = self.session()
        self.assertEqual(str(uuid.UUID(reference)), reference)
        self.assertNotEqual(reference, HANDLE)
        from backend.maipa.agent_accounts.models import SessionReference
        saved = SessionReference.objects.get(pk=reference)
        self.assertEqual((str(saved.user_id), str(saved.account_id), str(saved.installation_id), saved.provider),
                         (str(self.owner.pk), str(self.account.pk), str(self.installation.pk), 'codex'))
        loaded = self.api().resolve_session(self.owner, str(self.account.pk), reference)
        self.assertEqual((loaded.provider, loaded.value), ('codex', HANDLE))
        self.assertNotIn(HANDLE, repr(loaded))
        self.assertNotIn(HANDLE, repr(saved))
        self.refused(lambda: self.api().resolve_session(self.owner, str(self.account.pk), HANDLE))

    def test_wrong_actor_account_and_public_urls_cannot_reach_real_saved_payload(self):
        session, receipt = self.artifact()
        self.refused(lambda: self.api().resolve_session(self.other, str(self.account.pk), session))
        self.refused(lambda: self.api().resolve_session(self.owner, str(self.foreign.pk), session))
        self.refused(lambda: self.api().read_artifact(self.other, str(self.foreign.pk), session, receipt.artifact_id))
        client = Client()
        client.force_login(self.owner)
        for url in (f'/api/agent/sessions/{session}/', f'/artifacts/{uuid.UUID(receipt.artifact_id).hex}/payload'):
            response = client.get(url, secure=True)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn(PAYLOAD, response.content)
            self.assertNotIn(HANDLE.encode(), response.content)
        self.assertEqual(self.payload_path(receipt).read_bytes(), PAYLOAD)

    def test_disabled_reassigned_and_changed_provider_accounts_never_adopt_history(self):
        session, receipt = self.artifact()
        def denied():
            self.refused(lambda: self.api().resolve_session(self.owner, str(self.account.pk), session))
            self.refused(lambda: self.api().read_artifact(self.owner, str(self.account.pk), session, receipt.artifact_id))
        AgentAccount.objects.filter(pk=self.account.pk).update(enabled=False)
        denied()
        AgentAccount.objects.filter(pk=self.account.pk).update(enabled=True, provider='claude')
        denied()
        AgentAccount.objects.filter(pk=self.account.pk).update(owner=self.other)
        self.refused(lambda: self.api().resolve_session(self.other, str(self.account.pk), session))
        AgentAccount.objects.filter(pk=self.account.pk).update(owner=self.owner, provider='codex')
        self.assertEqual(self.api().resolve_session(self.owner, str(self.account.pk), session).value, HANDLE)

    def test_fresh_database_user_and_membership_authority_apply_to_every_operation(self):
        session, receipt = self.artifact()
        def denied():
            self.refused(lambda: self.api().resolve_session(self.owner, str(self.account.pk), session))
            self.refused(lambda: self.api().read_artifact(self.owner, str(self.account.pk), session, receipt.artifact_id))
            self.refused(lambda: self.api().store_artifact(self.owner, str(self.account.pk), session, b'no'))
            self.refused(lambda: self.api().record_session(self.owner, str(self.account.pk), 'new-synthetic-handle'))
            self.refused(lambda: self.api().revoke_session(self.owner, str(self.account.pk), session))
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        denied()  # self.owner is deliberately a stale active object.
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=True)
        Membership.objects.filter(pk=self.member.pk).update(enabled=False)
        denied()
        for role in ('viewer', 'reviewer'):
            Membership.objects.filter(pk=self.member.pk).update(enabled=True, role=role)
            denied()
        Membership.objects.filter(pk=self.member.pk).update(role='administrator')
        self.assertEqual(self.api().read_artifact(self.owner, str(self.account.pk), session, receipt.artifact_id), PAYLOAD)

    def test_original_installation_and_private_storage_binding_cannot_be_replaced(self):
        session, receipt = self.artifact()
        from backend.maipa.agent_accounts.models import SessionReference
        SessionReference.objects.filter(pk=session).update(installation_id=uuid.uuid4())
        self.refused(lambda: self.api().resolve_session(self.owner, str(self.account.pk), session))
        SessionReference.objects.filter(pk=session).update(installation=self.installation)
        metadata = self.data / 'installation.json'
        original = metadata.read_bytes()
        value = json.loads(original)
        metadata.write_text(json.dumps(value | {'storage_id': str(uuid.uuid4())}))
        self.refused(lambda: self.api().read_artifact(self.owner, str(self.account.pk), session, receipt.artifact_id))
        metadata.write_bytes(original)
        self.assertEqual(self.api().resolve_session(self.owner, str(self.account.pk), session).value, HANDLE)

    def test_revocation_is_durable_and_closes_handle_artifact_reads_and_writes(self):
        session, receipt = self.artifact()
        self.api().revoke_session(self.owner, str(self.account.pk), session)
        self.refused(lambda: self.api().resolve_session(self.owner, str(self.account.pk), session))
        self.refused(lambda: self.api().read_artifact(self.owner, str(self.account.pk), session, receipt.artifact_id))
        self.refused(lambda: self.api().store_artifact(self.owner, str(self.account.pk), session, PAYLOAD))
        self.refused(lambda: self.api().record_session(self.owner, str(self.account.pk), HANDLE))
        self.assertEqual(self.payload_path(receipt).read_bytes(), PAYLOAD)

    def test_duplicate_private_handle_refuses_without_replacing_original_identity(self):
        reference = self.session()
        from backend.maipa.agent_accounts.models import SessionReference
        self.refused(lambda: self.api().record_session(self.owner, str(self.account.pk), HANDLE))
        self.assertEqual(SessionReference.objects.count(), 1)
        self.assertEqual(str(SessionReference.objects.get().pk), reference)
        self.assertEqual(self.api().resolve_session(self.owner, str(self.account.pk), reference).value, HANDLE)

    def test_invalid_handles_ids_and_payload_budgets_refuse_before_file_creation(self):
        api = self.api()
        for handle in ('', '\nprivate', 'a'*4097, True, None, {}, b'bytes'):
            self.refused(lambda: api.record_session(self.owner, str(self.account.pk), handle))
        session = self.session()
        for reference in ('../outside', HANDLE, 'https://foreign.invalid', '', True, None, {}):
            self.refused(lambda: api.resolve_session(self.owner, str(self.account.pk), reference))
        for payload in ('text', bytearray(b'mutable'), None, b'x'*(1_048_576+1)):
            self.refused(lambda: api.store_artifact(self.owner, str(self.account.pk), session, payload))
        self.assertFalse((self.data/'artifacts').exists())

    def test_immutable_artifact_receipt_matches_exact_bytes_digest_size_and_private_acl(self):
        session, receipt = self.artifact()
        self.assertEqual(receipt.size, len(PAYLOAD))
        self.assertEqual(receipt.sha256, hashlib.sha256(PAYLOAD).hexdigest())
        self.assertEqual(str(uuid.UUID(receipt.artifact_id)), receipt.artifact_id)
        self.assertNotIn(str(self.data), repr(receipt))
        from backend.maipa.agent_accounts.models import ArtifactReference
        saved = ArtifactReference.objects.get(pk=receipt.artifact_id)
        self.assertEqual(str(saved.session_id), session)
        self.assertEqual(self.api().read_artifact(self.owner, str(self.account.pk), session, receipt.artifact_id), PAYLOAD)
        path = self.payload_path(receipt)
        if os.name == 'nt':
            acl = descriptor(path)
            self.assertIn('D:P', acl)
            self.assertEqual(acl.count('(A;'), 2)
        else:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_corruption_truncation_missing_file_and_wrong_session_never_return_bytes(self):
        session, receipt = self.artifact()
        path = self.payload_path(receipt)
        for payload in (PAYLOAD[:-1], bytes([PAYLOAD[0]^1])+PAYLOAD[1:], PAYLOAD+b'extra'):
            path.write_bytes(payload)
            self.refused(lambda: self.api().read_artifact(self.owner, str(self.account.pk), session, receipt.artifact_id))
        path.unlink()
        self.refused(lambda: self.api().read_artifact(self.owner, str(self.account.pk), session, receipt.artifact_id))
        another = self.api().record_session(self.owner, str(self.account.pk), 'another-synthetic-handle')
        self.refused(lambda: self.api().read_artifact(self.owner, str(self.account.pk), another, receipt.artifact_id))

    def test_hardlink_payload_is_refused_without_touching_foreign_bytes_or_acl(self):
        session, receipt = self.artifact()
        target = self.base/'foreign-bytes'
        target.write_bytes(PAYLOAD)
        path = self.payload_path(receipt)
        path.unlink()
        path.hardlink_to(target)
        before = descriptor(target) if os.name == 'nt' else target.stat().st_mode
        self.refused(lambda: self.api().read_artifact(self.owner, str(self.account.pk), session, receipt.artifact_id))
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertEqual(descriptor(target) if os.name == 'nt' else target.stat().st_mode, before)

    def test_reparse_artifact_directory_is_refused_without_touching_foreign_target(self):
        session, receipt = self.artifact()
        directory = self.payload_path(receipt).parent
        self.payload_path(receipt).unlink()
        directory.rmdir()
        target = self.base/'foreign-directory'
        target.mkdir()
        (target/'payload').write_bytes(PAYLOAD)
        before = descriptor(target/'payload') if os.name == 'nt' else (target/'payload').stat().st_mode
        if os.name == 'nt':
            result = subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(directory),str(target)],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0)
        else:
            directory.symlink_to(target, target_is_directory=True)
        self.refused(lambda: self.api().read_artifact(self.owner, str(self.account.pk), session, receipt.artifact_id))
        self.assertEqual((target/'payload').read_bytes(), PAYLOAD)
        self.assertEqual(descriptor(target/'payload') if os.name == 'nt' else (target/'payload').stat().st_mode, before)

    def test_failed_durable_write_and_database_publication_clean_only_new_owned_files(self):
        session = self.session()
        from backend.maipa.agent_accounts.models import ArtifactReference
        with patch('backend.maipa.agent_accounts.references.os.fsync', side_effect=OSError('synthetic-private-fsync')):
            self.refused(lambda: self.api().store_artifact(self.owner, str(self.account.pk), session, PAYLOAD))
        self.assertEqual(ArtifactReference.objects.count(), 0)
        self.assertEqual(list((self.data/'artifacts').iterdir()), [])
        with patch.object(ArtifactReference.objects, 'create', side_effect=OperationalError('synthetic-private-database')):
            self.refused(lambda: self.api().store_artifact(self.owner, str(self.account.pk), session, PAYLOAD))
        self.assertEqual(ArtifactReference.objects.count(), 0)
        self.assertEqual(list((self.data/'artifacts').iterdir()), [])
        receipt = self.api().store_artifact(self.owner, str(self.account.pk), session, PAYLOAD)
        self.assertEqual(self.api().read_artifact(self.owner, str(self.account.pk), session, receipt.artifact_id), PAYLOAD)

    def test_access_revoked_during_real_payload_read_is_checked_before_return(self):
        session, receipt = self.artifact()
        path = self.payload_path(receipt)
        original = Path.open
        @contextmanager
        def reading(current, *args, **kwargs):
            with original(current, *args, **kwargs) as stream:
                yield stream
            if current == path:
                Membership.objects.filter(pk=self.member.pk).update(enabled=False)
        with patch.object(Path, 'open', reading):
            self.refused(lambda: self.api().read_artifact(self.owner, str(self.account.pk), session, receipt.artifact_id))
        self.assertFalse(Membership.objects.get(pk=self.member.pk).enabled)
        self.assertEqual(path.read_bytes(), PAYLOAD)

    def test_storage_identity_collision_never_removes_or_replaces_existing_payload(self):
        session, receipt = self.artifact()
        with patch('backend.maipa.agent_accounts.references.uuid.uuid4', return_value=uuid.UUID(receipt.artifact_id)):
            self.refused(lambda: self.api().store_artifact(self.owner, str(self.account.pk), session, b'replacement refused'))
        from backend.maipa.agent_accounts.models import ArtifactReference
        self.assertEqual(ArtifactReference.objects.count(), 1)
        self.assertEqual(self.api().read_artifact(self.owner, str(self.account.pk), session, receipt.artifact_id), PAYLOAD)

    def test_session_database_failure_never_returns_or_publishes_a_reference(self):
        from backend.maipa.agent_accounts.models import SessionReference
        with patch.object(SessionReference.objects, 'create', side_effect=OperationalError('synthetic-private-database')):
            self.refused(lambda: self.api().record_session(self.owner, str(self.account.pk), HANDLE))
        self.assertEqual(SessionReference.objects.count(), 0)
        reference = self.session()
        self.assertEqual(self.api().resolve_session(self.owner, str(self.account.pk), reference).value, HANDLE)

    def test_malformed_storage_metadata_and_parser_failure_have_fixed_safe_errors(self):
        session = self.session()
        metadata = self.data/'installation.json'
        original = metadata.read_bytes()
        raw = b'['*1500+b'0'+b']'*1500
        metadata.write_bytes(raw)
        try:
            self.refused(lambda: self.api().resolve_session(self.owner, str(self.account.pk), session))
        finally:
            metadata.write_bytes(original)
        # This runtime parses the bounded nesting and refuses its shape. Separately
        # exercise a private parser failure at the metadata boundary, without
        # claiming the native decoder hit a recursion limit on that input.
        with patch('backend.maipa.agent_accounts.references.installation_metadata',
                   side_effect=RecursionError('synthetic private parser diagnostic')):
            self.refused(lambda: self.api().resolve_session(self.owner, str(self.account.pk), session))
        self.assertEqual(self.api().resolve_session(self.owner, str(self.account.pk), session).value, HANDLE)
