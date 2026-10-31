/* Benachrichtigungen ein- und ausschalten (Einstellungen-Seite).

   Auf dem iPhone geht Web Push nur, wenn die App über „Zum Home-Bildschirm"
   installiert ist, und die Erlaubnis muss aus einem echten Fingertipp kommen —
   deshalb fragt erst der Knopf danach, nie die Seite von selbst. */
(() => {
  const { $ } = window.Friday;

  const status = $('p-status');
  const knopfAn = $('p-an');
  const knopfTest = $('p-test');
  const knopfAus = $('p-aus');

  const geht = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  const installiert = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const apple = /iPhone|iPad|iPod/.test(navigator.userAgent);

  const senden = (url, daten) => fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(daten || {}),
  }).then((r) => r.json().catch(() => ({})));

  // base64url (so kommt der Schlüssel vom Server) -> Uint8Array (so will es der Browser)
  function schluesselBytes(b64) {
    const voll = (b64 + '='.repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    const roh = atob(voll);
    return Uint8Array.from([...roh].map((z) => z.charCodeAt(0)));
  }

  function zeigen(text, an) {
    status.textContent = text;
    knopfAn.hidden = an;
    knopfTest.hidden = !an;
    knopfAus.hidden = !an;
  }

  async function stand() {
    if (!geht) {
      zeigen(apple && !installiert
        ? 'Dafür muss Friday auf dem Home-Bildschirm liegen: Teilen → Zum Home-Bildschirm, dann hier nochmal schauen.'
        : 'Dieser Browser kann keine Benachrichtigungen.', false);
      knopfAn.hidden = true;
      return;
    }
    if (Notification.permission === 'denied') {
      zeigen('Du hast Benachrichtigungen abgelehnt. Das lässt sich nur in den Einstellungen deines Geräts ändern.', false);
      knopfAn.hidden = true;
      return;
    }
    const reg = await navigator.serviceWorker.getRegistration();
    const abo = reg && await reg.pushManager.getSubscription();
    zeigen(abo ? 'Benachrichtigungen sind an.' : 'Benachrichtigungen sind aus.', !!abo);
  }

  knopfAn.addEventListener('click', async () => {
    knopfAn.disabled = true;
    try {
      const erlaubnis = await Notification.requestPermission();
      if (erlaubnis !== 'granted') { await stand(); return; }
      const reg = await navigator.serviceWorker.register('/sw.js');
      await navigator.serviceWorker.ready;
      const { schluessel } = await (await fetch('/api/push/schluessel', { cache: 'no-store' })).json();
      const abo = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: schluesselBytes(schluessel),
      });
      const r = await senden('/api/push/anmelden', { anmeldung: abo.toJSON(), geraet: navigator.platform || '' });
      status.textContent = r.ok ? 'Benachrichtigungen sind an.' : (r.detail || 'Das hat nicht geklappt.');
      await stand();
    } catch (fehler) {
      status.textContent = `Das hat nicht geklappt: ${fehler.message || fehler}`;
    } finally {
      knopfAn.disabled = false;
    }
  });

  // Sofort: geprüft am 14.09.2026 — iOS zeigt das Banner auch, während Friday
  // offen ist. Der Grund einer Ablehnung wird angezeigt, nicht verschluckt.
  knopfTest.addEventListener('click', async () => {
    knopfTest.disabled = true;
    const r = await senden('/api/push/test', { verzoegerung: 0 });
    status.textContent = r.text || r.detail || 'Keine Antwort.';
    knopfTest.disabled = false;
  });

  knopfAus.addEventListener('click', async () => {
    knopfAus.disabled = true;
    try {
      const reg = await navigator.serviceWorker.getRegistration();
      const abo = reg && await reg.pushManager.getSubscription();
      if (abo) {
        await senden('/api/push/abmelden', { endpoint: abo.endpoint });
        await abo.unsubscribe();
      }
    } finally {
      knopfAus.disabled = false;
      await stand();
    }
  });

  const beiAnzeige = () => { if (location.hash === '#einstellungen') stand(); };
  window.addEventListener('hashchange', beiAnzeige);
  beiAnzeige();
})();
