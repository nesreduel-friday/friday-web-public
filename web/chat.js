/* Chat — ein Verlauf für alles: Text geschrieben, Gespräch gesprochen.

   Läuft ein Gespräch (Kreis im Kopf), schreibt sein Transkript hier mit — auch
   wenn du gerade auf einer anderen Seite bist — und die Eingabe geht über den
   Live-Kanal. Ohne Gespräch geht sie an /api/chat.

   Der Verlauf liegt seit 28.10.2026 auf dem SERVER (Chat-Strang, /api/chat/strang): Er bleibt beim Neuöffnen stehen, ist auf allen
   Geräten gleich, und Friday kann sich selbst darin melden. Der Browser lädt ihn, fragt alle paar Sekunden nach und schickt nur noch
   die neue Nachricht; den Kontext baut der Server. „Chat leeren“ leert den Verlauf überall (gelöscht wird nichts). */
(() => {
  const { $, Verlauf, bildVerkleinern, eingabeFeld, werkzeugText } = window.Friday;
  const live = window.Friday.live;

  const verlauf = new Verlauf($('chat-verlauf'));
  const form = $('chat-form'), text = $('chat-text'), sendenKnopf = $('chat-senden');
  const bildAuf = $('chat-bild-auf'), bildDatei = $('chat-bild-datei'), anhangEl = $('chat-anhang');
  const streifen = $('chat-live'), streifenText = $('chat-live-text'), neuKnopf = $('chat-neu');
  const oeffnenHier = $('oeffnen-hier');
  const feld = eingabeFeld(text, form);
  const SPEICHER = 'friday.chat';
  const PLATZHALTER = 'Schreib Friday oder tipp oben auf den Kreis, um zu sprechen.';
  const ABFRAGE_MS = 4000;   // so oft fragt der Chat nach neuen Zeilen (nur bei sichtbarer Seite)

  let gespraech = null, anhang = null, beschaeftigt = false;
  let ausstehend = null;     // {id, warte}: eine Anfrage, deren Antwort noch abgeholt werden muss (App war weg)
  let holt = false;
  let offen = null;          // der Live-Eintrag, an den Transkriptstücke gehängt werden (Anzeige)
  let letzteId = 0;          // größte Zeilen-Nummer des Strangs, die diese Seite verarbeitet hat
  let abfragend = false;
  let eigeneDu = 0;          // eigene, schon lokal gezeigte Zeilen, deren Strang-Zeile noch nicht gesehen wurde
  const gezeigt = new Set(); // Nummern der Strang-Zeilen, die hier schon stehen

  // Bilder bleiben nicht im Strang gespeichert — nur ein Platzhalter („[Bild] …“).
  function sichern() {
    try { sessionStorage.setItem(SPEICHER, JSON.stringify({ gespraech, ausstehend: ausstehend ? ausstehend.id : null })); } catch (_) {}
  }

  // Eine Zeile aus dem Strang zeichnen.
  function zeigeEintrag(e) {
    if (e.art === 'hinweis') verlauf.hinweis(werkzeugText(e.text));
    else if (e.art === 'quittung') verlauf.quittung(e.text, e.status);
    else { verlauf.text(e.rolle, e.text, e.quelle === 'live'); verlauf.schliessen(); }
  }

  /* Neue Zeilen holen. `erzwingen`: beim ersten Laden auch mitten in einer Anfrage. */
  async function nachholen(erzwingen = false) {
    if (abfragend || (beschaeftigt && !erzwingen)) return;
    abfragend = true;
    try {
      const r = await fetch(`/api/chat/strang?nach=${letzteId}`, { cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      if ((d.leer_ab || 0) > letzteId) {          // auf einem anderen Gerät geleert: hier auch
        verlauf.leeren(PLATZHALTER);
        gezeigt.clear();
        eigeneDu = 0;
        letzteId = d.leer_ab;
        offen = null;
        return nachholen(erzwingen);
      }
      for (const e of d.eintraege || []) {
        letzteId = Math.max(letzteId, e.id);
        if (gezeigt.has(e.id)) continue;
        gezeigt.add(e.id);
        if (e.rolle === 'du' && e.quelle === 'chat' && e.art === 'text' && eigeneDu > 0) { eigeneDu--; continue; }   // meine eigene Zeile, lokal schon gezeigt
        if (e.quelle === 'live' && live && live.laeuft()) continue;                                                  // das Gespräch hier schreibt selbst mit
        zeigeEintrag(e);
      }
    } catch (_) { /* Netz weg: beim nächsten Takt wieder */ } finally {
      abfragend = false;
    }
  }

  function laden() {
    let ausst = null;
    try {
      const d = JSON.parse(sessionStorage.getItem(SPEICHER) || 'null');
      if (d) { gespraech = d.gespraech || null; ausst = d.ausstehend || null; }
    } catch (_) {}
    nachholen(true).then(() => {
      // Die Seite wurde im Hintergrund neu geladen, während Friday noch antwortete: Ergebnis weiter abholen.
      if (ausst) {
        ausstehend = { id: ausst, warte: verlauf.hinweis('Friday schreibt …') };
        beschaeftigt = true;
        sendenKnopf.disabled = true;
        setTimeout(antwortHolen, 0);
      }
    });
    setInterval(() => { if (document.visibilityState === 'visible') nachholen(); }, ABFRAGE_MS);
  }

  function anhangZeigen() {
    anhangEl.textContent = '';
    anhangEl.hidden = !anhang;
    if (!anhang) return;
    const weg = document.createElement('button');
    weg.type = 'button';
    weg.textContent = '×';
    weg.setAttribute('aria-label', 'Anhang entfernen');
    weg.onclick = () => { anhang = null; anhangZeigen(); };
    if (anhang.art === 'pdf') { anhangEl.append(`PDF: ${anhang.name}`, weg); return; }
    const img = new Image();
    img.src = anhang.vorschau;
    img.alt = 'Anhang';
    anhangEl.append(img, 'Bild angehängt', weg);
  }

  bildAuf.addEventListener('click', () => bildDatei.click());
  bildDatei.addEventListener('change', async () => {
    const datei = bildDatei.files[0];
    bildDatei.value = '';
    if (!datei) return;
    if (datei.type === 'application/pdf') {
      if (datei.size > 10 * 1024 * 1024) { verlauf.hinweis('Das PDF ist zu groß: erlaubt sind 10 MB.'); return; }
      try {
        const data = await new Promise((ok, nein) => { const f = new FileReader(); f.onload = () => ok(String(f.result).split(',')[1]); f.onerror = nein; f.readAsDataURL(datei); });
        anhang = { art: 'pdf', name: datei.name, mime: 'application/pdf', data };
        anhangZeigen(); text.focus();
      } catch (_) { verlauf.hinweis('Das PDF ließ sich nicht lesen.'); }
      return;
    }
    try { anhang = await bildVerkleinern(datei); anhangZeigen(); text.focus(); }
    catch (_) { verlauf.hinweis('Das Bild ließ sich nicht lesen.'); }
  });

  /* ---------- Senden: Live-Kanal oder /api/chat ---------- */

  function liveSenden(eingabe) {
    // true: rausgegangen und im Verlauf. Die eigene Zeile schreibt der Chat selbst.
    const ok = anhang ? live.sendenBild(anhang, eingabe) : live.sendenText(eingabe);
    if (!ok) return false;
    verlauf.schliessen();
    offen = null;
    if (anhang) verlauf.bild('du', anhang.vorschau, eingabe);
    else verlauf.text('du', eingabe, true);
    verlauf.schliessen();
    sichern();
    return true;
  }

  function antwortZeigen(d) {
    gespraech = d.gespraech;
    const ids = d.strang_ids || [];
    // Steht die Antwort schon hier (Seite neu geladen, Zeilen aus dem Strang geholt), nicht noch einmal zeichnen.
    const schon = ids.length > 0 && ids.every((id) => gezeigt.has(id));
    if (ids.length) { eigeneDu = Math.max(0, eigeneDu - 1); ids.forEach((id) => gezeigt.add(id)); }
    if (!schon) {
      for (const l of d.links || []) verlauf.link(l.url, l.ok ? 'gelesen' : 'fehler', l.titel, l.grund);
      for (const w of d.werkzeuge || []) verlauf.hinweis(werkzeugText(w));
      verlauf.text('friday', d.antwort);
      for (const q of d.quittungen || []) verlauf.quittung(q.text, q.status);
    }
    if ((d.werkzeuge || []).some((w) => w === 'dashboard_karte' || w === 'verbindung_karte')) window.Friday.uebersichtNeu?.();
    for (const a of d.aktionen || []) {
      if (a.typ === 'oeffnen') verlauf.oeffnen(a.url, a.titel, a.auto);
      if (a.typ === 'bild') verlauf.bilder(a.bilder);
      if (a.typ === 'datei') verlauf.datei(a.url, a.titel);
      if (a.typ === 'pc') { verlauf.hinweis(`Wartet auf dich: ${a.text}`); window.FridayPc?.jetzt(); }
    }
    sichern();
  }

  async function restSenden(eingabe) {
    const n = { rolle: 'du', text: eingabe };
    if (anhang && anhang.art === 'pdf') { n.datei = { mime: 'application/pdf', name: anhang.name, data: anhang.data }; verlauf.text('du', `📎 ${anhang.name}${eingabe ? ' — ' + eingabe : ''}`); }
    else if (anhang) { n.bild = { mime: anhang.mime, data: anhang.data }; verlauf.bild('du', anhang.vorschau, eingabe); }
    else verlauf.text('du', eingabe);
    eigeneDu++;
    feld.leeren();
    anhang = null;
    anhangZeigen();

    beschaeftigt = true;
    sendenKnopf.disabled = true;
    const warte = verlauf.hinweis('Friday schreibt …');
    const id = (window.crypto && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}${Math.random().toString(36).slice(2)}`).replace(/[^A-Za-z0-9_-]/g, '');
    ausstehend = { id, warte };
    sichern();     // Kommt die Seite im Hintergrund abhanden, weiß sie danach noch, welche Antwort fehlt
    // Der Server kennt den Verlauf (Strang): geschickt wird nur die neue Nachricht.
    try {
      const r = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // Kennung mitgeben: sonst rät Friday bei „Wo bist du?" die zuletzt gemeldete Oberfläche.
        body: JSON.stringify({ nachrichten: [n], strang: true, gespraech, anfrage_id: id,
                               kennung: window.Friday.kennung ? window.Friday.kennung() : '' }),
      });
      const d = await r.json();
      warte.remove();
      ausstehend = null;
      sichern();
      if (!r.ok) { verlauf.hinweis(d.detail || `Fehler ${r.status}`); fertigMachen(); return; }
      antwortZeigen(d);
    } catch (_) {
      // Die Verbindung riss ab (App im Hintergrund): Friday antwortet auf dem Server trotzdem zu Ende — Ergebnis abholen.
      if (ausstehend && ausstehend.id === id) { antwortHolen(); return; }
      warte.remove();
      verlauf.hinweis('Keine Verbindung zu Friday.');
    }
    beschaeftigt = false;
    sendenKnopf.disabled = false;
  }

  function fertigMachen() {
    beschaeftigt = false;
    sendenKnopf.disabled = false;
    nachholen();     // Anderes, das inzwischen im Strang steht (Meldungen von Friday, andere Geräte)
  }

  /* Antwort nachholen: fragt den Server nach dem Ergebnis der Anfrage, bis es da ist (oder nicht mehr existiert). */
  async function antwortHolen() {
    if (holt || !ausstehend) return;
    holt = true;
    const a = ausstehend;
    try {
      for (let i = 0; i < 120 && ausstehend === a; i++) {
        try {
          const r = await fetch(`/api/chat/ergebnis/${a.id}`, { cache: 'no-store' });
          if (r.status === 404) break;
          const d = await r.json();
          if (d.zustand === 'fertig' && ausstehend === a) {
            ausstehend = null; a.warte.remove(); antwortZeigen(d.ergebnis); fertigMachen(); return;
          }
          if (d.zustand === 'fehler' && ausstehend === a) {
            ausstehend = null; a.warte.remove(); verlauf.hinweis(d.detail || 'Das ging schief.'); sichern(); fertigMachen(); return;
          }
        } catch (_) { /* Netz noch nicht wieder da: gleich noch einmal */ }
        await new Promise((ok) => setTimeout(ok, 2000));
      }
      if (ausstehend === a) {
        ausstehend = null; a.warte.remove(); sichern(); fertigMachen();
        verlauf.hinweis('Keine Verbindung zu Friday — die Antwort ist nicht angekommen. Schreib bitte noch einmal.');
      }
    } finally {
      holt = false;
    }
  }
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') antwortHolen(); });
  window.addEventListener('pageshow', () => antwortHolen());

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (beschaeftigt) return;
    const eingabe = text.value.trim();
    if (!eingabe && !anhang) return;

    if (live && live.laeuft() && anhang && anhang.art === 'pdf') { verlauf.hinweis('PDFs gehen nur im Schreib-Chat: beende erst das Gespräch.'); return; }
    if (live && live.laeuft()) {
      // Mitten im Verbindungsaufbau NICHT still an /api/chat ausweichen: das spaltete das
      // Gespräch in zwei Fäden. Der Text bleibt im Feld stehen.
      if (!live.bereit()) { verlauf.hinweis('Friday verbindet sich noch — einen Moment.'); return; }
      if (liveSenden(eingabe)) { feld.leeren(); anhang = null; anhangZeigen(); }
      else verlauf.hinweis('Das ging gerade nicht raus — versuch es gleich noch einmal.');
      return;
    }
    await restSenden(eingabe);
  });

  neuKnopf.addEventListener('click', async () => {
    if (live && live.laeuft()) return;
    try {
      const r = await fetch('/api/chat/leeren', { method: 'POST' });
      const d = await r.json();
      if (!r.ok) throw new Error('leeren');
      letzteId = Math.max(letzteId, d.leer_ab || 0);
    } catch (_) { verlauf.hinweis('Das Leeren hat nicht geklappt — versuch es gleich noch einmal.'); return; }
    gespraech = null;
    offen = null;
    gezeigt.clear();
    eigeneDu = 0;
    verlauf.leeren(PLATZHALTER);
    sichern();
  });

  /* ---------- Live-Gespräch schreibt mit ---------- */

  if (live) {
    live.auf('start', () => {
      streifen.hidden = false;
      streifenText.textContent = 'Gespräch läuft';
      neuKnopf.disabled = true;
      offen = null;
    });
    live.auf('ende', () => {
      streifen.hidden = true;
      neuKnopf.disabled = false;
      verlauf.schliessen();
      offen = null;
      verlauf.hinweis('Gespräch beendet.');
      sichern();
    });
    live.auf('status', (t) => { streifenText.textContent = t; });
    live.auf('text', (rolle, stueck) => {
      // Gesprochenes kommt in Stücken: dieselbe Seite schreibt in denselben Eintrag weiter.
      verlauf.text(rolle, stueck, true);
    });
    const schliessen = () => { verlauf.schliessen(); offen = null; sichern(); };
    live.auf('turn_end', schliessen);
    live.auf('interrupted', schliessen);
    live.auf('link', (url, zustand, titel, grund) => verlauf.link(url, zustand, titel, grund));
    live.auf('oeffnen', (url, titel, auto) => {
      const a = verlauf.oeffnen(url, titel, auto);
      // Der Knopf steht zusätzlich global unter dem Kopf: iOS öffnet nur nach einem Tipp,
      // und der Chat ist oft gar nicht die offene Seite. Nach dem Tipp weg.
      if (a) { oeffnenHier.replaceChildren(a.cloneNode(true)); oeffnenHier.hidden = false; }
    });
    live.auf('bild', (bilder) => verlauf.bilder(bilder));
    // Ein Werkzeug hat eine Datei erzeugt (z. B. „dokument_erstellen“): antippbarer Download im Verlauf.
    live.auf('datei', (url, titel) => verlauf.datei(url, titel));
    live.auf('pc', (t) => { verlauf.hinweis(`Wartet auf dich: ${t}`); window.FridayPc?.jetzt(); });
    live.auf('werkzeug', (name) => verlauf.hinweis(werkzeugText(name)));
    live.auf('quittung', (text, status) => verlauf.quittung(text, status));
    live.auf('korrektur', (text) => verlauf.korrektur(text));
    live.auf('hinweis', (t) => verlauf.hinweis(t));
    $('chat-live-ende').addEventListener('click', () => live.beenden());
  }
  oeffnenHier.addEventListener('click', () => { oeffnenHier.hidden = true; });

  // Beim Öffnen der Seite ans Ende springen: Im Hintergrund wächst der Verlauf weiter.
  window.addEventListener('hashchange', () => {
    if (location.hash === '#chat') requestAnimationFrame(() => { $('chat-verlauf').scrollTop = $('chat-verlauf').scrollHeight; });
  });

  // Modell des Chats (29.10.2026): gilt für die nächsten Nachrichten; mit Bild/PDF antwortet immer Gemini.
  const modellWahl = $('chat-backend');
  fetch('/api/chat/backend', { cache: 'no-store' }).then((r) => r.json()).then((d) => { modellWahl.value = d.backend || 'gemini'; }).catch(() => {});
  modellWahl.addEventListener('change', async () => {
    try {
      const r = await fetch('/api/chat/backend', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ backend: modellWahl.value }) });
      const d = await r.json();
      modellWahl.value = d.backend || 'gemini';
    } catch (e) { /* bleibt, wie es war */ }
  });

  laden();
})();
