# /brag plan: GCC Publyq, from first principles

**What it is:** a voice assistant that helps Greater Chennai Corporation ward staff act on public complaints, hands-free, in English, Tamil or Hindi.
**Who it's for:** ward officers (Assistant Engineers, Sanitary Inspectors…) in Ward 173, Adyar.
**Angle:** first principles. Start from the real failure in the records (90 of 96 open complaints never touched), ask what an officer actually needs (know what matters, act, close the loop), build only that, and carry the same rule into the stack ("every component must carry its weight", from ARCHITECTURE.md).
**Hook:** 96 dots, one per open complaint. 90 of them go red: never touched.
**Highlights:** 1) ask in Tamil, get the urgent list in Tamil; 2) "Escalate it" → nothing happens until you say சரி, and code (not the AI) confirms; 3) the stack: no queue, no cache, no vector store, no agent framework.
**Punchline:** "Talk to your ward work in English, Tamil or Hindi." · gcc.publyq.in
**Tone:** `polished` with a `default` pulse: calm civic instrument, clean cuts on the beat.
**Visual identity:** the app's own `tokens.css` + `app.css` (dark theme): canvas #070d10, marina teal #19cfc6, crit #ff5d5d, warn #e8a13c; Bricolage Grotesque / Inter / IBM Plex Mono / Anek Tamil. App UI is mirrored with the real class lists (`.top`, `.talk`, `.callbar`, `.turn .you/.bot`, `.card`, `.rows`, `.tag`, `.action.proposed/confirmed`, `.kv`, `.act .primary`).
**Music:** 120 BPM (beat 0.5 s), D major, I–vi–IV–V, one chord per bar. Pad + pluck intro, drums in at 3.0 s, chime on "Recorded", ring-out on the outro. Every cut on a beat.

## Storyboard (22.0 s, 660 frames @ 30 fps)

| # | Time | Scene |
|---|---|---|
| 1 Hook | 0.0–3.0 | "Ward 173 · Adyar, Chennai". 96 dots fill in; 90 turn red. "90 of 96 open complaints were never touched." |
| 2 Principle | 3.0–7.0 | Tools fall away (portal, forms, dashboards, spreadsheets, logins, struck out). "What does an officer actually need?" → Know what matters · Act on it · Close the loop. |
| 3 Talk | 7.0–11.5 | The real app. Tap the big mic → call bar "Listening". Officer asks in Tamil; it answers in Tamil and shows open drain manholes, safety + overdue. Caption: "Ask in Tamil. It answers in Tamil." |
| 4 Act | 11.5–15.5 | "Escalate the first one." → action card *Waiting for your confirmation*. "சரி" → *Recorded*, Telegram *sent*. Caption: "Nothing happens until you say சரி." / "Code confirms. Never the AI." |
| 5 Build | 15.5–19.0 | "Every part carries its weight." No queue · No cache · No vector store · No agent framework, struck one by one. What's left: Agora voice → Claude Opus 5.5 → Postgres → Telegram. |
| 6 Outro | 19.0–22.0 | Logo, "GCC Publyq", "Talk to your ward work in English, Tamil or Hindi.", gcc.publyq.in |

## Data notes
- Complaint IDs, types, streets and days-open come from the public Ward 173 GCC records (`Complaints/Images/ward173_complaints.csv`). No complainant names, handles or photos are shown.
- The action number (#41), its follow-up (#42) and the Telegram status are illustrative UI, as the app would show them. No real chat history, Telegram users or accounts appear.
