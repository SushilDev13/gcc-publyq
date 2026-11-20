"""Domain tools: the only code that reads facts or writes actions.

Every tool returns plain JSON-able dicts. The fast-path router, the LLM tool
loop and the REST API all call these same functions, so behaviour is
identical whichever front door a request comes through.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import db
from .config import settings
from .text import toks

IST = timezone(timedelta(hours=5, minutes=30))

# Owner roles that have a dedicated per-ward post in ward_map
WARD_ROLE_COL = {
    'Assistant Engineer': 'assistant_engineer',
    'Sanitary Inspector': 'sanitary_inspector',
    'Assistant Engineer (Electrical)': 'electrical_ae',
}

BRIEF_COLS = """c.id, c.complaint_date, c.complaint_type, c.issue, c.issue_group, c.locality, c.street,
  c.specific_location, c.status, c.status_group, c.is_safety_risk, c.is_overdue, c.urgency_label,
  c.holder_post_id, c.owner_post_id, c.hotspot_tier, c.repeat_count"""


class ToolError(ValueError):
    """A user-facing problem (bad ID, invalid action). Safe to read aloud."""


# ── helpers ───────────────────────────────────────────────────────────────
def now() -> datetime:
    return datetime.now(IST)


def _days_open(c: dict) -> int | None:
    d = c.get('complaint_date')
    return (now().replace(tzinfo=None) - d).days if d else None


def _place(c: dict) -> str:
    return (c.get('street') or c.get('locality') or 'Ward 173').strip()


def brief(c: dict) -> dict:
    out = {k: c.get(k) for k in ('id', 'complaint_type', 'issue', 'issue_group', 'locality', 'street',
                                  'specific_location', 'status', 'status_group', 'is_safety_risk', 'is_overdue',
                                  'urgency_label', 'holder_post_id', 'owner_post_id', 'hotspot_tier',
                                  'repeat_count')}
    out['filed'] = c['complaint_date'].strftime('%d %b %Y') if c.get('complaint_date') else None
    out['days_open'] = _days_open(c) if c.get('status_group') == 'open' else None
    out['say'] = f"{c['id']}: {(c.get('issue') or c['complaint_type']).lower()} at {_place(c)}" + (
        f", open {out['days_open']} days" if out['days_open'] is not None else f", {c.get('status_group')}")
    return out


SAFETY_WORDS = {
    'ctx_traffic': 'busy traffic', 'hazard_open_manhole': 'open manhole', 'ctx_danger': 'described as dangerous',
    'ctx_children': 'children nearby', 'hazard_live_wire': 'live wire', 'ctx_possible_accident': 'accident risk',
    'ctx_dark': 'dark stretch', 'ctx_elderly': 'elderly people', 'ctx_low_hanging': 'low-hanging',
    'harm_accident': 'accident reported', 'ctx_possible_injury': 'injury risk', 'hazard_cables': 'loose cables',
    'hazard_sharp': 'sharp edges', 'ctx_pedestrian_road': 'pedestrians forced onto road',
    'hazard_collapse': 'collapse risk', 'ctx_crime': 'crime risk', 'ctx_possible_death': 'risk to life',
    'hazard_animal': 'animal hazard', 'ctx_vulnerable': 'vulnerable people',
}


def safety_text(codes: str | None) -> str | None:
    """'type_defect:x;hazard_open_manhole;ctx_children' -> 'open manhole, children nearby'.
    type_* codes only restate the complaint type, so they're dropped."""
    out = []
    for c in (codes or '').split(';'):
        c = c.strip()
        if not c or c.startswith('type_'):
            continue
        w = SAFETY_WORDS.get(c) or re.sub(r'^(ctx|hazard|harm)_', '', c).replace('_', ' ')
        if w not in out:
            out.append(w)
    return ', '.join(out) or None


def effective_priority(sop: dict | None, is_safety_risk: bool) -> dict | None:
    """Apply the SOP's own safety_override: safety-flagged -> P1 make-safe step, repair keeps its tier."""
    if not sop:
        return None
    if is_safety_risk and sop['priority_tier'] != 'P1' and sop.get('safety_override'):
        return {'tier': 'P1', 'say': f"make safe today, then {sop['tier_meaning'][0].lower()}{sop['tier_meaning'][1:]}",
                'repair_tier': sop['priority_tier'], 'rule': sop['safety_override']}
    return {'tier': sop['priority_tier'], 'say': sop['tier_meaning'], 'repair_tier': sop['priority_tier'], 'rule': None}


def normalize_id(text: str) -> str | None:
    """Accepts '2026-110ONF', '2026 110 ONF', '110ONF', '110 o n f' (spoken)."""
    t = re.sub(r'[\s\-]', '', text.upper())
    m = re.search(r'(?:2026)?(\d{3})([A-Z]{3})', t)
    return f'2026-{m.group(1)}{m.group(2)}' if m else None


def media_url(file: str, source_url: str | None) -> str:
    if settings.media_base_url:
        return f"{settings.media_base_url.rstrip('/')}/{file}"
    if (settings.media_path / file).exists():
        return f'/media/{file}'
    return source_url or ''


# ── read tools ────────────────────────────────────────────────────────────
def search_complaints(status_group: str | None = 'open', locality: str | None = None,
                      issue_group: str | None = None, complaint_type: str | None = None,
                      safety: bool | None = None, overdue: bool | None = None, q: str | None = None,
                      holder_post_id: str | None = None, limit: int = 10,
                      scope: list[str] | None = None) -> dict:
    where, p = ['true'], {}
    if scope is not None:
        where.append('(c.holder_post_id = ANY(%(scope)s) OR c.owner_post_id = ANY(%(scope)s))'); p['scope'] = scope
    if status_group and status_group != 'all':
        where.append('c.status_group = %(sg)s'); p['sg'] = status_group
    if locality:
        where.append('c.locality ILIKE %(loc)s'); p['loc'] = f'%{locality}%'
    if issue_group:
        where.append('c.issue_group ILIKE %(ig)s'); p['ig'] = f'%{issue_group}%'
    if complaint_type:
        where.append('(c.complaint_type ILIKE %(ct)s OR c.issue ILIKE %(ct)s)'); p['ct'] = f'%{complaint_type}%'
    if safety is not None:
        where.append('c.is_safety_risk = %(sf)s'); p['sf'] = safety
    if overdue is not None:
        where.append('c.is_overdue = %(od)s'); p['od'] = overdue
    if holder_post_id:
        where.append('c.holder_post_id = %(hp)s'); p['hp'] = holder_post_id
    if q:
        where.append("c.search @@ websearch_to_tsquery('english', %(q)s)"); p['q'] = q
    w = ' AND '.join(where)
    total = db.fetchone(f'SELECT count(*) n FROM complaints c WHERE {w}', p)['n']
    p['lim'] = max(1, min(int(limit or 10), 50))
    items = db.fetchall(
        f"""SELECT {BRIEF_COLS} FROM complaints c WHERE {w}
            ORDER BY c.is_safety_risk DESC, c.is_overdue DESC, c.urgency_score DESC NULLS LAST,
                     c.complaint_date {'ASC' if status_group == 'open' else 'DESC'}
            LIMIT %(lim)s""", p)
    return {'total': total, 'items': [brief(c) for c in items],
            'filters': {k: v for k, v in dict(status_group=status_group, locality=locality, issue_group=issue_group,
                                               complaint_type=complaint_type, safety=safety, overdue=overdue, q=q,
                                               holder_post_id=holder_post_id).items() if v is not None}}


def _post(post_id: str | None) -> dict | None:
    if not post_id:
        return None
    return db.fetchone("""SELECT post_id, post, role, department_line, level, zone_no, ward_no, reports_to_post_id,
                                 open_now, open_overdue, complaints_all, resolved, typical_days_to_resolve,
                                 typical_hours_to_first_action
                          FROM officials WHERE post_id = %s""", (post_id,))


def escalation_target(post_id: str) -> dict | None:
    """Next post up: observed reporting line first, then the published role ladder within the same zone."""
    p = _post(post_id)
    if not p:
        return None
    if p['reports_to_post_id']:
        return _post(p['reports_to_post_id'])
    up = db.fetchone('SELECT hands_up_to FROM role_ladder WHERE role = %s AND hands_up_to IS NOT NULL '
                     'AND department_line = coalesce(%s, department_line) ORDER BY step LIMIT 1',
                     (p['role'], p['department_line']))
    if not up:
        return None
    r = db.fetchone("""SELECT post_id FROM officials WHERE role = %s AND zone_no = %s
                       ORDER BY complaints_all DESC NULLS LAST LIMIT 1""", (up['hands_up_to'], p['zone_no']))
    return _post(r['post_id']) if r else None


def owner_post(role: str, ward_no: int | None = None) -> dict | None:
    ward_no = ward_no or settings.ward_no
    col = WARD_ROLE_COL.get(role)
    wm = db.fetchone('SELECT * FROM ward_map WHERE ward_no = %s', (ward_no,))
    if col and wm and wm[col]:
        return _post(wm[col])
    if wm:
        r = db.fetchone("""SELECT post_id FROM officials WHERE role = %s AND zone_no = %s
                           ORDER BY complaints_all DESC NULLS LAST LIMIT 1""", (role, wm['zone_no']))
        if r:
            return _post(r['post_id'])
    return None


def sop_for_type(complaint_type: str) -> dict | None:
    return db.fetchone("""SELECT s.*, m.score AS match_score FROM type_sop_map m JOIN sops s ON s.id = m.sop_id
                          WHERE m.complaint_type = %s""", (complaint_type,))


def responsible(c: dict, sop: dict | None) -> dict:
    owner = owner_post(sop['owner_role'], c.get('ward_no')) if sop else None
    holder = _post(c.get('holder_post_id'))
    start = holder or owner
    return {'holder': holder, 'owner': owner,
            'escalate_to': escalation_target(start['post_id']) if start else None}


def get_complaint(complaint_id: str) -> dict:
    cid = normalize_id(complaint_id) or complaint_id
    c = db.fetchone('SELECT * FROM complaints WHERE id = %s', (cid,))
    if not c:
        raise ToolError(f'No complaint {complaint_id} in Ward 173 records.')
    sop = sop_for_type(c['complaint_type'])
    events = db.fetchall("""SELECT seq, at, author, author_type, role, message_en, forwarded_to
                            FROM complaint_events WHERE complaint_id = %s ORDER BY seq""", (cid,))
    media = db.fetchall("""SELECT file, kind, uploader, stage, source_url, duplicate_of FROM media
                           WHERE complaint_id = %s ORDER BY uploader, event_seq NULLS FIRST, file""", (cid,))
    repeats = []
    if c.get('repeat_cluster_id') not in (None, -1):
        repeats = [brief(r) for r in db.fetchall(
            f'SELECT {BRIEF_COLS} FROM complaints c WHERE repeat_cluster_id = %s AND id <> %s '
            'ORDER BY complaint_date DESC LIMIT 8', (c['repeat_cluster_id'], cid))]
    actions = list_actions(complaint_id=cid)['items']
    out = brief(c)
    out.update({
        'details': c['details_en'], 'complainant': c['complainant'], 'whats_failed': c['whats_failed'],
        'whats_to_resolve': c['whats_to_resolve'], 'safety_reasons': safety_text(c['safety_reasons']),
        'priority': effective_priority(sop, c['is_safety_risk']),
        'department': c['department'], 'sla_days': float(c['sla_days']) if c['sla_days'] is not None else None,
        'tweet_url': c['tweet_url'], 'source_url': c['source_url'],
        'data_as_of': c['last_fetched_at'].isoformat() if c['last_fetched_at'] else None,
        'events': [{**e, 'at': e['at'].isoformat() if e['at'] else None} for e in events],
        'media': [{'url': media_url(m['file'], m['source_url']), 'kind': m['kind'], 'uploader': m['uploader'],
                   'stage': m['stage']} for m in media if not m['duplicate_of']],
        'sop': _sop_view(sop), 'responsible': responsible(c, sop), 'repeats': repeats, 'actions': actions,
    })
    return out


def _sop_view(s: dict | None) -> dict | None:
    if not s:
        return None
    return {k: s[k] for k in ('sop_type', 'issue_group', 'owner_role', 'approver_role', 'priority_tier',
                              'tier_meaning', 'safety_override', 'diagnose', 'carry', 'fix', 'not_ours_if',
                              'sop_source')} | {
        'typical_days_to_resolve': float(s['typical_days_to_resolve']) if s['typical_days_to_resolve'] else None,
        'comes_back_after_fix_pct': float(s['comes_back_after_fix_pct']) if s['comes_back_after_fix_pct'] is not None else None}


def get_sop(topic: str) -> dict:
    s = db.fetchone("""SELECT s.* FROM sops s LEFT JOIN type_sop_map m ON m.sop_id = s.id
                       WHERE s.sop_type ILIKE %(t)s OR m.complaint_type ILIKE %(t)s OR s.issue_group ILIKE %(t)s
                       ORDER BY (s.sop_type ILIKE %(t)s) DESC, s.citywide_complaints DESC NULLS LAST LIMIT 1""",
                    {'t': f'%{topic}%'})
    if not s:
        raise ToolError(f'No SOP found for "{topic}".')
    return _sop_view(s)


def get_official(post_id: str) -> dict:
    p = _post(post_id.upper())
    if not p:
        raise ToolError(f'No post {post_id} in GCC records.')
    chain, cur = [], p
    for _ in range(4):
        nxt = escalation_target(cur['post_id'])
        if not nxt or nxt['post_id'] in [c['post_id'] for c in chain] or nxt['post_id'] == p['post_id']:
            break
        chain.append(nxt); cur = nxt
    held = search_complaints(status_group='open', holder_post_id=p['post_id'], limit=5)
    return {**_num(p), 'chain': [_num(c) for c in chain], 'held_in_ward': held}


def _num(d: dict) -> dict:
    return {k: (float(v) if hasattr(v, 'as_integer_ratio') and not isinstance(v, (int, bool)) else v)
            for k, v in d.items()}


def find_officials(role: str | None = None, ward_no: int | None = None) -> dict:
    ward_no = ward_no or settings.ward_no
    wm = db.fetchone('SELECT * FROM ward_map WHERE ward_no = %s', (ward_no,))
    if not wm:
        raise ToolError(f'Ward {ward_no} not in the ward map.')
    rows = db.fetchall("""SELECT post_id, post, role, level, open_now, open_overdue, typical_hours_to_first_action
                          FROM officials WHERE (ward_no = %(w)s OR (level <> 'Ward' AND zone_no = %(z)s))
                            AND (%(r)s::text IS NULL OR role ILIKE %(r)s)
                          ORDER BY level DESC, open_now DESC NULLS LAST""",
                       {'w': ward_no, 'z': wm['zone_no'], 'r': f'%{role}%' if role else None})
    return {'ward_no': ward_no, 'zone': wm['zone_name'], 'ward_committee_officer': wm['ward_committee_officer'],
            'mla': wm['mla'], 'party': wm['party'], 'constituency': wm['constituency'],
            'posts': [_num(r) for r in rows]}


def ward_summary(ward_no: int | None = None) -> dict:
    ward_no = ward_no or settings.ward_no
    tot = db.fetchone("""SELECT count(*) total,
            count(*) FILTER (WHERE status_group='open') open,
            count(*) FILTER (WHERE status_group='open' AND is_overdue) open_overdue,
            count(*) FILTER (WHERE status_group='open' AND is_safety_risk) open_safety,
            count(*) FILTER (WHERE status_group='open' AND coalesce(officer_message_count,0)=0) open_untouched,
            count(*) FILTER (WHERE status_group='resolved') resolved,
            max(last_fetched_at) data_as_of
          FROM complaints WHERE ward_no = %s OR ward_no IS NULL""", (ward_no,))
    by = lambda col: db.fetchall(  # noqa: E731
        f"""SELECT {col} AS key, count(*) n FROM complaints WHERE status_group='open'
            GROUP BY 1 ORDER BY 2 DESC LIMIT 6""")
    holders = db.fetchall("""SELECT c.holder_post_id post_id, o.post, count(*) n,
                                    o.typical_hours_to_first_action
                             FROM complaints c LEFT JOIN officials o ON o.post_id = c.holder_post_id
                             WHERE c.status_group='open' GROUP BY 1,2,4 ORDER BY 3 DESC LIMIT 5""")
    due = list_actions(status='confirmed', kind='follow_up', due_only=True)['items']
    tot['data_as_of'] = tot['data_as_of'].isoformat() if tot['data_as_of'] else None
    return {'ward_no': ward_no, **tot, 'open_by_issue': by('issue_group'), 'open_by_locality': by('locality'),
            'open_by_holder': [_num(h) for h in holders], 'follow_ups_due': len(due)}


# ── triage: classify free text against SOPs + similar real complaints ─────
def triage(text: str) -> dict:
    """Automated classification for a new report (phone call, field note).

    Two signals: (1) keyword overlap with GCC type + SOP names, (2) full-text
    similarity to real Ward 173 complaints, normalised by type frequency so
    the commonest type (debris) doesn't win every vote.
    """
    words = toks(text)
    types = db.fetchall("""SELECT m.complaint_type, s.sop_type, s.issue_group,
                                  (SELECT count(*) FROM complaints c WHERE c.complaint_type = m.complaint_type) freq
                           FROM type_sop_map m JOIN sops s ON s.id = m.sop_id""")
    votes: dict[str, float] = {}
    for t in types:
        vocab = toks(t['complaint_type']) | toks(t['sop_type'])
        hit = len(words & vocab)
        if hit:
            votes[t['complaint_type']] = hit / len(vocab) + hit
    freq = {t['complaint_type']: t['freq'] for t in types}
    sim = db.fetchall(
        f"""SELECT {BRIEF_COLS}, ts_rank(c.search, q) rank FROM complaints c,
                   to_tsquery('english', replace(plainto_tsquery('english', %(t)s)::text, '&', '|')) q
            WHERE c.search @@ q ORDER BY rank DESC LIMIT 15""", {'t': text}) if text.strip() else []
    fts: dict[str, float] = {}
    for r in sim:
        fts[r['complaint_type']] = fts.get(r['complaint_type'], 0) + float(r['rank']) / max(1, freq.get(r['complaint_type'], 1)) ** 0.5
    if fts:
        top = max(fts.values())
        for k, v in fts.items():
            votes[k] = votes.get(k, 0) + 0.5 * v / top
    loc = detect_locality(text)
    if not votes:
        return {'text': text, 'confident': False, 'locality': loc,
                'say': "I couldn't match that to a known complaint type. Can you describe the problem differently?"}
    ctype = max(votes, key=votes.get)
    conf = votes[ctype] / sum(votes.values())
    sop = sop_for_type(ctype)
    owner = owner_post(sop['owner_role']) if sop else None
    safety_words = re.search(r'\b(open manhole|manhole|live wire|electrocut|fallen tree|collaps|accident|child|school|'
                             r'hospital|sparking|shock|deep pit)\b', text.lower())
    dupes = [brief(r) for r in sim if r['status_group'] == 'open' and r['complaint_type'] == ctype
             and (not loc or (r['locality'] or '').lower() == loc.lower())][:3]
    tier = sop['priority_tier'] if sop else None
    if safety_words and sop and tier != 'P1':
        tier = f"P1 (make-safe step; full repair stays {sop['priority_tier']})"
    return {'text': text, 'confident': conf >= 0.5, 'confidence': round(conf, 2), 'complaint_type': ctype,
            'locality': loc, 'safety_signal': safety_words.group(0) if safety_words else None,
            'priority': tier, 'sop': _sop_view(sop), 'owner': owner, 'possible_duplicates': dupes,
            'say': f"Looks like {ctype.lower()}" + (f' in {loc}' if loc else '') +
                   (f", priority {tier}" if tier else '') +
                   (f", owned by {owner['post']}" if owner else '') +
                   (f". {len(dupes)} open complaint{'s' if len(dupes) > 1 else ''} already "
                    f"{'cover' if len(dupes) > 1 else 'covers'} this spot." if dupes else '.')}


LOCALITY_ALIASES = {
    'kb nagar': 'k.b.nagar', 'k b nagar': 'k.b.nagar', 'kasturba nagar': 'k.b.nagar',
    'kasthuribai nagar': 'k.b.nagar', 'kasturibai nagar': 'k.b.nagar', 'kasturiba nagar': 'k.b.nagar',
    'indira nagar': 'indira Nagar', 'indra nagar': 'indira Nagar', 'gandhi nagar': 'gandhi Nagar',
    'thiruvanmiyur': 'thiruvanmiyur', 'nehru nagar': 'nehru Nagar', 'kamaraj nagar': 'kamaraj Nagar',
    'adyar': 'adyar', 'lb road': 'l.b Road', 'l b road': 'l.b Road',
}


def detect_locality(text: str) -> str | None:
    t = re.sub(r'[.\-]', ' ', text.lower())
    t = re.sub(r'\s+', ' ', t)
    for alias in sorted(LOCALITY_ALIASES, key=len, reverse=True):
        if alias in t:
            return LOCALITY_ALIASES[alias]
    return None


# ── write tools: two-phase (propose -> confirm) ───────────────────────────
ACTION_KINDS = ('escalate', 'assign', 'note', 'follow_up')


def _action_view(a: dict) -> dict:
    tp = _post(a['target_post_id']) if a.get('target_post_id') else None
    out = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in a.items()}
    out['target_post'] = tp['post'] if tp else None
    verb = {'escalate': 'Escalate', 'assign': 'Assign', 'note': 'Add note to', 'follow_up': 'Follow up on'}[a['kind']]
    due = a['due_at'].astimezone(IST).strftime('%a %d %b') if a.get('due_at') else None
    out['say'] = (f"{verb} {a['complaint_id'] or 'ward'}" + (f" to {tp['post']}" if tp and a['kind'] != 'follow_up'
                                                             else '') + (f', due {due}' if due else ''))
    return out


def propose_action(kind: str, complaint_id: str, target_post_id: str | None = None, note: str | None = None,
                   due_in_days: float | None = None, session_id: str | None = None) -> dict:
    if kind not in ACTION_KINDS:
        raise ToolError(f'Unknown action "{kind}". I can {", ".join(ACTION_KINDS)}.')
    c = get_complaint(complaint_id)
    if kind in ('escalate', 'assign') and c['status_group'] != 'open':
        raise ToolError(f"{c['id']} is already {c['status_group']}; nothing to {kind}.")
    if kind == 'escalate' and not target_post_id:
        tgt = c['responsible']['escalate_to']
        if not tgt:
            raise ToolError(f"No higher post found in the records for {c['id']}.")
        target_post_id = tgt['post_id']
    if kind == 'assign' and not target_post_id:
        target_post_id = (c['responsible']['owner'] or {}).get('post_id')
    if target_post_id and not _post(target_post_id.upper()):
        raise ToolError(f'No post {target_post_id} in GCC records.')
    if kind == 'follow_up' and due_in_days is None:
        due_in_days = 2
    if kind == 'escalate' and not note:
        r = c['responsible']
        note = (f"{c['id']} ({c['complaint_type']}, {_place(c)}) open {c['days_open']} days"
                + (', flagged safety risk' if c['is_safety_risk'] else '')
                + (f"; held by {r['holder']['post']}" if r['holder'] else '')
                + (f"; {c['safety_reasons']}" if c['safety_reasons'] else '')
                + (f". SOP: {c['priority']['tier']} - {c['priority']['say']}" if c['priority'] else '') + '.')
    due_at = now() + timedelta(days=float(due_in_days)) if due_in_days is not None else None
    a = db.execute("""INSERT INTO actions (complaint_id, kind, target_post_id, note, due_at, session_id)
                      VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
                   (c['id'], kind, target_post_id.upper() if target_post_id else None, note, due_at, session_id))
    v = _action_view(a)
    v['say'] = f"Ready to {v['say'][0].lower()}{v['say'][1:]}. Say confirm to proceed, or cancel."
    return v


def _transition(action_id: int, frm: tuple[str, ...], to: str, ts_col: str) -> dict:
    a = db.execute(f"""UPDATE actions SET status = %s, {ts_col} = now()
                       WHERE id = %s AND status = ANY(%s) RETURNING *""", (to, int(action_id), list(frm)))
    if not a:
        cur = db.fetchone('SELECT status FROM actions WHERE id = %s', (int(action_id),))
        raise ToolError(f'Action {action_id} not found.' if not cur
                        else f"Action {action_id} is {cur['status']}; can't mark it {to}.")
    return _action_view(a)


def confirm_action(action_id: int) -> dict:
    v = _transition(action_id, ('proposed',), 'confirmed', 'confirmed_at')
    v['say'] = f"Done. Action {v['id']} recorded: {v['say'][0].lower()}{v['say'][1:]}."
    if v['kind'] == 'escalate':
        fu = db.execute("""INSERT INTO actions (complaint_id, kind, status, target_post_id, note, due_at,
                                                session_id, confirmed_at, payload)
                           VALUES (%s,'follow_up','confirmed',%s,%s, now() + interval '2 days', %s, now(),
                                   jsonb_build_object('auto_from', %s::bigint)) RETURNING *""",
                        (v['complaint_id'], v['target_post_id'], f"Check escalation #{v['id']} was acted on",
                         v['session_id'], v['id']))
        v['follow_up'] = _action_view(fu)
        v['say'] += f" I've set follow-up {fu['id']} for {v['follow_up']['say'].split('due ')[-1]}."
    return v


def cancel_action(action_id: int) -> dict:
    v = _transition(action_id, ('proposed', 'confirmed'), 'cancelled', 'closed_at')
    v['say'] = f"Cancelled action {v['id']}."
    return v


def complete_action(action_id: int) -> dict:
    v = _transition(action_id, ('confirmed',), 'done', 'closed_at')
    v['say'] = f"Marked action {v['id']} done."
    return v


def latest_proposed(session_id: str | None) -> dict | None:
    return db.fetchone("""SELECT id FROM actions WHERE status = 'proposed' AND session_id IS NOT DISTINCT FROM %s
                          ORDER BY id DESC LIMIT 1""", (session_id,))


def list_actions(status: str | None = None, kind: str | None = None, complaint_id: str | None = None,
                 due_only: bool = False, scope: list[str] | None = None) -> dict:
    rows = db.fetchall("""SELECT a.* FROM actions a
                          WHERE (%(s)s::text IS NULL OR status = %(s)s) AND (%(k)s::text IS NULL OR kind = %(k)s)
                            AND (%(sc)s::text[] IS NULL OR a.target_post_id = ANY(%(sc)s) OR a.complaint_id IN (
                                 SELECT id FROM complaints WHERE holder_post_id = ANY(%(sc)s)
                                                              OR owner_post_id = ANY(%(sc)s)))
                            AND (%(c)s::text IS NULL OR complaint_id = %(c)s)
                            AND (NOT %(d)s OR (due_at IS NOT NULL AND due_at <= now() + interval '1 day'))
                          ORDER BY coalesce(due_at, created_at) ASC LIMIT 50""",
                       {'s': status, 'k': kind, 'c': complaint_id, 'd': due_only, 'sc': scope})
    return {'items': [_action_view(r) for r in rows]}


# ── reports ───────────────────────────────────────────────────────────────
def complaint_report(complaint_id: str) -> dict:
    c = get_complaint(complaint_id)
    r, s = c['responsible'], c['sop']
    L = [f"# Complaint report — {c['id']}", '',
         f"**{c['complaint_type']}** · {c['specific_location'] or _place(c)} · Ward 173",
         f"Filed {c['filed']} via social media · GCC status **{c['status']}**"
         + (f" · open {c['days_open']} days" if c['days_open'] is not None else ''),
         f"Data as of GCC portal fetch {c['data_as_of'][:10] if c['data_as_of'] else 'unknown'}.", '',
         '## What the citizen reported', c['details'] or '—', '']
    if c['safety_reasons'] or c['is_safety_risk']:
        L += [f"**Safety risk:** {c['safety_reasons'] or 'flagged'}", '']
    L += ['## Who holds it',
          f"- Holder now: {r['holder']['post'] if r['holder'] else 'not recorded'}",
          f"- SOP owner: {r['owner']['post'] if r['owner'] else 'n/a'}",
          f"- Escalation: {r['escalate_to']['post'] if r['escalate_to'] else 'n/a'}", '']
    if s:
        pr = c['priority']
        L += [f"## SOP — {s['sop_type']} ({pr['tier']}: {pr['say']})"] + (
            [f"_{pr['rule']}_"] if pr['rule'] else [])
        L += [f"{i}. {d}" for i, d in enumerate(s['diagnose'], 1)]
        L += [f"- Carry: {', '.join(s['carry'])}" if s['carry'] else '', f"- Fix: {s['fix'] or '—'}",
              f"- Not GCC's if: {s['not_ours_if'] or '—'}", f"_Source: {s['sop_source']}_", '']
    L += ['## Officer actions (GCC portal)']
    L += [f"- {e['at'][:16].replace('T', ' ') if e['at'] else '?'} · {e['role'] or e['author_type']}: "
          f"{(e['message_en'] or '').strip()[:300]}" for e in c['events']] or ['- none recorded']
    L += ['', f"Photos/videos on file: {len(c['media'])}", '', '## Actions taken in GCC Publyq']
    L += [f"- #{a['id']} {a['kind']} → {a['target_post'] or '—'} · {a['status']}"
          + (f" · due {a['due_at'][:10]}" if a['due_at'] else '') + (f" · {a['note']}" if a['note'] else '')
          for a in c['actions'] if a['status'] != 'cancelled'] or ['- none']
    L += ['', '_Actions above are recorded in GCC Publyq; they are not written back to the GCC portal._']
    md = '\n'.join(x for x in L if x is not None)
    done = [a for a in c['actions'] if a['status'] in ('confirmed', 'done')]
    return {'complaint_id': c['id'], 'markdown': md,
            'say': f"Report ready for {c['id']}: {c['status_group']}, {len(c['events'])} officer actions on the portal, "
                   f"{len(done)} actions logged here."}


def ward_brief(ward_no: int | None = None) -> dict:
    s = ward_summary(ward_no)
    top = search_complaints(status_group='open', safety=True, limit=5)['items']
    due = list_actions(status='confirmed', kind='follow_up', due_only=True)['items']
    h = s['open_by_holder'][0] if s['open_by_holder'] else None
    L = [f"# Ward {s['ward_no']} brief — {now().strftime('%d %b %Y')}", '',
         f"Open **{s['open']}** · overdue **{s['open_overdue']}** · safety risk **{s['open_safety']}** · "
         f"never touched **{s['open_untouched']}** · resolved {s['resolved']} (data as of {(s['data_as_of'] or '')[:10]})",
         '', '## Safety-risk complaints first']
    L += [f"- {t['say']}" for t in top] or ['- none']
    L += ['', '## Open by issue'] + [f"- {r['key']}: {r['n']}" for r in s['open_by_issue']]
    L += ['', '## Who holds open complaints'] + [
        f"- {r['post'] or 'unassigned'}: {r['n']}" + (f" (typical first action {r['typical_hours_to_first_action']:.0f} h)"
                                                     if r['typical_hours_to_first_action'] else '')
        for r in s['open_by_holder']]
    L += ['', '## Follow-ups due'] + ([f"- #{a['id']} {a['say']}" for a in due] or ['- none'])
    return {'markdown': '\n'.join(L),
            'say': f"Ward {s['ward_no']}: {s['open']} open, {s['open_safety']} safety risks, {s['open_overdue']} overdue."
                   + (f" {h['post']} holds {h['n']} of them." if h and h['post'] else '')
                   + (f" {len(due)} follow-ups due." if due else '')}


# ── role-centred views ────────────────────────────────────────────────────
def _scope_sql(scope: list[str] | None) -> str:
    return 'TRUE' if scope is None else '(holder_post_id = ANY(%(scope)s) OR owner_post_id = ANY(%(scope)s))'


def my_queue(scope: list[str] | None = None, role_label: str = 'Ward 173', limit: int = 10,
             leadership: bool = True) -> dict:
    """Open work for a role, most urgent first (safety -> overdue -> oldest)."""
    r = search_complaints(status_group='open', scope=scope, limit=limit)
    stats = db.fetchone(f"""SELECT count(*) total,
              count(*) FILTER (WHERE status_group='resolved' AND complaint_date > now() - interval '90 days') resolved_90d,
              count(*) FILTER (WHERE status_group='open' AND is_safety_risk) open_safety,
              count(*) FILTER (WHERE status_group='open' AND coalesce(officer_message_count,0)=0) open_untouched,
              count(*) FILTER (WHERE status_group='open' AND owner_post_id IS NULL) open_unowned,
              max(last_fetched_at) as_of
            FROM complaints WHERE {_scope_sql(scope)}""", {'scope': scope})
    as_of = stats['as_of'].strftime('%d %b') if stats['as_of'] else 'last fetch'
    if not r['total']:
        say = (f"As {role_label}, you have no open complaints in the GCC records as of {as_of}. "
               f"{stats['total']} complaints in 2026 records, {stats['resolved_90d']} resolved in the last 90 days.")
    else:
        first = r['items'][0]
        say = (f"As {role_label}: {r['total']} open, {stats['open_safety']} flagged safety risk"
               + (f", {stats['open_untouched']} with no officer action yet" if stats['open_untouched'] else '')
               + f". Most urgent: {first['say']}.")
    if leadership and stats['open_unowned']:
        n = stats['open_unowned']
        say += (f" {n} open complaint{'s have' if n != 1 else ' has'} no owner post in the records: "
                f"the SOP owner role has no Ward 173 post.")
    return {**r, 'role': role_label, 'stats': {k: (v.isoformat() if hasattr(v, 'isoformat') else v)
                                                for k, v in stats.items()}, 'say': say}


def patterns(scope: list[str] | None = None, role_label: str = 'Ward 173') -> dict:
    """Recurring problems in a role's scope, from real records only."""
    p = {'scope': scope}
    w = _scope_sql(scope)
    spots = db.fetchall(f"""SELECT issue, street, locality, count(*) n,
              count(*) FILTER (WHERE status_group='open') open,
              count(*) FILTER (WHERE recurred_after_fix) came_back,
              min(complaint_date)::date first_seen, max(complaint_date)::date last_seen,
              (array_agg(id ORDER BY complaint_date DESC))[1:5] ids
            FROM complaints WHERE {w} AND street IS NOT NULL
            GROUP BY issue, street, locality HAVING count(*) >= 2
            ORDER BY count(*) DESC, count(*) FILTER (WHERE status_group='open') DESC LIMIT 6""", p)
    issues = db.fetchall(f"""SELECT issue, count(*) n,
              count(*) FILTER (WHERE complaint_date > now() - interval '90 days') last_90d,
              count(*) FILTER (WHERE complaint_date <= now() - interval '90 days'
                                 AND complaint_date > now() - interval '180 days') prev_90d,
              count(*) FILTER (WHERE status_group='open') open
            FROM complaints WHERE {w} GROUP BY issue ORDER BY count(*) DESC LIMIT 6""", p)
    tot = db.fetchone(f"""SELECT count(*) n, count(*) FILTER (WHERE recurred_after_fix) came_back,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM last_activity_at - complaint_date)/86400)
                FILTER (WHERE status_group='resolved' AND last_activity_at IS NOT NULL) median_days_to_close
            FROM complaints WHERE {w}""", p)
    months = db.fetchall(f"""SELECT to_char(date_trunc('month', complaint_date), 'Mon') m, count(*) n
            FROM complaints WHERE {w} AND complaint_date > date_trunc('month', now()) - interval '5 months'
            GROUP BY date_trunc('month', complaint_date), 1 ORDER BY date_trunc('month', complaint_date)""", p)
    if not tot['n']:
        return {'role': role_label, 'spots': [], 'issues': [], 'months': [], 'say': f'No complaints in scope for {role_label}.'}
    parts = []
    if spots:
        s = spots[0]
        parts.append(f"Top repeat spot: {s['issue'].lower()} at {s['street']}, {s['n']} complaints"
                     + (f", {s['open']} still open" if s['open'] else '')
                     + (f", {s['came_back']} came back after a fix" if s['came_back'] else ''))
    if issues:
        parts.append('Most common: ' + ', '.join(f"{i['issue'].lower()} {i['n']}" for i in issues[:3]))
    rising = [i for i in issues if i['last_90d'] >= 3 and i['last_90d'] >= 1.25 * max(1, i['prev_90d'])]
    if rising:
        parts.append(f"Rising: {rising[0]['issue'].lower()}, {rising[0]['last_90d']} in the last 90 days "
                     f"versus {rising[0]['prev_90d']} before")
    if tot['came_back']:
        parts.append(f"{tot['came_back']} of {tot['n']} came back after being marked fixed")
    md = float(tot['median_days_to_close']) if tot['median_days_to_close'] is not None else None
    return {'role': role_label, 'total': tot['n'], 'came_back': tot['came_back'], 'median_days_to_close': md,
            'spots': [{**s, 'first_seen': str(s['first_seen']), 'last_seen': str(s['last_seen'])} for s in spots],
            'issues': issues, 'months': months, 'say': '. '.join(parts) + '.'}
