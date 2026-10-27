"""Same-origin actor-bound chat and bounded SSE snapshots."""
import json
import time

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.http import JsonResponse, StreamingHttpResponse

from backend.maipa.acp import _object, _invalid_number
from backend.maipa.api import error, method_not_allowed
from backend.maipa.workspace.policy import authorize
from . import chat


def _guard(request):
    _, denied = authorize(request, 'agent.transport.check')
    if denied is not None:
        return denied
    if request.headers.get('X-Maipa-Agent-Actor') != str(request.user.pk):
        return error('actor_changed', 409)
    if request.GET:
        return error('invalid_request', 400)


def conversation(request):
    denied = _guard(request)
    if denied is not None:
        return denied
    if request.method not in ('GET', 'POST'):
        return method_not_allowed('GET, POST')
    try:
        if request.method == 'GET':
            return JsonResponse(chat.snapshot(request.user))
        if request.content_type != 'application/json':
            raise ValueError('Invalid request')
        raw = request.read(32769)
        if len(raw) > 32768:
            return error('request_too_large', 413)
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=_invalid_number)
        if type(value) is not dict:
            raise ValueError('Invalid request')
        action = value.get('action')
        if 'selection_revision' not in value:
            raise ValueError('Invalid request')
        chat._revision(value['selection_revision'])
        if action == 'select_provider' and set(value) == {'action', 'selection_revision', 'provider'}:
            state = chat.select_provider(request.user, value['selection_revision'], value['provider'])
        elif action == 'send' and set(value) == {'action', 'revision', 'request_id', 'text', 'selection_revision'}:
            state = chat.send(request.user, value['revision'], value['request_id'], value['text'], session_key=request.session.session_key,
                              selection_revision=value['selection_revision'])
        elif action == 'connect' and set(value) == {'action', 'revision', 'selection_revision'}:
            state = chat.connect(request.user, value['revision'], session_key=request.session.session_key,
                                 selection_revision=value['selection_revision'])
        elif action in ('login', 'cancel', 'disconnect') and set(value) == {'action', 'selection_revision'}:
            state = chat.control(request.user, action, session_key=request.session.session_key,
                                 selection_revision=value['selection_revision'])
        elif action == 'authorize' and set(value) == {'action', 'selection_revision', 'code'}:
            state = chat.control(request.user, action, session_key=request.session.session_key,
                                 selection_revision=value['selection_revision'], authorization_code=value['code'])
        else:
            raise ValueError('Invalid request')
        return JsonResponse(state)
    except chat.ChatConflict:
        return error('conversation_changed', 409)
    except chat.ChatUnavailable:
        return error('conversation_unavailable', 503)
    except (ValueError, UnicodeError, RecursionError):
        return error('invalid_request', 400)


def stream(request):
    denied = _guard(request)
    if denied is not None:
        return denied
    if request.method != 'GET':
        return method_not_allowed('GET')
    owner, key = request.user.pk, request.session.session_key
    auth_hash = request.user.get_session_auth_hash()

    def events():
        previous, started = None, time.monotonic()
        while time.monotonic() - started < 600:
            session = SessionStore(session_key=key).load()
            if session.get('_auth_user_id') != str(owner) or session.get('_auth_user_hash') != auth_hash:
                yield b'event: revoked\ndata: {}\n\n'
                return
            try:
                actor = get_user_model().objects.get(pk=owner, is_active=True)
                if actor.get_session_auth_hash() != auth_hash:
                    yield b'event: revoked\ndata: {}\n\n'
                    return
                state = chat.snapshot(actor)
                from .chat_runtime import transient
                state.update(transient(owner, key))
            except (chat.ChatUnavailable, get_user_model().DoesNotExist):
                yield b'event: revoked\ndata: {}\n\n'
                return
            wire = json.dumps(state, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
            if wire != previous:
                yield ('event: state\ndata: ' + wire + '\n\n').encode()
                previous = wire
            else:
                yield b': keepalive\n\n'
            time.sleep(0.25)
    response = StreamingHttpResponse(events(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-store'
    response['X-Accel-Buffering'] = 'no'
    return response
