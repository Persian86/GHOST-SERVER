import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator
from ghost_server import store, testcalls, access, security, faces, household, meals, tools
from ghost_server.app import app


@pytest.fixture
def env(tmp_path, monkeypatch):
    for m in (faces, household, meals):monkeypatch.setattr(m, '_ready', False)
    store.init(tmp_path/'testcalls.db');security.reset_all();security.end_all();store.put('approve_new_devices', False)
    now=[time.time()];monkeypatch.setattr(testcalls.time,'time',lambda:now[0]);testcalls._memory.clear()
    def pair(name,kind='phone'):
        did,key=store.redeem_pair_code(store.new_pair_code(),name,kind)
        if kind=='phone':testcalls.register(did)
        return did,{'Authorization':'Bearer '+key}
    a,ah=pair('Initiator');b,bh=pair('Other');t,th=pair('Tablet','tablet')
    account='AC'+'1'*32;auth='mock-auth-token-not-real-123456789';sid='CA'+'2'*32
    for key,value in {'TWILIO_ACCOUNT_SID':account,'TWILIO_AUTH_TOKEN':auth,'TWILIO_FROM_NUMBER':'+61200000000','GHOST_CALL_PUBLIC_URL':'https://ghost.example.com'}.items():monkeypatch.setenv(key,value)
    store.put('anthropic_api_key','mock-ai-key-not-real')
    jobs=[];calls=[];updates=[];prompts=[]
    monkeypatch.setattr(testcalls,'_enqueue',lambda fn,*args:jobs.append((fn,args)))
    class Calls:
        def create(self,**kw):calls.append(kw);return SimpleNamespace(sid=sid)
        def __call__(self,value):
            assert value==sid
            return SimpleNamespace(fetch=lambda:SimpleNamespace(status='in-progress'),update=lambda **kw:updates.append(kw))
    provider=SimpleNamespace(calls=Calls())
    monkeypatch.setattr(testcalls,'_client',lambda env=None:provider)
    real_respond=testcalls._respond
    monkeypatch.setattr(testcalls,'_respond',lambda history:prompts.append(history) or 'I can hear you. What would you like to test next?')
    cfg={**testcalls.DEFAULT,'enabled':True,'initiators':[a],
         'contacts':[{'id':'test_contact_1','name':'Test <contact>','number':'+61400000001','consent':True}]}
    testcalls.configure(cfg)
    def drain():
        while jobs:
            fn,args=jobs.pop(0);fn(*args)
    def body(rid='telephone_request_123456',dry_run=False,**kw):
        return {'request_id':rid,'contact_id':'test_contact_1','dry_run':dry_run,'setup_token':testcalls.inbox(a)['setup_token'],**kw}
    serial=[0]
    def activate():
        serial[0]+=1
        testcalls.configure({**cfg,'dry_run':False});payload=body(rid='telephone_request_'+str(serial[0]).zfill(8));item=testcalls.start(a,payload,True);drain()
        return testcalls.view(item['id'],a),payload
    def params(**kw):return {'AccountSid':account,'CallSid':sid,'From':'+61200000000','To':'+61400000001',**kw}
    def webhook(token,endpoint,**kw):return testcalls.provider(token,endpoint,params(**kw))
    yield locals()
    drain();testcalls._memory.clear();security.end_all()


def test_dry_run_default_zero_external_requests_and_idempotent(env,monkeypatch):
    e=env
    for key in ('TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_FROM_NUMBER','GHOST_CALL_PUBLIC_URL'):monkeypatch.delenv(key)
    store.put('anthropic_api_key','')
    body=e['body']();item=testcalls.start(e['a'],body)
    assert testcalls.DEFAULT['enabled'] is False and testcalls.DEFAULT['dry_run'] is True
    assert item['dry_run'] and item['status']=='dry_run' and e['calls']==[] and e['prompts']==[] and e['jobs']==[]
    assert testcalls.start(e['a'],body)['id']==item['id']
    assert item['emergency_services_contacted'] is False


@pytest.mark.parametrize('number',['000','112','106','+61000','+61112','1800123456','+611800123456','+19001234567','+61400000001;123','sip:test@example.com'])
def test_non_ordinary_australian_test_numbers_rejected(env,number):
    e=env;c={**e['cfg']['contacts'][0],'number':number}
    with pytest.raises(ValueError):testcalls.configure({**e['cfg'],'contacts':[c]})


def test_prior_consent_unique_contacts_and_roles(env):
    e=env;c={**e['cfg']['contacts'][0],'consent':False}
    with pytest.raises(ValueError):testcalls.configure({**e['cfg'],'contacts':[c]})
    with pytest.raises(ValueError):testcalls.configure({**e['cfg'],'contacts':e['cfg']['contacts']*2})
    with pytest.raises(PermissionError):testcalls.start(e['b'],e['body']())
    assert testcalls.inbox(e['b'])['contacts']==[]
    with pytest.raises(PermissionError):testcalls.register(e['t'])


def test_real_requires_https_and_configured_providers(env,monkeypatch):
    e=env;testcalls.configure({**e['cfg'],'dry_run':False});body=e['body']()
    with pytest.raises(PermissionError,match='HTTPS'):testcalls.start(e['a'],body)
    monkeypatch.delenv('TWILIO_AUTH_TOKEN')
    with pytest.raises(ValueError):testcalls.start(e['a'],body,True)
    with pytest.raises(ValueError):testcalls.configure({**e['cfg'],'dry_run':False})
    assert e['calls']==[]


def test_live_dispatch_exact_number_bounded_and_recording_off(env):
    e=env;item,body=e['activate']();call=e['calls'][0]
    assert call['to']=='+61400000001' and call['from_']=='+61200000000'
    assert call['time_limit']==180 and call['timeout']==20 and call['record'] is False
    assert call['url'].startswith('https://ghost.example.com/')
    assert item['status']=='awaiting_consent' and not item['consented']
    testcalls.start(e['a'],body,True);e['drain']();assert len(e['calls'])==1
    with pytest.raises(ValueError):testcalls.start(e['a'],{**body,'dry_run':True},True)
    with pytest.raises(ValueError,match='existing'):testcalls.start(e['a'],e['body'](rid='another_telephone_123456'),True)
    with pytest.raises(PermissionError):testcalls.view(item['id'],e['b'])


@pytest.mark.parametrize('digits',['','9','2'])
def test_no_consent_no_speech_or_ai(env,digits):
    e=env;item,_=e['activate']();token=item['id']
    xml=e['webhook'](token,'answer')
    assert 'input="dtmf"' in xml and 'automated AI' in xml and 'speech' not in xml.split('input=')[1].split('>')[0]
    result=e['webhook'](token,'consent',Digits=digits,SpeechResult='yes please')
    assert '<Hangup' in result and e['prompts']==[] and not testcalls.view(token)['consented']
    assert testcalls.view(token)['status']=='declined'


def test_keypad_consent_turns_are_tool_free_memory_only_and_stop(env):
    e=env;item,_=e['activate']();token=item['id'];e['webhook'](token,'answer')
    xml=e['webhook'](token,'consent',Digits='1')
    assert 'dtmf speech' in xml and 'turn/0' in xml and testcalls.view(token)['consented']
    xml=e['webhook'](token,'turn/0',SpeechResult='I can hear you clearly private test words')
    assert 'I can hear you.' in xml and 'turn/1' in xml and len(e['prompts'])==1
    assert 'private test words' not in str([dict(r) for r in store.q('SELECT * FROM testcalls')])
    assert store.q('SELECT * FROM turns')==[]
    repeat=e['webhook'](token,'turn/0',SpeechResult='I can hear you clearly private test words')
    assert repeat==xml and len(e['prompts'])==1
    end=e['webhook'](token,'turn/1',SpeechResult='please hang up')
    assert '<Hangup' in end and token not in testcalls._memory and len(e['prompts'])==1


def test_max_five_ai_turns_and_escaped_response(env,monkeypatch):
    e=env;item,_=e['activate']();token=item['id'];e['webhook'](token,'answer');e['webhook'](token,'consent',Digits='1')
    monkeypatch.setattr(testcalls,'_respond',lambda h:'Hello <Dial>000</Dial> & goodbye')
    for n in range(5):
        xml=e['webhook'](token,'turn/'+str(n),SpeechResult='keep testing')
        assert '<Dial>' not in xml and '&lt;Dial&gt;' in xml
    assert testcalls.view(token)['status']=='ended' and token not in testcalls._memory
    assert '<Gather' not in xml


def test_silence_and_ai_failure_end_test(env,monkeypatch):
    e=env;item,_=e['activate']();token=item['id'];e['webhook'](token,'answer');e['webhook'](token,'consent',Digits='1')
    xml=e['webhook'](token,'turn/0');assert '<Gather' not in xml and e['prompts']==[]
    item,_=e['activate']();token=item['id'];e['webhook'](token,'answer');e['webhook'](token,'consent',Digits='1')
    monkeypatch.setattr(testcalls,'_respond',lambda h:(_ for _ in ()).throw(TimeoutError()))
    assert 'unavailable' in e['webhook'](token,'turn/0',SpeechResult='test audio')
    assert token not in testcalls._memory


def test_queued_cancel_expired_plan_and_changed_setup_never_dial(env):
    e=env;testcalls.configure({**e['cfg'],'dry_run':False})
    body=e['body']();item=testcalls.start(e['a'],body,True);testcalls.stop(item['id'],e['a']);e['drain']()
    assert e['calls']==[] and testcalls.view(item['id'])['status']=='ended'
    body=e['body'](rid='expired_telephone_123456');e['now'][0]+=121
    with pytest.raises(ValueError,match='expired'):testcalls.start(e['a'],body,True)
    body=e['body'](rid='changed_telephone_123456');testcalls.configure({**e['cfg'],'dry_run':False,'contacts':[{**e['cfg']['contacts'][0],'name':'Changed'}]})
    with pytest.raises(ValueError,match='changed'):testcalls.start(e['a'],body,True)


def test_create_timeout_no_automatic_or_manual_replay(env,monkeypatch):
    e=env
    def fail(**kw):e['calls'].append(kw);raise TimeoutError()
    monkeypatch.setattr(e['provider'].calls,'create',fail)
    item,body=e['activate']();assert item['status']=='create_unconfirmed'
    testcalls.start(e['a'],body,True);e['drain']();assert len(e['calls'])==1
    testcalls.stop(item['id'],e['a']);e['drain']();assert testcalls.view(item['id'])['status']=='stop_unconfirmed'
    # A later signed status can bind the SID and finish the uncertain call.
    e['webhook'](item['id'],'status',CallStatus='ringing');e['drain']();assert e['updates']==[{'status':'completed'}]


def test_end_timeout_explicit_retry_and_connection_change(env,monkeypatch):
    e=env;item,_=e['activate']();token=item['id'];real=e['provider'].calls.__class__.__call__
    def fail(self,value):return SimpleNamespace(fetch=lambda:SimpleNamespace(status='in-progress'),update=lambda **kw:(_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr(e['provider'].calls.__class__,'__call__',fail)
    testcalls.stop(token,e['a']);e['drain']();assert testcalls.view(token)['status']=='stop_unconfirmed'
    monkeypatch.setattr(e['provider'].calls.__class__,'__call__',real)
    testcalls.stop(token,e['a']);e['drain']();assert testcalls.view(token)['status']=='ended'
    item,_=e['activate']();count=len(e['updates']);monkeypatch.setenv('TWILIO_FROM_NUMBER','+61200000002')
    testcalls.stop(item['id'],e['a']);e['drain']();assert len(e['updates'])==count and testcalls.view(item['id'])['status']=='stop_unconfirmed'


def test_recovery_no_new_calls_no_resumed_speech(env):
    e=env;item,_=e['activate']();token=item['id'];e['webhook'](token,'answer');e['webhook'](token,'consent',Digits='1')
    count=len(e['calls']);testcalls.recover();e['drain']()
    assert len(e['calls'])==count and e['updates']==[{'status':'completed'}] and token not in testcalls._memory
    assert '<Gather' not in e['webhook'](token,'turn/0',SpeechResult='hello')


def test_end_uses_cancel_for_ringing_call(env,monkeypatch):
    e=env;item,_=e['activate']()
    monkeypatch.setattr(e['provider'].calls.__class__,'__call__',lambda self,sid:SimpleNamespace(fetch=lambda:SimpleNamespace(status='ringing'),update=lambda **kw:e['updates'].append(kw)))
    testcalls.stop(item['id'],e['a']);e['drain']()
    assert e['updates']==[{'status':'canceled'}]


def test_config_disable_and_stop_during_ai_reply_fail_closed(env,monkeypatch):
    e=env;item,_=e['activate']();token=item['id'];e['webhook'](token,'answer');e['webhook'](token,'consent',Digits='1')
    def reply(history):
        testcalls.stop(token,e['a']);e['drain']();return 'This must not be spoken after End.'
    monkeypatch.setattr(testcalls,'_respond',reply)
    assert 'must not be spoken' not in e['webhook'](token,'turn/0',SpeechResult='testing a race')
    item,_=e['activate']();testcalls.configure({**e['cfg'],'enabled':False});e['drain']()
    assert testcalls.view(item['id'])['status']=='ended'


def test_deadline_revocation_and_setup_stop(env):
    e=env;item,_=e['activate']();e['now'][0]+=181;testcalls.tick();e['drain']()
    assert e['updates']==[{'status':'completed'}]
    item,_=e['activate']();access.set_role(e['a'],'child');store.put('household_access',True);testcalls.tick();e['drain']()
    assert testcalls.view(item['id'])['status']=='ended'
    with pytest.raises(PermissionError):testcalls.view(item['id'],e['a'])


def test_status_sid_binding_and_out_of_order_terminal(env):
    e=env;item,_=e['activate']();token=item['id']
    with pytest.raises(PermissionError):e['webhook'](token,'answer',CallSid='CA'+'3'*32)
    with pytest.raises(PermissionError):e['webhook'](token,'answer',To='+61400000002')
    e['webhook'](token,'status',CallStatus='completed');e['webhook'](token,'status',CallStatus='ringing')
    assert testcalls.view(token)['provider_status']=='completed'


def test_rate_limit_and_pruned_retry_cannot_create_new_call(env):
    e=env
    old=e['body']()
    for n in range(5):testcalls.start(e['a'],e['body'](rid='dry_call_test_request_'+str(n)))
    with pytest.raises(ValueError,match='five'):testcalls.start(e['a'],old)
    e['now'][0]+=86401;testcalls.admin_snapshot()
    assert store.q('SELECT * FROM testcalls')==[]
    with pytest.raises(ValueError,match='expired'):testcalls.start(e['a'],old)


def test_no_tools_and_actual_ai_adapter_gets_no_context_or_tools(env,monkeypatch):
    from ghost_server.tools import brain
    seen=[]
    class Model:
        def complete(self,history,**kw):seen.append((history,kw));return 'Hello',[]
    monkeypatch.setattr(brain,'claude',lambda **kw:Model())
    assert env['real_respond']([{'role':'user','content':'audio check'}])=='Hello'
    assert seen[0][1]['tools'] is None and seen[0][1]['timeout']==8
    assert not any('testcall' in d['name'] for d in tools.declarations('live'))


def test_provider_api_signatures_tamper_duplicate_fields_and_auth(env):
    e=env;client=TestClient(app);item,_=e['activate']();token=item['id']
    def signed(endpoint,params=None):
        params=params or e['params']();url=testcalls._url(token,endpoint)
        sig=RequestValidator(e['auth']).compute_signature(url,params)
        return client.post(url,data=params,headers={'X-Twilio-Signature':sig})
    assert client.get('/api/v1/testcalls').status_code==401
    assert client.get('/api/v1/testcalls',headers=e['th']).status_code==403
    assert client.post('/api/v1/testcalls/'+token+'/stop',headers=e['bh'],json={}).status_code==403
    url=testcalls._url(token,'answer')
    assert client.post(url,data=e['params']()).status_code==403
    assert signed('answer').status_code==200
    params=e['params'](AccountSid='AC'+'9'*32);assert signed('answer',params).status_code==403
    sig=RequestValidator(e['auth']).compute_signature(url,e['params']())
    assert client.post(url,data=e['params'](To='+61400000002'),headers={'X-Twilio-Signature':sig}).status_code==403
    assert client.post(url,content='CallSid=x&CallSid=y',headers={'Content-Type':'application/x-www-form-urlencoded','X-Twilio-Signature':sig}).status_code==403
    assert client.post(url,content='x='+'a'*8200,headers={'Content-Type':'application/x-www-form-urlencoded'}).status_code==403
    client.headers['X-Ghost-Admin']='1';client.post('/admin/login',json={'password':'owner-pass'})
    assert client.put('/admin/api/testcalls',headers={'X-Ghost-Admin':'','Origin':'https://other.test'},json=e['cfg']).status_code==403
