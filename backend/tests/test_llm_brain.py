"""Claude brain with a stubbed stream (no API spend): tool loop, SQL investigation, safety, fallback."""
from types import SimpleNamespace as NS

import pytest

from backend.app import llm
from backend.app.config import settings
from backend.tests.test_agent_e2e import say


def text_msg(text):
    return NS(stop_reason='end_turn', content=[NS(type='text', text=text)])


def tool_msg(name, args, tid='t1'):
    return NS(stop_reason='tool_use', content=[NS(type='tool_use', id=tid, name=name, input=args)])


@pytest.fixture
def claude(monkeypatch):
    """Script Claude's rounds; records what it was sent."""
    monkeypatch.setattr(settings, 'anthropic_api_key', 'test')
    script, seen = [], []

    async def fake(system, messages, tools, max_tokens=16000):
        seen.append({'system': system, 'messages': list(messages), 'tools': tools})
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        if step.stop_reason == 'end_turn':
            yield 'text', step.content[0].text
        yield 'final', step

    monkeypatch.setattr(llm, 'stream_round', fake)
    return NS(script=script, seen=seen)


def test_creative_question_investigates_with_sql(claude):
    claude.script += [tool_msg('query_db', {'sql': "select locality, count(*) n from complaints where "
                                                   "status_group='open' group by 1 order by 2 desc limit 1"}),
                      text_msg('Gandhi Nagar has the most open complaints right now, 28.')]
    r = say('Which part of the ward is struggling the most right now?', 'test-llm-sql')
    assert r['route'] == 'llm' and r['done']['tools'] == ['query_db']
    assert r['cards'][0]['type'] == 'table' and '"gandhi Nagar"' in r['cards'][0]['rows_json']
    tool_result = claude.seen[1]['messages'][-1]['content'][0]
    assert tool_result['type'] == 'tool_result' and 'gandhi Nagar' in tool_result['content']
    assert r['text'] == 'Gandhi Nagar has the most open complaints right now, 28.'


def test_sql_write_attempt_is_returned_as_tool_error(claude):
    claude.script += [tool_msg('query_db', {'sql': 'delete from actions'}), text_msg('I can only read records.')]
    say('clean up the actions table', 'test-llm-write')
    err = claude.seen[1]['messages'][-1]['content'][0]
    assert err['is_error'] and 'only SELECT' in err['content']


def test_role_and_scope_reach_claude_and_tools(claude):
    claude.script += [tool_msg('my_queue', {}), text_msg('Nothing open on your desk.')]
    r = say('what should I worry about today', 'test-llm-role', role='AEL-D173')
    dyn = claude.seen[0]['system'][1]['text']
    assert 'Assistant Engineer (Electrical), Ward 173' in dyn and 'AEL-D173' in dyn
    assert claude.seen[0]['system'][0]['cache_control'] == {'type': 'ephemeral'}   # stable prefix cached
    assert r['cards'][0]['type'] == 'queue' and r['cards'][0]['total'] == 0


def test_llm_never_gets_confirm_and_confirm_stays_deterministic(claude):
    claude.script += [tool_msg('propose_action', {'kind': 'close_request', 'complaint_id': '2026-185OET',
                                                  'note': 'Slab replaced on site'}),
                      text_msg('Ready to request closure. Say confirm.')]
    r = say('please close 185OET, the slab is replaced', 'test-llm-confirm', role='AE-D173')
    a = r['cards'][0]
    assert a['status'] == 'proposed' and a['kind'] == 'close_request' and a['notify'] is True
    assert a['payload']['role_id'] == 'AE-D173'
    assert not any(t['name'] in ('confirm_action', 'cancel_action') for t in claude.seen[0]['tools'])
    r = say('confirm', 'test-llm-confirm')                 # no script left: must not reach Claude
    assert r['route'] == 'control' and r['cards'][0]['status'] == 'confirmed'
    assert r['cards'][0]['communication']['status'] == 'not_configured'      # recorded, not silently dropped


def test_status_change_needs_a_status(claude):
    claude.script += [tool_msg('propose_action', {'kind': 'status_change', 'complaint_id': '2026-185OET'}),
                      text_msg('Which status should I request?')]
    say('change the status of 185OET', 'test-llm-status')
    err = claude.seen[1]['messages'][-1]['content'][0]
    assert err['is_error'] and 'Which status' in err['content']


def test_llm_outage_falls_back_to_router(claude):
    claude.script.append(llm.LLMError('down'))
    r = say("what's urgent", 'test-llm-down', role='AE-D173')
    assert r['done']['route'] == 'fallback' and '80 open' in r['text']


def test_refusal_is_handled(claude):
    claude.script.append(NS(stop_reason='refusal', content=[]))
    r = say('something off-topic', 'test-llm-refuse')
    assert "can't help with that" in r['text']
