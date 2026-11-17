"""Optional local relay check. Only synthetic video; private loopback API and temporary local media port.

Set GO2RTC, FFMPEG, NODE and CHROME to local executables, then run this script.
Requires uvicorn, playwright-core and the normal server HTTP dependencies.
"""
import ast
import os
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from ghost_server import cameras, store


def main():
    os.environ['PATH'] = str(Path(os.environ['FFMPEG']).parent) + os.pathsep + os.environ.get('PATH', '')
    processes = []
    server = None
    with tempfile.TemporaryDirectory(prefix='ghost-rtc-') as directory:
        config = Path(directory) / 'go2rtc.yaml'
        media_host = os.environ.get('RTC_MEDIA_HOST') or socket.gethostbyname(socket.gethostname())
        config.write_text(f'''api:
  listen: "127.0.0.1:21984"
rtsp:
  listen: "127.0.0.1:28554"
webrtc:
  listen: ":28555"
  candidates: ["{media_host}:28555"]
  ice_servers: []
streams:
  synthetic: []
  synthetic_main: []
''', encoding='utf-8')
        log = open(Path(directory) / 'relay.log', 'w', encoding='utf-8')
        try:
            processes.append(subprocess.Popen([os.environ['GO2RTC'], '-config', str(config)], stdout=log, stderr=log))
            for _ in range(30):
                try:
                    if httpx.get('http://127.0.0.1:21984/api/streams', timeout=.5, trust_env=False).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(.1)
            for name, size in [('synthetic', '640x360'), ('synthetic_main', '960x540')]:
                processes.append(subprocess.Popen([os.environ['FFMPEG'], '-hide_banner', '-loglevel', 'error',
                    '-re', '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=10', '-an', '-c:v', 'libx264',
                    '-preset', 'ultrafast', '-tune', 'zerolatency', '-pix_fmt', 'yuv420p', '-g', '10',
                    '-f', 'rtsp', '-rtsp_transport', 'tcp', f'rtsp://127.0.0.1:28554/{name}'], stdout=log, stderr=log))
            time.sleep(1)
            values = {'camera_webrtc_enabled': True, 'camera_go2rtc_url': 'http://127.0.0.1:21984',
                'camera_direct_streams': 'Front = rtsp://127.0.0.1:28554/synthetic\nBack = rtsp://127.0.0.1:28554/synthetic',
                'camera_main_streams': 'Front = rtsp://127.0.0.1:28554/synthetic_main'}
            store.get = lambda key, default=None: values.get(key, default)
            cams = cameras.rtsp_cameras('camera_direct_streams')
            token = cameras.issue(cams)
            app = FastAPI()
            tree = ast.parse((ROOT / 'ghost_server/app.py').read_text(encoding='utf-8'))
            env = {'__package__': 'ghost_server', 'app': app, 'Request': Request, 'Response': Response,
                   'HTMLResponse': HTMLResponse, 'HTTPException': HTTPException, 'cameras': cameras}
            for name in ('cam_page', 'cam_webrtc', 'cam_live'):
                node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
                exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual-camera-route>', 'exec'), env)
            @app.get('/cam/{tok}/media')
            def media(tok: str):
                return []
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=28080, log_level='error'))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            for _ in range(30):
                if server.started:
                    break
                time.sleep(.1)
            result = subprocess.run([os.environ['NODE'], 'tests/rtc_browser_smoke.cjs',
                                     f'http://127.0.0.1:28080/cam/{token}'], cwd=ROOT, timeout=90)
            if result.returncode:
                log.flush()
                print((Path(directory) / 'relay.log').read_text(encoding='utf-8')[-5000:])
            return result.returncode
        finally:
            if server:
                server.should_exit = True
            for proc in reversed(processes):
                proc.terminate()
                proc.wait(timeout=5)
            log.close()


if __name__ == '__main__':
    raise SystemExit(main())
