"""Which database this install uses. SQLite unless the install itself opts in to PostgreSQL.

A hosting environment may hand PostgreSQL variables to every process. Their presence alone never
selects PostgreSQL: that would point an existing install at an empty database. The install's own
start command must set MAIPA_DATABASE=postgres and MAIPA_DATABASE_NAME, and a half-configured
choice is refused rather than falling back. Connection values are read here and nowhere else;
they are never written to a file or included in an error.
"""
import re

from django.core.exceptions import ImproperlyConfigured

NAME = re.compile(r'[a-z_][a-z0-9_]{0,62}')
RESERVED = ('postgres', 'template0', 'template1')


def database_config(environ, data_root):
    choice = environ.get('MAIPA_DATABASE', '')
    # Reserve short atomic transactions before reads so concurrent app writers queue.
    sqlite = None if data_root is None else {'ENGINE': 'django.db.backends.sqlite3', 'NAME': data_root / 'maipa.sqlite3',
                                             'OPTIONS': {'transaction_mode': 'IMMEDIATE'}}
    if choice in ('', 'sqlite'):
        return {'default': sqlite}
    if choice != 'postgres':
        raise ImproperlyConfigured('MAIPA_DATABASE must be sqlite or postgres')
    name = environ.get('MAIPA_DATABASE_NAME', '')
    if not NAME.fullmatch(name) or name in RESERVED or name.startswith('pg_'):
        raise ImproperlyConfigured('MAIPA_DATABASE_NAME must name this install\'s own PostgreSQL database')
    values = {}
    for key in ('PGHOST', 'PGPORT', 'PGUSER', 'PGPASSWORD'):
        values[key] = environ.get(key, '')
        if not values[key]:
            raise ImproperlyConfigured(f'{key} is required when MAIPA_DATABASE is postgres')
    if not values['PGPORT'].isascii() or not values['PGPORT'].isdigit() or not 0 < int(values['PGPORT']) < 65536:
        raise ImproperlyConfigured('PGPORT must be a port number')
    chosen = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': name, 'HOST': values['PGHOST'], 'PORT': values['PGPORT'],
                          'USER': values['PGUSER'], 'PASSWORD': values['PGPASSWORD'], 'CONN_MAX_AGE': 0}}
    if sqlite is not None:
        # The install's earlier SQLite file stays reachable under its own name, for the one-time copy and as the fallback.
        # The application itself only ever uses 'default'.
        chosen['sqlite_file'] = sqlite
    return chosen
