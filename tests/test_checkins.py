import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from ghost_server import store, checkins, access, security, faces, household, meals, tools
from ghost_server.app import app


@pytest.fixture
def env(tmp_path, monkeypatch):
    for m in (faces, household, meals):
        monkeypatch.setattr(m, '_ready', False)
    store.init(tmp_path/'checkins.db')
    security.reset_all();security.end_all();store.put('approve_new_devices', False)
    now = [time.time()]
    monkeypatch.setattr(checkins.time, 'time', lambda: now[0])
    monkeypatch.setattr(checkins, '_last_tick', 0)
    def pair(name, kind='phone', register=True):
        did, key = store.redeem_pair_code(store.new_pair_code(), name, kind)
        if kind == 'phone' and register:
            checkins.register(did)
        return did, {'Authorization': 'Bearer '+key}
    a, ah = pair('Participant');b, bh = pair('Family <phone>');c, ch = pair('Other');t, th = pair('Tablet', 'tablet')
    jobs = [];sent = []
    monkeypatch.setattr(checkins, '_enqueue', lambda fn, *args: jobs.append((fn, args)))
    monkeypatch.setattr(checkins.hub, 'send_sync', lambda target, msg: sent.append((target, msg)) or True)
    cfg = {**checkins.DEFAULT, 'enabled': True, 'participants': [a], 'recipients': [b]}
    checkins.configure(cfg);checkins.tick()
    def body(rid='checkin_request_123456', **kw):
        return {'request_id': rid, 'minutes': 1, 'grace_minutes': 1, 'test': True,
                'setup_token': checkins.inbox(a)['setup_token'], **kw}
    def start(**kw):
        return checkins.start(a, body(**kw))
    def drain():
        while jobs:
            fn, args = jobs.pop(0);fn(*args)
    def advance(seconds):
        now[0] += seconds;checkins.tick();drain()
    yield locals()
    drain();security.end_all()


def test_defaults_and_reminder_grace_escalation(env):
    e=env;item=e['start']()
    assert checkins.DEFAULT['enabled'] is False and checkins.DEFAULT['test_mode'] is True
    assert item['test'] and item['status']=='waiting' and e['sent']==[]
    e['advance'](59);assert e['sent']==[]
    e['advance'](1);assert e['sent'][0][0]==e['a'] and e['sent'][0][1]['stage']=='reminder'
    e['advance'](59);assert len(e['sent'])==1
    e['advance'](1);assert e['sent'][1][0]==e['b'] and e['sent'][1][1]['stage']=='missed'
    e['advance'](1);assert len(e['sent'])==2
    assert 'Participant' not in json.dumps(e['sent']) and 'Family' not in json.dumps(e['sent'])
    assert checkins.view(item['id'],e['a'])['emergency_services_contacted'] is False


@pytest.mark.parametrize('when', [0,60,120])
def test_safe_finishes_and_prevents_later_escalation(env,when):
    e=env;item=e['start']();e['advance'](when)
    checkins.mark(item['id'],e['a'],'safe',{});e['drain']();count=len(e['sent'])
    assert checkins.view(item['id'],e['a'])['status']=='safe'
    e['advance'](180);assert len(e['sent'])==count
    if when==120:assert e['sent'][-1][1]['stage']=='update'


def test_cancel_queued_reminder_and_missed_jobs(env):
    e=env;item=e['start']();e['now'][0]+=120;checkins.tick()
    checkins.mark(item['id'],e['a'],'cancel',{});e['drain']()
    assert e['sent']==[] and checkins.view(item['id'],e['a'])['status']=='cancelled'


def test_retry_is_exact_and_one_timer_per_participant(env):
    e=env;body=e['body']();item=checkins.start(e['a'],body);e['advance'](60)
    assert checkins.start(e['a'],body)['due']==item['due'] and len(e['sent'])==1
    with pytest.raises(ValueError,match='exactly'):checkins.start(e['a'],{**body,'minutes':2})
    with pytest.raises(ValueError,match='existing'):e['start'](rid='another_request_123456')
    checkins.mark(item['id'],e['a'],'cancel',{});e['drain']()
    assert e['start'](rid='another_request_123456')['status']=='waiting'


def test_expiring_source_bound_setup_confirmation_and_no_old_retry_recreation(env):
    e=env;body=e['body']();e['advance'](121)
    with pytest.raises(ValueError,match='expired'):checkins.start(e['a'],body)
    body=e['body']();checkins.configure({**e['cfg'],'test_mode':False})
    with pytest.raises(ValueError,match='changed'):checkins.start(e['a'],body)
    checkins.configure({**e['cfg'],'participants':[e['a'],e['c']]});body=e['body']()
    with pytest.raises(ValueError,match='changed'):checkins.start(e['c'],body)


def test_old_retry_cannot_create_new_timer_after_retention_prune(env):
    e=env;body=e['body']();item=checkins.start(e['a'],body)
    e['advance'](125)
    assert checkins.start(e['a'],body)['id']==item['id']  # An existing request remains retrievable.
    e['advance'](90000);checkins.admin_snapshot()
    assert store.q('SELECT * FROM checkins')==[]
    with pytest.raises(ValueError,match='expired'):checkins.start(e['a'],body)


def test_delayed_worker_does_not_send_outside_alert_window(env):
    e=env;item=e['start']();e['now'][0]+=120;checkins.tick()
    e['now'][0]+=301;e['drain']();checkins.tick();e['drain']()
    assert e['sent']==[]
    assert next(d for d in checkins.view(item['id'])['deliveries'] if d['stage']=='missed')['transport']=='not_sent'


def test_start_rate_limit(env):
    e=env
    for n in range(20):
        item=e['start'](rid='checkin_rate_request_'+str(n));checkins.mark(item['id'],e['a'],'cancel',{})
    with pytest.raises(ValueError,match='Too many'):e['start'](rid='checkin_rate_request_21')


@pytest.mark.parametrize('changes', [{'minutes':True},{'grace_minutes':0},{'minutes':1441},{'grace_minutes':61},{'test':'false'},{'unexpected':1}])
def test_invalid_timer_inputs_rejected(env,changes):
    e=env
    with pytest.raises(ValueError):checkins.start(e['a'],e['body'](**changes))


def test_force_test_and_live_minimum(env):
    e=env;assert e['start'](test=False)['test']
    checkins.configure({**e['cfg'],'test_mode':False})
    with pytest.raises(ValueError,match='5–1440'):e['start'](rid='live_request_123456',test=False)
    item=e['start'](rid='live_request_123456',test=False,minutes=5)
    assert not item['test']


def test_roles_receipts_and_explicit_ack_not_safe(env):
    e=env;item=e['start']();token=item['id']
    with pytest.raises(PermissionError):checkins.view(token,e['c'])
    with pytest.raises(PermissionError):checkins.mark(token,e['b'],'safe',{})
    with pytest.raises(PermissionError):checkins.mark(token,e['b'],'received',{})
    e['advance'](120);checkins.mark(token,e['b'],'received',{'stage':'missed'})
    d=next(d for d in checkins.view(token,e['a'])['deliveries'] if d['stage']=='missed')
    assert d['received'] and not d['acknowledged']
    checkins.mark(token,e['b'],'acknowledge',{})
    assert checkins.view(token,e['a'])['status']=='alerted'
    checkins.mark(token,e['a'],'safe',{});e['drain']()
    assert checkins.view(token,e['b'])['status']=='safe'


@pytest.mark.parametrize('key',['a','b'])
def test_revocation_before_dispatch_prevents_notice(env,key):
    e=env;item=e['start']();e['now'][0]+=120;checkins.tick();store.revoke_device(e[key]);e['drain']()
    assert e['sent']==[]
    with pytest.raises(PermissionError):checkins.view(item['id'],e[key])


def test_config_changes_cancel_active_and_remove_access(env):
    e=env;item=e['start']();checkins.configure({**e['cfg'],'recipients':[e['c']]});e['advance'](120)
    assert e['sent']==[] and checkins.view(item['id'])['status']=='cancelled_by_owner'
    with pytest.raises(PermissionError):checkins.view(item['id'],e['b'])


def test_restart_resumes_only_pending_within_window_not_uncertain_sends(env):
    e=env;item=e['start']();e['now'][0]+=120;checkins.tick();e['jobs'].clear()
    checkins.recover();checkins.tick();e['drain']();assert len(e['sent'])==1
    store.x("UPDATE checkin_deliveries SET transport='sending' WHERE incident=? AND stage='missed'",(item['id'],))
    checkins.recover();checkins.tick();e['drain']();assert len(e['sent'])==1
    assert next(d for d in checkins.view(item['id'])['deliveries'] if d['stage']=='missed')['transport']=='unconfirmed'


def test_late_recovery_no_stale_family_alert(env):
    e=env;item=e['start']();e['advance'](421)
    assert e['sent']==[] and checkins.view(item['id'])['status']=='recovery_missed'


def test_offline_send_not_replayed_and_stale_worker_not_sent(env,monkeypatch):
    e=env;monkeypatch.setattr(checkins.hub,'send_sync',lambda *a:False)
    item=e['start']();e['advance'](120)
    d=next(d for d in checkins.view(item['id'])['deliveries'] if d['stage']=='missed')
    assert d['transport']=='not_confirmed'
    monkeypatch.setattr(checkins.hub,'send_sync',lambda *a:pytest.fail('Uncertain delivery replayed'))
    e['advance'](1)


def test_unhealthy_scheduler_blocks_new_timer(env):
    e=env;body=e['body']();e['now'][0]+=16
    with pytest.raises(ValueError,match='unavailable'):checkins.start(e['a'],body)


def test_access_restriction_cancels_and_invalid_config(env):
    e=env;item=e['start']();access.set_role(e['a'],'child');store.put('household_access',True)
    e['advance'](60)
    assert checkins.view(item['id'])['status']=='cancelled_access_changed' and e['sent']==[]
    with pytest.raises(PermissionError):checkins.configure(e['cfg'])


def test_expiry_retention_and_owner_cancel_not_safe(env):
    e=env;item=e['start']();checkins.cancel_by_owner(item['id']);e['drain']()
    assert checkins.view(item['id'])['status']=='cancelled_by_owner'
    e['advance'](4000)
    with pytest.raises(PermissionError):checkins.view(item['id'],e['a'])
    e['advance'](86401);checkins.admin_snapshot()
    assert store.q('SELECT * FROM checkins')==[] and store.q('SELECT * FROM checkin_deliveries')==[]


def test_routes_auth_csrf_and_no_direct_model_tools(env):
    e=env;client=TestClient(app);client.headers['X-Ghost-Admin']='1'
    client.post('/admin/login',json={'password':'owner-pass'})
    assert client.get('/api/v1/checkins').status_code==401
    assert client.get('/api/v1/checkins',headers=e['th']).status_code==403
    r=client.post('/api/v1/checkins',headers=e['ah'],json=e['body']());assert r.status_code==200
    assert client.post('/api/v1/checkins/'+r.json()['id']+'/safe',headers=e['bh'],json={}).status_code==403
    assert client.put('/admin/api/checkins',headers={'X-Ghost-Admin':'','Origin':'https://other.test'},json=e['cfg']).status_code==403
    assert not any('checkin' in d['name'] for d in tools.declarations('live'))
