"""Sign-in with Telegram: only members of the Ward 173 group get in.

1. The Telegram Login Widget hands the browser a signed profile {id, first_name, ..., auth_date, hash}.
2. We check the signature (HMAC-SHA256 keyed with SHA-256(BOT_TOKEN), per Telegram's spec) and its age.
3. We ask Telegram whether that account is in the ward group (getChatMember). Not a member: no entry.
4. A random session token goes to the browser in an HttpOnly, Secure, SameSite=Lax cookie; the database keeps
   only its SHA-256. Membership is re-checked once a day, so removing someone from the group signs them out.

Admin: ADMIN_TELEGRAM_IDS, or the group's owner (creator). Everyone else picks a GCC post themselves.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from functools import lru_cache

import httpx

from . import db
from . import roles as R
from .config import settings

log = logging.getLogger('gcc.auth')

COOKIE = 'gcc_session'
SESSION_DAYS = 30
MAX_AUTH_AGE_S = 24 * 3600            # a widget payload older than this is refused (replay)
RECHECK_S = 24 * 3600                 # how often an active session re-confirms group membership
MEMBER = {'creator', 'administrator', 'member'}


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def configured() -> bool:
    return bool(settings.bot_token and settings.group_chat_id)


def owner_key(user_id: int) -> str:
    """What conversations.device_id stores for a signed-in person."""
    return f'tg:{int(user_id)}'


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── Telegram ──────────────────────────────────────────────────────────────
def verify_widget(data: dict, now: float | None = None) -> dict:
    """Check a Login Widget payload; returns it without the hash. Raises AuthError."""
    if not settings.bot_token:
        raise AuthError('Sign-in is not configured', 503)
    fields = {k: str(v) for k, v in data.items() if v is not None and k != 'hash'}
    got = str(data.get('hash') or '')
    check = '\n'.join(f'{k}={fields[k]}' for k in sorted(fields))
    key = hashlib.sha256(settings.bot_token.encode()).digest()
    want = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    if not got or not hmac.compare_digest(want, got):
        raise AuthError('Telegram sign-in could not be verified')
    try:
        age = (now or time.time()) - int(fields.get('auth_date', '0'))
        int(fields['id'])
    except (KeyError, ValueError):
        raise AuthError('Telegram sign-in could not be verified')
    if not -60 <= age <= MAX_AUTH_AGE_S:
        raise AuthError('That Telegram sign-in has expired; please sign in again')
    return fields


def member_status(user_id: int) -> str | None:
    """The account's status in the ward group per Telegram ('member', 'creator', ...), None if unknown."""
    r = httpx.get(f'https://api.telegram.org/bot{settings.bot_token}/getChatMember',
                  params={'chat_id': settings.group_chat_id, 'user_id': int(user_id)}, timeout=8)
    data = r.json()
    if not data.get('ok'):
        return None
    m = data['result']
    if m.get('status') == 'restricted':
        return 'member' if m.get('is_member') else None
    return m.get('status')


def admin_ids() -> set[int]:
    return {int(x) for x in settings.admin_telegram_ids.replace(' ', '').split(',') if x.isdigit()}


@lru_cache(maxsize=1)
def bot_username() -> str | None:
    if settings.telegram_bot_username:
        return settings.telegram_bot_username.lstrip('@')
    if not settings.bot_token:
        return None
    try:
        return httpx.get(f'https://api.telegram.org/bot{settings.bot_token}/getMe', timeout=8).json()['result']['username']
    except Exception:  # noqa: BLE001 - report as "not configured" to the page
        log.warning('getMe failed')
        return None


# ── sessions ──────────────────────────────────────────────────────────────
def sign_in(widget: dict) -> tuple[str, dict]:
    """Verify, check membership, upsert the person, open a session. Returns (cookie token, user)."""
    if not configured():
        raise AuthError('Sign-in is not configured', 503)
    p = verify_widget(widget)
    uid = int(p['id'])
    try:
        status = member_status(uid)
    except httpx.HTTPError:
        raise AuthError('Could not reach Telegram to check the ward group; try again', 503)
    is_admin = uid in admin_ids() or status == 'creator'
    if status not in MEMBER and not is_admin:
        raise AuthError('This Telegram account is not in the Ward 173 group. Ask the group admin to add you.', 403)
    name = ' '.join(x for x in (p.get('first_name'), p.get('last_name')) if x) or p.get('username') or 'Staff'
    user = db.execute("""INSERT INTO users (id, name, username, photo_url, is_admin) VALUES (%s,%s,%s,%s,%s)
                         ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, username = EXCLUDED.username,
                           photo_url = EXCLUDED.photo_url, is_admin = EXCLUDED.is_admin, last_seen_at = now()
                         RETURNING *""", (uid, name[:120], p.get('username'), p.get('photo_url'), is_admin))
    if user['role_id'] == R.ADMIN and not is_admin:          # lost admin since last time
        user = db.execute('UPDATE users SET role_id = NULL WHERE id = %s RETURNING *', (uid,))
    token = secrets.token_urlsafe(32)
    db.execute("""INSERT INTO user_sessions (token_hash, user_id, expires_at)
                  VALUES (%s, %s, now() + make_interval(days => %s))""", (_hash(token), uid, SESSION_DAYS))
    log.info('sign-in user=%s admin=%s', uid, is_admin)
    return token, user


def current_user(token: str | None) -> dict | None:
    """The signed-in person for a cookie token, or None. Re-checks group membership once a day."""
    if not token or len(token) > 200:
        return None
    row = db.fetchone("""SELECT u.*, s.verified_at, s.token_hash FROM user_sessions s JOIN users u ON u.id = s.user_id
                         WHERE s.token_hash = %s AND s.expires_at > now()""", (_hash(token),))
    if not row:
        return None
    if configured() and time.time() - row['verified_at'].timestamp() > RECHECK_S:
        try:
            status = member_status(row['id'])
        except httpx.HTTPError:
            status = 'member'               # Telegram unreachable: don't lock staff out; retry next request
        else:
            if status not in MEMBER and row['id'] not in admin_ids():
                db.execute('DELETE FROM user_sessions WHERE user_id = %s', (row['id'],))
                log.info('signed out user=%s: no longer in the ward group', row['id'])
                return None
            db.execute('UPDATE user_sessions SET verified_at = now() WHERE token_hash = %s', (row['token_hash'],))
    return row


def sign_out(token: str | None) -> None:
    if token:
        db.execute('DELETE FROM user_sessions WHERE token_hash = %s', (_hash(token),))


def set_role(user: dict, role_id: str | None) -> dict:
    if role_id is not None and role_id not in R.BY_ID:
        raise AuthError('Unknown post', 400)
    if role_id == R.ADMIN and not user['is_admin']:
        raise AuthError('Admin is not available to this account', 403)
    return db.execute('UPDATE users SET role_id = %s WHERE id = %s RETURNING *', (role_id, user['id']))


def public(user: dict) -> dict:
    return {'id': user['id'], 'name': user['name'], 'username': user['username'], 'photo_url': user['photo_url'],
            'role_id': user['role_id'], 'is_admin': user['is_admin']}
