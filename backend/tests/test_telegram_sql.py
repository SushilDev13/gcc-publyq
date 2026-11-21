"""Telegram delivery + communications ledger, and the read-only SQL guards."""
import pytest

from backend.app import dbquery, telegram
from backend.app import tools as T
from backend.app.config import settings


class FakeResp:
    def __init__(self, data):
        self._d = data
        self.status_code = 200

    def json(self):
        return self._d


def test_confirmed_escalation_posts_to_telegram(monkeypatch):
    monkeypatch.setattr(settings, 'bot_token', 'x')
    monkeypatch.setattr(settings, 'group_chat_id', '-100')
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(json)
        return FakeResp({'ok': True, 'result': {'message_id': 77}})

    monkeypatch.setattr(telegram.httpx, 'post', fake_post)
    a = T.propose_action('escalate', '2026-185OET', session_id='test-tg', role_id='AE-D173')
    assert 'Telegram' in a['say']
    c = T.confirm_action(a['id'])
    assert c['communication']['status'] == 'sent' and 'Posted to the Ward 173 Telegram group' in c['say']
    assert '2026-185OET' in sent['text'] and 'Escalation' in sent['text'] and sent['parse_mode'] == 'HTML'
    assert 'Assistant Engineer' in sent['text']                       # requested-by role
    log = T.list_communications('2026-185OET')['items'][0]
    assert log['status'] == 'sent' and log['action_id'] == a['id']


def test_telegram_failure_is_recorded_honestly(monkeypatch):
    monkeypatch.setattr(settings, 'bot_token', 'x')
    monkeypatch.setattr(settings, 'group_chat_id', '-1')
    monkeypatch.setattr(telegram.httpx, 'post',
                        lambda *a, **k: FakeResp({'ok': False, 'description': 'Bad Request: chat not found'}))
    a = T.propose_action('help_request', '2026-185OET', note='Need a JCB', session_id='test-tg2')
    c = T.confirm_action(a['id'])
    assert c['communication']['status'] == 'failed' and 'chat not found' in c['say']


def test_telegram_text_is_escaped(monkeypatch):
    a = T.propose_action('note', '2026-185OET', note='<b>x</b> & y', notify=True, session_id='test-tg3')
    body = T.telegram_message(T.confirm_action(a['id']))
    assert '&lt;b&gt;x&lt;/b&gt; &amp; y' in body


@pytest.mark.parametrize('sql', ['delete from complaints', 'select 1; drop table complaints', 'select * from turns',
                                 'with x as (delete from actions returning *) select * from x',
                                 'select * from conversations'])
def test_sql_guards(sql):
    with pytest.raises(dbquery.QueryError):
        dbquery.run(sql)


def test_sql_timeout_and_row_cap():
    with pytest.raises(dbquery.QueryError, match='3 seconds'):
        dbquery.run('select pg_sleep(4)')
    r = dbquery.run('select id from complaints')
    assert r['row_count'] == 50 and r['truncated']


def test_supergroup_migration_is_followed(monkeypatch):
    monkeypatch.setattr(settings, 'bot_token', 'x')
    monkeypatch.setattr(settings, 'group_chat_id', '-111')
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json['chat_id'])
        if json['chat_id'] == '-111':
            return FakeResp({'ok': False, 'description': 'group chat was upgraded to a supergroup chat',
                             'parameters': {'migrate_to_chat_id': -100111}})
        return FakeResp({'ok': True, 'result': {'message_id': 5}})

    monkeypatch.setattr(telegram.httpx, 'post', fake_post)
    a = T.propose_action('help_request', '2026-185OET', note='need hands', session_id='test-tg4')
    assert T.confirm_action(a['id'])['communication']['status'] == 'sent' and calls == ['-111', '-100111']
