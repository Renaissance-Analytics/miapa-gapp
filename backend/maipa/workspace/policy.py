"""Initial role oracle. Private provider resources still require owner equality."""
from .models import Membership

PERMISSIONS = {
    "administrator": ("workspace.read", "agent.accounts.read", "agent.transport.check"),
    "preparer": ("workspace.read", "agent.accounts.read", "agent.transport.check"),
    "reviewer": ("workspace.read", "agent.accounts.read"),
    "viewer": ("workspace.read", "agent.accounts.read"),
}


def membership(user):
    if not user.is_authenticated or not user.is_active:
        return None
    return Membership.objects.select_related("installation").filter(user=user, enabled=True, installation__slot=1).first()


def authorize(request, permission):
    # Query current membership on every request. Browser hints and Django
    # staff/superuser flags do not grant installation membership or private data.
    from backend.maipa.api import error
    if not request.user.is_authenticated or not request.user.is_active:
        return None, error("authentication_required", 401)
    member = membership(request.user)
    if member is None:
        return None, error("workspace_access_denied", 403)
    if permission not in PERMISSIONS.get(member.role, ()):
        return None, error("permission_denied", 403)
    return member, None
