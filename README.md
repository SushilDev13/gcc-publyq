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

## Try the end-to-end workflow

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

## Deploy (Vercel + Neon) — not yet done

1. Create a Neon database and copy its **pooled** connection string.
2. Seed it: `DATABASE_URL=<neon-url> .venv/bin/python -m backend.scripts.seed`
3. Create a **new** Vercel project from this repo (not the Publyq one). Set the env vars
   from `.env.example`, then add the domain `gcc.publyq.in`.
4. Media (1.4 GB) isn't deployed. Photos fall back to their public source URLs until
   `MEDIA_BASE_URL` points at object storage.

## Agora ConvoAI voice (M7) — not yet done

The brain already speaks the OpenAI chat-completions format at `POST /v1/chat/completions?session=<id>`,
with optional `Authorization: Bearer $CUSTOM_LLM_TOKEN`. Next steps:
1. Run Agora's official ConvoAI quickstart once to prove the project works end to end.
2. Point its LLM at this endpoint (`vendor: custom`, `style: openai`) and set its TTS to `murf`.

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
