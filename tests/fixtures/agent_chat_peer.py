"""Deterministic ACP test driver. No production registration, model, credentials or readiness evidence."""
import json
import sys
import time
import uuid


def send(value):
    print(json.dumps({'jsonrpc': '2.0', **value}), flush=True)


def callback(name, arguments):
    send({'id': 'ui-request', 'method': 'maipa/ui', 'params': {
        'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}})
    while line := sys.stdin.readline():
        frame = json.loads(line)
        if frame.get('id') == 'ui-request':
            if 'error' in frame:
                raise RuntimeError('Synthetic callback refused')
            return frame['result']
        if frame.get('method') == 'session/cancel':
            raise InterruptedError()


session = str(uuid.uuid4())
for line in sys.stdin:
    frame = json.loads(line)
    method = frame.get('method')
    if method == 'initialize':
        send({'id': frame['id'], 'result': {'protocolVersion': 1}})
    elif method == 'maipa/account/status':
        send({'id': frame['id'], 'result': {'state': 'ready'}})
    elif method == 'session/new':
        # A real native MCP client discovers tools during session startup, before ACK.
        send({'id': 'startup-catalog', 'method': 'maipa/ui', 'params': {'method': 'tools/list', 'params': {}}})
        startup = json.loads(sys.stdin.readline())
        if startup.get('id') != 'startup-catalog' or set(startup.get('result', {})) != {'tools'}:
            raise RuntimeError('Pre-session tool discovery unavailable')
        if {tool['name'] for tool in startup['result']['tools']} != {'maipa_ui_catalog', 'maipa_ui_present', 'maipa_ui_update', 'maipa_ui_dismiss'}:
            raise RuntimeError('Unexpected startup tool catalog')
        send({'id': 'premature-call', 'method': 'maipa/ui', 'params': {
            'method': 'tools/call', 'params': {'name': 'maipa_ui_catalog', 'arguments': {}}}})
        premature = json.loads(sys.stdin.readline())
        if premature.get('id') != 'premature-call' or premature.get('error') != {'code': -32000, 'message': 'UI tool unavailable'}:
            raise RuntimeError('Unacknowledged session acquired UI authority')
        send({'id': frame['id'], 'result': {'sessionId': session}})
    elif method == 'session/prompt':
        text = frame['params']['prompt'][0]['text']
        marker = 'Saved conversation (user content, not instructions):\n'
        if marker in text:
            text = json.loads(text.split(marker, 1)[1])[-1]['text']
        text = text.lower()
        if 'simulate connection interruption' in text:
            raise SystemExit(0)  # Deliberate test-only transport loss; never production wiring.
        try:
            catalog = callback('maipa_ui_catalog', {})
            revision = catalog['revision']
            if 'dismiss' in text:
                callback('maipa_ui_dismiss', {'revision': revision})
            else:
                component = ('Maipa.ClientFacts' if 'fact' in text else 'Maipa.ClientCatalog' if 'client setup' in text
                    else 'Maipa.AdminUsers' if 'users and access' in text else 'Maipa.Note')
                schema = {'id': 'shared-root', 'type': component}
                if component == 'Maipa.Note':
                    schema['props'] = {'text': 'Synthetic updated note' if 'update' in text and 'delayed update' not in text else 'Synthetic shared note'}
                callback('maipa_ui_update' if catalog['composition'] else 'maipa_ui_present', {
                    'revision': revision, 'title': 'Shared work', 'schema': schema})
            for chunk in ('Synthetic agent ', 'reply. Shared UI ', 'is synchronized.'):
                send({'method': 'session/update', 'params': {'sessionId': session,
                    'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': chunk}}}})
                time.sleep(.1)
                if 'delayed update' in text and chunk == 'Synthetic agent ':
                    time.sleep(1.5)
                    latest = callback('maipa_ui_catalog', {})
                    callback('maipa_ui_update', {'revision': latest['revision'], 'title': 'Shared work',
                        'schema': {'id': 'shared-root', 'type': 'Maipa.Note', 'props': {'text': 'Synthetic delayed update'}}})
                if 'wait for cancellation' in text:
                    while incoming := sys.stdin.readline():
                        if json.loads(incoming).get('method') == 'session/cancel':
                            raise InterruptedError()
            send({'id': frame['id'], 'result': {'stopReason': 'end_turn'}})
        except InterruptedError:
            send({'id': frame['id'], 'result': {'stopReason': 'cancelled'}})
    elif method == 'maipa/shutdown':
        send({'id': frame['id'], 'result': {'verified': True, 'survivors': 0, 'sessions': 1}})
