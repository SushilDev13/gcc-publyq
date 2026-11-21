"""Deep links match Publyq's own URL format (frontend/src/state.js)."""
from urllib.parse import parse_qs, urlsplit

from backend.app import publyq as P


def q(url):
    frag = urlsplit(url).fragment            # '/complaints?range=all&...'
    path, _, query = frag.partition('?')
    return path, {k: v[0] for k, v in parse_qs(query).items()}


def test_group_of_complaints():
    link = P.complaints(['2026-185oet', ' 2026-735GQS', '2026-185OET', 'nonsense', '2026-11'])
    path, p = q(link['url'])
    assert link['url'].startswith('https://app.publyq.in/#/complaints?')
    assert path == '/complaints' and p == {'range': 'all', 'ids': '2026-185OET,2026-735GQS'}
    assert link['count'] == 2 and link['label'] == 'Open 2 in Publyq'
    assert P.complaints(['bad']) is None


def test_cap_at_100_is_reported():
    ids = [f'2026-{n:03d}ABC' for n in range(100, 230)]
    link = P.complaints(ids)
    assert link['count'] == 100 and link['truncated'] and len(q(link['url'])[1]['ids'].split(',')) == 100


def test_single_complaint_opens_detail_and_stays_in_results():
    _, p = q(P.complaint('2026-185oet')['url'])
    assert p == {'range': 'all', 'ids': '2026-185OET', 'id': '2026-185OET'}


def test_filtered_views_use_publyq_keys_in_order():
    url = P.view(issue='Debris removal', status='open', safety=True)['url']
    assert url == 'https://app.publyq.in/#/complaints?range=all&ward=173&type=Debris+removal&status=open&safety=1'
    assert q(P.view(category='debris')['url'])[1] == {'range': 'all', 'ward': '173', 'category': 'debris'}
    assert q(P.view(overdue=True, status='open')['url'])[1]['overdue'] == '1'
    assert q(P.ward_map()['url']) == ('/pulse', {'range': 'all', 'ward': '173'})
    assert q(P.accountability()['url'])[0] == '/acc'


def test_rejects_unknown_status():
    import pytest
    with pytest.raises(ValueError):
        P.view(status='closed')
