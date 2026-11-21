"""REST + two-phase workflow: propose -> confirm -> auto follow-up -> report."""
from fastapi.testclient import TestClient

from backend.app import db
from backend.app.main import app
from backend.tests.conftest import as_user, owner_of

api = TestClient(app)
SID = 'test-api'
WHO = 'test-device-api'


def _own_session():
    global ME
    ME = as_user(WHO)
    db.execute("INSERT INTO conversations (id, device_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (SID, owner_of(WHO)))


def test_health():
    r = api.get('/api/health').json()
    assert r['ok'] and r['complaints'] == 455


def test_complaints_filter_and_404():
    r = api.get('/api/complaints', params={'safety': True, 'limit': 5}).json()
    assert r['total'] == 26 and all(i['is_safety_risk'] for i in r['items'])
    assert api.get('/api/complaints/2026-000XXX').status_code == 404


def test_escalate_requires_confirm_then_schedules_follow_up():
    _own_session()
    a = api.post('/api/actions', json={'kind': 'escalate', 'complaint_id': '2026-185OET', 'session_id': SID},
                 headers=ME).json()
    assert a['status'] == 'proposed' and a['target_post_id'] == 'AEE3-Z13'
    assert 'confirm' in a['say'].lower()

    c = api.post(f"/api/actions/{a['id']}/confirm", headers=ME).json()
    assert c['status'] == 'confirmed' and c['follow_up']['kind'] == 'follow_up'

    again = api.post(f"/api/actions/{a['id']}/confirm", headers=ME)
    assert again.status_code == 400  # cannot confirm twice

    rep = api.get('/api/reports/complaint/2026-185OET', headers=ME).json()['markdown']
    assert f"#{a['id']} escalate" in rep and 'not written back to the GCC portal' in rep


def test_cannot_escalate_resolved_complaint():
    closed = api.get('/api/complaints', params={'status_group': 'resolved', 'limit': 1}).json()['items'][0]
    _own_session()
    r = api.post('/api/actions', json={'kind': 'escalate', 'complaint_id': closed['id'], 'session_id': SID},
                 headers=ME)
    assert r.status_code == 400


def test_cancel_and_unknown_post():
    _own_session()
    a = api.post('/api/actions', json={'kind': 'note', 'complaint_id': '2026-185OET', 'note': 'site visit',
                                       'session_id': SID}, headers=ME).json()
    assert api.post(f"/api/actions/{a['id']}/cancel", headers=ME).json()['status'] == 'cancelled'
    bad = api.post('/api/actions', json={'kind': 'assign', 'complaint_id': '2026-185OET',
                                         'target_post_id': 'NOPE-1', 'session_id': SID}, headers=ME)
    assert bad.status_code == 404


def test_media_path_traversal_blocked():
    assert api.get('/media/../../.env').status_code == 404


def test_ward_brief():
    r = api.get('/api/reports/ward', headers=as_user()).json()
    assert 'Ward 173' in r['say'] and '96 open' in r['say']
