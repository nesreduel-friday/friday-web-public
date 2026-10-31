/* Wo läuft Friday gerade? — Erkennung, Anzeige, Meldung an den Server.

   Handy im Browser, Handy als installierte App, PC im Browser, Desktop-
   Installation: Das steht auf der Einstellungen-Seite und geht an Friday.
   Grundlage für später ("mach mir XY auf" hängt davon ab, wo sie läuft).

   Der Server übernimmt nichts davon blind — er prüft jeden Wert gegen eine
   feste Liste, damit über eine erfundene Meldung nichts in den Prompt kommt. */
(() => {
  const { $ } = window.Friday;
  const SCHLUESSEL = 'friday-kennung';
  const HERZSCHLAG = 60000;   // passend zu FRISCH_SEKUNDEN im Kern (180 s)
  const ua = navigator.userAgent || '';

  const el = (tag, klasse, text) => {
    const e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };

  // Eine eigene Kennung je Oberfläche, damit Handy und PC nicht als dasselbe
  // Gerät gelten. Nur lokal, verrät nichts über Eike.
  function kennung() {
    try {
      let k = localStorage.getItem(SCHLUESSEL);
      if (!k) {
        k = crypto.randomUUID ? crypto.randomUUID() : `k${Date.now()}${Math.random()}`.replace(/\D/g, '');
        localStorage.setItem(SCHLUESSEL, k);
      }
      return k;
    } catch (_) {
      return 'ohne-speicher';   // privates Fenster o.ä. — die Anzeige geht trotzdem
    }
  }

  // Installiert heißt: aus dem Home-Bildschirm bzw. als Desktop-App gestartet.
  // window-controls-overlay deckt die Desktop-Installation mit ab, navigator.standalone das iPhone.
  function art() {
    const steht = (frage) => { try { return window.matchMedia(frage).matches; } catch (_) { return false; } };
    return (steht('(display-mode: standalone)') || steht('(display-mode: window-controls-overlay)')
            || steht('(display-mode: fullscreen)') || window.navigator.standalone === true)
      ? 'installiert' : 'browser';
  }

  function system() {
    if (/iPhone|iPod/.test(ua)) return 'iOS';
    // Das iPad meldet sich seit iPadOS 13 als Macintosh — Touch verrät es.
    if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return 'iPadOS';
    if (/Android/.test(ua)) return 'Android';
    if (/Windows/.test(ua)) return 'Windows';
    if (/Macintosh|Mac OS X/.test(ua)) return 'macOS';
    if (/Linux|X11/.test(ua)) return 'Linux';
    return '';
  }

  function geraet() {
    const s = system();
    if (s === 'iOS') return 'Handy';
    if (s === 'iPadOS') return 'Tablet';
    if (s === 'Android') return /Mobile/.test(ua) ? 'Handy' : 'Tablet';
    return 'PC';
  }

  function browser() {
    if (/Edg\//.test(ua)) return 'Edge';
    if (/OPR\/|Opera/.test(ua)) return 'Opera';
    if (/Firefox\/|FxiOS/.test(ua)) return 'Firefox';
    if (/CriOS|Chrome\//.test(ua)) return 'Chrome';
    if (/Safari\//.test(ua)) return 'Safari';
    return '';
  }

  const erheben = () => ({
    kennung: kennung(), art: art(), geraet: geraet(), system: system(), browser: browser(),
    breite: window.innerWidth || 0, hoehe: window.innerHeight || 0,
  });

  const WORTE = { installiert: 'als App installiert', browser: 'im Browser' };

  function anzeigen(u) {
    const ziel = $('u-hier');
    if (!ziel) return;
    const wo = WORTE[u.art] || 'auf unbekanntem Weg';
    const br = u.browser ? ` — ${u.browser}` : '';
    ziel.textContent = `${u.geraet} (${u.system || 'unbekannt'}), ${wo}${br}`;
  }

  async function sitzungenLaden() {
    const ziel = $('u-sitzungen');
    if (!ziel) return;
    let daten;
    try {
      daten = await (await fetch('/api/umgebung', { cache: 'no-store' })).json();
    } catch (_) {
      ziel.textContent = 'Die Sitzungen sind gerade nicht abrufbar.';
      return;
    }
    ziel.textContent = '';
    const meine = kennung();
    const sitzungen = daten.sitzungen || [];
    if (!sitzungen.length) {
      ziel.append(el('p', 'leer-klein', 'Gerade ist nichts offen.'));
      return;
    }
    // Der Kern liefert nur, was wirklich offen ist, jede Oberfläche einmal.
    for (const s of sitzungen) {
      const zeile = el('div', 'eintrag');
      const kopf = el('div', 'kopfzeile');
      kopf.append(el('span', 'titel', s.text));
      if (s.kennung === meine) kopf.append(el('span', 'klein', 'dieses Gerät'));
      zeile.append(kopf);
      ziel.append(zeile);
    }
  }

  async function melden() {
    const u = erheben();
    anzeigen(u);
    try {
      await fetch('/api/umgebung', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(u),
      });
    } catch (_) { return; }          // Die Anzeige steht auch ohne Server.
    if (location.hash === '#einstellungen') sitzungenLaden();
  }

  // Andere Seiten (Sprechen, Chat) schicken die Kennung mit, damit Friday weiß,
  // an WELCHER Oberfläche Eike gerade sitzt — nicht nur, welche zuletzt gemeldet hat.
  window.Friday.kennung = kennung;

  melden();
  // Regelmäßig melden, sonst gilt die Sitzung im Kern als geschlossen.
  setInterval(() => { if (document.visibilityState === 'visible') melden(); }, HERZSCHLAG);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') melden();
  });
  window.addEventListener('hashchange', () => { if (location.hash === '#einstellungen') melden(); });
  // Wird die Seite nachträglich installiert oder aus der App geöffnet, ändert sich die Art.
  try {
    window.matchMedia('(display-mode: standalone)').addEventListener('change', melden);
  } catch (_) { /* ältere Browser können das nicht */ }
})();
