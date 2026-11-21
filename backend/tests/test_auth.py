"""Telegram sign-in: signed widget data, ward-group membership, sessions, self-chosen posts, Admin."""
import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import auth, db
from backend.app.config import settings
from backend.app.main import app
from backend.tests.conftest import TEST_USER_BASE, as_user

api = TestClient(app)
BOT = '123456:TEST-bot-token'
UID = TEST_USER_BASE + 42


def widget(uid=UID, age=0, **extra) -> dict:
    """A Login Widget payload signed the way Telegram signs it."""
    d = {'id': uid, 'first_name': 'Kavya', 'username': 'kavya_ae', 'auth_date': int(time.time()) - age, **extra}
    check = '\n'.join(f'{k}={d[k]}' for k in sorted(d))
    d['hash'] = hmac.new(hashlib.sha256(BOT.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    return d


@pytest.fixture
def telegram(monkeypatch):
    monkeypatch.setattr(settings, 'bot_token', BOT)
    monkeypatch.setattr(settings, 'group_chat_id', '-100123')
    monkeypatch.setattr(settings, 'admin_telegram_ids', '')
    status = {'value': 'member'}
    monkeypatch.setattr(auth, 'member_status', lambda uid: status['value'])
    return status


def sign_in(payload) -> TestClient:
    c = TestClient(app, base_url='https://gcc.example.test')
    r = c.post('/api/auth/telegram', json=payload)
    c.last = r
    return c


def test_member_signs_in_with_a_private_cookie(telegram):
    c = sign_in(widget())
    assert c.last.status_code == 200 and c.last.json()['name'] == 'Kavya'
    cookie = c.last.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'secure' in cookie and 'samesite=lax' in cookie
    assert c.get('/api/auth/me').json()['id'] == UID
    token = c.cookies.get(auth.COOKIE)
    assert db.fetchone('SELECT 1 FROM user_sessions WHERE token_hash = %s', (token,)) is None   # only the hash


def test_forged_or_replayed_widget_is_refused(telegram):
    bad = widget(); bad['first_name'] = 'Admin'                     # changed after signing
    assert sign_in(bad).last.status_code == 401
    assert sign_in(widget(age=2 * 86400)).last.status_code == 401   # older than a day
    assert sign_in({**widget(), 'hash': '0' * 64}).last.status_code == 401


def test_only_ward_group_members_get_in(telegram):
    telegram['value'] = 'left'
    r = sign_in(widget()).last
    assert r.status_code == 403 and 'not in the Ward 173 group' in r.json()['detail']
    telegram['value'] = None                                       # never joined
    assert sign_in(widget()).last.status_code == 403


def test_people_pick_their_own_post_but_not_admin(telegram):
    c = sign_in(widget())
    assert c.post('/api/auth/role', json={'role_id': 'SI-D173'}).json()['role_id'] == 'SI-D173'
    assert c.post('/api/auth/role', json={'role_id': 'ADMIN'}).status_code == 403
    assert all(r['id'] != 'ADMIN' for r in c.get('/api/roles').json()['roles'])
    r = c.post('/api/agent/turn', json={'text': "what's urgent", 'session_id': 'test-auth-1', 'role_id': 'ADMIN'})
    assert r.status_code == 403


def test_group_owner_and_listed_ids_are_admin(telegram, monkeypatch):
    telegram['value'] = 'creator'
    c = sign_in(widget())
    assert c.last.json()['is_admin']
    assert c.post('/api/auth/role', json={'role_id': 'ADMIN'}).json()['role_id'] == 'ADMIN'
    assert any(r['id'] == 'ADMIN' for r in c.get('/api/roles').json()['roles'])
    telegram['value'] = 'member'
    monkeypatch.setattr(settings, 'admin_telegram_ids', f'1, {UID + 1}')
    assert sign_in(widget(uid=UID + 1)).last.json()['is_admin']
    demoted = sign_in(widget())                                       # owner no more, not listed
    assert not demoted.last.json()['is_admin'] and demoted.last.json()['role_id'] is None


def test_sign_out_ends_the_session(telegram):
    c = sign_in(widget())
    assert c.post('/api/auth/logout').json()['ok']
    assert TestClient(app).get('/api/auth/me', headers={'Cookie': f"{auth.COOKIE}={c.cookies.get(auth.COOKIE) or 'x'}"}).status_code == 401


def test_leaving_the_group_signs_you_out(telegram):
    c = sign_in(widget())
    db.execute("UPDATE user_sessions SET verified_at = now() - interval '2 days' WHERE user_id = %s", (UID,))
    telegram['value'] = 'kicked'
    assert c.get('/api/auth/me').status_code == 401
    assert db.fetchone('SELECT count(*) n FROM user_sessions WHERE user_id = %s', (UID,))['n'] == 0


@pytest.mark.parametrize('method,path', [
    ('post', '/api/agent/turn'), ('get', '/api/conversations'), ('get', '/api/voice/config?session_id=x'),
    ('post', '/api/voice/start'), ('get', '/api/actions'), ('get', '/api/communications'),
    ('get', '/api/reports/ward'), ('post', '/api/auth/role'),
])
def test_work_needs_sign_in(method, path):
    body = {'text': 'hi', 'session_id': 'test-x', 'channel': 'c', 'agent_uid': 2, 'user_uid': 1}
    r = getattr(api, method)(path, **({'json': body} if method == 'post' else {}))
    assert r.status_code == 401


def test_public_records_stay_open():
    assert api.get('/api/complaints', params={'limit': 1}).status_code == 200
    assert api.get('/api/auth/config').status_code == 200
    assert as_user()                                                # helper works for the rest of the suite
