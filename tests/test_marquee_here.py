import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
import pytest
from ghost_server import marquee_here as here

class Response:
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data

@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(here.store, 'device', lambda did: {'kind': 'tablet'})
    monkeypatch.setattr(here.access, 'tool_allowed', lambda *a: True)
    ctx = SimpleNamespace(device_id='this-tablet~room', extra={'request_id': 'session:call'})
    calls, commands = [], []
    def native(device, action, token, **args):
        calls.append((device, action, token, args))
        return {'ok': True, 'client_id': 'exact-player', 'connection_id': 'connection'}
    monkeypatch.setattr(here, 'native', native)
    def request(method, path, **args):
        commands.append((method, path, args))
        return Response({'strictClientTarget': 1, 'ghostHold': 1} if method == 'GET' else {'ok': True, 'action': {'requestId': 'receipt'}})
    return ctx, calls, commands, request

def test_exact_request_and_confirmation(setup):
    ctx, calls, commands, request = setup
    assert 'Queued on this tablet' in here.command('play Bluey here', ctx, request)
    assert [x[1] for x in calls] == ['prepare', 'confirm']
    assert all(x[0] == 'this-tablet' for x in calls)
    assert commands[1][2]['json'] == {'text': 'play Bluey', 'targetClientId': 'exact-player', 'targetConnectionId': 'connection', 'voiceCallId': 'session:call'}

@pytest.mark.parametrize('reason', ['phone', 'denied', 'no-token'])
def test_requires_own_tablet_and_permission(setup, monkeypatch, reason):
    ctx, calls, commands, request = setup
    if reason == 'phone': monkeypatch.setattr(here.store, 'device', lambda did: {'kind': 'phone'})
    if reason == 'denied': monkeypatch.setattr(here.access, 'tool_allowed', lambda *a: False)
    if reason == 'no-token': ctx.extra = {}
    assert 'No other screen' in here.command('play here', ctx, request)
    assert not calls and not commands

def test_older_marquee_cannot_ignore_target_and_play_elsewhere(setup):
    ctx, calls, _, _ = setup
    assert 'Update Marquee' in here.command('play here', ctx, lambda *a, **kw: Response({}))
    assert not calls

def test_pronouns_are_resolved_before_playing_a_literal_movie_named_it(setup):
    ctx, calls, commands, request = setup
    assert 'Which film or show' in here.command('play it here', ctx, request)
    assert not calls and not commands

def test_missing_player_does_not_post_to_marquee(setup, monkeypatch):
    ctx, _, commands, request = setup
    def offline(*a, **kw): raise RuntimeError('Offline')
    monkeypatch.setattr(here, 'native', offline)
    assert 'Couldn\'t confirm' in here.command('play here', ctx, request)
    assert len(commands) == 1 and commands[0][0] == 'GET'

def test_uncertain_http_result_cancels_without_retry(setup):
    ctx, calls, commands, request = setup
    def timeout(method, path, **kw):
        if method == 'POST': raise TimeoutError('uncertain')
        return request(method, path, **kw)
    assert 'don\'t retry automatically' in here.command('play here', ctx, timeout)
    assert [x[1] for x in calls] == ['prepare', 'cancel']

def test_unconfirmed_receipt_is_cancelled(setup, monkeypatch):
    ctx, calls, _, request = setup
    original = here.native
    def native(device, action, token, **args):
        if action == 'confirm': raise RuntimeError('No receipt')
        return original(device, action, token, **args)
    monkeypatch.setattr(here, 'native', native)
    assert 'Couldn\'t confirm' in here.command('play here', ctx, request)
    assert calls[-1][1] == 'cancel'

def test_rejected_or_information_requests_do_not_claim_playback(setup):
    ctx, calls, _, request = setup
    def rejected(method, path, **kw):
        if method == 'POST': return Response({'ok': False, 'speech': 'Time limit reached.'})
        return request(method, path, **kw)
    assert here.command('play here', ctx, rejected) == 'Time limit reached.'
    assert calls[-1][1] == 'cancel'

def test_native_requires_a_successful_structured_receipt(monkeypatch):
    monkeypatch.setattr(here.hub, 'call_sync', lambda *a, **kw: {'ok': True, 'result': '{"ok":false,"error":"Sign in"}'})
    with pytest.raises(RuntimeError, match='Sign in'): here.native('tablet', 'prepare', 'session:call')
