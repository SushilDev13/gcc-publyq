"""One agent turn: fast path -> LLM tool loop -> graceful fallback.

Emits events: route, card, delta, done. Both front doors (web SSE and the
OpenAI-compatible endpoint used by Agora ConvoAI) consume this generator.

Safety: the LLM can *propose* writes but is never given confirm/cancel.
Only the deterministic router executes a write, and only when the user's own
words are a confirmation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

from . import db, llm
from . import tools as T
from .config import settings
from .router import Reply, route

log = logging.getLogger('gcc.agent')

SYSTEM = """You are GCC Publyq, a voice assistant for Greater Chennai Corporation staff working Ward 173 (Adyar zone 13).
Facts come ONLY from tool results. Never invent complaint IDs, numbers, names or dates. If tools return nothing, say so.
Officials are posts (e.g. "Assistant Engineer, Ward 173"), not named people; don't invent names.
You can look things up, classify new reports (triage), and PROPOSE actions (escalate, assign, note, follow_up).
A proposed action only runs after the user says "confirm"; tell them that. You cannot confirm on their behalf.
Actions are recorded in GCC Publyq, not written to the GCC portal; don't claim otherwise.
Your reply is spoken aloud: at most 2 short sentences, no markdown, no lists, IDs as written. Details appear on screen.
Current focus complaint: {focus}. Last listed: {listed}."""

TOOLS: dict[str, tuple] = {
    'search_complaints': (T.search_complaints, {
        'description': 'Find Ward 173 complaints. Ordered safety-risk first, then overdue.',
        'properties': {
            'status_group': {'type': 'string', 'enum': ['open', 'resolved', 'rejected', 'all']},
            'locality': {'type': 'string', 'description': 'e.g. k.b.nagar, indira Nagar, gandhi Nagar, thiruvanmiyur'},
            'issue_group': {'type': 'string', 'description': 'e.g. Debris, Garbage, Drains, Roads, Streetlights'},
            'complaint_type': {'type': 'string'}, 'safety': {'type': 'boolean'}, 'overdue': {'type': 'boolean'},
            'q': {'type': 'string', 'description': 'free-text search'},
            'holder_post_id': {'type': 'string', 'description': 'e.g. AE-D173'},
            'limit': {'type': 'integer'}}}),
    'get_complaint': (T.get_complaint, {
        'description': 'Full dossier: details, officer actions, photos, SOP, holder/owner/escalation posts.',
        'properties': {'complaint_id': {'type': 'string'}}, 'required': ['complaint_id']}),
    'ward_summary': (T.ward_summary, {'description': 'Counts for the ward: open, overdue, safety, by issue/holder.',
                                      'properties': {}}),
    'find_officials': (T.find_officials, {'description': 'GCC posts for the ward and zone, optionally by role.',
                                          'properties': {'role': {'type': 'string'}}}),
    'get_official': (T.get_official, {'description': 'One post, its escalation chain and open complaints it holds.',
                                       'properties': {'post_id': {'type': 'string'}}, 'required': ['post_id']}),
    'get_sop': (T.get_sop, {'description': 'Standard operating procedure for a complaint type or issue.',
                            'properties': {'topic': {'type': 'string'}}, 'required': ['topic']}),
    'triage': (T.triage, {'description': 'Classify a new report: type, priority, owner post, likely duplicates.',
                          'properties': {'text': {'type': 'string'}}, 'required': ['text']}),
    'propose_action': (T.propose_action, {
        'description': 'Propose escalate/assign/note/follow_up. Needs user confirmation before it is recorded.',
        'properties': {'kind': {'type': 'string', 'enum': list(T.ACTION_KINDS)}, 'complaint_id': {'type': 'string'},
                       'target_post_id': {'type': 'string'}, 'note': {'type': 'string'},
                       'due_in_days': {'type': 'number'}},
        'required': ['kind', 'complaint_id']}),
    'list_actions': (T.list_actions, {
        'description': 'Actions/follow-ups recorded in GCC Publyq.',
        'properties': {'status': {'type': 'string'}, 'kind': {'type': 'string'}, 'complaint_id': {'type': 'string'},
                       'due_only': {'type': 'boolean'}}}),
    'complaint_report': (T.complaint_report, {'description': 'Completion report for one complaint (markdown).',
                                              'properties': {'complaint_id': {'type': 'string'}},
                                              'required': ['complaint_id']}),
    'ward_brief': (T.ward_brief, {'description': 'Daily ward brief (markdown).', 'properties': {}}),
}
TOOL_SCHEMAS = [{'type': 'function', 'function': {'name': n, 'description': s['description'],
                                                  'parameters': {'type': 'object', 'properties': s['properties'],
                                                                 'required': s.get('required', [])}}}
                for n, (_, s) in TOOLS.items()]
CARD_TYPE = {'search_complaints': 'complaint_list', 'get_complaint': 'complaint', 'find_officials': 'officials',
             'get_official': 'official', 'get_sop': 'sop', 'triage': 'triage', 'propose_action': 'action',
             'list_actions': 'actions', 'complaint_report': 'report', 'ward_brief': 'report',
             'ward_summary': 'summary'}

HELP = ("I can tell you what's urgent in Ward 173, open a complaint by ID, find who handles it, read its SOP, "
        "escalate it, set a follow-up, or write a report. Try: what's urgent?")


# ── session context (stateless server: read back from turns) ──────────────
def load_ctx(session_id: str) -> tuple[dict, list[dict]]:
    rows = db.fetchall('SELECT text, reply, ctx FROM turns WHERE session_id = %s ORDER BY id DESC LIMIT 6',
                       (session_id,))
    ctx = next((r['ctx'] for r in rows if r['ctx']), {}) or {}
    return ctx, list(reversed(rows))


def save_turn(session_id: str, channel: str, text: str, route_: str, intent: str | None, tools: list[str],
              reply: str, ctx: dict, ms: int) -> None:
    try:
        db.execute('INSERT INTO turns (session_id, channel, text, route, intent, tools, reply, ctx, latency_ms) '
                   'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                   (session_id, channel, text, route_, intent, json.dumps(tools), reply, json.dumps(ctx), ms))
    except Exception:  # audit log must never break a turn
        log.exception('failed to save turn')


def _next_ctx(prev: dict, focus: str | None, ids: list[str] | None, cards: list[dict]) -> dict:
    ctx = dict(prev)
    if focus:
        ctx['focus'] = focus
    if ids:
        ctx['list'] = ids
        briefs = {}
        for c in cards:
            for it in c.get('items', []) or []:
                briefs[it['id']] = ' '.join(str(it.get(k) or '') for k in ('complaint_type', 'issue', 'issue_group',
                                                                             'locality', 'street')).lower()
        ctx['briefs'] = briefs
    return ctx


def _compact(result: dict) -> str:
    """Tool result for the LLM: drop bulky fields, cap size."""
    r = dict(result)
    if 'media' in r:
        r['media'] = f"{len(r['media'])} files"
    if 'events' in r:
        r['events'] = [{'at': e['at'], 'role': e['role'], 'msg': (e['message_en'] or '')[:160]} for e in r['events'][-4:]]
    if 'markdown' in r:
        r['markdown'] = r['markdown'][:1500]
    s = json.dumps(r, default=str)
    return s[:6000]


# ── the turn ──────────────────────────────────────────────────────────────
async def run_turn(text: str, session_id: str, channel: str = 'web') -> AsyncIterator[tuple[str, dict]]:
    t0 = time.perf_counter()
    ctx, history = await asyncio.to_thread(load_ctx, session_id)

    # 1. fast path
    try:
        reply: Reply | None = await asyncio.to_thread(route, text, ctx, session_id)
    except T.ToolError as e:
        reply = Reply(say=str(e), intent='tool_error')
    if reply:
        yield 'route', {'route': 'fast', 'intent': reply.intent}
        for c in reply.cards:
            yield 'card', c
        yield 'delta', {'text': reply.say}
        new_ctx = _next_ctx(ctx, reply.focus, reply.ids, reply.cards)
        ms = int((time.perf_counter() - t0) * 1000)
        await asyncio.to_thread(save_turn, session_id, channel, text, 'fast', reply.intent, reply.tools, reply.say,
                                new_ctx, ms)
        yield 'done', {'route': 'fast', 'intent': reply.intent, 'latency_ms': ms, 'focus': new_ctx.get('focus')}
        return

    # 2. LLM tool loop
    if not settings.llm_enabled:
        yield 'route', {'route': 'fallback', 'intent': 'no_llm'}
        yield 'delta', {'text': HELP}
        ms = int((time.perf_counter() - t0) * 1000)
        await asyncio.to_thread(save_turn, session_id, channel, text, 'fallback', 'no_llm', [], HELP, ctx, ms)
        yield 'done', {'route': 'fallback', 'latency_ms': ms}
        return

    yield 'route', {'route': 'llm'}
    msgs = [{'role': 'system', 'content': SYSTEM.format(focus=ctx.get('focus') or 'none',
                                                        listed=', '.join((ctx.get('list') or [])[:5]) or 'none')}]
    for h in history:
        msgs += [{'role': 'user', 'content': h['text']}, {'role': 'assistant', 'content': h['reply'] or ''}]
    msgs.append({'role': 'user', 'content': text})

    said, used, cards, focus, ids = '', [], [], None, None
    try:
        for _ in range(settings.llm_max_tool_rounds + 1):
            calls = None
            async for kind, val in llm.stream_chat(msgs, TOOL_SCHEMAS):
                if kind == 'content':
                    said += val
                    yield 'delta', {'text': val}
                else:
                    calls = val
            if not calls:
                break
            msgs.append({'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': c['id'], 'type': 'function', 'function': {'name': c['name'], 'arguments': c['arguments']}}
                for c in calls]})
            for c in calls:
                used.append(c['name'])
                fn = TOOLS.get(c['name'], (None,))[0]
                try:
                    args = json.loads(c['arguments'] or '{}')
                    if c['name'] == 'propose_action':
                        args['session_id'] = session_id
                    if not fn:
                        raise T.ToolError(f"unknown tool {c['name']}")
                    res = await asyncio.to_thread(fn, **args)
                    card = {'type': CARD_TYPE[c['name']], **res}
                    cards.append(card)
                    yield 'card', card
                    focus = res.get('complaint_id') or (res.get('id') if c['name'] == 'get_complaint' else None) or focus
                    if c['name'] == 'search_complaints':
                        ids = [i['id'] for i in res['items']]
                    content = _compact(res)
                except (T.ToolError, TypeError, json.JSONDecodeError) as e:
                    content = json.dumps({'error': str(e)})
                msgs.append({'role': 'tool', 'tool_call_id': c['id'], 'content': content})
        route_ = 'llm'
    except llm.LLMError as e:
        log.warning('llm failed: %s', e)
        msg = "I couldn't reach the language model just now. " + HELP
        yield 'delta', {'text': (' ' if said else '') + msg}
        said += msg
        route_ = 'fallback'

    new_ctx = _next_ctx(ctx, focus, ids, cards)
    ms = int((time.perf_counter() - t0) * 1000)
    await asyncio.to_thread(save_turn, session_id, channel, text, route_, None, used, said, new_ctx, ms)
    yield 'done', {'route': route_, 'latency_ms': ms, 'tools': used, 'focus': new_ctx.get('focus')}
