"""Shared tokenizer for matching complaint wording to complaint types / SOPs."""
import re

SYN = {'coc': 'corporation', 'unauthorized': 'unauthorised', 'manholes': 'manhole', 'lights': 'light',
       'bins': 'bin', 'trees': 'tree', 'drains': 'drain', 'pot': 'pothole', 'mosquitoes': 'mosquito',
       'sweepers': 'sweeping', 'advertisement': 'hoarding', 'construction': 'building',
       'collector': 'collection', 'lighting': 'light', 'garbage': 'garbage', 'trash': 'garbage',
       'rubbish': 'garbage', 'waste': 'garbage', 'potholes': 'pothole', 'lamp': 'streetlight',
       'streetlamp': 'streetlight', 'dengue': 'mosquito', 'malaria': 'mosquito', 'flooding': 'stagnation',
       'flooded': 'stagnation', 'waterlogging': 'stagnation', 'banner': 'hoarding', 'banners': 'hoarding',
       'poster': 'hoarding', 'posters': 'hoarding', 'cables': 'cable', 'wires': 'cable', 'wire': 'cable',
       'cow': 'cattle', 'cows': 'cattle', 'dog': 'dogs', 'sewage': 'sewage', 'rubble': 'debris', 'fell': 'fallen', 'falling': 'fallen', 'uprooted': 'fallen'}
STOP = {'of', 'the', 'to', 'in', 'a', 'and', 'any', 'on', 'regarding', 'complaints', 'complaint', 'other', 'no',
        'for', 'is', 'are', 'not', 'near', 'at', 'there', 'this', 'that', 'with', 'from', 'our', 'my', 'it',
        'street', 'road', 'nagar', 'main', 'cross', 'avenue', 'please', 'since', 'days', 'very', 'has', 'have',
        'illegal', 'unauthorised', 'unauthorized'}  # modifiers, not the problem itself


def toks(text: str) -> set[str]:
    t = (text or '').lower().replace('-', ' ').replace('street light', 'streetlight')
    out = set()
    for w in re.findall(r'[a-z]+', t):
        w = SYN.get(w, w)
        if w in STOP or len(w) < 3:
            continue
        out.add(w[:-1] if len(w) > 4 and w.endswith('s') else w)
    return out
