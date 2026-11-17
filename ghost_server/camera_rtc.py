"""Private go2rtc signalling bridge. Camera addresses never reach the player."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import weakref
from urllib.parse import urlsplit

import httpx

from . import store

MAX_SDP = 65536
_locks = weakref.WeakKeyDictionary()


class _PrivateCameraLogs(logging.Filter):
    def filter(self, record):
        # HTTPX logs request URLs at INFO. Registration has an RTSP source in
        # its query string, so keep that source out of application logs too.
        if isinstance(record.args, tuple):
            record.args = tuple(arg.copy_set_param('src', '[private-camera-source]')
                                if isinstance(arg, httpx.URL) and arg.path.endswith('/api/streams')
                                and 'src' in arg.params else arg for arg in record.args)
        return True


logging.getLogger('httpx').addFilter(_PrivateCameraLogs())


class CameraRTCError(Exception):
    pass


def _key(name):
    return re.sub(r'[^a-z0-9]', '', str(name).lower())


def _mapping(setting):
    rows = {}
    for line in str(store.get(setting) or '').splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        name, url = (s.strip() for s in line.split('=', 1))
        if name and _rtsp(url):
            rows[_key(name)] = url
    return rows


def _rtsp(url):
    try:
        p = urlsplit(url)
        return p.scheme in ('rtsp', 'rtsps') and bool(p.hostname) and bool(p.port or 554)
    except ValueError:
        return False


def relay_url():
    value = str(store.get('camera_go2rtc_url') or '').strip().rstrip('/')
    try:
        p = urlsplit(value)
        if p.scheme in ('http', 'https') and p.hostname and not (p.username or p.password or p.query or p.fragment):
            return value
    except ValueError:
        pass
    raise CameraRTCError('Check the direct camera relay address in Ghost Server settings.')


def source(cam, quality='preview'):
    if quality not in ('preview', 'main'):
        raise CameraRTCError('Unknown camera quality.')
    direct = _mapping('camera_direct_streams')
    low = direct.get(_key(cam.id)) or direct.get(_key(cam.name))
    if not low and cam.source == 'rtsp' and _rtsp(cam.url):
        low = cam.url
    if quality == 'main':
        main = _mapping('camera_main_streams')
        return main.get(_key(cam.id)) or main.get(_key(cam.name)) or low
    return low


def options(cams):
    enabled = store.get('camera_webrtc_enabled') is True
    return [{'enabled': enabled and bool(source(cam)),
             'main': enabled and bool(source(cam)) and source(cam, 'main') != source(cam)} for cam in cams]


def validate_offer(offer):
    # Receive-only video: no microphone, upload track or arbitrary relay command.
    lines = offer.replace('\r', '').split('\n')
    media = [line for line in lines if line.startswith('m=')]
    if (not offer.startswith('v=0') or len(offer.encode()) > MAX_SDP or len(media) != 1
            or not media[0].startswith('m=video ') or 'a=recvonly' not in lines
            or any(line in ('a=sendonly', 'a=sendrecv') for line in lines)):
        raise CameraRTCError('Invalid receive-only camera offer.')


async def answer(cam, offer, quality='preview', client_factory=None):
    if store.get('camera_webrtc_enabled') is not True:
        raise CameraRTCError('Direct camera playback is not enabled.')
    validate_offer(offer)
    url = source(cam, quality)
    if not url:
        raise CameraRTCError('Add a direct stream for this camera in Ghost Server settings.')
    base = relay_url()
    name = 'ghost_' + hashlib.sha256(url.encode()).hexdigest()[:24]
    loop = asyncio.get_running_loop()
    lock = _locks.setdefault(loop, asyncio.Lock())
    factory = client_factory or httpx.AsyncClient
    try:
        async with asyncio.timeout(10), factory(timeout=httpx.Timeout(10, connect=3), trust_env=False, follow_redirects=False) as client:
            async with lock:
                check = await client.get(base + '/api/streams', params={'src': name})
                if check.status_code == 404:
                    # PATCH is runtime-only; PUT would write camera secrets into
                    # the relay configuration. Never accept a source from a device.
                    check = await client.patch(base + '/api/streams', params={'name': name, 'src': url})
                if check.status_code >= 300:
                    raise CameraRTCError('The direct camera relay could not prepare this feed.')
            response = await client.post(base + '/api/webrtc', params={'src': name}, content=offer,
                                         headers={'Content-Type': 'application/sdp'})
            if response.status_code >= 300:
                raise CameraRTCError('The direct feed could not connect. Check camera access and H.264 settings.')
            if len(response.content) > MAX_SDP or not response.text.startswith('v=0'):
                raise CameraRTCError('The direct camera relay returned an invalid answer.')
            return response.text
    except CameraRTCError:
        raise
    except Exception:
        # HTTPX exceptions and upstream errors may contain credential-bearing URLs.
        raise CameraRTCError('The direct camera relay is unavailable. Check that go2rtc is running.') from None


async def read_offer(request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_SDP:
            raise CameraRTCError('Camera offer is too large.')
    try:
        return body.decode('utf-8')
    except UnicodeDecodeError:
        raise CameraRTCError('Invalid camera offer.') from None


def check_relay(client_factory=None):
    try:
        factory = client_factory or httpx.Client
        with factory(timeout=3, trust_env=False, follow_redirects=False) as client:
            response = client.get(relay_url() + '/api/streams')
            if response.status_code == 200:
                return True, 'Private camera relay connected. Save your stream links and reopen Cameras.'
        return False, 'The private camera relay did not accept the connection. Check its address and setup.'
    except Exception:
        return False, 'Could not reach the private camera relay. Check that go2rtc is running on Ghost Server.'
