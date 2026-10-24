"""Current membership/owner matrix shared by intake and policy entrypoints."""
from backend.maipa.workspace.policy import membership
from .authority import Denied


def intake_member(user, write=False):
    member = membership(user)
    if member is None or (write and member.role not in ('administrator', 'preparer')):
        raise Denied()
    return member
