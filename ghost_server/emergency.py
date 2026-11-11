"""Manual help requests. No model tool, calls, home actions, or automatic escalation."""
import hashlib
import json
import math
import re
import secrets
import threading
import time
from . import store, access
from .hub import hub

_lock = threading.RLock()
DEFAULT = {'enabled': False, 'test_mode': True, 'initiators': [], 'recipients': [], 'home_address': ''}
SCHEMA = """CREATE TABLE IF NOT EXISTS help_config(id INTEGER PRIMARY KEY,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS help_incidents(
 id TEXT PRIMARY KEY, source TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL,
 source_name TEXT NOT NULL, is_test INTEGER NOT NULL, location TEXT NOT NULL,
 created REAL NOT NULL, expires REAL NOT NULL, cancelled REAL NOT NULL DEFAULT 0,
 UNIQUE(source,request_id));
CREATE TABLE IF NOT EXISTS help_deliveries(
 incident TEXT NOT NULL, recipient TEXT NOT NULL, transport TEXT NOT NULL DEFAULT 'pending',
 received REAL NOT NULL DEFAULT 0, acknowledged REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(incident,recipient));"""


def schema(): store._con().executescript(SCHEMA)


def _prune():
    schema()
    # Location/history are short-lived; backups follow the operator's retention.
    store.x('DELETE FROM help_deliveries WHERE incident IN (SELECT id FROM help_incidents WHERE created<?)',(time.time()-86400,))
    store.x('DELETE FROM help_incidents WHERE created<?',(time.time()-86400,))


def settings():
    schema(); rows=store.q('SELECT data FROM help_config WHERE id=1')
    return json.loads(rows[0]['data']) if rows else {**DEFAULT}


def _device(did, registered=True):
    row=store.device(did)
    if (row is None or row['revoked'] or not row['approved'] or row['kind']!='phone'
            or access.limited(did) or '~' in did):
        raise PermissionError('Help requires an approved, permitted personal phone.')
    if registered and json.loads(row['info'] or '{}').get('help_version')!=1:
        raise ValueError('Update this phone and open its Help screen once before selecting it.')
    return dict(row)


def register(did):
    _device(did,False); store.touch_device(did,info={'help_version':1})
    return {'ok':True}


def configure(body):
    if not isinstance(body,dict) or set(body)!=set(DEFAULT): raise ValueError('Save the complete Help configuration.')
    if type(body['enabled']) is not bool or type(body['test_mode']) is not bool: raise ValueError('Use on/off switches.')
    result={**body}
    for key in ('initiators','recipients'):
        values=body[key]
        if not isinstance(values,list) or len(values)>10 or any(not isinstance(v,str) for v in values) or len(set(values))!=len(values):
            raise ValueError('Choose up to ten distinct phones for each list.')
        for did in values: _device(did)
        result[key]=values[:]
    address=body['home_address']
    if not isinstance(address,str) or len(address)>300 or any(ord(c)<32 for c in address): raise ValueError('Use a one-line saved address, up to 300 characters.')
    result['home_address']=address.strip()
    if result['enabled'] and (not result['initiators'] or not result['recipients']): raise ValueError('Select initiating phones and recipient phones first.')
    if result['enabled'] and any(not any(target!=source for target in result['recipients']) for source in result['initiators']):
        raise ValueError('Every initiating phone needs at least one other recipient phone.')
    with _lock:
        schema(); store.x('INSERT INTO help_config VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(json.dumps(result),))
    return result


def _source(did):
    device=_device(did); cfg=settings()
    if not cfg['enabled'] or did not in cfg['initiators']: raise PermissionError('Help is off or this phone has not been selected to request help.')
    return device,cfg


def clean_location(value):
    if value is None: return {'kind':'unavailable'}
    if not isinstance(value,dict): raise ValueError('Invalid location information.')
    if value=={'kind':'saved_home'}: return value
    if set(value)!={'kind','latitude','longitude','accuracy_m','at'} or value['kind']!='last_known': raise ValueError('Use a labelled phone location or saved home address.')
    for k in ('latitude','longitude','accuracy_m','at'):
        if type(value[k]) not in (int,float) or not math.isfinite(value[k]): raise ValueError('Location values must be finite numbers.')
    if not -90<=value['latitude']<=90 or not -180<=value['longitude']<=180 or not 0<=value['accuracy_m']<=100000:
        raise ValueError('Invalid location coordinates or accuracy.')
    # Never silently promote a cached fix to a current position.
    if not time.time()-86400<=value['at']<=time.time()+60: return {'kind':'unavailable'}
    return {**value}


def start(did,body):
    if not isinstance(body,dict) or set(body)-{'request_id','test','location'}: raise ValueError('Unknown Help request field.')
    request_id=body.get('request_id')
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',request_id): raise ValueError('A stable request ID is required.')
    if type(body.get('test')) is not bool: raise ValueError('Choose test or live request explicitly.')
    location=clean_location(body.get('location'))
    digest=hashlib.sha256(json.dumps(body,sort_keys=True,allow_nan=False).encode()).hexdigest()
    with _lock:
        device,cfg=_source(did); _prune()
        previous=store.q('SELECT id,digest FROM help_incidents WHERE source=? AND request_id=?',(did,request_id))
        if previous:
            if previous[0]['digest']!=digest: raise ValueError('This request ID was already used with different details.')
            return view(previous[0]['id'],did)
        active=store.q('SELECT id FROM help_incidents WHERE source=? AND cancelled=0 AND expires>?',(did,time.time()))
        if active: raise ValueError('This phone already has an active request. Check or cancel it first.')
        if len(store.q('SELECT id FROM help_incidents WHERE created>?',(time.time()-3600,)))>=100: raise ValueError('Help request limit reached; use the phone dialler if help is needed.')
        recipients=[]
        for target in cfg['recipients']:
            if target==did: continue
            try: _device(target);recipients.append(target)
            except (ValueError,PermissionError): pass
        if not recipients: raise ValueError('No other configured recipient phone is available to receive requests.')
        if location['kind']=='saved_home':
            if not cfg['home_address']: raise ValueError('No saved home address has been configured.')
            location={'kind':'saved_home','address':cfg['home_address'],'notice':'User selected saved home address; not GPS.'}
        token='hp_'+secrets.token_hex(12); now=time.time(); is_test=cfg['test_mode'] or body['test']
        store.x('INSERT INTO help_incidents(id,source,request_id,digest,source_name,is_test,location,created,expires) VALUES(?,?,?,?,?,?,?,?,?)',
                (token,did,request_id,digest,device['name'],int(is_test),json.dumps(location),now,now+3600))
        for target in recipients: store.x('INSERT INTO help_deliveries(incident,recipient) VALUES(?,?)',(token,target))
    # Persist before sending. No automatic replay after restart or uncertain sends.
    for target in recipients:
        sent=False
        try:
            current=settings()
            if current['enabled'] and did in current['initiators'] and target in current['recipients']:
                _device(target)
                sent=hub.send_sync(target,_message(token,is_test))
        except Exception: pass
        store.x('UPDATE help_deliveries SET transport=? WHERE incident=? AND recipient=?',('sent_to_connection' if sent else 'not_confirmed',token,target))
    return view(token,did)


def _message(token,is_test,cancelled=False):
    return {'type':'notify','action':'help_request','help_id':token,'test':bool(is_test),'cancelled':cancelled,
            'title':('TEST — ' if is_test else '')+('Help request cancelled' if cancelled else 'Ghost help request'),
            'text':'Open Ghost Help to check the request. No emergency services have been contacted.'}


def _row(token,did=None):
    _prune(); rows=store.q('SELECT * FROM help_incidents WHERE id=?',(token,))
    if not rows: raise ValueError('Help request expired or was removed.')
    row=dict(rows[0])
    if did is not None:
        _device(did);cfg=settings()
        own=row['source']==did and did in cfg['initiators']
        recipient=did in cfg['recipients'] and store.q('SELECT 1 FROM help_deliveries WHERE incident=? AND recipient=?',(token,did))
        if not own and not recipient: raise PermissionError('This Help request is not available to this phone.')
    return row


def view(token,did=None):
    row=_row(token,did)
    deliveries=[]
    for d in store.q('SELECT * FROM help_deliveries WHERE incident=?',(token,)):
        device=store.device(d['recipient'])
        deliveries.append({**dict(d),'name':device['name'] if device else 'Removed phone'})
    return {'id':row['id'],'source':row['source'],'source_name':row['source_name'],'test':bool(row['is_test']),
            'location':json.loads(row['location']),'created':row['created'],'expires':row['expires'],
            'cancelled':row['cancelled'],'expired':row['expires']<=time.time(),'deliveries':deliveries,
            'emergency_services_contacted':False}


def inbox(did):
    _device(did); _prune(); cfg=settings()
    ids=store.q("SELECT DISTINCT i.id FROM help_incidents i LEFT JOIN help_deliveries d ON d.incident=i.id WHERE i.source=? OR d.recipient=? ORDER BY CASE WHEN i.cancelled=0 AND i.expires>? THEN 0 ELSE 1 END,i.created DESC LIMIT 30",(did,did,time.time()))
    result=[]
    for row in ids:
        try: result.append(view(row['id'],did))
        except PermissionError: pass
    return {'items':result,'enabled':cfg['enabled'],'can_start':cfg['enabled'] and did in cfg['initiators'],
            'test_mode':cfg['test_mode'],'has_home_address':bool(cfg['home_address'])}


def mark(token,did,action):
    if action not in ('received','acknowledge','cancel'): raise ValueError('Unsupported Help action.')
    with _lock:
        row=_row(token,did)
        if row['expires']<=time.time(): raise ValueError('This request has expired.')
        if action=='cancel':
            if did!=row['source']: raise PermissionError('Only the initiating phone can cancel this request.')
            if row['cancelled']: return view(token,did)
            store.x('UPDATE help_incidents SET cancelled=? WHERE id=?',(time.time(),token))
        else:
            if row['cancelled']: raise ValueError('This request was cancelled.')
            if not store.q('SELECT 1 FROM help_deliveries WHERE incident=? AND recipient=?',(token,did)): raise PermissionError('Only a selected recipient can acknowledge.')
            field='received' if action=='received' else 'acknowledged'
            store.x(f'UPDATE help_deliveries SET {field}=CASE WHEN {field}=0 THEN ? ELSE {field} END WHERE incident=? AND recipient=?',(time.time(),token,did))
    if action=='cancel':
        for d in store.q('SELECT recipient FROM help_deliveries WHERE incident=?',(token,)):
            try:
                if d['recipient'] in settings()['recipients']:
                    _device(d['recipient']);hub.send_sync(d['recipient'],_message(token,row['is_test'],True))
            except Exception: pass
    return view(token,did)


def admin_snapshot():
    _prune()
    return {'settings':settings(),'devices':[{'id':d['id'],'name':d['name'],'ready':d.get('info',{}).get('help_version')==1,'approved':bool(d['approved'])} for d in store.devices() if d['kind']=='phone' and not access.limited(d['id'])],
            'items':[view(r['id']) for r in store.q('SELECT id FROM help_incidents ORDER BY CASE WHEN cancelled=0 AND expires>? THEN 0 ELSE 1 END,created DESC LIMIT 30',(time.time(),))]}
