"""Agora ConvoAI voice sessions.

Adapted from Agora's official Python quickstart (agent-quickstart-python,
server/src/agent.py + server.py): same token builder, turn detection, RTM data
channel and start/stop lifecycle. Changed only the documented vendor blocks:
  - LLM  -> our own brain at /v1/chat/completions (OpenAI-style, role + session
            passed in the URL, CUSTOM_LLM_TOKEN as the bearer key)
  - TTS  -> Murf, model FALCON (params per docs.agora.io .../tts/murf)
  - STT  -> unchanged Agora-managed Deepgram nova-3 (en)
"""
from __future__ import annotations

import logging
import random
import time
from urllib.parse import urlencode

from agora_agent import Area, AsyncAgora
from agora_agent.agentkit import Agent as AgoraAgent
from agora_agent.agentkit.token import generate_convo_ai_token
from agora_agent.agentkit.vendors import DeepgramSTT, MurfTTS, OpenAI

from . import roles as R
from .config import settings

log = logging.getLogger('gcc.voice')

# The brain ignores this prompt (it answers from its own tools), but ConvoAI
# requires instructions; keep it truthful in case it is ever surfaced.
INSTRUCTIONS = ('You are GCC Publyq, a voice assistant for Greater Chennai Corporation Ward 173 staff. '
                'Answers come from the connected GCC Publyq service.')


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


def greeting(role_id: str | None) -> str:
    r = R.describe(role_id)
    if r['open']:
        return (f"Hello {r['label']}. You have {r['open']} open complaints"
                + (f", {r['open_safety']} flagged safety risk" if r['open_safety'] else '') + '. What do you need?')
    return f"Hello {r['label']}. Nothing is open on your desk. Ask me about patterns, a complaint, or a new report."


def vendors(session_id: str, role_id: str | None, hello: str):
    """STT / LLM / TTS blocks of the agent (the only part changed from the quickstart)."""
    brain_url = (settings.public_base_url.rstrip('/') + '/v1/chat/completions?'
                 + urlencode({'session': session_id, **({'role': role_id} if role_id else {})}))
    llm = OpenAI(api_key=settings.custom_llm_token, base_url=brain_url, model='gcc-publyq',
                 greeting_message=hello, failure_message='Sorry, I could not reach the ward records. Please try again.',
                 max_history=10)
    tts = MurfTTS(key=settings.murf_api_key, model='FALCON', voice_id=settings.murf_voice_id,
                  locale=settings.murf_locale, base_url='wss://global.api.murf.ai/v1/speech/stream-input',
                  sample_rate=24000)
    stt = DeepgramSTT(model='nova-3', language='en')
    return stt, llm, tts


async def start(channel: str, agent_uid: int, user_uid: int, session_id: str, role_id: str | None) -> dict:
    cfg = configured()
    if not cfg['ready']:
        raise VoiceError('Voice is not configured: missing ' + ', '.join(cfg['missing']))
    role_id = role_id if role_id in R.BY_ID else None
    hello = greeting(role_id)
    stt, llm, tts = vendors(session_id, role_id, hello)
    agent = AgoraAgent(
        client=_client(), instructions=INSTRUCTIONS, greeting=hello,
        failure_message='Sorry, I could not reach the ward records. Please try again.', max_history=50,
        turn_detection={'config': {
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
    log.info('voice agent started agent_id=%s channel=%s role=%s', agent_id, channel, role_id)
    return {'agent_id': agent_id, 'channel': channel, 'greeting': hello}


async def stop(agent_id: str) -> None:
    # Stateless stop (quickstart's fallback path) — works across serverless instances.
    await _client().stop_agent(agent_id)
    log.info('voice agent stopped agent_id=%s', agent_id)
