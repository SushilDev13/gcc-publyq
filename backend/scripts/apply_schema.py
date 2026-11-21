"""Apply backend/db/schema.sql to a database that already has data (e.g. Neon after a release that adds tables).

schema.sql is idempotent (CREATE ... IF NOT EXISTS, ADD COLUMN IF NOT EXISTS) and re-applies the gcc_reader
grants, so running it again only adds what is missing. Nothing is dropped or rewritten.

    vercel env pull .env.vercel --environment=production
    .venv/bin/python -m backend.scripts.apply_schema --env-file .env.vercel --target-env NEON_DATABASE_URL_UNPOOLED

The connection string is read from the env file and never printed.
"""
import argparse
import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.app.config import ROOT  # noqa: E402

CHECK = ['users', 'user_sessions', 'conversations', 'turns', 'actions']


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--env-file', default=str(ROOT / '.env'))
    ap.add_argument('--target-env', default='DATABASE_URL', help='variable in the env file holding the URL')
    a = ap.parse_args()
    url = dotenv_values(a.env_file).get(a.target_env)
    if not url:
        print(f'{a.target_env} not found in {a.env_file}', file=sys.stderr)
        return 2
    with psycopg.connect(url, connect_timeout=20, autocommit=True) as c:
        c.execute((ROOT / 'backend' / 'db' / 'schema.sql').read_text())
        have = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables "
                                        "WHERE table_schema = 'public'").fetchall()}
    for t in CHECK:
        print(f"{t:15} {'ok' if t in have else 'MISSING'}")
    return 0 if all(t in have for t in CHECK) else 1


if __name__ == '__main__':
    sys.exit(main())
