"""Finite content-hostile RPC errors; never a provider/model acceptance fixture."""
import json, sys
from pathlib import Path

mode=sys.argv[1]
if len(sys.argv)==3:Path(sys.argv[2]).write_bytes(b'ready')
for line in sys.stdin:
    request=json.loads(line);method=request.get('method')
    result={}
    if method=='initialize':result={'protocolVersion':1}
    elif method=='session/new':result={'sessionId':'synthetic-failure-session'}
    elif method in ('session/prompt','synthetic/nonprompt'):
        failure={'kind':'rate_limit','resetsAtMs':1793313000000,'reason':'PRIVATE_PROVIDER_REASON','raw':{'secret':'PRIVATE_RAW'}}
        if mode=='unknown':failure['kind']='PRIVATE_UNKNOWN_KIND'
        if mode=='bad-reset':failure['resetsAtMs']=True
        if mode=='other':failure={'kind':'other','reason':'PRIVATE_PROVIDER_REASON'}
        data={'particle.academy/turn_failure':failure}
        if mode=='nested':data={'_meta':data}
        print(json.dumps({'jsonrpc':'2.0','id':request['id'],'error':{'code':-32603,'message':'PRIVATE_RPC_MESSAGE','data':data}}),flush=True)
        continue
    elif method=='maipa/shutdown':result={'verified':True,'survivors':0,'sessions':0}
    else:continue
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
