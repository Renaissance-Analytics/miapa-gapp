"""Synthetic bidirectional ACP peer; no provider, credentials or model call."""
import json
import sys


def send(value):
    print(json.dumps({'jsonrpc': '2.0', **value}), flush=True)


for line in sys.stdin:
    frame = json.loads(line)
    method = frame.get('method')
    if method == 'initialize':
        send({'id': frame['id'], 'result': {'protocolVersion': 1}})
    elif method == 'session/new':
        send({'id': frame['id'], 'result': {'sessionId': 'synthetic-ui-session'}})
    elif method == 'session/prompt':
        turn = frame['id']
        text = frame['params']['prompt'][0]['text']
        send({'id': 'ui-1', 'method': 'maipa/ui', 'params': {'method': 'tools/list', 'params': {}}})
        send({'method': 'session/update', 'params': {'sessionId': 'synthetic-ui-session',
              'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'Still streaming'}}}})
        if text == 'flood':
            for i in range(2, 10):
                send({'id': f'ui-{i}', 'method': 'maipa/ui', 'params': {'method': 'tools/list', 'params': {}}})
        elif text == 'foreign':
            send({'id': 'foreign', 'method': 'terminal/create', 'params': {'command': 'never run'}})
        elif text == 'permission':
            send({'id': 'permission', 'method': 'session/request_permission', 'params': {}})
    elif frame.get('id') == 'foreign' or frame.get('id') == 'permission':
        send({'id': turn, 'result': frame})
    elif frame.get('id') == 'ui-1':
        if text not in ('foreign', 'permission', 'flood'):
            send({'id': turn, 'result': frame})
    elif frame.get('id') == 'ui-9':
        send({'id': turn, 'result': frame})
    elif method == 'maipa/shutdown':
        send({'id': frame['id'], 'result': {'verified': True, 'survivors': 0, 'sessions': 0}})
