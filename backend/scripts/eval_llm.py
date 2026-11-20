"""Score an LLM candidate on real Ward 173 questions the fast path does NOT handle.

Measures, per question: time to first token, total time, which tools were
called, and whether the expected tool was among them. Run once per
candidate by changing LLM_BASE_URL / LLM_MODEL / LLM_API_KEY:

    .venv/bin/python -m backend.scripts.eval_llm
"""
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.app import db  # noqa: E402
from backend.app.agent import run_turn  # noqa: E402
from backend.app.config import settings  # noqa: E402

# (question, a tool a correct answer must use)
CASES = [
    ('Which locality has the most debris complaints still pending?', 'search_complaints'),
    ('Is the Assistant Engineer for our ward slower than the sanitary inspector to act?', 'find_officials'),
    ('Give me the details of complaint 2026 one eight five O E T', 'get_complaint'),
    ('A shopkeeper says a big tree branch is hanging over the bus stop on LB Road', 'triage'),
    ('What should my team carry for a dead animal pickup?', 'get_sop'),
    ('Draft an escalation for 2026-735GQS to the zonal engineer', 'propose_action'),
    ('How many open drain complaints are flagged as dangerous?', 'search_complaints'),
    ('Who does the AEE for zone 13 report to?', 'get_official'),
]


async def one(q: str, sid: str) -> dict:
    t0, ttft, tools, text = time.perf_counter(), None, [], ''
    async for ev, d in run_turn(q, sid, 'eval'):
        if ev == 'delta':
            ttft = ttft or time.perf_counter() - t0
            text += d['text']
        elif ev == 'done':
            tools, route = d.get('tools', []), d['route']
    return {'q': q, 'ttft': ttft or 0, 'total': time.perf_counter() - t0, 'tools': tools, 'route': route,
            'text': text}


async def main() -> int:
    if not settings.llm_enabled:
        print('LLM not configured: set LLM_BASE_URL, LLM_MODEL, LLM_API_KEY in .env')
        return 2
    print(f'model={settings.llm_model} base={settings.llm_base_url}')
    hits, ttfts, totals = 0, [], []
    for i, (q, want) in enumerate(CASES):
        r = await one(q, f'eval-{i}')
        ok = want in r['tools'] or r['route'] == 'fast'
        hits += ok
        ttfts.append(r['ttft']); totals.append(r['total'])
        print(f"{'PASS' if ok else 'FAIL'} ttft={r['ttft']*1000:5.0f}ms total={r['total']*1000:5.0f}ms "
              f"route={r['route']:8} tools={r['tools']}\n     Q: {q}\n     A: {r['text'][:160]}")
    db.execute("DELETE FROM actions WHERE session_id LIKE 'eval-%'")
    print(f'\ntool accuracy {hits}/{len(CASES)} · median ttft {statistics.median(ttfts)*1000:.0f} ms · '
          f'median total {statistics.median(totals)*1000:.0f} ms')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
