"""Soundtrack for brag.mp4: 120 BPM, D major, I-vi-IV-V, one chord per 2 s bar.
Music and effects share one beat grid counted from frame 0 and one reverb space.
writes work/mix.wav (44.1 kHz stereo)"""
import numpy as np, wave
from scipy.signal import butter, sosfilt, fftconvolve

SR, DUR, BPM = 44100, 22.0, 120
BEAT = 60 / BPM
N = int(SR * DUR)
rng = np.random.default_rng(173)
t_all = np.arange(N) / SR

def f(m): return 440 * 2 ** ((m - 69) / 12)
def buf(): return np.zeros(N)
def place(dst, sig, at, g=1.0):
    i = int(round(at * SR)); j = min(N, i + len(sig))
    if i < N: dst[i:j] += g * sig[: j - i]
def lp(x, hz, o=2): return sosfilt(butter(o, hz, 'low', fs=SR, output='sos'), x)
def hp(x, hz, o=2): return sosfilt(butter(o, hz, 'high', fs=SR, output='sos'), x)
def bp(x, lo, hi): return sosfilt(butter(2, [lo, hi], 'band', fs=SR, output='sos'), x)

# D major I–vi–IV–V : D  Bm  G  A
CHORDS = [[62, 66, 69, 74], [59, 62, 66, 71], [55, 59, 62, 67], [57, 61, 64, 69]]
ROOTS = [38, 35, 31, 33]
BARS = int(DUR / (4 * BEAT)) + 1

pad, pluck, bass, kick, hat, clap, sfx = (buf() for _ in range(7))

# pad: soft additive saw, detuned, per bar with overlap
for b in range(BARS):
    c = CHORDS[b % 4] if b < 9 or b >= 10 else CHORDS[b % 4]
    if b >= 9: c = CHORDS[0]                       # outro: home chord from 18 s
    L = 4 * BEAT + 0.6
    tt = np.arange(int(L * SR)) / SR
    env = np.minimum(1, tt / 0.25) * np.minimum(1, (L - tt) / 0.6)
    s = np.zeros_like(tt)
    for m in c:
        for det in (-0.06, 0.06):
            fr = f(m + det)
            for h in range(1, 9):
                s += np.sin(2 * np.pi * fr * h * tt + h) / h ** 1.6
    place(pad, s * env / 8, b * 4 * BEAT)
pad = lp(pad, 2400)

# pluck arpeggio on 8ths (intro + body), bell-ish
for k in range(int(DUR / (BEAT / 2))):
    at = k * BEAT / 2
    if at >= 19.0: break
    b = int(at // (4 * BEAT)); c = CHORDS[b % 4]
    m = c[[0, 2, 1, 3, 2, 1, 3, 2][k % 8]] + 12
    tt = np.arange(int(0.45 * SR)) / SR
    s = (np.sin(2 * np.pi * f(m) * tt) + 0.25 * np.sin(2 * np.pi * f(m) * 2 * tt)) * np.exp(-tt / 0.12)
    vel = 0.55 if k % 2 == 0 else 0.38
    if 11.5 <= at < 15.5: vel *= 0.7                  # make room under the UI moments
    place(pluck, s * vel, at)

# drums + bass from the first cut (3.0 s) to the outro hit (19.0 s)
def kick_s():
    tt = np.arange(int(0.35 * SR)) / SR
    fr = 45 + 75 * np.exp(-tt / 0.04)
    return np.sin(2 * np.pi * np.cumsum(fr) / SR) * np.exp(-tt / 0.16)
def hat_s():
    n = lp(hp(rng.standard_normal(int(0.06 * SR)), 6000), 10000)
    return n * np.exp(-np.arange(len(n)) / SR / 0.018)
def clap_s():
    n = bp(rng.standard_normal(int(0.2 * SR)), 900, 3200); tt = np.arange(len(n)) / SR
    e = sum(np.exp(-np.clip(tt - d, 0, None) / 0.01) * (tt >= d) for d in (0, .01, .022)) * 0.6 + np.exp(-tt / 0.09)
    return n * e
K, H, C = kick_s(), hat_s(), clap_s()
duck = np.ones(N)
for k in range(int(DUR / BEAT)):
    at = k * BEAT
    if 3.0 <= at < 19.0:
        place(kick, K, at)
        i = int(at * SR); w = int(0.25 * SR)
        duck[i:i + w] = np.minimum(duck[i:i + w], 0.55 + 0.45 * np.linspace(0, 1, min(w, N - i)))
        place(hat, H, at + BEAT / 2, 0.5)
        if k % 2 == 1: place(clap, C, at, 0.6)
for k in range(int(DUR / (BEAT / 2))):
    at = k * BEAT / 2
    if 3.0 <= at < 19.0:
        b = int(at // (4 * BEAT)); r = ROOTS[b % 4] + (12 if k % 4 == 3 else 0)
        tt = np.arange(int(0.24 * SR)) / SR
        s = np.tanh(1.6 * (np.sin(2 * np.pi * f(r) * tt) + 0.3 * np.sin(2 * np.pi * f(r) * 2 * tt))) * np.exp(-tt / 0.14)
        place(bass, s, at, 0.8)
bass = lp(bass, 900)

# effects, in key and on the grid
def whoosh(end, L=0.5, g=0.25):
    n = rng.standard_normal(int(L * SR)); tt = np.arange(len(n)) / SR
    s = lp(n, 1800) * (tt / L) ** 2.2
    place(sfx, s * g, end - L)
def tick(at, m=93, g=0.22):   # A6 click
    tt = np.arange(int(0.08 * SR)) / SR
    place(sfx, np.sin(2 * np.pi * f(m) * tt) * np.exp(-tt / 0.012) * g, at)
def bell(at, m, g=0.3, d=0.9):
    tt = np.arange(int(1.6 * SR)) / SR
    s = (np.sin(2 * np.pi * f(m) * tt) + 0.35 * np.sin(2 * np.pi * f(m) * 2.76 * tt) * np.exp(-tt / 0.15)) * np.exp(-tt / d)
    place(sfx, s * g, at)
def thump(at, g=0.5):
    tt = np.arange(int(0.5 * SR)) / SR
    place(sfx, np.sin(2 * np.pi * f(38) * tt) * np.exp(-tt / 0.2) * g, at)

for cut in (3.0, 7.0, 15.5, 19.0): whoosh(cut)
thump(0.0, 0.35)
for i, at in enumerate(np.arange(0.75, 1.75, 0.125)): tick(at, [81, 83, 85, 86, 88, 90, 92, 93][i], 0.10)  # dots turn red: D-major run
tick(4.5, 86, .16); tick(5.0, 90, .16); tick(5.5, 93, .16)                            # 01 · 02 · 03
tick(7.5, 93, .28)                                                                      # tap the mic
bell(9.75, 86, .14, .5); tick(12.5, 90, .14)                                            # card lands / action proposed
tick(13.5, 93, .24)                                                                     # சரி
for d, m in ((13.75, 86), (13.8125, 90), (13.875, 93), (13.9375, 98)): bell(d, m, .2, .8) # recorded
for i in range(5): tick(16.25 + i * .25, 81 - (i % 2) * 2, .12)                         # crossed out
thump(19.0, .45); bell(19.0, 74, .3, 1.6); bell(19.0, 81, .2, 1.6); bell(19.0, 86, .16, 1.6)

# one shared room
ir_t = np.arange(int(1.4 * SR)) / SR
ir = rng.standard_normal(len(ir_t)) * np.exp(-ir_t / 0.35); ir = lp(ir, 5000); ir /= np.sqrt(np.sum(ir ** 2))
dry = 0.30 * pad * duck + 0.30 * pluck + 0.42 * bass * duck + 0.62 * kick + 0.06 * hat + 0.11 * clap + 0.75 * sfx
send = 0.35 * pad + 0.45 * pluck + 0.12 * clap + 0.6 * sfx
wet = fftconvolve(send, ir)[:N] * 0.22

def stereo(x, w):
    d = int(0.011 * SR); r = np.concatenate([np.zeros(d), x[:-d]])
    return np.stack([x + w * 0.5 * r, x * 0 + r * (1 - w * 0.5) + x * w * 0.5 + 0 * r], 1)
mix = np.stack([dry, dry], 1) + stereo(wet, 1.0)
mix[:, 0] += 0.06 * pluck; mix[:, 1] -= 0.0 * pluck
fade = np.minimum(1, (DUR - t_all) / 1.2) ** 1.5; mix *= fade[:, None]
mix = np.tanh(mix / np.max(np.abs(mix)) * 1.3) ; mix /= np.max(np.abs(mix)) / 0.89
with wave.open('mix.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((mix * 32767).astype('<i2').tobytes())
print('ok', mix.shape)
