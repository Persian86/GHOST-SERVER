"""Opt-in one-time check-ins. Family notifications only; no emergency actuation.

Single-process scheduler. Claim before dispatch: uncertain sends are never replayed.
An unavailable server may escalate only within five minutes after the grace deadline.
"""
import asyncio
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from . import store, access
from .hub import hub

DEFAULT = {'enabled': False, 'test_mode': True, 'participants': [], 'recipients': []}
ACTIVE = ('waiting', 'reminded', 'alerted')
_lock = threading.RLock()
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='GhostCheckIn')
_last_tick = 0
SCHEMA = """CREATE TABLE IF NOT EXISTS checkin_config(id INTEGER PRIMARY KEY,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checkins(id TEXT PRIMARY KEY,source TEXT NOT NULL,request_id TEXT NOT NULL,
 digest TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL,due REAL NOT NULL,
 escalate REAL NOT NULL,expires REAL NOT NULL,status TEXT NOT NULL,UNIQUE(source,request_id));
CREATE TABLE IF NOT EXISTS checkin_deliveries(incident TEXT NOT NULL,target TEXT NOT NULL,stage TEXT NOT NULL,
 transport TEXT NOT NULL DEFAULT 'pending',received REAL NOT NULL DEFAULT 0,acknowledged REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(incident,target,stage));"""


def schema():
    store._con().executescript(SCHEMA)


def settings():
    schema()
    rows = store.q('SELECT data FROM checkin_config WHERE id=1')
    return json.loads(rows[0]['data']) if rows else {**DEFAULT}


def _phone(did, registered=True):
    d = store.device(did)
    if d is None or d['revoked'] or not d['approved'] or d['kind'] != 'phone' or '~' in did or access.limited(did):
        raise PermissionError('Use an approved, permitted personal phone.')
    if registered and json.loads(d['info'] or '{}').get('checkin_version') != 1:
        raise ValueError('Update this phone and open Check-ins once first.')
    return d


def register(did):
    _phone(did, False)
    store.touch_device(did, info={'checkin_version': 1})
    return {'ok': True}


def _enqueue(fn, *args):
    _executor.submit(fn, *args)


def _prune():
    schema()
    cutoff = time.time() - 86400
    store.x('DELETE FROM checkin_deliveries WHERE incident IN (SELECT id FROM checkins WHERE expires<?)', (cutoff,))
    store.x('DELETE FROM checkins WHERE expires<?', (cutoff,))


def configure(body):
    if not isinstance(body, dict) or set(body) != set(DEFAULT):
        raise ValueError('Save the complete Check-in setup.')
    if type(body['enabled']) is not bool or type(body['test_mode']) is not bool:
        raise ValueError('Choose on/off switches.')
    cfg = {**body}
    for key in ('participants', 'recipients'):
        values = cfg[key]
        if not isinstance(values, list) or len(values) > 10 or any(not isinstance(v, str) for v in values) or len(set(values)) != len(values):
            raise ValueError('Choose up to ten distinct phones per list.')
        for did in values:
            _phone(did)
        cfg[key] = values[:]
    if cfg['enabled'] and (not cfg['participants'] or any(not any(r != s for r in cfg['recipients']) for s in cfg['participants'])):
        raise ValueError('Every participant needs another selected recipient phone.')
    with _lock:
        schema()
        # Saving setup always cancels active timers, including already-alerted ones.
        for row in store.q("SELECT id FROM checkins WHERE status IN ('waiting','reminded','alerted')"):
            _finish(row['id'], 'cancelled_by_owner')
        store.x('INSERT INTO checkin_config VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (json.dumps(cfg),))
    return cfg


def _row(token):
    if not isinstance(token, str) or not re.fullmatch(r'ci_[a-f0-9]{24}', token):
        raise ValueError('Unknown Check-in.')
    rows = store.q('SELECT * FROM checkins WHERE id=?', (token,))
    if not rows:
        raise ValueError('Unknown Check-in.')
    return rows[0]


def _authorized(row, did):
    _phone(did)
    cfg = settings()
    if did == row['source'] and did in cfg['participants']:
        return
    if did in cfg['recipients'] and store.q("SELECT 1 FROM checkin_deliveries WHERE incident=? AND target=? AND stage='missed'", (row['id'], did)):
        return
    raise PermissionError('This Check-in is not shared with this phone.')


def _name(did):
    d = store.device(did)
    return d['name'] if d else 'Removed phone'


def _signature(did, cfg, issued):
    key = store.get('checkin_plan_key')
    if not key:
        key = secrets.token_hex(32)
        store.put('checkin_plan_key', key)
    value = json.dumps([did, cfg, issued], sort_keys=True).encode()
    return hmac.new(key.encode(), value, hashlib.sha256).hexdigest()


def _plan_token(did, cfg):
    issued = str(int(time.time()))
    return issued + ':' + _signature(did, cfg, issued)


def _validate_plan(did, cfg, token):
    if not isinstance(token, str) or not re.fullmatch(r'\d{10,12}:[a-f0-9]{64}', token):
        raise ValueError('Refresh and review a new Check-in.')
    issued, signature = token.split(':')
    if not -5 <= time.time() - int(issued) <= 120 or not hmac.compare_digest(signature, _signature(did, cfg, issued)):
        raise ValueError('Confirmation expired or setup changed. Refresh and review again.')


def view(token, did=None):
    row = _row(token)
    if did is not None:
        _authorized(row, did)
        if time.time() > row['expires']:
            raise PermissionError('This Check-in has expired.')
    payload = json.loads(row['payload'])
    deliveries = [dict(d) for d in store.q('SELECT target,stage,transport,received,acknowledged FROM checkin_deliveries WHERE incident=?', (token,))]
    for d in deliveries:
        d['name'] = _name(d.pop('target'))
    return {'id': token, 'source_name': _name(row['source']), 'own': did == row['source'],
            'test': payload['test'], 'created': row['created'], 'due': row['due'], 'escalate': row['escalate'],
            'expires': row['expires'], 'status': row['status'], 'deliveries': deliveries,
            'can_finish': did == row['source'] and row['status'] in ACTIVE,
            'can_acknowledge': did != row['source'] and did is not None and row['status'] == 'alerted' and bool(store.q("SELECT 1 FROM checkin_deliveries WHERE incident=? AND target=? AND stage='missed' AND transport IN ('sending','sent_to_connection','not_confirmed','unconfirmed')", (token, did))),
            'emergency_services_contacted': False}


def start(did, body):
    required = {'request_id', 'minutes', 'grace_minutes', 'test', 'setup_token'}
    if not isinstance(body, dict) or set(body) != required:
        raise ValueError('Choose a one-time Check-in and grace period.')
    rid = body['request_id']
    if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', rid):
        raise ValueError('Use a stable request ID for retry.')
    if type(body['test']) is not bool or type(body['minutes']) is not int or type(body['grace_minutes']) is not int:
        raise ValueError('Use whole minutes and a Test switch.')
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    with _lock:
        _phone(did)
        cfg = settings()
        if not cfg['enabled'] or did not in cfg['participants']:
            raise PermissionError('Check-ins are disabled or not enabled for this phone.')
        old = store.q('SELECT id,digest FROM checkins WHERE source=? AND request_id=?', (did, rid))
        if old:
            if old[0]['digest'] != digest:
                raise ValueError('Retry exactly the same Check-in details.')
            return view(old[0]['id'], did)
        _validate_plan(did, cfg, body['setup_token'])
        if not _last_tick or time.time() - _last_tick >= 15:
            raise ValueError('Check-in scheduler is unavailable. Try again after refreshing.')
        test = cfg['test_mode'] or body['test']
        if not (1 if test else 5) <= body['minutes'] <= 1440 or not 1 <= body['grace_minutes'] <= 60:
            raise ValueError('Check-in: 5–1440 minutes (TEST allows 1); grace: 1–60 minutes.')
        now = time.time()
        _prune()
        if store.q("SELECT id FROM checkins WHERE source=? AND status IN ('waiting','reminded','alerted') AND expires>?", (did, now)):
            raise ValueError('Finish or cancel the existing Check-in first.')
        if store.q('SELECT COUNT(*) n FROM checkins WHERE source=? AND created>?', (did, now - 3600))[0]['n'] >= 20:
            raise ValueError('Too many Check-ins; try later.')
        recipients = []
        for target in cfg['recipients']:
            if target != did:
                _phone(target)
                recipients.append(target)
        if not recipients:
            raise ValueError('Select another eligible family phone in the owner dashboard.')
        token = 'ci_' + secrets.token_hex(12)
        due = now + body['minutes'] * 60
        escalate = due + body['grace_minutes'] * 60
        payload = {'test': test, 'recipients': recipients}
        con = store._con()
        with con:
            con.execute('INSERT INTO checkins VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (token, did, rid, digest, json.dumps(payload), now, due, escalate, escalate + 3600, 'waiting'))
            con.execute('INSERT INTO checkin_deliveries(incident,target,stage) VALUES(?,?,?)', (token, did, 'reminder'))
            con.executemany('INSERT INTO checkin_deliveries(incident,target,stage) VALUES(?,?,?)', [(token, r, 'missed') for r in recipients])
        return view(token, did)


def inbox(did):
    _phone(did)
    with _lock:
        _prune()
        cfg = settings()
        items = []
        for row in store.q("SELECT id FROM checkins WHERE expires>? AND (source=? OR id IN (SELECT incident FROM checkin_deliveries WHERE target=? AND stage='missed')) ORDER BY CASE WHEN status IN ('waiting','reminded','alerted') THEN 0 ELSE 1 END,created DESC LIMIT 100", (time.time(), did, did)):
            try:
                items.append(view(row['id'], did))
            except PermissionError:
                pass
        recipients = []
        for target in cfg['recipients']:
            try:
                if target != did:
                    _phone(target)
                    recipients.append({'id': target, 'name': _name(target)})
            except (PermissionError, ValueError):
                pass
        return {'enabled': cfg['enabled'], 'can_start': cfg['enabled'] and did in cfg['participants'] and bool(recipients) and not any(i['own'] and i['status'] in ACTIVE for i in items),
                'test_mode': cfg['test_mode'], 'setup_token': _plan_token(did, cfg), 'recipients': recipients, 'items': items,
                'server_time': time.time(), 'scheduler_healthy': bool(_last_tick and time.time() - _last_tick < 15)}


def _finish(token, status):
    row = _row(token)
    if row['status'] not in ACTIVE:
        return
    store.x('UPDATE checkins SET status=? WHERE id=?', (status, token))
    # A closure notice is useful only for a family phone whose missed alert was attempted.
    targets = store.q("SELECT target FROM checkin_deliveries WHERE incident=? AND stage='missed' AND transport IN ('sending','sent_to_connection','not_confirmed','unconfirmed')", (token,))
    store.x("UPDATE checkin_deliveries SET transport='not_sent' WHERE incident=? AND transport='pending'", (token,))
    for target in targets:
        store.x("INSERT OR IGNORE INTO checkin_deliveries(incident,target,stage) VALUES(?,?,'update')", (token, target['target']))
        _enqueue(_dispatch, token, target['target'], 'update')


def mark(token, did, action, body):
    if not isinstance(body, dict) or set(body) - {'stage'}:
        raise ValueError('Unsupported Check-in action details.')
    with _lock:
        row = _row(token)
        _authorized(row, did)
        if time.time() > row['expires']:
            raise ValueError('This Check-in expired.')
        if action in ('safe', 'cancel'):
            if did != row['source']:
                raise PermissionError('Only the participant can finish their Check-in.')
            _finish(token, 'safe' if action == 'safe' else 'cancelled')
        elif action in ('received', 'acknowledge'):
            stage = body.get('stage', 'missed')
            if stage not in ('reminder', 'missed', 'update'):
                raise ValueError('Unsupported notification stage.')
            delivery = store.q('SELECT * FROM checkin_deliveries WHERE incident=? AND target=? AND stage=?', (token, did, stage))
            if not delivery or delivery[0]['transport'] not in ('sending','sent_to_connection','not_confirmed','unconfirmed'):
                raise PermissionError('This notification has not been sent to this phone.')
            if action == 'acknowledge' and (stage != 'missed' or did == row['source'] or row['status'] != 'alerted'):
                raise ValueError('Only an active missed Check-in can be acknowledged by family.')
            field = 'received' if action == 'received' else 'acknowledged'
            store.x(f'UPDATE checkin_deliveries SET {field}=CASE WHEN {field}=0 THEN ? ELSE {field} END WHERE incident=? AND target=? AND stage=?', (time.time(), token, did, stage))
        else:
            raise ValueError('Unsupported Check-in action.')
        return view(token, did)


def cancel_by_owner(token):
    with _lock:
        _finish(token, 'cancelled_by_owner')
        return view(token)


def _dispatch(token, target, stage):
    with _lock:
        row = _row(token)
        expected = {'reminder': 'reminded', 'missed': 'alerted'}
        cfg = settings()
        if time.time() > row['expires'] or (stage in expected and row['status'] != expected[stage]):
            return
        if stage == 'reminder' and time.time() >= row['escalate']:
            return
        if stage == 'missed' and time.time() > row['escalate'] + 300:
            return
        try:
            _phone(target)
            if stage != 'update':
                _phone(row['source'])
                if not cfg['enabled'] or row['source'] not in cfg['participants']:
                    return
            if target != row['source'] and target not in cfg['recipients']:
                return
        except (PermissionError, ValueError):
            store.x("UPDATE checkin_deliveries SET transport='not_sent' WHERE incident=? AND target=? AND stage=? AND transport='pending'", (token, target, stage))
            return
        delivery = store.q('SELECT transport FROM checkin_deliveries WHERE incident=? AND target=? AND stage=?', (token, target, stage))
        if not delivery or delivery[0]['transport'] != 'pending':
            return
        store.x("UPDATE checkin_deliveries SET transport='sending' WHERE incident=? AND target=? AND stage=?", (token, target, stage))
    # Cancellation may occur once transmission has begun; the latest inbox is authoritative.
    try:
        sent = hub.send_sync(target, {'type': 'notify', 'action': 'checkin_request', 'checkin_id': token, 'stage': stage,
                                     'title': 'Ghost check-in', 'text': 'Open Ghost to review a check-in and its current status.'})
        transport = 'sent_to_connection' if sent else 'not_confirmed'
    except Exception:
        transport = 'unconfirmed'
    with _lock:
        store.x('UPDATE checkin_deliveries SET transport=? WHERE incident=? AND target=? AND stage=?', (transport, token, target, stage))


def recover():
    global _last_tick
    with _lock:
        _last_tick = 0
        schema()
        store.x("UPDATE checkin_deliveries SET transport='unconfirmed' WHERE transport='sending'")


def tick():
    global _last_tick
    with _lock:
        schema()
        now = time.time()
        for row in store.q("SELECT * FROM checkins WHERE status IN ('waiting','reminded','alerted')"):
            cfg = settings()
            try:
                _phone(row['source'])
                allowed = cfg['enabled'] and row['source'] in cfg['participants']
            except (PermissionError, ValueError):
                allowed = False
            if not allowed:
                _finish(row['id'], 'cancelled_access_changed')
                continue
            if now >= row['expires']:
                _finish(row['id'], 'expired')
            elif row['status'] in ('waiting', 'reminded') and now >= row['escalate']:
                if now > row['escalate'] + 300:
                    _finish(row['id'], 'recovery_missed')
                    continue
                store.x("UPDATE checkins SET status='alerted' WHERE id=?", (row['id'],))
            elif row['status'] == 'waiting' and now >= row['due']:
                store.x("UPDATE checkins SET status='reminded' WHERE id=?", (row['id'],))
            # Pending work may resume after restart, but a claimed/attempted send never replays.
            current = _row(row['id'])
            stage = 'reminder' if current['status'] == 'reminded' else 'missed' if current['status'] == 'alerted' else None
            if stage:
                if stage == 'missed' and now > current['escalate'] + 300:
                    store.x("UPDATE checkin_deliveries SET transport='not_sent' WHERE incident=? AND stage='missed' AND transport='pending'", (row['id'],))
                else:
                    for d in store.q("SELECT target FROM checkin_deliveries WHERE incident=? AND stage=? AND transport='pending'", (row['id'], stage)):
                        _enqueue(_dispatch, row['id'], d['target'], stage)
        _last_tick = time.time()


async def scheduler_loop():
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:
            pass  # The dashboard exposes a stale scheduler heartbeat.
        await asyncio.sleep(2)


def admin_snapshot():
    with _lock:
        _prune()
        return {'settings': settings(), 'server_time': time.time(),
                'scheduler_healthy': bool(_last_tick and time.time() - _last_tick < 15),
                'devices': [{'id': d['id'], 'name': d['name'], 'ready': d.get('info', {}).get('checkin_version') == 1, 'approved': bool(d['approved'])}
                            for d in store.devices() if d['kind'] == 'phone' and not access.limited(d['id'])],
                'items': [view(r['id']) for r in store.q('SELECT id FROM checkins ORDER BY created DESC LIMIT 50')]}
