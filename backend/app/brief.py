"""The 7 am morning brief: pushed to the Ward 173 Telegram group.

Deterministic on purpose (no LLM): the same facts every day, nothing invented,
no cost. For each field post: the most dangerous, the oldest and the
recurring open complaint, each one tap from GCC Publyq; for leadership, the
exceptions (untouched work, the biggest holder, complaints without an owner).
Tamil wording comes from the records (issue_ta), GCC's own post titles and
Publyq's Tamil labels.
"""
from __future__ import annotations

from urllib.parse import urlencode

from . import db
from . import publyq as PQ
from . import roles as R
from .config import settings
from .telegram import esc
from .tools import now

APP = 'https://gcc.publyq.in/'
FIELD_ROLES = ['AE-D173', 'AEL-D173', 'SI-D173', 'POS1-Z13']
TA_MONTHS = ['ஜனவரி', 'பிப்ரவரி', 'மார்ச்', 'ஏப்ரல்', 'மே', 'ஜூன்', 'ஜூலை', 'ஆகஸ்ட்', 'செப்டம்பர்', 'அக்டோபர்',
             'நவம்பர்', 'டிசம்பர்']

T = {
    'title': {'ta': 'காலை சுருக்கம்', 'en': 'Morning brief'},
    'ward': {'ta': 'வார்டு 173', 'en': 'Ward 173'},
    'open': {'ta': 'நிலுவையில்', 'en': 'open'},
    'safety': {'ta': 'பாதுகாப்பு அபாயம்', 'en': 'safety risk'},
    'overdue': {'ta': 'கால தாமதம்', 'en': 'overdue'},
    'untouched': {'ta': 'அதிகாரி நடவடிக்கை இல்லாதவை', 'en': 'no officer action yet'},
    'danger': {'ta': 'அபாயம்', 'en': 'Most dangerous'},
    'oldest': {'ta': 'பழையது', 'en': 'Oldest'},
    'repeat': {'ta': 'மீண்டும் வருவது', 'en': 'Keeps coming back'},
    'days': {'ta': 'நாள்', 'en': 'days'},
    'clear': {'ta': 'நிலுவையில் புகார்கள் இல்லை ✅', 'en': 'Nothing open ✅'},
    'talk': {'ta': 'பேசத் தொடங்குங்கள்', 'en': 'Talk it through'},
    'lead': {'ta': 'தலைமைக்கு', 'en': 'For leadership'},
    'holds': {'ta': 'வசம்', 'en': 'holds'},
    'noowner': {'ta': 'புகார்களுக்கு பொறுப்பாளர் பதவி இல்லை', 'en': 'open complaints have no owner post'},
    'followups': {'ta': 'இன்று பின்தொடர வேண்டியவை', 'en': 'follow-ups due today'},
    'pq': {'ta': 'Publyq-ல் பாதுகாப்பு அபாயங்கள்', 'en': 'Safety risks in Publyq'},
    'asof': {'ta': 'GCC தரவு', 'en': 'GCC data as of'},
}


def _t(key: str, lang: str) -> str:
    return T[key][lang]


def app_link(role: str, lang: str, ask: str) -> str:
    return APP + '?' + urlencode({'role': role, 'lang': lang, 'ask': ask})


def _scope_sql(scope: list[str]) -> str:
    return '(holder_post_id = ANY(%(s)s) OR owner_post_id = ANY(%(s)s))'


def _pick(scope: list[str], extra: str, order: str, exclude: list[str]) -> dict | None:
    return db.fetchone(f"""SELECT id, issue, raw->>'issue_ta' issue_ta, street, locality,
                                  (now()::date - complaint_date::date) days
                           FROM complaints WHERE status_group = 'open' AND {_scope_sql(scope)} AND {extra}
                             AND NOT (id = ANY(%(x)s)) ORDER BY {order} LIMIT 1""", {'s': scope, 'x': exclude})


def role_section(role_id: str) -> dict:
    scope = R.scope(role_id)
    stats = db.fetchone(f"""SELECT count(*) open, count(*) FILTER (WHERE is_safety_risk) safety,
                                   count(*) FILTER (WHERE coalesce(officer_message_count, 0) = 0) untouched
                            FROM complaints WHERE status_group = 'open' AND {_scope_sql(scope)}""", {'s': scope})
    picks, used = {}, []
    for key, extra, order in (
            ('danger', 'is_safety_risk', 'urgency_score DESC NULLS LAST, complaint_date'),
            ('oldest', 'TRUE', 'complaint_date'),
            ('repeat', "(hotspot_tier IN ('recurring', 'repeat') OR recurred_after_fix)",
             'repeat_count DESC NULLS LAST, complaint_date')):
        p = _pick(scope, extra, order, used)
        if p:
            picks[key] = p
            used.append(p['id'])
    return {'role': R.BY_ID[role_id], **stats, 'picks': picks}


def leadership() -> dict:
    tot = db.fetchone("""SELECT count(*) FILTER (WHERE status_group='open') open,
                                count(*) FILTER (WHERE status_group='open' AND is_safety_risk) safety,
                                count(*) FILTER (WHERE status_group='open' AND is_overdue) overdue,
                                count(*) FILTER (WHERE status_group='open' AND coalesce(officer_message_count,0)=0) untouched,
                                count(*) FILTER (WHERE status_group='open' AND owner_post_id IS NULL) no_owner,
                                max(last_fetched_at) as_of
                         FROM complaints""")
    no_owner_issue = db.fetchone("""SELECT issue_group, raw->>'issue_group_ta' issue_group_ta, count(*) n
                                    FROM complaints WHERE status_group='open' AND owner_post_id IS NULL
                                    GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 1""")
    due = db.fetchone("""SELECT count(*) n FROM actions WHERE status='confirmed' AND kind='follow_up'
                         AND due_at <= now() + interval '1 day'""")['n']
    return {**tot, 'no_owner_issue': no_owner_issue, 'followups_due': due}


def _line(key: str, p: dict, role_id: str, lang: str) -> str:
    issue = (p['issue_ta'] if lang == 'ta' else None) or p['issue'] or ''
    where = p['street'] or p['locality'] or ''
    href = esc(app_link(role_id, lang, f"open:{p['id']}"))
    mark = {'danger': '🔴', 'oldest': '⏳', 'repeat': '♻️'}[key]
    return (f"{mark} {esc(_t(key, lang))}: <a href=\"{href}\">{esc(p['id'])}</a> {esc(issue)} · {esc(where)} · "
            f"{p['days']} {esc(_t('days', lang))}")


def build(lang: str = 'ta') -> dict:
    if lang not in ('ta', 'en'):
        raise ValueError('lang must be ta or en')
    d = now()
    date = f"{d.day} {TA_MONTHS[d.month - 1]}" if lang == 'ta' else d.strftime('%d %b')
    lead = leadership()
    L = [f"☀️ <b>{esc(_t('title', lang))} · {esc(_t('ward', lang))}</b> · {esc(date)}",
         f"{esc(_t('open', lang))} {lead['open']} · {esc(_t('safety', lang))} {lead['safety']} · "
         f"{esc(_t('overdue', lang))} {lead['overdue']} · {esc(_t('untouched', lang))} {lead['untouched']}"]
    sections = []
    for rid in FIELD_ROLES:
        s = role_section(rid)
        sections.append(s)
        title = s['role']['label_ta'] if lang == 'ta' else s['role']['label']
        L += ['', f"<b>{esc(title)}</b> ({esc(rid)}) · {esc(_t('open', lang))} {s['open']}"]
        if not s['open']:
            L.append(esc(_t('clear', lang)))
            continue
        L += [_line(k, s['picks'][k], rid, lang) for k in ('danger', 'oldest', 'repeat') if k in s['picks']]
        L.append(f"👉 <a href=\"{esc(app_link(rid, lang, 'brief'))}\">{esc(_t('talk', lang))}</a>")
    L += ['', f"<b>{esc(_t('lead', lang))}</b>"]
    top = max(sections, key=lambda s: s['open'])          # the post carrying most of the ward's open work
    if top['open']:
        L.append(f"• {esc(top['role']['id'])} {esc(_t('holds', lang))} {top['open']} · "
                 f"{esc(_t('untouched', lang))} {top['untouched']}")
    if lead['no_owner']:
        g = lead['no_owner_issue'] or {}
        gname = (g.get('issue_group_ta') if lang == 'ta' else None) or g.get('issue_group') or ''
        L.append(f"• {lead['no_owner']} {esc(_t('noowner', lang))}" + (f" ({esc(gname)})" if gname else ''))
    if lead['followups_due']:
        L.append(f"• {esc(_t('followups', lang))}: {lead['followups_due']}")
    L.append(f"👉 <a href=\"{esc(app_link(R.WARD, lang, 'brief'))}\">{esc(_t('talk', lang))}</a> · "
             f"<a href=\"{esc(PQ.view(safety=True, status='open')['url'])}\">{esc(_t('pq', lang))}</a>")
    if lead['as_of']:
        L.append(f"<i>{esc(_t('asof', lang))} {lead['as_of'].strftime('%d-%m-%Y')}</i>")
    text = '\n'.join(L)
    return {'lang': lang, 'html': text, 'chars': len(text), 'sections': sections, 'leadership': lead}
