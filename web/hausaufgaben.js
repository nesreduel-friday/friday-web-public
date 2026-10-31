/* Extras-Seite: Reiter „HA“ — Hausaufgaben aus dem Schulmanager.

   Liste der letzten 7 Tage („7 Tage mehr“ holt je eine Woche dazu), jede Karte
   zugeklappt. Antippen öffnet sie: Aufgabe (wie im Schulmanager), Einschätzung,
   Materialien (welche Buchseiten Friday angesehen hat) und die Bearbeitung
   (Text, Diagramme, Bilder). Darunter die Schulbücher: hochladen (mehrere PDFs
   je Buch), Lesestand, Seiten ansehen — und die Goodnotes-Hefte: Stand,
   Suche, welche Ordner Friday liest.

   Alles per textContent, nie als HTML: Texte kommen von der Schule und vom
   Modell. Diagramme (SVG) werden nur als <img> gezeigt — so läuft darin nichts. */
(() => {
  const { $ } = window.Friday;
  const SCHRITT = 7;

  const el = (tag, klasse, text) => {
    const e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };
  const knopf = (text, klasse, aktion) => {
    const b = el('button', klasse, text);
    b.type = 'button';
    b.addEventListener('click', (e) => { e.stopPropagation(); aktion(b); });
    return b;
  };
  const antwort = async (r) => {
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `Fehler ${r.status}`);
    return d;
  };
  const holen = async (url) => antwort(await fetch(url, { cache: 'no-store' }));
  const senden = async (url) => antwort(await fetch(url, { method: 'POST' }));
  const tagText = (iso) => {
    try { return new Date(`${iso}T12:00:00`).toLocaleDateString('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit' }); }
    catch (_) { return iso; }
  };
  const abschnitt = (titel) => { const s = el('div', 'ha-abschnitt'); s.append(el('h4', '', titel)); return s; };
  const MACHBAR = {
    ja: ['✓ machbar', 'status-ok'],
    teilweise: ['◐ teilweise', 'ha-teilweise'],
    nein: ['✗ nicht machbar', 'status-fehler'],
  };
  const QUELLE = { buch: 'Buch', arbeitsblatt: 'Arbeitsblatt', heft: 'Heft', sonstiges: '' };
  const LOESUNG = {
    fertig: ['● bearbeitet', 'status-ok'],
    laeuft: ['● Friday arbeitet …', 'ha-teilweise'],
    fehler: ['● nicht bearbeitet', 'status-fehler'],
  };

  let tage = SCHRITT;
  let stand = null;
  let buecher = null;
  let takt = null;
  const offen = new Set();       // aufgeklappte Einträge
  const details = {};            // id → Detail (samt Stand, zu dem es geholt wurde)
  let buecherOffen = false;
  let goodnotes = null;
  let notizenOffen = false;
  let suchText = '';
  let suchTreffer = null;
  const offeneTreffer = {};      // "heft-nr" → Abschrift (oder null, solange sie lädt)
  let meldung = '';

  const aktiv = () => location.hash === '#schule' && !$('r-ha').hidden;

  /* ---------- Eine Hausaufgabe ---------- */

  function teilZeile(t) {
    const z = el('div', 'ha-teil');
    const wo = [QUELLE[t.quelle] || ''];
    if (t.seiten.length) wo.push(`S. ${t.seiten.join(', ')}`);
    if (t.aufgaben.length) wo.push(`Nr. ${t.aufgaben.join(', ')}`);
    const [zeichen, farbe] = MACHBAR[t.machbar] || MACHBAR.nein;
    const kopf = el('div', 'kopfzeile');
    kopf.append(el('span', '', [wo.filter(Boolean).join(' · '), t.was].filter(Boolean).join(' — ')),
                el('span', `klein ${farbe}`, zeichen.slice(0, 1)));
    z.append(kopf);
    if (t.grund) z.append(el('div', 'klein', t.grund));
    return z;
  }

  function materialien(d) {
    const s = abschnitt('Materialien');
    const liste = (d.loesung && d.loesung.materialien) || [];
    if (!liste.length) {
      s.append(el('p', 'leer-klein', d.loesung ? 'Keine Materialien verwendet.'
        : (d.buecher.length ? `Verfügbar: ${d.buecher.map((b) => b.titel).join(', ')}` : `Für ${d.fach} ist noch kein Buch eingescannt.`)));
      return s;
    }
    const reihe = el('div', 'ha-seiten');
    for (const m of liste) {
      const a = el('a', `ha-seite${m.genutzt ? '' : ' nebenbei'}`);
      a.href = m.bild_url || `/api/buecher/${m.buch}/seite/${m.seite}`;
      a.target = '_blank';
      a.rel = 'noopener';
      a.addEventListener('click', (e) => e.stopPropagation());
      const img = el('img');
      img.src = a.href;
      img.alt = `${m.titel} S. ${m.seite}`;
      img.loading = 'lazy';
      a.append(img, el('span', '', `${m.art === 'notiz' ? '✎ ' : ''}${m.titel} S. ${m.seite}`),
               el('span', 'klein', m.genutzt ? 'verwendet' : 'nur angesehen'));
      reihe.append(a);
    }
    s.append(reihe);
    return s;
  }

  function block(d, b) {
    const k = el('div', 'ha-block');
    if (b.titel) k.append(el('div', 'titel', b.titel));
    if (b.art === 'text') k.append(el('div', 'text', b.text));
    if (b.art === 'diagramm' && b.svg) {
      const img = el('img', 'ha-bild');
      img.alt = b.titel || 'Diagramm';
      img.src = URL.createObjectURL(new Blob([b.svg], { type: 'image/svg+xml' }));
      k.append(img);
    }
    if (b.art === 'bild' && b.datei) {
      const img = el('img', 'ha-bild');
      img.alt = b.titel || 'Bild';
      img.src = `/api/hausaufgaben/eintrag/${d.id}/bild/${b.datei}`;
      k.append(img);
    }
    if (b.art === 'bild' && !b.datei && b.beschreibung) k.append(el('div', 'klein', `Bildidee: ${b.beschreibung}`));
    if (b.fehler) k.append(el('div', 'klein status-fehler', b.fehler));
    return k;
  }

  function bearbeitung(d) {
    const s = abschnitt('Bearbeitung');
    const l = d.loesung;
    if (d.loesung_status === 'laeuft') s.append(el('p', 'leise ha-teilweise', 'Friday arbeitet gerade daran …'));
    if (d.loesung_status === 'fehler') s.append(el('p', 'klein status-fehler', d.loesung_fehler || 'Fehlgeschlagen.'));
    if (l) {
      if (l.zusammenfassung) s.append(el('p', '', l.zusammenfassung));
      for (const b of l.bloecke) s.append(block(d, b));
      if (l.offen) { const o = el('div', 'ha-offen'); o.append(el('b', '', 'Für dich: '), document.createTextNode(l.offen)); s.append(o); }
      s.append(el('p', 'leise', `Bearbeitet ${d.geloest ? new Date(d.geloest).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }) : ''} · ${l.modell}`));
    } else if (!d.loesung_status) {
      s.append(el('p', 'leer-klein', 'Noch nicht bearbeitet.'));
    }
    if (d.loesung_status !== 'laeuft') {
      const reihe = el('div', 'knoepfe');
      const b = knopf(l ? 'Neu bearbeiten' : 'Jetzt bearbeiten', l ? '' : 'ja', async (k) => {
        k.disabled = true;
        try { await senden(`/api/hausaufgaben/eintrag/${d.id}/bearbeiten`); delete details[d.id]; await laden(); }
        catch (e) { k.disabled = false; s.append(el('p', 'klein status-fehler', e.message)); }
      });
      if (stand && !stand.bearbeiten_frei) {
        b.disabled = true;
        s.append(el('p', 'leise', 'Zum Bearbeiten die Freigabe „Hausaufgaben bearbeiten“ einschalten.'));
      }
      reihe.append(b);
      s.append(reihe);
    }
    return s;
  }

  function aufgeklappt(e) {
    const box = el('div', 'details');
    box.addEventListener('click', (x) => x.stopPropagation());
    const d = details[e.id];
    if (!d) { box.append(el('p', 'leer-klein', 'lädt …')); return box; }
    const a = abschnitt('Aufgabe (Schulmanager)');
    a.append(el('div', 'text', d.text), el('div', 'klein', `${d.fach} · Kurs ${d.kurs} · gestellt ${tagText(d.datum)}`));
    box.append(a);
    if (d.auswertung && d.auswertung.teile.length) {
      const s = abschnitt('Einschätzung');
      for (const t of d.auswertung.teile) s.append(teilZeile(t));
      box.append(s);
    }
    box.append(materialien(d), bearbeitung(d));
    return box;
  }

  function karte(e) {
    const k = el('div', `eintrag klickbar${offen.has(e.id) ? ' gewaehlt' : ''}`);
    const kopf = el('div', 'kopfzeile');
    const a = e.auswertung;
    let marke;
    if (a) {
      const [text, farbe] = MACHBAR[a.machbar] || MACHBAR.nein;
      marke = el('span', `klein ${farbe}`, text);
    } else if (e.status === 'fehler') {
      marke = el('span', 'klein status-fehler', 'nicht ausgewertet');
    } else {
      marke = el('span', 'klein', 'wird geprüft …');
    }
    kopf.append(el('span', 'titel', `${offen.has(e.id) ? '▾' : '▸'} ${e.fach}`), marke);
    k.append(kopf, el('div', 'text ha-kurz', (a && a.kurz) || e.text.split('\n').filter(Boolean).join(' · ')));
    if (LOESUNG[e.loesung_status]) {
      const [text, farbe] = LOESUNG[e.loesung_status];
      k.append(el('div', `klein ${farbe}`, text));
    }
    if (e.fehler) k.append(el('div', 'klein status-fehler', e.fehler));
    k.addEventListener('click', () => umschalten(e.id));
    if (offen.has(e.id)) k.append(aufgeklappt(e));
    return k;
  }

  async function detailHolen(id) {
    try { details[id] = await holen(`/api/hausaufgaben/eintrag/${id}`); }
    catch (x) { details[id] = null; meldung = x.message; }
  }

  async function umschalten(id) {
    if (offen.has(id)) { offen.delete(id); zeichnen(); return; }
    offen.add(id);
    zeichnen();
    await detailHolen(id);
    zeichnen();
  }

  /* ---------- Bücher ---------- */

  function hochladen(url, formular, info) {
    // XHR statt fetch: nur so gibt es einen Fortschritt — Scans sind groß.
    return new Promise((fertig, fehler) => {
      const x = new XMLHttpRequest();
      x.open('POST', url);
      x.upload.onprogress = (p) => { if (p.lengthComputable) info.textContent = `lädt hoch … ${Math.round(100 * p.loaded / p.total)} %`; };
      x.onload = () => {
        let d = {};
        try { d = JSON.parse(x.responseText); } catch (_) { /* leer */ }
        if (x.status >= 200 && x.status < 300) fertig(d); else fehler(new Error(d.detail || `Fehler ${x.status}`));
      };
      x.onerror = () => fehler(new Error('Verbindung abgebrochen.'));
      x.send(formular);
    });
  }

  function dateiWahl(text, aktion) {
    const label = el('label', 'leise-knopf ha-datei', text);
    const input = el('input');
    input.type = 'file';
    input.accept = 'application/pdf,.pdf';
    input.multiple = true;
    input.hidden = true;
    input.addEventListener('change', () => { if (input.files.length) aktion([...input.files]); });
    label.append(input);
    return label;
  }

  function buchKarte(b) {
    const k = el('div', 'eintrag');
    const kopf = el('div', 'kopfzeile');
    const zustand = {
      laeuft: [`Friday liest … ${b.fortschritt}/${b.gesamt} Scans`, 'klein ha-teilweise'],
      neu: [b.fortschritt ? `unterbrochen bei ${b.fortschritt}/${b.gesamt} — geht gleich weiter` : 'wartet aufs Lesen', 'klein'],
      fertig: ['gelesen', 'klein status-ok'],
      fehler: ['Fehler', 'klein status-fehler'],
    }[b.status] || [b.status, 'klein'];
    kopf.append(el('span', 'titel', `${b.titel} · ${b.fach}`), el('span', zustand[1], zustand[0]));
    k.append(kopf);
    const pdfs = b.dateien.reduce((n, d) => n + d.seiten, 0);
    const zeilen = [`${b.dateien.length} PDF${b.dateien.length === 1 ? '' : 's'} · ${pdfs} Scans`];
    if (b.bereich) zeilen.push(`${b.buchseiten} Buchseiten (S. ${b.bereich[0]}–${b.bereich[1]})`);
    k.append(el('div', 'klein', zeilen.join(' · ')));
    if (b.bereich) {
      const hinweise = [];
      if (b.fehlend_anzahl) hinweise.push(`Fehlen: S. ${b.fehlend.join(', ')}${b.fehlend_anzahl > b.fehlend.length ? ' …' : ''}`);
      if (b.doppelt.length) hinweise.push(`doppelt: S. ${b.doppelt.join(', ')}`);
      if (b.geschaetzt) hinweise.push(`${b.geschaetzt} Seitenzahlen geschätzt`);
      if (b.ohne_zahl) hinweise.push(`${b.ohne_zahl} ohne erkennbare Zahl`);
      if (hinweise.length) k.append(el('div', 'klein', hinweise.join(' · ')));
    }
    if (b.fehler) k.append(el('div', `klein ${b.status === 'fehler' ? 'status-fehler' : ''}`, b.fehler));
    const info = el('div', 'klein');
    const reihe = el('div', 'knoepfe');
    if (b.status === 'fertig') {
      const feld = el('input', 'ha-zahl');
      feld.type = 'number';
      feld.min = '1';
      feld.placeholder = 'S.';
      feld.setAttribute('aria-label', 'Buchseite');
      reihe.append(feld, knopf('Seite ansehen', '', () => {
        if (feld.value) window.open(`/api/buecher/${b.id}/seite/${parseInt(feld.value, 10)}`, '_blank', 'noopener');
      }));
    }
    if (b.status !== 'laeuft') {
      reihe.append(knopf(b.status === 'neu' ? 'Jetzt lesen' : 'Neu lesen', '', async (x) => {
        x.disabled = true;
        try { await senden(`/api/buecher/${b.id}/lesen`); await buecherLaden(); }
        catch (e) { info.textContent = e.message; x.disabled = false; }
      }));
      reihe.append(dateiWahl('PDFs ergänzen', async (dateien) => {
        const f = new FormData();
        for (const d of dateien) f.append('dateien', d);
        try { await hochladen(`/api/buecher/${b.id}/dateien`, f, info); await buecherLaden(); }
        catch (e) { info.textContent = e.message; }
      }));
      reihe.append(knopf('Entfernen', 'weg', async (x) => {
        if (!confirm(`„${b.titel}“ entfernen? Friday benutzt es dann nicht mehr. Du kannst es unter „Entfernte Bücher“ wiederherstellen.`)) return;
        x.disabled = true;
        try { await senden(`/api/buecher/${b.id}/entfernen`); await buecherLaden(); }
        catch (e) { info.textContent = e.message; x.disabled = false; }
      }));
    }
    k.append(reihe, info);
    return k;
  }

  function neuesBuch() {
    const f = el('div', 'eintrag');
    f.append(el('div', 'titel', 'Buch hochladen'));
    f.append(el('div', 'klein', 'Mehrere PDFs sind in Ordnung, auch Scans mit zwei Buchseiten. Friday liest danach '
                                + 'jede Seite einmal und merkt sich, wo welche Buchseite liegt.'));
    const reihe = el('div', 'neu-fakt');
    const titel = el('input');
    titel.placeholder = 'Titel, z. B. Green Line 7';
    titel.setAttribute('aria-label', 'Buchtitel');
    const fach = el('select', 'auswahl');
    fach.setAttribute('aria-label', 'Fach');
    for (const name of (buecher && buecher.faecher) || []) { const o = el('option', '', name); o.value = name; fach.append(o); }
    fach.value = 'Englisch';
    reihe.append(titel, fach);
    const info = el('div', 'klein');
    const wahl = dateiWahl('PDFs wählen und hochladen', async (dateien) => {
      if (!titel.value.trim()) { info.textContent = 'Bitte erst einen Titel eintragen.'; return; }
      const daten = new FormData();
      daten.append('titel', titel.value.trim());
      daten.append('fach', fach.value);
      for (const d of dateien) daten.append('dateien', d);
      try {
        const d = await hochladen('/api/buecher', daten, info);
        info.textContent = d.gestartet ? 'Hochgeladen — Friday liest das Buch jetzt.'
          : 'Hochgeladen. Zum Lesen die Freigabe „Hausaufgaben bearbeiten“ einschalten, dann „Jetzt lesen“.';
        titel.value = '';
        await buecherLaden();
      } catch (e) { info.textContent = e.message; }
    });
    wahl.classList.add('voll-knopf');
    f.append(reihe, wahl, info);
    return f;
  }

  function buecherBlock() {
    const b = el('div', 'block');
    const liste = (buecher && buecher.buecher) || [];
    const kopf = el('div', 'eintrag klickbar');
    kopf.append(el('span', 'titel', `${buecherOffen ? '▾' : '▸'} Schulbücher (${liste.length})`));
    kopf.addEventListener('click', () => { buecherOffen = !buecherOffen; zeichnen(); });
    b.append(kopf);
    if (!buecherOffen) return b;
    const inhalt = el('div', 'liste ha-buecher');
    if (buecher && !buecher.frei) {
      inhalt.append(el('p', 'leise', 'Die Freigabe „Hausaufgaben bearbeiten“ ist aus — hochladen geht, gelesen wird erst danach.'));
    }
    for (const x of liste) inhalt.append(buchKarte(x));
    inhalt.append(neuesBuch());
    const entfernt = (buecher && buecher.entfernt) || [];
    if (entfernt.length) {
      const k = el('div', 'eintrag');
      k.append(el('div', 'titel', `Entfernte Bücher (${entfernt.length})`));
      for (const x of entfernt) {
        const z = el('div', 'kopfzeile');
        z.append(el('span', 'klein', `${x.titel} · ${x.fach}`),
          knopf('Wiederherstellen', 'leise-knopf', async (y) => {
            y.disabled = true;
            try { await senden(`/api/buecher/${x.id}/wiederherstellen`); await buecherLaden(); }
            catch (e) { y.disabled = false; meldung = e.message; zeichnen(); }
          }),
          knopf('Endgültig löschen', 'weg', async (y) => {
            if (!confirm(`„${x.titel}“ endgültig löschen? Die eingescannten PDFs werden von der Platte entfernt. Das lässt sich nicht rückgängig machen.`)) return;
            y.disabled = true;
            try { await senden(`/api/buecher/${x.id}/loeschen`); await buecherLaden(); }
            catch (e) { y.disabled = false; meldung = e.message; zeichnen(); }
          }));
        k.append(z);
      }
      inhalt.append(k);
    }
    b.append(inhalt);
    return b;
  }

  /* ---------- Goodnotes ---------- */

  function trefferKarte(t) {
    const schluessel = `${t.heft}-${t.nr}`;
    const k = el('div', 'eintrag klickbar');
    const kopf = el('div', 'kopfzeile');
    kopf.append(el('span', 'titel', `${t.name} · S. ${t.nr}`), el('span', 'klein', t.ordner || ''));
    k.append(kopf);
    if (t.titel) k.append(el('div', 'klein', t.titel));
    k.append(el('div', 'text ha-kurz', (t.stelle || '').replace(/\s+/g, ' ')));
    k.addEventListener('click', async () => {
      if (schluessel in offeneTreffer) { delete offeneTreffer[schluessel]; zeichnen(); return; }
      offeneTreffer[schluessel] = null;
      zeichnen();
      try { offeneTreffer[schluessel] = await holen(`/api/goodnotes/text/${t.heft}/${t.nr}`); }
      catch (e) { offeneTreffer[schluessel] = { text: e.message }; }
      zeichnen();
    });
    if (schluessel in offeneTreffer) {
      const d = el('div', 'details');
      d.addEventListener('click', (x) => x.stopPropagation());
      const a = el('a', 'ha-seite');
      a.href = `/api/goodnotes/seite/${t.heft}/${t.nr}`;
      a.target = '_blank';
      a.rel = 'noopener';
      const img = el('img');
      img.src = a.href;
      img.alt = `${t.name} S. ${t.nr}`;
      img.loading = 'lazy';
      a.append(img, el('span', 'klein', 'Seite öffnen'));
      const x = offeneTreffer[schluessel];
      d.append(a, el('div', 'text', x ? (x.text || '(leer)') : 'lädt …'));
      k.append(d);
    }
    return k;
  }

  async function suchen(text) {
    suchText = text;
    if (!text.trim()) { suchTreffer = null; zeichnen(); return; }
    try { suchTreffer = (await holen(`/api/goodnotes/suche?q=${encodeURIComponent(text)}`)).treffer; }
    catch (e) { suchTreffer = []; meldung = e.message; }
    zeichnen();
  }

  async function ordnerSetzen(name, lesen) {
    const aus = goodnotes.ordner.filter((o) => o.ausgeschlossen && o.name !== name).map((o) => o.name);
    if (!lesen) aus.push(name);
    try { await antwort(await fetch('/api/goodnotes/ordner', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ausgeschlossen: aus }),
    })); } catch (e) { meldung = e.message; }
    await laden();
  }

  function notizenBlock() {
    const b = el('div', 'block');
    const g = goodnotes;
    const st = g ? g.stand : { fertig: 0, gesamt: 0, offen: 0 };
    const kopf = el('div', 'eintrag klickbar');
    const k = el('div', 'kopfzeile');
    k.append(el('span', 'titel', `${notizenOffen ? '▾' : '▸'} Goodnotes-Hefte`),
             el('span', `klein${g && g.laeuft ? ' ha-teilweise' : ''}`,
                g && g.laeuft ? 'gleicht ab …' : `${st.fertig}/${st.gesamt} Seiten gelesen`));
    kopf.append(k);
    kopf.addEventListener('click', () => { notizenOffen = !notizenOffen; zeichnen(); });
    b.append(kopf);
    if (!notizenOffen || !g) return b;
    const inhalt = el('div', 'liste ha-buecher');
    if (!g.frei) {
      inhalt.append(el('p', 'leise', 'Die Freigabe „Goodnotes-Hefte“ ist aus. Einschalten auf der Freigaben-Seite.'));
      b.append(inhalt);
      return b;
    }
    // Stand und Abgleich
    const standKarte = el('div', 'eintrag');
    if (st.gesamt) {
      const balken = el('div', 'fortschritt');
      const fuellung = el('div', 'fortschritt-fuellung');
      fuellung.style.width = `${Math.round(100 * st.fertig / st.gesamt)}%`;
      balken.append(fuellung);
      standKarte.append(balken);
    }
    const zeilen = [`Ordner „${g.wurzel}“ im IServ-WebDAV: ${g.hefte.length} Hefte, ${st.gesamt} Seiten, ${st.fertig} abgeschrieben`];
    if (st.offen) zeilen.push(`${st.offen} warten`);
    if (st.fehlerhaft) zeilen.push(`${st.fehlerhaft} unlesbar`);
    standKarte.append(el('div', 'klein', zeilen.join(' · ')));
    if (g.laeuft && g.phase) standKarte.append(el('div', 'klein ha-teilweise', `Friday ${g.phase} …`));
    if (g.fehler) standKarte.append(el('div', 'klein status-fehler', g.fehler));
    if (!g.laeuft && g.letzter) {
      standKarte.append(el('div', 'leise', `Zuletzt abgeglichen ${new Date(g.letzter * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })}`
                                           + (g.ergebnis ? ` · ${g.ergebnis.geladen} Hefte geladen, ${g.ergebnis.gelesen} Seiten gelesen` : '')
                                           + ' · automatisch alle 30 Minuten'));
    }
    const reihe = el('div', 'knoepfe');
    const knopfAbgleich = knopf('Jetzt abgleichen', '', async (x) => {
      x.disabled = true;
      try { await senden('/api/goodnotes/abgleichen'); await laden(); } catch (e) { meldung = e.message; zeichnen(); }
    });
    knopfAbgleich.disabled = g.laeuft;
    reihe.append(knopfAbgleich);
    standKarte.append(reihe);
    inhalt.append(standKarte);

    // Suche
    const form = el('form', 'neu-fakt');
    const feld = el('input');
    feld.type = 'search';
    feld.placeholder = 'In den Heften suchen, z. B. Fotosynthese';
    feld.value = suchText;
    feld.setAttribute('aria-label', 'In den Heften suchen');
    const los = el('button', '', 'Suchen');
    los.type = 'submit';
    form.append(feld, los);
    form.addEventListener('submit', (e) => { e.preventDefault(); feld.blur(); suchen(feld.value); });
    inhalt.append(form);
    if (suchTreffer) {
      if (!suchTreffer.length) inhalt.append(el('p', 'leer-klein', 'Nichts gefunden.'));
      for (const t of suchTreffer) inhalt.append(trefferKarte(t));
    }
    inhalt.append(el('p', 'leise', 'Fragen zu den Heften kannst du Friday auch direkt im Chat oder beim Sprechen stellen.'));

    // Ordner
    const ordnerKarte = el('div', 'eintrag');
    ordnerKarte.append(el('div', 'titel', 'Welche Ordner Friday liest'));
    for (const o of g.ordner) {
      const z = el('label', 'check klein ha-ordner');
      const box = el('input');
      box.type = 'checkbox';
      box.checked = !o.ausgeschlossen;
      box.addEventListener('change', () => ordnerSetzen(o.name, box.checked));
      z.append(box, el('span', '', `${o.name || '(oberste Ebene)'} — ${o.hefte} Hefte, ${o.gelesen}/${o.seiten} Seiten`));
      ordnerKarte.append(z);
    }
    if (!g.ordner.length) ordnerKarte.append(el('p', 'leer-klein', 'Noch nicht abgeglichen.'));
    inhalt.append(ordnerKarte);
    b.append(inhalt);
    return b;
  }

  /* ---------- Seite ---------- */

  function zeichnen() {
    const ziel = $('ha-inhalt');
    if (!stand) return;
    // Beim Neuzeichnen nicht das Eingabefeld eines halb ausgefüllten Formulars verlieren.
    const aktivesFeld = document.activeElement;
    if (aktivesFeld && ziel.contains(aktivesFeld) && ['INPUT', 'SELECT'].includes(aktivesFeld.tagName)) return;
    ziel.textContent = '';
    if (!stand.aktiv) {
      ziel.append(el('p', 'leise', 'Die Freigabe „Schulmanager“ ist aus oder hat keine Zugangsdaten. '
                                  + 'Einschalten auf der Freigaben-Seite.'));
      ziel.append(buecherBlock(), notizenBlock());
      return;
    }
    const liste = stand.eintraege || [];
    const zahl = (w) => liste.filter((e) => e.auswertung && e.auswertung.machbar === w).length;
    const fertig = liste.filter((e) => e.loesung_status === 'fertig').length;
    ziel.append(el('p', 'leise', `Letzte ${stand.tage} Tage (gestellt ab ${tagText(stand.ab)}): ${liste.length} Aufgaben`
                                 + (liste.length ? ` · ${zahl('ja')} machbar, ${zahl('teilweise')} teilweise, ${zahl('nein')} nicht · ${fertig} bearbeitet` : '')
                                 + (stand.auswertung_laeuft ? ' · Friday wertet aus …' : '')));
    if (meldung) ziel.append(el('p', 'klein status-fehler', meldung));
    if (!liste.length) ziel.append(el('p', 'leer-klein', 'In diesem Zeitraum sind keine Hausaufgaben eingetragen.'));
    let tag = null;
    for (const e of liste) {
      if (e.datum !== tag) {
        tag = e.datum;
        ziel.append(el('h2', 'ha-tag', `gestellt ${tagText(tag)}`));
      }
      ziel.append(karte(e));
    }
    if (stand.weitere) {
      const b = knopf(`${SCHRITT} Tage mehr`, 'voll-knopf', (x) => { tage += SCHRITT; x.disabled = true; x.textContent = 'lädt …'; laden(); });
      ziel.append(b);
    } else if (liste.length) {
      ziel.append(el('p', 'leer-klein', 'Ältere Einträge gibt es im Schulmanager nicht.'));
    }
    ziel.append(el('p', 'leise', 'Der Schulmanager kennt keinen Abgabetermin — angezeigt ist der Tag, an dem die Aufgabe gestellt wurde.'));
    ziel.append(buecherBlock(), notizenBlock());
  }

  function taktSetzen(laeuft) {
    if (laeuft && !takt) takt = setInterval(laden, 4000);
    if (!laeuft && takt) { clearInterval(takt); takt = null; }
  }

  async function buecherLaden() {
    try { buecher = await holen('/api/buecher'); } catch (_) { buecher = null; }
    zeichnen();
  }

  async function laden() {
    if (!aktiv()) { taktSetzen(false); return; }
    try {
      [stand, buecher, goodnotes] = await Promise.all([holen(`/api/hausaufgaben?tage=${tage}`),
                                                       holen('/api/buecher').catch(() => buecher),
                                                       holen('/api/goodnotes').catch(() => goodnotes)]);
      meldung = '';
    } catch (e) {
      $('ha-inhalt').textContent = e.message || 'Die Hausaufgaben sind gerade nicht erreichbar.';
      taktSetzen(false);
      return;
    }
    // Aufgeklappte Einträge neu holen, wenn sich ihr Stand geändert hat.
    const nachStand = Object.fromEntries((stand.eintraege || []).map((e) => [e.id, e]));
    await Promise.all([...offen].filter((id) => nachStand[id] && (!details[id]
      || details[id].loesung_status !== nachStand[id].loesung_status
      || (!details[id].auswertung && nachStand[id].auswertung))).map(detailHolen));
    zeichnen();
    const buchLaeuft = ((buecher && buecher.buecher) || []).some((b) => b.status === 'laeuft');
    taktSetzen(Boolean(stand.auswertung_laeuft || stand.bearbeitung_laeuft || buchLaeuft || (goodnotes && goodnotes.laeuft)));
  }

  // Beim Betreten der Seite wieder mit dem kurzen Zeitraum beginnen.
  window.addEventListener('hashchange', () => { if (aktiv()) tage = SCHRITT; laden(); });
  document.addEventListener('schule-reiter', () => { if (aktiv()) tage = SCHRITT; laden(); });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') laden(); });
  laden();
})();
