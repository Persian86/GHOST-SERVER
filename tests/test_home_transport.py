import importlib.util
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location('ghost_home_transport_test', Path(__file__).parents[1] / 'ghost_server/home_transport.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_real_http_connection_reused_without_caching_state():
    peers, auth = [], []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def do_GET(self):
            peers.append(self.client_address[1])
            auth.append(self.headers.get('Authorization'))
            body = str(len(peers)).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    pool = module.HomeTransport()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        assert pool.request('GET', base, 'test-key', '/api/states').text == '1'
        assert pool.request('GET', base, 'test-key', '/api/states').text == '2'
        assert peers[0] == peers[1]  # Same TCP socket, fresh response each time.
        assert auth == ['Bearer test-key'] * 2
    finally:
        pool.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_failed_action_is_not_replayed():
    calls = []

    def failed(request):
        calls.append(request)
        raise httpx.ReadError('reply lost')

    pool = module.HomeTransport(lambda: httpx.Client(transport=httpx.MockTransport(failed)))
    try:
        with pytest.raises(httpx.ReadError):
            pool.request('POST', 'http://ha', 'key', '/api/services/light/toggle', json={})
        assert len(calls) == 1
    finally:
        pool.close()


def test_rotation_waits_for_active_request_and_new_credentials_use_new_client():
    entered, release = threading.Event(), threading.Event()
    clients = []

    class Client:
        closed = False

        def request(self, method, url, **kwargs):
            if len(clients) == 1:
                entered.set()
                assert release.wait(2)
            assert not self.closed
            return kwargs['headers']['Authorization']

        def close(self):
            self.closed = True

    def factory():
        client = Client()
        clients.append(client)
        return client

    pool = module.HomeTransport(factory)
    result = []
    worker = threading.Thread(target=lambda: result.append(pool.request('GET', 'http://ha', 'old', '/states')))
    worker.start()
    try:
        assert entered.wait(2)
        assert pool.request('GET', 'http://ha', 'new', '/states') == 'Bearer new'
        assert not clients[0].closed
        assert 'old' not in repr(pool._current)
    finally:
        release.set()
        worker.join(timeout=2)
        pool.close()
    assert result == ['Bearer old']
    assert all(client.closed for client in clients)


def test_server_address_change_replaces_the_pool():
    clients = []

    def factory():
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        clients.append(client)
        return client

    pool = module.HomeTransport(factory)
    try:
        pool.request('GET', 'http://first', 'key', '/')
        pool.request('GET', 'http://second', 'key', '/')
        assert len(clients) == 2
        assert clients[0].is_closed
        assert not clients[1].is_closed
    finally:
        pool.close()
