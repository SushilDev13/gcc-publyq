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


def test_english_in_devanagari_or_tamil_script_stays_english():
    # ARES hears hi-IN and ta-IN too, and writes Indian-accented English in those scripts. Every one of these used
    # to flip an English call (and restart the agent).
    for said in ('ओके', 'ओके थैंक यू', 'यस', 'नो', 'हेलो', 'प्लीज़ कन्फर्म', 'व्हाट इज़ ऑन माय प्लेट टुडे',
                 'शो मी द ओपन कंप्लेंट्स', 'what is the status of कंप्लेंट 2026-185OET',
                 'escalate this to the सैनिटरी इंस्पेक्टर', 'show me Adyar complaints, जल्दी',
                 'ஓகே', 'வாட் இஸ் தி ஸ்டேட்டஸ்', 'ஷோ மீ ஓபன் கம்ப்ளைண்ட்ஸ்'):
        assert L.detect(said, 'en') == 'en', said
    # and a Hindi/Tamil call can still be left by speaking English, even when ARES writes it in their script
    assert L.detect('व्हाट इज़ ऑन माय प्लेट टुडे', 'hi') == 'en'
    assert L.detect('ஷோ மீ தி ஓபன் கம்ப்ளைண்ட்ஸ்', 'ta') == 'en'


def test_real_hindi_and_tamil_still_switch():
    for said in ('ठीक है', 'आज क्या काम है', 'इस complaint को escalate कर दो', 'मुझे Adyar की शिकायतें दिखाओ',
                 'open complaints की list दिखाओ'):
        assert L.detect(said, 'en') == 'hi', said
    for said in ('ஆமா', 'எத்தனை புகார்கள் நிலுவையில் உள்ளன', 'இந்த complaint-ஐ escalate பண்ணுங்க',
                 'Adyar புகார்களைக் காட்டு', 'பேசி முடித்தேன்', 'பதிவு செய்யப்பட்டது'):
        assert L.detect(said, 'en') == 'ta', said


def test_naming_a_language_is_not_asking_for_it():
    for said in ('how many complaints came from Tamil Nadu Housing Board', 'is the board in Tamil or English',
                 'the complaint in Hindi Prachar Sabha street', 'notice was written in tamil nadu style'):
        assert L.detect(said, 'en') == 'en', said
    assert L.detect('सुन रही हूँ। हिंदी, अंग्रेज़ी या तमिल में बोलिए।', 'en') == 'hi'
    assert L.detect('கேட்கிறேன். தமிழ், ஆங்கிலம் அல்லது இந்தியில் பேசுங்கள்.', 'en') == 'ta'
    assert L.detect('switch to English', 'hi') == 'en' and L.detect('Hindi please', 'en') == 'hi'
    assert L.detect('speak in tamil', 'en') == 'ta' and L.detect('इंग्लिश में बोलो', 'hi') == 'en'


def test_an_english_call_never_restarts():
    cur, restarts = 'en', 0
    for said in ('hello', 'what is on my plate today', 'ओके', 'show me the oldest one', 'यस',
                 'what is the status of कंप्लेंट 2026-185OET', 'escalate this to the सैनिटरी इंस्पेक्टर',
                 'how many from Tamil Nadu Housing Board', 'थैंक यू'):
        new = L.detect(said, cur)
        restarts += new != cur
        cur = new
    assert restarts == 0


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
    cfg = captured['agent']._turn_detection['config']               # push to talk: the browser marks each turn
    assert cfg['start_of_speech'] == {'mode': 'manual'} and cfg['end_of_speech'] == {'mode': 'manual'}
    assert r['agent_uid'] == 22
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
