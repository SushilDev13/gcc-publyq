"""Role-centred experience and chat history."""
from fastapi.testclient import TestClient

from backend.app import roles as R
from backend.app import tools as T
from backend.app.main import app
from backend.tests.test_agent_e2e import say

api = TestClient(app)


def test_users_example_sentence_electrical_engineer():
    r = say('As an Assistant Engineer - Electrical, what are the high priority complaints that are in the '
            'system and what are the recurring patterns we hear', 'test-ael')
    assert r['intent'] == 'my_queue+patterns'
    assert r['done']['role'] == 'AEL-D173'
    assert 'no open complaints' in r['text']                      # true in the records: 0 open
    assert 'overhead cables at gandhi Nagar 4th Main Road' in r['text']
    assert {c['type'] for c in r['cards']} == {'queue', 'patterns'}


def test_role_persists_across_turns_and_scopes_answers():
    sid = 'test-role-persist'
    r = say('I am the sanitary inspector', sid)
    assert r['intent'] == 'set_role' and r['done']['role'] == 'SI-D173'
    r = say("what's on my plate", sid)
    assert r['done']['role'] == 'SI-D173' and r['cards'][0]['total'] == 1


def test_leadership_scope_includes_reports():
    assert set(R.scope('AEE3-Z13')) >= {'AEE3-Z13', 'AE-D173'}
    assert set(R.scope('ADE1-Z13')) == {'ADE1-Z13', 'AEL-D173'}
    assert R.scope(R.WARD) is None
    q = T.my_queue(None, 'Ward leadership')
    assert 'no owner post' in q['say']                             # hoarding complaints: no Revenue post


def test_field_role_does_not_get_unowned_warning():
    assert 'no owner post' not in T.my_queue(R.scope('AE-D173'), 'Assistant Engineer', leadership=False)['say']


def test_ui_role_choice_overrides():
    r = say("what's urgent", 'test-ui-role', role='ADE1-Z13')
    assert r['done']['role'] == 'ADE1-Z13' and 'no open complaints' in r['text']


def test_confirm_by_id_targets_that_action_only():
    sid = 'test-confirm-id'
    say('open 2026-185OET', sid)
    a1 = say('escalate it', sid)['cards'][0]['id']
    a2 = say('remind me about it in 3 days', sid)['cards'][0]['id']
    r = say(f'Confirm #{a1}', sid)                                 # older proposal, not the latest
    assert r['cards'][0]['id'] == a1 and r['cards'][0]['status'] == 'confirmed'
    assert T.db.fetchone('SELECT status FROM actions WHERE id = %s', (a2,))['status'] == 'proposed'
    other = say(f'Confirm #{a2}', 'test-confirm-other')            # another conversation can't confirm it
    assert other['intent'] == 'confirm_none'


def test_open_word_does_not_reopen_focus():
    sid = 'test-focus-bug'
    say('open 2026-185OET', sid)
    r = say('show open complaints in Indira Nagar', sid)
    assert r['intent'] == 'search'


def test_history_list_load_delete():
    dev = 'test-device-hist'
    say("what's urgent", 'test-hist-1', device=dev)
    say('open the first one', 'test-hist-1', device=dev)
    items = api.get('/api/conversations', params={'device_id': dev}).json()['items']
    assert [c['id'] for c in items] == ['test-hist-1'] and items[0]['turns'] == 2
    conv = api.get('/api/conversations/test-hist-1', params={'device_id': dev}).json()
    assert len(conv['turns']) == 2 and conv['turns'][1]['cards'][0]['type'] == 'complaint'
    assert api.get('/api/conversations/test-hist-1', params={'device_id': 'someone-else-x'}).status_code == 404
    assert api.delete('/api/conversations/test-hist-1', params={'device_id': dev}).json()['ok']
    assert api.get('/api/conversations', params={'device_id': dev}).json()['items'] == []


def test_roles_endpoint():
    rs = {r['id']: r for r in api.get('/api/roles').json()['roles']}
    assert rs['AE-D173']['open'] == 80 and rs['AEL-D173']['open'] == 0 and rs['WARD']['open'] == 96
