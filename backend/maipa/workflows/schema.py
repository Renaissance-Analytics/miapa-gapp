"""Bounded host admission policy over the published WorkflowSchema importer."""
from dataclasses import replace
from graphlib import CycleError, TopologicalSorter
import hashlib
import json
import math
import re

from fancy_flow import builtin, import_workflow
from fancy_flow.exceptions import FlowError
from fancy_flow.registry import NodeKindRegistry
from fancy_flow.security import GraphPolicy

from .errors import InvalidWorkflow

RUNTIME = 'fancy-flow/0.30.1'
KINDS = ('manual_trigger', 'transform', 'output', 'human_approval', 'user_input')
REGISTRY = builtin.register(NodeKindRegistry())
POLICY = replace(GraphPolicy.untrusted(allow=list(KINDS)), max_nodes=30, max_edges=60, max_bytes=65536,
                 max_depth=12, max_string_length=20000)


def require(condition):
    if not condition:
        raise InvalidWorkflow()


def bounded_json(value, maximum=32768, depth=0):
    """Reject non-JSON/nonfinite/control data and detach all caller-owned values."""
    def inspect(item, level):
        require(level <= 12)
        if type(item) is dict:
            require(all(type(key) is str for key in item))
            for key, child in item.items():
                inspect(key, level+1); inspect(child, level+1)
        elif type(item) is list:
            for child in item: inspect(child, level+1)
        elif type(item) is str:
            require(len(item) <= maximum and not any((ord(c)<32 and c not in '\t\n\r') or 127<=ord(c)<=159 for c in item))
        else:
            require(item is None or type(item) in (bool,int,float))
            if type(item) is float: require(math.isfinite(item))
            if type(item) is int: require(abs(item) <= 9_007_199_254_740_991)
    try:
        inspect(value,depth)
        encoded = json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf-8')
        require(len(encoded) <= maximum)
        return json.loads(encoded), hashlib.sha256(encoded).hexdigest()
    except (TypeError,UnicodeError,RecursionError,ValueError):
        raise InvalidWorkflow() from None


def handle(value):
    require(type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,80}',value) is not None)
    return value


def gate_config(kind,config):
    """Bounded host form policy, over the published human-node field contract."""
    allowed={'title','description'} if kind=='human_approval' else {'title','fields'}
    require(type(config) is dict and set(config)<=allowed)
    title=config.get('title','Approve action' if kind=='human_approval' else 'Need your input')
    require(type(title) is str and 0<len(title.strip())<=120)
    if kind=='human_approval':
        description=config.get('description')
        require(description is None or type(description) is str and len(description)<=2000)
        return {'title':title,'description':description}
    fields=config.get('fields')
    require(type(fields) is list and 1<=len(fields)<=12)
    seen=set()
    for field in fields:
        require(type(field) is dict and {'key','label','type'}<=set(field)<={'key','label','type','required'})
        handle(field['key']);require(field['key'] not in seen);seen.add(field['key'])
        require(type(field['label']) is str and 0<len(field['label'].strip())<=120)
        require(field['type'] in ('text','textarea','checkbox') and type(field.get('required',False)) is bool)
    return {'title':title,'fields':fields}


def validate(value):
    value, digest = bounded_json(value,65536)
    require(type(value) is dict and {'version','graph'} <= set(value) <= {'version','graph','$schema','metadata','view'})
    require(type(value['version']) is int and value['version']==1)
    if '$schema' in value: require(value['$schema']=='https://particle.academy/schemas/workflow/v1.json')
    if 'metadata' in value:
        meta=value['metadata']; require(type(meta) is dict and set(meta)<={'id','name','description','createdAt','updatedAt','author','tags'})
        for key,item in meta.items():
            if key in ('createdAt','updatedAt'): require(type(item) is int and 0<=item<=9_007_199_254_740_991)
            elif key=='tags': require(type(item) is list and len(item)<=20 and all(type(tag) is str and len(tag)<=128 for tag in item))
            else: require(type(item) is str and len(item)<=1024)
    if 'view' in value:
        view=value['view']; require(type(view) is dict and set(view)<={'viewport'})
        if 'viewport' in view:
            viewport=view['viewport']; require(type(viewport) is dict and set(viewport)=={'x','y','zoom'})
            require(all(type(number) in (int,float) and abs(number)<=1_000_000 for number in viewport.values()))
            require(0<viewport['zoom']<=10)
    raw = value['graph']
    require(type(raw) is dict and set(raw)=={'nodes','edges'} and type(raw['nodes']) is list and type(raw['edges']) is list)
    require(2 <= len(raw['nodes']) <= 30 and len(raw['edges']) <= 60)
    try:
        POLICY.assert_safe(value)
        for node in raw['nodes']:
            require(type(node) is dict and {'id','kind'} <= set(node) <= {'id','kind','config','position','label','description','startingMsg','stoppingMsg','inputs','outputs','width','height'})
            handle(node['id']); kind = REGISTRY.get(node['kind'])
            require(kind is not None and kind.name.rsplit('/',1)[-1] in KINDS)
            # Published Fancy exporters omit empty config. An absent object
            # means its documented empty default; an explicit non-object refuses.
            config = node.get('config',{}); require(type(config) is dict)
            require(set(config) <= {field.key for field in kind.config_schema})
            require(not REGISTRY.validate_config(kind,config))
            if kind.name.rsplit('/',1)[-1]=='transform':
                # Complete publication requires the selected authoring mode's
                # explicit config; unfinished expression drafts stay editable.
                if config.get('mode','expression')=='expression':
                    require(type(config.get('expression')) is str and 0 < len(config['expression'].strip()) <= 20000)
                else:
                    require(config.get('mode')=='fields' and type(config.get('fields')) is list)
            if kind.name.rsplit('/',1)[-1] in ('human_approval','user_input'):
                gate_config(kind.name.rsplit('/',1)[-1],config)
            for key in ('label','description','startingMsg','stoppingMsg'):
                if key in node: require(type(node[key]) is str and len(node[key]) <= 1024)
            for key,default in (('inputs','in'),('outputs','out')):
                if key in node:
                    declared=getattr(kind,key)
                    expected=[port.to_dict() for port in declared] if declared is not None else [{'id':default}]
                    require(node[key]==expected)
            for key in ('width','height'):
                if key in node: require(type(node[key]) in (int,float) and 0<node[key]<=10000)
            if 'position' in node:
                pos=node['position']; require(type(pos) is dict and set(pos)=={'x','y'})
                require(all(type(n) in (int,float) and abs(n) <= 1_000_000 for n in pos.values()))
        result = import_workflow(value,registry=REGISTRY)
        require(not result.refused and result.ok and not result.issues)
        graph = result.graph
        require(sum(n.type.rsplit('/',1)[-1]=='manual_trigger' for n in graph.nodes)==1)
        require(any(n.type.rsplit('/',1)[-1]=='output' for n in graph.nodes))
        edge_ids=set(); predecessors={node.id:set() for node in graph.nodes}
        for edge in raw['edges']:
            require(type(edge) is dict and {'id','source','target'} <= set(edge) <= {'id','source','target','sourceHandle','targetHandle','label'})
            handle(edge['id']); require(edge['id'] not in edge_ids); edge_ids.add(edge['id'])
            source=graph.node(edge['source']); target=graph.node(edge['target']); require(source is not None and target is not None)
            for key,node,direction,default in (('sourceHandle',source,'outputs','out'),('targetHandle',target,'inputs','in')):
                kind=REGISTRY.get(node.type); ports=getattr(kind,direction)
                allowed={port.id for port in ports} if ports is not None else {default}
                require(edge.get(key) is None or type(edge[key]) is str)
                require((edge.get(key) or default) in allowed)
            if 'label' in edge: require(type(edge['label']) is str and len(edge['label'])<=1024)
            predecessors[edge['target']].add(edge['source'])
        # Admission only. Fancy's Coordinator alone schedules and executes nodes.
        TopologicalSorter(predecessors).prepare()
        return value,digest,graph
    except (FlowError,InvalidWorkflow,TypeError,ValueError,KeyError,AttributeError,CycleError):
        raise InvalidWorkflow() from None
