{% load static %}/* Service worker – keeps the farmer app working without network. */
const VERSION = 'cocoa-{{ APP_VERSION }}';
const SHELL_CACHE = VERSION + '-shell';
const RUNTIME_CACHE = VERSION + '-runtime';
const APP_URL = '/app/';
const STATIC_ASSETS = [
  '{% static "css/app.css" %}',
  '{% static "js/db.js" %}',
  '{% static "js/sync.js" %}',
  '{% static "js/app.js" %}',
  '{% static "manifest.json" %}',
  '{% static "icons/icon-192.png" %}',
  '{% static "icons/icon-512.png" %}',
  '/offline/'
];

async function cacheAppShell() {
  try {
    const res = await fetch(APP_URL, { credentials: 'same-origin' });
    // Only cache the real app page, never the login redirect.
    if (res.ok && !res.redirected) {
      const cache = await caches.open(SHELL_CACHE);
      await cache.put(APP_URL, res);
    }
  } catch (e) { /* offline during install – will cache on next visit */ }
}

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    await cache.addAll(STATIC_ASSETS);
    await cacheAppShell();
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Sync API is handled by the page (IndexedDB queue) – never cache.
  if (url.pathname.startsWith('/api/')) return;

  // Farmer app shell: network first, fall back to cached copy.
  if (req.mode === 'navigate' && url.pathname.startsWith(APP_URL)) {
    event.respondWith((async () => {
      try {
        const res = await fetch(req);
        if (res.ok && !res.redirected) {
          const cache = await caches.open(SHELL_CACHE);
          cache.put(APP_URL, res.clone());
        }
        return res;
      } catch (e) {
        const cached = await caches.match(APP_URL);
        return cached || caches.match('/offline/');
      }
    })());
    return;
  }

  // Static files, photos and QR codes: cache first.
  if (url.pathname.startsWith('/static/') || url.pathname.startsWith('/media/') || url.pathname.endsWith('/qr.svg')) {
    event.respondWith((async () => {
      const cached = await caches.match(req, { ignoreSearch: url.pathname.startsWith('/static/') });
      if (cached) return cached;
      try {
        const res = await fetch(req);
        if (res.ok) {
          const cache = await caches.open(RUNTIME_CACHE);
          cache.put(req, res.clone());
        }
        return res;
      } catch (e) {
        return new Response('', { status: 504 });
      }
    })());
    return;
  }

  // Other pages (staff screens): network, then offline notice.
  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(async () => (await caches.match(req)) || caches.match('/offline/')));
  }
});

// Background Sync: wake any open app window to push its queue.
self.addEventListener('sync', (event) => {
  if (event.tag === 'cocoa-sync') {
    event.waitUntil(self.clients.matchAll({ includeUncontrolled: true }).then((clients) => {
      clients.forEach((c) => c.postMessage('cocoa-sync'));
    }));
  }
});
