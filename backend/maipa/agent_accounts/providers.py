"""Trusted app registrations, distinct from provider discovery or readiness.

Extra labels reserve an app identity; they never configure a process or make an
unimplemented ACP adapter usable. Generic runtime support awaits Prism's public
client contract. Provider-specific native policies remain in their adapters.
"""
import re
from django.conf import settings

PROVIDER_ID = r'^[a-z][a-z0-9-]{0,63}\Z'


def registrations():
    extra = getattr(settings, 'ACP_PROVIDER_LABELS', {})
    if type(extra) is not dict or len(extra) > 30:
        raise ValueError('Provider registry unavailable')
    labels = {'codex': 'Codex', 'claude': 'Claude', **extra}
    for key, label in labels.items():
        if (type(key) is not str or not re.fullmatch(PROVIDER_ID, key) or
                type(label) is not str or not label.strip() or len(label) > 80 or not label.isprintable()):
            raise ValueError('Provider registry unavailable')
    # These application adapters have separate pinned runtimes and policies.
    # No connection/model/auth capability is inferred from a label registration.
    return [{'id': key, 'label': label, 'available': key in {'codex', 'claude'}} for key, label in labels.items()]


def descriptor(provider):
    return next((item for item in registrations() if item['id'] == provider), None)
