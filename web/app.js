/* GCC Publyq web client — vanilla JS, no build step.
 * Voice first: one big mic. A call hears English, Tamil and Hindi; the language the person speaks becomes the
 * language of the answer, the voice and this screen. Typing works the same way.
 * Signed in with Telegram (members of the Ward 173 group); the session is an HttpOnly cookie the page never sees.
 * Role first: the person picks (or says) which GCC post they are, saved to their account; answers are scoped to it.
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
  let me = null;                 // the signed-in person {id, name, role_id, is_admin}
  let convId = store.get('conv') || null;
  let roleId = null;
  let roles = [];
  const seenTurns = new Set();   // turn IDs already on screen (streamed or loaded)
  let voiceStatus = { ready: false, missing: [] };
  let health = null;

  // ── the screen speaks the conversation's language (set by what the person last said) ──
  const LANGS = ['en', 'ta', 'hi'];
  const S = {
    en: { signIn: 'Sign in', signInSub: 'Use the Telegram account you use in the Ward 173 group.', notSetUp: "Sign-in isn't set up yet.", signOut: 'Sign out', signInFailed: "Couldn't sign in.",
      tap: 'Tap and talk', orAsk: 'Or ask', type: 'Or type here…', who: 'Who are you?', whoSub: 'Pick your post. Answers follow your responsibilities.',
      field: 'In the field', lead: 'Leadership', open: (n) => `${n} open`, safety: (n) => `${n} safety`,
      desk: (n, s) => n ? `${n} open on your desk${s ? `, ${s} safety risk` : ''}.` : 'Nothing open on your desk.',
      connecting: 'Connecting…', listening: 'Listening. Speak in English, Tamil or Hindi.', switching: 'Switching to English…', end: 'End',
      started: 'Call started', ended: 'Call ended', failed: "Couldn't start the call. You can still type.", voiceOff: 'Voice is not available right now. You can type.',
      offline: "Can't reach the server.", footer: (n) => `${n} complaints · Ward 173 (Adyar), Chennai`, history: 'Conversations', none: 'No conversations yet.',
      loading: 'Loading…', resumed: 'Continued from earlier', waiting: 'Waiting for your confirmation', recorded: 'Recorded', confirm: 'Confirm', cancel: 'Cancel',
      savedHere: 'Saved here and shared on Telegram, not on the GCC portal.', noServer: "I couldn't reach the server. Check your connection and try again." },
    ta: { signIn: 'உள்நுழையுங்கள்', signInSub: 'வார்டு 173 குழுவில் உள்ள உங்கள் Telegram கணக்கைப் பயன்படுத்துங்கள்.', notSetUp: "உள்நுழைவு இன்னும் அமைக்கப்படவில்லை.", signOut: 'வெளியேறு', signInFailed: "உள்நுழைய முடியவில்லை.",
      tap: 'தட்டிப் பேசுங்கள்', orAsk: 'அல்லது கேளுங்கள்', type: 'அல்லது இங்கே எழுதுங்கள்…', who: 'நீங்கள் யார்?', whoSub: 'உங்கள் பதவியைத் தேர்ந்தெடுங்கள்.',
      field: 'களப் பணி', lead: 'தலைமை', open: (n) => `${n} நிலுவை`, safety: (n) => `${n} அபாயம்`,
      desk: (n, s) => n ? `உங்களிடம் ${n} புகார்கள் நிலுவையில்${s ? `, ${s} பாதுகாப்பு அபாயம்` : ''}.` : 'நிலுவையில் புகார்கள் இல்லை.',
      connecting: 'இணைக்கிறது…', listening: 'கேட்கிறேன். தமிழ், ஆங்கிலம் அல்லது இந்தியில் பேசுங்கள்.', switching: 'தமிழுக்கு மாறுகிறது…', end: 'முடி',
      started: 'அழைப்பு தொடங்கியது', ended: 'அழைப்பு முடிந்தது', failed: 'அழைப்பைத் தொடங்க முடியவில்லை. எழுதிக் கேட்கலாம்.', voiceOff: 'இப்போது குரல் வசதி இல்லை. எழுதிக் கேட்கலாம்.',
      offline: 'சர்வருடன் இணைக்க முடியவில்லை.', footer: (n) => `${n} புகார்கள் · வார்டு 173 (அடையாறு), சென்னை`, history: 'உரையாடல்கள்', none: 'உரையாடல்கள் இல்லை.',
      loading: 'ஏற்றுகிறது…', resumed: 'முன்பு விட்ட இடத்திலிருந்து', waiting: 'உங்கள் உறுதிக்குக் காத்திருக்கிறது', recorded: 'பதிவு செய்யப்பட்டது', confirm: 'உறுதி செய்', cancel: 'ரத்து',
      savedHere: 'இங்கே பதிவாகி Telegram-ல் பகிரப்படும்; GCC போர்டலில் அல்ல.', noServer: 'சர்வருடன் இணைக்க முடியவில்லை. இணைப்பைச் சரிபார்த்து மீண்டும் முயற்சிக்கவும்.' },
    hi: { signIn: 'साइन इन करें', signInSub: 'वार्ड 173 ग्रुप वाले अपने Telegram अकाउंट से साइन इन करें।', notSetUp: "साइन-इन अभी सेट नहीं है।", signOut: 'साइन आउट', signInFailed: "साइन इन नहीं हो पाया।",
      tap: 'दबाएँ और बोलें', orAsk: 'या पूछें', type: 'या यहाँ लिखें…', who: 'आप कौन हैं?', whoSub: 'अपना पद चुनिए।',
      field: 'फ़ील्ड', lead: 'नेतृत्व', open: (n) => `${n} खुली`, safety: (n) => `${n} जोखिम`,
      desk: (n, s) => n ? `आपके पास ${n} शिकायतें खुली हैं${s ? `, ${s} सुरक्षा जोखिम` : ''}।` : 'कोई शिकायत खुली नहीं है।',
      connecting: 'जुड़ रहा है…', listening: 'सुन रही हूँ। हिंदी, अंग्रेज़ी या तमिल में बोलिए।', switching: 'हिंदी में बदल रही हूँ…', end: 'बंद करें',
      started: 'कॉल शुरू', ended: 'कॉल खत्म', failed: 'कॉल शुरू नहीं हो पाई। आप लिखकर पूछ सकते हैं।', voiceOff: 'अभी आवाज़ उपलब्ध नहीं है। आप लिखकर पूछ सकते हैं।',
      offline: 'सर्वर से नहीं जुड़ पा रहे।', footer: (n) => `${n} शिकायतें · वार्ड 173 (अडयार), चेन्नई`, history: 'बातचीत', none: 'अभी कोई बातचीत नहीं।',
      loading: 'लोड हो रहा है…', resumed: 'पिछली बातचीत से जारी', waiting: 'आपकी पुष्टि का इंतज़ार', recorded: 'दर्ज हो गया', confirm: 'पक्का करें', cancel: 'रद्द करें',
      savedHere: 'यहाँ दर्ज होकर Telegram पर भेजा जाएगा, GCC पोर्टल पर नहीं।', noServer: 'सर्वर से नहीं जुड़ पाए। कनेक्शन देखकर फिर कोशिश कीजिए।' },
  };
  const guess = () => { const n = (navigator.language || '').slice(0, 2); return LANGS.includes(n) ? n : 'en'; };
  let lang = LANGS.includes(store.get('lang')) ? store.get('lang') : guess();
  const t = () => S[lang];
  const roleName = (r) => (lang === 'ta' && r.label_ta) || (lang === 'hi' && r.label_hi) || r.label;

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
  // Deep link into app.publyq.in (built server-side; only https://app.publyq.in/ is accepted)
  const pq = (link, cls = '') => (link && /^https:\/\/app\.publyq\.in\//.test(link.url))
    ? `<a class="pq ${cls}" href="${esc(link.url)}" target="_blank" rel="noopener">${esc(link.label || 'Open in Publyq')}${link.truncated ? ' (first 100)' : ''} ↗</a>` : '';
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
        <div class="stats"><span><b>${q.total}</b> open</span><span><b>${s.open_safety ?? 0}</b> safety</span><span><b>${s.open_untouched ?? 0}</b> untouched</span></div>
        ${q.items.length ? `<ul class="rows">${rows(q.items)}</ul>` : '<p class="note">Nothing open in your scope in the GCC records.</p>'}
        ${q.total > q.items.length ? `<p class="note">Showing ${q.items.length} of ${q.total}, most urgent first.</p>` : ''}
        ${pq(q.publyq)}</div>`;
    },
    patterns(p) {
      if (!p.total) return `<div class="card"><h3>Patterns</h3><p class="note">${esc(p.say)}</p></div>`;
      const max = Math.max(...p.issues.map((i) => i.n), 1);
      const spots = p.spots.map((s) => `<li><b>${esc(s.issue)}</b> · ${esc(s.street)}
        <div class="s">${s.n} complaints${s.open ? ` · <span class="tag warn">${s.open} open</span>` : ''}${s.came_back ? ` · <span class="tag crit">${s.came_back} came back after fix</span>` : ''} · ${esc(s.first_seen)} → ${esc(s.last_seen)}</div>
        <div class="act" style="margin-top:6px">${(s.ids || []).slice(0, 3).map((id) => `<button data-say="Open ${esc(id)}">${esc(id)}</button>`).join('')}${pq(s.publyq ? { ...s.publyq, label: `All ${s.n} in Publyq` } : null, 'inline')}</div></li>`).join('');
      const bars = p.issues.map((i) => `<${i.publyq ? `a href="${esc(i.publyq.url)}" target="_blank" rel="noopener" title="${esc(i.publyq.label)}"` : 'div'} class="bar"><span>${esc(i.issue)}${i.publyq ? ' ↗' : ''}</span><i style="width:${Math.max(4, (i.n / max) * 100)}%"></i><em>${i.n}${i.open ? ` · ${i.open} open` : ''} · ${i.last_90d} in 90 d</em></${i.publyq ? 'a' : 'div'}>`).join('');
      return `<div class="card"><h3>Recurring patterns · ${esc(p.role)}</h3>
        <div class="stats"><span><b>${p.total}</b> complaints in 2026</span><span><b>${p.came_back}</b> came back after fix</span>
        ${p.median_days_to_close != null ? `<span><b>${p.median_days_to_close.toFixed(1)}</b> median days to close</span>` : ''}</div>
        ${spots ? `<h4>Repeat spots</h4><ul class="spots">${spots}</ul>` : '<p class="note">No street has repeated complaints in your scope.</p>'}
        <h4>Top issues</h4><div class="bars">${bars}</div>
        ${pq((p.publyq_links || {}).map)}</div>`;
    },
    complaint_list(c) {
      return `<div class="card"><h3>${c.total} complaint${c.total === 1 ? '' : 's'}${c.total > c.items.length ? ` · showing ${c.items.length}` : ''}</h3><ul class="rows">${rows(c.items) || '<li class="note">None</li>'}</ul>${pq(c.publyq)}</div>`;
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
` : ''}
          <ol class="steps">${s.diagnose.map((d) => `<li>${esc(d)}</li>`).join('')}</ol>
          <p style="font-size:13.5px;margin-top:6px"><b>Fix:</b> ${esc(s.fix || '—')}${s.not_ours_if && s.not_ours_if !== '-' ? ` <br><b>Not GCC's if:</b> ${esc(s.not_ours_if)}` : ''}</p>` : ''}
        ${media ? `<h4>Evidence (${c.media.length})</h4><div class="photos">${media}</div>` : ''}
        ${events ? `<h4>Officer actions on GCC portal</h4><ul class="timeline">${events}</ul>` : '<h4>Officer actions</h4><p class="note">None recorded on the GCC portal.</p>'}
        ${actions ? `<h4>Logged here</h4><ul class="timeline">${actions}</ul>` : ''}
        <div class="act">
          ${c.status_group === 'open' ? `<button class="primary" data-say="Escalate ${esc(c.id)}">Escalate</button>
          <button data-say="Remind me about ${esc(c.id)} in 2 days">Follow up in 2 days</button>` : ''}
          <button data-say="Report for ${esc(c.id)}">Report</button>
          ${pq(c.publyq, 'inline')}
          ${c.source_url ? `<a class="note" href="${esc(c.source_url)}" target="_blank" rel="noopener" style="align-self:center">GCC portal ↗</a>` : ''}
        </div>
        ${c.data_as_of ? `<p class="note">GCC data · ${esc(fmtDate(c.data_as_of).split(',')[0])}</p>` : ''}
      </div>`;
    },
    action(a) {
      const pending = a.status === 'proposed';
      return `<div class="card action ${esc(a.status)}" data-action-id="${a.id}">
        <h3>${pending ? t().waiting : a.status === 'confirmed' ? t().recorded : esc(a.status)} · #${a.id}</h3>
        <dl class="kv"><dt>Action</dt><dd>${esc(a.kind.replace('_', ' '))} · ${esc(a.complaint_id)}</dd>
          ${a.target_post ? `<dt>To</dt><dd>${esc(a.target_post)}</dd>` : ''}
          ${a.due_at ? `<dt>Due</dt><dd>${esc(fmtDate(a.due_at))}</dd>` : ''}
          ${a.note ? `<dt>Note</dt><dd>${esc(a.note)}</dd>` : ''}
          ${a.follow_up ? `<dt>Follow-up</dt><dd>#${a.follow_up.id} · ${esc(fmtDate(a.follow_up.due_at))}</dd>` : ''}
          ${(a.payload || {}).to_status ? `<dt>Requested status</dt><dd>${esc(a.payload.to_status.replace('_', ' '))}</dd>` : ''}
          ${a.notify && !a.communication && a.status === 'proposed' ? '<dt>Share</dt><dd>Ward 173 Telegram group, after you confirm</dd>' : ''}
          ${a.communication ? `<dt>Telegram</dt><dd><span class="tag ${a.communication.status === 'sent' ? 'pos' : 'warn'}">${esc(a.communication.status)}</span>${a.communication.error ? ' ' + esc(a.communication.error) : ''}</dd>` : ''}</dl>
        ${pending ? `<div class="act"><button class="primary" data-say="Confirm #${a.id}">${esc(t().confirm)}</button><button data-say="Cancel #${a.id}">${esc(t().cancel)}</button></div>` : ''}
        ${pending ? `<p class="note">${esc(t().savedHere)}</p>` : ''}
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
        <h4>Posts</h4><ul class="rows">${li}</ul></div>`;
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
</div>`;
    },
    triage(t) {
      if (!t.complaint_type) return `<div class="card"><h3>Triage</h3><p>${esc(t.say)}</p></div>`;
      const d = (t.possible_duplicates || []);
      return `<div class="card"><h3>Triage · ${esc(t.complaint_type)}</h3>
        <dl class="kv"><dt>Priority</dt><dd>${esc(t.priority || '—')}</dd><dt>Owner</dt><dd>${t.owner ? esc(t.owner.post) : '—'}</dd>
        <dt>Locality</dt><dd>${esc(t.locality || 'not detected')}</dd><dt>Confidence</dt><dd>${Math.round((t.confidence || 0) * 100)}%${t.confident ? '' : ' · please check'}</dd>
        ${t.safety_signal ? `<dt>Safety signal</dt><dd><span class="tag crit">${esc(t.safety_signal)}</span></dd>` : ''}</dl>
        ${d.length ? `<h4>Already reported? Open complaints at this spot</h4><ul class="rows">${rows(d)}</ul>${pq(t.publyq)}` : ''}
        ${t.sop ? `<h4>First check</h4><p>${esc(t.sop.diagnose[0] || '')}</p>` : ''}</div>`;
    },
    report(r) { return `<div class="card md">${md(r.markdown)}</div>`; },
    table(q) {
      let data = []; try { data = JSON.parse(q.rows_json || '[]'); } catch (_) {}
      const cols = q.columns || [];
      const head = cols.map((c) => `<th>${esc(c)}</th>`).join('');
      const body = data.slice(0, 20).map((r) => `<tr>${cols.map((c) => `<td>${esc(typeof r[c] === 'object' && r[c] !== null ? JSON.stringify(r[c]) : r[c])}</td>`).join('')}</tr>`).join('');
      return `<div class="card"><div class="tablewrap"><table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
        ${q.row_count > 20 ? `<p class="note">First 20 of ${q.row_count}</p>` : ''}${pq(q.publyq)}</div>`;
    },
    communications(c) {
      const li = c.items.map((m) => `<li><span class="tag ${m.status === 'sent' ? 'pos' : 'warn'}">${esc(m.status)}</span>
        ${esc(m.recipient)} · ${esc(fmtDate(m.created_at))}${m.complaint_id ? ' · ' + esc(m.complaint_id) : ''}${m.error ? ` · <span class="note">${esc(m.error)}</span>` : ''}</li>`).join('');
      return `<div class="card"><h3>Telegram messages</h3><ul class="timeline">${li || '<li class="note">None sent yet.</li>'}</ul></div>`;
    },
    summary(s) {
      const L = s.publyq_links || {};
      const groups = (s.open_by_issue || []).map((g) => `<li>${pq(g.publyq ? { ...g.publyq, label: `${g.key} · ${g.n} open` } : null) || `${esc(g.key)} · ${g.n}`}</li>`).join('');
      return `<div class="card"><h3>Ward ${s.ward_no}</h3><p>${s.open} open · ${s.open_overdue} overdue · ${s.open_safety} safety risk</p>
        <div class="act">${pq(L.safety, 'inline')}${pq(L.overdue, 'inline')}${pq(L.map, 'inline')}${pq(L.accountability, 'inline')}</div>
        ${groups ? `<h4>Open by issue group</h4><ul class="linklist">${groups}</ul>` : ''}</div>`;
    },
    publyq(l) { return `<div class="card">${pq(l, 'big')}</div>`; },
  };
  const card = (c) => (R[c.type] ? h(R[c.type](c) || '<span hidden></span>') : null);

  // ── role selection + the voice-first home ──────────────────────────
  const CHIPS = {
    field: {
      en: [["What's on my plate?", "What's on my plate?"], ['Recurring problems', 'What are the recurring patterns?'], ['My follow-ups', 'What follow-ups do I have?']],
      ta: [['இன்று என் வேலை', 'இன்று என் வேலை என்ன?'], ['மீண்டும் வரும் பிரச்சினைகள்', 'மீண்டும் மீண்டும் வரும் பிரச்சினைகள் என்ன?'], ['என் பின்தொடர்வுகள்', 'நான் பின்தொடர வேண்டியவை என்ன?']],
      hi: [['आज का काम', 'आज मेरा काम क्या है?'], ['बार-बार की समस्याएँ', 'बार-बार आने वाली समस्याएँ कौन सी हैं?'], ['मेरे फ़ॉलो-अप', 'मेरे कौन से फ़ॉलो-अप बाकी हैं?']],
    },
    leadership: {
      en: [['Brief me', 'Brief me'], ['Where are we stuck?', 'What is high priority for my team?'], ['Follow-ups due', 'What follow-ups are due?']],
      ta: [['சுருக்கம்', 'இன்றைய சுருக்கம் சொல்லுங்கள்'], ['எங்கே தேங்கியுள்ளது?', 'என் குழுவுக்கு எது அவசரம்?'], ['பின்தொடர வேண்டியவை', 'இன்று பின்தொடர வேண்டியவை என்ன?']],
      hi: [['सारांश', 'आज का सारांश बताइए'], ['कहाँ अटके हैं?', 'मेरी टीम के लिए सबसे ज़रूरी क्या है?'], ['बाकी फ़ॉलो-अप', 'कौन से फ़ॉलो-अप बाकी हैं?']],
    },
  };
  CHIPS.ward = CHIPS.leadership;
  const MIC_SVG = '<svg viewBox="0 0 24 24" width="44" height="44" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';

  function setRole(id, { announce, persist } = {}) {
    roleId = id;
    if (persist && me && me.role_id !== id) { me.role_id = id; api('/api/auth/role', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role_id: id }) }).catch(() => {}); }
    const r = roleOf(id);
    pill.hidden = !r;
    if (r) { pill.textContent = roleName(r); pill.title = r.post; }
    if (announce && r) send(`I am the ${r.label}`);
  }

  function showRolePicker() {
    feed.innerHTML = '';
    const group = (kind) => roles.filter((r) => r.kind === kind).map((r) => `<button class="role-card${r.id === roleId ? ' on' : ''}" data-role="${esc(r.id)}">
      <b>${esc(roleName(r))}</b><span class="n ${r.open_safety ? 'crit' : ''}">${esc(t().open(r.open))}${r.open_safety ? ` · ${esc(t().safety(r.open_safety))}` : ''}</span></button>`).join('');
    feed.append(h(`<div class="pick">
      <h1>${esc(t().who)}</h1><p>${esc(t().whoSub)}</p>
      <h2>${esc(t().field)}</h2><div class="roles">${group('field')}</div>
      <h2>${esc(t().lead)}</h2><div class="roles">${group('leadership')}${group('ward')}</div>
    </div>`));
  }

  function showHello() {
    const r = roleOf(roleId);
    if (!r) return showRolePicker();
    feed.innerHTML = '';
    const chips = ((CHIPS[r.kind] || CHIPS.field)[lang]).map(([l, q]) => `<button data-say="${esc(q)}">${esc(l)}</button>`).join('');
    feed.append(h(`<div class="hello">
      <h1>${esc(t().desk(r.open, r.open_safety))}</h1>
      <button class="talk" data-act="call" ${voiceStatus.ready ? '' : 'aria-disabled="true"'}>${MIC_SVG}</button>
      <p class="talk-label"><b>${esc(t().tap)}</b><span>English · தமிழ் · हिंदी</span></p>
      <p class="or">${esc(t().orAsk)}</p><div class="chips">${chips}</div></div>`));
  }

  // switch the screen's language; re-draw the home or picker if one is showing
  function setLang(l) {
    if (!LANGS.includes(l)) return;
    const changed = l !== lang;
    lang = l; store.set('lang', l);
    document.documentElement.lang = l;
    input.placeholder = t().type;
    $('#endBtn').textContent = t().end;
    $('#historyTitle').textContent = t().history;
    const r = roleOf(roleId); if (r) pill.textContent = roleName(r);
    renderStatus();
    if (changed && $('.hello', feed)) showHello();
    else if (changed && $('.pick', feed)) showRolePicker();
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
    const res = await fetch(`${API}/api/conversations/${encodeURIComponent(id)}`);
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
      $('.meta', el).textContent = fmtDate(t.created_at);
      feed.append(el);
    }
    const last = c.turns[c.turns.length - 1];
    if (last && last.lang) setLang(last.lang);
    if (c.turns.length) feed.append(h(`<p class="divider">${esc(t().resumed)} · ${esc(ago(c.updated_at))}</p>`));
    else showHello();
    scroll(true);
    return true;
  }

  async function openHistory() {
    const sheet = $('#historySheet'), list = $('#historyList');
    list.innerHTML = `<li class="note">${esc(t().loading)}</li>`;
    sheet.hidden = false;
    renderAccount();
    $('[data-act="close"]', sheet).focus();
    try {
      const { items } = await api('/api/conversations');
      list.innerHTML = items.length ? items.map((c) => `<li class="${c.id === convId ? 'on' : ''}">
        <button class="open" data-conv="${esc(c.id)}"><b>${esc(c.title || 'Conversation')}</b>
        <span>${esc(roleOf(c.role_id) ? roleName(roleOf(c.role_id)) : 'Ward 173')} · ${esc(ago(c.updated_at))}</span></button>
        <button class="del" data-del="${esc(c.id)}" aria-label="Delete conversation">✕</button></li>`).join('') : `<li class="note">${esc(t().none)}</li>`;
    } catch (_) { list.innerHTML = `<li class="note">${esc(t().offline)}</li>`; }
  }
  const closeHistory = () => { $('#historySheet').hidden = true; };
  const renderAccount = () => {
    $('#account').innerHTML = me ? `<span>${esc(me.name)}${me.username ? ` · @${esc(me.username)}` : ''}</span>
      <button class="signout" data-act="signout">${esc(t().signOut)}</button>` : '';
  };

  // ── the turn (queued, never dropped) ───────────────────────────────────
  const queue = [];
  let busy = false;
  const renderQueue = () => { const q = $('#queued'); q.hidden = !queue.length; q.textContent = queue.length ? `⏳ ${queue.map((x) => `“${x}”`).join(', ')}` : ''; };
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
    const sayEl = $('.say', turn), cardsEl = $('.cards', turn);
    let said = '';
    try {
      const res = await fetch(`${API}/api/agent/turn`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, session_id: convId, role_id: roleId, lang }) });
      if (res.status === 401) { busy = false; return showSignIn(); }
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
            if (data.role && data.role !== roleId) setRole(data.role, { persist: true });
            if (data.lang) setLang(data.lang);
          }
        }
        scroll();
      }
    } catch (err) {
      said = t().noServer;
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
    if (r.status === 401 && me) { me = null; showSignIn(); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || d.error || `HTTP ${r.status}`);
    return d;
  };

  // The agent's voice speaks one language. When the person switches, the brain says a short holding line and
  // saves its answer; we restart the agent in the new language and it opens with that answer.
  const agentUid = () => 10000000 + Math.floor(Math.random() * 89999999);
  async function startAgent(l, greetTurnId) {
    const started = await api('/api/voice/start', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel: call.channel, agent_uid: agentUid(), user_uid: call.uid, session_id: convId, role_id: roleId,
        lang: l, ...(greetTurnId ? { greet_turn_id: greetTurnId } : {}) }) });
    call.agentId = started.agent_id; call.lang = l;
    return started;
  }
  const stopAgent = (id) => id && api('/api/voice/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agent_id: id }) }).catch(() => {});

  async function switchLanguage(l, turnId) {
    if (!call || call.switching) return;
    call.switching = true;
    setLang(l); callState(t().switching);
    const old = call.agentId; call.agentId = null;
    await stopAgent(old);
    try { if (call) await startAgent(l, turnId); } catch (err) { if (call) callState(t().failed); }
    if (call) call.switching = false;
  }

  async function startCall() {
    if (call) return;
    if (!voiceStatus.ready) { status(t().voiceOff, true); return; }
    if (!convId) { convId = 'c-' + uid(); store.set('conv', convId); }
    $('.hello', feed)?.remove(); $('.pick', feed)?.remove();
    call = { agentId: null, client: null, mic: null, poll: null, lang, switching: false };
    document.body.classList.add('in-call'); $('#callbar').hidden = false; callState(t().connecting);
    mic.classList.add('live'); mic.setAttribute('aria-pressed', 'true'); mic.setAttribute('aria-label', t().end);
    try {
      await loadScript(RTC_SDK);
      const cfg = await api(`/api/voice/config?session_id=${encodeURIComponent(convId)}`);
      call.channel = cfg.channel; call.uid = cfg.uid;
      const client = window.AgoraRTC.createClient({ mode: 'rtc', codec: 'vp8' });
      call.client = client;
      client.on('user-published', async (user, mediaType) => {
        await client.subscribe(user, mediaType);
        if (mediaType === 'audio') { user.audioTrack.play(); callState(t().listening); }
      });
      await client.join(cfg.app_id, cfg.channel, cfg.token, cfg.uid);
      call.mic = await window.AgoraRTC.createMicrophoneAudioTrack({ AEC: true, ANS: true, AGC: true });
      await client.publish([call.mic]);
      await startAgent(lang);
      call.poll = setInterval(pollTurns, 1500);
      feed.append(h(`<p class="divider">${esc(t().started)}</p>`)); scroll(true);
    } catch (err) {
      feed.append(h(`<p class="divider bad">${esc(t().failed)}</p>`));
      await endCall({ quiet: true });
    }
  }

  async function endCall({ quiet } = {}) {
    const c = call; if (!c) return;
    call = null;
    clearInterval(c.poll);
    document.body.classList.remove('in-call'); $('#callbar').hidden = true;
    mic.classList.remove('live'); mic.setAttribute('aria-pressed', 'false'); mic.setAttribute('aria-label', t().tap);
    try { if (c.mic) { c.mic.stop(); c.mic.close(); } } catch (_) {}
    try { if (c.client) await c.client.leave(); } catch (_) {}
    stopAgent(c.agentId);
    if (!quiet) { feed.append(h(`<p class="divider">${esc(t().ended)}</p>`)); scroll(true); }
  }

  async function pollTurns() {
    if (!call || !convId) return;
    try {
      const c = await api(`/api/conversations/${encodeURIComponent(convId)}`);
      for (const tn of c.turns) {
        if (seenTurns.has(tn.id)) continue;
        seenTurns.add(tn.id);
        const el = turnEl(tn.text);
        el.classList.add('voice');
        $('.say', el).textContent = tn.reply || '';
        for (const cd of tn.cards || []) { const n = card(cd); if (n) $('.cards', el).append(n); }
        feed.append(el);
        if (tn.role_id && tn.role_id !== roleId) setRole(tn.role_id, { persist: true });
        if (call && tn.lang && tn.lang !== call.lang) switchLanguage(tn.lang, tn.id);
        else if (tn.lang) setLang(tn.lang);
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
    if (t.dataset.role) {
      const id = t.dataset.role; setRole(id, { persist: true });
      return feed.querySelector('.turn') && id !== 'ADMIN' ? send(`I am the ${roleOf(id).label}`) : showHello();
    }
    if (t.dataset.conv) { closeHistory(); return loadConversation(t.dataset.conv); }
    if (t.dataset.del) {
      await fetch(`${API}/api/conversations/${encodeURIComponent(t.dataset.del)}`, { method: 'DELETE' });
      if (t.dataset.del === convId) newConversation();
      return openHistory();
    }
    const act = t.dataset.act;
    if (act === 'theme') setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
    else if (act === 'call') call ? endCall() : startCall();
    else if (act === 'endcall') endCall();
    else if (act === 'new') newConversation();
    else if (act === 'history') openHistory();
    else if (act === 'close') closeHistory();
    else if (act === 'roles') showRolePicker();
    else if (act === 'signout') signOut();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeHistory(); });
  // ── theme (as app.publyq.in: light by default, an explicit choice is remembered) ──
  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#070d10' : '#f4f7f8');
    $('.theme-btn').setAttribute('aria-label', theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
    try { localStorage.setItem('gcc-theme', theme); } catch (_) {}
  }
  if (document.documentElement.dataset.theme === 'dark') $('.theme-btn').setAttribute('aria-label', 'Switch to light mode');

  // ── sign-in (Telegram Login Widget; the server checks the signature and ward-group membership) ──
  async function showSignIn() {
    if (call) await endCall({ quiet: true });
    document.body.classList.add('signed-out'); pill.hidden = true;
    feed.innerHTML = '';
    let cfg = {};
    try { cfg = await (await fetch(`${API}/api/auth/config`)).json(); } catch (_) {}
    const box = h(`<div class="signin"><img src="assets/logo.png" alt="" width="56" height="56" />
      <h1>${esc(t().signIn)}</h1><p>${esc(cfg.bot_username ? t().signInSub : t().notSetUp)}</p>
      <div id="tgLogin"></div><p class="err" role="alert"></p></div>`);
    feed.append(box);
    if (!cfg.bot_username) return;
    window.gccTelegramAuth = async (user) => {
      const r = await fetch(`${API}/api/auth/telegram`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(user) });
      if (r.ok) return location.reload();
      const d = await r.json().catch(() => ({}));
      $('.err', box).textContent = d.detail || t().signInFailed;
    };
    const sc = document.createElement('script');
    sc.async = true; sc.src = 'https://telegram.org/js/telegram-widget.js?22';
    Object.assign(sc.dataset, { telegramLogin: cfg.bot_username, size: 'large', radius: '999', onauth: 'gccTelegramAuth(user)', lang });
    $('#tgLogin', box).append(sc);
  }

  async function signOut() {
    if (call) await endCall({ quiet: true });
    await fetch(`${API}/api/auth/logout`, { method: 'POST' }).catch(() => {});
    store.set('conv', null);
    location.reload();
  }

  // ── boot ───────────────────────────────────────────────────────────────
  let statusNote = null;   // a problem worth showing; otherwise the plain ward line
  function status(text, bad) { statusNote = bad ? text : null; renderStatus(); }
  function renderStatus() {
    const txt = statusNote || (health && !health.ok ? t().offline : health ? t().footer(health.complaints) : '');
    statusEl.textContent = txt; statusEl.className = 'hint' + (statusNote || (health && !health.ok) ? ' bad' : '');
  }
  (async () => {
    try {
      const meRes = await fetch(`${API}/api/auth/me`);
      me = meRes.ok ? await meRes.json() : null;
    } catch (_) { me = null; }
    try {
      const [hRes, rRes, vRes] = await Promise.all([fetch(`${API}/api/health`), fetch(`${API}/api/roles`), fetch(`${API}/api/voice/status`)]);
      health = await hRes.json();
      voiceStatus = await vRes.json().catch(() => voiceStatus);
      mic.setAttribute('aria-disabled', String(!voiceStatus.ready));
      roles = (await rRes.json()).roles;
    } catch (_) { health = { ok: false }; }
    setLang(lang);
    if (!me) return showSignIn();                                 // a brief link waits in the URL until sign-in
    document.body.classList.remove('signed-out');
    roleId = me.role_id;
    const link = new URLSearchParams(location.search);
    if (link.has('role') || link.has('ask') || link.has('lang')) {
      history.replaceState(null, '', location.pathname);         // a link is applied once, then forgotten
      if (LANGS.includes(link.get('lang'))) setLang(link.get('lang'));
      if (roleOf(link.get('role'))) setRole(link.get('role'), { persist: true });
      convId = 'c-' + uid(); store.set('conv', convId);           // a fresh conversation for the link
      const ask = link.get('ask') || '';
      const open = ask.startsWith('open:') ? ask.slice(5).toUpperCase() : '';
      if (open && /^20\d{2}-\d{3,}[A-Z]{3}$/.test(open)) return send(`Open ${open}`);
      if (ask === 'brief') return send({ ta: 'இன்றைய காலை சுருக்கம் சொல்லுங்கள்', hi: 'आज का सारांश बताइए', en: 'Brief me' }[lang]);
      return roleId ? showHello() : showRolePicker();
    }
    if (roleId && roleOf(roleId)) setRole(roleId); else roleId = null;
    if (!(convId && await loadConversation(convId).catch(() => false))) { convId = null; store.set('conv', null); roleId ? showHello() : showRolePicker(); }
  })();
})();
