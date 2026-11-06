"""Finite ACP model negotiation peer; never registered by production."""
import json,sys,uuid
session=str(uuid.uuid4())
profile=json.loads(__import__('os').environ['MAIPA_ACP_PROFILE'])
def send(identity,result=None,error=None):
 print(json.dumps({'jsonrpc':'2.0','id':identity,**({'error':{'code':-32602,'message':'Synthetic model refusal'}} if error else {'result':result})}),flush=True)
for line in sys.stdin:
 frame=json.loads(line);method=frame.get('method');identity=frame.get('id')
 if method=='initialize':send(identity,{'protocolVersion':1})
 elif method=='maipa/account/status':send(identity,{'state':'ready'})
 elif method=='session/new':
  models={'currentModelId':'model-a','availableModels':[{'modelId':'model-a','name':'Model A','_meta':{'private':'SYNTHETIC_PRIVATE_STRIPPED'}},{'modelId':'model-b','name':'Model B'}]}
  if profile.get('synthetic_malformed_models'):models['availableModels'].append(models['availableModels'][0])
  if profile.get('synthetic_empty_models'):models={'availableModels':[],'currentModelId':None}
  send(identity,{'sessionId':session,'models':models})
 elif method=='maipa/native/status':send(identity,{'state':'ready'})
 elif method=='session/set_model':
  params=frame.get('params',{})
  valid=set(params)=={'sessionId','modelId'} and params['sessionId']==session and params['modelId'] in ('model-a','model-b')
  send(identity,{},error=not valid or profile.get('synthetic_model_refusal'))
 elif method=='maipa/shutdown':send(identity,{'verified':True,'survivors':0,'sessions':1});break
 elif identity is not None:send(identity,{},error=True)
