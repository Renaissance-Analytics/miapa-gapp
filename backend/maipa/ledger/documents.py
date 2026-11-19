"""PDF statements, read with the published Fancy PDF reader's guarded read. The text is evidence to check against, never instructions."""
import re

import fine_print

from .services import LedgerError, require

MAX_DOCUMENT_BYTES = 716_800
MAX_TEXT = 200_000
# One statement: a few pages, read quickly, with hidden text and embedded instructions redacted.
READ_LIMITS = {'maxFileBytes': MAX_DOCUMENT_BYTES, 'maxPages': 30, 'timeSeconds': 10, 'memoryBytes': 64 * 1_048_576,
               'maxStreamBytes': 8 * 1_048_576, 'maxTotalDecodedBytes': 32 * 1_048_576}
NUMBER = re.compile(r'(?<![0-9.,])[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,4})?(?![0-9])|(?<![0-9.,])[0-9]+(?:\.[0-9]{1,4})?(?![0-9])')


def pdf_text(data):
    """Every page's text in reading order. Refused when the file cannot be read or holds no text (a scanned image)."""
    require(type(data) is bytes and 0 < len(data) <= MAX_DOCUMENT_BYTES, 'import_document_invalid')
    try:
        result = fine_print.read(data, policy='redact', limits=READ_LIMITS)
    except Exception:  # Bad input is refused by the reader; anything else also means this file cannot be used.
        raise LedgerError('import_document_invalid') from None
    require(type(result) is dict and result.get('ok') is True and type(result.get('pages')) is list, 'import_document_invalid')
    parts = []
    for page in result['pages']:
        require(type(page) is dict and type(page.get('text')) is str and type(page.get('page')) is int, 'import_document_invalid')
        parts.append(f"--- page {page['page']} ---\n{page['text'].strip()}")
    text = '\n\n'.join(parts)
    require(any(page['text'].strip() for page in result['pages']), 'import_document_no_text')
    require(len(text) <= MAX_TEXT and not any(ord(c) < 32 and c not in '\n\r\t' for c in text), 'import_document_invalid')
    return text


def amounts_in(text):
    """Every number written in the text, as exact units, ignoring sign, currency marks and thousands separators."""
    found = set()
    for token in NUMBER.findall(text):
        whole, _, fraction = token.replace(',', '').partition('.')
        if len(whole) <= 14:
            found.add(int(whole) * 10000 + int((fraction + '0000')[:4]))
    return found


def require_written(text, units):
    """Each derived figure must literally appear in the document; a figure the document never states is refused."""
    written = amounts_in(text)
    require(all(abs(value) in written for value in units), 'import_amount_not_in_document')
