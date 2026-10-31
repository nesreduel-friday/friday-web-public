/* Extras → Memories: Snapchat-Export zusammenführen und filtern.

   ZIPs gehen in Teilen zum Server (ein Export ist mehrere GB groß), gerechnet wird im Container `snapmem`,
   das Ergebnis ist eine ZIP zum Herunterladen. Alles per textContent; Dateinamen kommen vom Nutzer. */
(() => {
  const wurzel = document.getElementById('memories-inhalt');
  if (!wurzel) return;
  const el = (tag, klasse, text) => {
    const e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };
  const knopf = (text, klasse, aktion) => {
    const b = el('button', klasse, text); b.type = 'button';
    b.addEventListener('click', () => aktion(b));
    return b;
  };
  const groesse = (b) => (b >= 2 ** 30 ? `${(b / 2 ** 30).toFixed(1)} GB` : `${Math.max(1, Math.round(b / 2 ** 20))} MB`);
  const dauer = (s) => (s >= 90 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`);
  const anruf = async (url, methode = 'POST', daten) => {
    try {
      const r = await fetch(url, { method: methode, headers: { 'Content-Type': 'application/json' },
                                   body: daten === undefined ? undefined : JSON.stringify(daten) });
      const d = await r.json().catch(() => ({}));
      return { ok: r.ok, text: d.detail || d.text || (r.ok ? '' : `Fehler ${r.status}`), ...d };
    } catch (_) { return { ok: false, text: 'Friday ist nicht erreichbar.' }; }
  };

  const MODI = [
    ['alles', 'Alles zusammenführen', 'Jedes Bild und Video (auch aus Chats) mit eingebranntem Text, Stickern und Filtern, plus Datum und Ort. Bei Chat-Medien steht der Chat in den Metadaten, wenn der Export ihn verrät.'],
    ['sauber', 'Nur saubere Bilder', 'Bilder (auch aus Chats) ohne Overlay, Sticker, Text und Linsen-Filter.'],
    ['personen', 'Nur Bilder mit Personen', 'Wie „sauber“, aber nur mit Personen darauf (auch von hinten).'],
  ];
  const ZUSTAND = { wartet: 'wartet …', laeuft: 'läuft', fertig: 'fertig', fehler: 'Fehler' };

  let stand = { uploads: [], laeufe: [] };
  let modus = 'alles';
  let gewaehlt = new Set();
  let hochladen = null;      // { text, anteil } während ein Upload läuft
  let meldung = '';
  let takt = null;

  async function hochladenDatei(datei, teil) {
    const start = await anruf('/api/snapmem/hochladen', 'POST', { name: datei.name, groesse: datei.size });
    if (!start.ok) throw new Error(start.text);
    const teile = Math.ceil(datei.size / start.teil);
    for (let nr = 0; nr < teile; nr += 1) {
      const stueck = await datei.slice(nr * start.teil, (nr + 1) * start.teil).arrayBuffer();
      let ok = false, text = '';
      for (let versuch = 0; versuch < 3 && !ok; versuch += 1) {
        try {
          const r = await fetch(`/api/snapmem/hochladen/${start.id}/${nr}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: stueck });
          ok = r.ok;
          if (!ok) text = (await r.json().catch(() => ({}))).detail || `Fehler ${r.status}`;
        } catch (_) { text = 'Verbindung unterbrochen.'; }
      }
      if (!ok) throw new Error(text);
      hochladen = { text: `${datei.name}: ${groesse(Math.min(datei.size, (nr + 1) * start.teil))} von ${groesse(datei.size)}`,
                    anteil: (nr + 1) / teile };
      zeichnen();
    }
    const fertig = await anruf(`/api/snapmem/hochladen/${start.id}/fertig`);
    if (!fertig.ok) throw new Error(fertig.text);
    gewaehlt.add(fertig.datei);
  }

  async function dateienHochladen(dateien) {
    meldung = '';
    for (const datei of dateien) {
      try { await hochladenDatei(datei); }
      catch (e) { meldung = `${datei.name}: ${e.message}`; break; }
    }
    hochladen = null;
    await laden();
  }

  function laufKarte(lauf) {
    const k = el('div', 'ausgabe');
    const modusName = (MODI.find((m) => m[0] === lauf.modus) || [lauf.modus, lauf.modus])[1];
    k.append(el('strong', '', `${modusName} — ${ZUSTAND[lauf.zustand] || lauf.zustand}`));
    if (lauf.zustand === 'laeuft' || lauf.zustand === 'wartet') {
      const bar = el('div', 'fortschritt');
      const fuell = el('div', 'fortschritt-fuellung');
      fuell.style.width = `${lauf.gesamt ? Math.round((100 * lauf.fertig) / lauf.gesamt) : 0}%`;
      bar.append(fuell);
      k.append(bar, el('div', 'leise', lauf.gesamt ? `${lauf.fertig} von ${lauf.gesamt} · ${lauf.schritt || ''}` : 'Start …'));
    }
    if (lauf.zustand === 'fehler') k.append(el('div', '', lauf.meldung || 'Unbekannter Fehler.'));
    if (lauf.zustand === 'fertig') {
      const z = lauf.zaehler || {};
      const zeilen = Object.entries(z).map(([n, v]) => `${n}: ${v}`);
      k.append(el('div', 'leise', `${groesse(lauf.groesse || 0)} · ${dauer(lauf.dauer || 0)}`));
      if (zeilen.length) k.append(el('div', 'leise', zeilen.join(' · ')));
      if (lauf.ergebnis) {
        const a = el('a', 'voll-knopf', 'ZIP herunterladen');
        a.href = `/api/snapmem/auftrag/${lauf.id}/ergebnis`;
        a.setAttribute('download', '');
        a.style.display = 'block'; a.style.textAlign = 'center'; a.style.textDecoration = 'none';
        k.append(a);
      }
    }
    if (lauf.zustand !== 'laeuft') {
      k.append(knopf('Entfernen', 'klein', async () => { await anruf(`/api/snapmem/auftrag/${lauf.id}/loeschen`); laden(); }));
    }
    return k;
  }

  function zeichnen() {
    wurzel.replaceChildren();
    wurzel.append(el('p', 'leise',
      'Lade deine Snapchat-Exporte (.zip) hoch, wähle, was du willst, und hole das Ergebnis als ZIP ab. '
      + 'Alles bleibt auf dem Server. Zeit und Ort gibt es nur, wo der Export sie eindeutig zuordnen lässt; '
      + 'sonst steht nur das Datum drin.'));

    const label = el('label', 'hochladen');
    const eingabe = el('input'); eingabe.type = 'file'; eingabe.accept = '.zip'; eingabe.multiple = true; eingabe.hidden = true;
    eingabe.addEventListener('change', () => { const d = [...eingabe.files]; eingabe.value = ''; if (d.length) dateienHochladen(d); });
    label.append(eingabe, el('span', '', hochladen ? 'Lädt hoch …' : 'Export-ZIPs auswählen …'));
    wurzel.append(label);
    if (hochladen) {
      const bar = el('div', 'fortschritt'); const f = el('div', 'fortschritt-fuellung');
      f.style.width = `${Math.round(hochladen.anteil * 100)}%`; bar.append(f);
      wurzel.append(bar, el('div', 'leise', hochladen.text));
    }
    if (meldung) wurzel.append(el('div', 'ausgabe', meldung));

    if (stand.uploads.length) {
      const block = el('div', 'block');
      block.append(el('h2', '', 'Hochgeladene ZIPs'));
      for (const u of stand.uploads) {
        const zeile = el('div', 'ausgabe');
        const cb = el('input'); cb.type = 'checkbox'; cb.checked = gewaehlt.has(u.datei);
        cb.addEventListener('change', () => { cb.checked ? gewaehlt.add(u.datei) : gewaehlt.delete(u.datei); });
        zeile.append(cb, document.createTextNode(` ${u.name} · ${groesse(u.groesse)} `),
          knopf('Löschen', 'klein', async () => { gewaehlt.delete(u.datei); await anruf(`/api/snapmem/upload/${u.id}/loeschen`); laden(); }));
        block.append(zeile);
      }
      wurzel.append(block);

      const wahl = el('div', 'block');
      wahl.append(el('h2', '', 'Was willst du haben?'));
      for (const [wert, titel, text] of MODI) {
        const z = el('label', 'ausgabe'); z.style.display = 'block';
        const r = el('input'); r.type = 'radio'; r.name = 'snapmem-modus'; r.checked = modus === wert;
        r.addEventListener('change', () => { modus = wert; });
        z.append(r, document.createTextNode(' '), el('strong', '', titel), el('div', 'leise', text));
        wahl.append(z);
      }
      const start = knopf('Los', 'voll-knopf', async () => {
        const zips = stand.uploads.map((u) => u.datei).filter((d) => gewaehlt.has(d));
        if (!zips.length) { meldung = 'Bitte mindestens eine ZIP ankreuzen.'; zeichnen(); return; }
        const r = await anruf('/api/snapmem/auftrag', 'POST', { zips, modus });
        meldung = r.ok ? '' : r.text;
        laden();
      });
      wahl.append(start);
      wurzel.append(wahl);
    } else if (!hochladen) {
      wurzel.append(el('p', 'leer-klein', 'Noch keine ZIP hochgeladen.'));
    }

    if (stand.laeufe.length) {
      const block = el('div', 'block');
      block.append(el('h2', '', 'Läufe'));
      for (const l of stand.laeufe) block.append(laufKarte(l));
      wurzel.append(block);
    }
  }

  async function laden() {
    try {
      const r = await fetch('/api/snapmem', { cache: 'no-store' });
      if (r.ok) {
        stand = await r.json();
        for (const u of stand.uploads) if (!gewaehlt.has(u.datei) && !wurzel.dataset.gesehen?.includes(u.datei)) gewaehlt.add(u.datei);
        wurzel.dataset.gesehen = stand.uploads.map((u) => u.datei).join('|');
      }
    } catch (_) { meldung = 'Friday ist nicht erreichbar.'; }
    zeichnen();
    clearTimeout(takt);
    if (stand.laeufe.some((l) => l.zustand === 'laeuft' || l.zustand === 'wartet')) takt = setTimeout(laden, 2000);
  }

  for (const b of document.querySelectorAll('#studio .reiter button')) {
    b.addEventListener('click', () => { if (b.dataset.reiter === 'memories') setTimeout(laden, 0); else clearTimeout(takt); });
  }
})();
