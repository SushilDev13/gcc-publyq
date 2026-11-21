"""Seeded DB mirrors the source CSVs exactly — no invented rows."""
import csv

from backend.app import db
from backend.app.config import ROOT
from backend.scripts.seed import viewed_by_to_post


def csv_count(rel: str) -> int:
    with open(ROOT / rel, newline='', encoding='utf-8') as f:
        return sum(1 for _ in csv.DictReader(f))


def test_row_counts_match_csvs():
    assert db.fetchone('SELECT count(*) n FROM complaints')['n'] == csv_count('data/complaints/ward173_complaints.csv')
    assert db.fetchone('SELECT count(*) n FROM complaint_events')['n'] == csv_count('data/complaints/ward173_events.csv')
    assert db.fetchone('SELECT count(*) n FROM officials')['n'] == csv_count('data/officials/gcc_officials.csv')
    assert db.fetchone('SELECT count(*) n FROM sops')['n'] == csv_count('data/officials/gcc_complaint_sop.csv')
    assert db.fetchone('SELECT count(*) n FROM ward_map')['n'] == csv_count('data/officials/gcc_ward_map.csv')


def test_every_complaint_type_has_an_sop():
    missing = db.fetchall('SELECT DISTINCT c.complaint_type FROM complaints c '
                          'LEFT JOIN type_sop_map m USING (complaint_type) WHERE m.sop_id IS NULL')
    assert missing == []


def test_viewed_by_codes_map_to_posts():
    assert viewed_by_to_post('AEDN173') == 'AE-D173'
    assert viewed_by_to_post('AEE3NZ13') == 'AEE3-Z13'
    assert viewed_by_to_post('AELD173') == 'AEL-D173'
    assert viewed_by_to_post('SO3NZ13') == 'SO3-Z13'
    assert viewed_by_to_post('-') is None


def test_ward_173_owner_posts_exist():
    w = db.fetchone('SELECT * FROM ward_map WHERE ward_no = 173')
    for col in ('assistant_engineer', 'sanitary_inspector', 'electrical_ae'):
        assert db.fetchone('SELECT 1 FROM officials WHERE post_id = %s', (w[col],))
