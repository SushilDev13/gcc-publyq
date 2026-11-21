"""Each test replays an attack that worked before the hardening pass; all must stay closed."""
import pytest
from fastapi.testclient import TestClient

from backend.app import db, dbquery
from backend.app import tools as T
from backend.app.config import settings
from backend.app.main import app

api = TestClient(app)
VICTIM, ATTACKER = {'X-Device-Id': 'test-device-victim'}, {'X-Device-Id': 'test-device-attacker'}


def _victim_conversation(sid='test-sec-victim') -> str:
    with api.stream('POST', '/api/agent/turn', json={'text': 'open 2026-185OET', 'session_id': sid},
                    headers=VICTIM) as r:
        list(r.iter_lines())
    return sid


# ── conversation history belongs to one device ──────────────────────────
def test_history_is_private_to_its_device():
    sid = _victim_conversation()
    assert api.get(f'/api/conversations/{sid}', headers=VICTIM).status_code == 200
    assert api.get(f'/api/conversations/{sid}', headers=ATTACKER).status_code == 404
    assert all(c['id'] != sid for c in api.get('/api/conversations', headers=ATTACKER).json()['items'])
    assert api.delete(f'/api/conversations/{sid}', headers=ATTACKER).status_code == 404
    assert api.get('/api/conversations').status_code == 401                    # no device, no history


def test_cannot_continue_someone_elses_conversation():
    sid = _victim_conversation()
    r = api.post('/api/agent/turn', json={'text': 'what did we discuss?', 'session_id': sid}, headers=ATTACKER)
    assert r.status_code == 404
    assert db.fetchone('SELECT count(*) n FROM turns WHERE session_id = %s', (sid,))['n'] == 1


def test_cannot_start_a_call_into_someone_elses_conversation():
    sid = _victim_conversation()
    assert api.get('/api/voice/config', params={'session_id': sid}, headers=ATTACKER).status_code == 404
    body = {'channel': 'c', 'agent_uid': 2, 'user_uid': 1, 'session_id': sid}
    assert api.post('/api/voice/start', json=body, headers=ATTACKER).status_code == 404
    assert db.fetchone('SELECT device_id FROM conversations WHERE id = %s', (sid,))['device_id'] == VICTIM['X-Device-Id']


# ── actions: only the proposing conversation's owner decides ─────────────
def test_stranger_cannot_confirm_or_cancel_an_action():
    sid = _victim_conversation()
    a = T.propose_action('note', '2026-185OET', note='victim note', session_id=sid)
    for op in ('confirm', 'cancel'):
        assert api.post(f"/api/actions/{a['id']}/{op}", headers=ATTACKER).status_code == 404
        assert api.post(f"/api/actions/{a['id']}/{op}").status_code == 401
    assert db.fetchone('SELECT status FROM actions WHERE id = %s', (a['id'],))['status'] == 'proposed'
    assert api.post(f"/api/actions/{a['id']}/confirm", headers=VICTIM).json()['status'] == 'confirmed'


def test_cannot_plant_a_proposal_in_someone_elses_conversation():
    sid = _victim_conversation()
    r = api.post('/api/actions', json={'kind': 'escalate', 'complaint_id': '2026-185OET', 'session_id': sid},
                 headers=ATTACKER)
    assert r.status_code == 404 and not T.latest_proposed(sid)


# ── the LLM's SQL can't reach chats or switch role ───────────────────────
@pytest.mark.parametrize('sql', [
    "select set_config('role','none',true) r, query_to_xml('select count(*) as n from turns', true, false, '') x",
    "select pg_catalog.set_config('role', 'none', true)",
    'select U&"\\0073et_config"(\'role\', \'none\', true)',
    "select database_to_xml(true, false, '')",
    "select text from turns limit 1",
    "select device_id from conversations limit 1",
    "select current_setting('role')",
])
def test_llm_sql_cannot_read_conversations(sql):
    with pytest.raises(dbquery.QueryError):
        dbquery.run(sql)


def test_llm_sql_still_answers_record_questions():
    r = dbquery.run("select count(*) n from complaints where status_group = 'open'")
    assert r['row_count'] == 1


# ── secrets: unset means off, wrong means 401 ───────────────────────────
def test_brain_endpoint_fails_closed_without_token(monkeypatch):
    monkeypatch.setattr(settings, 'custom_llm_token', '')
    body = {'messages': [{'role': 'user', 'content': "what's urgent"}], 'stream': False}
    assert api.post('/v1/chat/completions', json=body).status_code == 503
    assert api.post('/v1/chat/completions', json=body, headers={'Authorization': 'Bearer '}).status_code == 503


def test_mcp_and_cron_reject_wrong_or_missing_secrets(monkeypatch):
    monkeypatch.setattr(settings, 'mcp_token', 'm-secret')
    monkeypatch.setattr(settings, 'cron_secret', '')
    assert api.post('/mcp', json={}, headers={'Authorization': 'Bearer nope'}).status_code == 401
    assert api.get('/api/cron/morning-brief', headers={'Authorization': 'Bearer '}).status_code == 401


def test_secrets_never_leave_the_server():
    s = api.get('/api/voice/status').json()
    blob = str(s) + str(api.get('/api/health').json())
    for secret in (settings.murf_api_key, settings.agora_app_certificate, settings.custom_llm_token,
                   settings.anthropic_api_key, settings.bot_token):
        assert not secret or secret not in blob
    assert api.get('/web/../.env').status_code == 404 and api.get('/.env').status_code == 404
    assert api.get('/openapi.json').status_code == 404                          # no schema dump


# ── browser hardening ───────────────────────────────────────────────────
def test_cross_origin_browsers_get_no_grant():
    r = api.options('/api/conversations', headers={'Origin': 'https://evil.example',
                                                   'Access-Control-Request-Method': 'GET'})
    assert r.headers.get('access-control-allow-origin') in (None, '')


def test_security_headers_and_no_caching_of_private_data():
    r = api.get('/api/conversations', headers=VICTIM)
    assert r.headers['x-frame-options'] == 'DENY' and r.headers['x-content-type-options'] == 'nosniff'
    assert r.headers['cache-control'] == 'no-store'
