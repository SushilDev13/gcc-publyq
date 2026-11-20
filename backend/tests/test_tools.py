"""Domain tools against the real seeded Ward 173 data."""
import pytest

from backend.app import tools as T

TRIAGE_CASES = [
    ('open manhole near the school in Gandhi Nagar, kids at risk', 'Covering Manholes of Storm Water Drain'),
    ('street light not working in kasturba nagar 3rd cross', 'Non burning of Street lights'),
    ('garbage not cleared for 3 days in indira nagar', 'Removal of Garbage'),
    ('construction debris dumped on the footpath in KB Nagar', 'Removal of Debris'),
    ('big pothole on LB road', 'Pot hole fill up / Repairs to the damaged surface'),
    ('illegal banners on the median in Thiruvanmiyur', 'Unauthorized Advertisement Boards'),
    ('tree fell on the road after rain', 'Removal of Fallen Trees'),
    ('water stagnation and mosquitoes near the park', 'Stagnation of Water'),
    ('cows roaming on the road', 'Stray Cattle'),
]


@pytest.mark.parametrize('text,expected', TRIAGE_CASES)
def test_triage_classifies(text, expected):
    assert T.triage(text)['complaint_type'] == expected


def test_triage_detects_locality_and_safety():
    r = T.triage('open manhole near the school in Gandhi Nagar')
    assert r['locality'] == 'gandhi Nagar'
    assert r['safety_signal'] and r['priority'].startswith('P1')
    assert r['owner']['post_id'] == 'AE-D173'


def test_search_orders_safety_first():
    r = T.search_complaints(status_group='open', limit=30)
    flags = [i['is_safety_risk'] for i in r['items']]
    assert flags == sorted(flags, reverse=True)
    assert r['total'] == 96


def test_dossier_has_sop_and_chain():
    c = T.get_complaint('2026-185OET')
    assert c['sop']['sop_type'] == 'Storm-water drain repairs & construction'
    assert c['responsible']['holder']['post_id'] == 'AE-D173'
    assert c['responsible']['escalate_to']['post_id'] == 'AEE3-Z13'


def test_spoken_id_forms_normalise():
    assert T.normalize_id('2026 185 OET') == '2026-185OET'
    assert T.normalize_id('185 o e t') == '2026-185OET'
    with pytest.raises(T.ToolError):
        T.get_complaint('2026-000XXX')


def test_escalation_chain_uses_ladder_above_zone():
    assert [p['post_id'] for p in T.get_official('AE-D173')['chain']] == ['AEE3-Z13', 'EE1-Z13']
    assert [p['post_id'] for p in T.get_official('SI-D173')['chain']] == ['SO3-Z13', 'ZHO-Z13']
