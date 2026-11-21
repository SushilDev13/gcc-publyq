import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import db  # noqa: E402

TEST_USER_BASE = 9_000_000_000


def as_user(who: str = 'test-device-1', admin: bool = False) -> dict:
    """Request headers for a signed-in test person named `who` (a session row, no Telegram call)."""
    import hashlib

    from backend.app import auth
    uid = TEST_USER_BASE + int(hashlib.sha1(who.encode()).hexdigest()[:8], 16)
    db.execute("""INSERT INTO users (id, name, is_admin) VALUES (%s, %s, %s)
                  ON CONFLICT (id) DO UPDATE SET is_admin = EXCLUDED.is_admin""", (uid, who, admin))
    token = f'test-token-{uid}'
    db.execute("""INSERT INTO user_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, now() + interval '1 day')
                  ON CONFLICT (token_hash) DO NOTHING""", (auth._hash(token), uid))
    return {'Cookie': f'{auth.COOKIE}={token}'}


def owner_of(who: str) -> str:
    import hashlib
    return f"tg:{TEST_USER_BASE + int(hashlib.sha1(who.encode()).hexdigest()[:8], 16)}"


def pytest_collection_modifyitems(config, items):
    from backend.app.config import settings
    if 'localhost' not in settings.database_url and '127.0.0.1' not in settings.database_url:
        raise pytest.UsageError('Refusing to run tests against a non-local DATABASE_URL')
    try:
        db.fetchone('SELECT 1 FROM complaints LIMIT 1')
    except Exception as e:  # pragma: no cover
        skip = pytest.mark.skip(reason=f'database not reachable/seeded: {e}')
        for it in items:
            it.add_marker(skip)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Tests never call Claude or Telegram unless a test stubs them explicitly."""
    from backend.app.config import settings
    monkeypatch.setattr(settings, 'anthropic_api_key', '')
    monkeypatch.setattr(settings, 'bot_token', '')
    monkeypatch.setattr(settings, 'group_chat_id', '')


@pytest.fixture(autouse=True)
def clean_workspace():
    """Each test starts with an empty workspace ledger; source tables untouched."""
    db.execute("DELETE FROM communications WHERE session_id LIKE 'test-%'")
    db.execute("DELETE FROM actions WHERE session_id LIKE 'test-%'")
    db.execute("DELETE FROM turns WHERE session_id LIKE 'test-%'")
    db.execute("DELETE FROM conversations WHERE id LIKE 'test-%'")
    db.execute('DELETE FROM users WHERE id >= %s', (TEST_USER_BASE,))
    yield
