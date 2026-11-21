/* GCC Publyq web client — vanilla JS, no build step.
 * Role first: the user picks (or says) which GCC post they are; every answer is scoped to it.
 * Loop: type, or talk over an Agora voice call (Murf Falcon 2.1) -> the same brain -> reply + cards. No browser speech.
 * Conversations are stored server-side per device and can be resumed from History. */
(() => {
  'use strict';
  const API = (window.GCC_API || '').replace(/\/$/, '');
  const $ = (s, r = document) => r.querySelector(s);
  const feed = $('#feed'), input = $('#text'), mic = $('#mic'), statusEl = $('#status'), pill = $('#rolePill');

  // ── tiny persistent prefs (per-device convenience; server holds the real history) ──
  const store = {
    get(k) { try { return localStorage.getItem('gcc.' + k); } catch (_) { return null; } },
    set(k, v) { try { v == null ? localStorage.removeItem('gcc.' + k) : localStorage.setItem('gcc.' + k, v); } catch (_) {} },
  };
  const uid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
  const DEVICE = store.get('device') || (() => { const d = 'dev-' + uid(); store.set('device', d); return d; })();
  let convId = store.get('conv') || null;
  let roleId = store.get('role') || null;
  let roles = [];
  const seenTurns = new Set();   // turn IDs already on screen (streamed or loaded)
  let voiceStatus = { ready: false, missing: [] };

  // ── helpers ────────────────────────────────────────────────────────────
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const h = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
  const ago = (iso) => { const s = (Date.now() - new Date(iso)) / 1000; return s < 60 ? 'just now' : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; };
  const nearBottom = () => window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;
  const scroll = (force) => { if (force || stick) window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }); };
  let stick = true;
  window.addEventListener('scroll', () => { stick = nearBottom(); }, { passive: true });
  const roleOf = (id) => roles.find((r) => r.id === id);

  function md(src) {
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
  const rows = (items) => items.map((i) => `<li><button data-say="Open ${esc(i.id)}">
      <span class="id">${esc(i.id)}</span>
      <span class="t">${tags(i)}${esc(i.issue || i.complaint_type)}</span>
      <span class="s">${esc(i.street || i.locality || '')}${i.days_open != null ? ` · open ${i.days_open} d` : ` · ${esc(i.filed)}`}${i.holder_post_id ? ` · ${esc(i.holder_post_id)}` : ''}</span>
    </button></li>`).join('');

  // ── card renderers ─────────────────────────────────────────────────────
  const R = {
    queue(q) {
      const s = q.stats || {};
      return `<div class="card"><h3>${esc(q.role)} · open work</h3>
        <div class="stats"><span><b>${q.total}</b> open</span><span><b>${s.open_safety ?? 0}</b> safety</span><span><b>${s.open_untouched ?? 0}</b> untouched</span>
        <span><b>${s.resolved_90d ?? 0}</b> resolved (90 d)</span>${s.open_unowned ? `<span><b>${s.open_unowned}</b> no owner post</span>` : ''}</div>
        ${q.items.length ? `<ul class="rows">${rows(q.items)}</ul>` : '<p class="note">Nothing open in your scope in the GCC records.</p>'}
        ${q.total > q.items.length ? `<p class="note">Showing ${q.items.length} of ${q.total}, most urgent first.</p>` : ''}</div>`;
    },
    patterns(p) {
      if (!p.total) return `<div class="card"><h3>Patterns</h3><p class="note">${esc(p.say)}</p></div>`;
      const max = Math.max(...p.issues.map((i) => i.n), 1);
      const spots = p.spots.map((s) => `<li><b>${esc(s.issue)}</b> · ${esc(s.street)}
        <div class="s">${s.n} complaints${s.open ? ` · <span class="tag warn">${s.open} open</span>` : ''}${s.came_back ? ` · <span class="tag crit">${s.came_back} came back after fix</span>` : ''} · ${esc(s.first_seen)} → ${esc(s.last_seen)}</div>
        <div class="act" style="margin-top:6px">${(s.ids || []).slice(0, 3).map((id) => `<button data-say="Open ${esc(id)}">${esc(id)}</button>`).join('')}</div></li>`).join('');
      const bars = p.issues.map((i) => `<div class="bar"><span>${esc(i.issue)}</span><i style="width:${Math.max(4, (i.n / max) * 100)}%"></i><em>${i.n}${i.open ? ` · ${i.open} open` : ''} · ${i.last_90d} in 90 d</em></div>`).join('');
      return `<div class="card"><h3>Recurring patterns · ${esc(p.role)}</h3>
        <div class="stats"><span><b>${p.total}</b> complaints in 2026</span><span><b>${p.came_back}</b> came back after fix</span>
        ${p.median_days_to_close != null ? `<span><b>${p.median_days_to_close.toFixed(1)}</b> median days to close</span>` : ''}</div>
        ${spots ? `<h4>Repeat spots</h4><ul class="spots">${spots}</ul>` : '<p class="note">No street has repeated complaints in your scope.</p>'}
        <h4>Top issues</h4><div class="bars">${bars}</div></div>`;
    },
    complaint_list(c) {
      return `<div class="card"><h3>${c.total} complaint${c.total === 1 ? '' : 's'}${c.total > c.items.length ? ` · showing ${c.items.length}` : ''}</h3><ul class="rows">${rows(c.items) || '<li class="note">None</li>'}</ul></div>`;
    },
    complaint(c) {
      const r = c.responsible || {}, s = c.sop, pr = c.priority;
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
          ${c.status_group === 'open' ? `<p><span class="tag ${pr.tier === 'P1' ? 'crit' : pr.tier === 'P2' ? 'warn' : 'info'}">${esc(pr.tier)}</span>${esc(pr.say)}</p>
          ${pr.rule ? `<p class="note">${esc(pr.rule)} (SOP rule)</p>` : ''}` : ''}
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
      return `<div class="card action ${esc(a.status)}" data-action-id="${a.id}">
        <h3>${pending ? 'Waiting for your confirmation' : a.status === 'confirmed' ? 'Recorded' : esc(a.status)} · #${a.id}</h3>
        <dl class="kv"><dt>Action</dt><dd>${esc(a.kind.replace('_', ' '))} · ${esc(a.complaint_id)}</dd>
          ${a.target_post ? `<dt>To</dt><dd>${esc(a.target_post)}</dd>` : ''}
          ${a.due_at ? `<dt>Due</dt><dd>${esc(fmtDate(a.due_at))}</dd>` : ''}
          ${a.note ? `<dt>Note</dt><dd>${esc(a.note)}</dd>` : ''}
          ${a.follow_up ? `<dt>Follow-up</dt><dd>#${a.follow_up.id} · ${esc(fmtDate(a.follow_up.due_at))}</dd>` : ''}
          ${(a.payload || {}).to_status ? `<dt>Requested status</dt><dd>${esc(a.payload.to_status.replace('_', ' '))}</dd>` : ''}
          ${a.notify && !a.communication && a.status === 'proposed' ? '<dt>Share</dt><dd>Ward 173 Telegram group, after you confirm</dd>' : ''}
          ${a.communication ? `<dt>Telegram</dt><dd><span class="tag ${a.communication.status === 'sent' ? 'pos' : 'warn'}">${esc(a.communication.status)}</span>${a.communication.error ? ' ' + esc(a.communication.error) : ''}</dd>` : ''}</dl>
        ${pending ? `<div class="act"><button class="primary" data-say="Confirm #${a.id}">Confirm #${a.id}</button><button data-say="Cancel #${a.id}">Cancel</button></div>` : ''}
        <p class="note">Recorded in GCC Publyq, not written to the GCC portal.</p>
      </div>`;
    },
    actions(c) {
      const li = c.items.map((a) => `<li><button data-say="Open ${esc(a.complaint_id)}"><span class="id">#${a.id}</span>
        <span class="t">${esc(a.kind.replace('_', ' '))} · ${esc(a.complaint_id)}</span>
        <span class="s">${esc(a.status)}${a.due_at ? ' · due ' + esc(fmtDate(a.due_at)) : ''}${a.note ? ' · ' + esc(a.note) : ''}</span></button></li>`).join('');
      return `<div class="card"><h3>Follow-ups & actions</h3><ul class="rows">${li || '<li class="note">None yet.</li>'}</ul></div>`;
    },
    officials(c) {
      const li = c.posts.slice(0, 12).map((p) => `<li><button data-say="Who is ${esc(p.post_id)}?"><span class="id">${esc(p.post_id)}</span>
        <span class="t">${esc(p.post)}</span><span class="s">${p.open_now || 0} open${p.typical_hours_to_first_action ? ` · first action ~${Math.round(p.typical_hours_to_first_action)} h` : ''}</span></button></li>`).join('');
      return `<div class="card"><h3>Ward ${c.ward_no} · ${esc(c.zone)} zone</h3>
        <dl class="kv"><dt>Ward committee officer</dt><dd>${esc(c.ward_committee_officer)}</dd><dt>MLA</dt><dd>${esc(c.mla)} (${esc(c.party)}, ${esc(c.constituency)})</dd></dl>
        <h4>Posts</h4><ul class="rows">${li}</ul><p class="note">GCC records name posts, not people.</p></div>`;
    },
    official(p) {
      return `<div class="card"><h3>${esc(p.post)}</h3>
        <dl class="kv"><dt>Open now</dt><dd>${p.open_now ?? '—'} (${p.open_overdue ?? 0} overdue)</dd>
        <dt>Typical first action</dt><dd>${p.typical_hours_to_first_action ? Math.round(p.typical_hours_to_first_action) + ' h' : '—'}</dd>
        <dt>Typical resolution</dt><dd>${p.typical_days_to_resolve ?? '—'} days</dd></dl>
        <h4>Escalation chain</h4><div class="chain"><span>${esc(p.post_id)}</span>${p.chain.map((c) => `<i>→</i>${post(c)}`).join('')}</div></div>`;
    },
    sop(s) {
      if (!s.sop_type) return '';
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
      const d = (t.possible_duplicates || []);
      return `<div class="card"><h3>Triage · ${esc(t.complaint_type)}</h3>
        <dl class="kv"><dt>Priority</dt><dd>${esc(t.priority || '—')}</dd><dt>Owner</dt><dd>${t.owner ? esc(t.owner.post) : '—'}</dd>
        <dt>Locality</dt><dd>${esc(t.locality || 'not detected')}</dd><dt>Confidence</dt><dd>${Math.round((t.confidence || 0) * 100)}%${t.confident ? '' : ' · please check'}</dd>
        ${t.safety_signal ? `<dt>Safety signal</dt><dd><span class="tag crit">${esc(t.safety_signal)}</span></dd>` : ''}</dl>
        ${d.length ? `<h4>Already reported? Open complaints at this spot</h4><ul class="rows">${rows(d)}</ul>` : ''}
        ${t.sop ? `<h4>First check</h4><p>${esc(t.sop.diagnose[0] || '')}</p>` : ''}</div>`;
    },
    report(r) { return `<div class="card md">${md(r.markdown)}</div>`; },
    table(q) {
      let data = []; try { data = JSON.parse(q.rows_json || '[]'); } catch (_) {}
      const cols = q.columns || [];
      const head = cols.map((c) => `<th>${esc(c)}</th>`).join('');
      const body = data.slice(0, 20).map((r) => `<tr>${cols.map((c) => `<td>${esc(typeof r[c] === 'object' && r[c] !== null ? JSON.stringify(r[c]) : r[c])}</td>`).join('')}</tr>`).join('');
      return `<div class="card"><h3>Looked up in the records · ${q.row_count} row${q.row_count === 1 ? '' : 's'}${q.truncated ? ' (truncated)' : ''}</h3>
        <div class="tablewrap"><table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
        <details class="note"><summary>Query</summary><code>${esc(q.sql || '')}</code></details></div>`;
    },
    communications(c) {
      const li = c.items.map((m) => `<li><span class="tag ${m.status === 'sent' ? 'pos' : 'warn'}">${esc(m.status)}</span>
        ${esc(m.recipient)} · ${esc(fmtDate(m.created_at))}${m.complaint_id ? ' · ' + esc(m.complaint_id) : ''}${m.error ? ` · <span class="note">${esc(m.error)}</span>` : ''}</li>`).join('');
      return `<div class="card"><h3>Telegram messages</h3><ul class="timeline">${li || '<li class="note">None sent yet.</li>'}</ul></div>`;
    },
    summary(s) { return `<div class="card"><h3>Ward ${s.ward_no}</h3><p>${s.open} open · ${s.open_overdue} overdue · ${s.open_safety} safety risk</p></div>`; },
  };
  const card = (c) => (R[c.type] ? h(R[c.type](c) || '<span hidden></span>') : null);

  // ── role selection ─────────────────────────────────────────────────────
  const CHIPS = {
    field: [["What's on my plate?", "What's on my plate?"], ['Recurring problems', 'What are the recurring patterns?'],
      ['Brief me', 'Brief me'], ['My follow-ups', 'What follow-ups do I have?'],
      ['Triage a call', 'A resident called about an open manhole near the school in Gandhi Nagar']],
    leadership: [['Brief me', 'Brief me'], ['Where are we stuck?', 'What is high priority for my team?'],
      ['Recurring patterns', 'What are the recurring patterns?'], ['Follow-ups due', 'What follow-ups are due?'],
      ["Who's in charge?", 'Who are the officials?']],
  };
  CHIPS.ward = CHIPS.leadership;

  function setRole(id, { announce } = {}) {
    roleId = id; store.set('role', id);
    const r = roleOf(id);
    pill.hidden = !r;
    if (r) { pill.textContent = `${r.label} · ${r.open} open`; pill.title = `${r.post} — tap to switch role`; }
    if (announce && r) send(`I am the ${r.label}`);
  }

  function showRolePicker() {
    feed.innerHTML = '';
    const group = (kind) => roles.filter((r) => r.kind === kind).map((r) => `<button class="role-card${r.id === roleId ? ' on' : ''}" data-role="${esc(r.id)}">
      <span class="n ${r.open_safety ? 'crit' : ''}">${r.open} open${r.open_safety ? ` · ${r.open_safety} safety` : ''}</span>
      <b>${esc(r.label)}</b><span>${esc(r.dept)} · ${esc(r.id === 'WARD' ? 'whole ward' : r.id)}</span></button>`).join('');
    feed.append(h(`<div class="pick">
      <h1>Who's speaking?</h1>
      <p>Answers, priorities and patterns follow your post's responsibilities in the GCC records. You can also say “As the Assistant Engineer (Electrical)…”.</p>
      <h2>In the field</h2><div class="roles">${group('field')}</div>
      <h2>Leadership</h2><div class="roles">${group('leadership')}${group('ward')}</div>
    </div>`));
  }

  function showHello() {
    const r = roleOf(roleId);
    if (!r) return showRolePicker();
    feed.innerHTML = '';
    const chips = (CHIPS[r.kind] || CHIPS.field).map(([l, s]) => `<button data-say="${esc(s)}">${esc(l)}</button>`).join('');
    feed.append(h(`<div class="hello">
      <span class="eyebrow">${esc(r.post)}</span>
      <h1>${r.open ? `${r.open} open on your desk${r.open_safety ? `, ${r.open_safety} safety risk` : ''}.` : 'Nothing open on your desk.'}</h1>
      <p>Tap the mic and speak, or type. Every answer comes from GCC records; every action waits for your “confirm”.</p>
      <div class="chips">${chips}</div></div>`));
  }

  // ── conversations (server-side history) ────────────────────────────────
  function newConversation() {
    if (call) endCall();
    seenTurns.clear();
    convId = 'c-' + uid(); store.set('conv', convId);
    queue.length = 0; renderQueue();
    showHello();
  }

  async function loadConversation(id) {
    const res = await fetch(`${API}/api/conversations/${encodeURIComponent(id)}?device_id=${encodeURIComponent(DEVICE)}`);
    if (!res.ok) return false;
    const c = await res.json();
    convId = id; store.set('conv', id);
    if (c.role_id && roleOf(c.role_id)) setRole(c.role_id);
    feed.innerHTML = '';
    seenTurns.clear();
    for (const t of c.turns) {
      seenTurns.add(t.id);
      const el = turnEl(t.text);
      el.classList.add('past');
      $('.say', el).textContent = t.reply || '';
      for (const cd of t.cards || []) { const n = card(cd); if (n) $('.cards', el).append(n); }
      $('.meta', el).textContent = `${fmtDate(t.created_at)} · ${t.route}${t.intent ? ' · ' + t.intent : ''}`;
      feed.append(el);
    }
    if (c.turns.length) feed.append(h(`<p class="divider">Resumed · ${esc(ago(c.updated_at))} — you can carry on where you left off</p>`));
    else showHello();
    scroll(true);
    return true;
  }

  async function openHistory() {
    const sheet = $('#historySheet'), list = $('#historyList');
    list.innerHTML = '<li class="note">Loading…</li>';
    sheet.hidden = false;
    $('[data-act="close"]', sheet).focus();
    try {
      const { items } = await (await fetch(`${API}/api/conversations?device_id=${encodeURIComponent(DEVICE)}`)).json();
      list.innerHTML = items.length ? items.map((c) => `<li class="${c.id === convId ? 'on' : ''}">
        <button class="open" data-conv="${esc(c.id)}"><b>${esc(c.title || 'Conversation')}</b>
        <span>${esc(roleOf(c.role_id)?.label || 'Ward 173')} · ${c.turns} turn${c.turns === 1 ? '' : 's'} · ${esc(ago(c.updated_at))}</span></button>
        <button class="del" data-del="${esc(c.id)}" aria-label="Delete conversation">✕</button></li>`).join('') : '<li class="note">No conversations yet.</li>';
    } catch (_) { list.innerHTML = '<li class="note">Couldn\'t load history.</li>'; }
  }
  const closeHistory = () => { $('#historySheet').hidden = true; };

  // ── the turn (queued, never dropped) ───────────────────────────────────
  const queue = [];
  let busy = false;
  const renderQueue = () => { const q = $('#queued'); q.hidden = !queue.length; q.textContent = queue.length ? `Queued: ${queue.map((x) => `“${x}”`).join(', ')}` : ''; };
  const turnEl = (text) => h(`<article class="turn"><div class="you"><p>${esc(text)}</p></div><div class="bot"><p class="say"></p><div class="cards"></div><p class="meta"></p></div></article>`);

  function send(text) {
    text = (text || '').trim();
    if (!text) return;
    input.value = '';
    if (!convId) { convId = 'c-' + uid(); store.set('conv', convId); }
    if (busy) { queue.push(text); renderQueue(); return; }
    run(text);
  }

  async function run(text) {
    busy = true;
    $('.hello', feed)?.remove(); $('.pick', feed)?.remove();
    const turn = turnEl(text);
    feed.append(turn); stick = true; scroll(true);
    const sayEl = $('.say', turn), cardsEl = $('.cards', turn), metaEl = $('.meta', turn);
    const t0 = performance.now();
    let said = '';
    try {
      const res = await fetch(`${API}/api/agent/turn`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, session_id: convId, role_id: roleId, device_id: DEVICE }) });
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
          else if (ev === 'card') {
            if (data.type === 'action' && data.status !== 'proposed') { // a decision replaces its pending card
              document.querySelectorAll(`.action.proposed[data-action-id="${data.id}"] .act`).forEach((n) => n.remove());
            }
            const n = card(data); if (n) cardsEl.append(n);
          } else if (ev === 'done') {
            if (data.turn_id) seenTurns.add(data.turn_id);
            metaEl.textContent = `${data.route}${data.intent ? ' · ' + data.intent : ''} · server ${data.latency_ms ?? '?'} ms · total ${Math.round(performance.now() - t0)} ms`;
            if (data.role && data.role !== roleId) setRole(data.role);
          }
        }
        scroll();
      }
    } catch (err) {
      said = "I couldn't reach the server. Check your connection and try again.";
      sayEl.textContent = said;
    }
    busy = false;
    scroll();
    if (queue.length) { const q = queue.shift(); renderQueue(); run(q); }
  }

  // ── Agora voice call (primary voice path) ──────────────────────────────
  // Adapted from Agora's quickstart lifecycle: token -> invite agent -> join RTC -> publish mic,
  // play the agent's audio. The agent's LLM is our brain, so every spoken turn is saved under this
  // conversation; we poll it to show the same cards the chat shows.
  const RTC_SDK = 'https://cdn.jsdelivr.net/npm/agora-rtc-sdk-ng@4.24.10/AgoraRTC_N-production.min.js';
  let call = null;
  const loadScript = (src) => new Promise((ok, fail) => {
    if (window.AgoraRTC) return ok();
    const s = document.createElement('script'); s.src = src; s.onload = ok; s.onerror = () => fail(new Error('Could not load Agora SDK')); document.head.append(s);
  });
  const callState = (txt) => { $('#callState').textContent = txt; };
  const api = async (path, opts) => {
    const r = await fetch(`${API}${path}`, opts);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || d.error || `HTTP ${r.status}`);
    return d;
  };

  async function startCall() {
    if (call) return;
    if (!voiceStatus.ready) { status(`Voice calls need: ${voiceStatus.missing.join(', ')}`, true); return; }
    if (!convId) { convId = 'c-' + uid(); store.set('conv', convId); }
    $('.hello', feed)?.remove(); $('.pick', feed)?.remove();
    call = { agentId: null, client: null, mic: null, poll: null };
    document.body.classList.add('in-call'); $('#callbar').hidden = false; callState('Connecting…');
    mic.classList.add('live'); mic.setAttribute('aria-pressed', 'true'); mic.setAttribute('aria-label', 'End voice call');
    try {
      await loadScript(RTC_SDK);
      const cfg = await api(`/api/voice/config?session_id=${encodeURIComponent(convId)}`);
      const started = await api('/api/voice/start', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel: cfg.channel, agent_uid: cfg.agent_uid, user_uid: cfg.uid, session_id: convId, role_id: roleId, device_id: DEVICE, voice: $('#voice').value }) });
      $('#callVoice').textContent = `Agora · Murf Falcon 2.1 · ${started.voice}`;
      call.agentId = started.agent_id;
      const client = window.AgoraRTC.createClient({ mode: 'rtc', codec: 'vp8' });
      call.client = client;
      client.on('user-published', async (user, mediaType) => {
        await client.subscribe(user, mediaType);
        if (mediaType === 'audio') { user.audioTrack.play(); callState('Live — speak naturally'); }
      });
      client.on('user-left', () => callState('Agent left the call'));
      await client.join(cfg.app_id, cfg.channel, cfg.token, cfg.uid);
      call.mic = await window.AgoraRTC.createMicrophoneAudioTrack({ AEC: true, ANS: true, AGC: true });
      await client.publish([call.mic]);
      callState('Agent joining…');
      call.poll = setInterval(pollTurns, 1500);
      feed.append(h(`<p class="divider">Voice call started · ${esc(started.greeting)}</p>`)); scroll(true);
    } catch (err) {
      feed.append(h(`<p class="divider" style="color:var(--crit)">Call failed: ${esc(err.message)}. Browser voice and typing still work.</p>`));
      await endCall();
    }
  }

  async function endCall() {
    const c = call; if (!c) return;
    call = null;
    clearInterval(c.poll);
    document.body.classList.remove('in-call'); $('#callbar').hidden = true;
    mic.classList.remove('live'); mic.setAttribute('aria-pressed', 'false'); mic.setAttribute('aria-label', 'Start voice call');
    try { if (c.mic) { c.mic.stop(); c.mic.close(); } } catch (_) {}
    try { if (c.client) await c.client.leave(); } catch (_) {}
    if (c.agentId) api('/api/voice/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agent_id: c.agentId }) }).catch(() => {});
    feed.append(h('<p class="divider">Call ended · the transcript and actions are saved in History</p>')); scroll(true);
  }

  async function pollTurns() {
    if (!call || !convId) return;
    try {
      const c = await api(`/api/conversations/${encodeURIComponent(convId)}?device_id=${encodeURIComponent(DEVICE)}`);
      for (const t of c.turns) {
        if (seenTurns.has(t.id)) continue;
        seenTurns.add(t.id);
        const el = turnEl(t.text);
        el.classList.add('voice');
        $('.say', el).textContent = t.reply || '';
        for (const cd of t.cards || []) { const n = card(cd); if (n) $('.cards', el).append(n); }
        $('.meta', el).textContent = `voice · ${t.route}${t.intent ? ' · ' + t.intent : ''} · server ${t.latency_ms ?? '?'} ms`;
        feed.append(el);
        if (t.role_id && t.role_id !== roleId) setRole(t.role_id);
        scroll();
      }
    } catch (_) { /* transient; next tick retries */ }
  }
  window.addEventListener('pagehide', () => { if (call?.agentId) navigator.sendBeacon?.(`${API}/api/voice/stop`, new Blob([JSON.stringify({ agent_id: call.agentId })], { type: 'application/json' })); });

  // ── events ─────────────────────────────────────────────────────────────
  $('#composer').addEventListener('submit', (e) => { e.preventDefault(); send(input.value); });
  document.addEventListener('click', async (e) => {
    const t = e.target.closest('[data-say],[data-role],[data-act],[data-conv],[data-del]');
    if (!t) { if (e.target.id === 'historySheet') closeHistory(); return; }
    e.preventDefault();
    if (t.dataset.say) return send(t.dataset.say);
    if (t.dataset.role) { setRole(t.dataset.role); return feed.querySelector('.turn') ? send(`I am the ${roleOf(t.dataset.role).label}`) : showHello(); }
    if (t.dataset.conv) { closeHistory(); return loadConversation(t.dataset.conv); }
    if (t.dataset.del) {
      await fetch(`${API}/api/conversations/${encodeURIComponent(t.dataset.del)}?device_id=${encodeURIComponent(DEVICE)}`, { method: 'DELETE' });
      if (t.dataset.del === convId) newConversation();
      return openHistory();
    }
    const act = t.dataset.act;
    if (act === 'call') call ? endCall() : startCall();
    else if (act === 'endcall') endCall();
    else if (act === 'new') newConversation();
    else if (act === 'history') openHistory();
    else if (act === 'close') closeHistory();
    else if (act === 'roles') showRolePicker();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeHistory(); });
  const voiceSel = $('#voice');
  if (['anisha', 'abhinav'].includes(store.get('voice'))) voiceSel.value = store.get('voice');
  voiceSel.addEventListener('change', () => store.set('voice', voiceSel.value));

  // ── boot ───────────────────────────────────────────────────────────────
  function status(text, bad) { statusEl.textContent = text; statusEl.className = 'status' + (bad ? ' bad' : ''); }
  (async () => {
    try {
      const [hRes, rRes, vRes] = await Promise.all([fetch(`${API}/api/health`), fetch(`${API}/api/roles`), fetch(`${API}/api/voice/status`)]);
      const hd = await hRes.json();
      voiceStatus = await vRes.json().catch(() => voiceStatus);
      mic.setAttribute('aria-disabled', String(!voiceStatus.ready));
      mic.title = voiceStatus.ready ? 'Talk to GCC Publyq (Agora voice, Murf Falcon 2.1)' : `Voice not configured: ${voiceStatus.missing.join(', ')}`;
      roles = (await rRes.json()).roles;
      status(hd.ok ? `${hd.complaints} records · ${voiceStatus.llm ? 'Claude on' : 'no LLM key'} · Telegram ${voiceStatus.telegram ? 'on' : 'off'}` : 'database down', !hd.ok);
    } catch (_) { status('offline', true); }
    if (roleId && roleOf(roleId)) setRole(roleId); else roleId = null;
    if (!(convId && await loadConversation(convId).catch(() => false))) { convId = null; roleId ? showHello() : showRolePicker(); }
  })();
})();
