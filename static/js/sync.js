/* Sync engine: pushes records saved offline, then pulls the server copy.
 * Every local change sets _dirty=1 and bumps _rev. A record is only marked
 * clean if it was not edited again while the upload was in flight. */
const Sync = (() => {
  const listeners = new Set();
  const state = { status: navigator.onLine ? 'idle' : 'offline', lastSync: null, pending: 0, error: null };
  let running = false;
  let timer = null;

  const emit = () => listeners.forEach((fn) => fn({ ...state }));
  function on(fn) { listeners.add(fn); fn({ ...state }); return () => listeners.delete(fn); }

  function csrf() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  async function http(url, opts = {}) {
    const res = await fetch(url, {
      credentials: 'same-origin',
      ...opts,
      headers: { 'X-CSRFToken': csrf(), 'X-Requested-With': 'fetch', ...(opts.headers || {}) },
    });
    if (res.status === 401 || res.status === 403 || res.redirected) {
      let msg = 'Please log in again to send your records.';
      try { const j = await res.json(); if (j.error && j.error !== 'auth') msg = j.error; } catch (e) { /* html */ }
      const err = new Error(msg); err.status = 401; throw err;
    }
    if (!res.ok) { const err = new Error('Server error ' + res.status); err.status = res.status; throw err; }
    return res.json();
  }

  const strip = (r) => {
    const o = { ...r };
    ['_dirty', '_rev', '_error', 'blob'].forEach((k) => delete o[k]);
    return o;
  };

  async function countPending() {
    const [b, s, p] = await Promise.all([DB.all('batches'), DB.all('steps'), DB.all('photos')]);
    return [...b, ...s, ...p].filter((x) => x._dirty).length;
  }

  async function settle(store, sent, result) {
    const ok = new Set(result.ok || []);
    const failed = {};
    (result.failed || []).forEach((f) => { failed[f.id] = f.error; });
    for (const rec of sent) {
      const cur = await DB.get(store, rec.id);
      if (!cur) continue;
      if (ok.has(rec.id)) {
        if (cur._rev === rec._rev) { cur._dirty = 0; delete cur._error; await DB.put(store, cur); }
      } else if (failed[rec.id]) {
        cur._error = failed[rec.id]; await DB.put(store, cur);
      }
    }
  }

  async function push() {
    const batches = (await DB.all('batches')).filter((x) => x._dirty);
    const steps = (await DB.all('steps')).filter((x) => x._dirty);
    if (batches.length || steps.length) {
      const res = await http('/api/sync/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batches: batches.map(strip), steps: steps.map(strip) }),
      });
      await settle('batches', batches, res.batches);
      await settle('steps', steps, res.steps);
    }
    const photos = (await DB.all('photos')).filter((x) => x._dirty && x.blob);
    for (const p of photos) {
      const fd = new FormData();
      ['id', 'step_id', 'batch_id', 'step_type', 'kind', 'taken_at'].forEach((k) => fd.append(k, p[k] || ''));
      fd.append('image', p.blob, p.id + '.jpg');
      try {
        const r = await http('/api/photos/', { method: 'POST', body: fd });
        const cur = await DB.get('photos', p.id);
        if (cur) { cur._dirty = 0; cur.url = r.url; delete cur._error; await DB.put('photos', cur); }
      } catch (e) {
        if (e.status === 401) throw e;
        const cur = await DB.get('photos', p.id);
        if (cur) { cur._error = e.status === 409 ? 'Waiting for step to sync' : 'Photo upload failed – will retry'; await DB.put('photos', cur); }
      }
    }
  }

  const LOCKED = ['RECEIVED', 'ACCEPTED', 'REJECTED'];

  async function pull() {
    const data = await http('/api/bootstrap/');
    await DB.setMeta('farmer', data.farmer);
    await DB.setMeta('thresholds', data.thresholds);
    for (const b of data.batches) {
      const cur = await DB.get('batches', b.id);
      // Depot results always win; otherwise keep unsent local edits.
      if (cur && cur._dirty && !LOCKED.includes(b.status)) {
        cur.bags = b.bags; await DB.put('batches', cur);
      } else {
        await DB.put('batches', { ...b, _dirty: 0, _rev: cur ? cur._rev : 0 });
      }
    }
    for (const s of data.steps) {
      const cur = await DB.get('steps', s.id);
      if (!(cur && cur._dirty)) await DB.put('steps', { ...s, _dirty: 0, _rev: cur ? cur._rev : 0 });
    }
    for (const p of data.photos) {
      const cur = await DB.get('photos', p.id);
      if (!cur) await DB.put('photos', { ...p, blob: null, _dirty: 0 });
      else if (!cur._dirty && cur.url !== p.url) { cur.url = p.url; await DB.put('photos', cur); }
    }
    // Free phone storage: drop local photo copies once the depot has the batch.
    const lockedIds = new Set(data.batches.filter((b) => LOCKED.includes(b.status)).map((b) => b.id));
    if (lockedIds.size) {
      for (const p of await DB.all('photos')) {
        if (p.blob && !p._dirty && lockedIds.has(p.batch_id)) { p.blob = null; await DB.put('photos', p); }
      }
    }
  }

  async function run() {
    if (running) return;
    if (!navigator.onLine) { state.status = 'offline'; state.pending = await countPending(); emit(); return; }
    running = true; state.status = 'syncing'; emit();
    try {
      await push();
      await pull();
      state.lastSync = new Date().toISOString();
      await DB.setMeta('lastSync', state.lastSync);
      state.status = 'ok'; state.error = null;
    } catch (e) {
      state.status = e.status === 401 ? 'auth' : (navigator.onLine ? 'error' : 'offline');
      state.error = e.message || 'No connection';
    } finally {
      running = false;
      state.pending = await countPending();
      emit();
    }
  }

  function soon() {
    clearTimeout(timer);
    timer = setTimeout(run, 800);
    countPending().then((n) => { state.pending = n; emit(); });
    if ('serviceWorker' in navigator && 'SyncManager' in window) {
      navigator.serviceWorker.ready.then((r) => r.sync.register('cocoa-sync')).catch(() => {});
    }
  }

  async function init() {
    state.lastSync = await DB.getMeta('lastSync', null);
    state.pending = await countPending();
    emit();
    run();
  }

  window.addEventListener('online', () => run());
  window.addEventListener('offline', () => { state.status = 'offline'; emit(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) run(); });
  setInterval(() => { if (navigator.onLine) run(); }, 60 * 1000);
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', (e) => { if (e.data === 'cocoa-sync') run(); });
  }

  return { init, run, soon, on, countPending, get state() { return { ...state }; } };
})();
