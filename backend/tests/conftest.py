import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import db  # noqa: E402


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
    yield
