"""Fresh-process synthetic Django install for offline recovery acceptance only."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.maipa.storage import physical_chain, strict_json

# Fail before Django setup or any filesystem write. This diagnostic must never
# default to the user's install, even when executed manually by mistake.
raw_root = os.environ.get('MAIPA_DATA_DIR')
if os.environ.get('MAIPA_STORAGE_FIXTURE') != '1' or not raw_root:
    raise SystemExit('Storage fixture requires explicit owned synthetic storage')
owned = physical_chain(Path(raw_root)).resolve()
temporary = Path(tempfile.gettempdir()).resolve()
if (owned.name not in {'install', 'restored'} or not owned.parent.is_dir() or
        owned.parent.parent != temporary or not owned.parent.name.startswith(
            ('maipa-recovery-template-', 'maipa-recovery-test-'))):
    raise SystemExit('Storage fixture requires explicit owned synthetic storage')
os.environ['DJANGO_SETTINGS_MODULE'] = 'backend.maipa.settings'
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from backend.maipa.workspace.models import Installation, Membership, WorkspaceLayout
from backend.maipa.storage import prepare_directory, prepare_files, development_key


def main():
    operation = sys.argv[1]
    root = Path(settings.DATA_ROOT)
    if operation == 'seed':
        prepare_directory(root)
        development_key(root, lambda _: 'synthetic-signing-key-' * 4)
        prepare_files(root)
        call_command('migrate', interactive=False, verbosity=0)
        install = Installation.objects.create()
        users = [get_user_model().objects.create_user(username=name,
            password='synthetic-recovery-password!932') for name in ('recovery-owner', 'recovery-other')]
        for user in users:
            Membership.objects.create(installation=install, user=user, role='preparer')
        owner = users[0]
        layout = WorkspaceLayout.objects.create(installation=install, user=owner,
            revision=4, split_basis_points=3400)
        client = Client()
        client.force_login(owner)
        preferences = client.get('/api/workspace/preferences/', secure=True).json()
        saved_preferences = client.post('/api/workspace/preferences/', data=json.dumps({
            'expected_revision': preferences['revision'], 'theme': 'dark', 'active_view': 'workspace-guide'}),
            content_type='application/json', secure=True, HTTP_X_MAIPA_PREFERENCES=preferences['preferences_id'])
        assert saved_preferences.status_code == 200
        setup = client.get('/api/onboarding/', secure=True).json()
        response = client.post('/api/onboarding/', data=json.dumps({
            'expected_revision': setup['revision'], 'action': 'reply', 'answer': 'Synthetic saved profile'}),
            content_type='application/json', secure=True, HTTP_X_MAIPA_SETUP=setup['setup_id'])
        assert response.status_code == 200
        for relative, data in {
            'providers/11111111-1111-4111-8111-111111111111/synthetic.token': b'synthetic-provider-secret-only',
            'sessions/22222222-2222-4222-8222-222222222222/state.json': b'{"synthetic_resume":"only-a-fixture"}',
            'artifacts/33333333-3333-4333-8333-333333333333/proof.bin': b'synthetic artifact bytes\x00\x01',
            'workers/ephemeral-do-not-restore': b'ephemeral synthetic worker',
            'local-setup-code': b'c9' * 32,
        }.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return {'installation': str(install.pk), 'user': str(owner.pk),
            'layout': str(layout.identity), 'session': client.cookies['sessionid'].value,
            'setup': setup['setup_id'], 'preferences': preferences['preferences_id']}
    if operation == 'inspect':
        proof_path = physical_chain(Path(sys.argv[2]))
        if proof_path.parent != root.parent or proof_path.name != 'synthetic-proof.json':
            raise SystemExit('Storage fixture proof must be owned synthetic data')
        proof = strict_json(proof_path, 4096)
        client = Client()
        client.cookies['sessionid'] = proof['session']
        layout = client.get('/api/workspace/layout/', secure=True)
        setup = client.get('/api/onboarding/', secure=True)
        preferences = client.get('/api/workspace/preferences/', secure=True)
        owner = get_user_model().objects.get(pk=proof['user'])
        assert owner.check_password('synthetic-recovery-password!932')
        other = Client()
        other.force_login(get_user_model().objects.get(username='recovery-other'))
        other_setup = other.get('/api/onboarding/', secure=True)
        other_preferences = other.get('/api/workspace/preferences/', secure=True)
        denied_before = client.get('/api/onboarding/', secure=True).status_code
        Membership.objects.filter(user=owner).update(enabled=False)
        denied_after = client.get('/api/onboarding/', secure=True)
        preferences_revoked = client.get('/api/workspace/preferences/', secure=True)
        return {'installation': str(Installation.objects.get().pk),
            'layout_status': layout.status_code, 'layout': layout.json(),
            'setup_status': setup.status_code, 'setup': setup.json(),
            'preferences_status': preferences.status_code, 'preferences': preferences.json(),
            'other_preferences': other_preferences.json(), 'preferences_revoked': preferences_revoked.status_code,
            'preferences_revoked_cache': preferences_revoked['Cache-Control'],
            'other_draft': other_setup.json()['draft'],
            'revocation': [denied_before, denied_after.status_code],
            'revocation_cache': denied_after['Cache-Control'],
            'users': get_user_model().objects.count()}
    if operation == 'hold':
        from backend.maipa.storage import install_lock
        with install_lock(root):
            print('locked', flush=True)
            sys.stdin.readline()
        return {'released': True}
    raise ValueError('Unknown synthetic fixture operation')


if __name__ == '__main__':
    print(json.dumps(main()), flush=True)
