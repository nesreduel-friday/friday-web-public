/* Service Worker.
 *
 * Absichtlich ohne Offline-Cache: während Friday entsteht, soll jede
 * Änderung sofort ankommen, statt in einem alten Cache festzuhängen.
 *
 * Seine eigentliche Aufgabe ist Web Push: Benachrichtigungen kommen auch an,
 * wenn die App geschlossen ist — auf dem iPhone nur, wenn sie über
 * „Zum Home-Bildschirm" installiert wurde.
 */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('push', (e) => {
  let d = {};
  try {
    d = e.data ? e.data.json() : {};
  } catch (_) {
    d = { text: e.data ? e.data.text() : '' };
  }
  e.waitUntil(self.registration.showNotification(d.titel || 'Friday', {
    body: d.text || '',
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    lang: 'de',
    // Gleiche Kennung ersetzt eine ältere Nachricht, statt zu stapeln.
    tag: d.tag || undefined,
    renotify: !!d.tag,
    data: { url: d.url || '/' },
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const ziel = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil((async () => {
    const fenster = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const f of fenster) {
      if ('focus' in f) {
        await f.focus();
        if (f.navigate && ziel !== '/') await f.navigate(ziel);
        return;
      }
    }
    await self.clients.openWindow(ziel);
  })());
});
