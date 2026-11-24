"""Isolated tests against PostgreSQL only: never use this module to serve an application.

Run where the hosting environment supplies PGHOST, PGPORT, PGUSER and PGPASSWORD. Django creates and
drops its own `test_maipa_scratch` database; no installed database is touched.
"""
import os

from .database import database_config
from .test_settings import *  # noqa: F403

DATABASES = database_config({**{key: os.environ.get(key, '') for key in ('PGHOST', 'PGPORT', 'PGUSER', 'PGPASSWORD')},
                             'MAIPA_DATABASE': 'postgres', 'MAIPA_DATABASE_NAME': 'maipa_scratch'}, None)
DATABASES['default']['TEST'] = {'NAME': 'test_maipa_scratch'}
# A second empty database, used only by the database copy tests.
DATABASES['copy_target'] = {**DATABASES['default'], 'NAME': 'maipa_scratch_copy', 'TEST': {'NAME': 'test_maipa_scratch_copy'}}
