import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from ghost_server import store,access,security,faces,household,meals,routines,tools
from ghost_server.app import app


@pytest.fixture
def env(tmp_path,monkeypatch):
    for m in (faces,household,meals):monkeypatch.setattr(m,'_ready',False)
    monkeypatch.setattr(household,'_push',lambda *a:None)
    store.init(tmp_path/'routines.db');security.reset_all();security.end_all();store.put('approve_new_devices',False)
    def pair(name):
        did,key=store.redeem_pair_code(store.new_pair_code(),name,'desktop')
        return did,{'Authorization':'Bearer '+key}
    a,ah=pair('Personal PC');b,bh=pair('Other PC')
    jobs=[];monkeypatch.setattr(routines,'_launch',lambda *args:jobs.append(args));monkeypatch.setattr(routines,'_notify',lambda *a:None)
    c=TestClient(app);c.headers['X-Ghost-Admin']='1';assert c.post('/admin/login',json={'password':'owner-pass'}).status_code==200
    yield c,a,ah,b,bh,jobs
    security.end_all()


def save(steps,name='Focus',device_id=''):
    return routines.save({'name':name,'steps':steps,'enabled':True,'device_id':device_id})


def execute(jobs):routines._execute(*jobs.pop(0))


def test_configuration_never_runs_and_start_is_checkpointed(env):
    c,a,ah,b,bh,jobs=env
    rid=save([{'kind':'message','text':'Hello'},{'kind':'timer','seconds':60,'label':'Focus'}])
    assert jobs==[] and household.timers()==[]
    run=routines.start('focus',a);execute(jobs)
    result=routines.get_run(run['id'],a)
    assert result['status']=='done' and result['next_step']==2 and len(result['results'])==2
    assert len(household.timers())==1


def test_duplicate_start_and_same_device_overlap_do_not_repeat_steps(env):
    c,a,ah,b,bh,jobs=env
    rid=save([{'kind':'message','text':'One'}]);run=routines.start(rid,a)
    assert routines.start(rid,a)['id']==run['id'] and len(jobs)==1
    other=save([{'kind':'message','text':'Two'}],name='Other')
    with pytest.raises(ValueError):routines.start(other,a)
    execute(jobs);assert routines.start(rid,a)['id']==run['id'] and not jobs


def test_approval_pause_resumes_exact_step_without_repeating_timer(env,monkeypatch):
    c,a,ah,b,bh,jobs=env
    from ghost_server.tools import home
    store.put('household_access',True)
    monkeypatch.setattr(home,'_req',lambda *a,**k:type('R',(),{'json':lambda self:[{'entity_id':'light.study'}]})())
    calls=[];monkeypatch.setattr(home,'_call',lambda *args:calls.append(args) or 200)
    rid=save([{'kind':'timer','seconds':60,'label':'Focus'},{'kind':'home','entity_id':'light.study','action':'off'},{'kind':'message','text':'Ready'}])
    run=routines.start(rid,a);execute(jobs)
    paused=routines.get_run(run['id'],a)
    assert paused['status']=='waiting_approval' and paused['next_step']==1
    assert len(household.timers())==1 and calls==[]
    access.decide(access.pending()[0]['id'],True)
    routines.resume(run['id'],a);execute(jobs)
    final=routines.get_run(run['id'],a)
    assert final['status']=='done' and final['next_step']==3
    assert len(household.timers())==1 and len(calls)==1
    assert len(final['results'])==3


def test_restart_marks_uncertain_work_interrupted_without_replay(env):
    c,a,ah,b,bh,jobs=env
    rid=save([{'kind':'timer','seconds':60,'label':'Focus'}]);run=routines.start(rid,a)
    store.x('UPDATE routine_runs SET inflight=1 WHERE id=?',(run['id'],))
    routines.recover();execute(jobs)
    assert routines.get_run(run['id'],a)['status']=='interrupted'
    assert household.timers()==[]
    with pytest.raises(ValueError):routines.resume(run['id'],a)
    assert routines.cancel(run['id'],a)['status']=='cancelled'


def test_cancel_during_action_allows_current_result_but_no_next_step(env,monkeypatch):
    c,a,ah,b,bh,jobs=env
    rid=save([{'kind':'message','text':'One'},{'kind':'message','text':'Two'}]);run=routines.start(rid,a)
    calls=[]
    def step(*args):
        calls.append(args);routines.cancel(run['id'],a);return 'ok','Current action completed'
    monkeypatch.setattr(routines,'_step',step);execute(jobs)
    result=routines.get_run(run['id'],a)
    assert result['status']=='cancelled' and result['next_step']==1 and len(calls)==1


def test_failed_uncertain_action_is_not_resumed(env,monkeypatch):
    c,a,ah,b,bh,jobs=env
    rid=save([{'kind':'message','text':'One'}]);run=routines.start(rid,a)
    monkeypatch.setattr(routines,'_step',lambda *a:(_ for _ in ()).throw(TimeoutError()))
    execute(jobs);assert routines.get_run(run['id'],a)['status']=='failed'
    with pytest.raises(ValueError):routines.resume(run['id'],a)


def test_role_revocation_definition_changes_and_device_scope(env):
    c,a,ah,b,bh,jobs=env
    rid=save([{'kind':'message','text':'One'}],device_id=a)
    with pytest.raises(PermissionError):routines.start(rid,b)
    run=routines.start(rid,a);store.put('household_access',True);access.set_role(a,'guest');execute(jobs)
    assert routines.get_run(run['id'])['status']=='failed'
    assert 'Permission denied' in tools.run('routines',{'action':'list'},tools.Ctx(a,'PC'))
    access.set_role(a,'adult')
    rid2=save([{'kind':'message','text':'Two'}],name='Changed');run2=routines.start(rid2,b)
    routines.save({'name':'Changed','steps':[{'kind':'message','text':'New'}],'enabled':False},rid2)
    execute(jobs);assert routines.get_run(run2['id'],b)['status']=='failed'


def test_malformed_nested_or_security_steps_rejected(env):
    c,a,ah,b,bh,jobs=env
    bad=[[],[{'kind':[]}],[{'kind':'run_task'}],[{'kind':'device_action'}],[{'kind':'timer','seconds':True}],
         [{'kind':'timer','seconds':86401}],[{'kind':'message','text':'x','role':'owner'}],
         [{'kind':'home','entity_id':'lock.front','action':'off'}],[{'kind':'home','entity_id':'scene.night','action':'on'}],
         [{'kind':'home','entity_id':'light.study','action':'unlock'}],[{'kind':'briefing'}]*9]
    for steps in bad:
        with pytest.raises(ValueError):save(steps)
    assert not jobs


def test_admin_and_csrf_required_device_cannot_create_or_admin_start(env):
    c,a,ah,b,bh,jobs=env
    rid=save([{'kind':'message','text':'One'}]);public=TestClient(app)
    assert public.get('/admin/api/routines',headers=ah).status_code==401
    assert public.post('/admin/api/routines/'+rid+'/start',headers=ah,json={'device_id':a}).status_code==401
    c.headers.pop('X-Ghost-Admin')
    assert c.post('/admin/api/routines',json={'name':'Bad','steps':[{'kind':'briefing'}]}).status_code==403


def test_runs_are_bound_to_source_and_nested_automation_cannot_start(env):
    c,a,ah,b,bh,jobs=env
    rid=save([{'kind':'message','text':'One'}]);run=routines.start(rid,a)
    with pytest.raises(ValueError):routines.get_run(run['id'],b)
    with pytest.raises(ValueError):routines.cancel(run['id'],b)
    result=tools.run('routines',{'action':'start','name':'Focus'},tools.Ctx(a,'PC','task'))
    assert 'not a nested task' in result and len(jobs)==1


def test_exact_home_target_missing_does_not_infer_another_action(env,monkeypatch):
    c,a,ah,b,bh,jobs=env
    from ghost_server.tools import home
    monkeypatch.setattr(home,'_req',lambda *a,**k:type('R',(),{'json':lambda self:[]})())
    calls=[];monkeypatch.setattr(home,'_call',lambda *args:calls.append(args) or 200)
    run=routines.start(save([{'kind':'home','entity_id':'light.missing','action':'off'}]),a);execute(jobs)
    assert routines.get_run(run['id'],a)['status']=='failed' and calls==[]


def test_agent_cannot_replace_saved_steps_in_a_start_request(env):
    c,a,ah,b,bh,jobs=env
    rid=save([{'kind':'message','text':'Saved message'}])
    result=tools.run('routines',{'action':'start','name':'Focus','steps':[{'kind':'home','entity_id':'light.other','action':'toggle'}]},tools.Ctx(a,'PC'))
    run=routines.get_run(json.loads(result)['id'],a)
    assert run['steps']==[{'kind':'message','text':'Saved message'}]


def test_read_only_steps_keep_source_identity_and_standard_task_context(env,monkeypatch):
    c,a,ah,b,bh,jobs=env
    calls=[]
    monkeypatch.setattr(tools,'run',lambda name,args,ctx:calls.append((name,args,ctx.device_id,ctx.via)) or 'Read-only report')
    run=routines.start(save([{'kind':'weather','city':'Sydney'},{'kind':'briefing'}]),a);execute(jobs)
    assert calls==[('weather',{'city':'Sydney'},a,'task'),('briefing',{},a,'task')]
    assert routines.get_run(run['id'],a)['status']=='done'


def test_admin_home_options_are_read_only_and_unavailable_errors_are_generic(env,monkeypatch):
    c,a,ah,b,bh,jobs=env
    from ghost_server.tools import home
    monkeypatch.setattr(home,'_req',lambda *a,**k:type('R',(),{'json':lambda self:[{'entity_id':'light.room','attributes':{'friendly_name':'Room'}},{'entity_id':'lock.front'}]})())
    assert c.get('/admin/api/routine-home-options').json()['items']==[{'id':'light.room','name':'Room'}]
    monkeypatch.setattr(home,'_req',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('secret address and token')))
    result=c.get('/admin/api/routine-home-options').json()
    assert not result['connected'] and 'secret' not in json.dumps(result)
