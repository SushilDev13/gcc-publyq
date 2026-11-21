"""Which language the person is speaking, from the words alone: English, Tamil or Hindi.

A call hears all three (ARES with en-IN,ta-IN,hi-IN), and ARES often writes Indian-accented ENGLISH in Devanagari or
Tamil script ("ओके", "कंप्लेंट", "ஸ்டேட்டஸ்"). So the script alone can't decide, and in a call a wrong switch restarts
the agent. Grammar decides instead: real Hindi carries its function words (है, का, में, क्या…), real Tamil its
question words and case endings (என்ன, -கள், -இல், -க்கு…); English that is only transliterated carries neither.

  - Asking for a language ("reply in Hindi", "switch to English", "தமிழில்", "हिंदी में") switches to it; merely
    naming one ("Tamil Nadu Housing Board", "is the board in Tamil or English") does not.
  - Hindi or Tamil grammar, in words that outweigh the English ones, switches to that language.
  - Four or more English words with no Hindi/Tamil grammar mean English, whatever the script.
  - Anything shorter ("ok", "यस", "ஓகே", an ID) keeps the language already in use.
"""
from __future__ import annotations

import re

LANGS = ('en', 'ta', 'hi')
DEFAULT = 'en'

_NAMES = {'en': 'english', 'ta': 'tamil', 'hi': 'hindi'}
_VERB = r'(?:speak|talk|reply|answer|respond|explain|say\s+it|tell\s+me|continue|switch|change|go)'
_NATIVE_ASK = {'en': r'ஆங்கிலத்தில்|இங்கிலீஷ்ல|अंग्रेज़ी में|अंग्रेजी में|इंग्लिश में',
               'ta': r'தமிழில்|தமிழ்ல|तमिल में',
               'hi': r'இந்தியில்|ஹிந்தியில்|ஹிந்தில|हिंदी में|हिन्दी में'}
_ASK = [(re.compile(rf'\b{_VERB}\s+(?:(?:to|with)\s+me\s+)?(?:back\s+)?(?:(?:in|to|into)\s+)?{n}\b(?!\s+nadu)'
                    rf'|\b{n}\s+please\b|^\W*(?:in\s+)?{n}\W*$|{_NATIVE_ASK[k]}', re.I), k)
        for k, n in _NAMES.items()]
_NAMED = {'en': re.compile(r'\benglish\b|ஆங்கில|இங்கிலீஷ்|अंग्रेज़ी|अंग्रेजी|इंग्लिश', re.I),
          'ta': re.compile(r'\btamil\b(?!\s+nadu)|தமிழ்|तमिल', re.I),
          'hi': re.compile(r'\bhindi\b|இந்தி|ஹிந்தி|हिंदी|हिन्दी', re.I)}

_TA_CH = re.compile(r'[஀-௿]')
_HI_CH = re.compile(r'[ऀ-ॿ]')
_LATIN = re.compile(r'[A-Za-z]')
_WORD_EDGE = re.compile(r'^[^\w஀-௿ऀ-ॿ]+|[^\w஀-௿ऀ-ॿ]+$')

# Hindi words that only Hindi grammar produces. WEAK ones double as transliterated English (key->की, he->ही,
# say->से, per->पर), so they count only in pairs.
_HI_STRONG = set('''है हैं हूँ हूं था थी थे का के को में ने और भी नहीं हाँ हां क्या कितना कितनी कितने कौन कौनसा कब कहाँ
कहां क्यों कैसे मेरे मेरा मेरी मुझे मैं आप आपका आपके आपकी तुम हम हमें यह ये वह वो इस उस इसे उसे इन उन इसका उसका कर करो
करें करना करिए कीजिए दीजिए बताओ बताइए बताना बताएं दिखाओ दिखाइए दिखाएं चाहिए अभी आज कल ठीक अच्छा सब कुछ एक शिकायत
शिकायतें काम स्थिति खुली खुले धन्यवाद नमस्ते बोलो बोलिए रहा रही रहे गया गई गए सकते सकता सकती वाला वाली वाले लिए जल्दी
यहाँ यहां वहाँ वहां अपना अपनी अपने पूछें पूछिए पूछो लिखें लिखिए लिखो चुनिए बातचीत पिछली पिछला बाकी जारी समस्या समस्याएँ
समस्याएं सुरक्षा जोखिम पद सफाई कचरा सड़क पानी बिजली नाली गड्ढा इलाके इलाका वार्ड'''.split())
_HI_WEAK = set('की से पर ही तो जी ना हो दो दे'.split())
# Tamil words that only Tamil grammar produces, plus case/verb endings on native (non-Grantha) words.
_TA_STRONG = set('''என்ன எத்தனை எங்கே எங்க எப்போது எப்போ ஏன் யார் எது இருக்கு இருக்கிறது இருக்கா இருக்கின்றன உள்ளது உள்ளன
இல்லை இல்ல ஆமா ஆம் சரி வேண்டும் வேணும் சொல்லு சொல்லுங்கள் சொல்லுங்க காட்டு காட்டுங்கள் காட்டுங்க பற்றி ஒரு இந்த அந்த
நான் நீங்கள் நீங்க என் எனக்கு உங்கள் உங்க இன்று இன்னைக்கு நாளை நேற்று வேலை புகார் புகார்கள் பண்ணு பண்ணுங்க செய்
செய்யுங்கள் மீண்டும் எல்லா எல்லாம் நன்றி வணக்கம் போதும் கொடு கொடுங்க பதிவு அழைப்பு அபாயம் பாதுகாப்பு குப்பை சாலை
தண்ணீர் மின்சாரம் கால்வாய் பள்ளம் தெரு பகுதி முன்பு'''.split())
_TA_GRANTHA = re.compile(r'[ஜஷஸஹஶ]')
_TA_ENDING = re.compile(r'(கள|ில்$|க்கு|கிற|ங்க|றது|ும்$|ை$|ைக்$|ாக$|ிடம்$|ோடு$|ுடன்$|து$|ேன்$|ோம்$|ிருந்து|ப்பட்ட)')


def _grammar(words: list[str]) -> dict:
    """Per language: how many words are in its script, and whether its grammar is present."""
    out = {k: {'words': 0, 'strong': 0, 'weak': 0} for k in LANGS}
    for w in words:
        hi, ta, latin = len(_HI_CH.findall(w)), len(_TA_CH.findall(w)), len(_LATIN.findall(w))
        if hi and hi >= ta:
            out['hi']['words'] += 1
            out['hi']['strong'] += w in _HI_STRONG
            out['hi']['weak'] += w in _HI_WEAK
        elif ta:
            if latin:                                       # "complaint-ஐ": a Tamil case ending on an English word
                out['en']['words'] += 1
                out['ta']['strong'] += 1
                continue
            out['ta']['words'] += 1
            out['ta']['strong'] += w in _TA_STRONG or (not _TA_GRANTHA.search(w) and bool(_TA_ENDING.search(w)))
        elif latin:
            out['en']['words'] += 1
    for k in ('hi', 'ta'):
        g = out[k]
        g['grammar'] = g['strong'] >= 1 or g['weak'] >= 2
    return out


def detect(text: str, current: str | None = None) -> str:
    current = current if current in LANGS else DEFAULT
    t = text or ''
    named = sum(bool(p.search(t)) for p in _NAMED.values())
    asked = {lang for pat, lang in _ASK if pat.search(t)}
    if len(asked) == 1 and named == 1:   # naming several ("Tamil, English or Hindi") is a mention, not a request
        return asked.pop()
    words = [w for w in (_WORD_EDGE.sub('', x) for x in t.split()) if w]
    g = _grammar(words)
    en = g['en']['words']
    # Hindi/Tamil grammar that isn't swamped by English words (Hinglish/Tanglish with English nouns still counts).
    native = [k for k in ('hi', 'ta') if g[k]['grammar'] and g[k]['words'] * 2 >= en]
    if native:
        native.sort(key=lambda k: (g[k]['words'], k == current), reverse=True)
        return native[0]
    # No Hindi/Tamil grammar: four or more words are English, whether ARES wrote them in Latin or not.
    if en + g['hi']['words'] + g['ta']['words'] > 3:
        return 'en'
    return current
