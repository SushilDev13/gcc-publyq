"""MCP server: GCC Publyq's tools for any MCP client (Claude Desktop, Claude Code, IDEs, agents).

Streamable HTTP transport, stateless, JSON responses: every request is a self-contained JSON-RPC
message, which suits serverless (no server-side session, no SSE stream to keep alive).

Same tools the voice agent uses (tools.py / dbquery.py / publyq.py), same role scoping and SQL
guards. Writes keep the two-phase rule: propose_action records nothing permanent; confirm_action
and cancel_action carry destructiveHint so MCP hosts ask the human before running them.
Protected by MCP_TOKEN (Authorization: Bearer …) because confirming posts to Telegram.
"""
from __future__ import annotations

import json
import logging

from . import dbquery, publyq
from . import roles as R
from . import tools as T
from .config import settings

log = logging.getLogger('gcc.mcp')

SERVER = {'name': 'gcc-publyq', 'title': 'GCC Publyq · Ward 173', 'version': '1.0.0'}
SUPPORTED = ('2026-07-28', '2025-11-25', '2025-06-18', '2025-03-26')
INSTRUCTIONS = (
    'GCC Publyq: Greater Chennai Corporation Ward 173 (Adyar, zone 13) complaint records, officials (posts, '
    'not people), SOPs and a workflow ledger. Pass role (a post such as AE-D173, AEL-D173, SI-D173, AEE3-Z13, '
    'or WARD) to see that post\'s queue and patterns. Actions are two-phase: propose_action, then '
    'confirm_action only after the human agrees. GCC\'s own portal has no write API: actions are recorded in '
    'GCC Publyq and, when they need someone else, posted to the Ward 173 Telegram group.\n' + dbquery.SCHEMA_GUIDE)

ROLE = {'type': 'string', 'enum': [r['id'] for r in R.ROLES if r['id'] != R.ADMIN], 'description': 'GCC post whose view to use'}
READ = {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}


def _role_args(args: dict) -> dict:
    rid = args.pop('role', None) or R.WARD
    if rid not in R.BY_ID:
        raise T.ToolError(f'unknown role {rid}')
    return {'scope': R.scope(rid), 'role_label': R.BY_ID[rid]['label'],
            **({'leadership': R.BY_ID[rid]['kind'] != 'field'} if args.pop('_queue', False) else {})}


def _my_queue(role: str | None = None) -> dict:
    a = _role_args({'role': role, '_queue': True})
    return T.my_queue(**a)


def _patterns(role: str | None = None) -> dict:
    a = _role_args({'role': role})
    return T.patterns(**a)


def _search(role: str | None = None, **kw) -> dict:
    if role:
        kw['scope'] = R.scope(role) if role in R.BY_ID else None
    return T.search_complaints(**kw)


def _propose(**kw) -> dict:
    kw['session_id'] = 'mcp'          # MCP proposals are confirmed through MCP (host asks the human)
    return T.propose_action(**kw)


def _confirm(action_id: int) -> dict:
    return T.confirm_action(_mcp_action(action_id))


def _cancel(action_id: int) -> dict:
    return T.cancel_action(_mcp_action(action_id))


def _mcp_action(action_id: int) -> int:
    row = T.db.fetchone('SELECT session_id FROM actions WHERE id = %s', (int(action_id),))
    if not row or row['session_id'] != 'mcp':
        raise T.ToolError(f'Action {action_id} was not proposed through MCP; confirm it where it was proposed.')
    return int(action_id)


def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {'type': 'object', 'properties': props, 'required': required or [], 'additionalProperties': False}


TOOLS: dict[str, dict] = {
    'my_queue': {'fn': _my_queue, 'title': 'Open work for a post',
                 'description': "A post's open complaints, most urgent first (safety, overdue, oldest).",
                 'schema': _schema({'role': ROLE}), 'annotations': READ},
    'patterns': {'fn': _patterns, 'title': 'Recurring patterns',
                 'description': 'Repeat spots, top and rising issues, comes-back-after-fix rate for a post.',
                 'schema': _schema({'role': ROLE}), 'annotations': READ},
    'search_complaints': {'fn': _search, 'title': 'Search complaints',
                          'description': 'Filter Ward 173 complaints; safety-risk first, then overdue.',
                          'schema': _schema({'role': ROLE,
                                             'status_group': {'type': 'string', 'enum': ['open', 'resolved', 'rejected', 'all']},
                                             'locality': {'type': 'string'}, 'issue_group': {'type': 'string'},
                                             'complaint_type': {'type': 'string'}, 'safety': {'type': 'boolean'},
                                             'overdue': {'type': 'boolean'}, 'q': {'type': 'string'},
                                             'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}}),
                          'annotations': READ},
    'get_complaint': {'fn': T.get_complaint, 'title': 'Complaint dossier',
                      'description': 'Details, officer actions, photos, SOP and priority, holder/owner/escalation '
                                     'posts, actions logged in GCC Publyq, Publyq link.',
                      'schema': _schema({'complaint_id': {'type': 'string', 'description': 'e.g. 2026-185OET'}},
                                        ['complaint_id']), 'annotations': READ},
    'query_db': {'fn': dbquery.run, 'title': 'Read-only SQL',
                 'description': 'One SELECT/WITH over the records (see server instructions for tables). '
                                'Read-only role, 3 s timeout, 50 rows.',
                 'schema': _schema({'sql': {'type': 'string'}}, ['sql']), 'annotations': READ},
    'get_sop': {'fn': T.get_sop, 'title': 'Standard operating procedure',
                'description': 'SOP for a complaint type or issue: priority, diagnose steps, what to carry, fix.',
                'schema': _schema({'topic': {'type': 'string'}}, ['topic']), 'annotations': READ},
    'find_officials': {'fn': T.find_officials, 'title': 'Ward officials',
                       'description': 'GCC posts for Ward 173 and zone 13, optionally by role.',
                       'schema': _schema({'role': {'type': 'string'}}), 'annotations': READ},
    'get_official': {'fn': T.get_official, 'title': 'One post',
                     'description': 'Workload, speed, escalation chain and open complaints a post holds.',
                     'schema': _schema({'post_id': {'type': 'string'}}, ['post_id']), 'annotations': READ},
    'triage': {'fn': T.triage, 'title': 'Triage a new report',
               'description': 'Classify a reported problem: complaint type, priority, owner post, duplicates.',
               'schema': _schema({'text': {'type': 'string'}}, ['text']), 'annotations': READ},
    'ward_brief': {'fn': T.ward_brief, 'title': 'Ward brief', 'description': 'Daily brief for Ward 173 (markdown).',
                   'schema': _schema({}), 'annotations': READ},
    'list_actions': {'fn': T.list_actions, 'title': 'Actions and follow-ups',
                     'description': 'Requests, escalations and follow-ups recorded in GCC Publyq.',
                     'schema': _schema({'status': {'type': 'string'}, 'kind': {'type': 'string'},
                                        'complaint_id': {'type': 'string'}, 'due_only': {'type': 'boolean'}}),
                     'annotations': READ},
    'list_communications': {'fn': T.list_communications, 'title': 'Telegram messages sent',
                            'description': 'Messages the platform posted (or failed to post) to Telegram.',
                            'schema': _schema({'complaint_id': {'type': 'string'}}), 'annotations': READ},
    'open_in_publyq': {'fn': publyq.open_link, 'title': 'Link into app.publyq.in',
                       'description': 'URL to view complaints in Publyq: exact IDs (one ID opens its detail), a '
                                      'filtered Ward 173 list, the ward map or accountability.',
                       'schema': _schema({'complaint_ids': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 100},
                                          'issue': {'type': 'string'}, 'category': {'type': 'string'},
                                          'status': {'type': 'string', 'enum': ['open', 'resolved', 'rejected']},
                                          'safety': {'type': 'boolean'}, 'overdue': {'type': 'boolean'},
                                          'view_name': {'type': 'string', 'enum': ['complaints', 'map', 'accountability']}}),
                       'annotations': READ},
    'propose_action': {'fn': _propose, 'title': 'Propose an action (nothing is sent yet)',
                       'description': 'Propose escalate / assign / note / follow_up / status_change / close_request / '
                                      'help_request on a complaint. Returns an action id; nothing is final until '
                                      'confirm_action, which needs the human to agree.',
                       'schema': _schema({'kind': {'type': 'string', 'enum': list(T.ACTION_KINDS)},
                                          'complaint_id': {'type': 'string'}, 'target_post_id': {'type': 'string'},
                                          'note': {'type': 'string'},
                                          'to_status': {'type': 'string', 'enum': list(T.REQUESTABLE_STATUS)},
                                          'due_in_days': {'type': 'number', 'minimum': 0, 'maximum': 60},
                                          'notify': {'type': 'boolean'}, 'role_id': ROLE},
                                         ['kind', 'complaint_id']),
                       'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False,
                                       'openWorldHint': False}},
    'confirm_action': {'fn': _confirm, 'title': 'Confirm a proposed action (may post to Telegram)',
                       'description': 'Records a proposed action; requests others must act on are posted to the '
                                      'Ward 173 Telegram group. Only call after the human explicitly agrees.',
                       'schema': _schema({'action_id': {'type': 'integer'}}, ['action_id']),
                       'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True,
                                       'openWorldHint': True}},
    'cancel_action': {'fn': _cancel, 'title': 'Cancel a proposed or confirmed action',
                      'description': 'Cancels an action proposed through MCP.',
                      'schema': _schema({'action_id': {'type': 'integer'}}, ['action_id']),
                      'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True,
                                      'openWorldHint': False}},
}


def tool_list() -> list[dict]:
    return [{'name': n, 'title': t['title'], 'description': t['description'], 'inputSchema': t['schema'],
             'annotations': {'title': t['title'], **t['annotations']}} for n, t in TOOLS.items()]


def _ok(rid, result: dict) -> dict:
    return {'jsonrpc': '2.0', 'id': rid, 'result': result}


def _err(rid, code: int, message: str) -> dict:
    return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': code, 'message': message}}


def _call(name: str, args: dict) -> dict:
    t = TOOLS.get(name)
    if not t:
        raise KeyError(name)
    allowed = set(t['schema']['properties'])
    unknown = set(args) - allowed
    if unknown:
        return {'content': [{'type': 'text', 'text': f'Unknown argument(s): {", ".join(sorted(unknown))}'}],
                'isError': True}
    try:
        res = t['fn'](**args)
    except (T.ToolError, dbquery.QueryError, ValueError, TypeError) as e:
        return {'content': [{'type': 'text', 'text': str(e)}], 'isError': True}
    text = json.dumps(res, default=str, ensure_ascii=False)
    return {'content': [{'type': 'text', 'text': text[:60000]}],
            'structuredContent': json.loads(json.dumps(res, default=str)), 'isError': False}


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message -> response (None for notifications)."""
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or 'method' not in msg:
        return _err(msg.get('id') if isinstance(msg, dict) else None, -32600, 'Invalid Request')
    rid, method, params = msg.get('id'), msg['method'], msg.get('params') or {}
    if rid is None:                                  # notification (e.g. notifications/initialized)
        return None
    if method == 'initialize':
        asked = params.get('protocolVersion')
        return _ok(rid, {'protocolVersion': asked if asked in SUPPORTED else SUPPORTED[0],
                         'capabilities': {'tools': {'listChanged': False}},
                         'serverInfo': SERVER, 'instructions': INSTRUCTIONS})
    if method == 'ping':
        return _ok(rid, {})
    if method == 'tools/list':
        return _ok(rid, {'tools': tool_list()})
    if method == 'tools/call':
        name, args = params.get('name'), params.get('arguments') or {}
        if not isinstance(args, dict):
            return _err(rid, -32602, 'arguments must be an object')
        try:
            result = _call(name, dict(args))
        except KeyError:
            return _err(rid, -32602, f'Unknown tool: {name}')
        log.info('mcp tool %s error=%s', name, result.get('isError'))
        return _ok(rid, result)
    return _err(rid, -32601, f'Method not found: {method}')
