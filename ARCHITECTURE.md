# GCC Publyq — Architecture

A voice-first workplace assistant for Greater Chennai Corporation ward staff.
It answers questions about Ward 173 from real records, and it **does work**:
it triages new complaints, finds the responsible post, recommends SOP steps,
escalates, schedules follow-ups and writes completion reports — every write
gated behind an explicit spoken or tapped confirmation.

Separate from `app.publyq.in`: independent repo, independent database, no
shared credentials. It borrows only the visual design system (copied tokens).

---

## 1. First principles

What is the job? A ward engineer or sanitary inspector needs to go from
*"what's burning in my ward?"* to *"it's escalated and I'll be reminded"* in
under a minute, hands busy, on a phone.

So the system needs exactly four things:

1. **Ears and a mouth** — speech in, speech out, fast enough to feel like a call.
2. **Facts** — the ward's complaints, officer actions, posts, reporting lines, SOPs, photos.
3. **Judgement** — turning a sentence into the right lookup or action.
4. **Hands** — durable, auditable writes (escalate, assign, follow up, report).

Everything else is optional. Each component below exists because it carries
one of those four.

## 2. Final architecture

```
                 ┌──────────────────────── Agora ConvoAI Engine ────────────────────────┐
 Phone/Browser ──┤  RTC audio ─► ASR (ares / sarvam) ─► LLM = custom ─► TTS = murf ─► RTC │──► speaker
  (gcc.publyq.in)└───────────────────────────────┬──────────────────────────────────────┘
        │                                         │ POST /v1/chat/completions (OpenAI-style, SSE)
        │ text / fallback voice                   ▼
        │ POST /api/agent/turn (SSE)   ┌──────────────────────────────┐      ┌──────────────┐
        └─────────────────────────────►│  FastAPI "brain" (Vercel fn)  │─SQL─►│ Neon Postgres │
                                       │  1. fast-path intent router   │      └──────────────┘
                                       │  2. LLM tool loop (fallback)  │──► upstream LLM
                                       │  3. tools = domain functions  │    (OpenAI-compatible)
                                       └──────────────────────────────┘
```

**One brain, two front doors.** The same agent code answers:

- **Primary voice path:** Agora ConvoAI owns the audio loop (VAD, barge-in,
  ASR, Murf TTS) and calls our backend as its `custom` LLM. Audio never
  touches our server — one text hop per turn.
- **Text / fallback path:** the web client posts to `/api/agent/turn`. When
  Agora is unavailable (no minutes left, blocked network), the browser's own
  `SpeechRecognition` + `speechSynthesis` give a zero-cost voice loop over
  the same endpoint.

### Stack verdicts

| Layer | Asked for | Verdict | Why |
|---|---|---|---|
| Frontend | Reuse app.publyq.in stack | **Keep** — vanilla JS, no build, copied `tokens.css` | No bundler = no build step, instant deploy, same look |
| Hosting | Vercel | **Keep** — static `web/` + one Python function | Same platform as Publyq; streaming responses supported |
| Backend | FastAPI | **Keep** | Native SSE, Pydantic validation, Agora custom-LLM samples use it |
| DB | Neon Postgres | **Keep**, `psycopg3` raw SQL, no ORM | 11 tables, parameterized SQL; ORM adds weight not value |
| STT | Agora | **Keep, via ConvoAI** (not standalone STT) | ConvoAI bundles ASR+VAD+turn-taking; standalone STT would force us to build barge-in ourselves |
| TTS | Murf Falcon | **Keep, configured inside ConvoAI** (`vendor: murf`, Beta per Agora API spec) | Removes a separate TTS hop and a second streaming integration from our code |
| LLM | "Falcon 2.1" | **Not identified as an LLM API — needs your confirmation.** Murf's *Falcon* is a TTS model. Backend speaks the OpenAI-compatible chat format, so any provider (incl. a Falcon LLM endpoint if it's OpenAI-compatible) is an env-var swap. `scripts/eval_llm.py` scores candidates on tool-choice accuracy and time-to-first-token against real Ward 173 questions. | Don't lock in before measuring |
| Media | — | Dev: served from disk. Prod: needs object storage (1.4 GB won't ship in a Vercel bundle); officer photos fall back to the GCC ERP `source_url` | Don't push 1.4 GB through git |

**Removed on purpose:** Redis, queues, vector DB, LangChain, ORM, a separate
TTS proxy, a separate STT service. 455 complaints fit in Postgres full-text
search; a vector DB would be architecture theatre at this size.

## 3. Latency strategy

Target: speech-end → first audio < 1.5 s on the fast path.

| Stage | Budget | How |
|---|---|---|
| End-of-speech detect + ASR | ~400–700 ms | Agora ConvoAI VAD/ASR (outside our control; tune silence threshold) |
| Brain, **fast path** | < 60 ms | Deterministic router: complaint IDs, localities, "open/overdue/safety", "who handles", "SOP", "follow-ups", "confirm". No LLM call at all. |
| Brain, LLM path | TTFT of model + tool SQL (< 30 ms each) | Tools run in-process against Postgres; at most 4 tool rounds; stream the final answer token by token |
| TTS first audio | Murf streaming | Short, speech-shaped answers (≤ 2 sentences spoken, details shown on screen) |

Reliability rules:

- **No key, no outage:** with no LLM configured the fast path still answers
  most ward questions; unmatched questions get an honest "I can't do that
  without the language model" plus examples.
- **LLM failure → fast-path retry → graceful message**, never a stack trace read aloud.
- **Neon cold start:** pooled connection string + small pool; first query
  timeout 5 s.
- **Writes are two-phase:** `propose` returns an action in `proposed`
  state; only `confirm` executes. Prevents ASR mishearings from escalating
  the wrong complaint.

## 4. Data model (Postgres)

Source CSVs are loaded as-is into typed tables; nothing is invented.

| Table | Rows | Source |
|---|---|---|
| `complaints` | 455 | `ward173_complaints.csv` (key fields typed, full row in `raw jsonb`) |
| `complaint_events` | 512 | `ward173_events.csv` — officer/QA actions per complaint |
| `media` | ~1,300 | `images_manifest.csv` + `x_media_manifest.csv` |
| `officials` | 761 | `gcc_officials.csv` — **posts, not people** |
| `reporting_lines` | 892 | `gcc_reporting_lines.csv` |
| `ward_map` | 200 | `gcc_ward_map.csv` |
| `sops` | 56 | `gcc_complaint_sop.csv` |
| `type_sop_map` | per GCC type | derived at seed: GCC complaint type → SOP row, within the same `issue_group`, with match score |
| `actions` | workspace | **our** writes: escalate / assign / note / follow_up / report, two-phase |
| `turns` | workspace | audit log: utterance, route (fast/llm), tools called, latency |

`actions` and `turns` are the only tables the app writes. GCC source tables
are read-only from the app's point of view.

**Honesty boundary:** GCC's ERP has no write API available to us. "Escalate"
records the escalation in our ledger, names the target post from the real
reporting lines, and drafts the message. It does not claim to have changed
the GCC portal.

## 5. API contracts

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | DB reachable, LLM configured, counts |
| POST | `/api/agent/turn` | `{session_id, text}` → SSE: `route`, `tool`, `card`, `delta`, `done` events |
| POST | `/v1/chat/completions` | OpenAI-compatible (Agora ConvoAI custom LLM). Streams `chat.completion.chunk` |
| GET | `/api/complaints` | filters: `status_group, locality, issue_group, safety, overdue, q, limit` |
| GET | `/api/complaints/{id}` | full dossier: complaint, events, media, SOP, responsible posts, repeats, actions |
| GET | `/api/ward/{ward}/summary` | counts by status, type, locality; officials |
| GET | `/api/officials/{post_id}` | post + reporting chain |
| POST | `/api/triage` | `{text}` → SOP type, priority, owner post, likely duplicates |
| GET/POST | `/api/actions`, `/api/actions/{id}/confirm`, `/cancel` | workflow ledger |
| GET | `/api/reports/complaint/{id}` | completion report (markdown) |
| GET | `/media/{file}` | dev-only media |

## 6. The end-to-end workflow (demo script)

1. **Initiate** — "What's urgent in my ward?" → open safety-risk complaints, overdue first.
2. **Understand** — "Open the manhole one." → dossier: what citizen said, what officers did, photos, SOP diagnose steps, who holds it now.
3. **Execute** — "Escalate it." → proposes escalation to the post's real reporting line (e.g. `AE-D173 → AEE3-Z13`), drafts the note. "Confirm." → written.
4. **Confirm** — reads back action ID, target post, follow-up date.
5. **Follow up** — "What follow-ups are due?" → due list; "Report on 2026-…" → completion report with the audit trail.

## 7. Roadmap

| # | Milestone | Verifiable by |
|---|---|---|
| M0 | Repo, architecture, env, isolated dev DB | this doc; `scripts/dev-db.sh` |
| M1 | Schema + seed from CSVs | row counts match CSVs (tests) |
| M2 | Domain tools + REST API | pytest against seeded DB |
| M3 | Fast-path router + two-phase actions + reports | pytest: full workflow without an LLM |
| M4 | LLM tool loop + OpenAI-compatible endpoint | pytest with a stub LLM; live once a key is set |
| M5 | Voice-first web client (browser voice fallback) | run locally, drive the workflow by voice/text |
| M6 | Vercel + Neon deploy config | `vercel` preview deploy (needs your Neon URL) |
| M7 | Agora ConvoAI baseline via official quickstart, then point its LLM at `/v1/chat/completions` and TTS at Murf | agent joins channel; one spoken turn end to end (needs Agora App Certificate, Murf key) |
| M8 | Media to object storage | photos load on gcc.publyq.in |

## 8. Publyq integration (deep links, not duplication)

app.publyq.in already renders complaint lists, details, maps and accountability for the same GCC records,
and stores its full view state in the URL hash. So the integration is a pure function from "what the user
is looking at here" to "the exact Publyq URL" (`backend/app/publyq.py`): no shared database, no new API,
no second copy of Publyq's UI. Repeat spots and role-scoped issues link their exact complaint IDs (Publyq has
no notion of our role scopes); sets over Publyq's 100-ID cap fall back to the ward-wide filter and say so.

## 9. MCP: one toolset, every client

The voice agent, the chat, and any MCP client (Claude Desktop/Code, IDEs, other agents) call the same Python
functions with the same role scoping, SQL guards and two-phase writes. `/mcp` speaks Streamable HTTP in stateless
JSON mode, so each request is self-contained, which suits serverless functions. Confirmations through MCP rely on the
host's approval prompt (`destructiveHint`), and only actions proposed through MCP can be confirmed there.
