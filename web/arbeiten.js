/* Schule-Seite: Reiter „Arbeiten“ — alle Klassenarbeiten an einem Platz.

   Hochladen (Fotos der Seiten oder ein PDF): Friday liest Fach, Datum, Titel und
   Note, trägt die Note unter „Noten“ ein und merkt sich die Randnotizen der
   Lehrkraft. Wo sie unsicher ist, bleibt die Arbeit mit ihren Vorschlägen zum
   Prüfen liegen. Zum Lernen sammelt jedes Fach, was in den früheren Arbeiten
   angemerkt wurde. Jede Arbeit gibt es als PDF zurück.

   Alles per textContent, nie als HTML: Die Texte stammen aus der Arbeit. */
(() => {
  const { $, bildVerkleinern } = window.Friday;
  const { zahl, stufe, noteZuText, textZuNote } = window.FridayNoten;

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
  const tag = (iso) => {
    try { return new Date(`${iso}T12:00:00`).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' }); }
    catch (_) { return iso; }
  };
  const kurzTag = (iso) => (iso ? tag(iso).slice(0, 5) : '');

  const STATUS = {
    neu: ['wartet …', 'leise'],
    liest: ['Friday liest …', 'ar-liest'],
    pruefen: ['bitte prüfen', 'ar-pruefen'],
    eingetragen: ['in Noten', 'no-gut'],
    gelesen: ['gelesen', 'no-gut'],
    abgelegt: ['abgelegt', 'leise'],
    fehler: ['Fehler', 'no-schlecht'],
  };
  const ARTEN = [['klausur', 'Klausur'], ['test', 'Test'], ['referat', 'Referat'], ['sonstiges', 'Sonstiges']];
  const RAND = { lob: 'Lob', fehler: 'Fehler', hinweis: 'Hinweis', frage: 'Frage', sonstiges: 'Anmerkung' };

  let daten = { arbeiten: [], faecher: [], frei: false };
  let musterListe = [];
  let filter = 'alle';                     // 'alle' oder eine Fach-Id
  const offen = new Set();                 // aufgeklappte Arbeiten
  const bearbeitet = new Set();            // Arbeiten mit offenem Angaben-Formular
  const loeschFrage = new Set();           // Arbeiten, bei denen gerade „Löschen?“ gefragt wird
  let upload = '';                         // Meldung zum Hochladen
  let uploadFehler = false;
  const vorgeschlagen = new Set();         // Arbeiten, deren Angaben-Formular schon einmal von selbst aufging
  let geladen = false;
  let takt = null;

  const bewertung = (a) => {
    if (a.skala === 'punkte') return a.max && a.max !== 15 ? `${zahl(a.wert, 2)} / ${zahl(a.max, 2)}` : `${zahl(a.wert, 2)} P.`;
    if (a.skala === 'note') return `Note ${noteZuText(a.wert)}`;
    return '';
  };
  const bewertungStufe = (a) => (a.skala ? stufe(window.FridayNoten.punkteAus(a.skala, a.wert, a.max || 15)) : 'leise');

  /* ---------- Hochladen ---------- */

  async function hochladen(dateien) {
    const liste = [...dateien];
    if (!liste.length) return;
    const pdfs = liste.filter((d) => d.type === 'application/pdf' || /\.pdf$/i.test(d.name));
    if (pdfs.length && liste.length > 1) { upload = 'Entweder ein PDF oder mehrere Fotos — nicht gemischt.'; uploadFehler = true; zeichnen(); return; }
    upload = `Lade ${liste.length} ${pdfs.length ? 'PDF' : (liste.length === 1 ? 'Foto' : 'Fotos')} hoch …`;
    uploadFehler = false;
    zeichnen();
    try {
      const form = new FormData();
      for (const [i, d] of liste.entries()) {
        if (pdfs.length) form.append('dateien', d, d.name);
        else {
          const v = await bildVerkleinern(d, 2200);
          form.append('dateien', await (await fetch(v.vorschau)).blob(), `seite-${i + 1}.jpg`);
        }
      }
      const r = await antwort(await fetch('/api/arbeiten', { method: 'POST', body: form }));
      upload = r.gestartet ? 'Hochgeladen — Friday liest die Arbeit jetzt.' : 'Hochgeladen. Automatisches Lesen ist aus, die Angaben trägst du selbst ein.';
      offen.add(r.id);
      if (!r.gestartet) bearbeitet.add(r.id);
      await laden();
    } catch (e) {
      upload = e.message;
      uploadFehler = true;
      zeichnen();
    }
  }

  /* ---------- Angaben prüfen und ändern ---------- */

  function angabenFormular(a) {
    const form = el('form', 'no-form');
    form.append(el('h3', '', a.status === 'eingetragen' ? 'Angaben ändern' : 'Angaben prüfen'));
    const eing = (art, wert, extra = {}) => { const i = el('input'); i.type = art; if (wert !== null && wert !== undefined) i.value = wert; return Object.assign(i, extra); };
    const feld = (text, e) => { const f = el('label', 'g-feld'); f.append(el('span', 'leise', text), e); return f; };

    const fach = el('select');
    const leer = el('option', '', '— bitte wählen —');
    leer.value = '';
    fach.append(leer);
    for (const f of daten.faecher) { const o = el('option', '', f.name); o.value = f.id; fach.append(o); }
    fach.value = a.fach_id || '';
    const datum = eing('date', a.datum || '');
    const titel = eing('text', a.titel, { placeholder: 'z. B. Klausur Nr. 1 – Genetik', maxLength: 200 });
    const art = el('select');
    for (const [k, t] of ARTEN) { const o = el('option', '', t); o.value = k; art.append(o); }
    art.value = a.art;
    let skala = a.skala || 'punkte';
    const wert = eing('text', a.skala === 'note' ? noteZuText(a.wert) : (a.wert === null ? '' : zahl(a.wert, 2)), { inputMode: 'decimal', autocomplete: 'off' });
    const max = eing('text', a.skala === 'punkte' && a.max ? zahl(a.max, 2) : '15', { inputMode: 'decimal', autocomplete: 'off' });
    const wertFeld = el('div', 'no-wert');
    const chips = el('div', 'chips');
    const wertBauen = () => {
      wertFeld.textContent = '';
      chips.textContent = '';
      for (const [k, t] of [['punkte', 'Punkte'], ['note', 'Note']]) chips.append(knopf(t, k === skala ? 'aktiv' : '', () => { skala = k; wertBauen(); }));
      if (skala === 'note') { wert.placeholder = 'z. B. 2-, 1+'; wertFeld.append(feld('Note (1–6, mit + und −)', wert)); }
      else { wert.placeholder = 'z. B. 11'; wertFeld.append(feld('Punkte', wert), feld('von', max)); }
    };
    wertBauen();
    const zahlAus = (t) => { const z = Number(String(t).trim().replace(',', '.')); return String(t).trim() !== '' && Number.isFinite(z) ? z : null; };

    form.append(feld('Fach', fach), feld('Datum der Arbeit', datum), feld('Titel', titel), feld('Art', art),
      el('span', 'leise', 'Bewertung'), chips, wertFeld);
    const fehler = el('p', 'status-fehler', '');
    const reihe = el('div', 'knoepfe');
    const eintragen = el('button', 'ja', a.status === 'eingetragen' ? 'Änderungen speichern' : 'In Noten eintragen');
    eintragen.type = 'submit';
    reihe.append(eintragen);
    const senden = async (mitNote) => {
      const w = skala === 'note' ? textZuNote(wert.value) : zahlAus(wert.value);
      const nutz = { fach_id: fach.value ? Number(fach.value) : null, datum: datum.value, titel: titel.value, art: art.value,
        skala: w === null ? null : skala, wert: w, max: skala === 'punkte' ? (zahlAus(max.value) ?? 15) : 15, eintragen: mitNote };
      fehler.textContent = '';
      try { await schicken('PUT', `/api/arbeiten/${a.id}`, nutz); bearbeitet.delete(a.id); await laden(); }
      catch (e) { fehler.textContent = e.message; }
    };
    if (a.status !== 'eingetragen') reihe.append(knopf('Nur ablegen', '', () => senden(false)));
    reihe.append(knopf('Abbrechen', '', () => { bearbeitet.delete(a.id); zeichnen(); }));
    form.append(fehler, reihe);
    form.addEventListener('submit', (e) => { e.preventDefault(); senden(true); });
    return form;
  }

  /* ---------- Eine Arbeit ---------- */

  function kopfKarte(a) {
    const kopf = el('div', 'kopfzeile klickbar no-kopf');
    const bild = el('img', 'ar-vorschau');
    bild.loading = 'lazy';
    bild.alt = '';
    bild.src = `/api/arbeiten/${a.id}/seite/1?klein=1`;
    const links = el('div', 'ar-links');
    links.append(el('b', 'no-name', a.titel || 'Ohne Titel'));
    links.append(el('span', 'leise', [a.fach || a.fach_text || 'Fach unklar', a.datum ? tag(a.datum) : 'Datum unklar', a.halbjahr]
      .filter(Boolean).join(' · ')));
    if (a.person) links.append(el('span', 'ar-person', a.person));
    const [text, klasse] = STATUS[a.liest ? 'liest' : a.status] || STATUS.neu;
    links.append(el('span', `ar-status ${klasse}`, text));
    const rechts = el('div', 'no-rechts');
    if (a.skala) rechts.append(el('b', `no-gross ${bewertungStufe(a)}`, bewertung(a)));
    kopf.append(bild, links, rechts);
    kopf.addEventListener('click', () => { if (offen.has(a.id)) offen.delete(a.id); else offen.add(a.id); zeichnen(); });
    return kopf;
  }

  function liste(titel, zeilen) {
    const b = el('div', 'ar-abschnitt');
    b.append(el('h4', '', titel));
    for (const z of zeilen) b.append(z);
    return b;
  }

  function detail(a) {
    const box = el('div', 'no-inhalt');
    if (a.fehler) box.append(el('p', 'status-fehler', a.fehler));
    for (const h of a.hinweise) box.append(el('p', 'ar-hinweis', h));

    if (bearbeitet.has(a.id)) box.append(angabenFormular(a));

    const e = a.ergebnis || {};
    if (e.zusammenfassung) box.append(el('p', 'leise', e.zusammenfassung));
    if ((e.randnotizen || []).length) {
      box.append(liste('Randnotizen der Lehrkraft', e.randnotizen.map((r) => {
        const z = el('div', 'ar-rand');
        z.append(el('span', `ar-art ar-${r.art}`, RAND[r.art] || 'Anmerkung'), el('span', '', r.text), el('span', 'leise', `S. ${r.seite}`));
        return z;
      })));
    }
    if ((e.lernpunkte || []).length) box.append(liste('Fürs nächste Mal', e.lernpunkte.map((t) => el('div', 'ar-punkt', t))));
    if ((e.themen || []).length) {
      const t = el('div', 'chips ar-themen');
      for (const x of e.themen) t.append(el('span', 'ar-thema', x));
      box.append(liste('Themen', [t]));
    }

    const seiten = el('div', 'ar-seiten');
    for (let n = 1; n <= a.seiten; n++) {
      const link = el('a');
      link.href = `/api/arbeiten/${a.id}/seite/${n}`;
      link.target = '_blank';
      link.rel = 'noopener';
      const img = el('img');
      img.loading = 'lazy';
      img.alt = `Seite ${n}`;
      img.src = `/api/arbeiten/${a.id}/seite/${n}`;
      link.append(img);
      seiten.append(link);
    }
    box.append(liste(`Seiten (${a.seiten})`, [seiten]));

    const reihe = el('div', 'knoepfe');
    if (!bearbeitet.has(a.id)) reihe.append(knopf('Angaben ändern', '', () => { bearbeitet.add(a.id); zeichnen(); }));
    const pdf = el('a', 'ar-knopf', 'Als PDF herunterladen');
    pdf.href = `/api/arbeiten/${a.id}/pdf`;
    pdf.setAttribute('download', '');
    reihe.append(pdf);
    if (a.status !== 'eingetragen' && daten.frei && !a.liest) {
      reihe.append(knopf('Nochmal lesen', '', async () => {
        try { await schicken('POST', `/api/arbeiten/${a.id}/lesen`); await laden(); } catch (er) { upload = er.message; uploadFehler = true; zeichnen(); }
      }));
    }
    box.append(reihe);

    // Löschen: erst fragen, dann entscheiden, ob die Note mitgeht.
    const weg = el('div', 'knoepfe ar-weg');
    if (!loeschFrage.has(a.id)) weg.append(knopf('Löschen', 'weg', () => { loeschFrage.add(a.id); zeichnen(); }));
    else {
      weg.append(el('p', 'ar-hinweis', a.note_id ? 'Wirklich löschen? Die Arbeit ist dann weg. Was soll mit der eingetragenen Note passieren?'
        : 'Wirklich löschen? Die Arbeit mit allen Seiten ist dann weg.'));
      const los = async (mitNote) => {
        try { await schicken('DELETE', `/api/arbeiten/${a.id}${mitNote ? '?note=1' : ''}`); loeschFrage.delete(a.id); offen.delete(a.id); await laden(); }
        catch (er) { upload = er.message; uploadFehler = true; loeschFrage.delete(a.id); zeichnen(); }
      };
      const r2 = el('div', 'knoepfe');
      r2.append(knopf(a.note_id ? 'Arbeit löschen, Note behalten' : 'Ja, löschen', 'weg sicher', () => los(false)));
      if (a.note_id) r2.append(knopf('Arbeit und Note löschen', 'weg sicher', () => los(true)));
      r2.append(knopf('Abbrechen', '', () => { loeschFrage.delete(a.id); zeichnen(); }));
      weg.append(r2);
    }
    box.append(weg);
    return box;
  }

  /* ---------- Fürs Lernen: was in früheren Arbeiten eines Fachs angemerkt wurde ---------- */

  function lernhilfe(fachId, fachName, arbeiten) {
    const zeilen = [];
    const gesehen = new Set();
    const dazu = (art, text, a) => {
      const k = text.toLowerCase();
      if (gesehen.has(k)) return;
      gesehen.add(k);
      zeilen.push({ art, text, quelle: `${a.titel || 'Arbeit'}${a.datum ? ' · ' + kurzTag(a.datum) : ''}` });
    };
    for (const a of arbeiten) {
      for (const t of (a.ergebnis.lernpunkte || [])) dazu('lernen', t, a);
      for (const r of (a.ergebnis.randnotizen || [])) if (['fehler', 'hinweis', 'frage'].includes(r.art)) dazu(r.art, r.text, a);
    }
    if (!zeilen.length) return null;
    const themen = [...new Set(arbeiten.flatMap((a) => a.ergebnis.themen || []))];
    const d = el('details', 'ar-lernen');
    d.open = true;
    d.append(el('summary', '', `Fürs Lernen: ${fachName} (${zeilen.length} Punkte aus ${arbeiten.length} ${arbeiten.length === 1 ? 'Arbeit' : 'Arbeiten'})`));
    d.append(el('p', 'leise', 'Das hat die Lehrkraft in deinen früheren Arbeiten angemerkt — gut für die Vorbereitung auf die nächste.'));
    for (const z of zeilen.slice(0, 40)) {
      const r = el('div', 'ar-rand');
      r.append(el('span', `ar-art ar-${z.art === 'lernen' ? 'hinweis' : z.art}`, z.art === 'lernen' ? 'Lernen' : RAND[z.art]), el('span', '', z.text), el('span', 'leise', z.quelle));
      d.append(r);
    }
    if (themen.length) {
      const t = el('div', 'chips ar-themen');
      for (const x of themen) t.append(el('span', 'ar-thema', x));
      d.append(t);
    }
    return d;
  }

  /* ---------- Muster: wiederkehrende Fehler über mehrere Arbeiten, auch von Freunden ---------- */

  function musterAbschnitt() {
    if (!musterListe.length) return null;
    const d = el('details', 'ar-lernen');
    d.append(el('summary', '', `Muster über mehrere Arbeiten (${musterListe.length})`));
    d.append(el('p', 'leise', 'Automatisch aus allen bisher hochgeladenen Arbeiten zusammengefasst — je Person und Fach.'));
    for (const m of musterListe) {
      const z = el('div', 'ar-rand');
      z.append(el('span', 'ar-art ar-hinweis', m.person || 'Eike'), el('span', '', m.text),
        el('span', 'leise', `${m.fach} · aus ${m.anzahl} ${m.anzahl === 1 ? 'Arbeit' : 'Arbeiten'}`));
      d.append(z);
    }
    return d;
  }

  /* ---------- Seite ---------- */

  function zeichnen() {
    const ziel = $('arbeiten-inhalt');
    if (!ziel) return;
    const oben = document.activeElement && ziel.contains(document.activeElement) && ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName);
    if (oben) return;                                  // nicht mitten im Tippen neu zeichnen
    ziel.textContent = '';
    if (!geladen) { ziel.append(el('p', 'leer-klein', 'lädt …')); return; }

    const kasten = el('label', 'hochladen ar-hoch');
    const datei = el('input');
    datei.type = 'file';
    datei.multiple = true;
    datei.accept = 'image/*,application/pdf';
    datei.hidden = true;
    datei.addEventListener('change', () => { hochladen(datei.files); datei.value = ''; });
    kasten.append(datei, el('span', '', '＋ Arbeit hochladen'),
      el('span', 'leise', 'Fotos der Seiten (in der richtigen Reihenfolge, alle auf einmal) oder ein PDF'));
    ziel.append(kasten);
    if (upload) ziel.append(el('p', uploadFehler ? 'status-fehler' : 'leise', upload));
    if (!daten.frei) {
      const h = el('p', 'leise', 'Automatisches Lesen ist aus: Friday legt die Arbeiten nur ab, die Angaben trägst du selbst ein. Einschalten: ');
      const a = el('a', '', 'Mehr → Freigaben → Klassenarbeiten auswerten');
      a.href = '#freigaben';
      h.append(a);
      ziel.append(h);
    }
    const m = musterAbschnitt();
    if (m) ziel.append(m);

    const alle = daten.arbeiten;
    if (!alle.length) {
      ziel.append(el('p', 'leise', 'Noch keine Arbeit. Lade eine korrigierte Klassenarbeit hoch — Friday liest Fach, Datum und Note, trägt sie unter Noten '
        + 'ein und merkt sich die Randnotizen der Lehrkraft, damit du damit für die nächste Arbeit lernen kannst.'));
      return;
    }
    const faecherMitArbeit = [...new Map(alle.filter((a) => a.fach_id).map((a) => [a.fach_id, a.fach])).entries()];
    if (filter !== 'alle' && !faecherMitArbeit.some(([id]) => id === filter)) filter = 'alle';
    const chips = el('div', 'chips no-sem');
    chips.append(knopf('Alle', filter === 'alle' ? 'aktiv' : '', () => { filter = 'alle'; zeichnen(); }));
    for (const [id, name] of faecherMitArbeit) chips.append(knopf(name, filter === id ? 'aktiv' : '', () => { filter = id; zeichnen(); }));
    ziel.append(chips);

    const sichtbar = filter === 'alle' ? alle : alle.filter((a) => a.fach_id === filter);
    if (filter !== 'alle') {
      const l = lernhilfe(filter, faecherMitArbeit.find(([id]) => id === filter)[1], sichtbar);
      if (l) ziel.append(l);
    }
    for (const a of sichtbar) {
      const karte = el('div', 'eintrag ar-karte');
      karte.append(kopfKarte(a));
      if (offen.has(a.id)) karte.append(detail(a));
      ziel.append(karte);
    }
  }

  function taktSetzen(an) {
    if (an && !takt) takt = setInterval(laden, 3000);
    if (!an && takt) { clearInterval(takt); takt = null; }
  }

  const aktiv = () => location.hash === '#schule' && $('r-arbeiten') && !$('r-arbeiten').hidden;

  async function laden() {
    if (!aktiv()) { taktSetzen(false); return; }
    try {
      daten = await holen('/api/arbeiten');
      geladen = true;
    } catch (e) {
      $('arbeiten-inhalt').textContent = e.message || 'Die Arbeiten sind gerade nicht erreichbar.';
      taktSetzen(false);
      return;
    }
    try { musterListe = (await holen('/api/arbeiten/muster')).muster; } catch (e) { /* nur ein Zusatz, kein Abbruch */ }
    // Was Friday gerade liest, bleibt nicht lange so — solange nachfragen.
    taktSetzen(daten.arbeiten.some((a) => a.liest || a.status === 'liest' || a.status === 'neu'));
    for (const a of daten.arbeiten) {
      if (a.status === 'pruefen' && !a.liest && !vorgeschlagen.has(a.id)) { vorgeschlagen.add(a.id); offen.add(a.id); bearbeitet.add(a.id); }
    }
    zeichnen();
  }

  document.addEventListener('schule-reiter', laden);
  window.addEventListener('hashchange', laden);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') laden(); });
  laden();
})();
