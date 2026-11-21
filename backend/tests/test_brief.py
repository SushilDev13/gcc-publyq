"""Morning brief: deterministic, factual, linkable, Telegram-safe."""
import re

from backend.app import brief


def test_tamil_brief_content():
    b = brief.build('ta')
    h = b['html']
    assert h.startswith('☀️ <b>காலை சுருக்கம் · வார்டு 173</b>')
    assert 'நிலுவையில் 96' in h and 'பாதுகாப்பு அபாயம் 26' in h
    assert '<b>உதவிப் பொறியாளர்</b> (AE-D173) · நிலுவையில் 80' in h          # GCC's Tamil post title
    assert 'நிலுவையில் புகார்கள் இல்லை ✅' in h                                  # AE-Electrical: nothing open
    assert 'AE-D173 வசம் 80' in h and '10 புகார்களுக்கு பொறுப்பாளர் பதவி இல்லை' in h
    assert b['chars'] < 4096                                                    # Telegram message limit


def test_picks_are_distinct_and_correct():
    ae = brief.role_section('AE-D173')
    ids = [p['id'] for p in ae['picks'].values()]
    assert len(ids) == len(set(ids)) == 3
    assert ae['picks']['danger']['id'] == '2026-185OET' and ae['picks']['oldest']['id'] == '2026-735GQS'


def test_links_open_the_app_in_role_and_language():
    h = brief.build('en')['html']
    assert 'https://gcc.publyq.in/?role=AE-D173&amp;lang=en&amp;ask=open%3A2026-185OET' in h
    assert 'https://gcc.publyq.in/?role=WARD&amp;lang=en&amp;ask=brief' in h
    assert 'app.publyq.in/#/complaints?range=all&amp;ward=173&amp;status=open&amp;safety=1' in h


def test_html_is_telegram_safe():
    h = brief.build('ta')['html']
    assert ' & ' not in h                                   # every ampersand escaped
    assert set(re.findall(r'</?(\w+)', h)) <= {'b', 'i', 'a'}   # only tags Telegram HTML supports
    assert 'None' not in h
