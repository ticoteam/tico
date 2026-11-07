// Tico's service worker exists so Chrome and Edge offer "Install app". It caches nothing itself:
// the page itself goes to the network with cache: 'no-store'; everything else is left to the browser.
// The API in particular must never be served from a cache — Tico is a live view of what the bots are doing.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/')) return;     // untouched: no interception, no cache
  // The hashed script and stylesheet (backend/ui_bundle.py) are immutable at their URL: the browser's own cache serves them.
  if (url.pathname.startsWith('/tico/ui/app.bundle.')) return;
  // Scripts, styles, icons and images keep the server's own caching (ETag, an hour for /assets/): only the page
  // itself is fetched past every cache, so a deploy shows on the next load.
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request, {cache: 'no-store'}));
});
