import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from ghost_server import marquee_launch

@pytest.mark.parametrize('url,result',[
    ('http://media.test:5050','http://media.test:5050/'),
    ('https://media.test/marquee/','https://media.test/marquee/'),
    ('',''),('javascript:alert(1)',''),('https://user:password@media.test',''),
    ('https://media.test?token=secret',''),('https://media.test/#secret',''),
    ('https://media.test:bad',''),('http://media.test/\nsecret',''),
    ('http://localhost:5050',''),('http://127.0.0.1:5050',''),('http://[::1]:5050',''),
])
def test_launch_only_exposes_a_credential_free_application_address(url,result,monkeypatch):
    def setting(key):
        assert key in ('marquee_url','marquee_app_url'),'The launch endpoint must never read the assistant key'
        return url if key=='marquee_url' else ''
    monkeypatch.setattr(marquee_launch.store,'get',setting)
    assert marquee_launch.public_launch()=={'configured':bool(result),'url':result}

def test_tablet_address_can_override_a_server_only_container_address(monkeypatch):
    monkeypatch.setattr(marquee_launch.store,'get',lambda key:{'marquee_url':'http://marquee:8420','marquee_app_url':'https://media.test'}[key])
    assert marquee_launch.public_launch()['url']=='https://media.test/'

def test_device_access_is_checked_before_reading_configuration(monkeypatch):
    from ghost_server import app,access
    from fastapi import HTTPException
    monkeypatch.setattr(access,'tool_allowed',lambda did,tool:False)
    monkeypatch.setattr(marquee_launch,'public_launch',lambda:pytest.fail('Denied devices must not read media settings'))
    coroutine=app.marquee_launch_ep({'id':'denied-test'})
    with pytest.raises(HTTPException) as error:coroutine.send(None)
    assert error.value.status_code==403
