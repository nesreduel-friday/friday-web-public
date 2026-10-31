/* Schule-Seite: Reiter „Noten“ — Oberstufe Niedersachsen.

   Oben die Semester der Qualifikationsphase (12/1 bis 13/2) und eine Übersicht
   über alle. Jedes Fach ist nur in den Semestern da, in denen es belegt ist
   (das Seminarfach läuft nur drei), mit seiner Rolle (Leistungskurs P1–P3,
   P4, P5) und den Wochenstunden — eingestellt über „Fächer“, wie in der
   Fächerwahl der Schule. In jedem Fach legst du selbst fest, woraus die Note
   besteht (Bereiche mit Gewichtung), und jede Note kann sagen, wie viel sie im
   Bereich zählt („höchstens 20 %“). Gerechnet wird auf dem Server in
   Notenpunkten (0–15); hier wird nur gezeigt und eingegeben.

   Alles per textContent, nie als HTML. */
(() => {
  const { $ } = window.Friday;

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
    if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : 'Das hat nicht geklappt — bitte die Eingaben prüfen.');
    return d;
  };
  const holen = async (url) => antwort(await fetch(url, { cache: 'no-store' }));
  const schicken = async (methode, url, daten) => antwort(await fetch(url, {
    method: methode, headers: { 'Content-Type': 'application/json' }, body: daten === undefined ? undefined : JSON.stringify(daten),
  }));

  const zahl = (x, stellen = 1) => (x === null || x === undefined ? '–'
    : Number(x).toLocaleString('de-DE', { maximumFractionDigits: stellen }));
  const heute = () => new Date().toLocaleDateString('sv-SE');             // JJJJ-MM-TT, lokal
  const kurzDatum = (iso) => {
    try { return new Date(`${iso}T12:00:00`).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' }); }
    catch (_) { return iso; }
  };
  const stufe = (p) => (p >= 10 ? 'no-gut' : (p >= 5 ? 'no-mittel' : 'no-schlecht'));

  /* Note ↔ Text: 2- ist 2,33 (schlechter), 2+ ist 1,67. */
  const noteZuText = (n) => {
    const basis = Math.round(n), diff = n - basis;
    if (Math.abs(diff) < 0.05) return String(basis);
    if (Math.abs(Math.abs(diff) - 1 / 3) < 0.05) return `${basis}${diff > 0 ? '-' : '+'}`;
    return zahl(n, 2);
  };
  const textZuNote = (t) => {
    const s = String(t || '').trim().replace(',', '.').replace(/[−–]/g, '-');
    const m = s.match(/^([1-6])\s*([+-])?$/);
    if (m) return Math.min(6, Math.max(0.5, Number(m[1]) + (m[2] === '+' ? -1 / 3 : (m[2] === '-' ? 1 / 3 : 0))));
    const z = Number(s);
    return s && Number.isFinite(z) ? z : null;
  };
  const punkteAus = (skala, wert, max) => (skala === 'note'
    ? Math.min(15, Math.max(0, 17 - 3 * wert)) : (max > 0 ? wert / max * 15 : 0));
  window.FridayNoten = { zahl, stufe, noteZuText, textZuNote, punkteAus };      // auch für den Reiter „Arbeiten“

  const FAECHER = ['Mathematik', 'Biologie', 'Chemie', 'Physik', 'Deutsch', 'Englisch', 'Französisch', 'Latein', 'Spanisch',
    'Geschichte', 'Politik', 'Erdkunde', 'Philosophie', 'Religion', 'Werte und Normen', 'Musik', 'Kunst', 'Sport',
    'Informatik', 'Darstellendes Spiel', 'Seminarfach'];
  const VORLAGEN = [
    ['Klausuren + Mündlich', [['Klausuren', 50], ['Mündlich', 50]]],
    ['Nur Mündlich', [['Mündlich', 100]]],
    ['Ohne Bereiche', []],
  ];
  const HALBJAHRE = ['12/1', '12/2', '13/1', '13/2'];

  let daten = { faecher: [], halbjahre: HALBJAHRE, stunden: {} };
  let sicht = 'uebersicht';                // 'uebersicht', 'abitur' oder ein Semester
  const offen = new Set();                 // aufgeklappte Fächer
  let wahl = null;                         // Fächerwahl im Bearbeiten: Zeilen, sonst null
  let bereichForm = null;                  // Fach-Id: Bereiche und Ziel werden bearbeitet
  let noteForm = null;                     // { fach, id | null }
  let meldung = '';
  let geladen = false;

  try { sicht = localStorage.getItem('friday.noten.sicht') || '12/1'; } catch (_) { sicht = '12/1'; }
  if (sicht !== 'uebersicht' && sicht !== 'abitur' && !HALBJAHRE.includes(sicht)) sicht = '12/1';
  const sichtSetzen = (x) => {
    sicht = x;
    try { localStorage.setItem('friday.noten.sicht', x); } catch (_) {}
  };

  const rolle = (f) => (f.pruefung >= 1 && f.pruefung <= 3 ? `LK · P${f.pruefung}` : (f.pruefung ? `P${f.pruefung}` : ''));
  const inSemester = (hj) => daten.faecher.filter((f) => f.halbjahre.includes(hj));

  /* ---------- Bausteine ---------- */

  function feld(beschriftung, eingabe, hilfe) {
    const f = el('label', 'g-feld');
    f.append(el('span', 'leise', beschriftung), eingabe);
    if (hilfe) f.append(el('span', 'leise', hilfe));
    return f;
  }
  function eingabe(art, wert, extra = {}) {
    const i = el('input');
    i.type = art;
    if (wert !== undefined && wert !== null) i.value = wert;
    Object.assign(i, extra);
    return i;
  }
  function balken(p) {
    const b = el('div', 'no-balken');
    const f = el('div', `no-fuellung ${stufe(p)}`);
    f.style.width = `${Math.min(100, Math.max(0, p / 15 * 100))}%`;
    b.append(f);
    return b;
  }
  function verlauf(eintraege) {
    const reihe = [...eintraege].sort((a, b) => (a.datum + a.id).localeCompare(b.datum + b.id)).map((e) => e.punkte);
    if (reihe.length < 2) return null;
    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 100 26');
    svg.setAttribute('class', 'no-verlauf');
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('aria-label', 'Verlauf der Noten');
    const punkte = reihe.map((p, i) => `${(i / (reihe.length - 1) * 96 + 2).toFixed(1)},${(23 - p / 15 * 20).toFixed(1)}`);
    const linie = document.createElementNS(NS, 'polyline');
    linie.setAttribute('points', punkte.join(' '));
    svg.append(linie);
    return svg;
  }

  /* ---------- Bereiche und Ziel eines Fachs ---------- */

  function bereichFormular(f, hj) {
    const s = f.semester[hj];
    const form = el('form', 'no-form');
    form.append(el('h3', '', `${f.name}, ${hj}: Bereiche und Ziel`));
    form.append(el('p', 'leise', `Woraus besteht die Note in ${hj}, und wie stark zählt jeder Bereich? Das legst du für jedes Semester `
      + 'einzeln fest — so, wie die Lehrkraft es gesagt hat (mein Vorschlag: Klausuren und Mündlich je zur Hälfte). '
      + 'Ein Bereich ohne Note zählt noch nicht mit. Ziel und Notiz gelten für das ganze Fach.'));
    const ziel = eingabe('number', f.ziel ?? '', { min: 0, max: 15, step: 0.5, placeholder: 'optional', inputMode: 'decimal' });
    const notiz = el('textarea');
    notiz.rows = 2; notiz.maxLength = 1000; notiz.value = f.notiz;
    notiz.placeholder = 'z. B. Lehrkraft, wie sie bewertet';
    form.append(feld('Ziel in Punkten', ziel), feld('Notiz', notiz));

    form.append(el('h4', '', 'Bereiche'));
    const zeilen = el('div', 'no-bereiche');
    const summe = el('p', 'leise');
    const summeZeigen = () => {
      const s = [...zeilen.children].reduce((a, z) => a + (Number(z.querySelector('.no-b-gewicht').value) || 0), 0);
      summe.textContent = zeilen.children.length ? `Summe der Gewichtungen: ${zahl(s, 2)} %`
        + (s === 100 ? '' : (s < 100 ? ' — Noten ohne Bereich füllen den Rest.' : ' — wird anteilig gerechnet.')) : '';
    };
    const zeile = (b) => {
      const z = el('div', 'no-b-zeile');
      z.dataset.id = b && b.id ? b.id : '';
      const n = eingabe('text', b ? b.name : '', { placeholder: 'z. B. Klausuren', maxLength: 200 });
      n.className = 'no-b-name';
      const g = eingabe('number', b ? b.gewicht : 50, { min: 0, max: 100, step: 5, inputMode: 'decimal' });
      g.className = 'no-b-gewicht';
      g.addEventListener('input', summeZeigen);
      const weg = knopf('×', 'leise-knopf', () => { z.remove(); summeZeigen(); });
      weg.setAttribute('aria-label', 'Bereich entfernen');
      z.append(n, g, el('span', 'leise', '%'), weg);
      return z;
    };
    for (const b of s.bereiche) zeilen.append(zeile(b));
    const vorlagen = el('div', 'chips');
    for (const [text, bereiche] of VORLAGEN) {
      vorlagen.append(knopf(text, '', () => {
        // Bekannte Bereiche gleichen Namens behalten ihre Noten.
        const alt = new Map(s.bereiche.map((b) => [b.name.toLowerCase(), b.id]));
        zeilen.textContent = '';
        for (const [n, g] of bereiche) zeilen.append(zeile({ id: alt.get(n.toLowerCase()), name: n, gewicht: g }));
        summeZeigen();
      }));
    }
    form.append(vorlagen, zeilen, summe,
      knopf('+ Bereich', 'leise-knopf', () => { zeilen.append(zeile(null)); summeZeigen(); }));
    summeZeigen();
    const fuerAlle = eingabe('checkbox', undefined);
    const weitere = f.halbjahre.length > 1;
    if (weitere) {
      const l = el('label', 'check');
      l.append(fuerAlle, document.createTextNode(` Gleich für alle Semester von ${f.name} übernehmen`));
      form.append(l);
    }

    const fehler = el('p', 'status-fehler', meldung);
    const reihe = el('div', 'knoepfe');
    const speichern = el('button', 'ja', 'Speichern');
    speichern.type = 'submit';
    reihe.append(speichern, knopf('Abbrechen', '', () => { bereichForm = null; meldung = ''; zeichnen(); }));
    form.append(fehler, reihe);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const bereiche = [...zeilen.children].map((z) => ({
        id: z.dataset.id ? Number(z.dataset.id) : null,
        name: z.querySelector('.no-b-name').value, gewicht: Number(z.querySelector('.no-b-gewicht').value) || 0,
      }));
      const verloren = s.bereiche.filter((b) => !bereiche.some((x) => x.id === b.id));
      const betroffen = s.eintraege.filter((e2) => verloren.some((b) => b.id === e2.bereich_id)).length;
      if (betroffen && !confirm(`${betroffen} Noten haben dann keinen Bereich mehr und zählen nur noch mit dem Rest der Gewichtung. Trotzdem?`)) return;
      speichern.disabled = true;
      try {
        await schicken('PUT', `/api/noten/faecher/${f.id}`, { halbjahr: hj, fuer_alle: fuerAlle.checked,
          ziel: ziel.value === '' ? null : Number(ziel.value), notiz: notiz.value, bereiche });
        bereichForm = null; meldung = '';
        await laden();
      } catch (er) { fehler.textContent = er.message; speichern.disabled = false; }
    });
    return form;
  }

  /* ---------- Fächerwahl (wie in der Schule: LK, P4/P5, weitere Fächer) ---------- */

  const leereZeile = (pruefung, stunden, halbjahre = HALBJAHRE) => ({ id: null, name: '', pruefung, stunden, halbjahre: new Set(halbjahre) });
  const zeileAus = (f) => ({ id: f.id, name: f.name, pruefung: f.pruefung, stunden: f.stunden, halbjahre: new Set(f.halbjahre) });

  function wahlStarten() {
    const je = (n) => daten.faecher.find((f) => f.pruefung === n);
    const z = [];
    for (const n of [1, 2, 3]) z.push(je(n) ? zeileAus(je(n)) : leereZeile(n, 5));
    for (const n of [4, 5]) z.push(je(n) ? zeileAus(je(n)) : leereZeile(n, 3));
    for (const f of daten.faecher.filter((x) => !x.pruefung)) z.push(zeileAus(f));
    wahl = z;
    meldung = '';
    zeichnen();
  }

  function datalisteSichern() {
    if ($('no-faecher')) return;
    const liste = el('datalist');
    liste.id = 'no-faecher';
    for (const x of FAECHER) { const o = el('option'); o.value = x; liste.append(o); }
    document.body.append(liste);
  }

  function wahlFormular() {
    datalisteSichern();
    const form = el('form', 'no-form');
    form.append(el('h3', '', 'Meine Fächer'));
    form.append(el('p', 'leise', 'Wie in der Fächerwahl deiner Schule: drei Leistungskurse (erhöhtes Niveau, P1–P3), zwei '
      + 'weitere Prüfungsfächer (P4, P5) und die übrigen Fächer. Tippe unter jedem Fach an, in welchen Semestern du es hast — '
      + 'das Seminarfach läuft zum Beispiel nur bis 13/1. Ein leeres Feld heißt: kein Fach.'));
    const stundenZeile = el('p', 'leise no-stunden');
    const stundenZeigen = () => {
      stundenZeile.textContent = 'Wochenstunden: ' + HALBJAHRE.map((h) => `${h}: ${wahl
        .filter((z) => z.name.trim() && z.halbjahre.has(h)).reduce((a, z) => a + (Number(z.stunden) || 0), 0)}`).join(' · ');
    };

    const zeile = (z, entfernbar) => {
      const box = el('div', 'no-w-zeile');
      const oben = el('div', 'no-w-oben');
      const name = eingabe('text', z.name, { placeholder: 'Fach', maxLength: 200, autocomplete: 'off' });
      name.setAttribute('list', 'no-faecher');
      name.addEventListener('input', () => { z.name = name.value; stundenZeigen(); });
      const std = eingabe('number', z.stunden ?? '', { min: 0, max: 20, step: 1, inputMode: 'numeric', placeholder: 'Std.' });
      std.className = 'no-w-std';
      std.addEventListener('input', () => { z.stunden = std.value === '' ? null : Number(std.value); stundenZeigen(); });
      std.setAttribute('aria-label', 'Wochenstunden');
      oben.append(name, std, el('span', 'leise', 'Std.'));
      if (entfernbar) {
        const weg = knopf('×', 'leise-knopf', () => { wahl.splice(wahl.indexOf(z), 1); zeichnen(); });
        weg.setAttribute('aria-label', 'Fach entfernen');
        oben.append(weg);
      }
      const chips = el('div', 'chips no-w-chips');
      for (const h of HALBJAHRE) {
        const c = knopf(h, z.halbjahre.has(h) ? 'aktiv' : '', () => {
          if (z.halbjahre.has(h)) z.halbjahre.delete(h); else z.halbjahre.add(h);
          c.classList.toggle('aktiv', z.halbjahre.has(h));
          stundenZeigen();
        });
        chips.append(c);
      }
      box.append(oben, chips);
      return box;
    };
    const gruppe = (titel, hilfe, zeilen, entfernbar) => {
      const g = el('div', 'no-w-gruppe');
      g.append(el('h4', '', titel));
      if (hilfe) g.append(el('p', 'leise', hilfe));
      for (const z of zeilen) g.append(zeile(z, entfernbar));
      return g;
    };
    form.append(gruppe('Leistungskurse (erhöhtes Niveau, P1–P3)', '', wahl.filter((z) => z.pruefung >= 1 && z.pruefung <= 3), false));
    form.append(gruppe('Weitere Prüfungsfächer (grundlegendes Niveau, P4–P5)', '', wahl.filter((z) => z.pruefung >= 4), false));
    const weitere = gruppe('Weitere Fächer (grundlegendes Niveau)', '', wahl.filter((z) => !z.pruefung), true);
    weitere.append(knopf('+ Fach', 'leise-knopf', () => { wahl.push(leereZeile(0, 2)); zeichnen(); }));
    form.append(weitere, stundenZeile);
    stundenZeigen();

    const fehler = el('p', 'status-fehler', meldung);
    const reihe = el('div', 'knoepfe');
    const speichern = el('button', 'ja', 'Speichern');
    speichern.type = 'submit';
    reihe.append(speichern, knopf('Abbrechen', '', () => { wahl = null; meldung = ''; zeichnen(); }));
    form.append(fehler, reihe);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const rein = wahl.filter((z) => z.name.trim());
      const bleiben = new Set(rein.map((z) => z.id).filter(Boolean));
      const weg = daten.faecher.filter((f) => !bleiben.has(f.id));
      const mitNoten = weg.filter((f) => Object.values(f.semester).some((s) => s.eintraege.length));
      if (mitNoten.length && !confirm(`${mitNoten.map((f) => f.name).join(', ')} und alle Noten darin werden gelöscht. Trotzdem?`)) return;
      speichern.disabled = true;
      try {
        daten = await schicken('PUT', '/api/noten/fachwahl', { faecher: rein.map((z) => ({
          id: z.id, name: z.name, pruefung: z.pruefung, stunden: z.stunden, halbjahre: [...z.halbjahre] })) });
        wahl = null; meldung = '';
        zeichnen();
      } catch (er) { fehler.textContent = er.message; speichern.disabled = false; }
    });
    return form;
  }

  /* ---------- Noten-Formular (neu und ändern) ---------- */

  function noteFormular(f, e, hj) {
    const neu = !e;
    const form = el('form', 'no-form');
    form.append(el('h3', '', neu ? `Neue Note in ${f.name} (${hj})` : 'Note bearbeiten'));

    const titel = eingabe('text', neu ? '' : e.titel, { placeholder: 'z. B. Klausur 1, Präsentation, Vokabeltest', maxLength: 200, required: true });
    const datum = eingabe('date', neu ? heute() : e.datum, { required: true });
    const bereich = el('select');
    const bereiche = f.semester[hj].bereiche;
    if (bereiche.length) {
      for (const b of bereiche) {
        const o = el('option', '', `${b.name} (${zahl(b.gewicht, 2)} %)`);
        o.value = b.id;
        bereich.append(o);
      }
      const keiner = el('option', '', 'Ohne Bereich');
      keiner.value = '';
      bereich.append(keiner);
      bereich.value = neu ? String(bereiche[0].id) : (e.bereich_id ? String(e.bereich_id) : '');
    }

    let skala = neu ? 'punkte' : e.skala;
    const wert = eingabe('text', '', { inputMode: 'decimal', autocomplete: 'off', required: true });
    const max = eingabe('text', neu ? 15 : (e.skala === 'punkte' ? e.max : 15), { inputMode: 'decimal', autocomplete: 'off' });
    if (!neu) wert.value = e.skala === 'note' ? noteZuText(e.wert) : zahl(e.wert, 2);
    const wertFeld = el('div', 'no-wert');
    const art = el('div', 'chips');
    const vorschau = el('p', 'leise');
    const zahlAus = (t) => { const z = Number(String(t).trim().replace(',', '.')); return t !== '' && Number.isFinite(z) ? z : null; };
    const vorschauZeigen = () => {
      const w = skala === 'note' ? textZuNote(wert.value) : zahlAus(wert.value);
      const m = zahlAus(max.value);
      if (w === null || (skala === 'punkte' && !(m > 0))) { vorschau.textContent = ''; return; }
      const p = punkteAus(skala, w, m);
      vorschau.textContent = `= ${zahl(p, 2)} Notenpunkte (≈ Note ${zahl((17 - p) / 3, 1)})`;
    };
    const wertBauen = () => {
      wertFeld.textContent = '';
      art.textContent = '';
      for (const [k, text] of [['punkte', 'Punkte'], ['note', 'Note']]) {
        const b = knopf(text, k === skala ? 'aktiv' : '', () => { skala = k; wertBauen(); });
        art.append(b);
      }
      if (skala === 'note') {
        wert.placeholder = 'z. B. 2-, 1+, 3';
        wertFeld.append(feld('Note (1–6, mit + und −)', wert));
      } else {
        wert.placeholder = 'z. B. 13';
        wertFeld.append(feld('Punkte', wert), feld('von', max),
          el('span', 'leise no-wert-hinweis', 'von: 15 bei Notenpunkten, oder die Höchstzahl (z. B. 40 Rohpunkte)'));
      }
      vorschauZeigen();
    };
    for (const i of [wert, max]) i.addEventListener('input', vorschauZeigen);
    wertBauen();

    const anteil = eingabe('text', neu ? '' : (e.anteil ?? ''), { inputMode: 'decimal', placeholder: 'leer = zählt wie die übrigen', autocomplete: 'off' });
    const faktor = eingabe('text', neu ? 1 : e.faktor, { inputMode: 'decimal', autocomplete: 'off' });
    const notiz = el('textarea');
    notiz.rows = 2; notiz.maxLength = 1000; notiz.value = neu ? '' : e.notiz;
    notiz.placeholder = 'was die Lehrkraft dazu gesagt hat';

    form.append(feld('Was war es?', titel), feld('Datum', datum));
    if (bereiche.length) form.append(feld('Bereich', bereich));
    form.append(art, wertFeld, vorschau,
      feld('Zählt im Bereich mit … %', anteil, 'z. B. 20, wenn die Lehrkraft sagt „zählt höchstens 20 %“. Der Rest des Bereichs bleibt für die übrigen Noten.'));
    const mehr = el('details', 'no-mehr');
    mehr.append(el('summary', 'leise', 'Mehr'), feld('Faktor (wenn ohne Anteil)', faktor, 'z. B. 2, wenn diese Note doppelt zählt'), feld('Notiz', notiz));
    if (!neu && (e.faktor !== 1 || e.notiz)) mehr.open = true;
    form.append(mehr);

    const fehler = el('p', 'status-fehler', meldung);
    const reihe = el('div', 'knoepfe');
    const speichern = el('button', 'ja', 'Speichern');
    speichern.type = 'submit';
    reihe.append(speichern, knopf('Abbrechen', '', () => { noteForm = null; meldung = ''; zeichnen(); }));
    if (!neu) {
      // Zweistufig: erst „Löschen“, dann „Wirklich löschen?“ — ohne zweiten Tipp innerhalb von 5 s ist alles wie vorher.
      let zuruecksetzen = null;
      reihe.append(knopf('Löschen', 'weg', async (b) => {
        if (!b.classList.contains('sicher')) {
          b.classList.add('sicher');
          b.textContent = 'Wirklich löschen? Nochmal tippen';
          zuruecksetzen = setTimeout(() => { b.classList.remove('sicher'); b.textContent = 'Löschen'; }, 5000);
          return;
        }
        clearTimeout(zuruecksetzen);
        b.disabled = true;
        try { await schicken('DELETE', `/api/noten/eintraege/${e.id}`); noteForm = null; meldung = ''; await laden(); }
        catch (er) { meldung = er.message; zeichnen(); }
      }));
    }
    form.append(fehler, reihe);
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const w = skala === 'note' ? textZuNote(wert.value) : zahlAus(wert.value);
      const nutz = {
        bereich_id: bereiche.length && bereich.value ? Number(bereich.value) : null,
        halbjahr: neu ? hj : e.halbjahr,
        titel: titel.value, datum: datum.value, skala, wert: w,
        max: skala === 'punkte' ? (zahlAus(max.value) ?? 0) : 15,
        anteil: anteil.value.trim() === '' ? null : zahlAus(anteil.value),
        faktor: zahlAus(faktor.value) ?? 1, notiz: notiz.value,
      };
      if (anteil.value.trim() !== '' && nutz.anteil === null) { fehler.textContent = 'Der Anteil ist keine Zahl.'; return; }
      speichern.disabled = true;
      try {
        if (neu) await schicken('POST', `/api/noten/faecher/${f.id}/eintraege`, nutz);
        else await schicken('PUT', `/api/noten/eintraege/${e.id}`, nutz);
        noteForm = null; meldung = '';
        await laden();
      } catch (er) { fehler.textContent = er.message; speichern.disabled = false; }
    });
    return form;
  }

  /* ---------- Anzeige: ein Semester ---------- */

  function eintragZeile(f, e, hj) {
    const z = el('div', 'no-zeile klickbar');
    const links = el('div', 'no-links');
    links.append(el('b', '', e.titel));
    const meta = [kurzDatum(e.datum)];
    if (e.anteil !== null) meta.push(`zählt ${zahl(e.anteil, 2)} %`);
    if (e.anteil === null && e.faktor !== 1) meta.push(`×${zahl(e.faktor, 2)}`);
    links.append(el('span', 'leise', meta.join(' · ')));
    if (e.notiz) links.append(el('span', 'leise no-notiz', e.notiz));
    const rechts = el('div', 'no-rechts');
    rechts.append(el('b', stufe(e.punkte), e.skala === 'note' ? `Note ${noteZuText(e.wert)}` : `${zahl(e.wert, 2)} / ${zahl(e.max, 2)}`));
    if (e.skala === 'note' || e.max !== 15) rechts.append(el('span', 'leise', `${zahl(e.punkte)} P.`));
    const pfeil = el('span', 'no-pfeil', '›');
    pfeil.setAttribute('aria-hidden', 'true');
    z.append(links, rechts, pfeil);
    z.setAttribute('role', 'button');
    z.setAttribute('aria-label', `${e.titel} bearbeiten oder löschen`);
    z.addEventListener('click', () => { noteForm = { fach: f.id, id: e.id }; meldung = ''; zeichnen(); });
    return z;
  }

  function bereichKopf(name, gewichtText, wert, unter) {
    const k = el('div', 'no-b-kopf');
    const l = el('div');
    l.append(el('b', '', name), el('span', 'leise', gewichtText ? ` · ${gewichtText}` : ''));
    const r = el('div', 'no-rechts');
    r.append(el('b', wert === null ? 'leise' : stufe(wert), wert === null ? 'noch keine Note' : `${zahl(wert)} P.`));
    k.append(l, r);
    const box = el('div');
    box.append(k);
    if (unter) box.append(el('div', 'leise no-hinweis', unter));
    return box;
  }

  function fachKarte(f, hj) {
    const s = f.semester[hj];
    const karte = el('div', 'eintrag no-fach');
    const kopf = el('div', 'kopfzeile klickbar no-kopf');
    const name = el('div');
    const zeile1 = el('div', 'no-titelzeile');
    zeile1.append(el('b', 'no-name', f.name));
    if (rolle(f)) zeile1.append(el('span', 'no-tag', rolle(f)));
    name.append(zeile1);
    const sub = s.bereiche.filter((b) => b.wert !== null).map((b) => `${b.name} ${zahl(b.wert)}`);
    name.append(el('span', 'leise', sub.length ? sub.join(' · ') : (s.eintraege.length ? `${s.eintraege.length} Noten` : 'noch keine Noten')));
    const wert = el('div', 'no-rechts');
    if (s.wert === null) wert.append(el('span', 'leise', '–'));
    else {
      wert.append(el('b', `no-gross ${stufe(s.wert)}`, zahl(s.wert)));
      wert.append(el('span', 'leise', `≈ Note ${zahl(s.note)}`));
    }
    kopf.append(name, wert);
    kopf.addEventListener('click', () => { if (offen.has(f.id)) offen.delete(f.id); else offen.add(f.id); zeichnen(); });
    karte.append(kopf);
    if (s.wert !== null) karte.append(balken(s.wert));
    if (f.ziel !== null && s.wert !== null) {
      const d = s.wert - f.ziel;
      karte.append(el('div', 'leise no-ziel', d >= 0 ? `Ziel ${zahl(f.ziel)} P. erreicht (+${zahl(d)})`
        : `Ziel ${zahl(f.ziel)} P. — noch ${zahl(-d)} P. entfernt`));
    }
    if (!offen.has(f.id)) return karte;

    const inhalt = el('div', 'no-inhalt');
    if (f.notiz) inhalt.append(el('p', 'leise', f.notiz));
    const v = verlauf(s.eintraege);
    if (v) inhalt.append(v);

    if (bereichForm === f.id) { inhalt.append(bereichFormular(f, hj)); karte.append(inhalt); return karte; }

    const gruppen = s.bereiche.map((b) => ({ kopf: b, eintraege: s.eintraege.filter((x) => x.bereich_id === b.id) }));
    if (s.ohne_bereich) gruppen.push({ kopf: null, eintraege: s.eintraege.filter((x) => x.bereich_id === null) });
    for (const g of gruppen) {
      const box = el('div', 'no-gruppe');
      const b = g.kopf || s.ohne_bereich;
      const hinweise = (b.hinweise || []).join(' ');
      const belegt = b.anzahl && b.abgedeckt < 100 && !hinweise ? `${zahl(b.abgedeckt)} % belegt` : '';
      box.append(bereichKopf(g.kopf ? g.kopf.name : 'Ohne Bereich', `zählt ${zahl(b.gewicht, 2)} %`, b.wert,
        [hinweise, belegt].filter(Boolean).join(' ')));
      for (const e of g.eintraege) {
        box.append(noteForm && noteForm.id === e.id ? noteFormular(f, e, hj) : eintragZeile(f, e, hj));
      }
      inhalt.append(box);
    }
    if (!s.eintraege.length) inhalt.append(el('p', 'leise', `Noch nichts eingetragen in ${hj}.`));
    else if (!noteForm) inhalt.append(el('p', 'leise no-tipp', 'Tipp auf eine Note zum Ändern oder Löschen.'));

    if (noteForm && noteForm.fach === f.id && noteForm.id === null) inhalt.append(noteFormular(f, null, hj));
    else {
      const reihe = el('div', 'knoepfe');
      reihe.append(knopf('+ Note eintragen', 'ja', () => { noteForm = { fach: f.id, id: null }; meldung = ''; zeichnen(); }),
        knopf('Bereiche und Ziel', '', () => { bereichForm = f.id; meldung = ''; zeichnen(); }));
      inhalt.append(reihe);
    }
    karte.append(inhalt);
    return karte;
  }

  function schnittKarte(titel, werte, anzahl, gesamt) {
    const schnitt = werte.reduce((a, x) => a + x, 0) / werte.length;
    const s = el('div', 'eintrag no-schnitt');
    const l = el('div');
    l.append(el('b', '', titel), el('span', 'leise', `${anzahl} von ${gesamt} Fächern mit Note`));
    const r = el('div', 'no-rechts');
    r.append(el('b', `no-gross ${stufe(schnitt)}`, zahl(schnitt)), el('span', 'leise', `Punkte ≈ Note ${zahl((17 - schnitt) / 3)}`));
    const oben = el('div', 'kopfzeile');
    oben.append(l, r);
    s.append(oben, balken(schnitt));
    return s;
  }

  function semesterAnsicht(ziel, hj) {
    const faecher = inSemester(hj);
    if (!faecher.length) { ziel.append(el('p', 'leise', `In ${hj} ist noch kein Fach belegt (Fächer).`)); return; }
    const mitNote = faecher.filter((f) => f.semester[hj].wert !== null);
    if (mitNote.length) ziel.append(schnittKarte(`Schnitt ${hj}`, mitNote.map((f) => f.semester[hj].wert), mitNote.length, faecher.length));
    else ziel.append(el('p', 'leise', `Noch keine Note in ${hj}. Tippe ein Fach an und trag die erste ein — zum Beispiel eine Klausur oder eine Präsentation.`));
    for (const f of faecher) ziel.append(fachKarte(f, hj));
    const std = daten.stunden[hj];
    if (std) ziel.append(el('p', 'leise no-std', `${faecher.length} Fächer · ${std} Wochenstunden in ${hj}`));
  }

  /* ---------- Anzeige: Übersicht über alle Semester ---------- */

  function uebersichtAnsicht(ziel) {
    const tab = el('div', 'no-tab');
    const zelle = (text, klasse) => { const c = el('div', `no-zelle ${klasse || ''}`, text); tab.append(c); return c; };
    zelle('', 'no-kopfzelle');
    for (const h of HALBJAHRE) zelle(h, 'no-kopfzelle');
    zelle('Ø', 'no-kopfzelle');
    const mittel = (liste) => (liste.length ? liste.reduce((a, x) => a + x, 0) / liste.length : null);
    const wertZelle = (w, klasse, geklickt) => {
      const c = zelle(w === null ? '·' : zahl(w), `${klasse || ''} ${w === null ? 'leise' : stufe(w)}`);
      if (geklickt) { c.classList.add('klickbar'); c.addEventListener('click', geklickt); }
      return c;
    };
    for (const f of daten.faecher) {
      const namensZelle = zelle('', 'no-namezelle');
      namensZelle.append(el('span', '', f.name));
      if (rolle(f)) namensZelle.append(el('span', 'no-tag', rolle(f)));
      const werte = [];
      for (const h of HALBJAHRE) {
        if (!f.halbjahre.includes(h)) { zelle('–', 'leise no-aus'); continue; }
        const w = f.semester[h].wert;
        if (w !== null) werte.push(w);
        wertZelle(w, '', () => { sichtSetzen(h); offen.add(f.id); zeichnen(); });
      }
      wertZelle(mittel(werte), 'no-mittelzelle');
    }
    zelle('Ø', 'no-namezelle no-summe');
    const alle = [];
    for (const h of HALBJAHRE) {
      const m = mittel(inSemester(h).map((f) => f.semester[h].wert).filter((w) => w !== null));
      if (m !== null) alle.push(m);
      wertZelle(m, 'no-summe');
    }
    wertZelle(mittel(alle), 'no-summe no-mittelzelle');
    zelle('Std.', 'no-namezelle leise');
    for (const h of HALBJAHRE) zelle(daten.stunden[h] ? String(daten.stunden[h]) : '–', 'leise');
    zelle('', '');
    ziel.append(tab, el('p', 'leise', 'Kursnote je Semester in Notenpunkten. Tipp auf eine Zahl öffnet das Fach in diesem Semester.'));
  }

  /* ---------- Anzeige: Abitur-Schätzung (optional) ---------- */

  async function abiturSpeichern(einst) {
    try { daten = await schicken('PUT', '/api/noten/abitur', einst); meldung = ''; }
    catch (e) { meldung = e.message; }
    zeichnen();
  }

  function abiturAnsicht(ziel) {
    const a = daten.abitur;
    const karte = el('div', 'eintrag no-abi');
    const kopf = el('div', 'kopfzeile');
    const l = el('div');
    l.append(el('b', '', 'Abitur-Schätzung'),
      el('span', 'leise', `aus ${a.kurse_mit_note} von ${a.kurse_gesamt} Kursen mit Note`));
    const r = el('div', 'no-rechts');
    if (a.gesamt === null) r.append(el('span', 'leise', 'noch zu wenig Noten'));
    else {
      r.append(el('b', `no-gross ${a.bestanden ? 'no-gut' : 'no-schlecht'}`, `${a.gesamt}`),
        el('span', 'leise', a.note === null ? 'von 900 · unter 300' : `von 900 · Note ≈ ${zahl(a.note)}`));
    }
    kopf.append(l, r);
    karte.append(kopf);
    if (a.gesamt !== null) karte.append(balken(a.gesamt / 900 * 15));
    const zeilen = el('div', 'no-abi-zeilen');
    zeilen.append(el('span', '', `Block I (Kurse): ${a.block1 === null ? '–' : a.block1} / 600`
      + (a.schnitt === null ? '' : ` · Ø ${zahl(a.schnitt, 2)} Punkte`)),
      el('span', '', `Block II (Prüfungen): ${a.block2 === null ? '–' : a.block2} / 300`));
    karte.append(zeilen);
    for (const h of a.hinweise) karte.append(el('p', 'leise no-hinweis', h));
    ziel.append(karte);

    // Block I: Kursnoten je Semester; Tipp schaltet einen Kurs aus der Wertung
    const b1 = el('div', 'block');
    b1.append(el('h2', '', 'Block I: Kursnoten'));
    b1.append(el('p', 'leise', 'Kursnote = Note des Semesters in ganzen Punkten. Erhöhtes Niveau zählt doppelt (×2). '
      + 'Tipp auf einen Kurs nimmt ihn aus der Wertung — falls deine Schule ihn nicht einbringen muss.'));
    const tab = el('div', 'no-tab no-tab-4');
    const z = (text, klasse) => { const c = el('div', `no-zelle ${klasse || ''}`, text); tab.append(c); return c; };
    z('', 'no-kopfzelle');
    for (const h of HALBJAHRE) z(h, 'no-kopfzelle');
    for (const f of a.faecher) {
      const n = z('', 'no-namezelle');
      n.append(el('span', '', f.name));
      if (f.gewicht === 2) n.append(el('span', 'no-tag', '×2'));
      for (const h of HALBJAHRE) {
        const k = f.kurse[h];
        if (!k) { z('–', 'leise no-aus'); continue; }
        const c = z(k.punkte === null ? '·' : String(k.punkte), `${k.punkte === null ? 'leise' : stufe(k.punkte)} ${k.zaehlt ? '' : 'no-abgewaehlt'}`);
        if (k.punkte !== null) {
          c.classList.add('klickbar');
          c.setAttribute('role', 'button');
          c.setAttribute('aria-label', `${f.name} ${h} ${k.zaehlt ? 'aus der Wertung nehmen' : 'wieder werten'}`);
          c.addEventListener('click', () => {
            const schluessel = `${f.id}/${h}`;
            const weg = new Set(a.einstellung.ausgeschlossen);
            if (weg.has(schluessel)) weg.delete(schluessel); else weg.add(schluessel);
            abiturSpeichern({ pruefungen: a.einstellung.pruefungen, ausgeschlossen: [...weg] });
          });
        }
      }
    }
    b1.append(tab);
    ziel.append(b1);

    // Block II: die fünf Prüfungen
    const b2 = el('div', 'block');
    b2.append(el('h2', '', 'Block II: Prüfungen'));
    b2.append(el('p', 'leise', 'Trag das Ergebnis (0–15 Punkte) ein, sobald du es hast. Bis dahin nehme ich den Schnitt '
      + 'der Kursnoten des Fachs an (grau).'));
    for (const p of a.pruefungen) {
      const zeile = el('div', 'no-pruefung');
      const name = el('div');
      name.append(el('b', '', `P${p.nr}`), el('span', 'leise', ` ${p.fach || 'kein Fach zugeordnet'}`));
      const feldE = eingabe('number', p.eingabe ?? '', { min: 0, max: 15, step: 1, inputMode: 'numeric', disabled: !p.fach,
        placeholder: p.quelle === 'schnitt' ? `≈ ${p.wert}` : 'Punkte' });
      feldE.setAttribute('aria-label', `Ergebnis P${p.nr}`);
      feldE.addEventListener('change', () => {
        const rein = { ...a.einstellung.pruefungen };
        if (feldE.value === '') delete rein[String(p.nr)]; else rein[String(p.nr)] = Number(feldE.value);
        abiturSpeichern({ pruefungen: rein, ausgeschlossen: a.einstellung.ausgeschlossen });
      });
      zeile.append(name, feldE, el('span', 'leise', '× 4'));
      b2.append(zeile);
    }
    ziel.append(b2);
    if (meldung) ziel.append(el('p', 'status-fehler', meldung));

    const wie = el('details', 'no-mehr');
    wie.append(el('summary', 'leise', 'So wird gerechnet'),
      el('p', 'leise', 'Block I (höchstens 600): gewichteter Schnitt der Kursnoten in ganzen Punkten, erhöhtes Niveau doppelt, mal 40. '
        + 'Block II (höchstens 300): die fünf Prüfungen (P1–P5), jede mal 4. Bestanden ab 300 Punkten insgesamt, mit mindestens '
        + '200 in Block I und 100 in Block II. Note = 17/3 − Punkte/180, auf eine Stelle abgerundet (1,0 ab 823 Punkten).'),
      el('p', 'leise', 'Das ist eine Schätzung nach den Regeln der Kultusministerkonferenz, nicht die amtliche Berechnung. '
        + 'Welche Kurse deine Schule einbringen lässt oder verlangt, kann abweichen — dafür kannst du Kurse abwählen. '
        + 'Für Prüfungen ohne Ergebnis und Semester ohne Note rechne ich mit dem, was schon da ist.'));
    ziel.append(wie);
  }

  /* ---------- Seite ---------- */

  function zeichnen() {
    const ziel = $('noten-inhalt');
    if (!ziel) return;
    ziel.textContent = '';
    if (!geladen) { ziel.append(el('p', 'leer-klein', 'lädt …')); return; }
    if (wahl) { ziel.append(wahlFormular()); return; }

    const kopf = el('div', 'no-leiste');
    const chips = el('div', 'chips no-sem');
    for (const [k, text] of [['uebersicht', 'Übersicht'], ...HALBJAHRE.map((h) => [h, h]), ['abitur', 'Abitur']]) {
      chips.append(knopf(text, k === sicht ? 'aktiv' : '', () => { sichtSetzen(k); noteForm = null; bereichForm = null; zeichnen(); }));
    }
    kopf.append(chips, knopf('Fächer', '', wahlStarten));
    ziel.append(kopf);

    if (!daten.faecher.length) {
      ziel.append(el('p', 'leise', 'Noch keine Fächer. Trag zuerst deine Fächerwahl ein: Leistungskurse, Prüfungsfächer und die übrigen Fächer '
        + 'mit den Semestern, in denen du sie hast. Danach kannst du in jedem Fach Klausuren, Präsentationen und alles Weitere eintragen.'));
      ziel.append(knopf('Fächer einrichten', 'voll-knopf', wahlStarten));
      return;
    }
    if (sicht === 'uebersicht') uebersichtAnsicht(ziel);
    else if (sicht === 'abitur') abiturAnsicht(ziel);
    else semesterAnsicht(ziel, sicht);
  }

  async function laden() {
    try {
      daten = await holen('/api/noten');
      geladen = true;
    } catch (e) {
      $('noten-inhalt').textContent = e.message || 'Die Noten sind gerade nicht erreichbar.';
      return;
    }
    zeichnen();
  }

  /* ---------- Reiter der Schule-Seite ---------- */

  const knoepfe = [...document.querySelectorAll('#schule .reiter button')];
  for (const b of knoepfe) {
    b.addEventListener('click', () => {
      for (const x of knoepfe) {
        x.classList.toggle('aktiv', x === b);
        $(`r-${x.dataset.reiter}`).hidden = x !== b;
      }
      document.dispatchEvent(new Event('schule-reiter'));
      if (b.dataset.reiter === 'noten') laden();
    });
  }
  const notenAktiv = () => location.hash === '#schule' && $('r-noten') && !$('r-noten').hidden;
  window.addEventListener('hashchange', () => { if (notenAktiv() && !wahl && !noteForm && bereichForm === null) laden(); });
  if (notenAktiv()) laden();
})();
