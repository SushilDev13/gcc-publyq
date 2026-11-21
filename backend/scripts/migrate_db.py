"""Copy the whole local Postgres (records + workspace: actions, communications,
conversations, turns) to Neon, then prove it arrived intact.

    .venv/bin/python -m backend.scripts.migrate_db              # target = PROD_DATABASE_URL in .env
    .venv/bin/python -m backend.scripts.migrate_db --target-env SOME_URL_VAR
    .venv/bin/python -m backend.scripts.migrate_db --replace    # target already has tables: drop + reload
    # Neon created through the Vercel integration:
    vercel env pull .env.vercel --environment=production
    .venv/bin/python -m backend.scripts.migrate_db --env-file .env.vercel --target-env DATABASE_URL_UNPOOLED

Steps: refuse a non-empty target unless --replace -> pg_dump | pg_restore over the
*direct* endpoint (the -pooler endpoint is transaction-pooled and unsafe for a
restore) -> re-apply schema.sql (creates the gcc_reader role and its grants,
which a privilege-free dump leaves out) -> compare row counts table by table.
Connection strings are read from .env and never printed.
"""
import argparse
import subprocess
import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.app.config import ROOT, settings  # noqa: E402

PGBIN = Path('/opt/homebrew/opt/postgresql@18/bin')
TABLES = ['complaints', 'complaint_events', 'media', 'officials', 'reporting_lines', 'ward_map', 'sops',
          'type_sop_map', 'role_ladder', 'data_dictionary', 'actions', 'communications', 'conversations', 'turns']


def counts(url: str) -> dict:
    with psycopg.connect(url, connect_timeout=20) as c:
        have = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables "
                                        "WHERE table_schema = 'public'").fetchall()}
        return {t: (c.execute(f'SELECT count(*) FROM {t}').fetchone()[0] if t in have else None) for t in TABLES}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--target-env', default='PROD_DATABASE_URL')
    ap.add_argument('--replace', action='store_true')
    ap.add_argument('--env-file', default='.env', help='file holding the target URL (e.g. .env.vercel)')
    a = ap.parse_args()

    target = (dotenv_values(ROOT / a.env_file).get(a.target_env) or '').strip().strip('"\'')
    if not target.startswith(('postgres://', 'postgresql://')):
        print(f'{a.target_env} in .env is not a postgres:// connection string.')
        return 2
    direct = target.replace('-pooler.', '.')      # Neon: direct endpoint for the restore
    source = settings.database_url
    if direct == source:
        print('Target equals the source database; refusing.')
        return 2

    before = counts(direct)
    existing = [t for t, n in before.items() if n is not None]
    if existing and not a.replace:
        print(f'Target already has tables: {", ".join(existing)}. Re-run with --replace to drop and reload them.')
        return 3

    print('Dumping local database and restoring to target...')
    dump = subprocess.Popen([str(PGBIN / 'pg_dump'), '--format=custom', '--no-owner', '--no-privileges',
                             '--dbname', source], stdout=subprocess.PIPE)
    restore = subprocess.run([str(PGBIN / 'pg_restore'), '--no-owner', '--no-privileges', '--exit-on-error',
                              *(['--clean', '--if-exists'] if a.replace else []), '--dbname', direct],
                             stdin=dump.stdout, capture_output=True, text=True)
    dump.stdout.close()
    if dump.wait() != 0 or restore.returncode != 0:
        print('Restore failed:', (restore.stderr or '').strip()[-800:])
        return 1

    with psycopg.connect(direct, connect_timeout=20, autocommit=True) as c:   # role + grants
        c.execute((ROOT / 'backend' / 'db' / 'schema.sql').read_text())

    src, dst = counts(source), counts(direct)
    ok = True
    print(f'{"table":18} {"local":>7} {"target":>7}')
    for t in TABLES:
        mark = '' if src[t] == dst[t] else '  <-- MISMATCH'
        ok &= src[t] == dst[t]
        print(f'{t:18} {src[t]!s:>7} {dst[t]!s:>7}{mark}')
    with psycopg.connect(direct, connect_timeout=20) as c:
        reader_ok = c.execute("SELECT has_table_privilege('gcc_reader', 'complaints', 'SELECT') AND NOT "
                              "has_table_privilege('gcc_reader', 'turns', 'SELECT')").fetchone()[0]
    print('read-only role gcc_reader:', 'ok' if reader_ok else 'WRONG')
    print('MIGRATION VERIFIED' if ok and reader_ok else 'MIGRATION INCOMPLETE')
    return 0 if ok and reader_ok else 1


if __name__ == '__main__':
    sys.exit(main())
