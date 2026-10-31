/* Garantie-Tab: der Block „Bahn-Fahrgastrechte“ (22.09.2026) — in der Übersicht und auf der Personenseite.

   Züge von der S-Bahn bis zum ICE: Friday misst die Verspätung am Ziel wie bei der Garantie, rechnet nach Tarif und
   bereitet den Antrag vor — als ausgefülltes DB-Formular zum Drucken (Servicecenter Fahrgastrechte) oder zum Abtippen
   (S-Bahn Hannover). Abgeschickt wird NICHTS: „Eingereicht“ setzt Eike, wenn er selbst abgegeben hat.

   garantie.js ruft `window.FridayBahn.block(daten, { profil, zeichnen, laden })` und hängt das Ergebnis an. Der Zustand
   hier (offener Zug, gewählte Halte, Eingaben) überlebt das Neuzeichnen. Alles per textContent — Bahnhofs- und
   Betreibernamen kommen von fremden Diensten. */
(() => {
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
  const verweis = (text, href) => {
    const a = el('a', 'ar-knopf', text);
    a.href = href;
    a.target = '_blank';
    a.rel = 'noopener';
    return a;
  };
  const antwort = async (r) => {
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `Fehler ${r.status}`);
    return d;
  };
  const holen = async (url) => antwort(await fetch(url, { cache: 'no-store' }));
  const senden = async (url, daten) => antwort(await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(daten || {}),
  }));
  const entfernen = async (url) => antwort(await fetch(url, { method: 'DELETE' }));
  const uhr = (iso) => { try { return new Date(iso).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }); } catch (_) { return ''; } };
  const tag = (iso) => { try { return new Date(iso).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' }); } catch (_) { return ''; } };
  const datum = (iso) => (iso ? new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString('de-DE') : '?');
  const euro = (cent) => `${((cent || 0) / 100).toFixed(2).replace('.', ',')} €`;
  const lokal = (d) => {
    const z2 = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${z2(d.getMonth() + 1)}-${z2(d.getDate())}T${z2(d.getHours())}:${z2(d.getMinutes())}`;
  };

  // Was zwischen zwei Neuzeichnungen erhalten bleibt.
  const z = {
    zug: null, verlauf: null, wahl: { start: null, ziel: null }, fuer: 0, karte: 'zeitkarte', preis: '', auftrag: '',
    rueckfahrt: false, meldung: '', halt: 'Hannover Hbf', zeit: '', gefunden: null, suchText: '',
    auf: { faelle: false, erfassen: false, eingereicht: false }, abtippen: {}, bescheid: null,
  };
  const FARBE = { bereit: 'status-ok', gesammelt: 'status-ok', eingereicht: 'status-ok', genehmigt: 'status-ok',
                  'kein Anspruch': 'status-fehler', abgelehnt: 'status-fehler', 'Frist vorbei': 'status-fehler',
                  'unvollständig': 'status-warn' };

  /* ---------- Anträge (Entwürfe) ---------- */

  function rechnungText(e) {
    const r = e.rechnung || {};
    if (e.art === 'einzel') return r.prozent ? `${r.prozent} % von ${euro(r.preis_cent)} (${r.verspaetung} Min am Ziel)` : '';
    const deckel = r.gedeckelt ? ` → höchstens 25 % von ${euro(r.preis_cent)} = ${euro(r.deckel_cent)}` : '';
    if (e.art === 'dticket') {
      return `${r.minuten} Min aus ${r.zaehlbar} Fällen ab 20 Min = ${r.einheiten} volle Stunde${r.einheiten === 1 ? '' : 'n'} × `
        + `${euro(r.satz_cent)}${deckel}`;
    }
    return `${r.einheiten} Fall/Fälle ab 60 Min × ${euro(r.satz_cent)}${deckel}`;
  }

  function abtippenZeigen(box, d) {
    box.append(el('p', 'ausgabe', `Für ${d.person === 'Ich' ? 'dich' : d.person}, sortiert wie im Formular von ${d.stelle}. `
      + 'Jede Angabe lässt sich kopieren. IBAN und Unterschrift nie von Friday — die trägst du selbst ein.'));
    for (const h of d.hinweise || []) box.append(el('div', 'leise', h));
    for (const g of d.gruppen) {
      const teil = el('div', 'g-abtippen');
      teil.append(el('h4', '', g.titel));
      for (const [name, wert] of g.zeilen) {
        const zeile = el('div', 'g-abzeile');
        const links = el('div');
        links.append(el('span', 'klein', name), el('b', '', wert || '—'));
        zeile.append(links);
        if (wert && !/^—/.test(wert)) {
          zeile.append(knopf('Kopieren', 'leise-knopf', async (k) => {
            try { await navigator.clipboard.writeText(wert); k.textContent = '✓ kopiert'; } catch (_) { k.textContent = 'nicht möglich'; }
            setTimeout(() => { k.textContent = 'Kopieren'; }, 1500);
          }));
        }
        teil.append(zeile);
      }
      box.append(teil);
    }
  }

  function entwurfKarte(e, k) {
    const karte = el('div', 'eintrag');
    const kopf = el('div', 'kopfzeile');
    const wer = k.profil === null ? `${e.profil ? e.person : 'Ich'} · ` : '';
    kopf.append(el('span', 'titel', `${wer}${e.titel}`),
                el('span', `klein ${FARBE[e.stand] || ''}`, `${e.stand}${e.betrag_cent ? ` · ${euro(e.betrag_cent)}` : ''}`));
    karte.append(kopf);
    karte.append(el('div', 'klein', `${e.stelle_name} · ${e.faelle.length} Fall/Fälle`
      + (e.benoetigt && e.benoetigt.length < e.faelle.length ? ` (${e.benoetigt.length} reichen für den Betrag)` : '')
      + (e.offen ? ` · ${e.offen} warten noch auf die Messung` : '')));
    const r = rechnungText(e);
    if (r) karte.append(el('div', 'klein', r));
    const f = e.frist || {};
    if (e.stand === 'sammelt' && f.ab) karte.append(el('div', 'klein', `Einreichen ab ${datum(f.ab)}, wenn die Karte abgelaufen ist.`));
    else if (f.empfohlen && ['bereit', 'unvollständig'].includes(e.stand)) {
      karte.append(el('div', 'klein', `Einreichen bis ${datum(f.empfohlen)} (verjährt am ${datum(f.spaetestens)}).`));
    }
    if (e.stand === 'unter 4 €') karte.append(el('div', 'leise', 'Unter der Grenze von 4 € zahlt die Bahn nichts aus.'));
    for (const h of e.hindernisse || []) karte.append(el('div', 'klein status-fehler', h));
    if (e.fehlt && e.fehlt.length) karte.append(el('div', 'klein status-warn', `Fehlt noch: ${e.fehlt.join(', ')}`));
    for (const h of e.hinweise || []) karte.append(el('div', 'leise', h));
    const reihe = el('div', 'knoepfe');
    const unten = el('div');
    if (e.stand === 'bereit') {
      const basis = `/api/garantie/bahn/antrag/${encodeURIComponent(e.schluessel)}`;
      if (e.stelle === 'servicecenter') reihe.append(verweis('Formular (PDF) öffnen', `${basis}/pdf`));
      else {
        reihe.append(knopf('Zum Abtippen', 'leise-knopf', async () => {
          if (unten.children.length) { unten.textContent = ''; return; }
          try { abtippenZeigen(unten, await holen(`${basis}/abtippen`)); } catch (err) { unten.append(el('div', 'klein status-fehler', err.message)); }
        }));
        if (e.art !== 'einzel') reihe.append(verweis('Anschreiben (PDF)', `${basis}/anschreiben`));
        if (e.url) reihe.append(verweis(`Formular von ${e.stelle_name}`, e.url));
      }
      reihe.append(knopf('Eingereicht ✓', 'ja', async (b) => {
        const wer2 = e.profil ? e.person : 'dich';
        if (!confirm(`${e.titel} (${euro(e.betrag_cent)}) für ${wer2} bei ${e.stelle_name}: Hast du ihn abgegeben?\n\n`
          + 'Danach zählen die Fälle als beantragt und gehen nicht noch einmal raus.')) return;
        b.disabled = true;
        try { await senden(`${basis}/eingereicht`, { bestaetigt: true }); k.laden(); } catch (err) { b.disabled = false; unten.append(el('div', 'klein status-fehler', err.message)); }
      }));
    }
    if (reihe.children.length) karte.append(reihe);
    karte.append(unten);
    return karte;
  }

  function antraegeTeil(d, k) {
    const teil = el('div');
    teil.append(el('h3', '', 'Anträge'));
    if (!d.entwuerfe.length) {
      teil.append(el('p', 'leer-klein', 'Noch nichts gesammelt. Unten unter „Zug erfassen“ eine verspätete Fahrt auswählen.'));
      return teil;
    }
    for (const e of d.entwuerfe) teil.append(entwurfKarte(e, k));
    return teil;
  }

  /* ---------- Eingereicht und Bescheide ---------- */

  function eingereichtKarte(a, k) {
    const karte = el('div', 'eintrag');
    const kopf = el('div', 'kopfzeile');
    const zeitraum = a.von && a.bis && a.art !== 'einzel' ? `${datum(a.von)}–${datum(a.bis)}` : (a.von ? datum(a.von) : '');
    kopf.append(el('span', 'titel', `${k.profil === null ? `${a.profil ? a.person : 'Ich'} · ` : ''}B${a.id} ${a.stelle_name} ${zeitraum}`),
                a.status === 'genehmigt' ? el('span', 'g-plus-euro', `+${a.erhalten}`)
                                         : el('span', `klein ${FARBE[a.status] || ''}`, a.status));
    karte.append(kopf);
    karte.append(el('div', 'klein', `${(a.faelle || []).length} Fall/Fälle · erwartet ${a.erwartet} · eingereicht ${tag(a.eingereicht_am)}`
      + (a.vorgang ? ` · Vorgang ${a.vorgang}` : '')));
    if (a.grund) karte.append(el('div', 'leise', a.grund));
    const reihe = el('div', 'knoepfe');
    reihe.append(verweis('PDF', `/api/garantie/bahn/antrag/${encodeURIComponent(a.schluessel)}/pdf`));
    const form = el('div');
    if (a.status === 'eingereicht') {
      reihe.append(knopf('Bescheid eintragen', 'leise-knopf', () => { z.bescheid = z.bescheid === a.id ? null : a.id; k.zeichnen(); }));
      if (z.bescheid === a.id) {
        const betrag = el('input');
        betrag.inputMode = 'decimal';
        betrag.value = (a.erwartet || '').replace(' €', '');
        const grund = el('input');
        grund.placeholder = 'Begründung (bei Ablehnung)';
        const vorgang = el('input');
        vorgang.placeholder = 'Vorgangsnummer (falls genannt)';
        const feld = (text, eingabe) => { const f = el('label', 'g-feld'); f.append(el('span', 'klein', text), eingabe); return f; };
        form.append(feld('Genehmigt (Euro)', betrag), feld('Vorgang', vorgang), feld('Abgelehnt, weil', grund));
        const knoepfe = el('div', 'knoepfe');
        const setzen = (status) => async (b) => {
          b.disabled = true;
          const zahl = Number(String(betrag.value).replace(',', '.'));
          try {
            await senden(`/api/garantie/bahn/eingereicht/${a.id}`, { status, betrag: status === 'genehmigt' ? zahl : null,
                                                                    grund: grund.value, vorgang: vorgang.value });
            z.bescheid = null;
            k.laden();
          } catch (err) { b.disabled = false; form.append(el('div', 'klein status-fehler', err.message)); }
        };
        knoepfe.append(knopf('Genehmigt', 'ja', setzen('genehmigt')), knopf('Abgelehnt', 'nein', setzen('abgelehnt')));
        form.append(knoepfe);
      }
    }
    karte.append(reihe, form);
    return karte;
  }

  function eingereichtTeil(d, k) {
    const teil = el('details', 'g-archiv');
    teil.open = z.auf.eingereicht;
    teil.addEventListener('toggle', () => { z.auf.eingereicht = teil.open; });
    teil.append(el('summary', 'klein', `Eingereicht (${d.antraege.length})`));
    if (!d.antraege.length) teil.append(el('p', 'leer-klein', 'Noch nichts eingereicht.'));
    for (const a of d.antraege) teil.append(eingereichtKarte(a, k));
    return teil;
  }

  /* ---------- Fälle ---------- */

  function fallKarte(f, k) {
    const karte = el('div', 'eintrag');
    const kopf = el('div', 'kopfzeile');
    kopf.append(el('span', 'titel', `${k.profil === null ? `${f.profil ? f.person : 'Ich'} · ` : ''}${f.linie}: ${f.start} → ${f.ziel}`),
                el('span', `klein ${FARBE[f.stand] || ''}`, f.stand === 'wartet' ? `wartet bis ${uhr(f.faellig_ab)}` : f.stand));
    karte.append(kopf);
    karte.append(el('div', 'klein', `${tag(f.ab)} ${uhr(f.ab)}–${uhr(f.an)} · `
      + (f.verspaetung === null || f.verspaetung === undefined ? 'noch keine Messung' : `${f.verspaetung} Min am Ziel`)
      + ` · ${f.art === 'einzel' ? `Einzelfahrkarte ${euro(f.preis_cent)}` : 'Zeitkarte'} · ${f.stelle_name}`));
    for (const h of f.hindernisse || []) karte.append(el('div', 'klein status-fehler', h));
    if (f.fehlt && f.fehlt.length) karte.append(el('div', 'klein status-warn', `Fehlt noch: ${f.fehlt.join(', ')}`));
    const reihe = el('div', 'knoepfe');
    const fehler = (b, err) => { b.disabled = false; karte.append(el('div', 'klein status-fehler', err.message)); };
    if (f.stand === 'verworfen') {
      reihe.append(knopf('Zurückholen', 'leise-knopf', async (b) => {
        b.disabled = true;
        try { await senden(`/api/garantie/bahn/fall/${f.id}`, { status: 'offen' }); k.laden(); } catch (err) { fehler(b, err); }
      }));
    } else {
      reihe.append(knopf('Verwerfen', 'leise-knopf', async (b) => {
        b.disabled = true;
        try { await senden(`/api/garantie/bahn/fall/${f.id}`, { status: 'verworfen' }); k.laden(); } catch (err) { fehler(b, err); }
      }));
    }
    reihe.append(knopf('Löschen', 'weg', async (b) => {
      if (!confirm(`Bahn-Fall #${f.id} (${f.linie}, ${tag(f.ab)}) löschen?`)) return;
      b.disabled = true;
      try { await entfernen(`/api/garantie/bahn/fall/${f.id}`); k.laden(); } catch (err) { fehler(b, err); }
    }));
    karte.append(reihe);
    return karte;
  }

  function faelleTeil(d, k) {
    const teil = el('details', 'g-fahrten');
    teil.open = z.auf.faelle;
    teil.addEventListener('toggle', () => { z.auf.faelle = teil.open; });
    const gezaehlt = d.faelle.filter((f) => f.stand === 'gesammelt' || f.stand === 'bereit').length;
    teil.append(el('summary', '', `Fälle (${d.faelle.length}, davon ${gezaehlt} mit Anspruch)`));
    if (!d.faelle.length) teil.append(el('p', 'leer-klein', 'Noch keine.'));
    for (const f of d.faelle) teil.append(fallKarte(f, k));
    return teil;
  }

  /* ---------- Zug erfassen ---------- */

  async function zugOeffnen(id, k) {
    if (z.zug === id) { z.zug = null; z.verlauf = null; k.zeichnen(); return; }
    z.zug = id; z.verlauf = null; z.wahl = { start: null, ziel: null }; z.meldung = '';
    k.zeichnen();
    try { z.verlauf = await holen(`/api/garantie/bahn/verlauf?id=${encodeURIComponent(id)}`); }
    catch (err) { z.meldung = err.message; }
    // Ein Fernzug geht nur mit einer Fahrkarte für ihn — dann gleich „Einzelfahrkarte“ vorschlagen.
    if (z.verlauf && z.verlauf.fern) z.karte = 'einzel';
    k.zeichnen();
  }

  function zugKarte(f, k) {
    const karte = el('div', `eintrag klickbar${z.zug === f.id ? ' gewaehlt' : ''}`);
    const kopf = el('div', 'kopfzeile');
    kopf.append(el('span', 'titel', `${f.linie} → ${f.richtung}`),
                el('span', `klein ${f.verspaetung >= 60 ? 'status-warn' : ''}`,
                   f.verspaetung === null || f.verspaetung === undefined ? 'keine Echtzeit' : `+${f.verspaetung} Min`));
    karte.append(kopf);
    karte.append(el('div', 'klein', `${tag(f.plan)} ${uhr(f.plan)} · ${f.halt}${f.betreiber ? ` · ${f.betreiber}` : ''}`));
    karte.addEventListener('click', () => zugOeffnen(f.id, k));
    if (z.zug === f.id) karte.append(verlaufPanel(k));
    return karte;
  }

  function verlaufPanel(k) {
    const d = k.daten;
    const panel = el('div', 'g-panel');
    panel.addEventListener('click', (e) => e.stopPropagation());
    if (!z.verlauf) { panel.append(el('div', 'klein', z.meldung || 'Friday holt den Verlauf …')); return panel; }
    const v = z.verlauf;
    if (!v.zug) { panel.append(el('div', 'klein status-fehler', 'Das ist kein Zug — für Bus und Stadtbahn ist die ÜSTRA-Garantie da.')); return panel; }
    panel.append(el('div', 'klein', `${v.linie} · ${v.betreiber || 'Betreiber unbekannt'} → Antrag bei: ${v.stelle}. `
      + 'Tippe an, wo eingestiegen und wo ausgestiegen wurde.'));
    const tabelle = el('div', 'g-verlauf');
    v.halte.forEach((h, i) => {
      const zeile = el('div', 'g-halt');
      if (z.wahl.start === i || z.wahl.ziel === i) zeile.classList.add('gewaehlt');
      const plan = h.an_plan || h.ab_plan;
      const vs = h.faellt_aus ? 'fällt aus' : (h.verspaetung === null || h.verspaetung === undefined ? '–' : `+${h.verspaetung}`);
      zeile.append(el('span', 'g-zeit', uhr(plan)), el('span', 'g-name', h.name), el('span', `g-plus ${h.verspaetung >= 20 ? 'status-warn' : ''}`, vs));
      const wahl = el('span', 'g-wahl');
      wahl.append(knopf(z.wahl.start === i ? 'Start ✓' : 'Start', 'leise-knopf', () => { z.wahl.start = i; if (z.wahl.ziel !== null && z.wahl.ziel <= i) z.wahl.ziel = null; k.zeichnen(); }),
                  knopf(z.wahl.ziel === i ? 'Ziel ✓' : 'Ziel', 'leise-knopf', () => { z.wahl.ziel = i; k.zeichnen(); }));
      zeile.append(wahl);
      tabelle.append(zeile);
    });
    panel.append(tabelle);
    const bereit = z.wahl.start !== null && z.wahl.ziel !== null && z.wahl.start < z.wahl.ziel;
    if (z.wahl.start !== null && z.wahl.ziel !== null && !bereit) panel.append(el('div', 'klein status-fehler', 'Das Ziel muss nach dem Start kommen.'));

    // Für wen? Auf der Personenseite steht es fest.
    if (k.profil === null) {
      const wahl = el('select');
      for (const p of d.personen || []) { const o = el('option', '', p.id ? p.name : 'Ich'); o.value = String(p.id); wahl.append(o); }
      wahl.value = String(z.fuer);
      wahl.addEventListener('change', () => { z.fuer = Number(wahl.value); });
      const f = el('label', 'g-feld');
      f.append(el('span', 'klein', 'Für wen?'), wahl);
      panel.append(f);
    }
    // Welche Fahrkarte?
    panel.append(el('div', 'g-frage', 'Fahrkarte'));
    const radio = (wert, text) => {
      const l = el('label', 'check klein');
      const r = el('input');
      r.type = 'radio'; r.name = 'fb-karte'; r.checked = z.karte === wert;
      r.addEventListener('change', () => { z.karte = wert; k.zeichnen(); });
      l.append(r, el('span', '', text));
      return l;
    };
    panel.append(radio('zeitkarte', 'Deutschlandticket oder Monatskarte (das hochgeladene Ticket des Tages)'),
                 radio('einzel', 'Einzelfahrkarte (z. B. ICE-Ticket) — Preis für diese Richtung'));
    if (z.karte === 'einzel') {
      const preis = el('input');
      preis.inputMode = 'decimal'; preis.placeholder = 'z. B. 29,90'; preis.value = z.preis;
      preis.addEventListener('input', () => { z.preis = preis.value; });
      const auftrag = el('input');
      auftrag.placeholder = 'Auftragsnummer (Online-Ticket, optional)'; auftrag.value = z.auftrag;
      auftrag.addEventListener('input', () => { z.auftrag = auftrag.value.replace(/[^A-Za-z0-9]/g, ''); });
      const feld = (text, eingabe) => { const f = el('label', 'g-feld'); f.append(el('span', 'klein', text), eingabe); return f; };
      const raster = el('div', 'g-raster');
      raster.append(feld('Preis (Euro)', preis), feld('Auftragsnummer', auftrag));
      panel.append(raster);
      const rl = el('label', 'check klein');
      const rc = el('input');
      rc.type = 'checkbox'; rc.checked = z.rueckfahrt;
      rc.addEventListener('change', () => { z.rueckfahrt = rc.checked; });
      rl.append(rc, el('span', '', 'Rückfahrt einer Hin- und Rückfahrkarte (dann den halben Preis eintragen)'));
      panel.append(rl);
    } else if (v.fern) {
      panel.append(el('div', 'klein status-warn', 'Ein Fernzug: Mit Deutschlandticket oder Monatskarte gibt es hier nichts — nur mit einer Fahrkarte für diesen Zug.'));
    }
    const reihe = el('div', 'knoepfe');
    const los = knopf('Als Fahrgastrecht erfassen', '', async (b) => {
      b.disabled = true;
      const preis = Number(String(z.preis).replace(',', '.'));
      z.meldung = 'Friday prüft Messung, Ticket und Sperren …';
      k.zeichnen();
      try {
        const r = await senden('/api/garantie/bahn/fall', {
          fahrt: z.zug, start: v.halte[z.wahl.start].reference, ziel: v.halte[z.wahl.ziel].reference,
          profil: k.profil === null ? z.fuer : k.profil, fahrkarte: z.karte,
          preis: z.karte === 'einzel' && preis > 0 ? preis : null, auftrag: z.karte === 'einzel' ? (z.auftrag || null) : null,
          rueckfahrt: z.karte === 'einzel' && z.rueckfahrt });
        z.meldung = r.text;
        if (r.ok) { z.wahl = { start: null, ziel: null }; z.preis = ''; z.auftrag = ''; z.rueckfahrt = false; k.laden(); return; }
      } catch (err) { z.meldung = err.message; }
      k.zeichnen();
    });
    los.disabled = !bereit || (z.karte === 'einzel' && !(Number(String(z.preis).replace(',', '.')) > 0));
    reihe.append(los);
    panel.append(reihe);
    if (z.meldung) panel.append(el('div', 'ausgabe', z.meldung));
    return panel;
  }

  function erfassenTeil(d, k) {
    const teil = el('details', 'g-fahrten');
    teil.open = z.auf.erfassen || !!z.zug;
    teil.addEventListener('toggle', () => { z.auf.erfassen = teil.open; });
    teil.append(el('summary', '', 'Zug erfassen'));
    teil.append(el('p', 'leise', 'Verspätete Züge ab 20 Minuten aus Fridays Sammler — oder einen Zug an einem Bahnhof suchen '
      + '(auch ICE und IC). Am besten noch am selben Tag: Später kennt die Auskunft die Echtzeit nicht mehr.'));
    if (d.zuege.length) {
      teil.append(el('h4', '', 'Gemerkt (48 Stunden)'));
      for (const f of d.zuege.slice(0, 20)) teil.append(zugKarte(f, k));
    }
    teil.append(el('h4', '', 'Zug suchen'));
    const halt = el('input');
    halt.value = z.halt;
    halt.addEventListener('input', () => { z.halt = halt.value; });
    const wann = el('input');
    wann.type = 'datetime-local';
    if (!z.zeit) z.zeit = lokal(new Date(Date.now() - 30 * 60000));
    wann.value = z.zeit;
    wann.addEventListener('input', () => { z.zeit = wann.value; });
    const feld = (text, eingabe) => { const f = el('label', 'g-feld'); f.append(el('span', 'klein', text), eingabe); return f; };
    const raster = el('div', 'g-raster');
    raster.append(feld('Bahnhof', halt), feld('Abfahrt ab', wann));
    teil.append(raster);
    const reihe = el('div', 'knoepfe');
    reihe.append(knopf('Züge suchen', '', async (b) => {
      b.disabled = true;
      z.suchText = 'Friday fragt die Auskunft …';
      k.zeichnen();
      try {
        z.gefunden = await holen(`/api/garantie/bahn/zuege?halt=${encodeURIComponent(z.halt)}&zeit=${encodeURIComponent(z.zeit)}`);
        z.suchText = z.gefunden.zuege.length ? `${z.gefunden.zuege.length} Züge ab ${z.gefunden.halt}` : `Keine Züge ab ${z.gefunden.halt} in dieser Zeit.`;
      } catch (err) { z.gefunden = null; z.suchText = err.message; }
      k.zeichnen();
    }));
    teil.append(reihe);
    if (z.suchText) teil.append(el('div', 'klein', z.suchText));
    if (z.gefunden) for (const f of z.gefunden.zuege) teil.append(zugKarte(f, k));
    return teil;
  }

  /* ---------- Überblick und Regeln ---------- */

  function kacheln(d) {
    const s = d.summen || {};
    const bereit = d.entwuerfe.filter((e) => e.stand === 'bereit');
    const k = el('div', 'g-kacheln');
    const kachel = (wert, text) => { const x = el('div', 'g-kachel'); x.append(el('div', 'g-wert', wert), el('div', 'klein', text)); k.append(x); };
    kachel(euro(bereit.reduce((n, e) => n + (e.betrag_cent || 0), 0)), `bereit zum Einreichen (${bereit.length})`);
    kachel(euro(s.offen_cent), `eingereicht, offen (${s.eingereicht || 0})`);
    kachel(euro(s.genehmigt_cent), `genehmigt (${s.genehmigt || 0})`);
    return k;
  }

  function regeln(d) {
    const teil = el('details', 'g-archiv');
    teil.append(el('summary', 'klein', 'Was gilt? (mit Quellen)'));
    for (const r of d.regeln || []) teil.append(el('div', 'leise', `• ${r}`));
    for (const q of d.quellen || []) {
      const zeile = el('div', 'leise');
      const a = el('a', '', q.kurz);
      a.href = q.url; a.target = '_blank'; a.rel = 'noopener';
      zeile.append(a, document.createTextNode(` (${q.stand})`));
      teil.append(zeile);
    }
    return teil;
  }

  function block(d, kontext) {
    const k = { ...kontext, daten: d };
    const b = el('div', 'block');
    b.append(el('h2', '', 'Bahn-Fahrgastrechte'));
    if (!d || !d.aktiv) {
      b.append(el('p', 'leise', 'Aus. Für Züge von der S-Bahn bis zum ICE: ab 60 Minuten am Ziel 25 % (ab 120 Minuten 50 %) '
        + 'einer Einzelfahrkarte, mit Deutschlandticket 1,50 € je volle Verspätungsstunde. Einschalten auf der '
        + 'Freigaben-Seite unter „Bahn-Fahrgastrechte“ — Friday schickt nichts ab.'));
      b.append(regeln(d || {}));
      return b;
    }
    b.append(el('p', 'leise', 'Züge von der S-Bahn bis zum ICE. Für dieselbe Fahrt gibt es entweder die ÜSTRA-Garantie '
      + '(5 € ab 20 Minuten) oder Fahrgastrechte — nie beides. Friday druckt oder listet den Antrag; einreichen tust du.'));
    b.append(kacheln(d), antraegeTeil(d, k), eingereichtTeil(d, k), faelleTeil(d, k), erfassenTeil(d, k), regeln(d));
    return b;
  }

  window.FridayBahn = { block };
})();
