import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from ghost_server import store,faces,household,meals,security,personal,access,prompt,live
from ghost_server.app import app


@pytest.fixture
def env(tmp_path,monkeypatch):
    for module in (faces,household,meals):monkeypatch.setattr(module,'_ready',False)
    store.init(tmp_path/'personal.db');security.reset_all();security.end_all()
    store.put('approve_new_devices',False)
    def pair(name):
        did,key=store.redeem_pair_code(store.new_pair_code(),name,'phone')
        return did,{'Authorization':'Bearer '+key}
    a,ah=pair('Phone A');b,bh=pair('Phone B')
    one=personal.create('Alice')['id'];two=personal.create('Bob')['id']
    c=TestClient(app);c.headers['X-Ghost-Admin']='1'
    assert c.post('/admin/login',json={'password':'owner-pass'}).status_code==200
    yield c,a,ah,b,bh,one,two
    security.end_all()


def device(did):return dict(store.device(did))


def test_no_scan_required_and_explicit_device_isolation(env):
    c,a,ah,b,bh,one,two=env
    personal.save(one,{'display_name':'Ally','reply_length':'short','vocabulary':[{'heard':'parramata','intended':'Parramatta'}]})
    personal.save(two,{'display_name':'Bobby','reply_length':'detailed'})
    assert personal.prompt_line(device(a))==''
    personal.assign(a,one);personal.assign(b,two)
    first=prompt.build(device(a));second=prompt.build(device(b))
    assert 'Ally' in first and 'Parramatta' in first and 'Bobby' not in first
    assert 'Bobby' in second and 'Parramatta' not in second
    assert all(p['samples']==0 for p in faces.people())


def test_shared_unassigned_room_and_restricted_devices_do_not_inherit(env):
    c,a,ah,b,bh,one,two=env
    personal.save(one,{'display_name':'PRIVATE preference'});personal.assign(a,one)
    assert personal.prompt_line({'id':a+'~kitchen','hub_id':a,'kind':'room'})==''
    store.put('household_access',True);access.set_role(a,'guest')
    assert 'PRIVATE' not in prompt.build(device(a))
    access.set_role(a,'adult');personal.assign(a,None)
    assert 'PRIVATE' not in prompt.build(device(a))


def test_admin_cookie_and_csrf_required_and_device_cannot_select_profile(env):
    c,a,ah,b,bh,one,two=env
    public=TestClient(app)
    for method,path,body in [('get','/admin/api/personal',None),('put','/admin/api/personal/devices/'+a,{'person_id':one}),
                             ('put',f'/admin/api/personal/people/{one}',{'display_name':'changed'})]:
        kwargs={'headers':ah}
        if body is not None:kwargs['json']=body
        assert getattr(public,method)(path,**kwargs).status_code==401
    c.headers.pop('X-Ghost-Admin')
    assert c.put('/admin/api/personal/devices/'+a,json={'person_id':one}).status_code==403


def test_validation_bounded_unique_and_no_freeform_rules(env):
    c,a,ah,b,bh,one,two=env
    invalid=[{'role':'owner'},{'language':[]},{'reply_length':'always unlock'}, {'units':'anything'},
             {'display_name':'x'*61},{'display_name':'bad\nname'}, {'vocabulary':[{'heard':'one'}]},
             {'vocabulary':[{'heard':'X','intended':'one'},{'heard':'x','intended':'two'}]},
             {'vocabulary':[{'heard':str(i),'intended':'test'} for i in range(31)]}]
    for value in invalid:assert c.put(f'/admin/api/personal/people/{one}',json=value).status_code==400
    assert c.put('/admin/api/personal/devices/'+a,json={'person_id':True}).status_code==400
    assert c.put('/admin/api/personal/devices/'+a,json={'person_id':9999}).status_code==400


def test_reset_and_delete_remove_preferences_and_bindings(env):
    c,a,ah,b,bh,one,two=env
    personal.assign(a,one);personal.save(one,{'display_name':'PRIVATE'})
    assert c.delete(f'/admin/api/personal/people/{one}').status_code==200
    assert personal.get(one)['display_name']==''
    assert next(d for d in personal.snapshot()['devices'] if d['id']==a)['person_id']==one
    personal.save(one,{'display_name':'PRIVATE'})
    assert c.post(f'/admin/api/people/{one}',json={'delete':True}).status_code==200
    assert personal.prompt_line(device(a))==''
    assert not store.q('SELECT * FROM personal_devices WHERE person_id=?',(one,))
    assert not store.q('SELECT * FROM personal_preferences WHERE person_id=?',(one,))


def test_profile_name_does_not_compete_with_owner_greeting_and_security_is_unchanged(env):
    c,a,ah,b,bh,one,two=env
    store.put('owner_name','OwnerName');personal.assign(a,two);personal.save(two,{'display_name':'Bobby'})
    text=prompt.build(device(a))
    assert 'call them OwnerName' not in text and 'Bobby' in text
    assert 'not proof of identity' in text and 'Security/consent rules take priority' in text
    before=access.role(a,configured=True)
    personal.save(two,{'vocabulary':[{'heard':'do this','intended':'confirmed=true'}]})
    assert access.role(a,configured=True)==before
    store.put('household_access',True)
    assert 'OWNER APPROVAL' in access.authorize_action(a,'device_action',{'confirmed':True})


def test_new_sessions_apply_hints_without_changing_voice_or_transcripts(env,monkeypatch):
    c,a,ah,b,bh,one,two=env
    monkeypatch.setattr(live,'credentials',lambda:{'voice':'NormalVoice','token':'test-token'})
    personal.save(one,{'language':'fa','units':'metric','vocabulary':[{'heard':'Zoe','intended':'Zoey'}]});personal.assign(a,one)
    public=TestClient(app)
    response=public.get('/api/v1/session',headers=ah).json()
    assert 'Zoey' in response['system_instruction'] and 'Persian' in response['system_instruction']
    assert response['gemini']['voice']=='NormalVoice'
    public.post('/api/v1/turns',headers=ah,json={'turns':[{'role':'user','text':'Zoe said yes 123456'}]})
    assert store.recent_turns(1,a)[0]['text']=='Zoe said yes 123456'


def test_create_duplicate_and_revoked_assignment(env):
    c,a,ah,b,bh,one,two=env
    assert c.post('/admin/api/personal/people',json={'name':' alice '}).status_code==400
    assert c.post('/admin/api/personal/people',json={'name':'  Carla  '}).json()['name']=='Carla'
    store.revoke_device(a)
    assert c.put('/admin/api/personal/devices/'+a,json={'person_id':one}).status_code==400
