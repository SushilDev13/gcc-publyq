"""Load the Ward 173 CSVs into Postgres. Idempotent: re-running refreshes
source tables and leaves workspace tables (actions, turns) intact.

    .venv/bin/python -m backend.scripts.seed            # uses DATABASE_URL
"""
import csv
import json
import re
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.app.config import ROOT, settings  # noqa: E402
from backend.app.text import toks  # noqa: E402

C = ROOT / 'Complaints' / 'Images'
O = ROOT / 'Officials'
SCHEMA = ROOT / 'backend' / 'db' / 'schema.sql'


# ── parsing helpers ───────────────────────────────────────────────────────
def rows(path: Path) -> list[dict]:
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def s(v):
    v = (v or '').strip()
    return None if v in ('', '-', 'nan') else v


def i(v):
    v = s(v)
    return int(float(v)) if v is not None else None


def n(v):
    v = s(v)
    return float(v) if v is not None else None


def b(v):
    return (v or '').strip().lower() == 'true'


def ts(v):
    v = s(v)
    return v.replace('T', ' ').replace('Z', '') if v else None


VIEWED_BY = re.compile(r'^([A-Z]+\d*?)(DN|D|NZ|Z)(\d+)$')


def viewed_by_to_post(code: str | None) -> str | None:
    """GCC holder codes -> officials.post_id: AEDN173 -> AE-D173, AEE3NZ13 -> AEE3-Z13."""
    m = VIEWED_BY.match(code or '')
    if not m:
        return None
    role, scope, num = m.groups()
    return f"{role}-{'D' if scope in ('D', 'DN') else 'Z'}{num}"


# ── GCC type -> SOP matching (within the same issue_group) ────────────────
# Reviewed by hand where token overlap picks the wrong SOP. Keep this short.
OVERRIDES = {'Obstruction of Trees': 'Overgrown trees & bushes'}


def match_sop(gcc_type: str, group: str, sops: list[dict]) -> tuple[dict | None, float]:
    if gcc_type in OVERRIDES:
        return next(x for x in sops if x['complaint_type'] == OVERRIDES[gcc_type]), 1.0
    cands = [x for x in sops if x['issue_group'] == group]
    if not cands:
        return None, 0.0
    a = toks(gcc_type)
    best, score = None, -1.0
    for x in cands:
        bt = toks(x['complaint_type'])
        sc = len(a & bt) / max(1, len(a | bt))
        if sc > score:
            best, score = x, sc
    return best, round(score, 3)


# ── load ──────────────────────────────────────────────────────────────────
def main() -> None:
    comps = rows(C / 'ward173_complaints.csv')
    events = rows(C / 'ward173_events.csv')
    img = rows(C / 'images_manifest.csv')
    xmed = rows(C / 'x_media_manifest.csv')
    offs = rows(O / 'gcc_officials.csv')
    lines = rows(O / 'gcc_reporting_lines.csv')
    wards = rows(O / 'gcc_ward_map.csv')
    sops = rows(O / 'gcc_complaint_sop.csv')
    ladder = rows(O / 'gcc_role_ladder.csv')
    dictionary = rows(O / 'data_dictionary.csv')
    post_ids = {o['post_id'] for o in offs}

    with psycopg.connect(settings.database_url) as db, db.cursor() as cur:
        cur.execute(SCHEMA.read_text())
        cur.execute('DELETE FROM media; DELETE FROM complaint_events; DELETE FROM type_sop_map; '
                    'DELETE FROM reporting_lines; DELETE FROM ward_map; DELETE FROM role_ladder; '
                    'DELETE FROM data_dictionary;')
        for r in dictionary:
            cur.execute('INSERT INTO data_dictionary VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                        (r['file'], r['column'], s(r['meaning']), s(r['source'])))
        for r in ladder:
            cur.execute('INSERT INTO role_ladder VALUES (%s,%s,%s,%s,%s,%s)',
                        (r['department_line'], i(r['step']), r['role'], s(r['level']), s(r['hands_up_to']),
                         s(r['source'])))

        for o in offs:
            cur.execute(
                """INSERT INTO officials VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (post_id) DO UPDATE SET post=EXCLUDED.post, role=EXCLUDED.role,
                     reports_to_post_id=EXCLUDED.reports_to_post_id, reports_to_post=EXCLUDED.reports_to_post,
                     complaints_all=EXCLUDED.complaints_all, open_now=EXCLUDED.open_now,
                     open_overdue=EXCLUDED.open_overdue, open_safety_risk=EXCLUDED.open_safety_risk,
                     resolved=EXCLUDED.resolved, avg_days_to_resolve=EXCLUDED.avg_days_to_resolve,
                     typical_days_to_resolve=EXCLUDED.typical_days_to_resolve,
                     typical_hours_to_first_action=EXCLUDED.typical_hours_to_first_action,
                     top_complaint_categories=EXCLUDED.top_complaint_categories, evidence=EXCLUDED.evidence""",
                (o['post_id'], o['post'], s(o['role_code']), o['role'], s(o['department_line']), s(o['level']),
                 s(o['region']), i(o['zone_no']), s(o['zone_name']), i(o['ward_no']), s(o['reports_to_post_id']),
                 s(o['reports_to_post']), i(o['complaints_all']), i(o['open_now']), i(o['open_overdue']),
                 i(o['open_safety_risk']), i(o['resolved']), n(o['avg_days_to_resolve']),
                 n(o['typical_days_to_resolve']), n(o['typical_hours_to_first_action']),
                 s(o['top_complaint_categories']), s(o['evidence'])))

        for r in lines:
            cur.execute('INSERT INTO reporting_lines VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                        (r['from_post_id'], r['to_post_id'], i(r['handoffs_observed']),
                         n(r['share_of_from_post_upward_handoffs_pct']), b(r['is_primary_reporting_line'])))

        for w in wards:
            cur.execute('INSERT INTO ward_map VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (i(w['ward_no']), i(w['zone_no']), s(w['zone_name']), s(w['region']),
                         s(w['assistant_engineer']), s(w['sanitary_inspector']), s(w['electrical_ae']),
                         s(w['ward_committee_officer']), s(w['mla']), s(w['party']), s(w['constituency']),
                         json.dumps(w)))

        sop_ids = {}
        for x in sops:
            diagnose = [d for d in (s(x['diagnose_step_1']), s(x['diagnose_step_2']), s(x['diagnose_step_3'])) if d]
            carry = [c.strip() for c in (x['carry'] or '').split('|') if c.strip()]
            cur.execute(
                """INSERT INTO sops (issue_group, sop_type, owner_role, approver_role, priority_tier, tier_meaning,
                     safety_override, diagnose, carry, fix, not_ours_if, sop_source, citywide_complaints,
                     avg_days_to_resolve, typical_days_to_resolve, comes_back_after_fix_pct, who_acted_in_data)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (sop_type) DO UPDATE SET issue_group=EXCLUDED.issue_group,
                     owner_role=EXCLUDED.owner_role, approver_role=EXCLUDED.approver_role,
                     priority_tier=EXCLUDED.priority_tier, tier_meaning=EXCLUDED.tier_meaning,
                     safety_override=EXCLUDED.safety_override, diagnose=EXCLUDED.diagnose, carry=EXCLUDED.carry,
                     fix=EXCLUDED.fix, not_ours_if=EXCLUDED.not_ours_if, sop_source=EXCLUDED.sop_source,
                     citywide_complaints=EXCLUDED.citywide_complaints,
                     avg_days_to_resolve=EXCLUDED.avg_days_to_resolve,
                     typical_days_to_resolve=EXCLUDED.typical_days_to_resolve,
                     comes_back_after_fix_pct=EXCLUDED.comes_back_after_fix_pct,
                     who_acted_in_data=EXCLUDED.who_acted_in_data
                   RETURNING id""",
                (x['issue_group'], x['complaint_type'], x['owner_role'], s(x['approver_role']), s(x['priority_tier']),
                 s(x['tier_meaning']), s(x['safety_override']), diagnose, carry, s(x['fix']), s(x['not_ours_if']),
                 s(x['sop_source']), i(x['complaints']), n(x['avg_days_to_resolve']),
                 n(x['typical_days_to_resolve']), n(x['comes_back_after_fix_pct']), s(x['who_acted_in_data'])))
            sop_ids[x['complaint_type']] = cur.fetchone()[0]

        for c in comps:
            vb = s(c['viewed_by'])
            holder = viewed_by_to_post(vb)
            cur.execute(
                """INSERT INTO complaints (id, complaint_date, complainant, complaint_type, title, details_en, details_ta,
                     specific_location, locality, street, ward_no, zone_name, status, status_group, viewed_by,
                     holder_post_id, sla_days, is_overdue, is_safety_risk, safety_reasons, urgency_label, urgency_score,
                     issue, issue_group, department, whats_failed, whats_to_resolve, hotspot_tier, repeat_cluster_id,
                     repeat_count, recurred_after_fix, officer_message_count, last_activity_at, tweet_url, source_url,
                     last_fetched_at, raw)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           %s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status, status_group=EXCLUDED.status_group,
                     viewed_by=EXCLUDED.viewed_by, holder_post_id=EXCLUDED.holder_post_id,
                     is_overdue=EXCLUDED.is_overdue, is_safety_risk=EXCLUDED.is_safety_risk,
                     urgency_label=EXCLUDED.urgency_label, urgency_score=EXCLUDED.urgency_score,
                     officer_message_count=EXCLUDED.officer_message_count,
                     last_activity_at=EXCLUDED.last_activity_at, last_fetched_at=EXCLUDED.last_fetched_at,
                     raw=EXCLUDED.raw""",
                (c['complaint_id'], ts(c['complaint_date']), s(c['complainant_display']), c['complaint_type'],
                 s(c['complaint_title']), s(c['details_en']) or s(c['details_of_complaint']), s(c['details_ta']),
                 s(c['specific_location_en']) or s(c['complaint_specific_location']), s(c['locality']), s(c['street']),
                 i(c['ward_id']), s(c['zone_name']), c['status'], c['status_group'], vb,
                 holder if holder in post_ids else None, n(c['sla_days']), b(c['is_overdue']), b(c['is_safety_risk']),
                 s(c['safety_reasons']), s(c['urgency_label']), i(c['urgency_score']), s(c['issue']),
                 s(c['issue_group']), s(c['department']), s(c['whats_failed_en']) or s(c['whats_failed']),
                 s(c['whats_to_resolve_en']) or s(c['whats_to_resolve']), s(c['hotspot_tier']),
                 i(c['repeat_cluster_id']), i(c['repeat_count']), b(c['recurred_after_fix']),
                 i(c['officer_message_count']), ts(c['last_activity_at']), s(c['tweet_url']), s(c['source_url']),
                 s(c['last_fetched_at']), json.dumps(c, ensure_ascii=False)))

        mapping = {}
        for c in comps:
            t = c['complaint_type']
            if t not in mapping:
                sop, score = match_sop(t, c['issue_group'], sops)
                mapping[t] = (sop_ids[sop['complaint_type']] if sop else None, score,
                              sop['complaint_type'] if sop else None)
        for t, (sid, score, _) in mapping.items():
            cur.execute('INSERT INTO type_sop_map VALUES (%s,%s,%s)', (t, sid, score))

        for e in events:
            cur.execute('INSERT INTO complaint_events VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (e['complaint_id'], i(e['seq']), s(e['author']), s(e['author_type']), s(e['role']),
                         ts(e['at']), s(e['message_en']) or s(e['message']), s(e['message_ta']), s(e['forwarded_to'])))

        n_media = 0
        for m in img:
            if m['status'] != 'ok':
                continue
            cur.execute('INSERT INTO media (complaint_id, file, kind, uploader, stage, event_seq, author, source_url, '
                        'bytes, duplicate_of) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (file) DO NOTHING',
                        (m['complaint_id'], m['file'], 'video' if 'video' in m['content_type'] else 'photo',
                         m['uploader'], s(m['stage']), i(m['event_seq']), s(m['author']), s(m['source_url']),
                         i(m['bytes']), s(m['same_as'])))
            n_media += cur.rowcount
        for m in xmed:
            if m['status'] != 'ok' or not s(m['file']):
                continue
            cur.execute('INSERT INTO media (complaint_id, file, kind, uploader, source_url, bytes) '
                        'VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (file) DO NOTHING',
                        (m['complaint_id'], m['file'], 'video' if m['media_type'] != 'photo' else 'photo',
                         'complainant', s(m['source_url']), i(m['bytes'])))
            n_media += cur.rowcount
        db.commit()

        # owner post per complaint type (SOP owner role -> the ward's post for it)
        from backend.app.tools import owner_post
        cur.execute('SELECT DISTINCT c.complaint_type, s.owner_role, c.ward_no FROM complaints c '
                    'JOIN type_sop_map m USING (complaint_type) JOIN sops s ON s.id = m.sop_id')
        for ctype, role, ward in cur.fetchall():
            op = owner_post(role, ward or settings.ward_no)
            cur.execute('UPDATE complaints SET owner_post_id = %s WHERE complaint_type = %s',
                        (op['post_id'] if op else None, ctype))
        db.commit()
        cur.execute('SELECT count(*) FILTER (WHERE owner_post_id IS NULL), '
                    "count(*) FILTER (WHERE owner_post_id IS NULL AND status_group = 'open') FROM complaints")
        print('complaints without an owner post in records: %s (open %s)' % cur.fetchone())

        for t in ('complaints', 'complaint_events', 'media', 'officials', 'reporting_lines', 'ward_map', 'sops',
                  'type_sop_map', 'role_ladder', 'data_dictionary'):
            cur.execute(f'SELECT count(*) FROM {t}')
            print(f'{t:18} {cur.fetchone()[0]}')
        weak = [(t, v[2], v[1]) for t, v in mapping.items() if v[1] < 0.15]
        print(f'type->SOP matches: {len(mapping)}, weak (<0.15): {len(weak)}')
        for w in weak:
            print('   weak:', w)


if __name__ == '__main__':
    main()
