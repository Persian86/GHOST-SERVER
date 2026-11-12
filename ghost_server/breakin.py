"""Manual, allow-listed break-in response. No LLM tool or emergency calls.

Persist before dispatch; never replay ON commands or notifications after restart.
Only duration-capable Home Assistant sirens are accepted in live plans.
"""
import asyncio
import hashlib
import json
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from . import store, access
from .hub import hub
from .tools import home

DEFAULT={'enabled':False,'test_mode':True,'initiators':[],'recipients':[],
         'lights':[],'sirens':[],'duration_s':120,'home_address':''}
_lock=threading.RLock()
_home_lock=threading.Lock()
_executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='GhostBreakIn')
SCHEMA="""CREATE TABLE IF NOT EXISTS breakin_config(id INTEGER PRIMARY KEY,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS breakin_plans(token TEXT PRIMARY KEY,source TEXT NOT NULL,request_id TEXT NOT NULL,payload TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS breakin_incidents(id TEXT PRIMARY KEY,source TEXT NOT NULL,request_id TEXT NOT NULL,digest TEXT NOT NULL,payload TEXT NOT NULL,
 created REAL NOT NULL,deadline REAL NOT NULL,expires REAL NOT NULL,status TEXT NOT NULL,stop_requested INTEGER NOT NULL DEFAULT 0,UNIQUE(source,request_id));
CREATE TABLE IF NOT EXISTS breakin_actions(incident TEXT NOT NULL,entity TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',stop_state TEXT NOT NULL DEFAULT '',PRIMARY KEY(incident,entity));
CREATE TABLE IF NOT EXISTS breakin_deliveries(incident TEXT NOT NULL,recipient TEXT NOT NULL,transport TEXT NOT NULL DEFAULT 'pending',
 received REAL NOT NULL DEFAULT 0,acknowledged REAL NOT NULL DEFAULT 0,PRIMARY KEY(incident,recipient));"""

def schema():store._con().executescript(SCHEMA)
def settings():
    schema();rows=store.q('SELECT data FROM breakin_config WHERE id=1')
    return json.loads(rows[0]['data']) if rows else {**DEFAULT}
def _hash(value):return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()
def _connection():return _hash([str(store.get('ha_url') or '').rstrip('/'),str(store.get('ha_token') or '')])
def _revision():return _hash([settings(),_connection()])
def _enqueue(fn,*args):_executor.submit(fn,*args)

def _phone(did,registered=True):
    row=store.device(did)
    if row is None or row['revoked'] or not row['approved'] or row['kind']!='phone' or '~' in did or access.limited(did):
        raise PermissionError('Use an approved, permitted personal phone.')
    if registered and json.loads(row['info'] or '{}').get('breakin_version')!=1:
        raise ValueError('Update this phone and open Break-in mode once first.')
    return dict(row)

def register(did):
    _phone(did,False);store.touch_device(did,info={'breakin_version':1});return {'ok':True}

def _prune():
    schema();now=time.time();store.x('DELETE FROM breakin_plans WHERE expires<?',(now,))
    for table in ('breakin_actions','breakin_deliveries'):
        store.x(f'DELETE FROM {table} WHERE incident IN (SELECT id FROM breakin_incidents WHERE created<?)',(now-86400,))
    store.x('DELETE FROM breakin_incidents WHERE created<?',(now-86400,))

def configure(body):
    if not isinstance(body,dict) or set(body)!=set(DEFAULT):raise ValueError('Save the complete Break-in configuration.')
    if type(body['enabled']) is not bool or type(body['test_mode']) is not bool:raise ValueError('Choose on/off switches.')
    if type(body['duration_s']) is not int or not 30<=body['duration_s']<=600:raise ValueError('Siren time limit must be 30–600 seconds.')
    cfg={**body}
    for key in ('initiators','recipients','lights','sirens'):
        values=cfg[key]
        if not isinstance(values,list) or len(values)>10 or any(not isinstance(v,str) for v in values) or len(set(values))!=len(values):
            raise ValueError('Choose up to ten distinct values per list.')
        for value in values:
            if key in ('initiators','recipients'):_phone(value)
            elif not re.fullmatch(('light' if key=='lights' else 'siren')+r'\.[a-z0-9_]{1,120}',value):
                raise ValueError('Only exact light.* or siren.* entity IDs are supported.')
        cfg[key]=values[:]
    address=cfg['home_address']
    if not isinstance(address,str) or len(address)>300 or any(ord(c)<32 for c in address):raise ValueError('Use a one-line saved address, up to 300 characters.')
    cfg['home_address']=address.strip()
    if cfg['enabled']:
        if not cfg['initiators'] or not cfg['recipients'] or any(not any(r!=s for r in cfg['recipients']) for s in cfg['initiators']):
            raise ValueError('Every controller needs at least one other selected recipient phone.')
        if (cfg['lights'] or cfg['sirens']) and (not store.get('ha_url') or not store.get('ha_token')):
            raise ValueError('Configure Home Assistant first, or choose family alerts only.')
    with _lock:
        schema();store.x('INSERT INTO breakin_config VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(json.dumps(cfg),))
        active=[r['id'] for r in store.q("SELECT id FROM breakin_incidents WHERE status NOT IN ('stopped','test_stopped','not_started') AND expires>?",(time.time(),))]
        store.x('DELETE FROM breakin_plans')
    # An explicit configuration save closes active requests; never start old targets
    # under a newly edited profile or Home Assistant connection.
    for token in active:stop(token)
    return cfg

def _controller(did):
    phone=_phone(did);cfg=settings()
    if did not in cfg['initiators']:raise PermissionError('This phone is not a Break-in controller.')
    return phone,cfg

def _targets(payload):
    ids=payload['lights']+payload['sirens']
    if not ids:return []
    try:
        response=home._req('GET','/api/states');response.raise_for_status();states=response.json()
        if not isinstance(states,list):raise ValueError()
    except Exception:raise ValueError('Home Assistant target verification is unavailable.') from None
    by={s.get('entity_id'):s for s in states if isinstance(s,dict)};result=[]
    for entity in ids:
        state=by.get(entity,{})
        ready=state.get('state') not in (None,'unavailable','unknown')
        reason='' if ready else 'Unavailable or not found'
        if entity.startswith('siren.'):
            attrs=state.get('attributes') or {}
            features=attrs.get('supported_features',0) if isinstance(attrs,dict) else 0
            ready=ready and type(features) is int and features & 19==19
            if not ready:reason='Siren must support turn on, turn off and duration'
        result.append({'entity':entity,'ready':bool(ready),'reason':reason})
    return result

def check_targets():return {'items':_targets(settings())}

def prepare(did,body):
    if not isinstance(body,dict) or set(body)!={'request_id','test','share_home'}:raise ValueError('Unknown Break-in plan field.')
    rid=body['request_id']
    if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',rid):raise ValueError('A stable request ID is required.')
    if type(body['test']) is not bool or type(body['share_home']) is not bool:raise ValueError('Choose test mode and address sharing explicitly.')
    with _lock:
        phone,cfg=_controller(did);_prune()
        if not cfg['enabled']:raise PermissionError('Break-in mode is disabled.')
        if store.q("SELECT id FROM breakin_incidents WHERE status NOT IN ('stopped','test_stopped','not_started') AND expires>?",(time.time(),)):
            raise ValueError('A household Break-in request already exists. Refresh its status first.')
        if body['share_home'] and not cfg['home_address']:raise ValueError('No saved home address is configured.')
        recipients=[]
        for target in cfg['recipients']:
            if target==did:continue
            try:_phone(target);recipients.append(target)
            except (PermissionError,ValueError):pass
        if not recipients:raise ValueError('No eligible family recipients are configured.')
        if len(store.q('SELECT token FROM breakin_plans WHERE source=?',(did,)))>=10:raise ValueError('Too many pending plans. Wait two minutes and refresh.')
        payload={**cfg,'source_name':phone['name'],'recipients':recipients,'test':cfg['test_mode'] or body['test'],
                 'revision':_revision(),'connection':_connection(),
                 'location':{'kind':'saved_home','address':cfg['home_address'],'notice':'User selected saved home address; not GPS.'} if body['share_home'] else {'kind':'unavailable'}}
        # Remove the unshared address from the incident snapshot.
        payload.pop('home_address',None)
    if not payload['test']:
        checks=_targets(payload)
        if any(not v['ready'] for v in checks):raise ValueError('Check Home Assistant targets: '+', '.join(v['entity'] for v in checks if not v['ready']))
    token=secrets.token_urlsafe(24);expires=time.time()+120
    with _lock:
        _controller(did)
        if payload['revision']!=_revision():raise ValueError('Setup changed. Review a fresh plan.')
        store.x('INSERT INTO breakin_plans VALUES(?,?,?,?,?)',(_hash(token),did,rid,json.dumps(payload),expires))
    return {'request_id':rid,'plan_token':token,'expires':expires,'test':payload['test'],
            'lights':cfg['lights'],'sirens':cfg['sirens'],'duration_s':cfg['duration_s'],
            'recipients':[store.device(v)['name'] for v in recipients],'location':payload['location']}

def start(did,body):
    if not isinstance(body,dict) or set(body)!={'request_id','plan_token'}:raise ValueError('Confirm an exact prepared plan.')
    if not all(isinstance(body[k],str) and 16<=len(body[k])<=100 for k in body):raise ValueError('Invalid prepared plan.')
    with _lock:
        _controller(did);schema();digest=_hash(body)
        previous=store.q('SELECT id,digest FROM breakin_incidents WHERE source=? AND request_id=?',(did,body['request_id']))
        if previous:
            if previous[0]['digest']!=digest:raise ValueError('This request ID was already used with different details.')
            return view(previous[0]['id'],did)
        plans=store.q('SELECT * FROM breakin_plans WHERE token=? AND source=? AND request_id=?',(_hash(body['plan_token']),did,body['request_id']))
        if not plans or plans[0]['expires']<time.time():raise ValueError('The plan expired. Review a fresh plan before activating.')
        payload=json.loads(plans[0]['payload'])
        if not settings()['enabled'] or payload['revision']!=_revision():raise ValueError('Setup changed. Review a fresh plan.')
        if store.q("SELECT id FROM breakin_incidents WHERE status NOT IN ('stopped','test_stopped','not_started') AND expires>?",(time.time(),)):
            raise ValueError('A household Break-in request already exists. Refresh its status first.')
        if len(store.q('SELECT id FROM breakin_incidents WHERE created>?',(time.time()-3600,)))>=20:raise ValueError('Break-in request limit reached.')
        token='bi_'+secrets.token_hex(12);now=time.time()
        store.x('INSERT INTO breakin_incidents VALUES(?,?,?,?,?,?,?,?,?,0)',(token,did,body['request_id'],digest,json.dumps(payload),now,now+payload['duration_s'],now+3600,'queued'))
        for entity in payload['lights']+payload['sirens']:store.x('INSERT INTO breakin_actions(incident,entity) VALUES(?,?)',(token,entity))
        for recipient in payload['recipients']:store.x('INSERT INTO breakin_deliveries(incident,recipient) VALUES(?,?)',(token,recipient))
        store.x('DELETE FROM breakin_plans WHERE token=?',(_hash(body['plan_token']),))
    _enqueue(_run,token)
    return view(token,did)

def _row(token,did=None):
    schema();rows=store.q('SELECT * FROM breakin_incidents WHERE id=?',(token,))
    if not rows:raise ValueError('Request expired or was removed.')
    row=dict(rows[0])
    if did is not None:
        _phone(did);cfg=settings()
        recipient=did in cfg['recipients'] and store.q('SELECT 1 FROM breakin_deliveries WHERE incident=? AND recipient=?',(token,did))
        if did not in cfg['initiators'] and not recipient:raise PermissionError('This request is not available to this phone.')
    return row

def view(token,did=None):
    row=_row(token,did);payload=json.loads(row.pop('payload'))
    return {'id':row['id'],'source_name':payload['source_name'],'created':row['created'],'expires':row['expires'],
            'deadline':row['deadline'],'expired':row['expires']<=time.time(),'status':row['status'],
            'test':payload['test'],'location':payload['location'],'can_stop':did is None or did in settings()['initiators'],
            'actions':[dict(a) for a in store.q('SELECT entity,state,stop_state FROM breakin_actions WHERE incident=?',(token,))],
            'deliveries':[{**dict(d),'name':(store.device(d['recipient']) or {'name':'Removed phone'})['name']} for d in store.q('SELECT * FROM breakin_deliveries WHERE incident=?',(token,))],
            'emergency_services_contacted':False}

def inbox(did):
    _phone(did);_prune();cfg=settings();items=[]
    for row in store.q('SELECT id FROM breakin_incidents ORDER BY created DESC LIMIT 30'):
        try:items.append(view(row['id'],did))
        except PermissionError:pass
    active=bool(store.q("SELECT id FROM breakin_incidents WHERE status NOT IN ('stopped','test_stopped','not_started') AND expires>?",(time.time(),)))
    return {'enabled':cfg['enabled'],'can_start':cfg['enabled'] and did in cfg['initiators'] and not active,'active_request':active,
            'test_mode':cfg['test_mode'],'has_home_address':bool(cfg['home_address']),'items':items}

def _notify(token,closed=False):
    row=_row(token);payload=json.loads(row['payload'])
    for delivery in store.q('SELECT * FROM breakin_deliveries WHERE incident=?',(token,)):
        target=delivery['recipient'];sent=False
        if not closed and delivery['transport']!='pending':continue
        try:
            if not closed:
                row=_row(token)
                _,cfg=_controller(row['source'])
                if row['stop_requested'] or not cfg['enabled'] or payload['revision']!=_revision() or time.time()>=row['deadline']:continue
            if target not in settings()['recipients']:continue
            _phone(target)
            sent=hub.send_sync(target,{'type':'notify','action':'breakin_request','breakin_id':token,
                                     'title':'Ghost safety alert','text':'Open Ghost to review this request.'})
        except Exception:pass
        if not closed:store.x('UPDATE breakin_deliveries SET transport=? WHERE incident=? AND recipient=?',('sent_to_connection' if sent else 'not_confirmed',token,target))

def _service(domain,action,entities,**extra):
    try:
        response=home._req('POST','/api/services/'+domain+'/'+action,json={'entity_id':entities,**extra})
        return 'accepted' if 200<=response.status_code<300 else 'failed'
    except Exception:return 'unconfirmed'

def _run(token):
    # A worker handles only this newly persisted activation. It is never replayed.
    with _lock:
        row=_row(token)
        if row['status']!='queued' or row['stop_requested']:return
        payload=json.loads(row['payload'])
        eligible=[]
        for target in payload['recipients']:
            try:_phone(target);eligible.append(target)
            except (PermissionError,ValueError):pass
        try:
            _,cfg=_controller(row['source'])
            allowed=cfg['enabled'] and payload['revision']==_revision() and time.time()<row['deadline'] and bool(eligible)
        except (PermissionError,ValueError):allowed=False
        if not allowed:
            store.x("UPDATE breakin_incidents SET status='not_started',stop_requested=1 WHERE id=?",(token,))
            store.x("UPDATE breakin_actions SET state='not_sent' WHERE incident=?",(token,));return
        store.x("UPDATE breakin_incidents SET status='executing' WHERE id=?",(token,))
    # A slow/offline family connection must not delay the home response.
    _enqueue(_notify,token)
    for domain,key in [('light','lights'),('siren','sirens')]:
        entities=payload[key]
        if not entities:continue
        with _home_lock:
            with _lock:
                row=_row(token)
                allowed=not row['stop_requested'] and time.time()<row['deadline']
                try:
                    _,cfg=_controller(row['source'])
                    allowed=allowed and cfg['enabled'] and payload['revision']==_revision()
                except (PermissionError,ValueError):allowed=False
                if not allowed:
                    store.x("UPDATE breakin_actions SET state='not_sent' WHERE incident=? AND state='pending'",(token,));break
            if payload['test']:result='simulated'
            else:
                try:
                    if payload['connection']!=_connection() or any(not v['ready'] for v in _targets(payload)):raise ValueError()
                    with _lock:
                        fresh=_row(token);_controller(fresh['source'])
                        if fresh['stop_requested'] or time.time()>=fresh['deadline'] or payload['revision']!=_revision():raise ValueError()
                        for entity in entities:store.x("UPDATE breakin_actions SET state='attempting' WHERE incident=? AND entity=?",(token,entity))
                    remaining=max(1,int(row['deadline']-time.time()))
                    result=_service(domain,'turn_on',entities,**({'duration':remaining} if domain=='siren' else {}))
                except Exception:result='not_sent'
            with _lock:
                for entity in entities:store.x('UPDATE breakin_actions SET state=? WHERE incident=? AND entity=?',(result,token,entity))
    with _lock:
        row=_row(token)
        if not row['stop_requested']:store.x('UPDATE breakin_incidents SET status=? WHERE id=?',('test_active' if payload['test'] else 'active',token))

def stop(token,did=None):
    with _lock:
        row=_row(token,did)
        if did is not None:_controller(did)
        if row['status'] in ('stopped','test_stopped','stopping'):return view(token,did)
        store.x("UPDATE breakin_incidents SET stop_requested=1,status='stopping' WHERE id=?",(token,))
    _enqueue(_stop,token)
    return view(token,did)

def _stop(token):
    with _home_lock:
        row=_row(token);payload=json.loads(row['payload'])
        sirens=[a['entity'] for a in store.q("SELECT entity FROM breakin_actions WHERE incident=? AND state IN ('attempting','accepted','unconfirmed','simulated')",(token,)) if a['entity'].startswith('siren.')]
        if payload['test']:result='simulated_stop'
        elif not sirens:result='not_needed'
        elif payload['connection']!=_connection():result='connection_changed'
        else:result=_service('siren','turn_off',sirens)
        with _lock:
            for entity in sirens:store.x('UPDATE breakin_actions SET stop_state=? WHERE incident=? AND entity=?',(result,token,entity))
            store.x("UPDATE breakin_actions SET state='not_sent' WHERE incident=? AND state='pending'",(token,))
            status='test_stopped' if payload['test'] else 'stopped' if result in ('accepted','not_needed') else 'stop_unconfirmed'
            store.x('UPDATE breakin_incidents SET status=? WHERE id=?',(status,token))
    _notify(token,closed=True)

def mark(token,did,action):
    if action=='stop':return stop(token,did)
    if action not in ('received','acknowledge'):raise ValueError('Unsupported Break-in action.')
    with _lock:
        row=_row(token,did)
        if row['expires']<time.time():raise ValueError('This request expired.')
        if not store.q('SELECT 1 FROM breakin_deliveries WHERE incident=? AND recipient=?',(token,did)):raise PermissionError('Only a selected recipient can acknowledge.')
        field='received' if action=='received' else 'acknowledged'
        store.x(f'UPDATE breakin_deliveries SET {field}=CASE WHEN {field}=0 THEN ? ELSE {field} END WHERE incident=? AND recipient=?',(time.time(),token,did))
    return view(token,did)

def recover():
    with _lock:
        schema()
        store.x("UPDATE breakin_actions SET state='unconfirmed' WHERE state='attempting'")
        store.x("UPDATE breakin_actions SET state='not_sent' WHERE state='pending'")
        store.x("UPDATE breakin_incidents SET status='activation_unconfirmed' WHERE status IN ('queued','executing')")
        # Restart during a stop must make the deadline stop eligible again.
        store.x("UPDATE breakin_incidents SET status='stop_unconfirmed',stop_requested=0 WHERE status='stopping'")

def tick():
    schema()
    for row in store.q("SELECT id FROM breakin_incidents WHERE deadline<=? AND stop_requested=0 AND status NOT IN ('stopped','test_stopped','not_started')",(time.time(),)):
        detail=_row(row['id'])
        if time.time()>detail['deadline']+60:
            # A much later restart must not switch off a siren subsequently used
            # for another purpose. Hardware duration remains the first bound.
            store.x("UPDATE breakin_incidents SET status='deadline_missed',stop_requested=1 WHERE id=?",(row['id'],))
        else:stop(row['id'])

async def deadline_loop():
    while True:
        try:await asyncio.to_thread(tick)
        except Exception:pass
        await asyncio.sleep(2)

def admin_snapshot():
    _prune()
    return {'settings':settings(),'devices':[{'id':d['id'],'name':d['name'],'ready':d.get('info',{}).get('breakin_version')==1,'approved':bool(d['approved'])} for d in store.devices() if d['kind']=='phone' and not access.limited(d['id'])],
            'items':[view(r['id']) for r in store.q('SELECT id FROM breakin_incidents ORDER BY created DESC LIMIT 30')],
            'home_assistant_configured':bool(store.get('ha_url') and store.get('ha_token'))}
