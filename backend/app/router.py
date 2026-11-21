"""Deterministic fast path: common ward intents answered with no LLM call.

Returns a Reply or None (None = hand over to the LLM). Every reply carries
`focus` (the complaint now under discussion) and `list` (last listed IDs) so
the next turn can say "escalate it" or "open the second one".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import roles as R
from . import tools as T

CONFIRM = re.compile(r"^\s*(yes|yeah|yep|confirm(ed)?|go ahead|do it|proceed|ok(ay)?|sure|approve[d]?)\b[\s.!]*"
                     r"(please|it|that|go ahead|confirm)?[\s.!]*$", re.I)
CANCEL = re.compile(r"^\s*(no|nope|cancel( it| that)?|stop|don'?t|abort|never ?mind)\b[\s.!]*$", re.I)
ORDINALS = {'first': 0, '1st': 0, 'one': 0, 'second': 1, '2nd': 1, 'two': 1, 'third': 2, '3rd': 2, 'three': 2,
            'fourth': 3, '4th': 3, 'four': 3, 'fifth': 4, '5th': 4, 'five': 4, 'last': -1}
ROLE_WORDS = {
    'assistant engineer': 'Assistant Engineer', ' ae ': 'Assistant Engineer', 'engineer': 'Engineer',
    'sanitary inspector': 'Sanitary Inspector', ' si ': 'Sanitary Inspector', 'sanitary': 'Sanitary',
    'electrical': 'Electrical', 'park overseer': 'Park Overseer', 'health officer': 'Health Officer',
}
ISSUE_WORDS = {
    'debris': 'Debris', 'silt': 'Debris', 'garbage': 'Garbage', 'trash': 'Garbage', 'waste': 'Garbage',
    'drain': 'Drains', 'manhole': 'Drains', 'stagnation': 'Drains', 'flood': 'Drains',
    'encroach': 'Encroachment', 'hoarding': 'Hoardings', 'banner': 'Hoardings', 'poster': 'Hoardings',
    'pothole': 'Roads', 'road damage': 'Roads', 'streetlight': 'Streetlights', 'street light': 'Streetlights',
    'cable': 'Streetlights', 'tree': 'Trees', 'mosquito': 'Public Health', 'dengue': 'Public Health',
    'park': 'Parks', 'footpath': 'Footpaths', 'sweep': 'Sweeping', 'defecation': 'Toilets',
    'building violation': 'Building', 'illegal construction': 'Building', 'cattle': 'Stray',
}


@dataclass
class Reply:
    say: str
    intent: str
    cards: list[dict] = field(default_factory=list)
    focus: str | None = None
    ids: list[str] | None = None
    tools: list[str] = field(default_factory=list)
    role: str | None = None


def _has(t: str, *words: str) -> bool:
    return any(re.search(rf'\b{w}', t) for w in words)


def _resolve_ref(t: str, ctx: dict) -> tuple[str | None, str | None]:
    """(complaint_id, how): explicit 'id' > 'ordinal' / 'keyword' into the last list > 'focus'."""
    cid = T.normalize_id(t)
    if cid:
        return cid, 'id'
    last = ctx.get('list') or []
    if last:
        m = re.search(r'\b(?:number|no\.?|#)\s*(\d{1,2})\b', t)
        if m and 0 < int(m.group(1)) <= len(last):
            return last[int(m.group(1)) - 1], 'ordinal'
        for w, i in ORDINALS.items():
            if re.search(rf'\bthe {w}( one| complaint)?\b|\b{w} one\b', t):
                return (last[i], 'ordinal') if -len(last) <= i < len(last) else (None, None)
        kw = [k for k in ISSUE_WORDS if k in t]
        if kw and re.search(r'\b(the|that) \w+ one\b|\bone about\b', t):
            for c in last:
                if any(k in ctx.get('briefs', {}).get(c, '') for k in kw):
                    return c, 'keyword'
    return ctx.get('focus'), ('focus' if ctx.get('focus') else None)


def _list_reply(r: dict, intent: str, lead: str) -> Reply:
    items = r['items']
    if not items:
        return Reply(say=f'{lead}: none found.', intent=intent, cards=[{'type': 'complaint_list', **r}],
                     tools=['search_complaints'])
    more = f" {r['total'] - 1} more on screen." if r['total'] > 1 else ''
    return Reply(say=f"{lead}: {r['total']}. First: {items[0]['say']}.{more} Say 'open the first one' for details.",
                 intent=intent, cards=[{'type': 'complaint_list', **r}], ids=[i['id'] for i in items],
                 tools=['search_complaints'])


CONFIRM_ID = re.compile(r'^\s*(confirm|cancel|approve)\s+(?:action\s*)?#?\s*(\d+)\s*[.!]?\s*$', re.I)
QUEUE_WORDS = ('high.?priority', 'urgent', 'burning', 'priority', 'critical', 'dangerous', 'safety', 'my queue',
               'my complaints', 'on my plate', 'my desk', 'what should i (do|work on|focus)', 'my work', 'my day')
PATTERN_WORDS = ('recurring', 'patterns?', 'hotspots?', 'keeps? coming', 'comes? back', 'repeat(ed|ing)?',
                 'again and again', 'trends?', 'chronic')


def control(text: str, ctx: dict, session_id: str | None) -> tuple[Reply | None, str, str | None]:
    """Always-on deterministic layer: role intros and confirm/cancel. Writes and identity are never left to the
    LLM's interpretation. Returns (reply or None, text without the role intro, newly named role or None)."""
    new_role, rest = R.detect_role(text)
    role_id = new_role or ctx.get('role')
    if new_role and not rest.strip(' ?.!'):
        role = R.BY_ID[role_id]
        q = T.my_queue(R.scope(role_id), role['label'], leadership=role['kind'] != 'field')
        return (Reply(say=f"Okay, {role['label']}. " + q['say'], intent='set_role', role=role_id,
                      cards=[{'type': 'queue', **q}], ids=[i['id'] for i in q['items']], tools=['my_queue']),
                rest, new_role)
    body = rest if new_role else text
    raw = body.strip()
    if CONFIRM_ID.match(raw) or CONFIRM.match(raw) or CANCEL.match(raw):
        reply = _route(body, {**ctx, 'role': role_id}, session_id)
        if reply and new_role:
            reply.role = new_role
        return reply, body, new_role
    return None, body, new_role


def route(text: str, ctx: dict, session_id: str | None) -> Reply | None:
    role_id = ctx.get('role')
    new_role, rest = R.detect_role(text)
    if new_role:
        role_id = new_role
        if not rest.strip(' ?.!'):
            role = R.BY_ID[role_id]
            q = T.my_queue(R.scope(role_id), role['label'], leadership=role['kind'] != 'field')
            return Reply(say=f"Okay, {role['label']}. " + q['say'], intent='set_role', role=role_id,
                         cards=[{'type': 'queue', **q}], ids=[i['id'] for i in q['items']], tools=['my_queue'])
        text = rest
    reply = _route(text, {**ctx, 'role': role_id}, session_id)
    if reply and new_role:
        reply.role = new_role
    elif new_role:  # role set, rest goes to the LLM; remember the role anyway
        ctx['role'] = new_role
    return reply


def _route(text: str, ctx: dict, session_id: str | None) -> Reply | None:
    t = ' ' + re.sub(r'\s+', ' ', text.lower()).strip() + ' '
    raw = text.strip()
    role_id = ctx.get('role')
    role = R.BY_ID.get(role_id or '', R.BY_ID[R.WARD])
    scope = R.scope(role['id'])
    lead = role['kind'] != 'field'

    # 1. confirmation of a pending write (by ID from a card, or the latest by voice)
    m = CONFIRM_ID.match(raw)
    if m:
        aid = int(m.group(2))
        own = T.db.fetchone('SELECT session_id FROM actions WHERE id = %s', (aid,))
        if not own or own['session_id'] != session_id:
            return Reply(say=f'Action {aid} is not part of this conversation.', intent='confirm_none')
        a = T.cancel_action(aid) if m.group(1).lower() == 'cancel' else T.confirm_action(aid)
        return Reply(say=a['say'], intent='confirm' if a['status'] == 'confirmed' else 'cancel',
                     cards=[{'type': 'action', **a}], focus=a['complaint_id'], tools=['confirm_action'])
    if CONFIRM.match(raw) or CANCEL.match(raw):
        p = T.latest_proposed(session_id)
        if not p:
            return Reply(say='There is nothing waiting for confirmation.', intent='confirm_none')
        a = T.confirm_action(p['id']) if CONFIRM.match(raw) else T.cancel_action(p['id'])
        return Reply(say=a['say'], intent='confirm' if a['status'] == 'confirmed' else 'cancel',
                     cards=[{'type': 'action', **a}], focus=a['complaint_id'], tools=['confirm_action'])

    # 2. follow-ups / my actions
    if _has(t, 'follow.?ups?', 'reminders?', 'pending actions', 'my actions', "what's due", 'what is due', 'due today'):
        r = T.list_actions(status='confirmed', kind='follow_up', due_only=_has(t, 'due', 'today', 'overdue'),
                           scope=scope)
        n = len(r['items'])
        say = (f"{n} follow-up{'s' if n != 1 else ''} " + ('due' if _has(t, 'due', 'today') else 'open') + '. ' +
               '; '.join(f"#{a['id']} {a['say']}" for a in r['items'][:3])) if n else 'No follow-ups due.'
        return Reply(say=say.strip(), intent='follow_ups', cards=[{'type': 'actions', **r}], tools=['list_actions'])

    # 3. actions on a referenced complaint
    ref, how = _resolve_ref(t, ctx)
    pointed = how in ('id', 'ordinal', 'keyword') or (how == 'focus' and _has(t, 'it', 'this', 'that'))
    if ref and _has(t, 'escalate', 'push (it|this) up', 'raise (it|this)'):
        a = T.propose_action('escalate', ref, session_id=session_id)
        return Reply(say=a['say'], intent='propose_escalate', cards=[{'type': 'action', **a}], focus=ref,
                     tools=['propose_action'])
    if ref and _has(t, 'remind', 'follow up', 'follow-up', 'check back'):
        m = re.search(r'(\d+|one|two|three|four|five|seven)\s*(day|week)', t)
        n = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'seven': 7}.get(m.group(1), None) if m else None
        days = (n or (int(m.group(1)) if m and m.group(1).isdigit() else 2)) * (7 if m and m.group(2) == 'week' else 1)
        if 'tomorrow' in t:
            days = 1
        a = T.propose_action('follow_up', ref, due_in_days=days, session_id=session_id)
        return Reply(say=a['say'], intent='propose_follow_up', cards=[{'type': 'action', **a}], focus=ref,
                     tools=['propose_action'])
    if ref and _has(t, 'note', 'log that', 'record that'):
        note = re.sub(r'^.*?\b(note|log that|record that)\b[:,]?\s*', '', raw, flags=re.I)
        a = T.propose_action('note', ref, note=note or raw, session_id=session_id)
        return Reply(say=a['say'], intent='propose_note', cards=[{'type': 'action', **a}], focus=ref,
                     tools=['propose_action'])
    if ref and _has(t, 'report', 'close.?out', 'completion'):
        r = T.complaint_report(ref)
        return Reply(say=r['say'], intent='report', cards=[{'type': 'report', **r}], focus=ref,
                     tools=['complaint_report'])
    if ref and _has(t, 'sop', 'how (do|to|should)', 'procedure', 'what should', 'fix it', 'steps'):
        c = T.get_complaint(ref)
        s, pr = c['sop'], c['priority']
        say = (f"{s['sop_type']}, priority {pr['tier']}: {pr['say']}. First check: {s['diagnose'][0]} "
               f"Fix: {s['fix']}") if s else 'No SOP mapped for this type.'
        return Reply(say=say, intent='sop', cards=[{'type': 'sop', 'complaint_id': ref, **(s or {})}], focus=ref,
                     tools=['get_complaint'])
    if ref and _has(t, 'who', 'responsible', 'handling', 'holds', 'owner'):
        c = T.get_complaint(ref)
        r = c['responsible']
        say = (f"{ref} is held by {r['holder']['post'] if r['holder'] else 'no recorded post'}"
               + (f"; SOP owner is {r['owner']['post']}" if r['owner'] and r['owner'] != r['holder'] else '')
               + (f". Escalation goes to {r['escalate_to']['post']}." if r['escalate_to'] else '.'))
        return Reply(say=say, intent='responsible', cards=[{'type': 'complaint', **c}], focus=ref,
                     tools=['get_complaint'])
    if ref and (how == 'id' or (pointed and _has(t, 'open', 'show', 'details', 'tell me', 'what about', 'take',
                                                'photos?', 'pictures?', 'images?', 'more'))):
        c = T.get_complaint(ref)
        r, pr = c['responsible'], c['priority']
        n_ev = len(c['events'])
        say = (f"{c['say']}."
               + (f" Safety risk: {c['safety_reasons']}." if c['is_safety_risk'] and c['safety_reasons'] else '')
               + (f" Held by {r['holder']['post']}," if r['holder'] else '')
               + (f" {n_ev} officer action{'s' if n_ev != 1 else ''} so far." if n_ev else ' no officer action yet.')
               + (f" Priority {pr['tier']}: {pr['say']}." if pr and c['status_group'] == 'open' else '')
               + (" Say escalate, follow up, or report." if c['status_group'] == 'open' else ''))
        return Reply(say=say, intent='dossier', cards=[{'type': 'complaint', **c}], focus=c['id'],
                     tools=['get_complaint'])

    # 4. role-centred: my queue, recurring patterns, or both in one answer
    wants_q, wants_p = _has(t, *QUEUE_WORDS), _has(t, *PATTERN_WORDS)
    if role['id'] == R.WARD and _has(t, 'brief', 'how is (my|the) ward', "how's (my|the) ward"):
        b = T.ward_brief()
        return Reply(say=b['say'], intent='ward_brief', cards=[{'type': 'report', **b}], tools=['ward_brief'])
    if _has(t, 'brief', 'summary', 'overview', 'start my day', 'morning'):
        wants_q = wants_p = True
    if wants_q or wants_p:
        cards, says, ids, used = [], [], None, []
        if wants_q:
            q = T.my_queue(scope, role['label'], leadership=lead)
            cards.append({'type': 'queue', **q}); says.append(q['say']); used.append('my_queue')
            ids = [i['id'] for i in q['items']]
        if wants_p:
            pt = T.patterns(scope, role['label'])
            cards.append({'type': 'patterns', **pt}); used.append('patterns')
            says.append(('Patterns: ' if wants_q else '') + pt['say'])
        if wants_q and ids:
            says.append("Say 'open the first one' for details.")
        return Reply(say=' '.join(says), intent='+'.join(used), cards=cards, ids=ids, tools=used)

    # 5. new report -> triage (automated classification)
    if _has(t, 'resident (called|said|reported|complained)', 'someone (called|reported)', 'new complaint',
            'classify', 'triage', 'citizen (called|reported)', 'got a call', 'caller'):
        body = re.sub(r'^.*?\b(about|that|:)\s+', '', raw, count=1, flags=re.I)
        r = T.triage(body)
        return Reply(say=r['say'], intent='triage', cards=[{'type': 'triage', **r}],
                     ids=[d['id'] for d in r.get('possible_duplicates', [])] or None, tools=['triage'])

    # 6. officials
    if _has(t, 'who is', "who's", 'officer', 'officials?', 'contact', 'in charge'):
        role = next((v for k, v in ROLE_WORDS.items() if k in t), None)
        r = T.find_officials(role=role)
        top = r['posts'][:3]
        say = (f"Ward 173, zone {r['zone']}: " + '; '.join(
            f"{p['post']}, {p['open_now'] or 0} open" for p in top)
            + (f". Ward committee officer is {r['ward_committee_officer']}." if not role else '.'))
        return Reply(say=say, intent='officials', cards=[{'type': 'officials', **r}], tools=['find_officials'])

    # 7. filtered search
    loc = T.detect_locality(t)
    issue = next((v for k, v in ISSUE_WORDS.items() if k in t), None)
    wants_list = _has(t, 'open', 'overdue', 'pending', 'complaints?', 'list', 'show', 'how many', 'any')
    if wants_list and (loc or issue or _has(t, 'open', 'overdue', 'pending')):
        sg = 'resolved' if _has(t, 'resolved', 'closed') else 'open'
        mine = scope is not None and not _has(t, 'all', 'whole ward', 'everyone', 'ward wide')
        r = T.search_complaints(status_group=sg, locality=loc, issue_group=issue, scope=scope if mine else None,
                                overdue=True if 'overdue' in t else None, limit=10)
        heading = ' '.join(x for x in ['Your' if mine else None, sg if mine else sg.capitalize(),
                                       issue.lower() if issue else None, 'complaints',
                                       f'in {loc}' if loc else None] if x)
        return _list_reply(r, 'search', heading)

    return None
