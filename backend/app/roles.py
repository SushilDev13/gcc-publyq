"""Who is talking. A role is a GCC *post* (the records name posts, not people).

Field roles see complaints they hold or own. Leadership roles also see every
post whose escalation chain (from the real reporting lines + role ladder)
reaches them. "Ward leadership" sees everything in the ward.
"""
from __future__ import annotations

import re
from functools import lru_cache

from . import db
from . import tools as T

WARD = 'WARD'
ADMIN = 'ADMIN'      # whole ward; offered only to admins (auth.admin_ids / the group's owner)

# Ward 173 roles offered in the app: field posts first, then leadership.
ROLES = [
    {'id': 'AE-D173', 'label': 'Assistant Engineer', 'label_ta': 'உதவிப் பொறியாளர்', 'label_hi': 'सहायक अभियंता', 'dept': 'Works (civil)', 'kind': 'field'},
    {'id': 'AEL-D173', 'label': 'Assistant Engineer (Electrical)', 'label_ta': 'உதவிப் பொறியாளர் (மின்)', 'label_hi': 'सहायक अभियंता (विद्युत)', 'dept': 'Electrical', 'kind': 'field'},
    {'id': 'SI-D173', 'label': 'Sanitary Inspector', 'label_ta': 'சுகாதார ஆய்வாளர்', 'label_hi': 'स्वच्छता निरीक्षक', 'dept': 'Health & sanitation', 'kind': 'field'},
    {'id': 'POS1-Z13', 'label': 'Park Overseer', 'label_ta': 'பூங்கா மேற்பார்வையாளர்', 'label_hi': 'उद्यान पर्यवेक्षक', 'dept': 'Parks', 'kind': 'field'},
    {'id': 'AEE3-Z13', 'label': 'Assistant Executive Engineer', 'label_ta': 'உதவிச் செயற்பொறியாளர்', 'label_hi': 'सहायक कार्यकारी अभियंता', 'dept': 'Works (civil)', 'kind': 'leadership'},
    {'id': 'ADE1-Z13', 'label': 'Asst Divisional Engineer (Electrical)', 'label_ta': 'உதவிக் கோட்டப் பொறியாளர் (மின்)', 'label_hi': 'सहायक मंडल अभियंता (विद्युत)', 'dept': 'Electrical', 'kind': 'leadership'},
    {'id': 'SO3-Z13', 'label': 'Sanitary Officer', 'label_ta': 'சுகாதார அலுவலர்', 'label_hi': 'स्वच्छता अधिकारी', 'dept': 'Health & sanitation', 'kind': 'leadership'},
    {'id': 'EE1-Z13', 'label': 'Executive Engineer', 'label_ta': 'செயற்பொறியாளர்', 'label_hi': 'कार्यकारी अभियंता', 'dept': 'Works (civil)', 'kind': 'leadership'},
    {'id': WARD, 'label': 'Ward leadership', 'label_ta': 'வார்டு தலைமை', 'label_hi': 'वार्ड नेतृत्व', 'dept': 'All departments', 'kind': 'ward'},
    {'id': ADMIN, 'label': 'Admin', 'label_ta': 'நிர்வாகி', 'label_hi': 'एडमिन', 'dept': 'All departments', 'kind': 'ward'},
]
BY_ID = {r['id']: r for r in ROLES}

# Spoken role phrases -> role id. Order matters: most specific first.
ROLE_PHRASES = [
    (r'assistant engineer\W*(\(|-|–|—)?\W*electrical|electrical (ae|assistant engineer|engineer)|\bae\W*electrical|\bael\b', 'AEL-D173'),
    (r'ass(?:is)?t(?:ant)?\.? divisional engineer|\bade\b|electrical (head|lead|leadership)', 'ADE1-Z13'),
    (r'assistant executive engineer|\baee\b', 'AEE3-Z13'),
    (r'\bexecutive engineer|\bee\b', 'EE1-Z13'),
    (r'sanitary officer|\bso\b', 'SO3-Z13'),
    (r'sanitary inspector|\bsi\b|conservancy', 'SI-D173'),
    (r'park overseer|parks?\b', 'POS1-Z13'),
    (r'assistant engineer|\bae\b|civil engineer', 'AE-D173'),
    (r'ward (leadership|lead|head|committee)|councill?or|leadership|commissioner|zonal officer|\bmla\b', WARD),
]
INTRO = re.compile(r"^\s*(?:as (?:an?\s+|the\s+)?|i am (?:an?\s+|the\s+)?|i'm (?:an?\s+|the\s+)?|speaking as (?:an?\s+|the\s+)?|"
                   r"switch (?:role )?to (?:an?|the)?\s*|this is (?:the\s+)?)(?P<role>[^,.?!]{2,60}?)(?:[,.?!]|\s+(?:what|show|"
                   r"tell|give|list|how|which|any)\b|$)", re.I)


def detect_role(text: str) -> tuple[str | None, str]:
    """('AEL-D173', rest_of_sentence) if the user names their role, else (None, text)."""
    m = INTRO.match(text)
    if not m:
        return None, text
    phrase = m.group('role').lower()
    for pat, rid in ROLE_PHRASES:
        if re.search(pat, phrase):
            rest = text[m.end('role'):].lstrip(' ,.-–—')
            return rid, rest
    return None, text


@lru_cache(maxsize=1)
def _posts_in_ward() -> tuple[str, ...]:
    rows = db.fetchall("""SELECT DISTINCT p FROM (SELECT holder_post_id p FROM complaints UNION
                                                  SELECT owner_post_id FROM complaints) x WHERE p IS NOT NULL""")
    return tuple(r['p'] for r in rows)


@lru_cache(maxsize=64)
def _chain(post_id: str) -> tuple[str, ...]:
    out, cur = [], post_id
    for _ in range(5):
        nxt = T.escalation_target(cur)
        if not nxt or nxt['post_id'] in out or nxt['post_id'] == post_id:
            break
        out.append(nxt['post_id']); cur = nxt['post_id']
    return tuple(out)


def scope(role_id: str | None) -> list[str] | None:
    """Post IDs this role is responsible for; None = whole ward."""
    if not role_id or role_id not in BY_ID or BY_ID[role_id]['kind'] == 'ward':
        return None
    if BY_ID[role_id]['kind'] == 'field':
        return [role_id]
    return [role_id] + [p for p in _posts_in_ward() if role_id in _chain(p)]


def describe(role_id: str | None) -> dict:
    r = BY_ID.get(role_id or '', BY_ID[WARD])
    sc = scope(r['id'])
    where = 'TRUE' if sc is None else '(holder_post_id = ANY(%(s)s) OR owner_post_id = ANY(%(s)s))'
    stats = db.fetchone(f"""SELECT count(*) total, count(*) FILTER (WHERE status_group='open') open,
                                  count(*) FILTER (WHERE status_group='open' AND is_safety_risk) open_safety
                           FROM complaints WHERE {where}""", {'s': sc})
    post = db.fetchone('SELECT post FROM officials WHERE post_id = %s', (r['id'],))
    return {**r, 'post': post['post'] if post else 'Ward 173 (all departments)', 'scope': sc, **stats}


def list_roles() -> list[dict]:
    return [describe(r['id']) for r in ROLES]
