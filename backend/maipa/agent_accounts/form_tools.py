"""Finite public Fancy bridge consumer; original actor/version never come from RPC data."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections

from . import form_drafts as drafts, ui
from .form_drafts import FormUnavailable
from .references import _uuid

MESSAGE='Form draft unavailable'
TOOLS={'form_list':(), 'form_describe':('form',), 'form_get_values':('form',),
       'form_get_value':('form','field'), 'form_set_value':('form','field','value'),
       'form_set_values':('form','values'), 'form_focus':('form','field'), 'form_submit':('form',)}
_definitions=None


def _error():
    return {'content':[{'type':'text','text':'The shared form changed or is unavailable. Read it again before editing.'}],'isError':True}


@sync_to_async(thread_sensitive=True)
def _database(operation,*args):
    try:return operation(*args)
    finally:
        for connection in connections.all():
            if not connection.in_atomic_block:connection.close()


async def _run(forms, name=None, arguments=None, commit=None):
    root=Path(__file__).resolve().parents[3]/'frontend'
    # Resolve only installation-owned code and the configured executable. RPC
    # arguments cannot choose a process, path, environment or transport method.
    node=settings.ACP_NODE_EXECUTABLE or shutil.which('node')
    if not node or not Path(node).is_absolute():raise FormUnavailable(MESSAGE)
    environment={key:value for key,value in os.environ.items() if key.upper() in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP'}}
    process=None
    try:
        process=await asyncio.create_subprocess_exec(node,str(root/'tools/form-host.mjs'),cwd=root,env=environment,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
            limit=131_072,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        payload={'forms':forms,'listOnly':name is None,'name':name,'arguments':arguments}
        wire=json.dumps(payload,allow_nan=False,ensure_ascii=True).encode()+b'\n'
        if len(wire)>65_536:raise ValueError()
        process.stdin.write(wire);await asyncio.wait_for(process.stdin.drain(),5)
        async with asyncio.timeout(10):
            for _ in range(3):
                line=await process.stdout.readline()
                if not line or len(line)>65_536:raise ValueError()
                value=json.loads(line)
                if type(value) is not dict:raise ValueError()
                if set(value)=={'kind','form','values'} and value['kind']=='commit' and commit:
                    answer=await commit(value['form'],value['values'])
                    process.stdin.write(json.dumps(answer,allow_nan=False).encode()+b'\n');await process.stdin.drain()
                elif set(value)=={'kind','result'} and value['kind']=='result':
                    await process.wait()
                    if process.returncode!=0:raise ValueError()
                    return value['result']
                else:raise ValueError()
            raise ValueError()
    except (OSError,ValueError,TypeError,AttributeError,TimeoutError,RecursionError):
        raise FormUnavailable(MESSAGE) from None
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


async def public_form_tools():
    global _definitions
    if _definitions is None:
        value=await _run([])
        if type(value) is not list or {tool.get('name') for tool in value}!=set(TOOLS):raise FormUnavailable(MESSAGE)
        _definitions=deepcopy(value)
    return deepcopy(_definitions)


class FormBroker:
    def __init__(self, actor, account_id, identity):
        if not isinstance(actor,get_user_model()) or actor.is_authenticated is not True or actor.pk is None:
            raise FormUnavailable(MESSAGE)
        self.actor, self.account_id, self.identity = actor, _uuid(account_id), _uuid(identity)
        self.observed={};self.lock=asyncio.Lock();self.last_activity=None

    def _snapshots(self):
        try:
            actor=get_user_model().objects.get(pk=self.actor.pk,is_active=True)
            composition=ui.ui_snapshot(actor,self.account_id,self.identity)['composition']
            return [drafts.read_new_client_form(actor,self.account_id,self.identity,handle)
                    for handle in sorted(drafts._catalog_handles(composition))]
        except (ui.UiUnavailable,ValueError,get_user_model().DoesNotExist):
            raise FormUnavailable(MESSAGE) from None

    def _current(self):
        values=self._snapshots()
        if not values:raise FormUnavailable(MESSAGE)
        return values

    async def tools(self):
        return await public_form_tools()

    async def call(self, name, arguments):
        async with self.lock:
            current=await _database(self._current)
            if (name not in TOOLS or type(arguments) is not dict or set(arguments)-set(TOOLS[name])):return _error()
            try:
                if len(json.dumps(arguments,allow_nan=False,ensure_ascii=True).encode())>4096:return _error()
            except (ValueError,TypeError,RecursionError):return _error()
            mutation=name in ('form_set_value','form_set_values')
            if mutation and any(value['id'] not in self.observed for value in current):return _error()
            state=deepcopy([self.observed[value['id']] if mutation else value for value in current])
            accepted_activity=None
            async def commit(identity,values):
                nonlocal accepted_activity
                original=next((value for value in state if value['id']==identity),None)
                if original is None:return {'ok':False}
                try:
                    changed=await _database(drafts.change_new_client_form,self.actor,self.account_id,self.identity,
                        original['component_id'],{'id':original['id'],'expected_revision':original['revision'],'values':values})
                    self.observed[identity]=deepcopy(changed)
                    if changed['revision']>original['revision']:
                        accepted_activity={'id':str(uuid.uuid4()),'ui_id':self.identity,'form_id':identity,
                            'component_id':original['component_id'],'revision':changed['revision'],
                            'action':name,'at_ms':int(time.time()*1000)}
                    return {'ok':True,'values':changed['values']}
                except FormUnavailable:return {'ok':False}
            result=await _run(state,name,arguments,commit)
            checked=await _database(self._current)
            if {value['id'] for value in checked}!={value['id'] for value in state}:raise FormUnavailable(MESSAGE)
            if (type(result) is not dict or set(result)-{'content','structuredContent','isError'} or
                    type(result.get('content')) is not list or
                    ('isError' in result and type(result['isError']) is not bool)):raise FormUnavailable(MESSAGE)
            for item in result['content']:
                if type(item) is not dict or set(item)!={'type','text'} or item['type']!='text' or type(item['text']) is not str:
                    raise FormUnavailable(MESSAGE)
            if not result.get('isError',False) and name in ('form_list','form_describe','form_get_value','form_get_values'):
                self.observed={value['id']:deepcopy(value) for value in current}
            if not result.get('isError',False) and accepted_activity:self.last_activity=accepted_activity
            return result
