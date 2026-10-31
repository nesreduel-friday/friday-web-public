/* Standort — antwortet, wenn Friday den Standort braucht. Sonst passiert nichts.

   Früher meldete die App die Position beim Öffnen und danach im Takt. Auf dem
   iPhone fragt die installierte App bei jedem Start neu nach der Erlaubnis
   (Eike, 20.09.2026: „ich werde beim Öffnen jedes Mal neu gefragt“) — das kam
   also auch dann, wenn er nie nach seinem Standort fragte.

   Jetzt hält die App bei sichtbarer Seite eine Long-Poll-Anfrage offen. Fragt Eike
   „Wo bin ich?“, weckt der Server sie, die App holt die Position und antwortet.
   Der Erlaubnis-Dialog erscheint damit genau nach seiner Frage — und nie sonst.
   Ist der Konnektor aus, antwortet der Server mit 403; dann fragt die App den
   Browser nie und probiert es nur gelegentlich wieder. */
(() => {
  const FEHLER = { 1: 'verweigert', 2: 'nicht_verfuegbar', 3: 'zeitueberschreitung' };
  const PAUSE_AUS = 60000;        // Konnektor aus: so selten nachsehen
  const PAUSE_VERSTECKT = 3000;   // Seite nicht sichtbar: nicht lauschen
  const PAUSE_FEHLER = 10000;

  let verweigert = false;         // im Browser abgelehnt: nicht erneut fragen, bis die Seite neu lädt
  let gesehen = -1;               // Nummer der letzten Anfrage, die der Server gemeldet hat
  let beantwortet = null;         // Nummer der Anfrage, die diese Seite schon bedient hat

  const kennung = () => (window.Friday && window.Friday.kennung ? window.Friday.kennung() : '');
  const warte = (ms) => new Promise((ok) => setTimeout(ok, ms));

  const senden = (daten) => fetch('/api/standort', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kennung: kennung(), ...daten }),
  }).catch(() => {});

  // Hoch genau, weil „In welcher Straße bin ich?“ mit Funkzellen-Ortung nicht geht.
  // maximumAge klein: gefragt wird ja gerade jetzt.
  const position = () => new Promise((ok, fehl) => navigator.geolocation.getCurrentPosition(
    ok, fehl, { enableHighAccuracy: true, maximumAge: 10000, timeout: 20000 }));

  async function antworten() {
    if (!('geolocation' in navigator)) { await senden({ fehler: 'nicht_unterstuetzt' }); return; }
    if (verweigert) { await senden({ fehler: 'verweigert' }); return; }   // nicht noch einmal fragen
    try {
      const p = await position();
      await senden({ breite: p.coords.latitude, laenge: p.coords.longitude, genauigkeit: p.coords.accuracy });
    } catch (e) {
      if (e && e.code === 1) verweigert = true;
      await senden({ fehler: FEHLER[e && e.code] || 'nicht_verfuegbar' });
    }
  }

  async function lauschen() {
    for (;;) {
      if (document.visibilityState !== 'visible') { await warte(PAUSE_VERSTECKT); continue; }
      try {
        const antwort = await fetch(`/api/standort/warten?seit=${gesehen}`, { cache: 'no-store' });
        if (antwort.status === 403) { await warte(PAUSE_AUS); continue; }    // Konnektor aus
        if (!antwort.ok) { await warte(PAUSE_FEHLER); continue; }
        const d = await antwort.json();
        gesehen = d.nr;
        // „offen“: die Anfrage gilt noch. Eine alte, verfallene löst nichts aus.
        if (d.offen && d.nr !== beantwortet) { beantwortet = d.nr; await antworten(); }
      } catch (_) { await warte(PAUSE_FEHLER); }
    }
  }

  lauschen();
})();
