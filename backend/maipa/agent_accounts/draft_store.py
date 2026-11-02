"""Bounded owned draft storage; preparing a retry never submits it."""
from copy import deepcopy
import json,uuid
from django.conf import settings
from django.db import transaction,IntegrityError
from django.db.models import F
from backend.maipa.storage import installation_metadata
from . import chat
from .models import AgentAccount,ChatDraftRecord,Conversation

class DraftUnavailable(Exception):pass
class DraftConflict(DraftUnavailable):pass

def _require(value):
    if not value:raise ValueError('Invalid draft')

def _text(value,limit):
    _require(type(value) is str and len(value)<=limit and not any(ord(c)<32 and c not in '\t\n\r' for c in value))

def _file(value):
    _require(type(value) is dict and set(value)=={'id','name','type','bytes'})
    for key,limit in (('id',200),('name',240),('type',128)):_text(value[key],limit)
    _require(value['id'] and type(value['bytes']) is int and 0<=value['bytes']<=100_000_000)
    # This app has no durable attachment upload path yet. A URL/path/ref cannot
    # become a trusted handle; names without bytes remain visibly unsendable.

def validate(value):
    _require(type(value) is dict and set(value)=={'v','text','pills','attachments'} and type(value['v']) is int and value['v']==1)
    _text(value['text'],32768)
    _require(type(value['pills']) is list and len(value['pills'])<=24 and type(value['attachments']) is list and len(value['attachments'])<=8)
    ids=set()
    for pill in value['pills']:
        _require(type(pill) is dict)
        if pill.get('kind')=='text':
            _require(set(pill) in ({'kind','id','text'},{'kind','id','text','label'}));_text(pill['text'],32768)
            if 'label' in pill:_text(pill['label'],240)
        else:
            _require(pill.get('kind')=='image' and set(pill)=={'kind','id','label','file'});_text(pill['label'],240);_file(pill['file'])
        _text(pill['id'],200);_require(pill['id'] and pill['id'] not in ids);ids.add(pill['id'])
    for item in value['attachments']:_file(item)
    _require(len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())<=65536)
    return deepcopy(value)

def _pending(value):
    if value is None:return None
    _require(type(value) is dict and set(value)=={'id','text','draft'})
    _require(type(value['id']) is str and str(uuid.UUID(value['id']))==value['id'])
    _text(value['text'],8000);_require(value['text'].strip() and '\r' not in value['text']);validate(value['draft']);return deepcopy(value)

def _scope(actor,account_id):
    try:
        member=chat.authority(actor);storage=installation_metadata(settings.DATA_ROOT)['storage_id']
        if account_id is not None:
            _require(type(account_id) is str and str(uuid.UUID(account_id))==account_id)
            account=AgentAccount.objects.filter(pk=account_id,owner=actor,enabled=True).first()
            if account is None:raise DraftUnavailable()
            original=ChatDraftRecord.objects.filter(account=account).first()
            if original and original.user_id!=actor.pk:raise DraftUnavailable()
        else:account=None
        return member,storage,account,account_id or 'unselected'
    except (chat.ChatUnavailable,OSError):raise DraftUnavailable() from None

def _row(actor,account_id):
    member,storage,account,scope=_scope(actor,account_id)
    row=ChatDraftRecord.objects.filter(user=actor,scope=scope).first()
    if row and (row.installation_id,str(row.storage_id),row.account_id)!=(member.installation_id,storage,account.pk if account else None):raise DraftUnavailable()
    return row,member,storage,account,scope

@transaction.atomic
def read(actor,account_id):
    row,_,_,account,_=_row(actor,account_id)
    value={'account_id':account_id,'revision':row.revision if row else 0,'draft':validate(row.draft) if row else {'v':1,'text':'','pills':[],'attachments':[]},
        'pending':_pending(row.pending) if row else None,'accepted':False}
    if account and value['pending']:
        conversation=chat._conversation(actor,account)
        if conversation and value['pending']['id'] in conversation.requests:
            message=next((m for m in conversation.messages if m['id']==value['pending']['id']),None)
            if not message or message['text']!=value['pending']['text']:raise DraftUnavailable()
            value['accepted']=True
    return value

@transaction.atomic
def save(actor,account_id,expected_revision,draft,pending):
    _require(type(expected_revision) is int and 0<=expected_revision<1_999_999_999)
    draft,pending=validate(draft),_pending(pending)
    # Acquire the writer before reading the CAS/origin snapshot. A deferred
    # SQLite read-to-write upgrade otherwise refuses immediately when another
    # app writer holds a reserved lock, despite the normal busy timeout.
    ChatDraftRecord.objects.filter(user_id=getattr(actor,'pk',None),scope=account_id or 'unselected').update(revision=F('revision'))
    row,member,storage,account,scope=_row(actor,account_id)
    if row is None:
        if expected_revision!=0:raise DraftConflict()
        try:row=ChatDraftRecord.objects.create(user=actor,installation_id=member.installation_id,storage_id=storage,account=account,scope=scope,draft=draft,pending=pending)
        except IntegrityError:raise DraftConflict() from None
    if ChatDraftRecord.objects.filter(pk=row.pk,revision=expected_revision).update(revision=F('revision')+1,draft=draft,pending=pending)!=1:raise DraftConflict()
    return read(actor,account_id)
