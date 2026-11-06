"""Content-free consumer DTOs; never persist raw provider errors or reasons."""
PROVIDER_KINDS = ('rate_limit', 'overloaded', 'auth', 'billing', 'other')
MAX_RESET_MS = 253_402_300_799_999  # Last millisecond representable by a UTC year9999 date.


def valid_reset(value):
    return type(value) is int and 0 < value <= MAX_RESET_MS


def reported_failure(error):
    """Only the documented direct metadata on a correlated prompt refusal."""
    if type(error) is not dict or type(error.get('code')) is not int or error['code'] != -32603:
        return None
    data = error.get('data')
    value = data.get('particle.academy/turn_failure') if type(data) is dict else None
    if type(value) is not dict or value.get('kind') not in PROVIDER_KINDS:
        return None
    result = {'kind': value['kind']}
    if value['kind'] == 'rate_limit' and valid_reset(value.get('resetsAtMs')):
        result['resets_at_ms'] = value['resetsAtMs']
    return result


def validate_failure(value):
    if value is None:
        return None
    if (type(value) is not dict or not {'kind'} <= set(value) <= {'kind', 'resets_at_ms'}
            or value['kind'] not in (*PROVIDER_KINDS, 'unclassified')):
        raise ValueError('Invalid turn status')
    if 'resets_at_ms' in value and (value['kind'] != 'rate_limit' or not valid_reset(value['resets_at_ms'])):
        raise ValueError('Invalid reset time')
    return dict(value)


def read_failure(value):
    try:
        return validate_failure(value)
    except (ValueError, TypeError):
        return None  # Corrupt optional status cannot expose content or break the conversation.
