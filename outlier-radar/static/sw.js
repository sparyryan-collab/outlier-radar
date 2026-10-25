// Offline support for the installed app. Only same-origin files are handled; YouTube, Google and Firebase go straight to the network.
const CACHE = 'viral-radar-v2';
const SHELL = ['./', 'index.html', 'manifest.webmanifest', 'icons/icon-192.png', 'icons/icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

async function networkFirst(req) {   // fresh when online, the last copy when offline
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req.mode === 'navigate' ? req.url : req, {cache: 'no-cache', credentials: 'same-origin'});   // skip the browser's 10-minute copy
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (e) {
    return (await cache.match(req, {ignoreSearch: true})) || Response.error();
  }
}
async function staleWhileRevalidate(req) {   // instant from the cache, refreshed in the background
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req, {ignoreSearch: true});
  const update = fetch(req).then(res => { if (res.ok) cache.put(req, res.clone()); return res; }).catch(() => hit);
  return hit || update;
}
async function cacheFirst(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) cache.put(req, res.clone());
  return res;
}

self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.endsWith('/data-lite.json')) e.respondWith(staleWhileRevalidate(req));
  else if (url.pathname.endsWith('.json') || req.mode === 'navigate' || url.pathname.endsWith('/') || url.pathname.endsWith('.html')) e.respondWith(networkFirst(req));
  else if (url.pathname.includes('/avatars/') || url.pathname.includes('/icons/')) e.respondWith(cacheFirst(req));
});
