import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from ghost_server import action_feedback as feedback, access
from ghost_server.tools import Ctx
from ghost_server.tools import home, devices

def state(value='off', entity='light.office', **attributes):
    return {'entity_id': entity, 'state': value, 'attributes': {'friendly_name': entity, **attributes}}

@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(access, 'authorize_action', lambda *a: None)
    monkeypatch.setattr(access, 'enabled', lambda: True)
    ctx = Ctx('tablet', 'Kitchen tablet')
    calls = []
    monkeypatch.setattr(home, '_call', lambda *a: calls.append(a) or 200)
    return ctx, calls

def snapshot_sequence(monkeypatch, *snapshots):
    sequence = iter(snapshots)
    def get():
        value = next(sequence)
        if isinstance(value, Exception): raise value
        return value
    monkeypatch.setattr(home, '_states', get)

def test_state_must_match_before_success_is_reported(setup, monkeypatch):
    ctx, calls = setup
    snapshot_sequence(monkeypatch, [state()], [state('on')])
    result = home.home_assistant({'action': 'on', 'target': 'light.office'}, ctx)
    assert result.startswith('Confirmed by Home Assistant state:')
    assert feedback.public(ctx)['phase'] == 'confirmed' and len(calls) == 1

@pytest.mark.parametrize('after', [[state()], [], [state('unavailable')], [state('unknown')]])
def test_accepted_request_with_old_missing_or_unavailable_state_is_not_done(setup, monkeypatch, after):
    ctx, calls = setup
    snapshot_sequence(monkeypatch, [state()], after)
    result = home.home_assistant({'action': 'on', 'target': 'light.office'}, ctx)
    assert 'not confirmed' in result and "Don't retry automatically" in result
    assert feedback.public(ctx)['phase'] == 'unconfirmed' and len(calls) == 1

@pytest.mark.parametrize('code,phase', [(400,'failed'), (401,'failed'), (403,'failed'), (408,'unconfirmed'), (500,'unconfirmed')])
def test_http_rejections_cannot_be_reported_as_done(setup, monkeypatch, code, phase):
    ctx, calls = setup
    monkeypatch.setattr(home, '_states', lambda: [state()])
    monkeypatch.setattr(home, '_call', lambda *a: calls.append(a) or code)
    result = home.home_assistant({'action': 'on', 'target': 'light.office'}, ctx)
    assert 'HTTP' in result and not result.startswith('Done')
    assert feedback.public(ctx)['phase'] == phase and len(calls) == 1

def test_readback_failure_is_uncertain_and_never_retries_the_write(setup, monkeypatch):
    ctx, calls = setup
    snapshot_sequence(monkeypatch, [state()], TimeoutError('secret/token'))
    assert 'may have run' in home.home_assistant({'action': 'toggle', 'target': 'light.office'}, ctx)
    assert feedback.public(ctx)['phase'] == 'unconfirmed' and len(calls) == 1
    assert 'secret' not in str(feedback.public(ctx))

def test_transport_failure_before_dispatch_is_failed(setup, monkeypatch):
    ctx, calls = setup
    snapshot_sequence(monkeypatch, TimeoutError('private address'))
    home.home_assistant({'action': 'on', 'target': 'light.office'}, ctx)
    assert feedback.public(ctx)['phase'] == 'failed' and not calls

def test_transport_failure_during_post_is_uncertain(setup, monkeypatch):
    ctx, _ = setup
    monkeypatch.setattr(home, '_states', lambda: [state()])
    def send(*a): raise TimeoutError('secret/token')
    monkeypatch.setattr(home, '_call', send)
    assert 'may have run' in home.home_assistant({'action': 'on', 'target': 'light.office'}, ctx)
    assert feedback.public(ctx)['phase'] == 'unconfirmed'
    assert 'secret' not in str(feedback.public(ctx))

def test_partial_batch_failure_never_repeats_an_earlier_toggle(setup, monkeypatch):
    ctx, calls = setup
    monkeypatch.setattr(home, '_states', lambda: [state(entity='light.office_1'), state(entity='light.office_2')])
    def send(*a): calls.append(a); return 200 if len(calls)==1 else 403
    monkeypatch.setattr(home, '_call', send)
    result=home.home_assistant({'action': 'toggle', 'target': 'office lights'}, ctx)
    assert feedback.public(ctx)['phase'] == 'unconfirmed' and len(calls) == 2
    assert 'Some changes may have run' in result

def test_approval_blocks_the_entire_batch_before_any_write(setup, monkeypatch):
    ctx, calls = setup
    monkeypatch.setattr(home, '_states', lambda: [state()])
    monkeypatch.setattr(access, 'authorize_action', lambda *a: 'Approval required')
    assert home.home_assistant({'action': 'toggle', 'target': 'light.office'}, ctx) == 'Approval required'
    assert feedback.public(ctx)['phase'] == 'blocked' and not calls

def test_security_confirmation_remains_required(setup, monkeypatch):
    ctx, calls = setup
    monkeypatch.setattr(home, '_states', lambda: [state('locked', 'lock.front')])
    assert 'CONFIRMATION NEEDED' in home.home_assistant({'action': 'unlock', 'target': 'lock.front'}, ctx)
    assert feedback.public(ctx)['phase'] == 'blocked' and not calls

@pytest.mark.parametrize('entity,parameters', [('light.office',{'temperature':22}), ('switch.office',{'brightness':50}), ('climate.office',{'temperature':22,'color':'red'})])
def test_inapplicable_properties_are_not_silently_ignored(setup, monkeypatch, entity, parameters):
    ctx, calls = setup
    monkeypatch.setattr(home, '_states', lambda:[state(entity=entity)])
    home.home_assistant({'action':'set','target':entity,**parameters},ctx)
    assert feedback.public(ctx)['phase']=='failed' and not calls

def test_scene_acceptance_is_not_claimed_as_physical_completion(setup, monkeypatch):
    ctx, calls = setup
    monkeypatch.setattr(home, '_states', lambda: [state('scening', 'scene.evening')])
    assert 'accepted' in home.home_assistant({'action': 'scene', 'target': 'scene.evening'}, ctx)
    assert feedback.public(ctx)['phase'] == 'accepted' and len(calls) == 1

def test_read_only_requests_have_no_action_receipt(setup, monkeypatch):
    ctx, calls = setup
    monkeypatch.setattr(home, '_states', lambda: [state('on')])
    home.home_assistant({'action': 'status', 'target': 'light.office'}, ctx)
    assert feedback.public(ctx) is None and not calls

@pytest.mark.parametrize('service,before,after,data,expected', [
    ('turn_on',state(),state('on'),{},True),
    ('toggle',state('on'),state('off'),{},True),
    ('toggle',state('off'),state('off'),{},False),
    ('turn_on',state(),state('on',brightness=128),{'brightness_pct':50},True),
    ('turn_on',state(),state('on',brightness=255),{'brightness_pct':50},False),
    ('turn_on',state(),state('on'),{'color_name':'red'},False),
    ('set_temperature',state(entity='climate.office'),state('heat',entity='climate.office',temperature=22),{'temperature':22},True),
    ('set_temperature',state(entity='climate.office'),state('heat',entity='climate.office',temperature=19),{'temperature':22},False),
])
def test_verification_checks_requested_properties(service,before,after,data,expected):
    item={'domain':before['entity_id'].split('.')[0],'service':service,'data':{'entity_id':before['entity_id'],**data}}
    assert feedback.home_matches(item,before,after) is expected

@pytest.mark.parametrize('reply,phase', [({'ok':True,'result':'done'},'accepted'),({'ok':False,'delivery':'not_sent'},'failed'),({'ok':False,'error':'timeout'},'unconfirmed')])
def test_remote_device_acknowledgement_is_distinct_from_state_confirmation(setup, monkeypatch, reply, phase):
    from types import SimpleNamespace
    ctx, _ = setup
    monkeypatch.setattr(devices, 'find_device', lambda name: {'id':'pc','name':'Office PC'})
    monkeypatch.setattr(devices.hub, 'conns', {'pc':SimpleNamespace(capabilities=[{'name':'lock_screen'}])})
    calls=[]
    monkeypatch.setattr(devices.hub, 'call_sync', lambda *a: calls.append(a) or reply)
    result=devices.device_action({'device':'Office PC','action':'lock_screen'},ctx)
    assert feedback.public(ctx)['phase'] == phase and len(calls) == 1
    if phase=='unconfirmed': assert "Don't retry automatically" in result

def test_feedback_is_bounded_and_never_includes_extra_request_metadata():
    ctx=Ctx(extra={'request_id':'private-correlation','raw_args':{'token':'secret'}})
    feedback.record(ctx,'confirmed','x'*100,'y'*1000)
    assert len(feedback.public(ctx)['label'])==80 and len(feedback.public(ctx)['detail'])==240
    assert 'secret' not in str(feedback.public(ctx)) and 'private-correlation' not in str(feedback.public(ctx))

def test_dispatcher_denial_has_a_blocked_receipt_and_never_calls_the_handler(monkeypatch):
    from ghost_server import tools
    ctx=Ctx('blocked')
    monkeypatch.setattr(tools, 'load', lambda: None)
    monkeypatch.setattr(access, 'tool_allowed', lambda *a: False)
    monkeypatch.setitem(tools.TOOLS,'test_denied',tools.Tool('test_denied','',{},lambda *a: pytest.fail('Blocked action ran')))
    assert 'Permission denied' in tools.run('test_denied',{},ctx)
    assert feedback.public(ctx)['phase']=='blocked'
    tools.run('unknown',{},ctx)
    assert feedback.public(ctx) is None

def test_offline_preflight_is_unsent_but_a_failed_socket_send_is_uncertain(monkeypatch):
    from ghost_server.hub import Hub
    h=Hub()
    coroutine=h.call('offline','toggle',{})
    with pytest.raises(StopIteration) as result: coroutine.send(None)
    assert result.value.value['delivery']=='not_sent'
    # No real sockets/event loop are needed: the send fails before any awaited reply.
    from types import SimpleNamespace
    monkeypatch.setattr('asyncio.get_running_loop',lambda:SimpleNamespace(create_future=lambda:object()))
    h.conns['pc']=object()
    async def send(*a): return False
    monkeypatch.setattr(h,'send',send)
    coroutine=h.call('pc','toggle',{})
    with pytest.raises(StopIteration) as result: coroutine.send(None)
    assert result.value.value['delivery']=='unknown'
    assert not h.pending and not h.pending_owner
