"""Which language the person is speaking, from the words alone: English, Tamil or Hindi.

A call hears all three (ARES with en-IN,ta-IN,hi-IN) and writes each in its own script, so the script decides:
Tamil letters -> 'ta', Devanagari -> 'hi', otherwise 'en'. Short Latin replies ("ok", "confirm", an ID) keep the
language already in use, so a Tamil conversation doesn't flip to English on "ok". Asking for a language
("in Hindi", "தமிழில்", "हिंदी में") switches to it.
"""
from __future__ import annotations

import re

LANGS = ('en', 'ta', 'hi')
DEFAULT = 'en'

_TA = re.compile(r'[஀-௿]')
_HI = re.compile(r'[ऀ-ॿ]')
_LATIN = re.compile(r'[A-Za-z]')
_ASK = [(re.compile(r'\b(in|speak|talk|reply)\s+(in\s+)?english\b|ஆங்கிலத்தில்|अंग्रेज़ी में|अंग्रेजी में|इंग्लिश में', re.I), 'en'),
        (re.compile(r'\b(in|speak|talk|reply)\s+(in\s+)?tamil\b|தமிழில்|तमिल में', re.I), 'ta'),
        (re.compile(r'\b(in|speak|talk|reply)\s+(in\s+)?hindi\b|இந்தியில்|हिंदी में|हिन्दी में', re.I), 'hi')]


def detect(text: str, current: str | None = None) -> str:
    current = current if current in LANGS else DEFAULT
    t = text or ''
    for pat, lang in _ASK:
        if pat.search(t):
            return lang
    ta, hi, latin = len(_TA.findall(t)), len(_HI.findall(t)), len(_LATIN.findall(t))
    if ta or hi:
        return 'ta' if ta >= hi else 'hi'
    if latin and len(t.split()) > 3:
        return 'en'
    return current
