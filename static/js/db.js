/* Tiny IndexedDB wrapper – the phone's offline database. */
const DB = (() => {
  const NAME = 'ncal-cocoa';
  const VERSION = 1;
  let dbPromise = null;

  function open() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(NAME, VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains('batches')) db.createObjectStore('batches', { keyPath: 'id' });
        if (!db.objectStoreNames.contains('steps')) {
          db.createObjectStore('steps', { keyPath: 'id' }).createIndex('batch_id', 'batch_id');
        }
        if (!db.objectStoreNames.contains('photos')) {
          db.createObjectStore('photos', { keyPath: 'id' }).createIndex('step_id', 'step_id');
        }
        if (!db.objectStoreNames.contains('meta')) db.createObjectStore('meta', { keyPath: 'key' });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return dbPromise;
  }

  const wrap = (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const done = (t) => new Promise((res, rej) => { t.oncomplete = () => res(); t.onerror = t.onabort = () => rej(t.error); });

  async function get(store, key) {
    const db = await open();
    return wrap(db.transaction(store).objectStore(store).get(key));
  }
  async function all(store) {
    const db = await open();
    return wrap(db.transaction(store).objectStore(store).getAll());
  }
  async function byIndex(store, index, value) {
    const db = await open();
    return wrap(db.transaction(store).objectStore(store).index(index).getAll(value));
  }
  async function put(store, value) {
    const db = await open();
    const t = db.transaction(store, 'readwrite');
    t.objectStore(store).put(value);
    return done(t);
  }
  async function putMany(store, values) {
    const db = await open();
    const t = db.transaction(store, 'readwrite');
    const s = t.objectStore(store);
    values.forEach((v) => s.put(v));
    return done(t);
  }
  async function del(store, key) {
    const db = await open();
    const t = db.transaction(store, 'readwrite');
    t.objectStore(store).delete(key);
    return done(t);
  }
  async function getMeta(key, fallback = null) {
    const r = await get('meta', key);
    return r ? r.value : fallback;
  }
  function setMeta(key, value) {
    return put('meta', { key, value });
  }
  async function clearAll() {
    const db = await open();
    const stores = ['batches', 'steps', 'photos', 'meta'];
    const t = db.transaction(stores, 'readwrite');
    stores.forEach((s) => t.objectStore(s).clear());
    return done(t);
  }

  return { get, all, byIndex, put, putMany, del, getMeta, setMeta, clearAll };
})();
