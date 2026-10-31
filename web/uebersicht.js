/* Übersicht — die Startseite: Karten für das, was gerade relevant ist.

   Der Kern setzt die Liste zusammen (GET /api/dashboard), diese Datei zeichnet
   sie und zählt Countdown und Fortschritt selbst weiter. Eine Karte je Art
   steht im Register ZEICHNER: Für eine neue Kartenart genügt ein Eintrag hier
   plus ein Anbieter im Kern; eine unbekannte Art wird als schlichte Karte
   gezeichnet statt verschluckt.

   Daten werden nie als HTML eingesetzt, nur als Text: Titel und Texte kommen vom Kern, teils vom Modell. */
(() => {
  const { $ } = window.Friday;

  const ABFRAGE_MS = 15000;
  const GESEHEN_NACH_MS = 2000;
  const seite = $('uebersicht'), behaelter = $('karten'), hinweis = $('ueb-hinweis');

  const karten = new Map();      // id → { el, z, gesehen }
  let versatz = 0;               // Serverzeit − Browserzeit in ms
  let laeuft = false;
  let leerKarte = null;

  const sichtbar = () => !seite.hidden && document.visibilityState === 'visible';
  const jetzt = () => Date.now() + versatz;

  /* ---------- kleine Bausteine ---------- */

  function el(tag, klasse, text) {
    const e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  const SVG = 'http://www.w3.org/2000/svg';
  const SYMBOLE = {
    play: 'M8 5v14l11-7z',
    pause: 'M6 5h4v14H6zM14 5h4v14h-4z',
    weiter: 'M6 6l8.5 6L6 18zM16 6h2v12h-2z',
    zu: 'M6 6l12 12M18 6L6 18',
    zurueck: 'M18 6l-8.5 6L18 18zM6 6h2v12H6z',
    // Pfeil aus dem Kasten: „öffnen in einer anderen App“
    oeffnen: 'M14 4h6v6M20 4l-9 9M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4',
    sperren: 'M7 11V8a5 5 0 0 1 10 0v3M6 11h12v9H6z',
    stumm: 'M4 9h4l5-4v14l-5-4H4zM17 9l4 6M21 9l-4 6',
    mond: 'M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z',
    power: 'M12 3v9M6.5 6.5a8 8 0 1 0 11 0',
  };
  const STRICH = new Set(['zu', 'oeffnen', 'sperren', 'stumm', 'mond', 'power']);
  function symbol(name) {
    const s = document.createElementNS(SVG, 'svg');
    s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('aria-hidden', 'true');
    const p = document.createElementNS(SVG, 'path');
    p.setAttribute('d', SYMBOLE[name]);
    if (STRICH.has(name)) { p.setAttribute('fill', 'none'); p.setAttribute('stroke', 'currentColor'); p.setAttribute('stroke-width', '2'); p.setAttribute('stroke-linecap', 'round'); p.setAttribute('stroke-linejoin', 'round'); }
    else p.setAttribute('fill', 'currentColor');
    s.appendChild(p);
    return s;
  }

  function knopf(name, beschriftung, klick) {
    const b = el('button', 'karte-knopf');
    b.type = 'button';
    b.setAttribute('aria-label', beschriftung);
    b.appendChild(symbol(name));
    b.addEventListener('click', klick);
    return b;
  }

  const zweistellig = (n) => String(n).padStart(2, '0');
  // Immer deutsche Zeit, egal wo das Gerät steht — die Zeitpunkte der Schule sind deutsche Zeit.
  const ZEITZONE = 'Europe/Berlin';
  const uhr = (ms) => new Date(ms).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: ZEITZONE });

  function restzeit(ms) {
    const s = Math.max(0, Math.ceil(ms / 1000));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h}:${zweistellig(m)} h` : `${zweistellig(m)}:${zweistellig(s % 60)}`;
  }

  function vorZeit(iso) {
    const min = Math.round((jetzt() - Date.parse(iso)) / 60000);
    if (min < 1) return 'gerade eben';
    if (min < 60) return `vor ${min} min`;
    return `vor ${Math.floor(min / 60)} h`;
  }

  // Ein Bild nur von https oder aus Fridays eigenen Bildern — die Adresse setzt der Browser ein.
  // Testkarten (vorschau) dürfen zusätzlich ein Bild der App selbst zeigen, damit man das Cover-Aussehen prüfen kann.
  const bildOk = (u, vorschau = false) => typeof u === 'string' && (/^https:\/\/[^\s"'()]+$/.test(u) || /^\/api\/bilder\/[0-9a-f]{32}$/.test(u)
    || /^\/api\/netzbild\/[0-9a-f]{16}$/.test(u)
    || /^\/api\/jellyfin\/bild\/[0-9a-f]{32}$/.test(u)
    || (vorschau && /^\/icons\/[\w.-]+\.png$/.test(u)));

  async function melden(pfad) {
    try { await fetch(pfad, { method: 'POST', headers: { 'Content-Type': 'application/json' } }); } catch (_) {}
  }
  async function gesehenMelden(id) {
    if (id.startsWith('vorschau-')) return;        // Testkarten aus den Einstellungen: nichts geht an den Server
    try {
      await fetch('/api/dashboard/gesehen', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                              body: JSON.stringify({ id }) });
    } catch (_) {}
  }

  /* ---------- Glas-Menü: klappt aus der angetippten Karte, führt in eine andere App ---------- */

  // Nur spotify:<art>:<id> wird zum Link — sonst wäre die Adresse aus den Daten ein Einfallstor.
  const SPOTIFY_ADRESSE = /^spotify:(track|episode|album|show|artist):[A-Za-z0-9]{8,40}$/;
  const SPOTIFY_APP = 'spotify://';
  const spotifyOk = (u) => typeof u === 'string' && SPOTIFY_ADRESSE.test(u);

  // Die Schulmanager-App hat kein bekanntes URL-Schema und keinen Universal Link (25.09.2026: die https-Adresse
  // öffnete nur die Webseite). Praktisch jede App startet ein Kurzbefehl — Eike legt einmal einen namens
  // „Schulmanager“ mit der Aktion „App öffnen“ an. Die Webseite bleibt als zweiter Eintrag.
  const SCHULMANAGER_APP = 'shortcuts://run-shortcut?name=Schulmanager';
  const SCHULMANAGER_WEB = 'https://login.schulmanager-online.de/';

  let menue = null;              // { karte, uri, teile: [scrim, menue] }

  function menueSchliessen(weich = true) {
    if (!menue) return;
    const m = menue;
    menue = null;
    document.removeEventListener('keydown', m.taste);
    m.karte.classList.remove('offen');
    const [scrim, glas] = m.teile;
    scrim.remove();
    if (!weich) { glas.remove(); return; }
    glas.classList.add('zu');
    setTimeout(() => glas.remove(), 200);
  }

  function glasMenue(karte, liste, x, y) {
    menueSchliessen(false);
    const scrim = el('div', 'glas-scrim');
    scrim.addEventListener('click', () => menueSchliessen());
    const glas = el('div', 'glas-menue');
    glas.setAttribute('role', 'menu');
    liste.forEach((e, i) => {
      // Ein echter Link: iOS öffnet die App nach einem Tipp am zuverlässigsten über <a href="spotify:…">.
      const a = el('a', 'glas-eintrag');
      a.href = e.uri;
      a.setAttribute('role', 'menuitem');
      a.style.setProperty('--i', String(i));
      if (/^https?:/.test(e.uri)) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
      a.append(symbol('oeffnen'), el('span', '', e.text));
      a.addEventListener('click', () => setTimeout(() => menueSchliessen(), 180));
      glas.appendChild(a);
    });
    document.body.append(scrim, glas);
    // Unter den Tippunkt setzen, im Fenster halten (über der Leiste unten).
    const b = Math.min(270, window.innerWidth - 28), h = glas.offsetHeight;
    const links = Math.max(14, Math.min(x - b / 2, window.innerWidth - b - 14));
    const oben = Math.max(14, Math.min(y + 14, window.innerHeight - h - 90));
    glas.style.width = `${b}px`;
    glas.style.left = `${links}px`;
    glas.style.top = `${oben}px`;
    glas.style.transformOrigin = `${x - links}px ${y - oben}px`;   // wächst aus der Stelle, die man angetippt hat
    karte.classList.add('offen');
    const taste = (e) => { if (e.key === 'Escape') menueSchliessen(); };
    document.addEventListener('keydown', taste);
    menue = { karte, uri: '', teile: [scrim, glas], taste };
    const erster = glas.querySelector('a');
    if (erster && !x) erster.focus();
  }

  // Antippen einer Schulkarte (Stunde/Pause oder Stundenplan): Menü mit der App und der Webseite.
  function schulmanagerMenue(karte, ev) {
    if (ev.target.closest('.karte-knopf')) return;
    const r = karte.getBoundingClientRect();
    const x = ev.clientX || r.left + r.width / 2, y = ev.clientY || r.top + r.height / 2;
    glasMenue(karte, [{ uri: SCHULMANAGER_APP, text: 'Schulmanager öffnen', fest: true },
                      { uri: SCHULMANAGER_WEB, text: 'Im Browser öffnen', fest: true }], x, y);
  }
  function antippbar(karte, beiTipp) {
    karte.classList.add('mit-menue');
    karte.tabIndex = 0;
    karte.setAttribute('role', 'button');
    karte.setAttribute('aria-haspopup', 'menu');
    karte.addEventListener('click', beiTipp);
    karte.addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.key === ' ') && e.target === karte) { e.preventDefault(); beiTipp(e); }
    });
  }

  /* ---------- Zeichner: art → (karte) => { el, update(karte), tick(ms) } ---------- */

  const ZEICHNER = {};

  // Meldung: Push, den Eike schon kennt — hier noch einmal, bis er sie gesehen hat.
  ZEICHNER.meldung = (k) => {
    const wurzel = el('article', 'karte meldung');
    const titel = el('h2', 'karte-titel'), text = el('p', 'karte-text'), zeit = el('span', 'karte-zeit');
    const zu = knopf('zu', 'Meldung ausblenden', () => ausblenden(k.id));
    zu.classList.add('karte-zu');
    wurzel.append(zu, titel, text, zeit);
    const z = {
      el: wurzel, art: 'meldung', beobachten: true,
      update(n) {
        k = n;
        titel.textContent = n.titel; text.textContent = n.text; text.hidden = !n.text;
        zeit.textContent = vorZeit(n.zeit);
        // „/“ und die Übersicht selbst (alte Push-Links: /#sprechen) sind kein Ziel — man ist ja schon da.
        const ziel = n.url && !['/', '/#sprechen', '/#uebersicht', '#uebersicht'].includes(n.url) ? n.url : '';
        wurzel.classList.toggle('mit-ziel', !!ziel);
        wurzel.onclick = ziel ? (e) => { if (!e.target.closest('.karte-zu')) location.href = ziel; } : null;
      },
      tick() { zeit.textContent = vorZeit(k.zeit); },
    };
    z.update(k);
    return z;
  };

  // Von Friday gelegt: Titel, Text, optional ein Bild.
  ZEICHNER.friday = (k) => {
    const wurzel = el('article', 'karte friday');
    const bild = el('img', 'karte-bild'); bild.alt = ''; bild.loading = 'lazy';
    const titel = el('h2', 'karte-titel'), text = el('p', 'karte-text'), bis = el('span', 'karte-zeit');
    const datei = el('a', 'karte-datei');
    const zu = knopf('zu', 'Karte ausblenden', () => ausblenden(k.id));
    zu.classList.add('karte-zu');
    wurzel.append(zu, bild, titel, text, datei, bis);
    const z = {
      el: wurzel, art: 'friday',
      update(n) {
        k = n;
        titel.textContent = n.titel; text.textContent = n.text; text.hidden = !n.text;
        // Eine Datei zum Herunterladen (Ausarbeitung eines Aufgabenblatts): nur Adressen auf Friday selbst.
        const url = n.datei && typeof n.datei.url === 'string' && /^\/api\/aufgabenblatt\/\d+\/ausarbeitung$/.test(n.datei.url) ? n.datei.url : '';
        datei.hidden = !url;
        if (url) { datei.href = url; datei.setAttribute('download', ''); datei.textContent = `Herunterladen: ${n.datei.titel || 'Datei'}`; }
        // Wie lange die Karte noch steht — damit nichts Veraltetes unbemerkt stehen bleibt.
        bis.textContent = n.bis ? `bis ${uhr(Date.parse(n.bis))}` : ''; bis.hidden = !n.bis;
        const ok = bildOk(n.bild);
        bild.hidden = !ok;
        if (ok && bild.getAttribute('src') !== n.bild) bild.src = n.bild;
      },
      tick() {},
    };
    z.update(k);
    return z;
  };

  // Regeln (Eike, 15.10.2026): was Friday für seine Regeln getan hat. Die Karte bleibt immer stehen; einzelne Einträge lassen sich als gesehen markieren.
  const REGEL_STATUS = { ok: ['erledigt', 'ok'], fehler: ['Fehler', 'fehler'], gedrosselt: ['gedrosselt', 'ruhig'], uebersprungen: ['übersprungen', 'ruhig'], laeuft: ['läuft …', 'ruhig'] };
  ZEICHNER.regeln = (k) => {
    const wurzel = el('article', 'karte regeln');
    const titel = el('h2', 'karte-titel', 'Regeln'), kopf = el('p', 'karte-text'), regeln = el('ul', 'regel-liste'), liste = el('ul', 'regel-laeufe');
    wurzel.append(titel, kopf, regeln, liste);
    const z = {
      el: wurzel, art: 'regeln',
      update(n) {
        k = n;
        const teile = [`${n.aktiv} aktiv`];
        if (n.pausiert) teile.push(`${n.pausiert} pausiert (${(n.pausiert_namen || []).join(', ')})`);
        kopf.textContent = teile.join(' · ');
        regeln.replaceChildren();
        for (const r of n.regeln || []) {
          const li = el('li', 'regel-zeile');
          li.append(el('strong', '', r.name), el('span', 'regel-wann', `${r.wann}${r.status === 'aktiv' ? '' : ' · ' + r.status}`));
          const weg = el('button', 'regel-loeschen', 'Löschen');
          weg.type = 'button';
          weg.onclick = async () => {
            if (!confirm(`Regel „${r.name}“ wirklich löschen? Auch ihre Läufe sind dann weg.`)) return;
            try {
              const antwort = await fetch(`/api/regeln/${r.id}/loeschen`, { method: 'POST' });
              if (antwort.ok || antwort.status === 404) li.remove();
            } catch (_) {}
          };
          li.append(weg);
          regeln.append(li);
        }
        liste.replaceChildren();
        const offen = (n.laeufe || []).filter((l) => !l.gesehen);
        if (!offen.length) {
          const leer = el('li', 'regel-lauf leer', n.aktiv ? 'Nichts Neues.' : 'Keine Regeln aktiv.');
          liste.append(leer);
          return;
        }
        for (const l of offen) {
          const [zeichen, klasse] = REGEL_STATUS[l.status] || [l.status, 'ruhig'];
          const li = el('li', `regel-lauf ${klasse}${l.gesehen ? ' gesehen' : ''}`);
          const kopfzeile = el('div', 'regel-kopf');
          kopfzeile.append(el('strong', '', l.regel), el('span', 'regel-status', zeichen), el('span', 'karte-zeit', vorZeit(l.zeit)));
          li.append(kopfzeile, el('div', 'regel-ausloeser', l.ausloeser));
          for (const a of l.aktionen || []) li.append(el('div', 'regel-aktion', '→ ' + a));
          if (!l.gesehen) {
            const b = el('button', 'regel-gesehen', 'Gesehen');
            b.type = 'button';
            b.onclick = async () => { await gesehenMelden(l.id); l.gesehen = true; li.classList.add('gesehen'); b.remove(); };
            li.append(b);
          }
          liste.append(li);
        }
      },
      tick() {},
    };
    z.update(k);
    return z;
  };

  // Minecraft (21.10.2026): Spielstand aus der Mod `fridaycard`; gezeichnet in minecraft.js.
  ZEICHNER.minecraft = (k) => {
    const m = window.FridayMC.bauen(k);
    const z = { el: m.el, art: 'minecraft', update(n) { k = n; m.update(n); }, tick() {} };
    z.update(k);
    return z;
  };

  // Ein Bild, das Friday gezeigt hat (aus dem Gedächtnis oder eine Kopie aus dem Netz), groß mit Titel und Quelle.
  // Die Adresse ist immer eine von Fridays eigenen Routen (bildOk), nie ein fremder Server.
  ZEICHNER.bild = (k) => {
    const wurzel = el('article', 'karte bildkarte');
    const zu = knopf('zu', 'Bild ausblenden', () => ausblenden(k.id));
    zu.classList.add('karte-zu');
    const a = el('a', 'bildkarte-link'); a.target = '_blank'; a.rel = 'noopener';
    const img = el('img', 'bildkarte-bild'); img.alt = ''; img.loading = 'lazy';
    a.append(img);
    const unten = el('div', 'bildkarte-unten');
    const titel = el('h2', 'karte-titel'), quelle = el('p', 'karte-hinweis'), zeit = el('span', 'karte-zeit');
    const q = el('a', 'bildkarte-quelle', ' Quelle'); q.target = '_blank'; q.rel = 'noopener noreferrer';
    quelle.append(document.createTextNode(''), q);
    const text = el('p', 'karte-text');
    unten.append(titel, text, quelle, zeit);
    wurzel.append(a, zu, unten);
    const z = {
      el: wurzel, art: 'bild',
      update(n) {
        k = n;
        titel.textContent = n.titel;
        text.textContent = n.text || ''; text.hidden = !n.text;
        const ok = bildOk(n.bild, n.vorschau);
        a.hidden = !ok;
        if (ok && img.getAttribute('src') !== n.bild) { img.src = n.bild; a.href = n.bild; }
        const teile = [n.quelle, n.lizenz, n.urheber].filter(Boolean).join(' · ');
        quelle.firstChild.textContent = teile;
        const link = typeof n.link === 'string' && /^https:\/\//.test(n.link) ? n.link : '';
        q.hidden = !link; if (link) q.href = link;
        quelle.hidden = !teile && !link;
        zeit.textContent = n.bis ? `bis ${uhr(Date.parse(n.bis))}` : '';
      },
      tick() {},
    };
    z.update(k);
    return z;
  };

  // Verbindungskarte: Wege zu einem Ziel — Bus & Bahn (GVH, Tür zu Tür), zu Fuß, Auto. Der Server rechnet, hier wird nur gezeigt.
  // Antippen: Fuß- und Autoweg in Google Maps öffnen (App, sonst Browser). Die Adressen entstehen im Code aus geprüften Zahlen.
  const koordOk = (o) => o && Number.isFinite(o.breite) && Number.isFinite(o.laenge) && Math.abs(o.breite) <= 90 && Math.abs(o.laenge) <= 180;
  const punkt = (o) => `${o.breite.toFixed(6)},${o.laenge.toFixed(6)}`;
  const MAPS_MODUS = { fuss: 'walking', auto: 'driving' };
  const mapsEintraege = (k, modus) => {
    if (!koordOk(k.start) || !koordOk(k.ziel) || !MAPS_MODUS[modus]) return [];
    const wie = modus === 'fuss' ? 'Zu Fuß' : 'Mit dem Auto';
    return [
      { uri: `comgooglemaps://?saddr=${punkt(k.start)}&daddr=${punkt(k.ziel)}&directionsmode=${modus === 'fuss' ? 'walking' : 'driving'}`,
        text: `${wie} in Google Maps`, fest: true },
      { uri: `https://www.google.com/maps/dir/?api=1&origin=${punkt(k.start)}&destination=${punkt(k.ziel)}&travelmode=${MAPS_MODUS[modus]}`,
        text: `${wie} im Browser (Google Maps)`, fest: true },
    ];
  };
  const minText = (m) => { m = Math.max(0, Math.round(m)); const h = Math.floor(m / 60); return h ? `${h} h ${zweistellig(m % 60)} min` : `${m} min`; };
  const ROUTE_ICON = { oepnv: '🚌', fuss: '🚶', auto: '🚗' };
  const ABSCHNITT_ICON = { fuss: '🚶', bus: '🚌', tram: '🚋', ubahn: '🚇', sbahn: '🚆', zug: '🚆', schiff: '⛴️', bahn: '🚆' };
  const kmText = (m) => (m < 950 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(1).replace('.', ',')} km`);

  ZEICHNER.route = (k) => {
    const wurzel = el('article', 'karte route');
    const zu = knopf('zu', 'Verbindungen ausblenden', () => ausblenden(k.id));
    zu.classList.add('karte-zu');
    const titel = el('h2', 'karte-titel'), von = el('p', 'karte-hinweis route-von'), live = el('p', 'route-live');
    const liste = el('div', 'route-liste'), hinweise = el('div', 'route-hinweise'), tipp = el('p', 'karte-hinweis route-tipp');
    wurzel.append(zu, titel, von, live, liste, hinweise, tipp);
    const abzeiten = [];

    function menueFuer(modus, ev) {
      const eintraege = modus ? mapsEintraege(k, modus)
        : k.optionen.map((o) => o.modus).filter((m, i, a) => MAPS_MODUS[m] && a.indexOf(m) === i).flatMap((m) => mapsEintraege(k, m));
      if (!eintraege.length) return;
      const r = wurzel.getBoundingClientRect();
      const x = ev.clientX || r.left + r.width / 2, y = ev.clientY || r.top + r.height / 2;
      glasMenue(wurzel, eintraege, x, y);
    }
    wurzel.addEventListener('click', (ev) => { if (!ev.target.closest('.karte-knopf')) menueFuer('', ev); });

    const z = {
      el: wurzel, art: 'route',
      update(n) {
        k = n;
        titel.textContent = n.titel || `Nach ${n.ziel?.name || 'Ziel'}`;
        von.textContent = `Von ${n.start?.name || 'deinem Standort'}`;
        wurzel.classList.toggle('angekommen', !!n.angekommen);
        wurzel.classList.toggle('veraltet', !!n.veraltet);
        abzeiten.length = 0;
        liste.hidden = !!n.angekommen;
        liste.replaceChildren(...(n.angekommen ? [] : n.optionen || []).map((o, i) => {
          const zeile = el('div', `route-opt ${o.modus}${i === n.schnellste ? ' schnellste' : ''}`);
          const kopf = el('div', 'route-kopf');
          const links = el('div', 'route-links');
          links.append(el('span', 'route-icon', ROUTE_ICON[o.modus] || '•'), el('span', 'route-name', o.titel || o.modus));
          if (i === n.schnellste) links.append(el('span', 'route-marke', 'Früheste Ankunft'));
          const rechts = el('div', 'route-rechts');
          rechts.append(el('span', 'route-dauer', minText(o.dauer_min)), el('span', 'route-an', o.an ? `an ${o.an}` : ''));
          kopf.append(links, rechts);
          zeile.append(kopf);
          if (o.modus === 'oepnv') {
            const kette = el('div', 'route-kette');
            (o.abschnitte || []).forEach((a) => {
              const c = el('span', `route-chip ${a.art}`, `${ABSCHNITT_ICON[a.art] || '•'} ${a.art === 'fuss' ? `${a.min} Min` : a.linie}`);
              c.title = a.art === 'fuss' ? `${a.von} → ${a.nach}${a.meter ? ` (${a.meter} m)` : ''}` : `${a.linie} ${a.richtung}: ${a.ab} ${a.von} → ${a.an} ${a.nach}`;
              kette.append(c);
            });
            const info = el('div', 'route-info');
            const ab = el('span', '', ''); abzeiten.push({ ab, o });
            const versp = o.verspaetung_min > 0 ? el('span', 'route-versp', `Ankunft +${o.verspaetung_min} Min`) : null;
            info.append(ab, document.createTextNode(` · ${o.umstiege ? `${o.umstiege} Umstieg${o.umstiege > 1 ? 'e' : ''}` : 'ohne Umstieg'} · ${o.echtzeit ? 'Echtzeit' : 'Fahrplan'}`));
            if (versp) info.append(document.createTextNode(' · '), versp);
            zeile.append(kette, info);
          } else {
            zeile.append(el('div', 'route-info', [kmText(o.distanz_m || 0), o.hinweis].filter(Boolean).join(' · ')));
            if (MAPS_MODUS[o.modus] && koordOk(n.start) && koordOk(n.ziel)) {
              zeile.classList.add('mit-maps');
              zeile.append(el('div', 'route-maps', 'Tippen: in Google Maps öffnen'));
              zeile.addEventListener('click', (ev) => { ev.stopPropagation(); menueFuer(o.modus, ev); });
            }
          }
          return zeile;
        }));
        hinweise.replaceChildren(
          ...(n.ausfaelle || []).map((a) => el('p', 'route-ausfall', a.text)),
          ...(n.hinweise || []).map((h) => el('p', 'route-hinweis', h)));
        const maps = (n.optionen || []).some((o) => MAPS_MODUS[o.modus]) && koordOk(n.start) && koordOk(n.ziel);
        tipp.textContent = maps ? 'Tipp auf Zu Fuß oder Auto öffnet den Weg in Google Maps.' : '';
        tipp.hidden = !maps;
        wurzel.classList.toggle('mit-menue', maps);
        z.tick(jetzt());
      },
      tick(ms) {
        // Live: der Server rechnet die Karte laufend neu (Standort, Abfahrten, Verspätungen); `stand` ist der Zeitpunkt der letzten Rechnung.
        const stand = Date.parse(k.stand || k.zeit);
        const vor = Math.max(0, Math.round((ms - stand) / 1000));
        live.classList.toggle('warn', !!k.veraltet);
        live.textContent = k.angekommen ? 'Du bist am Ziel.'
          : k.veraltet ? 'Nicht aktuell — Standort oder Auskunft antwortet gerade nicht.'
          : `Live · vor ${vor < 90 ? `${vor} s` : `${Math.round(vor / 60)} min`} aktualisiert`;
        const vergangen = Math.max(0, (ms - stand) / 60000);
        for (const { ab, o } of abzeiten) {
          const noch = o.ab_in_min === null || o.ab_in_min === undefined ? null : Math.max(0, o.ab_in_min - vergangen);
          ab.textContent = `Abfahrt ${o.ab}${noch === null ? '' : noch < 1 ? ' (jetzt)' : ` (in ${minText(noch)})`}`;
        }
      },
    };
    z.update(k);
    return z;
  };

  // Schule: Stunde/Pause mit Countdown und Fortschritt. Zeitpunkte kommen vom Kern, gezählt wird hier.
  const SCHULE_KOPF = { unterricht: 'Im Unterricht', pause: 'Pause', freistunde: 'Freistunde', vor_schule: 'Gleich geht’s los' };
  const SCHULE_BIS = { unterricht: 'bis zur Pause', pause: 'bis zur nächsten Stunde', freistunde: 'bis zur nächsten Stunde', vor_schule: 'bis zur ersten Stunde' };
  function stundenText(s) {
    return [s.raum && `Raum ${s.raum}`, s.lehrer].filter(Boolean).join(' · ');
  }
  ZEICHNER.schule = (k) => {
    const wurzel = el('article', 'karte schule');
    const marke = el('div', 'karte-marke'), fach = el('h2', 'schule-fach'), wo = el('p', 'karte-text');
    const zahl = el('div', 'schule-zeit'), bis = el('div', 'schule-bis');
    const balken = el('div', 'fortschritt'), fuellung = el('div', 'fortschritt-fuellung');
    balken.appendChild(fuellung);
    const danach = el('p', 'schule-danach'), extra = el('p', 'karte-hinweis');
    wurzel.append(marke, fach, wo, zahl, bis, balken, danach, extra);
    antippbar(wurzel, (ev) => schulmanagerMenue(wurzel, ev));
    const z = {
      el: wurzel, art: 'schule',
      update(n) {
        k = n;
        const art = n.zustand === 'pause' && n.art_pause === 'freistunde' ? 'freistunde' : n.zustand;
        wurzel.dataset.zustand = art;
        marke.textContent = SCHULE_KOPF[art] || 'Schule';
        const jetztStunde = n.aktuell, naechste = n.naechste;
        const haupt = jetztStunde || naechste;
        fach.textContent = haupt ? haupt.fach : '';
        wo.textContent = haupt ? stundenText(haupt) : '';
        wo.hidden = !wo.textContent;
        bis.textContent = SCHULE_BIS[art] || '';
        danach.hidden = !(jetztStunde && naechste);
        if (jetztStunde && naechste) danach.textContent = `Danach: ${naechste.fach}${naechste.raum ? ' · ' + naechste.raum : ''} · ${uhr(Date.parse(naechste.von))}`;
        if (!jetztStunde && naechste) fach.textContent = `Als Nächstes: ${naechste.fach}`;
        extra.textContent = n.hinweis || ''; extra.hidden = !n.hinweis;
        z.tick(jetzt());
      },
      tick(ms) {
        const von = Date.parse(k.von), ende = Date.parse(k.bis);
        zahl.textContent = restzeit(ende - ms);
        fuellung.style.width = `${Math.min(100, Math.max(0, ((ms - von) / (ende - von)) * 100))}%`;
      },
    };
    z.update(k);
    return z;
  };

  // Musik: Cover, Titel, Fortschritt, Steuerung. Der Fortschritt läuft lokal weiter.
  // Herkunftsmarke in der Kartenecke: Logo und Name des Dienstes (Spotify, Jellyfin, Windows, PC-Medien).
  function logo(dienst) {
    const svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    const pfad = (d, attr) => {
      const p = document.createElementNS(SVG, 'path');
      p.setAttribute('d', d);
      for (const [k, v] of Object.entries(attr)) p.setAttribute(k, v);
      svg.appendChild(p);
    };
    if (dienst === 'spotify') {
      const c = document.createElementNS(SVG, 'circle');
      c.setAttribute('cx', '12'); c.setAttribute('cy', '12'); c.setAttribute('r', '11'); c.setAttribute('fill', '#1DB954');
      svg.appendChild(c);
      for (const d of ['M6 9.2c4-1.1 8.3-.8 12 1.2', 'M6.7 12.7c3.4-.9 7-.6 10.1 1.1', 'M7.4 16c2.7-.7 5.4-.4 7.9.9']) {
        pfad(d, { fill: 'none', stroke: '#0a0d12', 'stroke-width': '1.9', 'stroke-linecap': 'round' });
      }
    } else if (dienst === 'medien') {
      pfad('M4 3h16a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z', { fill: '#F5803E' });
      pfad('M10 8.2v7.6l6.2-3.8z', { fill: '#0a0d12' });
    } else if (dienst === 'windows') {
      for (const d of ['M2 2h9v9H2z', 'M13 2h9v9h-9z', 'M2 13h9v9H2z', 'M13 13h9v9h-9z']) pfad(d, { fill: '#00A4EF' });
    } else {
      pfad('M12 1.5 22.5 21H1.5z', { fill: '#AA5CC3' });
      pfad('M12 8.2 17 17.5H7z', { fill: '#00A4DC' });
    }
    return svg;
  }
  function quellenmarke(dienst, name) {
    const m = el('div', 'quelle');
    m.append(logo(dienst), el('span', '', name));
    return m;
  }

  ZEICHNER.musik = (k) => {
    const wurzel = el('article', 'karte musik');
    const cover = el('img', 'cover'); cover.alt = 'Cover';
    // Lädt das Bild nicht (Spotify-CDN gesperrt, Adresse abgelaufen), bleibt die Karte ohne Cover statt mit kaputtem Bild.
    let kaputt = '';
    cover.addEventListener('error', () => { kaputt = cover.getAttribute('src') || ''; cover.hidden = true; wurzel.classList.add('ohne-cover'); });
    const titel = el('h2', 'karte-titel musik-titel'), wer = el('p', 'karte-text'), geraet = el('p', 'karte-hinweis');
    const balken = el('div', 'fortschritt'), fuellung = el('div', 'fortschritt-fuellung');
    balken.appendChild(fuellung);
    const zeiten = el('div', 'musik-zeiten'), pos = el('span'), dauer = el('span');
    zeiten.append(pos, dauer);
    const regler = el('div', 'musik-regler');
    // Drei Knöpfe, die Mitte größer: so sitzt die Reihe mittig und beide Richtungen sind da.
    const zurueck = knopf('zurueck', 'Vorheriger Titel', () => befehl('vorheriger'));
    const mitte = knopf('pause', 'Pause', () => befehl(k.laeuft ? 'pause' : 'weiter'));
    mitte.classList.add('haupt');
    const weiter = knopf('weiter', 'Nächster Titel', () => befehl('naechster'));
    regler.append(zurueck, mitte, weiter);
    const text = el('div', 'musik-text'); text.append(titel, wer, geraet);
    const kopf = el('div', 'musik-kopf'); kopf.append(cover, text);
    wurzel.append(kopf, balken, zeiten, regler, quellenmarke('spotify', 'Spotify'));

    async function befehl(name) {
      if (k.vorschau) return;                      // Testkarte: nie wirklich Spotify steuern
      await melden(`/api/dashboard/musik/${name}`);
      setTimeout(abfragen, 600);
    }

    // Antippen der Karte (außer auf die Knöpfe) öffnet ein kleines Glas-Menü: direkt in die Spotify-App.
    function eintraege() {
      const art = (u) => (u || '').split(':')[1];
      return [
        // Ganz oben: die App selbst. Feste Adresse im Code, nie aus Daten.
        { uri: SPOTIFY_APP, text: 'Spotify öffnen', fest: true },
        { uri: k.uri, text: art(k.uri) === 'episode' ? 'Folge in Spotify' : 'In Spotify öffnen' },
        { uri: k.album_uri, text: art(k.album_uri) === 'show' ? 'Show öffnen' : 'Album öffnen' },
        { uri: k.artist_uri, text: 'Künstler öffnen' },
      ].filter((e) => e.fest || spotifyOk(e.uri));
    }
    function menueAuf(ev) {
      if (ev.target.closest('.karte-knopf')) return;
      const liste = eintraege();
      const r = wurzel.getBoundingClientRect();
      const x = ev.clientX || r.left + r.width / 2, y = ev.clientY || r.top + r.height / 2;
      glasMenue(wurzel, liste, x, y);
      if (menue) menue.uri = k.uri;
    }
    wurzel.addEventListener('click', menueAuf);
    wurzel.tabIndex = 0;
    wurzel.setAttribute('role', 'button');
    wurzel.setAttribute('aria-haspopup', 'menu');
    wurzel.addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.key === ' ') && e.target === wurzel) { e.preventDefault(); menueAuf(e); }
    });
    const mm = (ms) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${zweistellig(s % 60)}`; };

    const z = {
      el: wurzel, art: 'musik',
      update(n) {
        k = n;
        titel.textContent = n.titel; wer.textContent = n.wer; wer.hidden = !n.wer;
        geraet.textContent = n.geraet ? `auf ${n.geraet}${n.laeuft ? '' : ' · pausiert'}` : (n.laeuft ? '' : 'pausiert');
        geraet.hidden = !geraet.textContent;
        const ok = bildOk(n.cover, n.vorschau) && n.cover !== kaputt;
        cover.hidden = !ok;
        if (ok && cover.getAttribute('src') !== n.cover) {
          cover.src = n.cover;
          wurzel.style.setProperty('--cover', `url("${n.cover}")`);
        }
        wurzel.classList.toggle('ohne-cover', !ok);
        wurzel.classList.toggle('pausiert', !n.laeuft);
        wurzel.classList.add('mit-menue');
        if (menue && menue.karte === wurzel && menue.uri !== n.uri) menueSchliessen(false);   // anderer Titel: Menü passt nicht mehr
        regler.hidden = !n.steuerbar;
        mitte.replaceChildren(symbol(n.laeuft ? 'pause' : 'play'));
        mitte.setAttribute('aria-label', n.laeuft ? 'Pause' : 'Weiter');
        dauer.textContent = mm(n.dauer_ms);
        z.tick(jetzt());
      },
      tick(ms) {
        const gelaufen = k.laeuft ? Math.max(0, ms - Date.parse(k.stand)) : 0;
        const p = Math.min(k.dauer_ms, k.position_ms + gelaufen);
        pos.textContent = mm(p);
        fuellung.style.width = `${k.dauer_ms ? (p / k.dauer_ms) * 100 : 0}%`;
      },
    };
    z.update(k);
    return z;
  };

  // PC: läuft er, seit wann, was ist offen — und Schnellknöpfe. Heikles fragt in der Karte selbst nach.
  const dauerText = (s) => {
    const min = Math.floor(s / 60), h = Math.floor(min / 60);
    return h ? `${h} h ${zweistellig(min % 60)} min` : `${min} min`;
  };
  async function pcSenden(pfad, daten) {
    try {
      const r = await fetch(pfad, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                    body: daten ? JSON.stringify(daten) : undefined });
      return await r.json();
    } catch (_) { return { ok: false, text: 'Friday ist nicht erreichbar.' }; }
  }
  ZEICHNER.pc = (k) => {
    const wurzel = el('article', 'karte pc');
    const kopf = el('div', 'pc-kopf'), punkt = el('span', 'pc-punkt');
    const titel = el('h2', 'karte-titel'), zeile = el('p', 'karte-text');
    const text = el('div', 'pc-text'); text.append(titel, zeile);
    kopf.append(punkt, text);
    // Tippen auf den Kopf führt in den PC-Bereich (Extras → PC).
    kopf.classList.add('mit-ziel');
    kopf.addEventListener('click', () => { if (!k.vorschau) location.hash = '#studio/pc'; });
    const fenster = el('div', 'pc-fenster');
    const rueck = el('div', 'pc-rueck');
    const knoepfe = el('div', 'pc-knoepfe'), meldung = el('p', 'karte-hinweis');
    meldung.hidden = true;
    const zeigen = (r) => { meldung.textContent = r.text || ''; meldung.hidden = !meldung.textContent; };
    const aktion = (symbolName, beschriftung, pfad, daten) => {
      const b = el('button', 'pc-aktion');
      b.type = 'button';
      b.append(symbol(symbolName), el('span', '', beschriftung));
      b.addEventListener('click', async () => {
        if (k.vorschau) return;                          // Testkarte: nie den echten PC anfassen
        b.disabled = true;
        zeigen(await pcSenden(pfad, daten));
        b.disabled = false;
        abfragen();                                      // die Rückfrage soll sofort in der Karte stehen
      });
      return b;
    };
    knoepfe.append(aktion('sperren', 'Sperren', '/api/pc/schnell', { aktion: 'sperren' }),
                   aktion('stumm', 'Ton aus', '/api/pc/schnell', { aktion: 'stumm' }),
                   aktion('mond', 'Energiesparen', '/api/pc/energiesparen'),
                   aktion('power', 'Herunterfahren', '/api/pc/herunterfahren'));
    const update = el('p', 'karte-hinweis pc-update');
    wurzel.append(kopf, fenster, rueck, knoepfe, meldung, update, quellenmarke('windows', 'Windows'));

    function rueckfragen(liste) {
      rueck.replaceChildren(...liste.map((o) => {
        const box = el('div', 'pc-frage');
        const ja = el('button', 'pc-ja', 'Ausführen'), nein = el('button', 'pc-nein', 'Ablehnen');
        ja.type = nein.type = 'button';
        const entscheiden = (antwort) => async (ev) => {
          ev.currentTarget.disabled = true;
          if (k.vorschau) return;
          zeigen(await pcSenden(`/api/pc/offen/${encodeURIComponent(o.id)}`, { ja: antwort }));
          abfragen();
        };
        ja.addEventListener('click', entscheiden(true)); nein.addEventListener('click', entscheiden(false));
        const t = el('div', 'pc-frage-text'); t.append(el('b', '', 'Bestätigen?'), el('span', '', o.beschreibung));
        box.append(t, nein, ja);
        return box;
      }));
      rueck.hidden = !liste.length;
    }

    const z = {
      el: wurzel, art: 'pc',
      update(n) {
        k = n;
        titel.textContent = `PC · ${n.rechner}`;
        wurzel.classList.toggle('gesperrt', !!n.gesperrt);
        fenster.replaceChildren(...(n.fenster || []).map((f) => {
          const c = el('span', 'pc-chip', f.programm); c.title = f.titel || ''; return c;
        }));
        fenster.hidden = !(n.fenster || []).length;
        rueckfragen(n.offen || []);
        knoepfe.hidden = !n.schreiben;
        // Der Helfer am PC ist älter als der, den dieser Friday mitbringt: Neues (Laufzeit, Programme, Wiedergabe) fehlt noch.
        update.textContent = n.helfer_neu ? `Neuer PC-Helfer ${n.helfer_neu} verfügbar — Freigaben → PC → „Einrichten“, den Befehl am PC ausführen.` : '';
        update.hidden = !n.helfer_neu;
        z.tick(jetzt());
      },
      tick(ms) {
        const teile = [];
        if (k.laufzeit_s != null) teile.push(`an seit ${dauerText(k.laufzeit_s + Math.max(0, (ms - Date.parse(k.stand)) / 1000))}`);
        teile.push(k.gesperrt ? 'gesperrt' : 'entsperrt');
        zeile.textContent = teile.join(' · ');
      },
    };
    z.update(k);
    return z;
  };

  // Medienkarte (Jellyfin, PC-Wiedergabe): wie die Musikkarte — Bild als Schleier, Fortschritt, drei Knöpfe.
  const hms = (s) => {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h}:${zweistellig(m)}:${zweistellig(s % 60)}` : `${m}:${zweistellig(s % 60)}`;
  };
  // o: { art, pfad (Befehlsroute), logo, marke(n) → Name, sub(n), geraet(n) }
  function medienkarte(k, o) {
    const wurzel = el('article', `karte musik ${o.art}`);
    const bild = el('img', 'cover'); bild.alt = 'Vorschau';
    let kaputt = '';
    bild.addEventListener('error', () => { kaputt = bild.getAttribute('src') || ''; bild.hidden = true; wurzel.classList.add('ohne-cover'); });
    const titel = el('h2', 'karte-titel musik-titel'), sub = el('p', 'karte-text'), geraet = el('p', 'karte-hinweis');
    const balken = el('div', 'fortschritt'), fuellung = el('div', 'fortschritt-fuellung');
    balken.appendChild(fuellung);
    const zeiten = el('div', 'musik-zeiten'), pos = el('span'), dauer = el('span');
    zeiten.append(pos, dauer);
    const regler = el('div', 'musik-regler');
    const zurueck = knopf('zurueck', 'Zurück', () => befehl('vorheriger'));
    const mitte = knopf('pause', 'Pause', () => befehl(k.laeuft ? 'pause' : 'weiter'));
    mitte.classList.add('haupt');
    const weiter = knopf('weiter', 'Weiter', () => befehl('naechster'));
    regler.append(zurueck, mitte, weiter);
    const text = el('div', 'musik-text'); text.append(titel, sub, geraet);
    const kopf = el('div', 'musik-kopf'); kopf.append(bild, text);
    const marke = quellenmarke(o.logo, o.marke(k)), markeText = marke.querySelector('span');
    wurzel.append(kopf, balken, zeiten, regler, marke);

    async function befehl(name) {
      if (k.vorschau) return;                      // Testkarte: nie wirklich etwas steuern
      await melden(`${o.pfad}${name}`);
      setTimeout(abfragen, 2500);                  // der Player meldet seinen neuen Stand erst nach ein, zwei Sekunden
    }

    const z = {
      el: wurzel, art: o.art,
      update(n) {
        k = n;
        titel.textContent = n.titel;
        const s2 = o.sub(n), g = o.geraet(n);
        sub.textContent = s2; sub.hidden = !s2;
        geraet.textContent = g; geraet.hidden = !g;
        markeText.textContent = o.marke(n);
        const ok = bildOk(n.bild, n.vorschau) && n.bild !== kaputt;
        bild.hidden = !ok;
        if (ok && bild.getAttribute('src') !== n.bild) { bild.src = n.bild; wurzel.style.setProperty('--cover', `url("${n.bild}")`); }
        wurzel.classList.toggle('ohne-cover', !ok);
        wurzel.classList.toggle('pausiert', !n.laeuft);
        regler.hidden = !n.steuerbar;
        balken.hidden = zeiten.hidden = !n.dauer_s;         // Livestreams kennen keine Länge
        mitte.replaceChildren(symbol(n.laeuft ? 'pause' : 'play'));
        mitte.setAttribute('aria-label', n.laeuft ? 'Pause' : 'Weiter');
        dauer.textContent = hms(n.dauer_s);
        z.tick(jetzt());
      },
      tick(ms) {
        const gelaufen = k.laeuft ? Math.max(0, (ms - Date.parse(k.stand)) / 1000) : 0;
        const p = Math.min(k.dauer_s, k.position_s + gelaufen);
        pos.textContent = hms(Math.floor(p));
        fuellung.style.width = `${k.dauer_s ? (p / k.dauer_s) * 100 : 0}%`;
      },
    };
    z.update(k);
    return z;
  }
  ZEICHNER.jellyfin = (k) => medienkarte(k, {
    art: 'jellyfin', pfad: '/api/dashboard/jellyfin/', logo: 'jellyfin', marke: () => 'Jellyfin',
    sub: (n) => n.untertitel || '',
    geraet: (n) => (n.geraet ? `auf ${n.geraet}${n.laeuft ? '' : ' · pausiert'}` : (n.laeuft ? '' : 'pausiert')),
  });
  // Was am PC läuft (YouTube im Browser, VLC …): die Marke nennt die App.
  ZEICHNER.pcmedien = (k) => medienkarte(k, {
    art: 'pcmedien', pfad: '/api/dashboard/pcmedien/', logo: 'medien', marke: (n) => n.app || 'PC',
    sub: (n) => n.kuenstler || '',
    geraet: (n) => (n.laeuft ? 'auf dem PC' : 'auf dem PC · pausiert'),
  });

  // Stundenplan eines Tages (von Friday gelegt, „zeig mir den Stundenplan für Montag“).
  // Jedes Fach bekommt seine Farbe (aus dem Namen gerechnet, also immer dieselbe); läuft der Tag gerade,
  // ist die aktuelle Stunde hervorgehoben und Vergangenes gedämpft.
  const farbeZu = (fach) => {
    let h = 0;
    for (const c of String(fach)) h = (h * 31 + c.charCodeAt(0)) % 360;
    return `hsl(${h} 72% 64%)`;
  };
  const tagIso = (ms) => new Date(ms).toLocaleDateString('sv-SE', { timeZone: ZEITZONE });   // YYYY-MM-DD
  ZEICHNER.stundenplan = (k) => {
    const wurzel = el('article', 'karte plan');
    const marke = el('div', 'karte-marke'), titel = el('h2', 'plan-tag'), sub = el('p', 'plan-sub');
    const liste = el('ol', 'plan-liste');
    const zu = knopf('zu', 'Stundenplan ausblenden', () => ausblenden(k.id));
    zu.classList.add('karte-zu');
    wurzel.append(zu, marke, titel, sub, liste);
    antippbar(wurzel, (ev) => schulmanagerMenue(wurzel, ev));
    let zeilen = [];
    const z = {
      el: wurzel, art: 'stundenplan',
      update(n) {
        k = n;
        const tag = new Date(`${n.datum}T12:00:00Z`);
        const heute = tagIso(jetzt()), morgen = tagIso(jetzt() + 86400000);
        marke.textContent = n.datum === heute ? 'Stundenplan · Heute' : n.datum === morgen ? 'Stundenplan · Morgen' : 'Stundenplan';
        titel.textContent = isNaN(tag) ? n.titel : tag.toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' });
        // Aufeinanderfolgende gleiche Stunden (Doppelstunde) werden eine Zeile: „1.–2.“, von der ersten bis zur letzten Zeit.
        const stunden = [];
        for (const s of n.stunden || []) {
          const l = stunden[stunden.length - 1];
          if (l && ['fach', 'raum', 'lehrer', 'status', 'hinweis'].every((f) => (l[f] || '') === (s[f] || ''))) {
            l.bis = s.bis || l.bis;
            if (s.nr && l.nr !== s.nr) l.nr = `${String(l.nr).split('–')[0]}–${s.nr}`;
            l.anzahl += 1;
          } else stunden.push({ ...s, anzahl: 1 });
        }
        const ausfall = stunden.filter((s) => s.status === 'ausfall').length, vertr = stunden.filter((s) => s.status === 'vertretung').length;
        const anzahl = stunden.reduce((a, s) => a + s.anzahl, 0);
        sub.textContent = [`${anzahl} Stunden`, ausfall && `${ausfall} entfällt`, vertr && `${vertr} Vertretung`].filter(Boolean).join(' · ');
        zeilen = stunden.map((s) => {
          const li = el('li', `plan-zeile ${s.status}`);
          li.style.setProperty('--fb', farbeZu(s.fach));
          const zeit = el('div', 'plan-zeit');
          if (s.von) zeit.append(el('b', '', s.von), el('span', '', s.bis));
          else zeit.append(el('b', '', s.nr ? `${s.nr}.` : '–'));
          const info = el('div', 'plan-info');
          const fach = el('div', 'plan-fach', s.fach || '?');
          if (s.status === 'ausfall') fach.append(el('span', 'plan-chip aus', 'Entfällt'));
          if (s.status === 'vertretung') fach.append(el('span', 'plan-chip vertretung', 'Vertretung'));
          info.appendChild(fach);
          const meta = [s.raum && `Raum ${s.raum}`, s.lehrer, s.hinweis].filter(Boolean).join(' · ');
          if (meta) info.appendChild(el('div', 'plan-meta', meta));
          li.append(zeit, el('div', 'plan-balken'), info);
          li.dataset.von = s.von || ''; li.dataset.bis = s.bis || '';
          return li;
        });
        liste.replaceChildren(...zeilen);
        z.tick(jetzt());
      },
      tick(ms) {
        // Nur am heutigen Tag gibt es „jetzt“ und „vorbei“.
        const heute = k.datum === tagIso(ms), jetztUhr = k.uhr_test || uhr(ms);   // uhr_test: nur die Vorschau
        for (const li of zeilen) {
          const von = li.dataset.von, bis = li.dataset.bis;
          li.classList.toggle('jetzt', heute && !!von && !!bis && von <= jetztUhr && jetztUhr < bis);
          li.classList.toggle('vorbei', heute && !!bis && bis <= jetztUhr);
        }
      },
    };
    z.update(k);
    return z;
  };

  // Leer: nichts Wichtiges — Uhrzeit und ein Satz statt einer leeren Fläche.
  ZEICHNER.leer = () => {
    const wurzel = el('article', 'karte leer');
    const zeit = el('div', 'leer-zeit'), satz = el('p', 'karte-text', 'Gerade nichts Wichtiges.');
    const tipp = el('p', 'karte-hinweis', 'Tipp auf den Kreis oben, um mit Friday zu sprechen.');
    wurzel.append(zeit, satz, tipp);
    return { el: wurzel, art: 'leer', update() {}, tick(ms) { zeit.textContent = uhr(ms); } };
  };

  // Unbekannte Art: schlichte Karte, damit eine neue Anbieter-Art nie unsichtbar bleibt.
  ZEICHNER._allgemein = (k) => {
    const wurzel = el('article', 'karte');
    const titel = el('h2', 'karte-titel'), text = el('p', 'karte-text');
    wurzel.append(titel, text);
    const z = { el: wurzel, art: k.art, update(n) { titel.textContent = n.titel || n.art; text.textContent = n.text || ''; text.hidden = !n.text; }, tick() {} };
    z.update(k);
    return z;
  };
  window.Friday.ZEICHNER = ZEICHNER;

  /* ---------- Abgleich ---------- */

  function entfernen(id, sofort) {
    const eintrag = karten.get(id);
    if (!eintrag) return;
    karten.delete(id);
    eintrag.beobachter && eintrag.beobachter.disconnect();
    clearTimeout(eintrag.timer);
    if (sofort) { eintrag.z.el.remove(); return; }
    eintrag.z.el.classList.add('raus');
    setTimeout(() => eintrag.z.el.remove(), 260);
  }

  function ausblenden(id) {
    if (vorschau) vorschau.karten = vorschau.karten.filter((k) => k.id !== id);
    gesehenMelden(id);
    entfernen(id, false);
    leerpruefen();
  }

  function beobachten(eintrag, id) {
    // Gesehen = die Karte war ≥ 2 s zu mindestens 60 % im Bild, bei sichtbarer Seite.
    if (!('IntersectionObserver' in window)) return;
    eintrag.beobachter = new IntersectionObserver((treffer) => {
      const im_bild = treffer[0].isIntersecting && document.visibilityState === 'visible';
      clearTimeout(eintrag.timer);
      if (im_bild && !eintrag.gesehen) {
        eintrag.timer = setTimeout(() => { eintrag.gesehen = true; gesehenMelden(id); }, GESEHEN_NACH_MS);
      }
    }, { threshold: 0.6 });
    eintrag.beobachter.observe(eintrag.z.el);
  }

  function leerpruefen() {
    const echte = [...karten.keys()].filter((i) => i !== '_leer');
    if (!echte.length && !leerKarte) {
      leerKarte = { z: ZEICHNER.leer(), gesehen: true };
      leerKarte.z.el.classList.add('neu');
      karten.set('_leer', leerKarte);
      behaelter.appendChild(leerKarte.z.el);
      leerKarte.z.tick(jetzt());
    } else if (echte.length && leerKarte) {
      entfernen('_leer', true);
      leerKarte = null;
    }
  }

  function abgleichen(neue) {
    const ids = new Set(neue.map((k) => k.id));
    for (const [id, e] of [...karten]) {
      if (id === '_leer') continue;
      // Eine gesehene Meldung bleibt stehen, bis die Seite verlassen wird — sonst
      // verschwände sie unter Eikes Augen, kaum dass er sie liest.
      if (!ids.has(id) && !e.gesehen) entfernen(id, false);
    }
    neue.forEach((k, i) => {
      let e = karten.get(k.id);
      if (e) e.z.update(k);
      else {
        const bauen = ZEICHNER[k.art] || ZEICHNER._allgemein;
        e = { z: bauen(k), gesehen: false };
        e.z.el.classList.add('neu');
        if (k.vorschau) e.z.el.classList.add('vorschau');
        e.z.el.addEventListener('animationend', () => e.z.el.classList.remove('neu'), { once: true });
        karten.set(k.id, e);
        if (e.z.beobachten) beobachten(e, k.id);
      }
      const an = behaelter.children[i];
      if (an !== e.z.el) behaelter.insertBefore(e.z.el, an || null);
    });
    leerpruefen();
  }

  /* ---------- Vorschau (Einstellungen → Übersicht testen) ----------
     Testkarten, nur im Browser gerechnet: nichts wird gespeichert oder gesendet. So sieht Eike, wie die
     Übersicht im Unterricht aussieht, ohne auf einen Schultag zu warten. Sie ersetzt echte Karten der
     gleichen Art und hält, bis er sie beendet oder die Seite neu lädt. */

  let vorschau = null;           // { name, text, karten }
  let serverKarten = [];

  const TEST_TITEL = 'spotify:track:4uLU6hMCjMI75M1A2tKUQC';
  const TEST_ALBUM = 'spotify:album:1DFixLWuPkv3KT3TnV35m3';
  const TEST_KUENSTLER = 'spotify:artist:1dfeR4HaWDbWqFHLkxsg1d';

  function vorschauKarten(name) {
    const t = jetzt(), m = (min) => new Date(t + min * 60000).toISOString();
    const stunde = (fach, raum, lehrer, vonMin, vertretung = false) => ({ fach, raum, lehrer, von: m(vonMin), bis: m(vonMin + 45), vertretung });
    const schule = (zustand, extra) => ({ id: 'vorschau-schule', art: 'schule', prio: 30, zustand, hinweis: '', art_pause: '',
                                          aktuell: null, naechste: null, vorschau: true, ...extra });
    const meldung = { id: 'vorschau-meldung', art: 'meldung', prio: 10, quelle: 'verspaetungen', titel: 'Verspätungen: 10 € gesammelt',
                      text: 'Deine Fahrten heute bringen dir 10 € Garantie.', url: '/', zeit: m(-6), vorschau: true };
    const musik = { id: 'vorschau-musik', art: 'musik', prio: 40, titel: 'So What', wer: 'P!nk', album: 'Funhouse', cover: '/icons/icon-192.png',
                    laeuft: true, position_ms: 75000, dauer_ms: 215000, geraet: 'Handy', steuerbar: true, stand: m(0),
                    uri: TEST_TITEL, album_uri: TEST_ALBUM, artist_uri: TEST_KUENSTLER, vorschau: true };
    const friday = { id: 'vorschau-friday', art: 'friday', prio: 20, titel: 'Bus 100 um 7:52', text: 'Fährt ab Kröpcke, 3 Minuten später.',
                     bild: '', bis: m(8), zeit: m(-2), vorschau: true };
    // Außerhalb der Schulzeit (abends, nachts) läge „jetzt“ nicht zwischen den Stunden: dann tut die Vorschau so,
    // als wäre es 9:50 Uhr, damit man die Hervorhebung der laufenden Stunde sehen kann.
    const echtUhr = uhr(t), inSchule = echtUhr >= '07:30' && echtUhr < '15:00', anker = inSchule ? echtUhr : '09:50';
    const zeitAn = (min) => {
      const [h, mi] = anker.split(':').map(Number), g = h * 60 + mi + min;
      return `${zweistellig(Math.floor(g / 60) % 24)}:${zweistellig(g % 60)}`;
    };
    const plan = { id: 'vorschau-stundenplan', uhr_test: inSchule ? '' : anker, art: 'stundenplan', prio: 20, titel: 'Stundenplan', datum: tagIso(t), vorschau: true,
                   app_url: 'https://login.schulmanager-online.de', bis: m(120), zeit: m(0),
                   stunden: [['1', -95, 'Mathematik', 'B 1.15', 'Müller', 'regulaer', ''], ['2', -50, 'Mathematik', 'B 1.15', 'Müller', 'regulaer', ''],
                             ['3', -5, 'Englisch', 'C 1.01', 'Meier', 'vertretung', 'eigentlich Latein'], ['4', 40, 'Englisch', 'C 1.01', 'Meier', 'vertretung', 'eigentlich Latein'],
                             ['5', 85, 'Sport', 'Halle A', 'Krause', 'regulaer', ''], ['6', 130, 'Sport', 'Halle A', 'Krause', 'regulaer', '']]
                     .map(([nr, min, fach, raum, lehrer, status, hinweis]) => ({ nr, von: zeitAn(min), bis: zeitAn(min + 45), fach, raum, lehrer, status, hinweis })) };
    const pc = { id: 'vorschau-pc', art: 'pc', prio: 35, rechner: 'PC-VON-EIKE', laufzeit_s: 11520, gesperrt: false, schreiben: true,
                 fenster: [{ programm: 'chrome', titel: 'Friday' }, { programm: 'Code', titel: 'friday-web' }, { programm: 'Spotify', titel: 'So What' }],
                 offen: [], vorschau: true, stand: m(0) };
    const pcFrage = { ...pc, offen: [{ id: 'vorschau', beschreibung: 'PC in 60 Sekunden herunterfahren', rest_s: 590 }] };
    const jf = { id: 'vorschau-jellyfin', art: 'jellyfin', prio: 42, titel: 'Beispielserie', untertitel: 'S1 E3 · Der Anfang',
                 geraet: 'Wohnzimmer-TV', laeuft: true, position_s: 1260, dauer_s: 2700, bild: '/icons/icon-192.png',
                 steuerbar: true, vorschau: true, stand: m(0) };
    const pcm = { id: 'vorschau-pcmedien', art: 'pcmedien', prio: 44, titel: 'Ein Beispielvideo', kuenstler: 'Ein Kanal', app: 'Chrome',
                  laeuft: true, position_s: 61, dauer_s: 300, bild: '/icons/icon-192.png', steuerbar: true, vorschau: true, stand: m(0) };
    const mcBasis = { id: 'vorschau-minecraft', art: 'minecraft', prio: 46, version: '26.3', loader: 'Fabric', loader_version: '0.19.5', mods: 31, vorschau: true };
    const mcWelt = { ...mcBasis, zustand: 'welt', name: 'Neue Welt', dimension: 'overworld', x: 124, y: 64, z: -310, tageszeit: 8200, tag: 4,
                     leben: 15, leben_max: 20, absorption: 0, hunger: 13, ruestung_wert: 12, erfahrung: 7, skin_url: '', skin_pfad: 'textures/entity/player/wide/steve.png', skin_schmal: false,
                     ruestung: { kopf: 'diamond_helmet', brust: 'iron_chestplate', beine: '', fuesse: 'golden_boots' },
                     hotbar: ['diamond_pickaxe', 'diamond_sword', 'bow', 'oak_planks', 'torch', 'cooked_beef', 'golden_apple', 'water_bucket', 'ender_pearl']
                       .map((id, i) => ({ id, anzahl: [1, 1, 1, 64, 23, 12, 3, 1, 8][i] })), auswahl: 0, hand: { id: 'iron_sword', anzahl: 1 },
                     inventar: ['grass_block', 'stone', 'oak_log', 'furnace', 'oak_leaves', 'cobblestone', 'crafting_table', 'dirt', 'sand', 'iron_ingot', 'diamond', 'coal', 'apple', 'arrow', 'oak_stairs', 'chest', '', 'gold_ingot',
                                'bricks', 'netherrack', 'glass', 'tnt', 'bookshelf', 'pumpkin', 'red_wool', 'deepslate', 'bone']
                       .map((id, i) => ({ id, anzahl: id ? [64, 32, 16, 1, 64, 40, 1, 64, 30, 12, 5, 33, 6, 24, 8, 1, 0, 9, 64, 64, 12, 4, 3, 1, 16, 64, 7][i] : 0 })), nebenhand: { id: '', anzahl: 0 } };
    const bildKarte = { id: 'vorschau-bild', art: 'bild', prio: 20, titel: 'Eiffelturm bei Nacht', bild: '/icons/icon-512.png',
                        quelle: 'Wikimedia Commons', lizenz: 'CC BY-SA 4.0', urheber: 'Anna Muster', link: 'https://commons.wikimedia.org/', bis: m(60), vorschau: true };
    const route = { id: 'vorschau-route', art: 'route', prio: 20, titel: 'Nach Hannover Hauptbahnhof', zeit: m(0), bis: m(45), vorschau: true,
                    start: { name: 'Bennigsen, Linderter Weg', breite: 52.2891, laenge: 9.6215 }, ziel: { name: 'Hannover Hauptbahnhof', breite: 52.3759, laenge: 9.7320 },
                    schnellste: 0,
                    optionen: [
                      { modus: 'oepnv', titel: 'Bus & Bahn', dauer_min: 31, ab: '14:06', an: '14:37', ab_in_min: 6, umstiege: 1, echtzeit: true, verspaetung_min: 2, hinweis: '',
                        abschnitte: [{ art: 'fuss', linie: '', min: 4, meter: 310, von: 'Linderter Weg', nach: 'Bennigsen Bahnhof' },
                                     { art: 'sbahn', linie: 'S1', richtung: 'Haltern', von: 'Bennigsen', nach: 'Hannover Hbf', ab: '14:10', an: '14:35', min: 25 },
                                     { art: 'fuss', linie: '', min: 2, meter: 90, von: 'Hannover Hbf', nach: 'Ziel' }] },
                      { modus: 'fuss', titel: 'Zu Fuß', dauer_min: 214, distanz_m: 17800, ab: '14:00', an: '17:34', hinweis: 'Das ist weit zu Fuß (17,8 km).' },
                      { modus: 'auto', titel: 'Auto', dauer_min: 19, distanz_m: 16900, ab: '14:00', an: '14:19', hinweis: 'Ohne aktuellen Verkehr gerechnet.' }],
                    ausfaelle: [], hinweise: ['Dein Standort ist ungenau (vermutlich WLAN- oder Netz-Ortung, kein GPS) — der Start kann ein Stück daneben liegen.'] };
    const unterricht = schule('unterricht', { von: m(-20), bis: m(25), aktuell: stunde('Mathematik', 'B 1.15', 'Müller', -20),
                                              naechste: stunde('Deutsch', 'A 2.03', 'Schmidt', 30) });
    return {
      unterricht: ['Im Unterricht', [unterricht]],
      pause: ['Pause', [schule('pause', { von: m(-3), bis: m(7), naechste: stunde('Englisch', 'C 1.01', 'Meier', 7) })]],
      freistunde: ['Freistunde', [schule('pause', { von: m(-10), bis: m(35), art_pause: 'freistunde', hinweis: 'Fällt aus: Deutsch',
                                                    naechste: stunde('Physik', 'N 2.10', 'Weber', 35) })]],
      vor_schule: ['Vor der Schule', [schule('vor_schule', { von: m(-10), bis: m(20), naechste: stunde('Mathematik', 'B 1.15', 'Müller', 20) })]],
      vertretung: ['Vertretung', [schule('unterricht', { von: m(-10), bis: m(35), hinweis: 'Vertretung, eigentlich Mathematik',
                                                        aktuell: stunde('Kunst', 'K 1', 'Lehmann', -10, true),
                                                        naechste: stunde('Deutsch', 'A 2.03', 'Schmidt', 40) })]],
      stundenplan: ['Stundenplan-Karte', [plan]],
      pc: ['PC-Karte', [pc]],
      pc_frage: ['PC-Karte mit Rückfrage', [pcFrage]],
      jellyfin: ['Jellyfin-Karte', [jf]],
      pcmedien: ['PC-Wiedergabe (YouTube, VLC …)', [pcm]],
      bild: ['Bildkarte', [bildKarte]],
      minecraft: ['Minecraft-Karte (in der Welt)', [mcWelt]],
      minecraft_menu: ['Minecraft-Karte (Hauptmenü)', [{ ...mcBasis, zustand: 'menu' }]],
      route: ['Verbindungskarte', [route]],
      schultag: ['Schultag komplett', [meldung, friday, unterricht, musik]],
    }[name];
  }

  const vorschauBanner = $('ueb-vorschau');

  function vorschauSetzen(name) {
    const v = name && name !== 'aus' ? vorschauKarten(name) : null;
    vorschau = v ? { name, text: v[0], karten: v[1] } : null;
    vorschauBanner.hidden = !vorschau;
    if (vorschau) $('ueb-vorschau-text').textContent = `Vorschau: ${vorschau.text}`;
    abgleichen(mitVorschau(serverKarten));
    if (!vorschau) abfragen();
  }
  window.Friday.uebersichtVorschau = vorschauSetzen;
  $('ueb-vorschau-ende').addEventListener('click', () => vorschauSetzen('aus'));
  for (const b of document.querySelectorAll('[data-vorschau]')) {
    b.addEventListener('click', () => { vorschauSetzen(b.dataset.vorschau); location.hash = '#uebersicht'; });
  }

  // Testkarten ersetzen echte Karten derselben Art; danach nach Priorität.
  function mitVorschau(echte) {
    if (!vorschau) return echte;
    const arten = new Set(vorschau.karten.map((k) => k.art));
    return [...vorschau.karten, ...echte.filter((k) => !arten.has(k.art))].sort((a, b) => a.prio - b.prio);
  }

  /* ---------- Abfrage ---------- */

  async function abfragen(nochmal = true) {
    if (laeuft || !sichtbar()) return;
    laeuft = true;
    try {
      const r = await fetch('/api/dashboard', { cache: 'no-store' });
      if (!r.ok) throw new Error(String(r.status));
      const d = await r.json();
      versatz = Date.parse(d.jetzt) - Date.now();
      serverKarten = d.karten || [];
      abgleichen(mitVorschau(serverKarten));
      hinweis.hidden = true;
    } catch (_) {
      // Direkt nach der Rückkehr in die App ist das Netz oft noch nicht wach: einmal kurz später noch versuchen,
      // bevor „Keine Verbindung“ erscheint (Eike, 09.10.2026).
      if (nochmal) { laeuft = false; setTimeout(() => abfragen(false), 2000); return; }
      // Die letzten Karten bleiben stehen; nur ein leiser Hinweis.
      hinweis.textContent = `Keine Verbindung — Stand von ${uhr(Date.now())}`;
      hinweis.hidden = false;
    } finally {
      laeuft = false;
    }
  }
  window.Friday.uebersichtNeu = abfragen;

  function datumZeigen() {
    $('ueb-datum').textContent = new Date().toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long', timeZone: ZEITZONE });
  }

  // Beim Verlassen räumen wir Gesehenes weg: „nicht den ganzen Tag“ (Eike, 24.09.2026).
  function verlassen() {
    for (const [id, e] of [...karten]) if (id !== '_leer' && e.gesehen) entfernen(id, true);
    leerpruefen();
  }

  function zeigt() {
    if (seite.hidden) { verlassen(); return; }
    datumZeigen();
    abfragen();
  }

  window.addEventListener('hashchange', zeigt);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') zeigt(); else verlassen(); });
  setInterval(() => abfragen(), ABFRAGE_MS);
  window.addEventListener('online', () => abfragen());
  // Liegt eine Verbindungskarte, die noch unterwegs ist, fragen wir öfter — sie rechnet der Server alle ~30 s neu, so kommt es zeitnah an.
  setInterval(() => {
    for (const e of karten.values()) if (e.z.art === 'route' && !e.z.el.classList.contains('angekommen')) { abfragen(); break; }
  }, 10000);
  setInterval(() => { if (sichtbar()) for (const e of karten.values()) e.z.tick(jetzt()); }, 1000);

  /* ---------- Live-Zeile: Fridays letzter Satz, solange ein Gespräch läuft ---------- */

  const zeile = $('live-zeile');
  const live = window.Friday.live;
  if (live) {
    let rolle = null;
    live.auf('start', () => { zeile.hidden = false; zeile.querySelector('.wer').textContent = ''; zeile.querySelector('.t').textContent = 'Friday hört zu …'; });
    live.auf('ende', () => { zeile.hidden = true; rolle = null; });
    live.auf('text', (r, text) => {
      if (r !== rolle) { zeile.querySelector('.t').textContent = ''; zeile.querySelector('.wer').textContent = r === 'du' ? 'Du' : 'Friday'; rolle = r; }
      zeile.querySelector('.t').textContent += text;
    });
    live.auf('turn_end', () => { rolle = null; });
    live.auf('werkzeug', (name) => { if (name === 'dashboard_karte') setTimeout(abfragen, 400); });
  }

  leerpruefen();
  zeigt();
})();
