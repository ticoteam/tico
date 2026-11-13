// Tico's service worker exists so Chrome and Edge offer "Install app", and to show Web Push (docs/push.md). It caches nothing itself:
// the page itself goes to the network with cache: 'no-store'; everything else is left to the browser.
// The API in particular must never be served from a cache — Tico is a live view of what the bots are doing.
// A classic script (no import or export), compiled to build/ui/sw.js and served at /sw.js (backend/ui_bundle_routes.py).
// Type-checked alone by ui/tsconfig.worker.json, whose WebWorker lib types `self` as a worker in general: the one alias
// below names it as the service worker it is.
const worker = self as ServiceWorkerGlobalScope & typeof globalThis;
worker.addEventListener('install', () => worker.skipWaiting());
worker.addEventListener('activate', event => event.waitUntil(worker.clients.claim()));
worker.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/')) return;     // untouched: no interception, no cache
  // The hashed script and stylesheet (backend/ui_bundle.py) are immutable at their URL: the browser's own cache serves them.
  if (url.pathname.startsWith('/tico/ui/app.bundle.')) return;
  // Scripts, styles, icons and images keep the server's own caching (ETag, an hour for /assets/): only the page
  // itself is fetched past every cache, so a deploy shows on the next load.
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request, {cache: 'no-store'}));
});
// Web Push (backend/web_push.py): each message is {title, body, url, tag, ring}. Every one is shown, a Tico tab open or
// not: Safari takes a site's push permission away when a push shows nothing. A ring (a call) sounds again under its tag
// and stays up until it is answered or dismissed, where the browser supports that.
type PushMessage = {title?: string; body?: string; url?: string; tag?: string; ring?: boolean};
type ShownOptions = NotificationOptions & {renotify?: boolean};
worker.addEventListener('push', event => {
  let message: PushMessage = {};
  try { message = (event.data?.json() ?? {}) as PushMessage; } catch { message = {body: event.data?.text() ?? ''}; }
  const options: ShownOptions = {body: message.body || '', icon: '/assets/icon-192.png', data: {url: message.url || '/'}};
  if (message.tag) {
    options.tag = message.tag;
    if (message.ring) { options.renotify = true; options.requireInteraction = true; }
  }
  event.waitUntil(worker.registration.showNotification(message.title || 'Tico', options));
});
// A click opens the message's page in Tico: the open Tico tab is focused and taken there, else a new one opens. Only a
// page of this origin is opened.
worker.addEventListener('notificationclick', event => {
  event.notification.close();
  const data = event.notification.data as {url?: string} | null;
  const asked = new URL(data?.url || '/', worker.location.origin);
  const url = asked.origin === worker.location.origin ? asked.href : worker.location.origin + '/';
  event.waitUntil((async () => {
    const open = await worker.clients.matchAll({type: 'window', includeUncontrolled: true});
    const tab = open.find(c => c.focused) || open.find(c => c.visibilityState === 'visible') || open[0];
    if (tab) {
      const shown = await tab.focus().catch(() => tab);
      // navigate() works only on a tab this worker controls; any other is left as it is and a new tab opens.
      if (await shown.navigate(url).catch(() => null)) return;
    }
    await worker.clients.openWindow(url);
  })());
});
