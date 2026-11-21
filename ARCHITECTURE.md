# GCC Publyq — Architecture

What the system is made of, why each piece is there, and what we chose not to build. The plain-English overview
is in [README.md](README.md).

## 1. First principles

Start from the user and work back to the system.

**The user** is a junior Assistant Engineer in Ward 173, on a street, phone in hand. They switch between Tamil,
English and Hindi, often mid-sentence. They don't have time to search a portal. They need to know what matters
today, act on it, and be sure nothing happens by mistake.

**What that demands of the system:**

| The user needs… | So the system must… | How |
|---|---|---|
| To talk, not type | Make voice the main path | One big mic button; Agora carries the call, Murf speaks |
| To speak their own language | Hear all three at once and reply in kind | ARES listens for `en-IN,ta-IN,hi-IN`; the script of the words picks the reply language |
| To be heard only when they mean it | Ignore street and office noise | Push to talk: only speech between two taps is a turn |
| Answers about *their* work | Scope everything to their post | Every tool runs inside the post's scope |
| Answers they can trust | Ground every fact in records | Tools and read-only SQL over GCC's data; no free-text guessing |
| Nothing to happen by accident | Keep people in charge of changes | The AI only proposes; plain code checks "confirm" |
| Their conversations to stay theirs | Tie data to a person | Telegram sign-in; every conversation and action has one owner |

**Rules for building it:**

1. **Every component must carry its weight.** No queue, cache, vector store, ORM or agent framework. Postgres and a
   few hundred lines of Python carry the load.
2. **One brain, many front doors.** Voice (Agora), the web chat and MCP clients all call the same tool functions,
   with the same scoping, guards and two-step writes.
3. **Facts come from records; changes come from people.** The AI reads (and writes its own read-only SQL); only the
   person's own words confirm an action, and that check is code.
4. **Honest about limits.** GCC's portal has no write API, so actions are recorded here and shared on Telegram, and
   the app says so.

## 2. Components

```
 Browser / phone ──RTC audio + RTM tap signals──► Agora ConvoAI ───────────────────┐
   │                               ARES STT (en-IN + ta-IN + hi-IN), manual turns │ POST /v1/chat/completions
   │                               Murf Falcon 2.1 TTS (Anisha, call's language)  │ ?session&role&lang (bearer)
   │                                                                              ▼
   ├── sign-in (Telegram widget) ─► /api/auth/* ─►┌────────── FastAPI on Vercel (iad1) ──────────────┐
   ├── text chat (SSE) ──────────► /api/agent/turn │ agent.run_turn                                   │
   │                                               │   1. control: role intro, confirm/cancel (code)  │
   │                                               │   2. Claude Opus 5.5 tool loop (streaming)       │
   │                                               │   3. deterministic router if Claude unavailable  │
   ├── GET /api/conversations/{id} (call view) ◄──│ tools · dbquery · roles · lang · auth · publyq   │
   │                                               └───────┬──────────────┬──────────────┬────────────┘
 MCP client ── POST /mcp (bearer) ────────────────────────►│              │              │
 Vercel Cron 07:00 IST ── /api/cron/morning-brief ────────►│              ▼              ▼
                                                     Neon Postgres   Telegram     app.publyq.in
                                                     (records,       (sign-in,    (deep links,
                                                      workspace,      ward group)  read-only)
                                                      accounts)
```

| Module | Role |
|---|---|
| `agent.py` | One turn: control layer → Claude with 15 tools (manual streaming loop) → fallback. Saves every turn. |
| `router.py` | Fixed rules: role intros, confirm/cancel in English, Tamil and Hindi (whole utterance only), and the no-LLM fallback. |
| `lang.py` | The speaker's language from the script of their words (Tamil / Devanagari / Latin). Short Latin replies ("ok", an ID) keep the current language. |
| `tools.py` | Complaints, dossiers, SOP + safety-override priority, escalation chains, triage, actions, reports. |
| `dbquery.py` | The AI's SQL: one SELECT, `gcc_reader` role, READ ONLY transaction, 3 s timeout, 50 rows, escape functions blocked. |
| `roles.py` | Nine GCC posts. Field posts see what they hold or own; leadership also sees posts that escalate to them. |
| `auth.py` | Telegram sign-in: Login Widget signature, ward-group membership (re-checked daily), hashed session tokens; an admin flag for listed ids / the group owner. |
| `voice.py` | One Agora agent per call: ARES hears three languages, manual turns, Murf in the call's language, our brain as the LLM. Adapted from Agora's Python quickstart. |
| `brief.py` | The 7 am brief: fixed template, Tamil by default, once per day, logged. |
| `telegram.py` | Sends and logs every message (sent / failed / not configured); follows supergroup migration. |
| `publyq.py` | Builds app.publyq.in URLs that mirror its own URL state. |
| `mcp.py` | MCP server (Streamable HTTP, stateless JSON) over the same tools. |
| `main.py` | The web API and who may touch what (sign-in, ownership, bearer secrets, headers). |

## 3. One spoken turn

1. **Push to talk.** The mic is muted until the person taps it. The tap unmutes it and sends the agent a start of
   speech signal; the next tap sends end of speech and mutes again. The signals go over RTM straight to the agent
   (customType `user.manual_sos` / `user.manual_eos`, body `{"request_id": …}`), as Agora's client toolkit sends
   them, using the call's combined RTC + RTM token. The agent runs with manual start and end of speech.
2. ARES transcribes the turn, listening for `en-IN,ta-IN,hi-IN` at once.
3. Agora calls `POST /v1/chat/completions?session=…&role=…&lang=…` with the shared bearer token.
4. `run_turn` loads the conversation's context (post, complaint in focus, last list) from the `turns` table. The
   server keeps no state between requests; context lives in Postgres.
5. The script of the words sets the turn's language, and Claude replies in it. Control layer first: "confirm",
   "சரி" or "हाँ" → `confirm_action` in code. Otherwise Claude streams, opening with "Let me check…" before tool calls so
   the caller isn't left in silence.
6. Tools run in-process against Neon; results go back to Claude; text streams to Agora as
   `chat.completion.chunk`s, and Murf speaks it.
7. The turn (text, reply, tools, cards, language, latency) is saved. The browser polls the conversation and shows
   the same cards a typed question would.
8. **Language switch.** A running agent's voice can't change language (Agora's update takes `llm` and `token`
   only). If the turn's language differs from the call's, the brain says a short holding line ("One moment.")
   instead of the answer. The browser sees the new language on the polled turn, stops the agent, and starts one in
   the new language in the same channel, with that turn's saved answer as its greeting. About 2 s, once per switch.

**Measured:** first spoken word ≈ 0.9–1.1 s on Claude turns with lookups; fixed-rule turns take 1–20 ms on the server.

## 4. Decisions

| Area | Chosen | Rejected, and why |
|---|---|---|
| Turn-taking | **Push to talk** (manual start and end of speech) | Automatic voice detection: in a street or office, any noise can start a turn |
| Speech-to-text | Agora **ARES**, three languages in one call | A language picker: the user switches mid-conversation. Agora-managed Deepgram: timed out on this project (`agent-error asr 1000`) |
| Language switching | **Restart the agent** in the new language, opening with the answer | Fixed language per call: breaks the user who switches. Updating the running agent: Agora doesn't allow changing TTS |
| Text-to-speech | Murf **Falcon 2.1** inside ConvoAI (`vendor: murf`) | A separate TTS proxy: one more hop for nothing |
| Brain | **Claude Opus 5.5**, official SDK, manual tool loop, cached system prompt | Agent frameworks: we need our own approval gate and per-token streaming |
| Retrieval | **Tools + read-only SQL** over Postgres | Vector RAG: 455 tabular records answer exactly with SQL; embeddings add cost and fuzziness |
| Writes | **Two-phase**, confirm in code, Telegram on confirm | Letting the AI confirm; email (GCC has no email endpoints for posts) |
| Sign-in | **Telegram**, ward-group members only | Email/Google (anyone can sign up); staff PINs (get shared, need resets). Staff already live in the Telegram group |
| Admin | **An account flag**, not a post | An "Admin" post: it isn't a GCC post, so it doesn't belong in the post list |
| Voice fallback | **None.** Only Agora voices; text chat always works | Browser speech: built first, removed |
| Hosting region | **iad1**, next to Claude and Neon US-East | Mumbai: closer to users, but each turn makes several LLM and database round trips |
| Analytics | **Deep links** into app.publyq.in | Rebuilding Publyq's lists and maps |
| MCP transport | **Stateless JSON** over Streamable HTTP | Session/SSE transport: needs a long-lived session manager that serverless won't keep |
| Morning brief | **Fixed template** | AI-written: varies daily, costs money, and can be wrong in front of the whole team |

## 5. Data model (Postgres)

**Source records** (from the Ward 173 CSVs; read-only to the app): `complaints` (455; typed columns + `raw` jsonb;
`holder_post_id` from GCC's viewer code, `owner_post_id` from the SOP owner role, `issue_group_id` = Publyq
category), `complaint_events` (512), `media` (1,326 links), `officials` (761 posts), `reporting_lines` (892),
`ward_map` (200), `sops` (56), `type_sop_map`, `role_ladder`, `data_dictionary`.

**Workspace** (what the app writes): `actions` (escalate / assign / note / follow_up / status_change /
close_request / help_request; proposed → confirmed → done or cancelled), `communications` (every Telegram message,
`kind` = action or morning_brief), `conversations` + `turns` (chat history, owned by one account, with cards, the
post and the language used).

**Accounts:** `users` (Telegram id, name, chosen post, admin flag) and `user_sessions` (SHA-256 of the session token,
expiry, last membership check).

`gcc_reader`, the AI's database role, can read records, actions and communications only. It has no access to
`conversations`, `turns`, `users` or `user_sessions`.

## 6. API

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /api/auth/config`, `POST /api/auth/telegram`, `GET /api/auth/me`, `POST /api/auth/role`, `POST /api/auth/logout` | Sign-in, the person, their post, sign out | Public / signed in |
| `POST /api/agent/turn` | Chat turn, server-sent events: `route`, `card`, `delta`, `done` | Owner of the conversation |
| `GET /api/voice/config`, `POST /api/voice/start` | Call token; start the agent | Owner of the conversation |
| `POST /api/voice/stop` | Stop an agent | Signed in |
| `GET /api/voice/status` | Is voice configured, which languages | Public |
| `GET /api/conversations[/{id}]`, `DELETE …` | History | Owner |
| `GET/POST /api/actions`, `POST /api/actions/{id}/{confirm\|cancel\|done}` | Action ledger; decisions | Signed in; owner decides |
| `GET /api/reports/complaint/{id}`, `/api/reports/ward`, `/api/communications` | Reports and Telegram log | Signed in |
| `GET /api/health`, `/api/roles`, `/api/complaints[/{id}]`, `/api/ward/{n}/summary`, `/api/ward/{n}/officials`, `/api/officials/{post}`, `/api/sops`, `POST /api/triage` | Health and GCC's own records | Public |
| `POST /v1/chat/completions` | Agora ConvoAI's custom LLM (OpenAI chat format) | `CUSTOM_LLM_TOKEN` |
| `GET /api/cron/morning-brief` | Daily brief; `?dry_run=true` previews | `CRON_SECRET` |
| `POST /mcp` | MCP server | `MCP_TOKEN` |

## 7. Safety model

| Risk | Guard |
|---|---|
| Noise or a mis-heard word makes a change | Push to talk; two-phase writes; confirm/cancel parsed by code from whole-utterance lists in English, Tamil and Hindi (ambiguous words left out) |
| The AI writes to the database | `query_db` accepts one SELECT, runs as `gcc_reader` in a READ ONLY transaction, 3 s timeout |
| The AI's SQL reads people's chats | `gcc_reader` has no access to chats or accounts; functions that could undo `SET ROLE` (`set_config`) or run SQL from a string (`query_to_xml`, `dblink`, …) are refused; results are discarded if the role changed |
| Prompt injection via complaint text | Record text is data in tool results; the AI has no confirm tool; Telegram HTML is escaped |
| Strangers using the app | Telegram sign-in, Ward 173 group members only, re-checked daily. Session = random token in an HttpOnly, Secure, SameSite=Lax cookie; the database stores only its SHA-256 |
| Someone else's conversation | Each conversation belongs to one account. Reading, continuing, calling into, proposing into or confirming actions in it all check the owner; strangers get 404 |
| Endpoints abused | Separate secrets for the voice brain, the cron job and MCP, compared in constant time; an unset secret turns its endpoint off |
| Browser attacks | CORS only for `CORS_ORIGINS`; `X-Frame-Options: DENY`, `nosniff`, HSTS; API responses `no-store`; no OpenAPI schema served |
| Silent delivery failures | Every Telegram attempt is logged with its status and error |

Each security fix has a test that replays the original attack (`backend/tests/test_security.py`, `test_auth.py`).

## 8. Deployment

Vercel project `gcc-publyq`, Git-connected: a push to `main` deploys to production. Static `web/` plus one Python
function (`api/index.py`) in `iad1`, under Vercel's 225 MB function limit; `.vercelignore` keeps data, tests, scripts
and local media out. Cron `30 1 * * *` (07:00 IST). Neon is attached through the Vercel integration
(`NEON_DATABASE_URL`). Schema changes: `backend/scripts/apply_schema.py` (adds what's missing, re-applies the
read-only role, never drops). First full copy: `backend/scripts/migrate_db.py` (refuses a non-empty target, checks
row counts per table). Domain `gcc.publyq.in` (CNAME), also registered with BotFather for Telegram sign-in.

## 9. Known limits

- **Snapshot data:** records are from 15–20 Nov 2026; Publyq's views are live, so filtered counts can drift.
- **No GCC write-back:** GCC's portal has no API for outside apps.
- **Posts are self-chosen:** sign-in proves a person is in the ward group, not which GCC post they hold.
- **No per-person usage cap:** a signed-in member can run as many chats and calls as they like.
- **The action ledger is shared ward-wide:** actions and Telegram messages are team work, visible to every signed-in
  member. Chat history is not.
- **Three-language calls are new:** automatic switching, push to talk and the Murf `hi-IN` voice need more real calls
  to be proven. Agora doesn't document whether starting a turn interrupts the agent mid-answer. Hinglish in Latin
  script is treated as English.
- **Cards and the morning brief** stay in English / Tamil; the screen's own words follow the conversation language.
