# GCC Publyq — Architecture

The plain-English overview is in [README.md](README.md). This document covers how the system is put
together, why, and what was deliberately left out.

## 1. Principles

1. **Every component must carry its weight.** No queue, cache, vector store, ORM or agent framework: Postgres
   and a few hundred lines of Python carry the whole load.
2. **One brain, many front doors.** Voice (Agora), the web chat, and MCP clients all call the same tool
   functions, with the same role scoping, guards and two-step writes.
3. **Facts come from records; writes come from people.** The LLM reads (and writes its own read-only SQL);
   only the user's own words confirm an action, and that check is plain code.
4. **Honest about limits.** GCC's portal has no write API, so actions are recorded here and shared on
   Telegram, and the UI says so.

## 2. Components

```
 Browser / phone ──RTC audio──► Agora ConvoAI ──────────────────────────────┐
   │                            ARES STT (en-IN + ta-IN + hi-IN)            │ POST /v1/chat/completions
   │                            Murf Falcon 2.1 TTS (Anisha, call's lang)   │ ?session&role&lang (bearer)
   │                                                                        ▼
   ├── text chat (SSE) ──► POST /api/agent/turn ──►┌──────────── FastAPI on Vercel (iad1) ────────────┐
   │                                               │ agent.run_turn                                   │
   │                                               │   1. control: role intro, confirm/cancel (code)  │
   │                                               │   2. Claude Opus 5.5 tool loop (streaming)       │
   │                                               │   3. deterministic router if Claude unavailable  │
   ├── GET /api/conversations/{id} (call view) ◄──│ tools.py · dbquery.py · publyq.py · roles.py     │
   │                                               └───────┬──────────────┬──────────────┬────────────┘
 MCP client ── POST /mcp (bearer) ────────────────────────►│              │              │
 Vercel Cron 07:00 IST ── /api/cron/morning-brief ────────►│              ▼              ▼
                                                     Neon Postgres   Telegram     app.publyq.in
                                                     (records +      (Ward 173    (deep links,
                                                      workspace)      group)       read-only)
```

| Module | Role |
|---|---|
| `agent.py` | One turn: control layer → Claude with 15 tools (manual streaming loop) → fallback. Saves every turn. |
| `router.py` | Deterministic intents: role intros, confirm/cancel (English, Tamil, Hindi), and the no-LLM fallback. |
| `lang.py` | The speaker's language from the script of their words (Tamil / Devanagari / Latin); short Latin replies keep the current one. |
| `tools.py` | Complaints, dossiers, SOP + safety-override priority, escalation chains, triage, actions, reports. |
| `dbquery.py` | The LLM's SQL: one SELECT, `gcc_reader` role, READ ONLY transaction, 3 s timeout, 50 rows. |
| `roles.py` | Nine GCC posts. Field posts see what they hold or own; leadership also sees posts that escalate to them. |
| `voice.py` | Agora session per call: ARES hears all three languages, Murf speaks the call's language, brain URL; adapted from Agora's Python quickstart. |
| `brief.py` | The 7 am brief: deterministic, Tamil by default, once per day, logged. |
| `telegram.py` | Sends and logs every message (sent / failed / not configured); follows supergroup migration. |
| `publyq.py` | Builds app.publyq.in URLs that mirror its own URL state (`state.js`). |
| `mcp.py` | MCP server (Streamable HTTP, stateless JSON) over the same tools. |

## 3. One spoken turn

1. Agora's VAD detects the end of speech; ARES transcribes it, listening for `en-IN,ta-IN,hi-IN` at once.
2. Agora calls `POST /v1/chat/completions?session=…&role=…&lang=…` with the shared bearer token.
3. `run_turn` loads the session context (role, focus complaint, last list) from the `turns` table: the server
   is stateless, so context lives in Postgres.
4. The script of the words sets the turn's language (`lang.py`); Claude replies in it. Control layer: "confirm" /
   "சரி" / "हाँ" → `confirm_action` in code; otherwise Claude streams. It opens with
   "Let me check…" before tool calls so the caller isn't left in silence.
5. Tool calls run in-process against Neon; results go back to Claude; text streams back to Agora as
   `chat.completion.chunk`s, and Murf speaks it.
6. The turn (text, reply, tools, cards, language, latency) is saved. The browser polls the conversation and shows
   the same cards a typed question would.
7. **Language switch.** A running agent's TTS can't be changed (Agora's update takes `llm` and `token` only). If the
   turn's language differs from the call's, the brain speaks a short holding line ("One moment.") instead of the
   answer; the browser sees the new language on the polled turn, stops the agent and starts one in the new language
   in the same channel, with that turn's saved answer as its greeting. Costs one agent start (~2 s), once per switch.

**Measured:** first spoken word ≈ 0.9–1.1 s on Claude turns with lookups; deterministic turns 1–20 ms server-side.

## 4. Decisions

| Area | Chosen | Rejected, and why |
|---|---|---|
| Speech-to-text | Agora **ARES** | Agora-managed Deepgram: timed out on this project (`agent-error asr 1000`); reproduced and fixed on Agora's own quickstart first |
| Text-to-speech | Murf **Falcon 2.1** inside ConvoAI (`vendor: murf`, model `falcon-2.1`) | A separate TTS proxy: one more hop and streaming integration for nothing |
| Brain | **Claude Opus 5.5**, official SDK, manual tool loop, server-side refusal fallback, cached system prompt | OpenAI-style shim (first version); agent frameworks (we need our own approval gate and per-token streaming) |
| Retrieval | **Tools + read-only SQL** over Postgres | Vector RAG: 455 tabular records answer exactly with SQL; embeddings would add cost and fuzziness |
| Voice fallback | **None.** Only Agora voices; text chat is always available | Browser speech (built first, removed at the user's request) |
| Writes | **Two-phase**, confirm in code, Telegram on confirm | Letting the LLM confirm, or email (GCC has no email endpoints for posts) |
| Hosting region | **iad1** next to Claude and Neon US-East | Mumbai: closer to users, but each turn makes several LLM + DB round trips |
| Analytics | **Deep links** into app.publyq.in | Rebuilding Publyq's lists and maps |
| MCP transport | **Stateless JSON** over Streamable HTTP | Session/SSE transport: needs a long-lived session manager that serverless won't keep |
| Morning brief | **Deterministic template** | LLM-written: varies daily, costs money, and can be wrong in front of the whole team |

## 5. Data model (Postgres)

**Source records** (from the Ward 173 CSVs; read-only to the app): `complaints` (455; typed columns +
`raw` jsonb; `holder_post_id` from GCC's viewer code, `owner_post_id` from the SOP owner role,
`issue_group_id` = Publyq category), `complaint_events` (512), `media` (1,326 links), `officials` (761 posts),
`reporting_lines` (892), `ward_map` (200), `sops` (56), `type_sop_map`, `role_ladder`, `data_dictionary`.

**Workspace** (what the app writes): `actions` (escalate / assign / note / follow_up / status_change /
close_request / help_request; proposed → confirmed → done or cancelled), `communications` (every Telegram
message, `kind` = action or morning_brief), `conversations` + `turns` (chat history per device, with cards and
the role and context used).

`gcc_reader` has SELECT on records, actions and communications only, never conversations or turns.

## 6. API

| Endpoint | Purpose |
|---|---|
| `POST /api/agent/turn` | Chat turn, server-sent events: `route`, `card`, `delta`, `done` |
| `POST /v1/chat/completions` | Agora ConvoAI's custom LLM (OpenAI chat format, bearer token) |
| `GET/POST /api/voice/status · config · start · stop` | Agora call lifecycle (token, start agent, stop) |
| `GET /api/conversations[/{id}]`, `DELETE …` | History per device |
| `GET /api/roles`, `/api/complaints[/{id}]`, `/api/ward/173/summary`, `/api/officials/{post}`, `/api/sops` | Read APIs |
| `POST /api/triage`, `GET/POST /api/actions`, `POST /api/actions/{id}/{confirm|cancel|done}` | Workflow |
| `GET /api/reports/complaint/{id}`, `/api/reports/ward`, `/api/communications` | Reports and Telegram log |
| `GET /api/cron/morning-brief` | Daily brief (Vercel Cron secret); `?dry_run=true` previews |
| `POST /mcp` | MCP server (bearer token) |

## 7. Safety model

| Risk | Guard |
|---|---|
| A mis-heard voice command makes a change | Two-phase writes; confirm/cancel parsed by code (English and Tamil whole-utterance lists) |
| The LLM writes to the database | `query_db` accepts one SELECT, runs as `gcc_reader` in a READ ONLY transaction, 3 s timeout |
| The LLM's SQL reads people's chats | `gcc_reader` has no grant on `conversations`/`turns`; functions that could undo `SET ROLE` (`set_config`) or run SQL from a string (`query_to_xml`, `dblink`, …) are refused, and results are discarded if the role changed |
| Prompt injection via complaint text | Record text is data in tool results; the LLM has no confirm tool; Telegram HTML is escaped |
| Someone else's conversation | A conversation belongs to the device that started it (random ID sent as `X-Device-Id`, never in URLs). Reading, continuing, calling into, proposing into, or confirming actions in it all check the owner; strangers get 404 |
| Public endpoints abused | Separate bearer secrets for the ConvoAI brain, the cron job and MCP, compared in constant time; an unset secret turns its endpoint off |
| Browser attacks | CORS only for `CORS_ORIGINS`; `X-Frame-Options: DENY`, `nosniff`, HSTS; API responses `no-store`; no OpenAPI schema served |
| Silent delivery failures | Every Telegram attempt is logged with its status and error |

## 8. Deployment

Vercel project `gcc-publyq`: static `web/` + one Python function (`api/index.py`), region `iad1`, cron
`30 1 * * *`; `.vercelignore` ships about 0.2 MB. Neon is attached through the Vercel integration
(`NEON_DATABASE_URL`). `backend/scripts/migrate_db.py` copies local → Neon over the direct endpoint, refuses a
non-empty target, re-applies the read-only role and checks row counts per table. Domain: `gcc.publyq.in`
(CNAME at the DNS provider).

## 9. Known limits

- **Snapshot data:** the records are from 15–20 Nov 2026; Publyq's views are live, so filtered counts can drift.
- **No GCC write-back:** GCC's portal has no API for outside apps.
- **Identity is per device:** there's no sign-in yet; the role is chosen, not verified. Anyone can open the app and start
  calls and chats, so Claude / Agora / Murf spend has no per-user cap; a sign-in is the real fix.
- **The action ledger is shared ward-wide** (actions and Telegram messages are team work, visible to every role);
  chat history is not.
- **Three-language calls** (ARES `en-IN,ta-IN,hi-IN`, auto switching) haven't been measured on many real calls yet;
  Murf `hi-IN` for the Anisha voice is not yet verified on this account. Hinglish in Latin script is treated as English.
- **Cards and the morning brief** stay English / Tamil; the screen's own words follow the conversation language.
