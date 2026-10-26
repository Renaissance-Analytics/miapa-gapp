"""Atomic, one-time administrator creation for a local installation."""
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.password_validation import (CommonPasswordValidator, MinimumLengthValidator,
    NumericPasswordValidator, UserAttributeSimilarityValidator, validate_password)
from django.db import transaction

from backend.maipa.intake.schema import fields, require
from .models import BootstrapState, Installation, Membership


class SetupClosed(Exception):
    pass


def required():
    return (BootstrapState.objects.filter(slot=1, closed=False).exists()
            and not Installation.objects.exists() and not get_user_model().objects.exists())


@transaction.atomic
def claim(value):
    if not required():
        raise SetupClosed()
    fields(value, ('username', 'password', 'password_confirmation'))
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
