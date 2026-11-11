import sys,time,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from ghost_server import store,faces,household,meals,security,access,emergency,tools
from ghost_server.app import app


@pytest.fixture
def env(tmp_path,monkeypatch):
    for module in (faces,household,meals):monkeypatch.setattr(module,'_ready',False)
    store.init(tmp_path/'help.db');security.reset_all();security.end_all();store.put('approve_new_devices',False)
    def pair(name,kind='phone'):
        did,key=store.redeem_pair_code(store.new_pair_code(),name,kind)
        if kind=='phone':emergency.register(did)
        return did,{'Authorization':'Bearer '+key}
    a,ah=pair('Alex phone');b,bh=pair('Family <phone>');c,ch=pair('Unselected phone');t,th=pair('Shared tablet','tablet')
    sent=[];monkeypatch.setattr(emergency.hub,'send_sync',lambda did,msg:sent.append((did,msg)) or True)
    cfg={**emergency.DEFAULT,'enabled':True,'initiators':[a],'recipients':[b],'home_address':'123 Saved St'}
    emergency.configure(cfg)
    client=TestClient(app);client.headers['X-Ghost-Admin']='1';client.post('/admin/login',json={'password':'owner-pass'})
    yield client,a,ah,b,bh,c,ch,t,th,cfg,sent
    security.end_all()


def body(**extra):return {'request_id':'request_id_12345678','test':False,**extra}


def test_off_by_default_and_exact_registered_phones(env):
    client,a,ah,b,bh,c,ch,t,th,cfg,sent=env
    assert emergency.DEFAULT['enabled'] is False and emergency.DEFAULT['test_mode'] is True
    with pytest.raises(PermissionError):emergency.register(t)
    with pytest.raises(PermissionError):emergency.start(c,body())
    emergency.configure({**cfg,'enabled':False})
    with pytest.raises(PermissionError):emergency.start(a,body())
    assert sent==[]
    with pytest.raises(ValueError):emergency.configure({**cfg,'recipients':[a]})


def test_snapshot_dedup_and_distinct_receipt_and_ack(env):
    client,a,ah,b,bh,c,ch,t,th,cfg,sent=env
    result=emergency.start(a,body())
    assert result['test'] and result['location']=={'kind':'unavailable'}
    assert result['emergency_services_contacted'] is False
    assert result['deliveries'][0]['transport']=='sent_to_connection'
    assert not result['deliveries'][0]['received'] and not result['deliveries'][0]['acknowledged']
    assert emergency.start(a,body())['id']==result['id'] and len(sent)==1
    assert 'location' not in sent[0][1] and 'Alex' not in sent[0][1]['text']
    received=emergency.mark(result['id'],b,'received')
    assert received['deliveries'][0]['received'] and not received['deliveries'][0]['acknowledged']
    ack=emergency.mark(result['id'],b,'acknowledge')
    stamp=ack['deliveries'][0]['acknowledged']
    assert emergency.mark(result['id'],b,'acknowledge')['deliveries'][0]['acknowledged']==stamp


def test_source_and_recipient_binding(env):
    client,a,ah,b,bh,c,ch,t,th,cfg,sent=env;r=emergency.start(a,body())
    for action in ('received','acknowledge','cancel'):
        with pytest.raises(PermissionError):emergency.mark(r['id'],c,action)
    with pytest.raises(PermissionError):emergency.mark(r['id'],b,'cancel')
    with pytest.raises(PermissionError):emergency.mark(r['id'],a,'acknowledge')
    assert emergency.inbox(c)['items']==[]
    with pytest.raises(PermissionError):emergency.view(r['id'],c)


def test_cancel_is_idempotent_and_not_undo_or_resend(env):
    client,a,ah,b,bh,c,ch,t,th,cfg,sent=env;r=emergency.start(a,body())
    cancelled=emergency.mark(r['id'],a,'cancel');assert cancelled['cancelled'] and len(sent)==2
    emergency.mark(r['id'],a,'cancel');assert len(sent)==2
    assert emergency.start(a,body())['cancelled'] and len(sent)==2
    with pytest.raises(ValueError):emergency.mark(r['id'],b,'acknowledge')


def test_live_requires_owner_switch_and_explicit_client_mode(env):
    client,a,ah,b,bh,c,ch,t,th,cfg,sent=env
    emergency.configure({**cfg,'test_mode':False})
    r=emergency.start(a,body(test=True));assert r['test'];emergency.mark(r['id'],a,'cancel')
    r=emergency.start(a,body(request_id='different_id_123456',test=False));assert not r['test']


def test_location_not_substituted_and_stale_is_unknown(env):
    client,a,ah,b,bh,c,ch,t,th,cfg,sent=env
    loc={'kind':'last_known','latitude':-33.8,'longitude':151.2,'accuracy_m':45,'at':time.time()-600}
    r=emergency.start(a,body(location=loc));assert r['location']==loc
    emergency.mark(r['id'],a,'cancel')
    r=emergency.start(a,body(request_id='second_request_1234',location={**loc,'at':time.time()-90000}))
    assert r['location']=={'kind':'unavailable'}
    emergency.mark(r['id'],a,'cancel')
    r=emergency.start(a,body(request_id='home_request_123456',location={'kind':'saved_home'}))
    assert r['location']['address']=='123 Saved St' and 'not GPS' in r['location']['notice']


@pytest.mark.parametrize('loc',[{'kind':'gps'}, {'kind':'last_known','latitude':float('nan'),'longitude':1,'accuracy_m':1,'at':1}, {'kind':'last_known','latitude':91,'longitude':1,'accuracy_m':1,'at':time.time()}])
def test_invalid_location_rejected_before_send(env,loc):
    with pytest.raises(ValueError):emergency.start(env[1],body(location=loc))
    assert env[-1]==[]


def test_rechecks_revocation_policy_and_recipient_removal(env):
    client,a,ah,b,bh,c,ch,t,th,cfg,sent=env;r=emergency.start(a,body())
    emergency.configure({**cfg,'recipients':[c]})
    with pytest.raises(PermissionError):emergency.view(r['id'],b)
    assert emergency.inbox(c)['items']==[]
    store.x('UPDATE devices SET revoked=1 WHERE id=?',(a,))
    with pytest.raises(PermissionError):emergency.start(a,body())


def test_permissions_and_request_validation(env):
    client,a,ah,b,bh,c,ch,t,th,cfg,sent=env
    store.put('household_access',True);access.set_role(b,'guest')
    with pytest.raises(PermissionError):emergency.configure(cfg)
    access.set_role(b,'adult')
    for value in ({'request_id':'short','test':True},body(test='true'),body(extra='bad')):
        with pytest.raises(ValueError):emergency.start(a,value)
    emergency.start(a,body())
    with pytest.raises(ValueError):emergency.start(a,body(test=True))
    with pytest.raises(ValueError):emergency.start(a,body(request_id='another_request_1234'))


def test_offline_transport_and_expiry_do_not_claim_delivery_or_replay(env,monkeypatch):
    client,a,ah,b,bh,c,ch,t,th,cfg,sent=env
    monkeypatch.setattr(emergency.hub,'send_sync',lambda *args:False)
    r=emergency.start(a,body());assert r['deliveries'][0]['transport']=='not_confirmed'
    store.x('UPDATE help_incidents SET expires=? WHERE id=?',(time.time()-1,r['id']))
    assert emergency.view(r['id'],a)['expired']
    with pytest.raises(ValueError):emergency.mark(r['id'],b,'acknowledge')
    assert emergency.start(a,body())['id']==r['id']
    store.x('UPDATE help_incidents SET created=? WHERE id=?',(time.time()-90000,r['id']))
    assert emergency.inbox(a)['items']==[] and store.q('SELECT * FROM help_deliveries')==[]


def test_http_auth_csrf_and_no_model_tool(env):
    client,a,ah,b,bh,c,ch,t,th,cfg,sent=env
    anon=TestClient(app)
    assert anon.get('/api/v1/help').status_code==401
    assert anon.put('/admin/api/help',json=cfg).status_code==401
    client.headers.pop('X-Ghost-Admin')
    assert client.put('/admin/api/help',json=cfg).status_code==403
    client.headers['X-Ghost-Admin']='1'
    assert client.put('/admin/api/help',json={**cfg,'enabled':'true'}).status_code==400
    result=anon.post('/api/v1/help',headers=ah,json=body()).json()
    assert result['test']
    assert anon.post('/api/v1/help/'+result['id']+'/acknowledge',headers=bh,json={}).status_code==200
    assert anon.post('/api/v1/help/'+result['id']+'/cancel',headers=ch,json={}).status_code==403
    assert not any('emergency' in t['name'] or t['name']=='help' for t in tools.declarations())


def test_active_requests_remain_visible_ahead_of_recent_closed_history(env):
    client,a,ah,b,bh,c,ch,t,th,cfg,sent=env
    first=emergency.start(a,body())
    # Simulate retained, newer closed history without sending any notifications.
    for i in range(32):
        store.x('INSERT INTO help_incidents(id,source,request_id,digest,source_name,is_test,location,created,expires,cancelled) VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('old_'+str(i),a,'old_request_'+str(i),'digest','Old',1,'{}',time.time()+i,0,time.time()))
        store.x('INSERT INTO help_deliveries(incident,recipient) VALUES(?,?)',('old_'+str(i),b))
    assert emergency.inbox(b)['items'][0]['id']==first['id']
    assert emergency.admin_snapshot()['items'][0]['id']==first['id']
