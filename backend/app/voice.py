"""Agora ConvoAI voice sessions.

Adapted from Agora's official Python quickstart (agent-quickstart-python,
server/src/agent.py + server.py): same token builder, turn detection, RTM data
channel and start/stop lifecycle. Changed only the documented vendor blocks:
  - LLM  -> our own brain at /v1/chat/completions (OpenAI-style, role + session
            + the call's spoken language in the URL, CUSTOM_LLM_TOKEN as bearer)
  - TTS  -> Murf Falcon 2.1 (model "falcon-2.1", the only model Murf serves
            over its stream-input WebSocket), voice en-IN-anisha, locale = the
            language the call is speaking (en-IN, ta-IN or hi-IN)
  - STT  -> Agora ARES hearing en-IN, ta-IN and hi-IN in the same call (Agora
            sends the list as one comma-separated asr.language). The managed
            Deepgram preset timed out on this project (agent-error asr 1000).

Language switching is automatic. ARES hears all three languages, but a running
agent's TTS can't be changed (Agora's update takes llm and token only). So when
the speaker changes language, the brain says a short holding line, the browser
restarts the agent with the new locale, and the new agent's greeting is the
answer (see main.voice_start greet_turn_id and app.js switchLanguage).
"""
from __future__ import annotations

import logging
import random
import time
from urllib.parse import urlencode

from agora_agent import Area, AsyncAgora
from agora_agent.agentkit import Agent as AgoraAgent
from agora_agent.agentkit.token import generate_convo_ai_token
from agora_agent.agentkit.vendors import AresSTT, MurfTTS, OpenAI

from . import roles as R
from .config import settings

log = logging.getLogger('gcc.voice')

# The brain ignores this prompt (it answers from its own tools), but ConvoAI
# requires instructions; keep it truthful in case it is ever surfaced.
INSTRUCTIONS = ('You are GCC Publyq, a voice assistant for Greater Chennai Corporation Ward 173 staff. '
                'Answers come from the connected GCC Publyq service.')


# Murf voices verified on this account via GET /v1/speech/voices (both en-IN, Conversational).
VOICES = {'anisha': {'id': 'en-IN-anisha', 'label': 'Anisha (en-IN, female)'},
          'abhinav': {'id': 'en-IN-abhinav', 'label': 'Abhinav (en-IN, male)'}}
DEFAULT_VOICE = 'anisha'
# Murf: Anisha speaks en-IN and ta-IN (checked via GET /v1/speech/voices); hi-IN for her is NOT yet verified on
# this account (check that endpoint; if missing, pick a Murf voice that lists all three). Abhinav is English only,
# so a call that may switch language uses Anisha.
LANGS = {'en': {'code': 'en-IN', 'voices': ('anisha', 'abhinav')},
         'ta': {'code': 'ta-IN', 'voices': ('anisha',)},
         'hi': {'code': 'hi-IN', 'voices': ('anisha',)}}
DEFAULT_LANG = 'en'
HEARS = [v['code'] for v in LANGS.values()]          # ARES listens for all three in every call
# Spoken by the old agent while the call switches language (English office words every caller knows).
HOLD = {'en': 'One moment.', 'ta': 'ஒரு நிமிடம்.', 'hi': 'एक पल।'}
FAILURE = {'en': 'Sorry, I could not reach the ward records. Please try again.',
           'ta': 'மன்னிக்கவும், வார்டு பதிவுகளை அணுக முடியவில்லை. மீண்டும் முயற்சிக்கவும்.',
           'hi': 'माफ़ कीजिए, वार्ड के रिकॉर्ड तक नहीं पहुँच पाई। फिर से कोशिश कीजिए।'}


class VoiceError(RuntimeError):
    pass


def configured() -> dict:
    missing = [k for k, v in {'AGORA_APP_ID': settings.agora_app_id,
                              'AGORA_APP_CERTIFICATE': settings.agora_app_certificate,
                              'MURF_API_KEY': settings.murf_api_key,
                              'PUBLIC_BASE_URL': settings.public_base_url,
                              'CUSTOM_LLM_TOKEN': settings.custom_llm_token}.items() if not v]
    return {'ready': not missing, 'missing': missing}


def _client() -> AsyncAgora:
    return AsyncAgora(area=Area.US, app_id=settings.agora_app_id, app_certificate=settings.agora_app_certificate)


def connection_config(session_id: str) -> dict:
    """RTC + RTM token for the browser (quickstart /get_config)."""
    if not (settings.agora_app_id and settings.agora_app_certificate):
        raise VoiceError('Agora credentials are not configured')
    user_uid = random.randint(1000, 9999999)          # RTM subjects can't be 0
    agent_uid = random.randint(10000000, 99999999)
    channel = f'gcc-{session_id[-12:]}-{int(time.time())}'
    token = generate_convo_ai_token(app_id=settings.agora_app_id, app_certificate=settings.agora_app_certificate,
                                    channel_name=channel, uid=user_uid, token_expire=3600)
    return {'app_id': settings.agora_app_id, 'token': token, 'uid': user_uid, 'channel': channel,
            'agent_uid': agent_uid}


def greeting(role_id: str | None, lang: str = DEFAULT_LANG) -> str:
    r = R.describe(role_id)
    if lang == 'ta':
        if r['open']:
            return (f"வணக்கம், {r['label_ta']}. உங்களிடம் {r['open']} புகார்கள் நிலுவையில் உள்ளன"
                    + (f", அவற்றில் {r['open_safety']} பாதுகாப்பு அபாயம்" if r['open_safety'] else '')
                    + '. என்ன உதவி வேண்டும்?')
        return (f"வணக்கம், {r['label_ta']}. உங்களிடம் நிலுவையில் புகார்கள் இல்லை. "
                'மீண்டும் வரும் பிரச்சினைகள், ஒரு புகார், அல்லது புதிய தகவல் பற்றிக் கேளுங்கள்.')
    if lang == 'hi':
        if r['open']:
            return (f"नमस्ते, {r['label_hi']}. आपके पास {r['open']} शिकायतें खुली हैं"
                    + (f", जिनमें {r['open_safety']} सुरक्षा जोखिम हैं" if r['open_safety'] else '')
                    + '। बताइए, क्या मदद करूँ?')
        return (f"नमस्ते, {r['label_hi']}. आपके पास कोई शिकायत खुली नहीं है। "
                'बार-बार आने वाली समस्याओं, किसी शिकायत, या नई सूचना के बारे में पूछिए।')
    if r['open']:
        return (f"Hello {r['label']}. You have {r['open']} open complaints"
                + (f", {r['open_safety']} flagged safety risk" if r['open_safety'] else '') + '. What do you need?')
    return f"Hello {r['label']}. Nothing is open on your desk. Ask me about patterns, a complaint, or a new report."


def vendors(session_id: str, role_id: str | None, hello: str, voice: str = DEFAULT_VOICE,
            lang: str = DEFAULT_LANG):
    """STT / LLM / TTS blocks of the agent (the only part changed from the quickstart)."""
    brain_url = (settings.public_base_url.rstrip('/') + '/v1/chat/completions?'
                 + urlencode({'session': session_id, 'lang': lang, **({'role': role_id} if role_id else {})}))
    llm = OpenAI(api_key=settings.custom_llm_token, base_url=brain_url, model='gcc-publyq',
                 greeting_message=hello, failure_message=FAILURE.get(lang, FAILURE['en']), max_history=10)
    if lang not in LANGS:
        raise VoiceError(f"Unknown language {lang!r}; choose {', '.join(LANGS)}")
    if voice not in VOICES:
        raise VoiceError(f"Unknown voice {voice!r}; choose {', '.join(VOICES)}")
    if voice not in LANGS[lang]['voices']:
        raise VoiceError(f"{VOICES[voice]['label'].split(' (')[0]} doesn't speak {LANGS[lang]['code']}; "
                         f"choose {', '.join(LANGS[lang]['voices'])}")
    tts = MurfTTS(key=settings.murf_api_key, model='falcon-2.1', voice_id=VOICES[voice]['id'],
                  locale=LANGS[lang]['code'],
                  base_url='wss://global.api.murf.ai/v1/speech/stream-input', sample_rate=24000)
    stt = AresSTT(keywords=['Adyar', 'Kasturba Nagar', 'Indira Nagar', 'Gandhi Nagar', 'Thiruvanmiyur',
                            'Kamaraj Nagar', 'Venkatarathinam Nagar', 'escalate', 'sanitary inspector'])
    return stt, llm, tts


async def start(channel: str, agent_uid: int, user_uid: int, session_id: str, role_id: str | None,
                voice: str = DEFAULT_VOICE, lang: str = DEFAULT_LANG, hello: str | None = None) -> dict:
    """Start the agent. `hello` replaces the greeting when the call restarts to switch language: it is the
    answer the person is waiting for, spoken in their new language."""
    cfg = configured()
    if not cfg['ready']:
        raise VoiceError('Voice is not configured: missing ' + ', '.join(cfg['missing']))
    role_id = role_id if role_id in R.BY_ID else None
    hello = hello or greeting(role_id, lang)
    stt, llm, tts = vendors(session_id, role_id, hello, voice, lang)
    agent = AgoraAgent(
        client=_client(), instructions=INSTRUCTIONS, greeting=hello,
        failure_message=FAILURE.get(lang, FAILURE['en']), max_history=50,
        turn_detection={'language': HEARS, 'config': {
            'speech_threshold': 0.5,
            'start_of_speech': {'mode': 'vad', 'vad_config': {'interrupt_duration_ms': 160, 'prefix_padding_ms': 300}},
            'end_of_speech': {'mode': 'vad', 'vad_config': {'silence_duration_ms': 480}},
        }},
        advanced_features={'enable_rtm': True, 'enable_tools': True},
        parameters={'audio_scenario': 'chorus', 'data_channel': 'rtm', 'enable_error_message': True,
                    'enable_metrics': True},
    ).with_stt(stt).with_llm(llm).with_tts(tts)

    session = agent.create_async_session(channel=channel, agent_uid=str(agent_uid), remote_uids=[str(user_uid)],
                                         enable_string_uid=False, idle_timeout=30, expires_in=3600)
    agent_id = await session.start()
    log.info('voice agent started agent_id=%s channel=%s role=%s lang=%s', agent_id, channel, role_id, lang)
    return {'agent_id': agent_id, 'channel': channel, 'greeting': hello, 'voice': VOICES[voice]['label'],
            'lang': lang}


async def stop(agent_id: str) -> None:
    # Stateless stop (quickstart's fallback path) — works across serverless instances.
    await _client().stop_agent(agent_id)
    log.info('voice agent stopped agent_id=%s', agent_id)
