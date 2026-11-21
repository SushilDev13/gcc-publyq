"""English / Tamil / Hindi: detected from the words, switched automatically, confirmed in code."""
import json

from fastapi.testclient import TestClient

from backend.app import lang as L
from backend.app import tools as T
from backend.app import voice
from backend.app.config import settings
from backend.app.main import app
from backend.tests.conftest import as_user
from backend.tests.test_agent_e2e import say

api = TestClient(app)


def test_script_decides_the_language():
    assert L.detect('இன்று என்ன வேலை இருக்கிறது?') == 'ta'
    assert L.detect('आज मेरे पास कितनी शिकायतें हैं?') == 'hi'
    assert L.detect('what is on my plate today?') == 'en'
    assert L.detect('2026-185OET की स्थिति क्या है') == 'hi'          # an ID inside Hindi stays Hindi


def test_short_latin_keeps_the_current_language():
    assert L.detect('ok', 'ta') == 'ta' and L.detect('confirm', 'hi') == 'hi'
    assert L.detect('open 2026-185OET', 'hi') == 'hi'


def test_asking_for_a_language_switches():
    assert L.detect('please reply in Hindi', 'en') == 'hi'
    assert L.detect('தமிழில் சொல்லுங்கள்', 'hi') == 'ta'
    assert L.detect('अंग्रेज़ी में बताइए', 'hi') == 'en'


def test_hindi_greeting_uses_post_title():
    g = voice.greeting('AE-D173', 'hi')
    assert g.startswith('नमस्ते, सहायक अभियंता. आपके पास 80 शिकायतें खुली हैं')
    assert 'कोई शिकायत खुली नहीं' in voice.greeting('AEL-D173', 'hi')


def _configure(monkeypatch):
    for k, v in dict(agora_app_id='a' * 32, agora_app_certificate='b' * 32, murf_api_key='murf-test',
                     public_base_url='https://gcc.example.test', custom_llm_token='tok').items():
        monkeypatch.setattr(settings, k, v)


def test_every_call_hears_three_languages(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    class FakeSession:
        async def start(self):
            return 'agent-hi'

    def fake_create(self, **kw):
        captured['agent'] = self
        return FakeSession()

    monkeypatch.setattr(voice.AgoraAgent, 'create_async_session', fake_create)
    body = {'channel': 'gcc-h', 'agent_uid': 22, 'user_uid': 11, 'session_id': 'test-hi1', 'role_id': 'AE-D173',
            'lang': 'hi'}
    r = api.post('/api/voice/start', json=body, headers=as_user('test-device-h')).json()
    assert r['lang'] == 'hi' and r['greeting'].startswith('नमस्ते')
    assert captured['agent']._turn_detection['language'] == ['en-IN', 'ta-IN', 'hi-IN']
    _, _, tts = voice.vendors('s', None, 'x', 'anisha', 'hi')
    assert tts.to_config()['params']['locale'] == 'hi-IN'


def test_restart_opens_with_the_saved_answer(monkeypatch):
    _configure(monkeypatch)
    seen = {}

    async def fake_start(*a):
        seen['hello'] = a[-1]
        return {'agent_id': 'x', 'greeting': a[-1]}

    monkeypatch.setattr(voice, 'start', fake_start)
    out = say('what is on my plate today?', 'test-hi2', role='AE-D173')
    tid = out['done']['turn_id']
    body = {'channel': 'c', 'agent_uid': 3, 'user_uid': 1, 'session_id': 'test-hi2', 'lang': 'en', 'greet_turn_id': tid}
    me = as_user('test-device-1')
    assert api.post('/api/voice/start', json=body, headers=me).json()['greeting'] == out['text']
    other = {**body, 'session_id': 'test-hi3'}
    assert api.post('/api/voice/start', json=other, headers=me).status_code == 404     # only this conversation's turns
    assert api.post('/api/voice/start', json=body, headers=as_user('test-stranger')).status_code == 404


def test_call_holds_when_the_speaker_switches_language(monkeypatch):
    monkeypatch.setattr(settings, 'custom_llm_token', 'secret')
    msgs = {'messages': [{'role': 'user', 'content': 'मेरे पास कितनी शिकायतें खुली हैं, बताइए'}], 'stream': True}
    with api.stream('POST', '/v1/chat/completions', params={'session': 'test-hi4', 'role': 'AE-D173', 'lang': 'en'},
                    json=msgs, headers={'Authorization': 'Bearer secret'}) as r:
        lines = [json.loads(x[6:]) for x in r.iter_lines() if x.startswith('data: {')]
    spoken = ''.join(c['choices'][0]['delta'].get('content', '') for c in lines)
    assert spoken == voice.HOLD['en']                 # the English voice doesn't attempt Hindi
    t = T.db.fetchone("SELECT ctx->>'lang' lang, reply FROM turns WHERE session_id = 'test-hi4'")
    assert t['lang'] == 'hi' and t['reply']           # the answer is saved for the restarted agent


def test_hindi_confirm_is_deterministic():
    a = T.propose_action('note', '2026-185OET', note='site visit', session_id='test-hi-confirm')
    out = say('हाँ', 'test-hi-confirm')
    assert out['route'] == 'control' and out['cards'][0]['id'] == a['id'] and out['cards'][0]['status'] == 'confirmed'
    T.propose_action('note', '2026-185OET', note='x', session_id='test-hi-confirm')
    assert say('नहीं।', 'test-hi-confirm')['cards'][0]['status'] == 'cancelled'
