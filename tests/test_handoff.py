import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from ghost_server import store, faces, personal, access, handoff, tools


@pytest.fixture
def devices(tmp_path,monkeypatch):
    monkeypatch.setattr(faces,'_ready',False)
    store.init(tmp_path/'handoff.db');store.put('approve_new_devices',False)
    def pair(name,kind='desktop'):
        return store.redeem_pair_code(store.new_pair_code(),name,kind)[0]
    a,b,c,t=pair('PC'),pair('Phone','phone'),pair('Other'),pair('Tablet','tablet')
    p=personal.create('Alex')['id'];q=personal.create('Sam')['id']
    for d in (a,b,t):personal.assign(d,p)
    personal.assign(c,q);store.put('handoff_enabled',True)
    return a,b,c,t,p


def test_explicit_single_use_accept_without_history_copy_or_push(devices):
    a,b,c,t,p=devices
    r=handoff.offer(a,b,'We were choosing paint colours.')
    assert r['delivered'] is False
    assert len(handoff.pending(b))==1 and 'recap' not in str(handoff.pending(b))
    assert store.recent_turns(20,b)==[]
    result=handoff.accept(b,r['id'])
    assert result['context_data']=='We were choosing paint colours.'
    assert 'not instructions' in result['notice']
    with pytest.raises(ValueError):handoff.accept(b,r['id'])
    assert handoff.pending(a)==[]


def test_same_person_only_and_shared_devices_excluded(devices):
    a,b,c,t,p=devices
    assert [d['id'] for d in handoff.targets(a)]==[b]
    for target in (c,t,a):
        with pytest.raises((ValueError,PermissionError)):handoff.offer(a,target,'Test')
    personal.assign(b,None)
    with pytest.raises(PermissionError):handoff.offer(a,b,'Test')


def test_destination_binding_and_source_cancellation(devices):
    a,b,c,t,p=devices;r=handoff.offer(a,b,'Test')
    with pytest.raises(ValueError):handoff.accept(c,r['id'])
    handoff.cancel(c,r['id']);assert len(handoff.pending(b))==1
    handoff.cancel(a,r['id']);assert handoff.pending(b)==[]


def test_expiry_and_replace(devices,monkeypatch):
    a,b,c,t,p=devices;now=handoff.time.time()
    first=handoff.offer(a,b,'First');second=handoff.offer(a,b,'Second')
    with pytest.raises(ValueError):handoff.accept(b,first['id'])
    monkeypatch.setattr(handoff.time,'time',lambda:now+601)
    assert handoff.pending(b)==[]
    with pytest.raises(ValueError):handoff.accept(b,second['id'])


@pytest.mark.parametrize('change',['assignment','revoked','unapproved','role','deleted'])
def test_rechecks_access_before_accept(devices,change):
    a,b,c,t,p=devices;r=handoff.offer(a,b,'Private recap')
    if change=='assignment':personal.assign(a,personal.create('New')['id'])
    elif change=='revoked':store.x('UPDATE devices SET revoked=1 WHERE id=?',(a,))
    elif change=='unapproved':store.x('UPDATE devices SET approved=0 WHERE id=?',(b,))
    elif change=='role':store.put('household_access',True);access.set_role(a,'guest')
    else:personal.remove(p)
    with pytest.raises(PermissionError):handoff.accept(b,r['id'])


def test_opt_in_and_clear(devices):
    a,b,c,t,p=devices;handoff.offer(a,b,'Private recap')
    store.put('handoff_enabled',False)
    with pytest.raises(PermissionError):handoff.pending(b)
    handoff.clear();store.put('handoff_enabled',True)
    assert handoff.pending(b)==[]


def test_validation_and_no_background_execution(devices):
    a,b,c,t,p=devices
    for recap in ('','x'*2001,None,'bad\x00text'):
        with pytest.raises(ValueError):handoff.offer(a,b,recap)
    result=tools.run('conversation_handoff',{'action':'offer','target_id':b,'recap':'Test'},tools.Ctx(a,'PC','task'))
    assert 'failed' in result and handoff.pending(b)==[]
    result=json.loads(tools.run('conversation_handoff',{'action':'offer','target_id':b,'recap':'Test'},tools.Ctx(a,'PC','chat')))
    assert result['expires_in']==600
    assert 'conversation_handoff' not in [x['name'] for x in tools.declarations('task')]


def test_admin_setting_requires_login_and_csrf_and_clears_offers(devices):
    from fastapi.testclient import TestClient
    from ghost_server import security
    from ghost_server.app import app
    a,b,c,t,p=devices
    security.reset_all();security.end_all()
    client=TestClient(app)
    assert client.put('/admin/api/settings',json={'handoff_enabled':True}).status_code in (401,403)
    client.headers['X-Ghost-Admin']='1'
    assert client.post('/admin/login',json={'password':'owner-pass'}).status_code==200
    assert client.put('/admin/api/settings',json={'handoff_enabled':'true'}).status_code==400
    handoff.offer(a,b,'Test')
    assert client.put('/admin/api/settings',json={'handoff_enabled':False}).status_code==200
    assert store.get('handoff_enabled') is False
    assert store.q('SELECT * FROM conversation_handoffs')==[]
    client.headers.pop('X-Ghost-Admin')
    assert client.put('/admin/api/settings',json={'handoff_enabled':True}).status_code==403
    security.end_all()


def test_simultaneous_accept_consumes_once(devices):
    from concurrent.futures import ThreadPoolExecutor
    a,b,c,t,p=devices;r=handoff.offer(a,b,'One recap')
    def take():
        try:return handoff.accept(b,r['id'])['context_data']
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(lambda _:take(),range(2)))
    assert result.count('One recap')==1 and result.count(None)==1
