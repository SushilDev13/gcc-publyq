"""Read-only SQL for the LLM's open-ended investigations.

Four independent guards, so a bad or adversarial query can't change data or
stall the service:
  1. single SELECT/WITH statement only (parsed check, no ';' chaining)
  2. runs as role gcc_reader: SELECT on records only, no workspace chat tables. Functions that could switch
     the role back (set_config) or run SQL from a string (query_to_xml, dblink, ...) are refused, and the
     result is thrown away if the role is not gcc_reader when the query finishes.
  3. READ ONLY transaction + 3 s statement timeout
  4. at most 50 rows / 8 KB returned to the model
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal

from psycopg import errors

from . import db, publyq

MAX_ROWS = 50
MAX_CHARS = 8000
FORBIDDEN = re.compile(r'\b(insert|update|delete|merge|drop|alter|create|grant|revoke|truncate|copy|call|do|'
                       r'vacuum|analyze|refresh|reset|set|listen|notify|lock|prepare|execute|comment|security)\b',
                       re.I)
# set_config('role', ...) would undo SET ROLE; these run a SQL string or touch files/backends. U&"..." spells
# identifiers with escapes, which would hide any of them.
FORBIDDEN_FN = re.compile(r'\b(set_config|current_setting|\w*_to_xml\w*|dblink\w*|pg_read_\w+|pg_ls_\w+|pg_stat_file|'
                          r'lo_\w+|pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_advisory\w*)\b|\bu&["\']',
                          re.I)

SCHEMA_GUIDE = """Postgres tables (Ward 173, Adyar zone 13; GCC complaint records Jan-Nov 2026; posts not people):
complaints(id, complaint_date timestamp IST, complainant, complaint_type, title, details_en, specific_location, locality,
  street, ward_no, zone_name, status, status_group['open'|'resolved'|'rejected'], viewed_by, holder_post_id, owner_post_id,
  sla_days, is_overdue bool, is_safety_risk bool, safety_reasons, urgency_label, urgency_score, issue, issue_group,
  issue_group_id (Publyq category key: debris, garbage, drains, roads, streetlights, hoardings, encroachment, ...),
  department, whats_failed, whats_to_resolve, hotspot_tier['recurring'|'repeat'|...], repeat_cluster_id, repeat_count,
  recurred_after_fix bool, officer_message_count, last_activity_at, tweet_url, source_url, last_fetched_at, raw jsonb)
complaint_events(complaint_id, seq, author, author_type['officer'|'qa'], role, at, message_en, forwarded_to)
media(complaint_id, file, kind['photo'|'video'], uploader['complainant'|'officer'], stage['before'|'after'], source_url)
officials(post_id, post, role, department_line, level['Ward'|'Zone'...], zone_no, ward_no, reports_to_post_id,
  complaints_all, open_now, open_overdue, open_safety_risk, resolved, avg_days_to_resolve, typical_days_to_resolve,
  typical_hours_to_first_action, top_complaint_categories)   -- city-wide stats per post
reporting_lines(from_post_id, to_post_id, handoffs, share_pct, is_primary)
ward_map(ward_no, zone_no, zone_name, assistant_engineer, sanitary_inspector, electrical_ae, ward_committee_officer,
  mla, party, constituency)
sops(id, issue_group, sop_type, owner_role, approver_role, priority_tier, tier_meaning, safety_override, diagnose text[],
  carry text[], fix, not_ours_if, typical_days_to_resolve, comes_back_after_fix_pct)
type_sop_map(complaint_type, sop_id, score)   role_ladder(department_line, step, role, hands_up_to)
data_dictionary(file, col, meaning, source)
actions(id, complaint_id, kind, status, target_post_id, note, due_at, created_at)   -- requests logged in GCC Publyq
communications(id, action_id, complaint_id, channel, recipient, body, status, created_at)  -- Telegram messages sent
Localities: 'k.b.nagar', 'indira Nagar', 'gandhi Nagar', 'thiruvanmiyur', 'adyar', 'nehru Nagar', 'kamaraj Nagar'.
Use ILIKE for text matches. Dates: complaint_date. Always LIMIT."""


class QueryError(ValueError):
    pass


def _check(sql: str) -> str:
    s = sql.strip().rstrip(';').strip()
    if not s:
        raise QueryError('empty query')
    if ';' in s:
        raise QueryError('one statement only')
    if not re.match(r'^(select|with)\b', s, re.I):
        raise QueryError('only SELECT / WITH queries are allowed')
    stripped = re.sub(r"'(?:[^']|'')*'", "''", s)   # ignore words inside string literals
    if FORBIDDEN.search(stripped):
        raise QueryError('read-only: that statement type is not allowed')
    if FORBIDDEN_FN.search(s):                       # checked on the raw text: names can hide in quotes
        raise QueryError('not allowed: that function is blocked for record queries')
    return s


def _jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def run(sql: str) -> dict:
    s = _check(sql)
    try:
        with db.conn() as c:
            with c.transaction():
                c.execute('SET TRANSACTION READ ONLY')
                c.execute("SET LOCAL statement_timeout = '3s'")
                c.execute('SET LOCAL ROLE gcc_reader')
                cur = c.execute(s)
                cols = [d.name for d in cur.description or []]
                rows = cur.fetchmany(MAX_ROWS + 1)
                if c.execute('SELECT current_user u').fetchone()['u'] != 'gcc_reader':
                    raise QueryError('not allowed: the query changed its database role')
    except errors.QueryCanceled:
        raise QueryError('query took longer than 3 seconds; narrow it down')
    except errors.InsufficientPrivilege as e:
        raise QueryError(f'not allowed: {str(e).splitlines()[0]}')
    except errors.Error as e:
        raise QueryError(str(e).splitlines()[0])
    truncated = len(rows) > MAX_ROWS
    data = [{k: _jsonable(r[k]) for k in cols} for r in rows[:MAX_ROWS]]
    text = json.dumps(data, default=str)
    if len(text) > MAX_CHARS:
        text, truncated = text[:MAX_CHARS], True
    found = [v for r in data for v in r.values() if isinstance(v, str) and publyq.COMPLAINT_ID.match(v)]
    return {'columns': cols, 'row_count': len(data), 'truncated': truncated, 'rows_json': text,
            'publyq': publyq.complaints(found)}
