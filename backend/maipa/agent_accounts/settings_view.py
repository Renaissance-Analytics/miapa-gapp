"""Owned human settings metadata; no discovery, login, process or filesystem probe."""
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError,transaction
from django.http import JsonResponse
from backend.maipa.api import error,method_not_allowed
from . import chat
from .chat_views import _guard
from .models import AgentAccount,ProviderProfile
from .providers import registrations
from .references import _binding,ReferenceUnavailable

class SettingsUnavailable(Exception):pass

@transaction.atomic
def snapshot(actor):
    try:
        chat.authority(actor);providers=registrations();accounts=[]
        for account in AgentAccount.objects.filter(owner=actor,enabled=True).order_by('provider','id')[:65]:
            original=_binding(actor,str(account.pk))
            profile=ProviderProfile.objects.filter(account=account).first()
            if profile and (profile.user_id,profile.account_id,profile.installation_id,str(profile.storage_id),profile.provider)!=original:
                raise SettingsUnavailable()
            accounts.append({'id':str(account.pk),'provider':account.provider,'label':account.label,'profile_assigned':profile is not None})
        if len(accounts)>64:raise SettingsUnavailable()
        return {'user_id':str(actor.pk),'providers':providers,'accounts':accounts}
    except (chat.ChatUnavailable,ReferenceUnavailable,DatabaseError,ObjectDoesNotExist,ValueError,OSError):
        raise SettingsUnavailable() from None

def settings(request):
    denied=_guard(request)
    if denied is not None:return denied
    if request.method!='GET':return method_not_allowed('GET')
    try:return JsonResponse(snapshot(request.user))
    except SettingsUnavailable:return error('agent_settings_unavailable',404)
