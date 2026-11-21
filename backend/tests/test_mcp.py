"""MCP endpoint: protocol handshake, tools, auth, and the two-phase write rule."""
import pytest
from fastapi.testclient import TestClient

from backend.app import db
from backend.app.config import settings
from backend.app.main import app

api = TestClient(app)
H = {'Authorization': 'Bearer tok', 'Accept': 'application/json, text/event-stream'}


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setattr(settings, 'mcp_token', 'tok')
    yield
    db.execute("DELETE FROM communications WHERE session_id = 'mcp'")
    db.execute("DELETE FROM actions WHERE session_id = 'mcp'")


def rpc(method, params=None, rid=1, headers=H):
    return api.post('/mcp', json={'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params or {}},
                    headers=headers)


def call(name, args):
    return rpc('tools/call', {'name': name, 'arguments': args}).json()['result']


def test_auth_and_disabled(monkeypatch):
    assert rpc('ping', headers={}).status_code == 401
    assert rpc('ping', headers={'Authorization': 'Bearer nope'}).status_code == 401
    monkeypatch.setattr(settings, 'mcp_token', '')
    assert rpc('ping').status_code == 503


def test_initialize_negotiates_version():
    r = rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                           'clientInfo': {'name': 't', 'version': '1'}}).json()['result']
    assert r['protocolVersion'] == '2025-06-18' and r['capabilities'] == {'tools': {'listChanged': False}}
    assert r['serverInfo']['name'] == 'gcc-publyq' and 'Ward 173' in r['instructions']
    r = rpc('initialize', {'protocolVersion': '1999-01-01'}).json()['result']
    assert r['protocolVersion'] == '2026-07-28'


def test_notifications_and_methods():
    n = api.post('/mcp', json={'jsonrpc': '2.0', 'method': 'notifications/initialized'}, headers=H)
    assert n.status_code == 202 and n.content == b''
    assert rpc('nope').json()['error']['code'] == -32601
    assert api.get('/mcp', headers=H).status_code == 405
    assert api.post('/mcp', json=[{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}], headers=H).status_code == 400


def test_tools_list_has_annotations():
    tools = {t['name']: t for t in rpc('tools/list').json()['result']['tools']}
    assert {'my_queue', 'patterns', 'query_db', 'get_complaint', 'propose_action', 'confirm_action',
            'open_in_publyq'} <= set(tools)
    assert tools['query_db']['annotations']['readOnlyHint'] is True
    assert tools['confirm_action']['annotations']['destructiveHint'] is True
    assert tools['my_queue']['inputSchema']['properties']['role']['enum'][-1] == 'WARD'


def test_role_scoped_read_and_sql_guard():
    q = call('my_queue', {'role': 'AEL-D173'})
    assert not q['isError'] and q['structuredContent']['total'] == 0
    assert call('my_queue', {'role': 'AE-D173'})['structuredContent']['total'] == 80
    assert call('query_db', {'sql': 'delete from actions'})['isError']
    assert call('get_complaint', {'complaint_id': '2026-185OET'})['structuredContent']['publyq']['url'].endswith(
        'id=2026-185OET')
    assert call('get_complaint', {'bogus': 1})['isError']                      # unknown argument


def test_two_phase_write_through_mcp():
    p = call('propose_action', {'kind': 'help_request', 'complaint_id': '2026-185OET', 'note': 'need a JCB',
                                'role_id': 'AE-D173'})['structuredContent']
    assert p['status'] == 'proposed' and 'confirm' in p['say'].lower()
    c = call('confirm_action', {'action_id': p['id']})['structuredContent']
    assert c['status'] == 'confirmed' and c['communication']['status'] == 'not_configured'   # tests: no Telegram


def test_cannot_confirm_an_action_from_another_channel():
    from backend.app import tools as T
    a = T.propose_action('note', '2026-185OET', note='x', session_id='test-web-only')
    r = call('confirm_action', {'action_id': a['id']})
    assert r['isError'] and 'not proposed through MCP' in r['content'][0]['text']
    db.execute("DELETE FROM actions WHERE session_id = 'test-web-only'")
