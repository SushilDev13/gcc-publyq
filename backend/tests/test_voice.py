"""Voice call endpoints. The Agora session start is mocked: tests never spend call minutes."""
from fastapi.testclient import TestClient

from backend.app import voice
from backend.app.config import settings
from backend.app.main import app

api = TestClient(app)


def _configure(monkeypatch):
    for k, v in dict(agora_app_id='a' * 32, agora_app_certificate='b' * 32, murf_api_key='murf-test',
                     public_base_url='https://gcc.example.test', custom_llm_token='tok').items():
        monkeypatch.setattr(settings, k, v)


def test_status_reports_missing(monkeypatch):
    monkeypatch.setattr(settings, 'murf_api_key', '')
    monkeypatch.setattr(settings, 'public_base_url', '')
    r = api.get('/api/voice/status').json()
    assert not r['ready'] and {'MURF_API_KEY', 'PUBLIC_BASE_URL'} <= set(r['missing'])


def test_start_refuses_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, 'murf_api_key', '')
    body = {'channel': 'c', 'agent_uid': 2, 'user_uid': 1, 'session_id': 'test-v1', 'device_id': 'test-device-v'}
    r = api.post('/api/voice/start', json=body)
    assert r.status_code == 503 and 'MURF_API_KEY' in r.json()['detail']


def test_config_issues_rtc_token(monkeypatch):
    _configure(monkeypatch)
    r = api.get('/api/voice/config', params={'session_id': 'test-v2'}).json()
    assert r['token'] and r['uid'] > 0 and r['agent_uid'] > 0 and r['channel'].startswith('gcc-')


def test_agent_is_wired_to_brain_and_murf(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    class FakeSession:
        async def start(self):
            return 'agent-123'

    def fake_create(self, **kw):
        captured['agent'] = self
        captured['session'] = kw
        return FakeSession()

    monkeypatch.setattr(voice.AgoraAgent, 'create_async_session', fake_create)
    body = {'channel': 'gcc-x', 'agent_uid': 22, 'user_uid': 11, 'session_id': 'test-v3', 'role_id': 'AEL-D173',
            'device_id': 'test-device-v'}
    r = api.post('/api/voice/start', json=body).json()
    assert r['agent_id'] == 'agent-123' and 'Assistant Engineer (Electrical)' in r['greeting']
    _, llm_v, tts_v = voice.vendors('test-v3', 'AEL-D173', 'hi')
    llm, tts = llm_v.to_config(), tts_v.to_config()
    assert llm['url'] == 'https://gcc.example.test/v1/chat/completions?session=test-v3&lang=en&role=AEL-D173'
    assert llm['api_key'] == 'tok'
    assert tts['vendor'] == 'murf' and tts['params']['model'] == 'falcon-2.1'
    assert tts['params']['voiceId'] == 'en-IN-anisha' and tts['params']['locale'] == 'en-IN'
    assert captured['session']['remote_uids'] == ['11']
    hist = api.get('/api/conversations', params={'device_id': 'test-device-v'}).json()['items']
    assert any(c['id'] == 'test-v3' for c in hist)            # call appears in history


def test_greeting_honest_for_empty_queue():
    assert 'Nothing is open' in voice.greeting('AEL-D173')
    assert '80 open complaints' in voice.greeting('AE-D173')


def test_voice_choice_and_stt(monkeypatch):
    _configure(monkeypatch)
    stt, _, tts = voice.vendors('s', None, 'hi', 'abhinav')
    assert tts.to_config()['params']['voiceId'] == 'en-IN-abhinav' and stt.to_config()['vendor'] == 'ares'
    import pytest
    with pytest.raises(voice.VoiceError):
        voice.vendors('s', None, 'hi', 'matthew')
    body = {'channel': 'c', 'agent_uid': 2, 'user_uid': 1, 'session_id': 'test-v9', 'device_id': 'test-device-v',
            'voice': 'matthew'}
    assert api.post('/api/voice/start', json=body).status_code == 503


def test_status_lists_voices():
    s = api.get('/api/voice/status').json()
    assert [v['key'] for v in s['voices']] == ['anisha', 'abhinav'] and s['default_voice'] == 'anisha'


def test_tamil_call_config(monkeypatch):
    _configure(monkeypatch)
    stt, llm_v, tts = voice.vendors('s-ta', 'AE-D173', 'hi', 'anisha', 'ta')
    p = tts.to_config()['params']
    assert p['locale'] == 'ta-IN' and p['voiceId'] == 'en-IN-anisha' and p['model'] == 'falcon-2.1'
    assert 'lang=ta' in llm_v.to_config()['url']
    import pytest
    with pytest.raises(voice.VoiceError, match="doesn't speak ta-IN"):
        voice.vendors('s', None, 'hi', 'abhinav', 'ta')
    with pytest.raises(voice.VoiceError):
        voice.vendors('s', None, 'hi', 'anisha', 'fr')


def test_tamil_greetings_use_gcc_post_titles():
    assert voice.greeting('AE-D173', 'ta').startswith('வணக்கம், உதவிப் பொறியாளர். உங்களிடம் 80 புகார்கள் நிலுவையில்')
    assert 'நிலுவையில் புகார்கள் இல்லை' in voice.greeting('AEL-D173', 'ta')
