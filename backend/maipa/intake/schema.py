"""Bounded recorded client assertions, never inferred tax/legal determinations."""
from datetime import date
from decimal import Decimal
import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

KEYS = ('legal_form', 'tax_classification', 'tax_elections', 'ownership', 'periods', 'currency',
        'reporting_basis', 'jurisdictions', 'industry', 'systems', 'records_quality',
        'records_complete', 'authorized_contacts', 'engagement_scope')
CHOICES = {
    'legal_form': ('sole_proprietorship', 'partnership', 'llc', 'corporation', 'nonprofit', 'trust', 'other'),
    'tax_classification': ('disregarded_entity', 'partnership', 'c_corporation', 's_corporation', 'tax_exempt', 'trust', 'other'),
    'reporting_basis': ('cash', 'accrual', 'other'),
    'records_quality': ('complete', 'incomplete', 'unreconciled', 'needs_review'),
}


class InvalidFact(ValueError):
    pass


def require(condition):
    if not condition:
        raise InvalidFact('Invalid client assertion')


def text(value, maximum=128):
    require(isinstance(value, str) and 0 < len(value) <= maximum and value == value.strip()
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))
    return value


def day(value):
    require(isinstance(value, str) and bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)))
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise InvalidFact('Invalid date') from None
    return result


def fields(value, expected):
    require(isinstance(value, dict) and set(value) == set(expected))


def entries(value, maximum=50):
    require(isinstance(value, list) and len(value) <= maximum)
    return value


def validate_value(key, value):
    if key in CHOICES:
        require(isinstance(value, str) and value in CHOICES[key])
    elif key == 'records_complete':
        require(type(value) is bool)
    elif key == 'currency':
        require(isinstance(value, str) and bool(re.fullmatch('[A-Z]{3}', value)))
    elif key in {'industry', 'engagement_scope'}:
        text(value, 1024 if key == 'engagement_scope' else 128)
    elif key in {'jurisdictions', 'systems'}:
        entries(value)
        for item in value:
            text(item, 32 if key == 'jurisdictions' else 128)
        require(len(set(value)) == len(value))
    elif key == 'ownership':
        total = Decimal(0)
        for item in entries(value):
            fields(item, ('name', 'percentage')); text(item['name'])
            percentage = item['percentage']
            require(isinstance(percentage, str) and bool(re.fullmatch(r'(?:0|[1-9]\d{0,2})(?:\.\d{1,4})?', percentage)))
            amount = Decimal(percentage)
            require(amount <= 100)
            total += amount
        require(total <= 100)
    elif key == 'periods':
        for item in entries(value):
            fields(item, ('name', 'start', 'end')); text(item['name'])
            require(day(item['start']) <= day(item['end']))
    elif key == 'tax_elections':
        for item in entries(value):
            fields(item, ('name', 'confirmed')); text(item['name'])
            require(type(item['confirmed']) is bool)
    elif key == 'authorized_contacts':
        for item in entries(value):
            fields(item, ('name', 'role', 'email')); text(item['name']); text(item['role']); text(item['email'], 254)
            try:
                validate_email(item['email'])
            except ValidationError:
                raise InvalidFact('Invalid contact') from None
    else:
        raise InvalidFact('Unknown fact')


def assertion(body):
    fields(body, ('key', 'knowledge', 'value', 'expected_revision', 'effective_from', 'effective_to', 'evidence'))
    require(isinstance(body['key'], str) and body['key'] in KEYS)
    require(type(body['expected_revision']) is int and 0 <= body['expected_revision'] < 2_000_000_000)
    require(body['knowledge'] in ('known', 'unknown'))
    if body['knowledge'] == 'unknown':
        require(body['value'] is None)
    else:
        require(body['value'] is not None)
        validate_value(body['key'], body['value'])
    start = day(body['effective_from'])
    end = None if body['effective_to'] is None else day(body['effective_to'])
    require(end is None or end > start)
    evidence = body['evidence']
    fields(evidence, ('reference', 'locator', 'observed_on'))
    text(evidence['reference'], 256); text(evidence['locator'], 512); day(evidence['observed_on'])
    return body
