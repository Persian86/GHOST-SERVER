import json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from ghost_server import store,breakin,access,security,faces,household,meals,tools
from ghost_server.app import app

@pytest.fixture
def env(tmp_path,monkeypatch):
    for m in (faces,household,meals):monkeypatch.setattr(m,'_ready',False)
    store.init(tmp_path/'breakin.db');security.reset_all();security.end_all();store.put('approve_new_devices',False)
    def pair(name,kind='phone',register=True):
        did,key=store.redeem_pair_code(store.new_pair_code(),name,kind)
        if kind=='phone' and register:breakin.register(did)
        return did,{'Authorization':'Bearer '+key}
    a,ah=pair('Controller');b,bh=pair('Family <phone>');c,ch=pair('Other');t,th=pair('Tablet','tablet')
    store.put('ha_url','http://homeassistant.test');store.put('ha_token','fake-test-only-key')
    jobs=[];calls=[];sent=[]
    monkeypatch.setattr(breakin,'_enqueue',lambda fn,*args:jobs.append((fn,args)))
    monkeypatch.setattr(breakin.hub,'send_sync',lambda target,msg:sent.append((target,msg)) or True)
    class Response:
        status_code=200
        def raise_for_status(self):pass
        def json(self):return [{'entity_id':'light.hall','state':'off','attributes':{}},{'entity_id':'siren.house','state':'off','attributes':{'supported_features':19}}]
    def req(method,path,**kw):calls.append((method,path,kw));return Response()
    monkeypatch.setattr(breakin.home,'_req',req)
    cfg={**breakin.DEFAULT,'enabled':True,'initiators':[a],'recipients':[b],'lights':['light.hall'],'sirens':['siren.house'],'home_address':'123 Test Address'}
    breakin.configure(cfg)
    def drain():
        while jobs:
            fn,args=jobs.pop(0);fn(*args)
    def prepare(test=False,share=False,rid='request_breakin_123456'):
        p=breakin.prepare(a,{'request_id':rid,'test':test,'share_home':share})
        return {'request_id':p['request_id'],'plan_token':p['plan_token']}
    def activate(test=False,share=False,rid='request_breakin_123456'):
        body=prepare(test,share,rid);item=breakin.start(a,body);drain();return breakin.view(item['id'],a),body
    yield locals()
    drain();security.end_all()

def test_default_test_mode_and_no_home_requests(env):
    e=env;item,body=e['activate']()
    assert breakin.DEFAULT['enabled'] is False and breakin.DEFAULT['test_mode'] is True
    assert item['test'] and e['calls']==[] and item['location']=={'kind':'unavailable'}
    assert '123 Test Address' not in store.q('SELECT payload FROM breakin_incidents WHERE id=?',(item['id'],))[0]['payload']
    assert all(a['state']=='simulated' for a in item['actions'])
    assert item['emergency_services_contacted'] is False
    assert len(e['sent'])==1 and 'Test Address' not in json.dumps(e['sent']) and 'Controller' not in json.dumps(e['sent'])
    assert breakin.start(e['a'],body)['id']==item['id'];e['drain']();assert len(e['sent'])==1

def test_live_exact_plan_and_manual_stop_leave_lights_on(env):
    e=env;breakin.configure({**e['cfg'],'test_mode':False})
    item,body=e['activate'](share=True)
    assert not item['test'] and item['location']['kind']=='saved_home'
    posts=[c for c in e['calls'] if c[0]=='POST']
    assert [p[1] for p in posts]==['/api/services/light/turn_on','/api/services/siren/turn_on']
    assert posts[1][2]['json']['entity_id']==['siren.house'] and 1<=posts[1][2]['json']['duration']<=120
    breakin.mark(item['id'],e['a'],'stop');e['drain']()
    assert breakin.view(item['id'],e['a'])['status']=='stopped'
    assert e['calls'][-1][1]=='/api/services/siren/turn_off'
    assert not any(c[1]=='/api/services/light/turn_off' for c in e['calls'])
    count=len(e['calls']);breakin.start(e['a'],body);breakin.stop(item['id'],e['a']);e['drain']();assert len(e['calls'])==count

def test_mode_forcing_changed_plan_and_expiry(env,monkeypatch):
    e=env;body=e['prepare']()
    breakin.configure({**e['cfg'],'test_mode':False})
    with pytest.raises(ValueError):breakin.start(e['a'],body)
    body=e['prepare'](test=True)
    store.x('UPDATE breakin_plans SET expires=?',(time.time()-1,))
    with pytest.raises(ValueError):breakin.start(e['a'],body)
    item,_=e['activate'](test=True,rid='another_request_123456');assert item['test']
    assert not [c for c in e['calls'] if c[0]=='POST']

def test_source_recipient_roles_and_one_household_activation(env):
    e=env;body=e['prepare']()
    with pytest.raises(PermissionError):breakin.start(e['c'],body)
    with pytest.raises(PermissionError):breakin.register(e['t'])
    item,_=e['activate']()
    assert breakin.inbox(e['c'])['items']==[]
    with pytest.raises(PermissionError):breakin.view(item['id'],e['c'])
    with pytest.raises(PermissionError):breakin.mark(item['id'],e['b'],'stop')
    assert not breakin.view(item['id'],e['b'])['can_stop']
    received=breakin.mark(item['id'],e['b'],'received');assert received['deliveries'][0]['received']
    assert not received['deliveries'][0]['acknowledged']
    ack=breakin.mark(item['id'],e['b'],'acknowledge')['deliveries'][0]['acknowledged']
    assert breakin.mark(item['id'],e['b'],'acknowledge')['deliveries'][0]['acknowledged']==ack
    with pytest.raises(ValueError):breakin.start(e['a'],e['prepare'](rid='second_request_123456'))
    access.set_role(e['b'],'child');store.put('household_access',True)
    with pytest.raises(PermissionError):breakin.view(item['id'],e['b'])

@pytest.mark.parametrize('extra',[{'lights':['lock.front']},{'sirens':['switch.alarm']},{'duration_s':True},{'duration_s':601},{'unknown':True}])
def test_config_validation(env,extra):
    with pytest.raises(ValueError):breakin.configure({**env['cfg'],**extra})

def test_live_siren_capability_failure_has_no_commands(env,monkeypatch):
    e=env;breakin.configure({**e['cfg'],'test_mode':False})
    class Response:
        def raise_for_status(self):pass
        def json(self):return [{'entity_id':'light.hall','state':'off'},{'entity_id':'siren.house','state':'off','attributes':{'supported_features':3}}]
    monkeypatch.setattr(breakin.home,'_req',lambda *args,**kwargs:Response())
    with pytest.raises(ValueError,match='siren.house'):e['prepare']()
    assert not store.q('SELECT id FROM breakin_incidents')

def test_timeout_does_not_repeat_on_commands_and_stop_can_retry(env,monkeypatch):
    e=env;breakin.configure({**e['cfg'],'test_mode':False});real=breakin.home._req
    def request(method,path,**kw):
        if method=='POST':e['calls'].append((method,path,kw));raise TimeoutError()
        return real(method,path,**kw)
    monkeypatch.setattr(breakin.home,'_req',request)
    item,body=e['activate']();assert all(a['state']=='unconfirmed' for a in item['actions'])
    count=len(e['calls']);breakin.start(e['a'],body);e['drain']();assert len(e['calls'])==count
    breakin.stop(item['id'],e['a']);e['drain']();assert breakin.view(item['id'],e['a'])['status']=='stop_unconfirmed'
    monkeypatch.setattr(breakin.home,'_req',real);breakin.stop(item['id'],e['a']);e['drain']()
    assert breakin.view(item['id'],e['a'])['status']=='stopped'

def test_stop_queued_activation_and_stop_during_verification(env,monkeypatch):
    e=env;breakin.configure({**e['cfg'],'test_mode':False})
    item=breakin.start(e['a'],e['prepare']());breakin.stop(item['id'],e['a']);e['drain']()
    assert not [c for c in e['calls'] if c[0]=='POST']
    body=e['prepare'](rid='race_request_123456');item=breakin.start(e['a'],body)
    real=breakin._targets
    def verify(payload):
        result=real(payload);breakin.stop(item['id'],e['a']);return result
    monkeypatch.setattr(breakin,'_targets',verify);e['drain']()
    assert not [c for c in e['calls'] if c[0]=='POST']

def test_slow_notification_queue_cannot_delay_home_commands(env):
    e=env;breakin.configure({**e['cfg'],'test_mode':False})
    item=breakin.start(e['a'],e['prepare']());fn,args=e['jobs'].pop(0);fn(*args)
    assert len([c for c in e['calls'] if c[0]=='POST'])==2
    assert e['sent']==[]  # Notification work remains separate, rather than blocking ON.
    e['drain']();assert len(e['sent'])==1

def test_expiry_during_verification_cannot_send_late_on(env,monkeypatch):
    e=env;breakin.configure({**e['cfg'],'test_mode':False})
    item=breakin.start(e['a'],e['prepare']());deadline=item['deadline'];real=breakin._targets;now=[time.time()]
    monkeypatch.setattr(breakin.time,'time',lambda:now[0])
    def verify(payload):
        result=real(payload);now[0]=deadline+2;return result
    monkeypatch.setattr(breakin,'_targets',verify);e['drain']()
    assert not [c for c in e['calls'] if c[0]=='POST']

def test_recovery_never_replays_on_and_bounds_deadline_stop(env,monkeypatch):
    e=env;breakin.configure({**e['cfg'],'test_mode':False});item,_=e['activate']()
    store.x("UPDATE breakin_actions SET state='attempting' WHERE incident=?",(item['id'],))
    store.x("UPDATE breakin_incidents SET status='executing' WHERE id=?",(item['id'],))
    count=len(e['calls']);breakin.recover();assert len(e['calls'])==count
    assert breakin.view(item['id'],e['a'])['status']=='activation_unconfirmed'
    store.x('UPDATE breakin_incidents SET deadline=? WHERE id=?',(time.time()-2,item['id']))
    breakin.tick();e['drain']();assert e['calls'][-1][1]=='/api/services/siren/turn_off'
    item,_=e['activate'](rid='late_request_123456');count=len(e['calls'])
    store.x('UPDATE breakin_incidents SET deadline=? WHERE id=?',(time.time()-70,item['id']))
    breakin.tick();e['drain']();assert len(e['calls'])==count
    assert breakin.view(item['id'],e['a'])['status']=='deadline_missed'

def test_changed_connection_never_operates_a_different_home(env):
    e=env;breakin.configure({**e['cfg'],'test_mode':False});item,_=e['activate']();count=len(e['calls'])
    store.put('ha_url','http://different-home.test');breakin.stop(item['id'],e['a']);e['drain']()
    assert len(e['calls'])==count and breakin.view(item['id'],e['a'])['status']=='stop_unconfirmed'

@pytest.mark.parametrize('key',['a','b'])
def test_revocation_before_worker_starts_cannot_send_or_operate(env,key):
    e=env;breakin.configure({**e['cfg'],'test_mode':False})
    item=breakin.start(e['a'],e['prepare']());e['calls'].clear();store.revoke_device(e[key]);e['drain']()
    assert e['calls']==[] and e['sent']==[] and breakin.view(item['id'])['status']=='not_started'

def test_setup_disable_stops_active_sirens_and_revocation_blocks_access(env):
    e=env;breakin.configure({**e['cfg'],'test_mode':False});item,_=e['activate']()
    breakin.configure({**e['cfg'],'enabled':False});e['drain']()
    assert breakin.view(item['id'],e['a'])['status']=='stopped'
    store.revoke_device(e['a'])
    with pytest.raises(PermissionError):breakin.view(item['id'],e['a'])

def test_routes_auth_csrf_and_no_model_tool(env):
    e=env;client=TestClient(app);client.headers['X-Ghost-Admin']='1';client.post('/admin/login',json={'password':'owner-pass'})
    assert client.get('/api/v1/breakin').status_code==401
    assert client.get('/api/v1/breakin',headers=e['th']).status_code==403
    p=client.post('/api/v1/breakin/prepare',headers=e['ah'],json={'request_id':'api_request_123456','test':True,'share_home':False})
    assert p.status_code==200
    plan=p.json();started=client.post('/api/v1/breakin',headers=e['ah'],json={'request_id':plan['request_id'],'plan_token':plan['plan_token']})
    assert started.status_code==200
    assert client.put('/admin/api/breakin',headers={'X-Ghost-Admin':'','Origin':'https://other.test'},json=e['cfg']).status_code==403
    assert not any('breakin' in d['name'] for d in tools.declarations('live'))
