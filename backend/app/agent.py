"""One agent turn, LLM-first.

  1. deterministic control: role intros and confirm/cancel (never left to the LLM)
  2. Claude (Opus 5.5) answers everything else, investigating with tools and
     read-only SQL, streaming its reply as it is written
  3. if Claude is unavailable: the deterministic router, then an honest help line

Emits events: route, card, delta, done. Both front doors (web SSE and the
OpenAI-compatible endpoint Agora ConvoAI calls) consume this generator.

Safety: the LLM can *propose* writes but is never given confirm/cancel.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

from . import db, dbquery, llm, publyq
from . import roles as R
from . import tools as T
from .config import settings
from .router import Reply, control, route
from .tools import IST

log = logging.getLogger('gcc.agent')

STABLE_PROMPT = """You are GCC Publyq, a voice-first workplace assistant for Greater Chennai Corporation staff working Ward 173 (Adyar, zone 13). You help field staff and leadership find information, resolve complaints, and get work done.

How to answer:
- Ground every fact in tool results from this conversation. Never invent complaint IDs, counts, names, dates or posts. If the data can't answer the question, say what is missing.
- For questions the specialised tools don't cover (comparisons, trends, "which street...", "how many...", anything creative), investigate with query_db: a few small targeted SELECT queries beat one huge one. Check a number before you say it.
- Officials are posts (for example "Assistant Engineer, Ward 173"), not people. The records hold no officer names, phone numbers or emails.
- Talk like a capable colleague: lead with the answer, then the one detail that matters most. The screen shows cards with the full detail, so never read out long lists.
- Publyq (app.publyq.in) is the analytics view of these same records. Cards already carry "Open in Publyq" links; when the user wants to see, browse or drill into complaints there, call open_in_publyq (for a set you found with query_db, pass its complaint IDs). Never read a URL aloud; say the link is on screen.

Actions:
- You can propose: escalate, assign, note, follow_up, status_change (with to_status), close_request, help_request. GCC's portal has no write API, so these are recorded in GCC Publyq; requests that someone else must act on are posted to the Ward 173 Telegram group once the user confirms.
- Proposing records nothing yet. Say what will happen and that the user needs to say "confirm". You cannot confirm on their behalf.
- Be sure which complaint the user means before proposing; if it is ambiguous, ask.

""" + dbquery.SCHEMA_GUIDE

STYLE = {
    'convoai': ('Your reply is spoken aloud by a text-to-speech voice. If you need to look anything up, first say '
                'a short natural acknowledgement such as "Let me check." so the caller is not left in silence. Then '
                'answer in one to three short sentences of plain words: no markdown, no lists, no symbols. Say '
                'complaint IDs as written. Offer the next step in a few words if one is useful.'),
    'web': ('Your reply appears in a chat beside detail cards that already show the full data. If you need to look '
            'anything up, start with a short acknowledgement such as "Let me check." Then answer in at most four '
            'sentences of plain text: no markdown, no bold, no headings, no lists.'),
}


def _tool(fn, description: str, properties: dict, required: list[str] | None = None) -> tuple:
    return fn, {'description': description, 'input_schema': {'type': 'object', 'properties': properties,
                                                             'required': required or []}}


TOOLS: dict[str, tuple] = {
    'my_queue': _tool(T.my_queue, "The user's own open complaints, most urgent first (already scoped to their role).", {}),
    'patterns': _tool(T.patterns, "Recurring spots, top and rising issues, comes-back-after-fix rate in the user's scope.", {}),
    'search_complaints': _tool(T.search_complaints, 'Find Ward 173 complaints by filters. Safety-risk first, then overdue.', {
        'status_group': {'type': 'string', 'enum': ['open', 'resolved', 'rejected', 'all']},
        'locality': {'type': 'string'}, 'issue_group': {'type': 'string'}, 'complaint_type': {'type': 'string'},
        'safety': {'type': 'boolean'}, 'overdue': {'type': 'boolean'}, 'q': {'type': 'string'},
        'holder_post_id': {'type': 'string'}, 'limit': {'type': 'integer'}}),
    'get_complaint': _tool(T.get_complaint, 'Full dossier of one complaint: details, officer actions, photos, SOP, '
                           'holder/owner/escalation posts, actions logged here.',
                           {'complaint_id': {'type': 'string'}}, ['complaint_id']),
    'query_db': _tool(dbquery.run, 'Read-only SQL (one SELECT/WITH, Postgres) over the ward records described in the '
                      'system prompt. Max 50 rows. Use for anything the other tools do not answer directly.',
                      {'sql': {'type': 'string'}}, ['sql']),
    'find_officials': _tool(T.find_officials, 'GCC posts for the ward and zone, optionally filtered by role.',
                            {'role': {'type': 'string'}}),
    'get_official': _tool(T.get_official, 'One post: workload, speed, escalation chain, open complaints it holds.',
                          {'post_id': {'type': 'string'}}, ['post_id']),
    'get_sop': _tool(T.get_sop, 'Standard operating procedure for a complaint type or issue.',
                     {'topic': {'type': 'string'}}, ['topic']),
    'triage': _tool(T.triage, 'Classify a newly reported problem: type, priority, owner post, likely duplicates.',
                    {'text': {'type': 'string'}}, ['text']),
    'propose_action': _tool(T.propose_action, 'Propose an action on a complaint. Nothing is recorded or sent until '
                            'the user says confirm.', {
                                'kind': {'type': 'string', 'enum': list(T.ACTION_KINDS)},
                                'complaint_id': {'type': 'string'},
                                'target_post_id': {'type': 'string', 'description': 'defaults to the right post'},
                                'note': {'type': 'string', 'description': 'reason / what help is needed'},
                                'to_status': {'type': 'string', 'enum': list(T.REQUESTABLE_STATUS)},
                                'due_in_days': {'type': 'number'},
                                'notify': {'type': 'boolean', 'description': 'post to the ward Telegram group'}},
                            ['kind', 'complaint_id']),
    'list_actions': _tool(T.list_actions, 'Actions, requests and follow-ups recorded in GCC Publyq.', {
        'status': {'type': 'string'}, 'kind': {'type': 'string'}, 'complaint_id': {'type': 'string'},
        'due_only': {'type': 'boolean'}}),
    'list_communications': _tool(T.list_communications, 'Telegram messages the platform sent (or failed to send).',
                                 {'complaint_id': {'type': 'string'}}),
    'complaint_report': _tool(T.complaint_report, 'Completion report for one complaint.',
                              {'complaint_id': {'type': 'string'}}, ['complaint_id']),
    'ward_brief': _tool(T.ward_brief, 'Daily brief for the whole ward.', {}),
    'open_in_publyq': _tool(publyq.open_link, 'Link to view or drill into complaints in Publyq (app.publyq.in): '
                            'an exact set of complaint IDs (one ID opens its detail), or a Ward 173 list filtered by '
                            'issue / category key / status / safety / overdue, or the ward map / accountability view.', {
                                'complaint_ids': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 100},
                                'issue': {'type': 'string', 'description': 'complaints.issue, e.g. "Debris removal"'},
                                'category': {'type': 'string', 'description': 'complaints.issue_group_id, e.g. debris'},
                                'status': {'type': 'string', 'enum': ['open', 'resolved', 'rejected']},
                                'safety': {'type': 'boolean'}, 'overdue': {'type': 'boolean'},
                                'view_name': {'type': 'string', 'enum': ['complaints', 'map', 'accountability']}}),
}
TOOL_DEFS = [{'name': n, **spec, 'eager_input_streaming': True} for n, (_, spec) in TOOLS.items()]
CARD_TYPE = {'my_queue': 'queue', 'patterns': 'patterns', 'search_complaints': 'complaint_list',
             'get_complaint': 'complaint', 'query_db': 'table', 'find_officials': 'officials',
             'get_official': 'official', 'get_sop': 'sop', 'triage': 'triage', 'propose_action': 'action',
             'list_actions': 'actions', 'list_communications': 'communications', 'complaint_report': 'report',
             'ward_brief': 'report', 'open_in_publyq': 'publyq'}

HELP = ("I can tell you what's urgent in Ward 173, open a complaint by ID, find who handles it, read its SOP, "
        "escalate it, set a follow-up, or write a report. Try: what's urgent?")


# ── session context (stateless server: read back from turns) ──────────────
def load_ctx(session_id: str) -> tuple[dict, list[dict]]:
    rows = db.fetchall('SELECT text, reply, ctx FROM turns WHERE session_id = %s ORDER BY id DESC LIMIT 8',
                       (session_id,))
    ctx = next((r['ctx'] for r in rows if r['ctx']), {}) or {}
    return ctx, list(reversed(rows))


def save_turn(session_id: str, channel: str, text: str, route_: str, intent: str | None, tools: list[str],
              reply: str, ctx: dict, ms: int, cards: list[dict] | None = None,
              device_id: str | None = None) -> int | None:
    try:
        role = ctx.get('role')
        db.execute("""INSERT INTO conversations (id, device_id, role_id, title) VALUES (%s, %s, %s, %s)
                      ON CONFLICT (id) DO UPDATE SET updated_at = now(), role_id = EXCLUDED.role_id""",
                   (session_id, device_id or channel, role, text[:80]))
        return db.execute('INSERT INTO turns (session_id, channel, text, route, intent, tools, reply, ctx, latency_ms, '
                          'cards, role_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                          (session_id, channel, text, route_, intent, json.dumps(tools), reply, json.dumps(ctx), ms,
                           json.dumps(cards or [], default=str), role))['id']
    except Exception:  # audit log must never break a turn
        log.exception('failed to save turn')
        return None


def _next_ctx(prev: dict, focus: str | None, ids: list[str] | None, cards: list[dict]) -> dict:
    ctx = dict(prev)
    if focus:
        ctx['focus'] = focus
    if ids:
        ctx['list'] = ids
        ctx['briefs'] = {it['id']: ' '.join(str(it.get(k) or '') for k in ('complaint_type', 'issue', 'issue_group',
                                                                            'locality', 'street')).lower()
                         for c in cards for it in (c.get('items') or [])}
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
    return json.dumps(r, default=str)[:8000]


def _system(ctx: dict, channel: str) -> list[dict]:
    role = R.BY_ID.get(ctx.get('role') or '', R.BY_ID[R.WARD])
    scope = R.scope(role['id'])
    dyn = (f"Today is {T.now().strftime('%A %d %B %Y')} (IST). You are speaking with: {R.describe(role['id'])['post']} "
           f"({role['kind']} role; scope: {', '.join(scope) if scope else 'whole ward'}). Answer from their point of "
           f"view: their queue, their escalation chain, their recurring problems.\n"
           f"Complaint in focus: {ctx.get('focus') or 'none'}. Last listed: "
           f"{', '.join((ctx.get('list') or [])[:5]) or 'none'}.\n{STYLE.get(channel, STYLE['web'])}")
    return [{'type': 'text', 'text': STABLE_PROMPT, 'cache_control': {'type': 'ephemeral'}},
            {'type': 'text', 'text': dyn}]


def _reply_events(reply: Reply, route_label: str):
    yield 'route', {'route': route_label, 'intent': reply.intent}
    for c in reply.cards:
        yield 'card', c
    yield 'delta', {'text': reply.say}


# ── the turn ──────────────────────────────────────────────────────────────
async def run_turn(text: str, session_id: str, channel: str = 'web', role_id: str | None = None,
                   device_id: str | None = None) -> AsyncIterator[tuple[str, dict]]:
    t0 = time.perf_counter()
    ctx, history = await asyncio.to_thread(load_ctx, session_id)
    if role_id in R.BY_ID:  # explicit choice in the UI wins over the remembered one
        ctx['role'] = role_id

    try:
        reply, body, new_role = await asyncio.to_thread(control, text, ctx, session_id)
    except T.ToolError as e:
        reply, body, new_role = Reply(say=str(e), intent='tool_error'), text, None
    if new_role:
        ctx['role'] = new_role

    route_label = 'control'
    if not reply and not settings.llm_enabled:
        try:
            reply = await asyncio.to_thread(route, body, ctx, session_id)
        except T.ToolError as e:
            reply = Reply(say=str(e), intent='tool_error')
        reply = reply or Reply(say=HELP, intent='no_llm')
        route_label = 'fallback'

    if reply:
        for ev in _reply_events(reply, route_label):
            yield ev
        new_ctx = _next_ctx(ctx, reply.focus, reply.ids, reply.cards)
        if reply.role:
            new_ctx['role'] = reply.role
        ms = int((time.perf_counter() - t0) * 1000)
        tid = await asyncio.to_thread(save_turn, session_id, channel, text, route_label, reply.intent, reply.tools,
                                      reply.say, new_ctx, ms, reply.cards, device_id)
        yield 'done', {'turn_id': tid, 'route': route_label, 'intent': reply.intent, 'latency_ms': ms,
                       'focus': new_ctx.get('focus'), 'role': new_ctx.get('role')}
        return

    # ── Claude ──
    yield 'route', {'route': 'llm'}
    role = R.BY_ID.get(ctx.get('role') or '', R.BY_ID[R.WARD])
    scope = R.scope(role['id'])
    system = _system(ctx, channel)
    msgs: list[dict] = []
    for h in history:
        if h['text'] and h['reply']:
            msgs += [{'role': 'user', 'content': h['text']}, {'role': 'assistant', 'content': h['reply']}]
    msgs.append({'role': 'user', 'content': body or text})

    said, used, cards, focus, ids, first_token_ms = '', [], [], None, None, None
    route_ = 'llm'
    try:
        for _ in range(settings.llm_max_tool_rounds + 1):
            final, round_text = None, False
            async for kind, val in llm.stream_round(system, msgs, TOOL_DEFS):
                if kind == 'text':
                    if first_token_ms is None:
                        first_token_ms = int((time.perf_counter() - t0) * 1000)
                    if not round_text and said and not said[-1].isspace() and not val[:1].isspace():
                        val = ' ' + val          # separate text from consecutive tool rounds
                    round_text = True
                    said += val
                    yield 'delta', {'text': val}
                else:
                    final = val
            if final.stop_reason == 'refusal':
                msg = ("I can't help with that one. Ask me about Ward 173 complaints, posts or actions.")
                msg = (' ' + msg) if said else msg
                said += msg
                yield 'delta', {'text': msg}
                break
            if final.stop_reason == 'pause_turn':
                msgs.append({'role': 'assistant', 'content': final.content})
                continue
            tool_uses = [b for b in final.content if b.type == 'tool_use']
            if not tool_uses:
                break
            if final.stop_reason == 'max_tokens':
                raise llm.LLMError('tool input truncated')
            msgs.append({'role': 'assistant', 'content': final.content})
            results = []
            for b in tool_uses:
                used.append(b.name)
                fn = TOOLS.get(b.name, (None,))[0]
                args = b.input if isinstance(b.input, dict) else None
                try:
                    if fn is None or args is None:
                        raise T.ToolError(f'invalid call to {b.name}')
                    args = dict(args)
                    if b.name == 'propose_action':
                        args.update(session_id=session_id, role_id=role['id'])
                    if b.name in ('search_complaints', 'my_queue', 'patterns', 'list_actions'):
                        args.setdefault('scope', scope)
                    if b.name in ('my_queue', 'patterns'):
                        args.update(role_label=role['label'])
                    if b.name == 'my_queue':
                        args.update(leadership=role['kind'] != 'field')
                    res = await asyncio.to_thread(fn, **args)
                    card = {'type': CARD_TYPE[b.name], **res, **({'sql': args['sql']} if b.name == 'query_db' else {})}
                    cards.append(card)
                    yield 'card', card
                    if b.name == 'get_complaint':
                        focus = res.get('id')
                    elif res.get('complaint_id'):
                        focus = res['complaint_id']
                    if b.name in ('search_complaints', 'my_queue'):
                        ids = [i['id'] for i in res['items']]
                    results.append({'type': 'tool_result', 'tool_use_id': b.id, 'content': _compact(res)})
                except (T.ToolError, dbquery.QueryError, TypeError, ValueError) as e:
                    results.append({'type': 'tool_result', 'tool_use_id': b.id, 'is_error': True,
                                    'content': json.dumps({'error': str(e)})})
            msgs.append({'role': 'user', 'content': results})
    except llm.LLMError as e:
        log.warning('llm failed: %s', e)
        route_ = 'fallback'
        try:
            fb = await asyncio.to_thread(route, body, ctx, session_id)
        except T.ToolError as te:
            fb = Reply(say=str(te), intent='tool_error')
        if fb:
            for c in fb.cards:
                cards.append(c)
                yield 'card', c
            focus, ids = fb.focus or focus, fb.ids or ids
        msg = fb.say if fb else "I couldn't reach the language model just now. " + HELP
        yield 'delta', {'text': (' ' if said else '') + msg}
        said += (' ' if said else '') + msg

    new_ctx = _next_ctx(ctx, focus, ids, cards)
    ms = int((time.perf_counter() - t0) * 1000)
    tid = await asyncio.to_thread(save_turn, session_id, channel, text, route_, None, used, said, new_ctx, ms,
                                  cards, device_id)
    yield 'done', {'turn_id': tid, 'route': route_, 'latency_ms': ms, 'first_token_ms': first_token_ms,
                   'tools': used, 'focus': new_ctx.get('focus'), 'role': new_ctx.get('role')}
