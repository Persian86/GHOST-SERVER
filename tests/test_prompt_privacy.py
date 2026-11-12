import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ghost_server import store, prompt, faces, household, meals, access

def test_raw_transcripts_do_not_cross_devices(tmp_path,monkeypatch):
    for module in (faces,household,meals):monkeypatch.setattr(module,'_ready',False)
    store.init(tmp_path/'scope.db');store.put('approve_new_devices',False)
    phone,_=store.redeem_pair_code(store.new_pair_code(),'Private phone','phone')
    tablet,_=store.redeem_pair_code(store.new_pair_code(),'Family tablet','tablet')
    store.add_turn(phone,'user','PRIVATE_PHONE_SENTINEL')
    store.add_turn(tablet,'assistant','PRIVATE_TABLET_SENTINEL')
    for did in (phone,tablet):
        text=prompt.build(dict(store.device(did)))
        assert 'PRIVATE_PHONE_SENTINEL' not in text
        assert 'PRIVATE_TABLET_SENTINEL' not in text
    assert store.recent_turns(8,phone)[0]['text']=='PRIVATE_PHONE_SENTINEL'
    assert store.recent_turns(8,tablet)[0]['text']=='PRIVATE_TABLET_SENTINEL'

def test_deliberately_shared_facts_remain_available(tmp_path,monkeypatch):
    for module in (faces,household,meals):monkeypatch.setattr(module,'_ready',False)
    store.init(tmp_path/'facts.db');store.put('approve_new_devices',False)
    did,_=store.redeem_pair_code(store.new_pair_code(),'Family tablet','tablet')
    store.add_fact('SHARED_HOUSEHOLD_SENTINEL','Home','owner')
    assert 'SHARED_HOUSEHOLD_SENTINEL' in prompt.build(dict(store.device(did)))
