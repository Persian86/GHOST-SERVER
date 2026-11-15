"""Everyday timer commands against a temporary DB; no real devices or AI calls."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from ghost_server import household as hh, store
from ghost_server.tools import Ctx, TOOLS
from ghost_server.tools.household import timer

HERE=Ctx('tablet-test','Test tablet')

@pytest.fixture(autouse=True)
def temporary_household(tmp_path,monkeypatch):
    store.init(tmp_path/'timers.db')
    monkeypatch.setattr(hh,'_ready',False)
    monkeypatch.setattr(hh,'_push',lambda _:None)
    store.put('timezone','Australia/Melbourne')

def make(label='pasta',device='tablet-test',ringing=False,alarm=False):
    if alarm:
        row=hh.set_alarm('23:59',label,'every day',device)
    else:
        row=hh.set_timer(600,label,device)
    if ringing:store.x("UPDATE timers SET status='ringing',ends_at=? WHERE id=?",(time.time()-1,row['id']))
    return row['id']

def rows():return {r['id']:r for r in hh.timers(include_done=True)}

@pytest.mark.parametrize('action',['stop','add','snooze'])
def test_ambiguous_commands_ask_before_changing_anything(action):
    make('pasta',ringing=action=='snooze');make('washing',ringing=action=='snooze')
    before=rows()
    result=timer({'action':action,'minutes':5},HERE)
    assert 'Which one' in result and 'pasta' in result and 'washing' in result
    assert {k:(v['status'],v['ends_at']) for k,v in rows().items()}=={k:(v['status'],v['ends_at']) for k,v in before.items()}

def test_exact_names_win_over_partial_label_matches():
    tea=make('tea');iced=make('iced tea')
    assert 'Stopped the tea timer' in timer({'action':'stop','label':'tea'},HERE)
    assert rows()[tea]['status']=='done' and rows()[iced]['status']=='running'

def test_partial_matches_are_not_silently_group_commands():
    make('front garden');make('back garden')
    assert 'Which one' in timer({'action':'stop','label':'garden'},HERE)
    assert all(r['status']=='running' for r in rows().values())

def test_explicit_all_stops_running_and_ringing_timers():
    make('pasta',ringing=True);make('washing')
    assert timer({'action':'stop','label':'all'},HERE)=='Stopped 2.'
    assert all(r['status']=='done' for r in rows().values())

def test_all_timers_does_not_cancel_an_alarm():
    make();alarm=make('morning',alarm=True)
    assert 'Stopped' in timer({'action':'stop','label':'all timers'},HERE)
    assert rows()[alarm]['status']=='running'

def test_snooze_never_delays_a_timer_that_is_not_ringing():
    tid=make();before=rows()[tid]['ends_at']
    assert 'Nothing' in timer({'action':'snooze'},HERE)
    assert rows()[tid]['ends_at']==before

def test_only_local_ringing_timer_is_dismissed_when_other_rooms_ring():
    local=make('pasta',ringing=True);remote=make('washing',device='other-test',ringing=True)
    assert 'Stopped the pasta timer' in timer({'action':'stop'},HERE)
    assert rows()[local]['status']=='done' and rows()[remote]['status']=='ringing'

def test_unique_timer_can_still_be_controlled_from_another_device():
    tid=make(device='other-test')
    assert 'Stopped' in timer({'action':'stop'},HERE)
    assert rows()[tid]['status']=='done'

def test_dismissing_a_ringing_repeat_alarm_preserves_its_schedule():
    tid=make('morning',alarm=True,ringing=True)
    assert 'Stopped' in timer({'action':'stop','kind':'alarm'},HERE)
    row=rows()[tid]
    assert row['status']=='running' and row['days']=='mon,tue,wed,thu,fri,sat,sun'
    assert row['ends_at']>time.time()

def test_cancelling_a_future_alarm_does_not_change_other_alarms():
    first=make('morning',alarm=True);other=make('evening',alarm=True)
    assert 'Stopped' in timer({'action':'cancel','label':'morning'},HERE)
    assert rows()[first]['status']=='cancelled' and rows()[other]['status']=='running'

@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,86401,True,'nonsense',0])
def test_bad_durations_are_rejected_without_creating_a_timer(value):
    assert 'duration' in timer({'action':'set','seconds':value},HERE)
    assert rows()=={}

def test_duration_is_not_silently_clamped_or_inverted():
    assert 'duration' in timer({'action':'set','hours':48},HERE)
    assert 'duration' in timer({'action':'set','minutes':1,'seconds':-10},HERE)
    assert rows()=={}

def test_ended_timers_cannot_be_resurrected_or_reported_stopped_again():
    tid=make();assert hh.stop(tid)
    assert not hh.add_time(tid,300) and not hh.stop(tid) and not hh.cancel_alarm_forever(tid)
    assert rows()[tid]['status']=='done'

@pytest.mark.parametrize('action,method',[('stop','stop'),('add','add_time'),('snooze','add_time')])
def test_failed_updates_are_not_reported_as_success(action,method,monkeypatch):
    make(ringing=action=='snooze')
    monkeypatch.setattr(hh,method,lambda *args:False)
    result=timer({'action':action,'minutes':5},HERE)
    assert 'changed before' in result
    assert not result.startswith(('Stopped','Added','Snoozed'))

def test_concurrent_extensions_never_lose_an_acknowledged_update():
    tid=make();original=rows()[tid]['ends_at']
    with ThreadPoolExecutor(max_workers=2) as pool:
        successful=sum(pool.map(lambda _:hh.add_time(tid,300),range(2)))
    assert successful in (1,2)
    assert rows()[tid]['ends_at']==pytest.approx(original+300*successful)

def test_snooze_and_type_filter_are_exposed_to_all_assistants():
    declaration=TOOLS['timer'].parameters['properties']
    assert 'snooze' in declaration['action']['description']
    assert 'kind' in declaration
