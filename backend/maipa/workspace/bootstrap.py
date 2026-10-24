"""Local operator pairing and atomic, one-time administrator creation."""
import os
from pathlib import Path
import re
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.password_validation import (CommonPasswordValidator, MinimumLengthValidator,
    NumericPasswordValidator, UserAttributeSimilarityValidator, validate_password)
from django.db import transaction

from backend.maipa.intake.schema import fields, require
from .models import BootstrapState, Installation, Membership


class SetupClosed(Exception):
    pass


class SetupUnavailable(Exception):
    pass


class InvalidCode(Exception):
    pass


def required():
    return (BootstrapState.objects.filter(slot=1, closed=False).exists()
            and not Installation.objects.exists() and not get_user_model().objects.exists())


def prepare_local_setup():
    """Called by the local launcher after migrate. Never print or serve the code."""
    if not required():
        return
    path = Path(settings.LOCAL_SETUP_CODE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        from .local_code import protect
        protect(path, output.fileno())
        output.write(secrets.token_hex(32))


def _operator_code():
    path = Path(settings.LOCAL_SETUP_CODE_FILE)
    try:
        if path.is_symlink() or path.stat().st_size != 64:
            raise SetupUnavailable()
        value = path.read_text(encoding='utf-8')
        if not re.fullmatch('[a-f0-9]{64}', value):
            raise SetupUnavailable()
        return value
    except (OSError, UnicodeError):
        raise SetupUnavailable() from None


@transaction.atomic
def claim(value):
    if not required():
        raise SetupClosed()
    fields(value, ('setup_code', 'username', 'password', 'password_confirmation'))
    code = value['setup_code']
    require(isinstance(code, str) and bool(re.fullmatch('[a-f0-9]{64}', code)))
    if not secrets.compare_digest(code, _operator_code()):
        raise InvalidCode()
    username, password = value['username'], value['password']
    require(isinstance(username, str) and 1 <= len(username) <= 150 and username == username.strip())
    require(isinstance(password, str) and 12 <= len(password) <= 1024 and '\0' not in password)
    require(password == value['password_confirmation'])
    username = AbstractBaseUser.normalize_username(username)
    user = get_user_model()(username=username)
    user.full_clean(exclude=['password'])
    validate_password(password, user, [MinimumLengthValidator(12), CommonPasswordValidator(),
        NumericPasswordValidator(), UserAttributeSimilarityValidator()])
    # The first write is a compare-and-swap on a migration-created singleton.
    # On SQLite concurrent snapshots either serialize or fail closed and retry.
    if not BootstrapState.objects.filter(slot=1, closed=False).update(closed=True):
        raise SetupClosed()
    if Installation.objects.exists() or get_user_model().objects.exists():
        raise SetupClosed()
    installation = Installation.objects.create()
    user.set_password(password)
    user.save()
    Membership.objects.create(user=user, installation=installation, role='administrator')
    return user
