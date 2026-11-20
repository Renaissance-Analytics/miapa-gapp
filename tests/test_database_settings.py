"""The database is SQLite unless this install explicitly opts in to PostgreSQL with complete settings."""
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from backend.maipa.database import database_config

ROOT = Path('synthetic-data-root')
# What a hosting environment hands to every process, whether or not the install uses it. Synthetic values.
AMBIENT = {'PGHOST': '127.0.0.1', 'PGPORT': '5999', 'PGUSER': 'synthetic_role', 'PGPASSWORD': 'synthetic-not-a-secret',
           'PGDATABASE': 'shared_default', 'DATABASE_URL': 'postgresql://synthetic_role:synthetic-not-a-secret@127.0.0.1:5999/shared_default',
           'DB_CONNECTION': 'pgsql', 'DB_DATABASE': 'shared_default'}
SQLITE = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ROOT / 'maipa.sqlite3', 'OPTIONS': {'transaction_mode': 'IMMEDIATE'}}}


class DatabaseSettingsTests(SimpleTestCase):
    def test_sqlite_stays_the_database_even_when_postgresql_variables_are_present(self):
        for environ in ({}, AMBIENT, {**AMBIENT, 'MAIPA_DATABASE': ''}, {**AMBIENT, 'MAIPA_DATABASE': 'sqlite'},
                        {**AMBIENT, 'MAIPA_DATABASE_NAME': 'maipa_live'}):
            self.assertEqual(database_config(environ, ROOT), SQLITE, sorted(environ))

    def test_an_explicit_opt_in_with_its_own_database_name_selects_postgresql(self):
        chosen = database_config({**AMBIENT, 'MAIPA_DATABASE': 'postgres', 'MAIPA_DATABASE_NAME': 'maipa_accept'}, ROOT)
        self.assertEqual(chosen, {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'maipa_accept', 'HOST': '127.0.0.1', 'PORT': '5999',
                                              'USER': 'synthetic_role', 'PASSWORD': 'synthetic-not-a-secret', 'CONN_MAX_AGE': 0}})
        # The install's own name is used, never the ambient default database or URL.
        self.assertNotIn('shared_default', repr(chosen))

    def test_a_half_configured_or_unknown_choice_is_refused_instead_of_falling_back(self):
        full = {**AMBIENT, 'MAIPA_DATABASE': 'postgres', 'MAIPA_DATABASE_NAME': 'maipa_accept'}
        cases = [{k: v for k, v in full.items() if k != missing} for missing in ('MAIPA_DATABASE_NAME', 'PGHOST', 'PGPORT', 'PGUSER', 'PGPASSWORD')]
        cases += [{**full, key: value} for key, value in (('MAIPA_DATABASE', 'postgresql'), ('MAIPA_DATABASE', 'Postgres'), ('MAIPA_DATABASE', 'mysql'),
                  ('MAIPA_DATABASE_NAME', ''), ('MAIPA_DATABASE_NAME', 'Maipa'), ('MAIPA_DATABASE_NAME', 'maipa-accept'), ('MAIPA_DATABASE_NAME', 'pg_catalog'),
                  ('MAIPA_DATABASE_NAME', 'postgres'), ('MAIPA_DATABASE_NAME', 'a' * 64), ('MAIPA_DATABASE_NAME', 'x; drop'),
                  ('PGPORT', 'port'), ('PGPORT', '0'), ('PGPORT', '70000'), ('PGHOST', ''), ('PGUSER', ''), ('PGPASSWORD', ''))]
        for environ in cases:
            with self.assertRaises(ImproperlyConfigured, msg=sorted(environ.items())) as refused:
                database_config(environ, ROOT)
            # The refusal names the setting, never a value.
            self.assertNotIn('synthetic-not-a-secret', str(refused.exception))
