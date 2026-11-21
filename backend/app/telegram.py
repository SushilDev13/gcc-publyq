"""Telegram: the platform's only outbound channel (GCC has no write API).

Every attempt is recorded in `communications`, sent or not, so the platform
keeps the full trail of what was requested and who was told.
"""
from __future__ import annotations

import html
import logging

import httpx

from . import db
from .config import settings

log = logging.getLogger('gcc.telegram')
RECIPIENT = 'Ward 173 Telegram group'


def configured() -> bool:
    return bool(settings.bot_token and settings.group_chat_id)


def send(body_html: str, *, action_id: int | None = None, complaint_id: str | None = None,
         session_id: str | None = None) -> dict:
    status, ref, err = 'not_configured', None, None
    if configured():
        try:
            r = httpx.post(f'https://api.telegram.org/bot{settings.bot_token}/sendMessage',
                           json={'chat_id': settings.group_chat_id, 'text': body_html, 'parse_mode': 'HTML',
                                 'disable_web_page_preview': True}, timeout=10)
            data = r.json()
            if data.get('ok'):
                status, ref = 'sent', str(data['result']['message_id'])
            else:
                status, err = 'failed', str(data.get('description') or r.status_code)[:300]
        except httpx.HTTPError as e:
            status, err = 'failed', type(e).__name__
    else:
        err = 'BOT_TOKEN / GROUP_CHAT_ID not set'
    row = db.execute("""INSERT INTO communications (action_id, complaint_id, channel, recipient, body, status,
                                                    provider_ref, error, session_id)
                        VALUES (%s,%s,'telegram',%s,%s,%s,%s,%s,%s) RETURNING id, status, error, created_at""",
                     (action_id, complaint_id, RECIPIENT, body_html, status, ref, err, session_id))
    if status != 'sent':
        log.warning('telegram %s: %s', status, err)
    return {'id': row['id'], 'status': status, 'error': err, 'recipient': RECIPIENT}


def esc(s) -> str:
    return html.escape(str(s or ''), quote=False)
