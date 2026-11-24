"""Copy every application record from one configured database to another, and prove the copy.

Used to move an install between database engines. The source is only read. The target must be freshly
migrated and otherwise empty; the whole copy and its row-by-row verification happen in one target
transaction, so a failed or unequal copy leaves the target as it was. Nothing here reads or prints
connection settings.
"""
import hashlib
import json
import os
import uuid
from pathlib import Path

from django.apps import apps
from django.core import serializers
from django.core.management import call_command
from django.core.management.color import no_style
from django.db import connections, transaction

# Created by migrations in every database, with ids that may differ; never copied.
SKIPPED = frozenset(('contenttypes.ContentType', 'auth.Permission'))
# Rows that point at those ids cannot be carried over faithfully. Maipa does not use them.
UNSUPPORTED = (('auth', 'Group', 'permissions'), ('auth', 'User', 'user_permissions'))
# Rows a migration creates in an empty database; the copy replaces them by primary key.
SEEDED = frozenset(('workspace.BootstrapState',))


class CopyRefused(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _models():
    ordered = serializers.sort_dependencies([(config, None) for config in apps.get_app_configs()], allow_cycles=True)
    return [model for model in ordered if model._meta.label not in SKIPPED and not model._meta.proxy and model._meta.managed]


def _alias(name):
    if type(name) is not str or name not in connections.databases:
        raise CopyRefused('unknown_database')
    return name


def _rows(model, alias):
    return serializers.serialize('python', model._base_manager.using(alias).order_by('pk').iterator(chunk_size=500))


def _canonical(row):
    fields = {key: sorted(value, key=str) if isinstance(value, list) and key in row.get('_many', ()) else value for key, value in row['fields'].items()}
    return json.dumps({'pk': row['pk'], 'fields': fields}, sort_keys=True, default=str, ensure_ascii=True, separators=(',', ':'))


def digest(alias):
    """Per model: how many rows, and one hash over every row's exact field values in primary-key order."""
    alias = _alias(alias)
    result = {}
    for model in _models():
        many = {field.name for field in model._meta.many_to_many}
        count, sha = 0, hashlib.sha256()
        for row in _rows(model, alias):
            row['_many'] = many
            sha.update(_canonical(row).encode()); sha.update(b'\n')
            count += 1
        result[model._meta.label] = {'rows': count, 'sha256': sha.hexdigest()}
    return result


def compare(source, target):
    """Every model whose row count or content differs between the two databases. Empty means identical."""
    left, right = digest(source), digest(target)
    return {label: {'source_rows': left[label]['rows'], 'target_rows': right[label]['rows'],
                    'same_content': left[label]['sha256'] == right[label]['sha256']}
            for label in left if left[label] != right[label]}


def copy(source, target):
    """Copy all records from source into the empty target, verify them, and return the per-model row counts."""
    source, target = _alias(source), _alias(target)
    if source == target or connections.databases[source] == connections.databases[target]:
        raise CopyRefused('same_database')
    for app, name, field in UNSUPPORTED:
        through = getattr(apps.get_model(app, name), field).through
        if through._base_manager.using(source).exists():
            raise CopyRefused('unsupported_permission_rows')
    models = _models()
    with transaction.atomic(using=target):
        for model in models:
            if model._meta.label not in SEEDED and model._base_manager.using(target).exists():
                raise CopyRefused('target_not_empty')
        for model in models:
            if model._meta.label in SEEDED:
                model._base_manager.using(target).all()._raw_delete(target)
            for item in serializers.deserialize('python', _rows(model, source), using=target):
                item.save(using=target)       # A raw save: stored values exactly, no model save() logic.
        connection = connections[target]
        statements = connection.ops.sequence_reset_sql(no_style(), models)
        if statements:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
        differences = compare(source, target)
        if differences:
            raise CopyRefused('copy_not_identical')     # Rolls the target back.
    return {label: value['rows'] for label, value in digest(target).items()}


def export_to_sqlite(source, path):
    """Write every record of `source` into a new SQLite database file at `path`, verified identical.

    The file is built under a temporary name beside it and moved into place only after the copy has been
    verified, so `path` is either the previous file or a complete, current one. This is how an install that
    keeps its records in another database produces the file its backup is made from.
    """
    source, path = _alias(source), Path(path)
    if not path.is_absolute() or not path.parent.is_dir() or path.suffix != '.sqlite3':
        raise CopyRefused('invalid_export_path')
    alias = 'export_file'
    if alias in connections.settings:
        raise CopyRefused('export_in_progress')
    temporary = path.with_name(f'{path.name}.export-{uuid.uuid4().hex}')
    defaults = connections.configure_settings({'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(temporary)}})['default']
    connections.settings[alias] = defaults
    try:
        call_command('migrate', database=alias, interactive=False, verbosity=0)
        counts = copy(source, alias)
        connections[alias].close()
        os.replace(temporary, path)
        return counts
    finally:
        try:
            connections[alias].close()
        finally:
            del connections.settings[alias]
            if hasattr(connections._connections, alias):
                delattr(connections._connections, alias)
            for leftover in (temporary, Path(str(temporary) + '-journal'), Path(str(temporary) + '-wal'), Path(str(temporary) + '-shm')):
                if leftover.exists():
                    leftover.unlink()
