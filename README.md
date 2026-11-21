# GCC Publyq

Voice-first workplace assistant for Greater Chennai Corporation staff in **Ward 173 (Adyar)**.
Ask what's urgent, open a complaint, find who holds it, read the SOP, escalate, set a
follow-up, and get a completion report. Answers come from real GCC records, and every
write waits for your "confirm".

Design and decisions: [ARCHITECTURE.md](ARCHITECTURE.md).

## Run locally

```bash
./scripts/dev-db.sh start                         # isolated Postgres on :5444 (own data dir)
cp .env.example .env                              # defaults work locally
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m backend.scripts.seed          # load the Ward 173 CSVs
.venv/bin/uvicorn backend.app.main:app --port 8173 --reload
```

Open http://localhost:8173. Use Chrome or Edge for browser voice input; any browser can type.

## Who it's for

Users pick their GCC **post** on first open, or say "As the Assistant Engineer (Electrical)…".
- **Field roles** (AE, AE-Electrical, Sanitary Inspector, Park Overseer) see the complaints they hold or own.
- **Leadership roles** (AEE, ADE-Electrical, Sanitary Officer, EE) also see every post whose escalation
  chain, taken from the real reporting lines, reaches them.
- **Ward leadership** sees the whole ward, including complaints whose SOP owner has no post in the records.

Conversations are saved server-side per device. History reopens a conversation and continues it,
including pending confirmations. Hands-free mode reopens the mic after each spoken reply.

## Try the end-to-end workflow

Role example: "As an Assistant Engineer - Electrical, what are the high priority complaints and what
recurring patterns do we hear?"

| Step | Say |
|---|---|
| Initiate | "What's urgent in my ward?" |
| Understand | "Open the first one" · "Who is handling it?" · "What does the SOP say?" |
| Execute | "Escalate it" (it's proposed, not recorded yet) |
| Confirm | "Confirm" (recorded, and a 2-day follow-up is auto-scheduled) |
| Follow up | "What follow-ups do I have?" · "Write the report for it" |

Also try: "A resident called about an open manhole near the school in Gandhi Nagar" (triage),
"Ward brief", "Open complaints in Indira Nagar", "Who is the sanitary inspector?".

## Tests

```bash
.venv/bin/pytest
```

These run against the local seeded DB and refuse to run if `DATABASE_URL` is remote.
They cover CSV fidelity, triage accuracy, the escalation chain, the two-phase action
ledger, the full spoken workflow, the LLM tool loop (stub model), LLM outage fallback,
and the OpenAI-compatible endpoint.

## LLM

Optional. Without it, the deterministic fast path handles the common intents above in
a few ms. To enable it, set `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` for any
OpenAI-compatible chat-completions API, then compare candidates:

```bash
.venv/bin/python -m backend.scripts.eval_llm
```

## Live

**https://gcc.publyq.in** — Vercel project `gcc-publyq` (functions in `iad1`, next to Claude and Neon US-East),
Neon Postgres via the Vercel integration (`NEON_DATABASE_URL`), DNS CNAME at Spaceship.

| Piece | What runs |
|---|---|
| Voice | Agora ConvoAI: ARES speech-to-text (`en-IN`) → GCC Publyq brain → Murf Falcon 2.1 (Anisha default, Abhinav selectable) |
| Brain | Claude Opus 5.5 (official SDK) with role-scoped tools + read-only SQL; deterministic confirm/cancel |
| Outbound | Telegram group "Publyq Ward 173" via @Publyqbot; every message logged in `communications` |
| Data | All CSVs in Postgres (local and Neon); media bytes stay in object storage, served from source URLs |

### Publyq integration (app.publyq.in)

GCC Publyq doesn't rebuild Publyq's analytics. It deep-links into them, because Publyq keeps its whole
screen in the URL hash (`backend/app/publyq.py` mirrors its `state.js`). Guests can open every link.

| In GCC Publyq | Opens in Publyq |
|---|---|
| Any list: queue, search, repeat spot, SQL result, duplicates | `#/complaints?range=all&ids=A,B,C` (exact set, max 100) |
| One complaint | `…&ids=X&id=X` (detail panel open) |
| Top issue / issue group | `…&ward=173&type=<issue>` / `…&category=<key>` |
| Safety risks / overdue | `…&ward=173&status=open&safety=1` / `overdue=1` |
| Ward map / accountability | `#/pulse?ward=173` / `#/acc?ward=173` |

Claude can also call `open_in_publyq` for anything it finds, including sets from its own SQL.

Re-deploy: `vercel deploy --prod`. Move data again: `python -m backend.scripts.migrate_db` (refuses a
non-empty target unless `--replace`; verifies every table). Link a new Telegram group:
`python -m backend.scripts.telegram_link --write`.

## Layout

```
backend/app/     config, db, tools (facts + writes), router (fast path), agent (turn loop), llm, main (API)
backend/db/      schema.sql
backend/scripts/ seed.py, eval_llm.py
backend/tests/   pytest suite
web/             static client (index.html, app.js, styles/)
api/index.py     Vercel entry
Complaints/, Officials/   source data (media gitignored)
```
