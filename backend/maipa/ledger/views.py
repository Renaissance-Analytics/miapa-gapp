"""Human session controls; no model-supplied identity or approval provenance."""
from functools import wraps

from django.db import DatabaseError, transaction
from django.http import JsonResponse

from backend.maipa.api import error, method_not_allowed
from backend.maipa.intake.schema import InvalidFact
from backend.maipa.intake.views import body
from backend.maipa.workspace.policy import authorize
from . import services


def endpoint(function):
    @wraps(function)
    def call(request, *args, **kwargs):
        _, denied = authorize(request, 'workspace.read')
        if denied is not None:
            return denied
        if request.headers.get('X-Maipa-Agent-Actor') != str(request.user.pk):
            return error('ledger_actor_changed', 409)
        try:
            return function(request, *args, **kwargs)
        except services.LedgerError as exc:
            code = exc.code
            return error(code, 404 if code == 'ledger_not_found' else 409 if
                         code.endswith('conflict') or code.endswith('exists') or code == 'book_already_initialized' else 400)
        except InvalidFact:
            return error('invalid_ledger_request', 400)
        except (ValueError, TypeError, AttributeError):
            return error('invalid_ledger_request', 400)
        except DatabaseError:
            return error('ledger_unavailable', 503)
    return call


@endpoint
def books(request):
    if request.method == 'GET':
        return JsonResponse(services.list_books(request.user))
    if request.method != 'POST':
        return method_not_allowed('GET, POST')
    value, denied = body(request)
    if denied is not None:
        return denied
    services.fields(value, ('entity_id', 'currency', 'period_start', 'period_end'))
    entity = services.identity(value['entity_id'])
    return JsonResponse(services.create_book(request.user, entity,
        {key: value[key] for key in ('currency', 'period_start', 'period_end')}), status=201)


@endpoint
@transaction.atomic
def book(request, book_id):
    if request.method != 'GET':
        return method_not_allowed('GET')
    return JsonResponse({'actor_id': str(request.user.pk), 'book': services.read_book(request.user, str(book_id)),
                         'report': services.journal_preview(request.user, str(book_id))})


@endpoint
def accounts(request, book_id):
    if request.method != 'POST':
        return method_not_allowed('POST')
    value, denied = body(request)
    if denied is not None:
        return denied
    return JsonResponse(services.create_account(request.user, str(book_id), value), status=201)


@endpoint
def account_activity(request, book_id, account_id):
    if request.method != 'GET':
        return method_not_allowed('GET')
    return JsonResponse(services.account_activity(request.user, str(book_id), str(account_id)))


@endpoint
def imports(request, book_id):
    if request.method == 'GET':
        return JsonResponse(services.source_rows(request.user, str(book_id)))
    if request.method != 'POST':
        return method_not_allowed('GET, POST')
    value, denied = body(request)
    if denied is not None:
        return denied
    result = services.import_statement(request.user, str(book_id), value)
    return JsonResponse(result, status=201 if result['created'] else 200)


@endpoint
def journals(request, book_id):
    if request.method != 'POST':
        return method_not_allowed('POST')
    value, denied = body(request)
    if denied is not None:
        return denied
    return JsonResponse(services.propose_journal(request.user, str(book_id), value), status=201)


@endpoint
def post_journal(request, book_id, journal_id):
    if request.method != 'POST':
        return method_not_allowed('POST')
    value, denied = body(request)
    if denied is not None:
        return denied
    return JsonResponse(services.confirm_own_journal(request.user, str(book_id), str(journal_id), value))
