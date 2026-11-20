/* GCC Publyq web client — vanilla JS, no build step.
 * One loop: speak/type -> POST /api/agent/turn (SSE) -> stream reply text + cards -> speak reply.
 * Voice here is the browser fallback (Web Speech API). The primary voice path is
 * Agora ConvoAI calling the same brain via /v1/chat/completions. */
(() => {
  'use strict';
  const API = (window.GCC_API || '').replace(/\/$/, '');
  const $ = (s, r = document) => r.querySelector(s);
  const feed = $('#feed'), input = $('#text'), mic = $('#mic'), statusEl = $('#status');

  // ── session (per-tab convenience only; server keeps the real context) ──
  let SID;
  try { SID = sessionStorage.getItem('gcc.sid'); } catch (_) {}
  if (!SID) {
    SID = 'web-' + (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2));
    try { sessionStorage.setItem('gcc.sid', SID); } catch (_) {}
  }

  // ── helpers ────────────────────────────────────────────────────────────
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const h = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
  const scroll = () => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });

  function md(src) { // tiny, escape-first markdown: headings, lists, bold, italic, paragraphs
    const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/_(.+?)_/g, '<em>$1</em>');
    let out = '', list = null;
    const close = () => { if (list) { out += `</${list}>`; list = null; } };
    for (const line of String(src || '').split('\n')) {
      let m;
      if ((m = line.match(/^(#{1,3})\s+(.*)/))) { close(); out += `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`; }
      else if ((m = line.match(/^\s*[-*]\s+(.*)/))) { if (list !== 'ul') { close(); out += '<ul>'; list = 'ul'; } out += `<li>${inline(m[1])}</li>`; }
      else if ((m = line.match(/^\s*\d+\.\s+(.*)/))) { if (list !== 'ol') { close(); out += '<ol>'; list = 'ol'; } out += `<li>${inline(m[1])}</li>`; }
      else if (line.trim()) { close(); out += `<p>${inline(line)}</p>`; }
      else close();
    }
    close();
    return out;
  }

  const tags = (c) => [
    c.is_safety_risk ? '<span class="tag crit">safety</span>' : '',
    c.is_overdue && c.status_group === 'open' ? '<span class="tag warn">overdue</span>' : '',
    c.status_group === 'resolved' ? '<span class="tag pos">resolved</span>' : '',
    c.hotspot_tier === 'recurring' ? '<span class="tag info">recurring spot</span>' : '',
  ].join('');
  const post = (p) => p ? `<span title="${esc(p.post_id)}">${esc(p.post)}</span>` : '<span>—</span>';

  // ── card renderers ─────────────────────────────────────────────────────
  const R = {
    complaint_list(c) {
      const rows = c.items.map((i) => `<li><button data-say="Open ${esc(i.id)}">
        <span class="id">${esc(i.id)}</span>
        <span class="t">${tags(i)}${esc(i.issue || i.complaint_type)}</span>
        <span class="s">${esc(i.street || i.locality || '')}${i.days_open != null ? ` · open ${i.days_open} d` : ` · ${esc(i.filed)}`}${i.holder_post_id ? ` · ${esc(i.holder_post_id)}` : ''}</span>
      </button></li>`).join('');
      return `<div class="card"><h3>${c.total} complaint${c.total === 1 ? '' : 's'}${c.total > c.items.length ? ` · showing ${c.items.length}` : ''}</h3><ul class="rows">${rows || '<li>None</li>'}</ul></div>`;
    },
    complaint(c) {
      const r = c.responsible || {}, s = c.sop;
      const media = (c.media || []).slice(0, 12).map((m) => m.kind === 'video'
        ? `<a href="${esc(m.url)}" target="_blank" rel="noopener"><video src="${esc(m.url)}" preload="none" muted></video><small>${esc(m.uploader)} video</small></a>`
        : `<a href="${esc(m.url)}" target="_blank" rel="noopener"><img src="${esc(m.url)}" alt="${esc(m.uploader)} photo${m.stage ? ' ' + esc(m.stage) : ''}" loading="lazy" /><small>${esc(m.uploader)}${m.stage ? ' · ' + esc(m.stage) : ''}</small></a>`).join('');
      const events = (c.events || []).map((e) => `<li><time>${esc(fmtDate(e.at))} · ${esc(e.role || e.author_type)}</time>${esc((e.message_en || '').slice(0, 280))}</li>`).join('');
      const actions = (c.actions || []).filter((a) => a.status !== 'cancelled').map((a) => `<li><time>#${a.id} · ${esc(a.status)}</time>${esc(a.say)}</li>`).join('');
      return `<div class="card">
        <h3>${tags(c)}${esc(c.complaint_type)} <span class="mono" style="color:var(--fg-muted);font-size:12px">${esc(c.id)}</span></h3>
        <p>${esc(c.details)}</p>
        <dl class="kv" style="margin-top:10px">
          <dt>Where</dt><dd>${esc(c.specific_location || c.street || c.locality)}</dd>
          <dt>Filed</dt><dd>${esc(c.filed)}${c.days_open != null ? ` · open ${c.days_open} days` : ''} · GCC status <b>${esc(c.status)}</b></dd>
          ${c.safety_reasons ? `<dt>Safety</dt><dd>${esc(c.safety_reasons)}</dd>` : ''}
          <dt>Needs</dt><dd>${esc(c.whats_to_resolve || '—')}</dd>
        </dl>
        <h4>Responsibility</h4>
        <div class="chain">${r.holder ? post(r.holder) : '<span>no holder recorded</span>'}<i>→</i>${post(r.escalate_to)}${r.owner && r.holder && r.owner.post_id !== r.holder.post_id ? ` <i>· SOP owner</i> ${post(r.owner)}` : ''}</div>
        ${s ? `<h4>SOP · ${esc(s.sop_type)}</h4>
          <p><span class="tag ${c.priority.tier === 'P1' ? 'crit' : c.priority.tier === 'P2' ? 'warn' : 'info'}">${esc(c.priority.tier)}</span>${esc(c.priority.say)}</p>
          ${c.priority.rule ? `<p class="note">${esc(c.priority.rule)} (SOP rule)</p>` : ''}
          <ol class="steps">${s.diagnose.map((d) => `<li>${esc(d)}</li>`).join('')}</ol>
          <p style="font-size:13.5px;margin-top:6px"><b>Fix:</b> ${esc(s.fix || '—')}${s.not_ours_if && s.not_ours_if !== '-' ? ` <br><b>Not GCC's if:</b> ${esc(s.not_ours_if)}` : ''}</p>
          <p class="note">${esc(s.sop_source)}</p>` : ''}
        ${media ? `<h4>Evidence (${c.media.length})</h4><div class="photos">${media}</div>` : ''}
        ${events ? `<h4>Officer actions on GCC portal</h4><ul class="timeline">${events}</ul>` : '<h4>Officer actions</h4><p class="note">None recorded on the GCC portal.</p>'}
        ${actions ? `<h4>Logged here</h4><ul class="timeline">${actions}</ul>` : ''}
        <div class="act">
          ${c.status_group === 'open' ? `<button class="primary" data-say="Escalate ${esc(c.id)}">Escalate</button>
          <button data-say="Remind me about ${esc(c.id)} in 2 days">Follow up in 2 days</button>` : ''}
          <button data-say="Report for ${esc(c.id)}">Report</button>
          ${c.source_url ? `<a class="note" href="${esc(c.source_url)}" target="_blank" rel="noopener" style="align-self:center">GCC portal ↗</a>` : ''}
        </div>
        <p class="note">Data as of GCC portal fetch ${esc((c.data_as_of || '').slice(0, 10))}.</p>
      </div>`;
    },
    action(a) {
      const pending = a.status === 'proposed';
      return `<div class="card action ${esc(a.status)}">
        <h3>${pending ? 'Waiting for your confirmation' : a.status === 'confirmed' ? 'Recorded' : esc(a.status)} · #${a.id}</h3>
        <dl class="kv"><dt>Action</dt><dd>${esc(a.kind.replace('_', ' '))} · ${esc(a.complaint_id)}</dd>
          ${a.target_post ? `<dt>To</dt><dd>${esc(a.target_post)}</dd>` : ''}
          ${a.due_at ? `<dt>Due</dt><dd>${esc(fmtDate(a.due_at))}</dd>` : ''}
          ${a.note ? `<dt>Note</dt><dd>${esc(a.note)}</dd>` : ''}
          ${a.follow_up ? `<dt>Follow-up</dt><dd>#${a.follow_up.id} · ${esc(fmtDate(a.follow_up.due_at))}</dd>` : ''}</dl>
        ${pending ? '<div class="act"><button class="primary" data-say="Confirm">Confirm</button><button data-say="Cancel">Cancel</button></div>' : ''}
        <p class="note">Recorded in GCC Publyq, not written to the GCC portal.</p>
      </div>`;
    },
    actions(c) {
      const rows = c.items.map((a) => `<li><button data-say="Open ${esc(a.complaint_id)}"><span class="id">#${a.id}</span>
        <span class="t">${esc(a.kind.replace('_', ' '))} · ${esc(a.complaint_id)}</span>
        <span class="s">${esc(a.status)}${a.due_at ? ' · due ' + esc(fmtDate(a.due_at)) : ''}${a.note ? ' · ' + esc(a.note) : ''}</span></button></li>`).join('');
      return `<div class="card"><h3>Actions</h3><ul class="rows">${rows || '<li class="note">None yet.</li>'}</ul></div>`;
    },
    officials(c) {
      const rows = c.posts.slice(0, 12).map((p) => `<li><button data-say="Who is ${esc(p.post_id)}?"><span class="id">${esc(p.post_id)}</span>
        <span class="t">${esc(p.post)}</span><span class="s">${p.open_now || 0} open${p.typical_hours_to_first_action ? ` · first action ~${Math.round(p.typical_hours_to_first_action)} h` : ''}</span></button></li>`).join('');
      return `<div class="card"><h3>Ward ${c.ward_no} · ${esc(c.zone)} zone</h3>
        <dl class="kv"><dt>Ward committee officer</dt><dd>${esc(c.ward_committee_officer)}</dd><dt>MLA</dt><dd>${esc(c.mla)} (${esc(c.party)}, ${esc(c.constituency)})</dd></dl>
        <h4>Posts</h4><ul class="rows">${rows}</ul><p class="note">GCC records name posts, not people.</p></div>`;
    },
    official(p) {
      return `<div class="card"><h3>${esc(p.post)}</h3>
        <dl class="kv"><dt>Open now</dt><dd>${p.open_now ?? '—'} (${p.open_overdue ?? 0} overdue)</dd>
        <dt>Typical first action</dt><dd>${p.typical_hours_to_first_action ? Math.round(p.typical_hours_to_first_action) + ' h' : '—'}</dd>
        <dt>Typical resolution</dt><dd>${p.typical_days_to_resolve ?? '—'} days</dd></dl>
        <h4>Escalation chain</h4><div class="chain"><span>${esc(p.post_id)}</span>${p.chain.map((c) => `<i>→</i>${post(c)}`).join('')}</div></div>`;
    },
    sop(s) {
      return `<div class="card"><h3>SOP · ${esc(s.sop_type)}</h3>
        <p><span class="tag ${s.priority_tier === 'P1' ? 'crit' : s.priority_tier === 'P2' ? 'warn' : 'info'}">${esc(s.priority_tier)}</span>${esc(s.tier_meaning)}</p>
        <h4>Diagnose</h4><ol class="steps">${(s.diagnose || []).map((d) => `<li>${esc(d)}</li>`).join('')}</ol>
        <h4>Carry</h4><ul class="steps">${(s.carry || []).map((d) => `<li>${esc(d)}</li>`).join('')}</ul>
        <h4>Fix</h4><p>${esc(s.fix)}</p>
        <dl class="kv" style="margin-top:8px"><dt>Owner</dt><dd>${esc(s.owner_role)}</dd><dt>Approver</dt><dd>${esc(s.approver_role)}</dd>
        ${s.not_ours_if && s.not_ours_if !== '-' ? `<dt>Not GCC's if</dt><dd>${esc(s.not_ours_if)}</dd>` : ''}</dl>
        <p class="note">${esc(s.sop_source)}</p></div>`;
    },
    triage(t) {
      if (!t.complaint_type) return `<div class="card"><h3>Triage</h3><p>${esc(t.say)}</p></div>`;
      const d = (t.possible_duplicates || []).map((i) => `<li><button data-say="Open ${esc(i.id)}"><span class="id">${esc(i.id)}</span><span class="t">${esc(i.issue || i.complaint_type)}</span><span class="s">${esc(i.street || i.locality || '')} · open ${i.days_open ?? '?'} d</span></button></li>`).join('');
      return `<div class="card"><h3>Triage · ${esc(t.complaint_type)}</h3>
        <dl class="kv"><dt>Priority</dt><dd>${esc(t.priority || '—')}</dd><dt>Owner</dt><dd>${t.owner ? esc(t.owner.post) : '—'}</dd>
        <dt>Locality</dt><dd>${esc(t.locality || 'not detected')}</dd><dt>Confidence</dt><dd>${Math.round((t.confidence || 0) * 100)}%${t.confident ? '' : ' · please check'}</dd>
        ${t.safety_signal ? `<dt>Safety signal</dt><dd><span class="tag crit">${esc(t.safety_signal)}</span></dd>` : ''}</dl>
        ${d ? `<h4>Already reported? Open complaints at this spot</h4><ul class="rows">${d}</ul>` : ''}
        ${t.sop ? `<h4>First check</h4><p>${esc(t.sop.diagnose[0] || '')}</p>` : ''}</div>`;
    },
    report(r) { return `<div class="card md">${md(r.markdown)}</div>`; },
    summary(s) { return `<div class="card"><h3>Ward ${s.ward_no}</h3><p>${s.open} open · ${s.open_overdue} overdue · ${s.open_safety} safety risk</p></div>`; },
  };

  // ── speech out (browser fallback TTS) ──────────────────────────────────
  const synth = window.speechSynthesis;
  let voice = null;
  const pickVoice = () => { const vs = synth ? synth.getVoices() : []; voice = vs.find((v) => v.lang === 'en-IN') || vs.find((v) => v.lang.startsWith('en')) || null; };
  if (synth) { pickVoice(); synth.onvoiceschanged = pickVoice; }
  function speak(text) {
    if (!synth || !$('#speak').checked || !text) return;
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text.replace(/(\d{4})-(\d{3})([A-Z]{3})/g, '$1 $2 $3'));
    if (voice) u.voice = voice;
    u.lang = voice ? voice.lang : 'en-IN';
    u.rate = 1.05;
    u.onstart = () => mic.classList.add('speaking');
    u.onend = u.onerror = () => mic.classList.remove('speaking');
    synth.speak(u);
  }

  // ── speech in (browser fallback STT) ───────────────────────────────────
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null, listening = false;
  if (SR) {
    rec = new SR();
    rec.lang = 'en-IN'; rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
    rec.onresult = (e) => {
      let txt = '', final = false;
      for (const r of e.results) { txt += r[0].transcript; final = final || r.isFinal; }
      input.value = txt;
      if (final) { stopListening(); send(txt); }
    };
    rec.onerror = (e) => { stopListening(); if (e.error === 'not-allowed') status('Microphone blocked — type instead', true); };
    rec.onend = () => stopListening();
  } else {
    mic.disabled = true;
    mic.title = 'Voice input not supported in this browser — type instead';
    $('#voiceMode').textContent = 'not supported here (type instead)';
  }
  function startListening() {
    if (!rec || listening) return;
    if (synth) synth.cancel(); // barge-in: stop talking when the user starts
    try { rec.start(); listening = true; mic.classList.add('live'); mic.setAttribute('aria-pressed', 'true'); input.placeholder = 'Listening…'; } catch (_) {}
  }
  function stopListening() {
    if (!listening) return;
    listening = false; mic.classList.remove('live'); mic.setAttribute('aria-pressed', 'false'); input.placeholder = 'Say or type: escalate 2026-185OET';
    try { rec.stop(); } catch (_) {}
  }
  mic.addEventListener('click', () => (listening ? stopListening() : startListening()));

  // ── the turn ───────────────────────────────────────────────────────────
  let busy = false;
  async function send(text) {
    text = (text || '').trim();
    if (!text || busy) return;
    busy = true; input.value = '';
    $('#hello')?.remove();
    const turn = h(`<article class="turn"><div class="you"><p>${esc(text)}</p></div><div class="bot"><p class="say"></p><div class="cards"></div><p class="meta"></p></div></article>`);
    feed.append(turn); scroll();
    const sayEl = $('.say', turn), cardsEl = $('.cards', turn), metaEl = $('.meta', turn);
    const t0 = performance.now();
    let said = '';
    try {
      const res = await fetch(`${API}/api/agent/turn`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, session_id: SID }) });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          const ev = (block.match(/^event: (.*)$/m) || [])[1];
          const data = JSON.parse((block.match(/^data: (.*)$/m) || [])[1] || '{}');
          if (ev === 'delta') { said += data.text; sayEl.textContent = said; }
          else if (ev === 'card' && R[data.type]) { cardsEl.append(h(R[data.type](data))); }
          else if (ev === 'done') {
            const wall = Math.round(performance.now() - t0);
            metaEl.textContent = `${data.route}${data.intent ? ' · ' + data.intent : ''} · server ${data.latency_ms ?? '?'} ms · total ${wall} ms`;
          }
        }
        scroll();
      }
    } catch (err) {
      said = "I couldn't reach the server. Check your connection and try again.";
      sayEl.textContent = said;
    } finally {
      busy = false;
      speak(said);
      scroll();
    }
  }

  $('#composer').addEventListener('submit', (e) => { e.preventDefault(); send(input.value); });
  document.addEventListener('click', (e) => { const b = e.target.closest('[data-say]'); if (b) { e.preventDefault(); send(b.dataset.say); } });

  // ── health ─────────────────────────────────────────────────────────────
  function status(text, bad) { statusEl.textContent = text; statusEl.className = 'status ' + (bad ? 'bad' : 'ok'); }
  fetch(`${API}/api/health`).then((r) => r.json()).then((d) => status(d.ok ? `${d.complaints} records · ${d.llm ? 'AI on' : 'fast mode'}` : 'database down', !d.ok))
    .catch(() => status('offline', true));
})();
