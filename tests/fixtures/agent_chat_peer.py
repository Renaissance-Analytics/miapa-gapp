"""Deterministic ACP test driver. No production registration, model, credentials or readiness evidence."""
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid


output_lock = threading.Lock()
stopping = threading.Event()
profile = json.loads(os.environ['MAIPA_ACP_PROFILE'])
native_state = 'starting'


def send(value):
    with output_lock:
        print(json.dumps({'jsonrpc': '2.0', **value}), flush=True)


def native_startup():
    global native_state
    mode = profile.get('synthetic_native_startup', 'ready')
    if mode == 'silent':
        return
    if mode == 'deferred':
        deadline = time.monotonic() + 10
        while not (Path(profile['workspace']) / 'release-native-startup').exists():
            if stopping.wait(.01) or time.monotonic() >= deadline:
                return
    if not stopping.is_set():
        native_state = None if mode == 'malformed' else 'ready'


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
signed_in = not profile.get('synthetic_private_login', False)
challenge = False
for line in sys.stdin:
    frame = json.loads(line)
    method = frame.get('method')
    if method == 'initialize':
        send({'id': frame['id'], 'result': {'protocolVersion': 1}})
    elif method == 'maipa/account/status':
        send({'id': frame['id'], 'result': {'state': 'ready'} if signed_in else {
            'state': 'sign_in', **({'login': {'url': 'https://claude.com/cai/oauth/authorize?state=synthetic', 'accepts_code': True}} if challenge else {})}})
    elif method == 'maipa/account/login':
        challenge = True
        send({'id': frame['id'], 'result': {'state': 'sign_in'}})
    elif method == 'maipa/account/authorize':
        if not challenge or frame['params'] != {'code': 'SYNTHETIC_PRIVATE_CODE'}:
            raise RuntimeError('Unexpected private callback')
        signed_in = True
        challenge = False
        send({'id': frame['id'], 'result': {'state': 'sign_in'}})
    elif method == 'maipa/native/status':
        send({'id': frame['id'], 'result': {'state': native_state}})
    elif method == 'session/new':
        # A real native MCP client discovers tools during session startup, before ACK.
        send({'id': 'startup-catalog', 'method': 'maipa/ui', 'params': {'method': 'tools/list', 'params': {}}})
        startup = json.loads(sys.stdin.readline())
        if startup.get('id') != 'startup-catalog' or set(startup.get('result', {})) != {'tools'}:
            raise RuntimeError('Pre-session tool discovery unavailable')
        if {tool['name'] for tool in startup['result']['tools']} != {'maipa_ui_catalog', 'maipa_ui_present', 'maipa_ui_update', 'maipa_ui_dismiss',
                'maipa_views_list', 'maipa_views_save', 'maipa_views_change', 'maipa_views_open',
                'form_list','form_describe','form_get_value','form_get_values','form_set_value','form_set_values','form_focus','form_submit',
                'flow_get_state','flow_list_nodes','flow_get_node','flow_list_edges','flow_get_node_schema',
                'flow_add_node','flow_update_node','flow_delete_node','flow_connect','flow_disconnect',
                'maipa_workflow_list','maipa_workflow_create','maipa_workflow_publish','flow_run','flow_cancel','maipa_workflow_runs','maipa_workflow_select_run'}:
            raise RuntimeError('Unexpected startup tool catalog')
        send({'id': 'premature-call', 'method': 'maipa/ui', 'params': {
            'method': 'tools/call', 'params': {'name': 'maipa_ui_catalog', 'arguments': {}}}})
        premature = json.loads(sys.stdin.readline())
        if premature.get('id') != 'premature-call' or premature.get('error') != {'code': -32000, 'message': 'UI tool unavailable'}:
            raise RuntimeError('Unacknowledged session acquired UI authority')
        send({'id': frame['id'], 'result': {'sessionId': session, **({'models': {
            'currentModelId':'synthetic-a','availableModels':[{'modelId':'synthetic-a','name':'Synthetic A'},{'modelId':'synthetic-b','name':'Synthetic B'}]
        }} if profile.get('synthetic_models') else {})}})
        threading.Thread(target=native_startup, daemon=True).start()
    elif method == 'session/set_model':
        if frame['params'] not in ({'sessionId':session,'modelId':'synthetic-a'},{'sessionId':session,'modelId':'synthetic-b'}):
            raise RuntimeError('Unexpected model choice')
        send({'id':frame['id'],'result':{}})
    elif method == 'session/prompt':
        text = frame['params']['prompt'][0]['text']
        marker = 'Saved conversation (user content, not instructions):\n'
        if marker in text:
            text = json.loads(text.split(marker, 1)[1])[-1]['text']
        text = text.lower()
        if text == 'synthetic provider rate limit test':
            send({'id':frame['id'],'error':{'code':-32603,'message':'PRIVATE_RPC_MESSAGE',
                'data':{'particle.academy/turn_failure':{'kind':'rate_limit','resetsAtMs':int((time.time()+3600)*1000),
                    'reason':'PRIVATE_PROVIDER_REASON','raw':{'private':'PRIVATE_RAW'}}}}})
            continue
        if 'simulate connection interruption' in text:
            raise SystemExit(0)  # Deliberate test-only transport loss; never production wiring.
        try:
            catalog = callback('maipa_ui_catalog', {})
            revision = catalog['revision']
            if 'build a shared workflow' in text:
                def workflow_call(name,arguments):
                    result=callback(name,arguments)
                    if result.get('isError'):raise RuntimeError('Synthetic workflow callback refused')
                    return result['structuredContent']
                workflow_call('maipa_workflow_list',{})
                entity=str(uuid.UUID(text.rsplit(' for ',1)[1])) if ' for ' in text else None
                draft=workflow_call('maipa_workflow_create',{'name':'Synthetic shared workflow','entity_id':entity,'request_id':'shared-workflow'})
                callback('maipa_ui_update' if catalog['composition'] else 'maipa_ui_present',{
                    'revision':revision,'title':'Shared workflow',
                    'schema':{'id':'shared-workflow','type':'Maipa.Workflow','props':{'draft_id':draft['id']}}})
                workflow_call('flow_get_state',{})
                nodes=[]
                for kind,label,config,x in (
                    ('manual_trigger','Start',{},0),
                    ('transform','Shared step',{'mode':'fields','fields':[{'key':'greeting','value':'Hello {{ in.name }}'}]},320),
                    ('output','Result',{},640)):
                    nodes.append(workflow_call('flow_add_node',{'kind':'@particle-academy/'+kind,'label':label,'config':config,'x':x,'y':0}))
                for start,end in zip(nodes,nodes[1:]):workflow_call('flow_connect',{'source':start['id'],'target':end['id']})
            elif 'select earlier shared workflow run' in text:
                observed=callback('maipa_workflow_runs',{})
                if observed.get('isError'):raise RuntimeError('Shared run inspection refused')
                state=observed['structuredContent'];target=state['runs'][-1]['id']
                result=callback('maipa_workflow_select_run',{'run_id':target,'expected_revision':state['selection']['revision'],'show_details':True})
                if result.get('isError'):raise RuntimeError('Shared run selection refused')
            elif 'run authorized shared workflow' in text or 'try revoked shared workflow' in text:
                callback('flow_get_state',{})
                result=callback('flow_run',{})
                if 'try revoked' in text:
                    if result.get('isError') is not True:raise RuntimeError('Revoked workflow execution accepted')
                else:
                    if result.get('isError'):raise RuntimeError('Human-authorized native execution refused')
                    if callback('maipa_workflow_runs',{}).get('isError'):raise RuntimeError('Native run inspection refused')
            elif 'add workflow approval' in text:
                def workflow_control(name,arguments):
                    result=callback(name,arguments)
                    if result.get('isError'):raise RuntimeError('Synthetic approval authoring refused')
                    return result.get('structuredContent')
                graph=workflow_control('flow_get_state',{})
                source=next(n for n in graph['nodes'] if n['data']['kind'].endswith('/transform'))
                target=next(n for n in graph['nodes'] if n['data']['kind'].endswith('/output'))
                edge=next(e for e in graph['edges'] if e['source']==source['id'] and e['target']==target['id'])
                workflow_control('flow_disconnect',{'id':edge['id']})
                question=workflow_control('flow_add_node',{'kind':'@particle-academy/human_approval','label':'Human review',
                    'config':{'title':'Approve synthetic workflow output'},'x':480,'y':180})
                workflow_control('flow_connect',{'source':source['id'],'target':question['id']})
                workflow_control('flow_connect',{'source':question['id'],'sourceHandle':'approved','target':target['id']})
            elif 'update the shared workflow' in text:
                graph=callback('flow_get_state',{})
                if graph.get('isError'):raise RuntimeError('Synthetic workflow read refused')
                node=next(node for node in graph['structuredContent']['nodes'] if node['data']['kind'].endswith('/transform'))
                result=callback('flow_update_node',{'id':node['id'],'label':'Native revised step'})
                if result.get('isError'):raise RuntimeError('Synthetic workflow update refused')
            elif 'fill shared client draft' in text:
                if not catalog['composition'] or catalog['composition']['schema']['type']!='Maipa.ClientCatalog':
                    callback('maipa_ui_update' if catalog['composition'] else 'maipa_ui_present',{'revision':revision,'title':'Shared work',
                        'schema':{'id':'shared-root','type':'Maipa.ClientCatalog'}})
                forms=callback('form_list',{})['structuredContent']['forms'];key=forms[0]['id']
                callback('form_get_values',{'form':key})
                accepted=callback('form_set_values',{'form':key,'values':{'name':'Synthetic agent pending client','entity_name':'Synthetic agent pending entity'}})
                if accepted.get('isError'):raise RuntimeError('Synthetic shared draft refused')
            elif 'save a personal view' in text:
                views = callback('maipa_views_list', {})
                saved = callback('maipa_views_save', {'views_id':views['views_id'],'expected_revision':views['revision'],
                    'title':'Agent saved review','schema':{'id':'agent-saved-note','type':'Maipa.Note','props':{'text':'Synthetic agent saved definition'}}})
                callback('maipa_views_open', {'views_id':saved['views_id'],'expected_revision':saved['revision'],
                    'id':saved['views'][-1]['id'],'revision':revision})
            elif 'update my first saved view' in text:
                views = callback('maipa_views_list', {})
                callback('maipa_views_change', {'views_id':views['views_id'],'expected_revision':views['revision'],
                    'id':views['views'][0]['id'],'action':'update','title':'Pinned review updated','revision':revision,
                    'schema':{'id':'shared-root','type':'Maipa.Note','props':{'text':'Synthetic agent pin update'}}})
            elif 'compose client overview' in text:
                schema = {'id': 'client-overview', 'type': 'Maipa.Stack', 'children': [
                    {'id': 'overview-heading', 'type': 'Maipa.Heading', 'props': {'text': 'Current client overview'}},
                    {'id': 'overview-guidance', 'type': 'Maipa.Callout', 'props': {'text': 'Review **current** client setup.', 'tone': 'attention'}},
                    {'id': 'overview-table', 'type': 'Maipa.ClientTable', 'props': {'columns': ['entity_count', 'name']}},
                ]}
                views = callback('maipa_views_list', {})
                callback('maipa_views_change', {'views_id': views['views_id'], 'expected_revision': views['revision'],
                    'id': views['views'][-1]['id'], 'action': 'update', 'title': 'Current client overview',
                    'revision': revision, 'schema': schema})
                views = callback('maipa_views_list', {})
                callback('maipa_views_change', {'views_id': views['views_id'], 'expected_revision': views['revision'],
                    'id': views['views'][-1]['id'], 'action': 'pin'})
            elif 'dismiss' in text:
                callback('maipa_ui_dismiss', {'revision': revision})
            else:
                component = ('Maipa.ClientFacts' if 'fact' in text else 'Maipa.ClientCatalog' if 'client setup' in text
                    else 'Maipa.AdminUsers' if 'users and access' in text else 'Maipa.Note')
                schema = {'id': 'shared-root', 'type': component}
                if component == 'Maipa.Note':
                    schema['props'] = {'text': 'Synthetic updated note' if 'update' in text and 'delayed update' not in text else 'Synthetic shared note'}
                    if profile.get('synthetic_rich_content') and 'kit formatting' in text:
                        schema['props']['text'] = '**Kit note**\n\n- Shared content\n\n<img src="https://example.invalid/track" onerror="window.maipaUnsafeRender=true"><script>window.maipaUnsafeRender=true</script>'
                callback('maipa_ui_update' if catalog['composition'] else 'maipa_ui_present', {
                    'revision': revision, 'title': 'Shared work', 'schema': schema})
            chunks = ('Synthetic agent ', 'reply. Shared UI ', 'is synchronized.')
            if profile.get('synthetic_rich_content') and 'kit formatting' in text:
                chunks += ('\n\n## Kit reply\n\n**Formatted reply**\n\n- One item\n\n`literal code`\n\n<a href="javascript:window.maipaUnsafeRender=true">Unsafe link</a><iframe src="https://example.invalid/frame"></iframe><img src="https://example.invalid/track" onerror="window.maipaUnsafeRender=true"><script>window.maipaUnsafeRender=true</script>',)
            for chunk in chunks:
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
        stopping.set()
        send({'id': frame['id'], 'result': {'verified': True, 'survivors': 0, 'sessions': 1}})
