import asyncio
from types import SimpleNamespace

import httpx
import pytest

from ghost_server import camera_rtc, cameras, store

OFFER = 'v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=recvonly\r\n'
ANSWER = 'v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=sendonly\r\n'
LOW = 'rtsp://viewer:secret@192.0.2.20:554/Preview_01_sub'
HIGH = 'rtsp://viewer:secret@192.0.2.20:554/Preview_01_main'


@pytest.fixture
def settings(monkeypatch):
    values = {'camera_webrtc_enabled': True, 'camera_go2rtc_url': 'http://relay:1984',
              'camera_direct_streams': 'Front Door = ' + LOW, 'camera_main_streams': 'Front Door = ' + HIGH}
    monkeypatch.setattr(store, 'get', lambda key, default=None: values.get(key, default))
    return values


def cam():
    return cameras.Cam('camera.front_door', 'Front Door', 'ha')


def test_matching_main_and_preview_do_not_expose_sources(settings):
    assert camera_rtc.source(cam()) == LOW
    assert camera_rtc.source(cam(), 'main') == HIGH
    assert camera_rtc.options([cam()]) == [{'enabled': True, 'main': True}]
    with pytest.raises(camera_rtc.CameraRTCError):
        camera_rtc.source(cam(), 'rtsp://attacker')
    settings['camera_direct_streams'] = 'camera.front_door = ' + LOW
    assert camera_rtc.source(cam()) == LOW
    settings['camera_webrtc_enabled'] = False
    assert camera_rtc.options([cam()])[0]['enabled'] is False


def test_new_direct_cameras_and_ha_duplicates(settings, monkeypatch):
    settings['camera_direct_streams'] += '\nGate = rtsp://192.0.2.30/Preview_01_sub'
    monkeypatch.setattr(cameras, 'ha_cameras', lambda: [cam()])
    rows = cameras.all_cameras()
    assert [c.name for c in rows] == ['Front Door', 'Gate']
    assert rows[0].source == 'ha'  # Existing snapshots/recordings stay attached to the HA camera.
    assert rows[1].source == 'rtsp'


def test_runtime_registration_and_sdp_proxy_use_only_server_sources(settings, caplog):
    caplog.set_level('INFO', logger='httpx')
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == 'GET':
            return httpx.Response(404)
        if request.method == 'PATCH':
            assert request.url.params['src'] == HIGH
            assert request.url.params['name'].startswith('ghost_')
            return httpx.Response(200)
        assert request.method == 'POST'
        assert request.url.params['src'].startswith('ghost_')
        assert request.content.decode() == OFFER
        return httpx.Response(201, text=ANSWER)

    factory = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    assert asyncio.run(camera_rtc.answer(cam(), OFFER, 'main', factory)) == ANSWER
    assert [r.method for r in requests] == ['GET', 'PATCH', 'POST']
    assert all(r.method != 'PUT' for r in requests)  # Camera secrets are not persisted by go2rtc.
    assert 'secret' not in caplog.text


def test_existing_relay_stream_is_reused(settings):
    methods = []

    def handle(request):
        methods.append(request.method)
        return httpx.Response(200, text='{}' if request.method == 'GET' else ANSWER)

    factory = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    asyncio.run(camera_rtc.answer(cam(), OFFER, client_factory=factory))
    assert methods == ['GET', 'POST']


@pytest.mark.parametrize('offer', ['bad', OFFER + 'm=audio 9 RTP/AVP 0\r\n', OFFER.replace('recvonly', 'sendrecv'), OFFER + 'x' * 65536])
def test_rejects_invalid_or_sending_offers(settings, offer):
    with pytest.raises(camera_rtc.CameraRTCError):
        asyncio.run(camera_rtc.answer(cam(), offer))


def test_relay_failures_do_not_leak_credentials(settings):
    def handle(_):
        return httpx.Response(500, text='failed to open ' + LOW)

    factory = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kw)
    with pytest.raises(camera_rtc.CameraRTCError) as error:
        asyncio.run(camera_rtc.answer(cam(), OFFER, client_factory=factory))
    assert 'secret' not in str(error.value) and LOW not in str(error.value)


def test_rendered_player_has_only_flags_and_scoped_paths(settings):
    token = cameras.issue([cam()])
    page = cameras.page(token, [cam()])
    assert 'RTCPeerConnection' in page and '/webrtc/' in page
    assert '__CAMERA_' not in page
    assert LOW not in page and HIGH not in page and 'viewer:secret' not in page


def test_secret_settings_are_masked(monkeypatch):
    monkeypatch.setattr(store, 'q', lambda *a: [{'k': 'camera_direct_streams', 'v': __import__('json').dumps(LOW)},
                                               {'k': 'camera_main_streams', 'v': __import__('json').dumps(HIGH)}])
    result = store.all_settings()
    assert result['camera_direct_streams'].startswith('•••')
    assert result['camera_main_streams'].startswith('•••')
    assert 'secret' not in str(result)


def test_offer_stream_is_bounded():
    async def chunks():
        yield b'x' * 65536
        yield b'x'
    with pytest.raises(camera_rtc.CameraRTCError):
        asyncio.run(camera_rtc.read_offer(SimpleNamespace(stream=chunks)))


@pytest.mark.parametrize('status', [200, 403, 500])
def test_relay_setup_check_is_safe(settings, status):
    factory = lambda **kw: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text=LOW)), **kw)
    ok, message = camera_rtc.check_relay(factory)
    assert ok is (status == 200)
    assert LOW not in message and 'secret' not in message


def test_route_checks_expiry_index_and_origin_before_signalling(settings, monkeypatch):
    # Compile the actual route to isolate it from unrelated server startup integrations.
    import ast
    from pathlib import Path
    from fastapi import FastAPI, HTTPException, Request, Response
    tree = ast.parse((Path(__file__).parents[1] / 'ghost_server/app.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'cam_webrtc')
    node.decorator_list = []
    env = {'__package__': 'ghost_server', 'Request': Request, 'Response': Response,
           'HTTPException': HTTPException, 'cameras': cameras}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<camera-route>', 'exec'), env)
    app = FastAPI()
    app.post('/cam/{tok}/webrtc/{i}')(env['cam_webrtc'])
    called = []

    async def answer(*args):
        called.append(args)
        return ANSWER

    monkeypatch.setattr(camera_rtc, 'answer', answer)
    token = cameras.issue([cam()])

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://ghost') as client:
            assert (await client.post('/cam/expired/webrtc/0')).status_code == 410
            assert (await client.post(f'/cam/{token}/webrtc/2')).status_code == 410
            url = f'/cam/{token}/webrtc/0'
            assert (await client.post(url, content=OFFER)).status_code == 403
            assert (await client.post(url, content=OFFER, headers={'X-Ghost-Camera': '1', 'Origin': 'http://other'})).status_code == 403
            assert not called
            response = await client.post(url + '?quality=main', content=OFFER, headers={'X-Ghost-Camera': '1'})
            assert response.status_code == 200 and response.text == ANSWER
            assert response.headers['cache-control'] == 'no-store'
            assert called[0][2] == 'main'
    asyncio.run(check())
