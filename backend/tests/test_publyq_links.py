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


def test_tools_carry_publyq_links():
    from backend.app import dbquery, roles as R, tools as T
    s = T.search_complaints(safety=True, limit=5)
    assert s['publyq']['count'] == s['total'] == 26                     # whole matching set, not the 5 shown
    assert T.get_complaint('2026-185OET')['publyq']['url'].endswith('ids=2026-185OET&id=2026-185OET')
    spot = T.patterns(R.scope('AEL-D173'), 'x')['spots'][0]
    assert spot['publyq']['count'] == spot['n'] and len(spot['ids']) <= 5
    debris = next(i for i in T.patterns(R.scope('AE-D173'), 'x')['issues'] if i['issue'] == 'Debris removal')
    assert 'type=Debris+removal' in debris['publyq']['url']             # >100 ids -> ward-wide filter
    w = T.ward_summary()
    assert all('category=' in g['publyq']['url'] for g in w['open_by_issue'])
    assert w['publyq_links']['safety']['url'].endswith('status=open&safety=1')
    assert dbquery.run("select id from complaints where id = '2026-185OET'")['publyq']['count'] == 1
    assert dbquery.run('select count(*) from complaints')['publyq'] is None
