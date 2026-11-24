/* NCAL Cocoa farmer app – renders entirely from the phone's IndexedDB,
 * so every screen works with no mobile network. */
(() => {
  const DEFS = JSON.parse(document.getElementById('step-defs').textContent);
  const CFG = JSON.parse(document.getElementById('app-config').textContent);
  const $view = document.getElementById('view');
  const LABEL = (pairs, v) => (pairs.find((p) => p[0] === v) || [v, v])[1];
  const STATUS_LABEL = (v) => LABEL(CFG.statuses, v);
  const ACTIVE = ['FERMENTING', 'DRYING'];

  let formDirty = false;
  let currentRoute = '';
  const objectUrls = [];

  // ---------- helpers ----------
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const uuid = () => {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  };
  const pad = (n) => String(n).padStart(2, '0');
  const toLocalInput = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const fromLocalInput = (v) => new Date(v).toISOString();
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '–';
  const fmtDay = (iso) => iso ? new Date(iso).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' }) : '–';
  const kg = (v) => (v === null || v === undefined || v === '') ? '–' : `${Number(v).toLocaleString('en-AU', { maximumFractionDigits: 1 })} kg`;
  function dur(hours) {
    const neg = hours < 0; let h = Math.abs(hours);
    const d = Math.floor(h / 24); h -= d * 24;
    const hh = Math.floor(h); const m = Math.round((h - hh) * 60);
    const s = d ? `${d}d ${hh}h` : (hh ? `${hh}h ${m}m` : `${m}m`);
    return neg ? `-${s}` : s;
  }
  const hoursBetween = (a, b) => (new Date(b) - new Date(a)) / 3600000;
  const defsFor = (b) => DEFS[b.drying_method] || DEFS.SOLAR;

  function toast(msg, kind = 'ok') {
    const t = document.getElementById('toast');
    t.textContent = msg; t.className = `toast toast-${kind}`; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 3500);
  }

  function releaseUrls() { while (objectUrls.length) URL.revokeObjectURL(objectUrls.pop()); }
  function photoSrc(p) {
    if (p.blob) { const u = URL.createObjectURL(p.blob); objectUrls.push(u); return u; }
    return p.url || '';
  }

  async function saveRecord(store, rec) {
    rec._dirty = 1;
    rec._rev = (rec._rev || 0) + 1;
    rec.client_updated_at = new Date().toISOString();
    delete rec._error;
    await DB.put(store, rec);
  }

  async function savePhoto(step, kind, blob) {
    await DB.put('photos', {
      id: uuid(), step_id: step.id, batch_id: step.batch_id, step_type: step.step_type,
      kind, taken_at: new Date().toISOString(), blob, url: null, _dirty: 1,
    });
  }

  // Shrink camera photos (often 4–8 MB) to ~200 KB so they fit on the phone and upload on weak signal.
  async function compressImage(file, maxSide = 1280, quality = 0.72) {
    let src;
    try { src = await createImageBitmap(file); } catch (e) {
      src = await new Promise((res, rej) => {
        const img = new Image(); img.onload = () => res(img); img.onerror = rej; img.src = URL.createObjectURL(file);
      });
    }
    const w = src.width; const h = src.height;
    const scale = Math.min(1, maxSide / Math.max(w, h));
    const c = document.createElement('canvas');
    c.width = Math.round(w * scale); c.height = Math.round(h * scale);
    c.getContext('2d').drawImage(src, 0, 0, c.width, c.height);
    return new Promise((res) => c.toBlob((b) => res(b || file), 'image/jpeg', quality));
  }

  function getPosition() {
    return new Promise((resolve) => {
      if (!navigator.geolocation) return resolve(null);
      const timer = setTimeout(() => resolve(null), 8000);
      navigator.geolocation.getCurrentPosition(
        (p) => { clearTimeout(timer); resolve({ lat: +p.coords.latitude.toFixed(6), lng: +p.coords.longitude.toFixed(6) }); },
        () => { clearTimeout(timer); resolve(null); },
        { enableHighAccuracy: false, timeout: 7000, maximumAge: 10 * 60 * 1000 },
      );
    });
  }

  async function loadBatch(id) {
    const b = await DB.get('batches', id);
    if (!b) return null;
    const steps = await DB.byIndex('steps', 'batch_id', id);
    const byType = {};
    steps.forEach((s) => { byType[s.step_type] = s; });
    const photosByStep = {};
    for (const s of steps) photosByStep[s.id] = await DB.byIndex('photos', 'step_id', s.id);
    return { b, steps, byType, photosByStep };
  }

  function currentIndex(b, byType) {
    const defs = defsFor(b);
    for (let i = 0; i < defs.length; i++) {
      const s = byType[defs[i].code];
      if (!s || !s.ended_at) return i;
    }
    return -1;
  }

  function dueInfo(def, step) {
    if (!step || !step.started_at) return null;
    const elapsed = hoursBetween(step.started_at, new Date());
    if (!def.hours) return { elapsed, left: null, overdue: elapsed > CFG.tolerance };
    const left = def.hours - elapsed;
    return { elapsed, left, overdue: left < -CFG.tolerance, ready: left <= CFG.tolerance };
  }

  function photoInput(name, label = 'Take photo') {
    return `
      <div class="photo-field" data-photo="${name}">
        <label class="btn btn-photo">
          <svg><use href="#i-camera"/></svg><span>${esc(label)}</span>
          <input type="file" accept="image/*" capture="environment" hidden>
        </label>
        <img class="photo-preview" alt="" hidden>
      </div>`;
  }

  function wirePhoto(root, holder) {
    root.querySelectorAll('.photo-field').forEach((pf) => {
      const input = pf.querySelector('input'); const img = pf.querySelector('img'); const span = pf.querySelector('span');
      input.addEventListener('change', async () => {
        const f = input.files && input.files[0];
        if (!f) return;
        span.textContent = 'Saving photo…';
        const blob = await compressImage(f);
        holder[pf.dataset.photo] = blob;
        const u = URL.createObjectURL(blob); objectUrls.push(u);
        img.src = u; img.hidden = false; span.textContent = 'Retake photo';
        formDirty = true;
      });
    });
  }

  // ---------- header sync pill ----------
  const pill = document.getElementById('sync-pill');
  pill.addEventListener('click', () => { Sync.run(); location.hash = '#/me'; });
  Sync.on((s) => {
    const span = pill.querySelector('span');
    pill.className = `sync-pill sp-${s.status}`;
    const waiting = s.pending ? ` · ${s.pending} waiting` : '';
    const txt = {
      offline: `Offline${waiting}`,
      syncing: 'Sending…',
      ok: s.pending ? `${s.pending} waiting` : 'All sent',
      idle: s.pending ? `${s.pending} waiting` : 'Ready',
      error: `No connection${waiting}`,
      auth: 'Log in again',
    }[s.status] || s.status;
    span.textContent = txt;
  });

  // ---------- views ----------
  async function viewHome() {
    const farmer = await DB.getMeta('farmer');
    const batches = (await DB.all('batches')).sort((a, b) => (b.started_at || '').localeCompare(a.started_at || ''));
    const active = batches.filter((b) => ACTIVE.includes(b.status));
    const year = String(new Date().getFullYear());
    const thisYear = batches.filter((b) => (b.started_at || '').startsWith(year));
    const wet = thisYear.reduce((s, b) => s + (+b.wet_weight_kg || 0), 0);
    const packed = thisYear.filter((b) => b.dry_weight_kg);
    const dry = packed.reduce((s, b) => s + (+b.dry_weight_kg || 0), 0);
    const wetPacked = packed.reduce((s, b) => s + (+b.wet_weight_kg || 0), 0);
    const bags = batches.flatMap((b) => b.bags || []).filter((x) => x.test);
    const premium = bags.filter((x) => x.test.premium).length;

    const cards = [];
    for (const b of active) {
      const d = await loadBatch(b.id);
      const i = currentIndex(b, d.byType);
      const def = i >= 0 ? defsFor(b)[i] : null;
      const step = def ? d.byType[def.code] : null;
      const due = def ? dueInfo(def, step) : null;
      let when = '';
      if (due && def.hours) {
        when = due.left > 0 ? `Ready in ${dur(due.left)}` : (due.overdue ? `Late by ${dur(-due.left)}` : 'Ready now');
      } else if (def) {
        when = 'Do it now';
      }
      const cls = due && due.overdue ? 'due-late' : (due && due.ready ? 'due-now' : '');
      cards.push(`
        <a class="batch-card ${cls}" href="#/batch/${b.id}">
          <div class="bc-top"><strong class="mono">${esc(b.code)}</strong><span class="chip st-${b.status.toLowerCase()}">${esc(STATUS_LABEL(b.status))}</span></div>
          <div class="bc-step">${def ? esc(def.label) : ''}</div>
          <div class="bc-meta"><svg><use href="#i-clock"/></svg>${esc(when)}</div>
          <div class="progress"><i style="width:${Math.round(((i < 0 ? defsFor(b).length : i) / defsFor(b).length) * 100)}%"></i></div>
        </a>`);
    }

    $view.innerHTML = `
      <section class="hello">
        <h1>${farmer ? `Hello, ${esc(farmer.first_name)}` : 'Welcome'}</h1>
        ${farmer ? `<p class="muted">${esc(farmer.member_number)} · ${esc(farmer.cooperative)}${farmer.cluster ? ' · ' + esc(farmer.cluster) : ''}</p>`
          : '<p class="muted">Connect to network once so the app can load your farmer details.</p>'}
      </section>
      <a class="btn btn-primary btn-block btn-lg" href="#/new"><svg><use href="#i-plus"/></svg>Start new batch</a>
      <h2 class="section-title">Batches in process</h2>
      ${cards.length ? `<div class="batch-cards">${cards.join('')}</div>` : '<p class="empty">No batches fermenting or drying. Start a new batch after harvest.</p>'}
      <h2 class="section-title">This year</h2>
      <div class="kpis kpis-app">
        <div class="kpi"><span>Batches</span><strong>${thisYear.length}</strong></div>
        <div class="kpi"><span>Wet beans</span><strong>${kg(wet)}</strong></div>
        <div class="kpi"><span>Dry beans</span><strong>${kg(dry)}</strong><small>${wetPacked ? `yield ${(dry / wetPacked * 100).toFixed(1)}%` : ''}</small></div>
        <div class="kpi kpi-pod"><span>Premium bags</span><strong>${bags.length ? `${premium}/${bags.length}` : '–'}</strong></div>
      </div>`;
  }

  async function viewNew() {
    const farmer = await DB.getMeta('farmer');
    const holder = {};
    const radios = (name, pairs, def) => pairs.map(([v, l]) => `
      <label class="choice"><input type="radio" name="${name}" value="${v}" ${v === def ? 'checked' : ''}><span>${esc(l)}</span></label>`).join('');

    $view.innerHTML = `
      <h1>New batch</h1>
      <p class="muted">Fill this in when you put the wet beans in to ferment.</p>
      <form id="new-form" class="stack" novalidate>
        <label class="field"><span>Weight of wet beans (kg)</span>
          <input name="wet" type="number" inputmode="decimal" step="0.1" min="0.1" required></label>
        <fieldset class="field"><legend>Where are the wet beans from?</legend>
          <div class="choices">${radios('source', CFG.sources, 'OWN')}</div></fieldset>
        <label class="field" id="detail-field" hidden><span id="detail-label">Bought from (name)</span>
          <input name="detail" maxlength="200"></label>
        <fieldset class="field"><legend>Who is fermenting?</legend>
          <div class="choices">${radios('fermented_by', CFG.fermented_by, 'INDIVIDUAL')}</div></fieldset>
        <fieldset class="field"><legend>Drying method</legend>
          <div class="choices">${radios('drying', CFG.drying, 'SOLAR')}</div></fieldset>
        <label class="field"><span>Start date and time</span>
          <input name="start" type="datetime-local" value="${toLocalInput(new Date())}" required></label>
        <div class="field"><span>Photo of the wet beans in the box</span>${photoInput('start', 'Take photo')}</div>
        <label class="field"><span>Notes (optional)</span><textarea name="notes" rows="2"></textarea></label>
        <p class="form-error" id="form-error" hidden></p>
        <button class="btn btn-primary btn-block btn-lg" type="submit">Start fermentation</button>
      </form>`;

    const form = document.getElementById('new-form');
    wirePhoto(form, holder);
    form.addEventListener('input', () => { formDirty = true; });
    const detailField = document.getElementById('detail-field');
    const detailLabel = document.getElementById('detail-label');
    form.addEventListener('change', () => {
      const src = form.source.value;
      detailField.hidden = src === 'OWN';
      detailLabel.textContent = src === 'PURCHASED' ? 'Bought from (name of seller)' : 'Members who contributed beans';
    });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = document.getElementById('form-error');
      const wet = parseFloat(form.wet.value);
      const showErr = (m) => { err.textContent = m; err.hidden = false; err.scrollIntoView({ block: 'center' }); };
      if (!(wet > 0)) return showErr('Enter the weight of the wet beans.');
      if (!form.start.value) return showErr('Enter the start date and time.');
      if (new Date(form.start.value) > new Date(Date.now() + 10 * 60000)) return showErr('Start time cannot be in the future.');
      if (!holder.start) return showErr('Take a photo of the wet beans. Photos are proof for the buyer.');

      const btn = form.querySelector('button[type=submit]'); btn.disabled = true; btn.textContent = 'Saving…';
      const started = fromLocalInput(form.start.value);
      const d = new Date(started);
      const member = (farmer && farmer.member_number ? farmer.member_number : 'NEW').replace(/[^A-Za-z0-9]/g, '').toUpperCase();
      const id = uuid();
      const code = `${member}-${String(d.getFullYear()).slice(2)}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${id.slice(0, 4).toUpperCase()}`;
      const batch = {
        id, code, bean_source: form.source.value, source_detail: form.detail.value.trim(),
        fermented_by: form.fermented_by.value, drying_method: form.drying.value,
        wet_weight_kg: wet, dry_weight_kg: null, bag_count: null, status: 'FERMENTING',
        started_at: started, completed_at: null, notes: form.notes.value.trim(), bags: [],
      };
      await saveRecord('batches', batch);
      const gps = await getPosition();
      const first = defsFor(batch)[0];
      const step = {
        id: uuid(), batch_id: id, step_type: first.code, sequence: 0, started_at: started, ended_at: null,
        weight_start_kg: wet, weight_end_kg: null, latitude: gps ? gps.lat : null, longitude: gps ? gps.lng : null, notes: '',
      };
      await saveRecord('steps', step);
      await savePhoto(step, 'START', holder.start);
      formDirty = false;
      Sync.soon();
      toast('Batch saved on your phone');
      location.hash = `#/batch/${id}`;
    });
  }

  function stepFacts(def, s) {
    const rows = [];
    if (s.started_at && def.hours) rows.push(['Start', fmtDate(s.started_at)]);
    if (s.ended_at) rows.push([def.hours ? 'End' : 'Done', fmtDate(s.ended_at)]);
    if (s.started_at && s.ended_at && def.hours) rows.push(['Time', dur(hoursBetween(s.started_at, s.ended_at))]);
    if (s.weight_start_kg != null && def.kind !== 'turn' && def.kind !== 'pack') rows.push(['Weight in', kg(s.weight_start_kg)]);
    if (s.weight_end_kg != null) rows.push([def.kind === 'pack' ? 'Dry weight' : 'Weight out', kg(s.weight_end_kg)]);
    return `<dl class="facts">${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>`;
  }

  function finishForm(def, step, b) {
    const due = dueInfo(def, step);
    let notice = '';
    if (due && def.hours) {
      if (due.left > CFG.tolerance) {
        notice = `<div class="notice notice-warn">Only ${dur(due.elapsed)} of ${def.hours} hours done. The buyer needs the full time – wait until <strong>${fmtDate(new Date(new Date(step.started_at).getTime() + def.hours * 3600000).toISOString())}</strong>.</div>`;
      } else if (due.overdue) {
        notice = `<div class="notice notice-bad">This step is late by ${dur(-due.left)}. Record it now.</div>`;
      } else {
        notice = '<div class="notice notice-ok">Ready – record this step now.</div>';
      }
    }
    const weightField = def.kind === 'pack'
      ? `<label class="field"><span>Total dry weight (kg)</span><input name="weight" type="number" inputmode="decimal" step="0.1" min="0.1" required></label>
         <label class="field"><span>Number of jute bags</span><input name="bags" type="number" inputmode="numeric" min="1" max="60" required></label>`
      : `<label class="field"><span>${def.kind === 'turn' ? 'Weight (kg, optional)' : 'Weight of beans now (kg)'}</span>
          <input name="weight" type="number" inputmode="decimal" step="0.1" min="0.1" ${def.kind === 'turn' ? '' : 'required'}
          placeholder="${step && step.weight_start_kg ? 'was ' + step.weight_start_kg + ' kg' : ''}"></label>`;
    const action = { ferment: 'Finish fermentation', turn: 'Beans turned', dry: 'Finish drying day', pack: 'Beans packed' }[def.kind] || 'Save';
    return `
      ${notice}
      <form class="stack step-form" id="step-form" novalidate>
        <label class="field"><span>${def.kind === 'turn' ? 'Time of turning' : 'Date and time now'}</span>
          <input name="when" type="datetime-local" value="${toLocalInput(new Date())}" required></label>
        ${weightField}
        <div class="field"><span>Photo</span>${photoInput('end', def.kind === 'pack' ? 'Photo of the bags' : 'Take photo')}</div>
        <label class="field"><span>Notes (optional)</span><textarea name="notes" rows="2"></textarea></label>
        <p class="form-error" id="form-error" hidden></p>
        <button class="btn btn-primary btn-block btn-lg" type="submit"><svg><use href="#i-check"/></svg>${action}</button>
      </form>`;
  }

  async function viewBatch(id) {
    const d = await loadBatch(id);
    if (!d) { $view.innerHTML = '<p class="empty">Batch not found on this phone.</p>'; return; }
    const { b, byType, photosByStep } = d;
    const defs = defsFor(b);
    const ci = currentIndex(b, byType);
    const done = ci < 0 ? defs.length : ci;
    const locked = !ACTIVE.includes(b.status);
    const holder = {};

    const items = defs.map((def, i) => {
      const s = byType[def.code];
      const photos = s ? (photosByStep[s.id] || []) : [];
      const state = s && s.ended_at ? 'ok' : (i === ci ? 'current' : 'pending');
      const thumbs = photos.length ? `<div class="photo-row">${photos.map((p) => `<img src="${esc(photoSrc(p))}" alt="" loading="lazy">`).join('')}</div>` : '';
      let body = '';
      if (state === 'ok') body = stepFacts(def, s) + thumbs + (s.notes ? `<p class="small">${esc(s.notes)}</p>` : '');
      else if (state === 'current' && !locked) {
        const due = dueInfo(def, s);
        body = `<p class="tip">${esc(def.tip)}</p>
          ${s && s.started_at ? `<p class="small muted">Started ${esc(fmtDate(s.started_at))}${due && def.hours ? ` · ${dur(due.elapsed)} so far` : ''}</p>` : ''}
          ${thumbs}${finishForm(def, s, b)}`;
      } else body = def.hours ? `<span class="muted small">${def.hours} hours</span>` : '';
      return `
        <li class="tl-item tl-${state}" ${state === 'current' ? 'id="current-step"' : ''}>
          <span class="tl-dot">${state === 'ok' ? '<svg><use href="#i-check"/></svg>' : i + 1}</span>
          <div class="tl-body"><div class="tl-head"><strong>${esc(def.label)}</strong></div>${body}</div>
        </li>`;
    }).join('');

    const bags = b.bags || [];
    const depot = bags.length ? `
      <section class="card">
        <h2>Depot results</h2>
        <ul class="list">${bags.map((x) => `
          <li><span><strong>Bag ${x.bag_number}</strong> <span class="muted small">${kg(x.weight_kg)}</span></span>
          ${x.test ? `<span class="chip res-${x.test.result.toLowerCase()}">${esc(x.test.result_label)} · ${x.test.moisture_pct}% moisture</span>` : '<span class="chip">Not tested yet</span>'}</li>`).join('')}
        </ul>
      </section>` : '';

    const qr = ['COMPLETED', 'RECEIVED', 'ACCEPTED', 'REJECTED'].includes(b.status) ? `
      <section class="card qr-card">
        <img src="/batches/${encodeURIComponent(b.code)}/qr.svg" alt="QR code" width="180" height="180"
             onerror="this.replaceWith(Object.assign(document.createElement('p'),{className:'muted small',textContent:'QR code appears after the batch is sent (needs network once).'}))">
        <p class="mono big">${esc(b.code)}</p>
        <p class="small muted">Show this code at the depot. Write it on every bag.</p>
        <a class="btn btn-ghost btn-sm" href="/batches/${encodeURIComponent(b.code)}/label/?bags=1" target="_blank">Print bag labels</a>
      </section>` : '';

    $view.innerHTML = `
      <a class="back" href="#/batches">← All batches</a>
      <section class="card batch-head">
        <div class="bc-top"><h1 class="mono">${esc(b.code)}</h1><span class="chip st-${b.status.toLowerCase()}">${esc(STATUS_LABEL(b.status))}</span></div>
        <dl class="facts">
          <div><dt>Wet beans</dt><dd>${kg(b.wet_weight_kg)}</dd></div>
          <div><dt>Source</dt><dd>${esc(LABEL(CFG.sources, b.bean_source))}</dd></div>
          <div><dt>Drying</dt><dd>${esc(LABEL(CFG.drying, b.drying_method))}</dd></div>
          <div><dt>Started</dt><dd>${esc(fmtDay(b.started_at))}</dd></div>
          ${b.dry_weight_kg ? `<div><dt>Dry beans</dt><dd>${kg(b.dry_weight_kg)} (${(b.dry_weight_kg / b.wet_weight_kg * 100).toFixed(1)}%)</dd></div>` : ''}
          ${b.bag_count ? `<div><dt>Bags</dt><dd>${b.bag_count}</dd></div>` : ''}
        </dl>
        <div class="progress"><i style="width:${Math.round(done / defs.length * 100)}%"></i></div>
        <p class="small muted">${done} of ${defs.length} steps done${b._dirty ? ' · <strong>saved on phone, not sent yet</strong>' : ''}</p>
        ${b._error ? `<p class="form-error">${esc(b._error)}</p>` : ''}
      </section>
      ${qr}${depot}
      <ol class="timeline">${items}</ol>`;

    const form = document.getElementById('step-form');
    if (!form) return;
    wirePhoto(form, holder);
    form.addEventListener('input', () => { formDirty = true; });
    form.addEventListener('submit', (e) => { e.preventDefault(); finishStep(form, holder, d, ci); });
  }

  async function finishStep(form, holder, d, ci) {
    const { b, byType } = d;
    const defs = defsFor(b);
    const def = defs[ci];
    const err = form.querySelector('#form-error');
    const showErr = (m) => { err.textContent = m; err.hidden = false; err.scrollIntoView({ block: 'center' }); };
    const prev = ci > 0 ? byType[defs[ci - 1].code] : null;
    let step = byType[def.code];
    if (!step) {
      step = { id: uuid(), batch_id: b.id, step_type: def.code, sequence: ci, started_at: prev ? prev.ended_at : b.started_at,
        ended_at: null, weight_start_kg: prev ? (prev.weight_end_kg ?? prev.weight_start_kg) : b.wet_weight_kg,
        weight_end_kg: null, latitude: null, longitude: null, notes: '' };
    }

    if (!form.when.value) return showErr('Enter the date and time.');
    const when = fromLocalInput(form.when.value);
    if (new Date(when) > new Date(Date.now() + 10 * 60000)) return showErr('Time cannot be in the future.');
    if (step.started_at && new Date(when) < new Date(step.started_at)) return showErr('Time is before this step started. Check the date.');
    const w = form.weight.value ? parseFloat(form.weight.value) : null;
    if (def.kind !== 'turn' && !(w > 0)) return showErr('Enter the weight in kg.');
    if (w && step.weight_start_kg && w > step.weight_start_kg * 1.2) return showErr(`Weight is more than at the start (${step.weight_start_kg} kg). Check the number.`);
    let bagCount = null;
    if (def.kind === 'pack') {
      bagCount = parseInt(form.bags.value, 10);
      if (!(bagCount > 0)) return showErr('Enter the number of bags.');
    }
    if (!holder.end) return showErr('Take a photo. Every step needs a photo as proof.');

    const btn = form.querySelector('button[type=submit]'); btn.disabled = true; btn.lastChild.textContent = 'Saving…';
    const gps = await getPosition();
    step.ended_at = when;
    if (!step.started_at) step.started_at = when;
    step.weight_end_kg = w;
    step.notes = form.notes.value.trim();
    if (gps) { step.latitude = gps.lat; step.longitude = gps.lng; }
    await saveRecord('steps', step);
    await savePhoto(step, 'END', holder.end);

    const next = defs[ci + 1];
    if (next && !byType[next.code]) {
      await saveRecord('steps', {
        id: uuid(), batch_id: b.id, step_type: next.code, sequence: ci + 1, started_at: when, ended_at: null,
        weight_start_kg: w ?? step.weight_start_kg, weight_end_kg: null, latitude: null, longitude: null, notes: '',
      });
    }
    if (def.kind === 'pack') {
      b.status = 'COMPLETED'; b.completed_at = when; b.dry_weight_kg = w; b.bag_count = bagCount;
    } else if (next && next.kind !== 'ferment' && next.kind !== 'turn') {
      b.status = 'DRYING';
    }
    await saveRecord('batches', b);
    formDirty = false;
    Sync.soon();
    toast(def.kind === 'pack' ? 'Batch complete! Take the bags to the depot.' : `${def.label} saved`);
    await viewBatch(b.id);
    const cur = document.getElementById('current-step');
    if (cur) cur.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function viewBatches() {
    const filter = sessionStorage.getItem('batchFilter') || 'all';
    const all = (await DB.all('batches')).sort((a, b) => (b.started_at || '').localeCompare(a.started_at || ''));
    const list = all.filter((b) => filter === 'all' || (filter === 'active' ? ACTIVE.includes(b.status) : !ACTIVE.includes(b.status)));
    $view.innerHTML = `
      <h1>My batches</h1>
      <div class="tabs">
        ${[['all', 'All'], ['active', 'In process'], ['done', 'Packed']].map(([v, l]) => `<a href="#/batches" data-f="${v}" class="${v === filter ? 'active' : ''}">${l}</a>`).join('')}
      </div>
      ${list.length ? `<ul class="list list-cards">${list.map((b) => `
        <li><a href="#/batch/${b.id}">
          <span><strong class="mono">${esc(b.code)}</strong><span class="muted small">${esc(fmtDay(b.started_at))} · ${kg(b.wet_weight_kg)} wet${b.dry_weight_kg ? ' · ' + kg(b.dry_weight_kg) + ' dry' : ''}</span></span>
          <span class="chip st-${b.status.toLowerCase()}">${esc(STATUS_LABEL(b.status))}</span>
          ${b._dirty ? '<span class="dot-pending" title="Not sent yet"></span>' : ''}
        </a></li>`).join('')}</ul>` : '<p class="empty">No batches yet.</p>'}`;
    $view.querySelectorAll('[data-f]').forEach((a) => a.addEventListener('click', (e) => {
      e.preventDefault(); sessionStorage.setItem('batchFilter', a.dataset.f); viewBatches();
    }));
  }

  let installPrompt = null;
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); installPrompt = e; });

  async function viewMe() {
    const farmer = await DB.getMeta('farmer');
    const s = Sync.state;
    const [bs, ss, ps] = await Promise.all([DB.all('batches'), DB.all('steps'), DB.all('photos')]);
    const errors = [...bs, ...ss, ...ps].filter((x) => x._error).map((x) => x._error);
    let storage = '';
    if (navigator.storage && navigator.storage.estimate) {
      const est = await navigator.storage.estimate();
      storage = `${(est.usage / 1048576).toFixed(1)} MB used on this phone`;
    }
    $view.innerHTML = `
      <h1>Me</h1>
      ${farmer ? `
      <section class="card">
        <dl class="facts facts-col">
          <div><dt>Name</dt><dd>${esc(farmer.name)}</dd></div>
          <div><dt>Member number</dt><dd class="mono">${esc(farmer.member_number)}</dd></div>
          <div><dt>Cooperative</dt><dd>${esc(farmer.cooperative)}</dd></div>
          <div><dt>Cluster group</dt><dd>${esc(farmer.cluster || '–')}</dd></div>
          <div><dt>Village</dt><dd>${esc(farmer.village || '–')}</dd></div>
        </dl>
      </section>` : ''}
      <section class="card">
        <h2>Sending records</h2>
        <p>${s.pending ? `<strong>${s.pending}</strong> record(s) saved on this phone, waiting for network.` : 'Everything has been sent.'}</p>
        <p class="small muted">Last sent: ${s.lastSync ? esc(fmtDate(s.lastSync)) : 'never'}${storage ? ' · ' + storage : ''}</p>
        ${s.status === 'auth' ? `<p class="form-error">${esc(s.error)} <a href="/login/?next=/app/">Log in</a></p>` : ''}
        ${errors.length ? `<ul class="small form-error">${[...new Set(errors)].map((e) => `<li>${esc(e)}</li>`).join('')}</ul>` : ''}
        <button class="btn btn-primary btn-block" id="sync-now"><svg><use href="#i-sync"/></svg>Send now</button>
      </section>
      <section class="card">
        <h2>App</h2>
        <button class="btn btn-ghost btn-block" id="install" ${installPrompt ? '' : 'hidden'}>Install on home screen</button>
        <button class="btn btn-ghost btn-block" id="logout">Log out</button>
        <p class="small muted">${esc(CFG.org)} Cocoa · works without network</p>
      </section>`;
    document.getElementById('sync-now').addEventListener('click', async () => {
      if (!navigator.onLine) return toast('No network now. Records will send when network comes back.', 'warn');
      await Sync.run(); viewMe();
    });
    const inst = document.getElementById('install');
    inst.addEventListener('click', async () => { if (installPrompt) { installPrompt.prompt(); installPrompt = null; inst.hidden = true; } });
    document.getElementById('logout').addEventListener('click', async () => {
      const n = await Sync.countPending();
      if (n && !confirm(`${n} record(s) are not sent yet. If you log out they will be deleted from this phone. Log out anyway?`)) return;
      await DB.clearAll();
      document.getElementById('logout-form').submit();
    });
  }

  // ---------- router ----------
  async function route(force = false) {
    const hash = location.hash || '#/';
    if (!force && formDirty && hash !== currentRoute && !confirm('You have not saved this form. Leave anyway?')) {
      history.replaceState(null, '', currentRoute); return;
    }
    formDirty = false;
    currentRoute = hash;
    releaseUrls();
    const [, name, arg] = hash.split('/');
    document.querySelectorAll('#app-nav a').forEach((a) => a.classList.toggle('active', a.dataset.nav === (name || 'home') || (name === 'batch' && a.dataset.nav === 'batches')));
    try {
      if (name === 'new') await viewNew();
      else if (name === 'batch' && arg) await viewBatch(arg);
      else if (name === 'batches') await viewBatches();
      else if (name === 'me') await viewMe();
      else await viewHome();
    } catch (e) {
      console.error(e);
      $view.innerHTML = `<p class="form-error">Something went wrong: ${esc(e.message)}</p>`;
    }
    if (!force) window.scrollTo(0, 0);
  }

  window.addEventListener('hashchange', () => route());
  // Refresh the screen after a sync or every minute (countdowns) – but never while typing.
  const refresh = () => {
    const typing = document.activeElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
    if (!formDirty && !typing && !currentRoute.startsWith('#/new')) route(true);
  };
  let lastStatus = null;
  Sync.on((s) => { if (lastStatus === 'syncing' && s.status === 'ok') refresh(); lastStatus = s.status; });
  setInterval(refresh, 60 * 1000);
  window.addEventListener('beforeunload', (e) => { if (formDirty) { e.preventDefault(); e.returnValue = ''; } });

  route(true);
  Sync.init();
})();
