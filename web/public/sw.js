// Tico's service worker, served at /sw.js with scope / (backend/web_routes.py). Browsers that installed it keep it and
// ask that same URL for updates; it shows Web Push (backend/web_push.py, docs/push.md), and caches nothing itself: the
// page goes to the network with cache: 'no-store', everything else is left to the browser. The API in particular is
// never served from a cache: Tico is a live view of what the bots are doing.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/')) return; // untouched: no interception, no cache
  // The build's files are named by their content, immutable at their URL: the browser's own cache serves them.
  if (url.pathname.startsWith('/next/assets/')) return;
  // Scripts, styles, icons and images keep the server's own caching: only the page itself is fetched past every cache,
  // so a deploy shows on the next load.
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request, { cache: 'no-store' }));
});
// Web Push: each message is {title, body, url, tag, ring}. Every one is shown, a Tico tab open or not: Safari takes a
// site's push permission away when a push shows nothing. A ring (a call) sounds again under its tag and stays up until
// it is answered or dismissed, where the browser supports that.
self.addEventListener('push', (event) => {
  let message;
  try {
    message = (event.data && event.data.json()) || {};
  } catch {
    message = { body: event.data ? event.data.text() : '' };
  }
  const options = { body: message.body || '', icon: '/assets/icon-192.png', data: { url: message.url || '/' } };
  if (message.tag) {
    options.tag = message.tag;
    if (message.ring) {
      options.renotify = true;
      options.requireInteraction = true;
    }
  }
  event.waitUntil(self.registration.showNotification(message.title || 'Tico', options));
});
// A click opens the message's page in Tico: the open Tico tab is focused and taken there, else a new one opens. Only a
// page of this origin is opened. An old address (`/#/task/12`) opens its page in the app (web/src/router.tsx).
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data;
  const asked = new URL((data && data.url) || '/', self.location.origin);
  const url = asked.origin === self.location.origin ? asked.href : self.location.origin + '/';
  event.waitUntil(
    (async () => {
      const open = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
      const tab = open.find((c) => c.focused) || open.find((c) => c.visibilityState === 'visible') || open[0];
      if (tab) {
        const shown = await tab.focus().catch(() => tab);
        // navigate() works only on a tab this worker controls; any other is left as it is and a new tab opens.
        if (await shown.navigate(url).catch(() => null)) return;
      }
      await self.clients.openWindow(url);
    })(),
  );
});
