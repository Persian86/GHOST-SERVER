import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from ghost_server.tools import Ctx
from ghost_server.tools import marquee as media
from ghost_server import marquee_here as here

@pytest.fixture
def tablet(monkeypatch):
    monkeypatch.setattr(here.store,'device',lambda did:{'kind':'tablet'})
    monkeypatch.setattr(here.access,'tool_allowed',lambda *a:True)
    return Ctx('tablet','Kitchen tablet',extra={'request_id':'session:call'})

def test_movie_requests_default_to_exact_tablet_player(tablet,monkeypatch):
    calls=[]
    monkeypatch.setattr(here,'command',lambda text,ctx,request:calls.append(text) or 'queued here')
    monkeypatch.setattr(media,'_req',lambda *a,**kw:pytest.fail('Must not use fuzzy target fallback'))
    assert media.marquee({'action':'command','text':'play Bluey'},tablet)=='queued here'
    assert calls==['play Bluey']

def test_explicit_named_tv_retains_existing_remote_routing(tablet,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(here,'command',lambda *a:pytest.fail('Named TV is not this tablet'))
    sent=[]
    monkeypatch.setattr(media,'_req',lambda *a,**kw:sent.append(kw['json']) or SimpleNamespace(json=lambda:{'speech':'Sent to lounge TV'}))
    assert media.marquee({'action':'command','text':'play Bluey on lounge tv','target':'named'},tablet)=='Sent to lounge TV'
    assert sent[0]['text']=='play Bluey on lounge tv'

def test_open_routes_only_to_the_requesting_tablet_without_starting_a_movie(tablet,monkeypatch):
    sent=[]
    monkeypatch.setattr(here,'native',lambda *a,**kw:sent.append(a) or {'ok':True})
    assert 'Opening' in media.marquee({'action':'open'},tablet)
    assert sent==[('tablet','open','session:call')]

def test_open_needs_permission_and_a_current_voice_token(tablet,monkeypatch):
    monkeypatch.setattr(here,'native',lambda *a,**kw:pytest.fail('Invalid request must not navigate'))
    monkeypatch.setattr(here.access,'tool_allowed',lambda *a:False)
    assert 'No other screen' in here.open_library(tablet)
    monkeypatch.setattr(here.access,'tool_allowed',lambda *a:True);tablet.extra={}
    assert 'update' in here.open_library(tablet)

def test_a_phone_keeps_existing_named_device_behaviour(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(here.store,'device',lambda did:{'kind':'phone'})
    monkeypatch.setattr(here,'command',lambda *a:pytest.fail('Do not choose a tablet for a phone'))
    monkeypatch.setattr(media,'_req',lambda *a,**kw:SimpleNamespace(json=lambda:{'speech':'Existing route'}))
    assert media.marquee({'action':'command','text':'pause'},Ctx('phone','Phone'))=='Existing route'

def test_background_and_typed_requests_keep_their_existing_route(tablet,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(here,'command',lambda *a:pytest.fail('Only live tablet voice defaults to the embedded player'))
    monkeypatch.setattr(media,'_req',lambda *a,**kw:SimpleNamespace(json=lambda:{'speech':'Existing route'}))
    for via in ('chat','task'):
        tablet.via=via
        assert media.marquee({'action':'command','text':'pause'},tablet)=='Existing route'
