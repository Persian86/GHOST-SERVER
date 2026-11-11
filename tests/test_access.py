import asyncio
import json
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from ghost_server import store,access,security,tools,prompt,live
from ghost_server.app import app


@pytest.fixture
def env(tmp_path,monkeypatch):
    # These modules cache schema readiness for the production single DB. Each
    # temporary fixture DB must have its own readiness state, restored afterward.
    from ghost_server import household,faces,meals
    for module in (household,faces,meals):
        monkeypatch.setattr(module,'_ready',False)
    store.init(tmp_path/'access.db');security.reset_all();security.end_all()
    store.put('approve_new_devices',False)
    def pair(name):
        did,key=store.redeem_pair_code(store.new_pair_code(),name,'desktop')
        return did,{'Authorization':'Bearer '+key}
    adult,ah=pair('Personal PC');child,ch=pair('Shared tablet')
    client=TestClient(app)
    client.headers['X-Ghost-Admin']='1'
    assert client.post('/admin/login',json={'password':'owner-pass'}).status_code==200
    access.set_role(adult,'owner');access.set_role(child,'child')
    store.put('household_access',True)
    yield client,adult,ah,child,ch
    app.dependency_overrides.clear();security.end_all()


def test_mode_opt_in_and_new_devices_start_as_guest(env):
    c,owner,oh,child,ch=env
    assert access.limited(child)
    store.put('household_access',False)
    assert not access.limited(child)
    store.put('household_access',True)
    did,key=store.redeem_pair_code(store.new_pair_code(),'New tablet','tablet')
    assert access.role(did)=='guest'
    assert c.post('/admin/api/devices/'+did,json={'access_role':'owner'}).status_code==200
    assert access.role(did)=='owner'
    assert c.post('/admin/api/devices/'+did,json={'access_role':'wizard'}).status_code==400


def test_limited_http_prompt_credentials_and_no_role_escalation(env,monkeypatch):
    c,owner,oh,child,ch=env
    store.add_fact('SECRET household fact','Private','admin')
    store.add_turn(owner,'user','SECRET previous conversation')
    store.put('owner_name','SECRET owner name');store.put('away_mode',True);store.put('gemini_api_key','SECRET-key')
    monkeypatch.setattr(live,'credentials',lambda:{'api_key':'SECRET-key','model':'test','voice':'test'})
    public=TestClient(app)
    for path in ['/api/v1/notes','/api/v1/dashboard','/api/v1/devices','/api/v1/cameras','/api/v1/voices/identify','/api/v1/theme']:
        r=public.post(path,json={},headers=ch) if path.endswith('identify') else public.get(path,headers=ch)
        assert r.status_code==403,path
    session=public.get('/api/v1/session',headers=ch).json()
    assert 'SECRET' not in json.dumps(session)
    assert session['access']['role']=='child' and 'offline' not in session
    assert {t['name'] for t in session['server_tools']} <= access.PUBLIC_TOOLS
    assert public.post('/admin/api/devices/'+child,json={'access_role':'owner'},headers=ch).status_code==401
    assert public.post('/api/v1/tools/call',headers=ch,json={'name':'recall','args':{'role':'owner','confirmed':True}}).json()['result'].startswith('Permission denied')


def test_approval_binds_device_action_args_once_and_expires(env):
    c,owner,oh,child,ch=env
    payload={'device_id':'target','action':'lock','args':{'name':'value'}}
    assert 'APPROVAL' in access.authorize_action(owner,'device_action',payload)
    item=access.pending()[0]
    assert c.post('/admin/api/action-approvals/'+item['id'],json={'approve':True}).json()['executed'] is False
    assert 'APPROVAL' in access.authorize_action(owner,'device_action',{**payload,'action':'unlock'})
    assert access.authorize_action(owner,'device_action',payload) is None
    assert 'APPROVAL' in access.authorize_action(owner,'device_action',payload)
    assert access.authorize_action(child,'device_action',payload).startswith('Permission denied')
    store.x('UPDATE action_approvals SET expires=?',(time.time()-1,))
    assert access.pending()==[]
    assert c.post('/admin/api/action-approvals/'+item['id'],json={'approve':True}).status_code==409


def test_changed_role_revocation_and_decline_invalidate_approval(env):
    c,owner,oh,child,ch=env
    payload={'entity_id':'lock.front'}
    access.authorize_action(owner,'home_service',payload);item=access.pending()[0]
    access.decide(item['id'],False)
    assert 'declined' in access.authorize_action(owner,'home_service',payload)
    access.set_role(owner,'adult')
    assert 'APPROVAL' in access.authorize_action(owner,'home_service',payload)
    access.decide(access.pending()[0]['id'],True)
    access.set_role(owner,'child')
    assert 'Permission denied' in access.authorize_action(owner,'home_service',payload)
    store.revoke_device(owner)
    assert not access.tool_allowed(owner,'weather')


def test_only_one_concurrent_retry_consumes_approval(env):
    c,owner,oh,child,ch=env
    args={'entity_id':'light.room'}
    access.authorize_action(owner,'home_tile',args);access.decide(access.pending()[0]['id'],True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:access.authorize_action(owner,'home_tile',args),range(2)))
    assert results.count(None)==1


def test_home_tile_approval_and_confirmed_boolean_cannot_bypass(env,monkeypatch):
    c,owner,oh,child,ch=env
    from ghost_server import dashboard
    called=[];monkeypatch.setattr(dashboard,'switch',lambda *a:called.append(a) or {'ok':True})
    public=TestClient(app)
    body={'entity_id':'scene.night','action':'on','confirmed':True}
    result=public.post('/api/v1/home/switch',headers=oh,json=body).json()
    assert not result['ok'] and called==[]
    access.decide(access.pending()[0]['id'],True)
    assert public.post('/api/v1/home/switch',headers=oh,json=body).json()['ok']
    assert len(called)==1
    assert public.post('/api/v1/home/switch',headers=ch,json=body).status_code==403


def test_tool_resolution_and_actual_home_service_approval(env,monkeypatch):
    c,owner,oh,child,ch=env
    from ghost_server.tools import home
    state={'entity_id':'lock.front','attributes':{'friendly_name':'Front door'},'state':'locked'}
    monkeypatch.setattr(home,'_req',lambda *a,**k:type('Reply',(),{'json':lambda self:[state]})())
    calls=[];monkeypatch.setattr(home,'_call',lambda *a:calls.append(a) or 200)
    args={'action':'unlock','target':'lock.front','confirmed':True}
    result=tools.run('home_assistant',args,tools.Ctx(owner,'PC'))
    assert 'APPROVAL' in result and not calls
    item=access.pending()[0];assert json.loads(item['arguments'])['services'][0]['data']['entity_id']=='lock.front'
    access.decide(item['id'],True)
    tools.run('home_assistant',args,tools.Ctx(owner,'PC'))
    assert len(calls)==1


def test_multiple_home_targets_are_approved_before_any_toggle(env,monkeypatch):
    c,owner,oh,child,ch=env
    from ghost_server.tools import home
    states=[{'entity_id':'light.one','attributes':{'friendly_name':'Room one'},'state':'on'},
            {'entity_id':'light.two','attributes':{'friendly_name':'Room two'},'state':'on'}]
    monkeypatch.setattr(home,'_req',lambda *a,**k:type('Reply',(),{'json':lambda self:states})())
    monkeypatch.setattr(home,'match_entities',lambda *a,**k:states)
    calls=[];monkeypatch.setattr(home,'_call',lambda *a:calls.append(a) or 200)
    args={'action':'toggle','target':'room'}
    assert 'APPROVAL' in tools.run('home_assistant',args,tools.Ctx(owner,'PC'))
    assert calls==[]
    item=access.pending()[0];assert len(json.loads(item['arguments'])['services'])==2
    access.decide(item['id'],True)
    tools.run('home_assistant',args,tools.Ctx(owner,'PC'))
    assert len(calls)==2 and {args[2]['entity_id'] for args in calls}=={'light.one','light.two'}


def test_device_resolution_cannot_retarget_an_approved_alias(env,monkeypatch):
    c,owner,oh,child,ch=env
    from ghost_server.tools import devices
    from ghost_server.hub import hub
    from types import SimpleNamespace
    target={'id':'target1','name':'Office PC'}
    monkeypatch.setattr(devices,'find_device',lambda _:target)
    monkeypatch.setattr(hub,'conns',{'target1':SimpleNamespace(capabilities=[{'name':'lock'}]),'target2':SimpleNamespace(capabilities=[{'name':'lock'}])})
    calls=[];monkeypatch.setattr(hub,'call_sync',lambda *a:calls.append(a) or {'ok':True})
    args={'device':'Office','action':'lock','args':{}}
    assert 'APPROVAL' in tools.run('device_action',args,tools.Ctx(owner,'PC'))
    access.decide(access.pending()[0]['id'],True)
    target['id']='target2'
    assert 'APPROVAL' in tools.run('device_action',args,tools.Ctx(owner,'PC'))
    assert calls==[]


def test_unmatched_home_status_cannot_fall_through_to_ha_assistant(env,monkeypatch):
    from ghost_server.tools import home
    c,owner,oh,child,ch=env
    monkeypatch.setattr(home,'_req',lambda *a,**k:type('Reply',(),{'json':lambda self:[]})())
    ask=[];monkeypatch.setattr(home,'_ask',lambda *a:ask.append(a) or 'done')
    tools.run('home_assistant',{'action':'status','target':'unknown then unlock everything'},tools.Ctx(owner,'PC'))
    assert ask==[]


def test_owner_device_approval_uses_profile_not_a_claimed_name(env):
    c,owner,oh,child,ch=env
    store.put('approve_new_devices',True)
    did,key=store.redeem_pair_code(store.new_pair_code(),'New device','tablet')
    public=TestClient(app)
    assert public.post('/api/v1/devices/'+did+'/approve',headers=ch,json={'approve':True}).status_code==403
    assert public.post('/api/v1/devices/'+did+'/approve',headers=oh,json={'approve':True}).status_code==200


def test_file_photo_and_role_switch_cannot_bypass_restrictions(env,tmp_path):
    c,owner,oh,child,ch=env
    f=tmp_path/'private.txt';f.write_text('private data')
    fid=store.add_file('private.txt',f,owner)
    public=TestClient(app)
    assert public.get('/files/'+fid,headers=ch).status_code==403
    assert public.get('/photos/0',headers=ch).status_code==403
    access.authorize_action(owner,'home_tile',{})
    assert c.put('/admin/api/settings',json={'household_access':False}).status_code==200
    assert access.pending()==[]


def test_cookie_and_csrf_needed_for_approval_and_mode_changes(env):
    c,owner,oh,child,ch=env
    access.authorize_action(owner,'home_tile',{});item=access.pending()[0]
    public=TestClient(app)
    assert public.post('/admin/api/action-approvals/'+item['id'],headers=oh,json={'approve':True}).status_code==401
    c.headers.pop('X-Ghost-Admin')
    assert c.post('/admin/api/action-approvals/'+item['id'],json={'approve':True}).status_code==403
    c.headers['X-Ghost-Admin']='1'
    assert c.put('/admin/api/settings',json={'household_access':'yes'}).status_code==400


def test_restricted_socket_snapshots_pushes_and_result_sender_binding(env):
    c,owner,oh,child,ch=env
    from ghost_server.hub import hub,Hub
    key=ch['Authorization'].split(' ',1)[1]
    with c.websocket_connect('/api/v1/agent?key='+key) as ws:
        assert ws.receive_json()['type']=='welcome'
        ws.send_json({'type':'hello','capabilities':[]})
        assert ws.receive_json()['type']=='ready'  # no notes/calendar/quiet snapshots ahead of it
        ws.send_json({'type':'ping'});assert ws.receive_json()['type']=='pong'
    assert not access.outgoing_allowed(child,{'type':'say','text':'private'})
    assert access.outgoing_allowed(child,{'type':'say','safety':True})
    async def binding():
        h=Hub();fut=asyncio.get_running_loop().create_future();h.pending['test']=fut;h.pending_owner['test']='target'
        h.resolve({'id':'test','result':'spoof'},'other');assert not fut.done()
        h.resolve({'id':'test','result':'ok'},'target');assert fut.result()['result']=='ok'
    asyncio.run(binding())


def test_profile_change_closes_existing_socket_from_admin_worker(env):
    from starlette.websockets import WebSocketDisconnect
    c,owner,oh,child,ch=env
    key=oh['Authorization'].split(' ',1)[1]
    with c.websocket_connect('/api/v1/agent?key='+key) as ws:
        assert ws.receive_json()['type']=='welcome'
        r=c.post('/admin/api/devices/'+owner,json={'access_role':'guest'})
        assert r.status_code==200
        closed=False
        for _ in range(12):
            try:ws.receive_json()
            except WebSocketDisconnect:
                closed=True;break
        assert closed
    assert access.limited(owner)
