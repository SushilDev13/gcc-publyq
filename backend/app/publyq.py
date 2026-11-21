"""Deep links into app.publyq.in (read-only analytics for the same GCC records).

Publyq keeps its whole screen state in the URL hash, e.g.
    https://app.publyq.in/#/complaints?range=all&ward=173&type=Debris+removal
so GCC Publyq never re-implements Publyq's views: it builds the exact URL.
Keys, order and limits mirror Publyq's frontend/src/state.js (FILTER_KEYS,
PARAM_KEYS, MAX_IDS = 100, COMPLAINT_ID) so links are stable and valid.
Publyq serves these views to guests: no sign-in needed.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

BASE = 'https://app.publyq.in/'
WARD = 173
MAX_IDS = 100
COMPLAINT_ID = re.compile(r'^20\d{2}-\d{3,}[A-Z]{3}$')
# Publyq's serialization order (state.js): filters first, then view params
ORDER = ['range', 'from', 'to', 'zone', 'constituency', 'ward', 'dept', 'category', 'type', 'status', 'severity',
         'q', 'ids', 'cmp', 'safety', 'overdue', 'hotspot', 'level', 'owner', 'sort', 'page', 'id', 'sv', 'vs']
VIEWS = ('pulse', 'complaints', 'acc')


def _url(view: str, **kv) -> str:
    if view not in VIEWS:
        raise ValueError(f'unknown Publyq view {view!r}')
    items = [(k, str(kv[k])) for k in ORDER if kv.get(k) not in (None, '', False)]
    return f"{BASE}#/{view}" + (('?' + urlencode(items)) if items else '')


def clean_ids(ids) -> list[str]:
    """Upper-cased, de-duplicated, valid GCC complaint numbers, in order."""
    out = []
    for i in ids or []:
        i = str(i).strip().upper()
        if COMPLAINT_ID.match(i) and i not in out:
            out.append(i)
    return out


def complaints(ids) -> dict | None:
    """An exact set of complaints. Over 100 (Publyq's cap) links the first 100 and says so."""
    ids = clean_ids(ids)
    if not ids:
        return None
    shown = ids[:MAX_IDS]
    return {'url': _url('complaints', range='all', ids=','.join(shown)), 'count': len(shown),
            'truncated': len(ids) > MAX_IDS, 'label': f"Open {len(shown)} in Publyq" if len(shown) > 1
            else 'Open in Publyq'}


def complaint(cid: str) -> dict | None:
    """One complaint with its detail panel open (ids= keeps it in the result set, or Publyq closes it)."""
    ids = clean_ids([cid])
    if not ids:
        return None
    return {'url': _url('complaints', range='all', ids=ids[0], id=ids[0]), 'count': 1, 'label': 'Open in Publyq'}


def view(*, issue: str | None = None, category: str | None = None, status: str | None = None,
         safety: bool = False, overdue: bool = False, ward: int | None = WARD, label: str | None = None) -> dict:
    """A filtered list for the ward: a top issue, an issue group, safety risks, overdue."""
    if status and status not in ('open', 'resolved', 'rejected'):
        raise ValueError(f'unknown status {status!r}')
    return {'url': _url('complaints', range='all', ward=ward, category=category, type=issue, status=status,
                        safety='1' if safety else None, overdue='1' if overdue else None),
            'label': label or 'Open in Publyq'}


def ward_map(ward: int = WARD) -> dict:
    return {'url': _url('pulse', range='all', ward=ward), 'label': f'Ward {ward} on the Publyq map'}


def accountability(ward: int = WARD) -> dict:
    return {'url': _url('acc', range='all', ward=ward), 'label': f'Ward {ward} accountability in Publyq'}
