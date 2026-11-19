"""Excel statements, read with the published Fancy spreadsheet reader and reduced to the same CSV the importer checks."""
import csv
import io
import re
from decimal import Decimal, InvalidOperation

import holy_sheet

from .services import IMPORT_HEADERS, MAX_IMPORT_ROWS, LedgerError, require

MAX_WORKBOOK_BYTES = 524_288
# A statement is one small table: a header row and at most the importer's row limit.
READ_LIMITS = {'maxArchiveBytes': MAX_WORKBOOK_BYTES, 'maxPartBytes': 8 * 1_048_576, 'maxExpandedBytes': 16 * 1_048_576,
               'maxRows': MAX_IMPORT_ROWS + 1, 'maxColumns': 8, 'maxCells': (MAX_IMPORT_ROWS + 1) * 8, 'maxSharedStrings': 20_000}
AMOUNT = re.compile(r'-?(?:0|[1-9][0-9]{0,13})(?:\.[0-9]{1,4})?')
DAY = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')


def _blank(cell):
    return cell is None or (type(cell) is str and cell.strip() == '')


def _text(cell):
    """Text exactly as typed, or a whole number written as digits (a cheque number, say)."""
    if type(cell) is int:
        return str(cell)
    require(type(cell) is str)
    return cell.strip()


def _amount(cell):
    """A typed number or its text, kept only when it is exact to four decimals."""
    if type(cell) in (int, float):
        try:
            cell = format(Decimal(repr(cell)), 'f')
        except InvalidOperation:
            raise LedgerError() from None
    require(type(cell) is str and AMOUNT.fullmatch(cell.strip()) is not None)
    return cell.strip()


def statement_csv(data):
    """The workbook's single sheet as date,description,amount[,reference] CSV text. Nothing is guessed or rounded."""
    require(type(data) is bytes and 0 < len(data) <= MAX_WORKBOOK_BYTES, 'import_workbook_invalid')
    try:
        sheets = holy_sheet.read(data, limits=READ_LIMITS)['sheets']
        require(len(sheets) == 1, 'import_workbook_sheets')
        table = holy_sheet.rows(sheets[0])
    except LedgerError:
        raise
    except Exception:  # The reader names many refusal types; every one means this file cannot be used.
        raise LedgerError('import_workbook_invalid') from None
    require(len(table) >= 1 and all(type(cell) is str for cell in table[0]), 'import_header_invalid')
    header = [cell.strip().lower() for cell in table[0]]
    require(header in IMPORT_HEADERS, 'import_header_invalid')
    width = len(header)
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(header)
    count = 0
    for number, cells in enumerate((row for row in table[1:] if not all(_blank(cell) for cell in row)), start=1):
        try:
            require(len(cells) <= width)
            cells = list(cells) + [None] * (width - len(cells))
            require(type(cells[0]) is str and DAY.fullmatch(cells[0].strip()) is not None)
            description = _text(cells[1])
            require(description != '')
            reference = '' if width == 3 or _blank(cells[3]) else _text(cells[3])
            writer.writerow([cells[0].strip(), description, _amount(cells[2]), *([reference] if width == 4 else [])])
        except LedgerError:
            raise LedgerError(f'import_row_{number}_invalid') from None
        count = number
    require(0 < count <= MAX_IMPORT_ROWS, 'import_row_count_invalid')
    return out.getvalue()
