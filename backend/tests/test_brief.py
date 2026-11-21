"""Morning brief: deterministic, factual, linkable, Telegram-safe."""
import re

from backend.app import brief


def test_tamil_brief_content():
    b = brief.build('ta')
    h = b['html']
    assert h.startswith('☀️ <b>காலை சுருக்கம் · வார்டு 173</b>')
    assert 'நிலுவையில் 96' in h and 'பாதுகாப்பு அபாயம் 26' in h
    assert '<b>உதவிப் பொறியாளர்</b> (AE-D173) · நிலுவையில் 80' in h          # GCC's Tamil post title
    assert 'நிலுவையில் புகார்கள் இல்லை ✅' in h                                  # AE-Electrical: nothing open
    assert 'AE-D173 வசம் 80' in h and '10 புகார்களுக்கு பொறுப்பாளர் பதவி இல்லை' in h
    assert b['chars'] < 4096                                                    # Telegram message limit


def test_picks_are_distinct_and_correct():
    ae = brief.role_section('AE-D173')
    ids = [p['id'] for p in ae['picks'].values()]
    assert len(ids) == len(set(ids)) == 3
    assert ae['picks']['danger']['id'] == '2026-185OET' and ae['picks']['oldest']['id'] == '2026-735GQS'


def test_links_open_the_app_in_role_and_language():
    h = brief.build('en')['html']
    assert 'https://gcc.publyq.in/?role=AE-D173&amp;lang=en&amp;ask=open%3A2026-185OET' in h
    assert 'https://gcc.publyq.in/?role=WARD&amp;lang=en&amp;ask=brief' in h
    assert 'app.publyq.in/#/complaints?range=all&amp;ward=173&amp;status=open&amp;safety=1' in h


def test_html_is_telegram_safe():
    h = brief.build('ta')['html']
    assert ' & ' not in h                                   # every ampersand escaped
    assert set(re.findall(r'</?(\w+)', h)) <= {'b', 'i', 'a'}   # only tags Telegram HTML supports
    assert 'None' not in h


from fastapi.testclient import TestClient  # noqa: E402

from backend.app import db, telegram  # noqa: E402
from backend.app.config import settings  # noqa: E402
from backend.app.main import app  # noqa: E402

api = TestClient(app)


class _Resp:
    status_code = 200

    def json(self):
        return {'ok': True, 'result': {'message_id': 9}}


def _cleanup():
    db.execute("DELETE FROM communications WHERE session_id = 'cron-morning-brief'")


def test_cron_requires_vercel_secret(monkeypatch):
    monkeypatch.setattr(settings, 'cron_secret', 's3cret')
    assert api.get('/api/cron/morning-brief').status_code == 401
    assert api.get('/api/cron/morning-brief', headers={'Authorization': 'Bearer nope'}).status_code == 401
    monkeypatch.setattr(settings, 'cron_secret', '')
    assert api.get('/api/cron/morning-brief', headers={'Authorization': 'Bearer '}).status_code == 401


def test_dry_run_previews_without_sending(monkeypatch):
    monkeypatch.setattr(settings, 'cron_secret', 's3cret')
    monkeypatch.setattr(telegram.httpx, 'post', lambda *a, **k: (_ for _ in ()).throw(AssertionError('sent!')))
    r = api.get('/api/cron/morning-brief', params={'dry_run': True, 'lang': 'en'},
                headers={'Authorization': 'Bearer s3cret'}).json()
    assert r['status'] == 'preview' and r['html'].startswith('☀️ <b>Morning brief')


def test_sends_once_per_day(monkeypatch):
    _cleanup()
    monkeypatch.setattr(settings, 'cron_secret', 's3cret')
    monkeypatch.setattr(settings, 'bot_token', 'x')
    monkeypatch.setattr(settings, 'group_chat_id', '-1')
    sent = []
    monkeypatch.setattr(telegram.httpx, 'post', lambda url, json, timeout: sent.append(json) or _Resp())
    h = {'Authorization': 'Bearer s3cret'}
    first = api.get('/api/cron/morning-brief', headers=h).json()
    second = api.get('/api/cron/morning-brief', headers=h).json()
    assert first['status'] == 'sent' and second['status'] == 'already_sent' and len(sent) == 1
    assert sent[0]['text'].startswith('☀️ <b>காலை சுருக்கம்')                    # Tamil by default
    row = db.fetchone("SELECT kind FROM communications WHERE id = %s", (first['communication_id'],))
    assert row['kind'] == 'morning_brief'
    _cleanup()
