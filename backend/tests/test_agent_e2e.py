"""End-to-end conversations through the agent, as a ward engineer would speak.

Initiate -> Understand -> Execute -> Confirm -> Follow up, fast path only
(no LLM key needed), then the LLM path with a stub model, then the
OpenAI-compatible endpoint Agora ConvoAI calls.
"""
import json

from fastapi.testclient import TestClient

from backend.app import llm
from backend.app.config import settings
from backend.app.main import app
from backend.tests.conftest import as_user

api = TestClient(app)


def say(text: str, sid: str, role: str | None = None, device: str = 'test-device-1') -> dict:
    """POST one turn, parse the SSE stream into {route, intent, cards, text, done}."""
    out = {'cards': [], 'text': ''}
    body = {'text': text, 'session_id': sid, 'role_id': role}
    with api.stream('POST', '/api/agent/turn', json=body, headers=as_user(device)) as r:
        assert r.status_code == 200
        ev = None
        for line in r.iter_lines():
            if line.startswith('event: '):
                ev = line[7:]
            elif line.startswith('data: '):
                d = json.loads(line[6:])
                if ev == 'route':
                    out.update(d)
                elif ev == 'card':
                    out['cards'].append(d)
                elif ev == 'delta':
                    out['text'] += d['text']
                elif ev == 'done':
                    out['done'] = d
    return out


def test_full_workflow_by_voice_commands():
    sid = 'test-e2e'
    # Initiate
    r = say("What's urgent in my ward?", sid)
    assert r['route'] == 'fallback' and r['intent'] == 'my_queue'
    first = r['cards'][0]['items'][0]
    assert first['is_safety_risk'] and '96 open, 26 flagged safety risk' in r['text']

    # Understand
    r = say('Open the first one', sid)
    assert r['intent'] == 'dossier' and r['cards'][0]['id'] == first['id']
    r = say('Who is handling it?', sid)
    assert r['intent'] == 'responsible' and 'Escalation goes to' in r['text']
    r = say('What does the SOP say?', sid)
    assert r['intent'] == 'sop' and 'Fix:' in r['text']

    # Execute (proposal only — nothing recorded as confirmed yet)
    r = say('Escalate it', sid)
    assert r['intent'] == 'propose_escalate'
    action = r['cards'][0]
    assert action['status'] == 'proposed' and action['complaint_id'] == first['id']

    # Confirm
    r = say('Confirm', sid)
    assert r['intent'] == 'confirm' and r['cards'][0]['status'] == 'confirmed'
    assert 'follow-up' in r['text']

    # Follow up
    r = say('What follow-ups do I have?', sid)
    assert r['intent'] == 'follow_ups' and any(a['complaint_id'] == first['id'] for a in r['cards'][0]['items'])
    r = say('Write the report for it', sid)
    assert r['intent'] == 'report' and f"#{action['id']} escalate" in r['cards'][0]['markdown']

    assert r['done']['latency_ms'] < 1000


def test_cancel_and_nothing_pending():
    sid = 'test-cancel'
    say('show complaint 2026-185OET', sid)
    assert say('remind me in 3 days', sid)['intent'] == 'propose_follow_up'
    r = say('cancel', sid)
    assert r['intent'] == 'cancel' and r['cards'][0]['status'] == 'cancelled'
    assert say('yes', sid)['intent'] == 'confirm_none'


def test_triage_new_report():
    r = say('A resident called about an open manhole near the school in Gandhi Nagar', 'test-triage')
    assert r['intent'] == 'triage'
    assert r['cards'][0]['complaint_type'] == 'Covering Manholes of Storm Water Drain'


def test_unknown_without_llm_is_honest():
    r = say('Write me a poem about the monsoon', 'test-nollm')      # conftest: no Anthropic key
    assert r['route'] == 'fallback' and "what's urgent" in r['text'].lower()


def test_openai_compatible_endpoint_streams(monkeypatch):
    monkeypatch.setattr(settings, 'custom_llm_token', 'secret')
    body = {'model': 'x', 'stream': True, 'messages': [{'role': 'system', 'content': 'ignored'},
                                                        {'role': 'user', 'content': "what's urgent"}]}
    with api.stream('POST', '/v1/chat/completions?session=test-convoai', json=body,
                    headers={'Authorization': 'Bearer secret'}) as r:
        lines = [ln for ln in r.iter_lines() if ln.startswith('data: ')]
    assert lines[-1] == 'data: [DONE]'
    text = ''.join(json.loads(ln[6:])['choices'][0]['delta'].get('content', '') for ln in lines[:-1])
    assert '96 open, 26 flagged safety risk' in text


def test_openai_endpoint_token(monkeypatch):
    monkeypatch.setattr(settings, 'custom_llm_token', 'secret')
    body = {'messages': [{'role': 'user', 'content': 'hi'}], 'stream': False}
    assert api.post('/v1/chat/completions', json=body).status_code == 401
    ok = api.post('/v1/chat/completions?session=test-tok', json=body, headers={'Authorization': 'Bearer secret'})
    assert ok.status_code == 200
