/* Extras-Seite: Reiter „Studio“ und „Garantie“ (dazu „HA“ in hausaufgaben.js).

   Garantie, mit so wenig Aufwand wie möglich für Eike: „Suchen“ → eine Fahrt
   antippen → ihr Verlauf → Start und Ziel antippen → für wen? → Friday bereitet vor
   und prüft die Bedingungen. Das Ticket jeden Monat hochladen — Art und Gültigkeit
   liest Friday selbst aus. Angaben einmal eintragen, danach bleiben sie zugeklappt.

   Abgeschickt wird nur über den Knopf „Abschicken“ (eigene Freigabe, vorher
   Prüfung und Rückfrage). „Selbst eingereicht“ zählt mit, was Eike auf der
   Webseite eingereicht hat.

   Zwei Ansichten (21.09.2026, weil Eike für immer mehr Freunde einreicht):
   - Übersicht: was über alle Personen ansteht (bereite Anträge, Rückfragen, fehlende
     Tickets), je Person eine Zeile mit Ampel und Limits — wer noch kann, wer voll ist —,
     die verspäteten Fahrten mit Personenauswahl und das Geld in Kürze.
   - Person: alles von genau einer Person — Anträge, Ticket, Angaben, Limits, Bescheide,
     Abrechnung. Anträge, Ticket und Angaben gehören immer zu genau einer Person.
   Alles per textContent, nie als HTML: Namen kommen von fremden Diensten. */
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
    if (!r.ok) throw new Error(d.detail || `Fehler ${r.status}`);
    return d;
  };
  const holen = async (url) => antwort(await fetch(url, { cache: 'no-store' }));
  const senden = async (url, daten) => antwort(await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(daten || {}),
  }));
  const stellen = async (url, daten) => antwort(await fetch(url, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(daten || {}),
  }));
  const uhr = (iso) => { try { return new Date(iso).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }); } catch (_) { return ''; } };
  const tag = (iso) => { try { return new Date(iso).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' }); } catch (_) { return ''; } };
  const datum = (iso) => (iso ? new Date(`${iso}T12:00:00`).toLocaleDateString('de-DE') : '?');
  const kurzDatum = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.` : '?');
  const block = (titel) => { const b = el('div', 'block'); b.append(el('h2', '', titel)); return b; };
  const euro = (cent) => `${((cent || 0) / 100).toFixed(2).replace('.', ',')} €`;
  const monatsname = (m) => { try { return new Date(`${m}-15T12:00:00`).toLocaleDateString('de-DE', { month: 'long', year: 'numeric' }); } catch (_) { return m; } };
  const JUGEND = /jugendnetz|ausbildungsnetz/i;

  /* ---------- Reiter ---------- */
  let reiter = 'studio';
  for (const b of document.querySelectorAll('#studio .reiter button')) {
    b.addEventListener('click', () => {
      reiter = b.dataset.reiter;
      for (const x of document.querySelectorAll('#studio .reiter button')) x.classList.toggle('aktiv', x === b);
      for (const x of document.querySelectorAll('#studio .reiter button')) $(`r-${x.dataset.reiter}`).hidden = x !== b;
      laden();
    });
  }

  /* ---------- Zustand der Seite ---------- */
  let ansicht = 'alle';    // 'alle' (Übersicht) | 'person' | 'neu' (neue Person anlegen)
  let uebersicht = null;   // GET /api/garantie/uebersicht
  let stand = null;        // GET /api/garantie?profil=… — eine Person
  let profil = 0;          // die Person der Personenseite: 0 = ich, sonst ein Freund
  let gesucht = null;      // Ergebnis des letzten „Suchen“: { fahrten, bereich }
  let offen = null;        // gewählte Fahrt-Kennung
  let verlauf = null;
  let wahl = { start: null, ziel: null };
  let meldung = '';
  let ticketText = '';
  let aufgabenText = '';
  let fuerWen = null;      // für die offene Fahrt: wer kann (GET /api/garantie/fahrt/personen)
  const auswahl = new Set();
  let filter = 'alle';     // Personen-Filter der Übersicht
  let namensuche = '';
  let verfolgungOffen = false; // Übersicht: Block „Verfolgung“ aufgeklappt?
  let fahrtenOffen = false; // Personenseite: Block „Fahrt vorbereiten“ aufgeklappt?
  let profilForm = null;   // 'neu' oder die Id eines Profils, dessen Angaben gerade bearbeitet werden
  try { filter = localStorage.getItem('friday.garantie.filter') || 'alle'; } catch (_) {}

  // Bus oder Bahn? Die Auskunft nennt „Bus“, „S-Bahn“, „R-Bahn“, „Stadtbahn“, „Zug“ und Betreiberkürzel für den
  // Schienenersatzverkehr (Linie „SEV“) — der fährt mit Bussen, zählt hier also zu Bus.
  let fahrzeug = 'alle';   // 'alle' | 's5' | 'bus' | 'bahn'
  try { fahrzeug = localStorage.getItem('friday.garantie.fahrzeug') || 'alle'; } catch (_) {}
  const istBahn = (f) => /bahn|zug/i.test(f.kategorie || '');
  const istS5 = (f) => /^S\s*5$/i.test((f.linie || '').trim());
  const passtFahrzeug = (f) => fahrzeug === 'alle' || (fahrzeug === 's5' ? istS5(f) : fahrzeug === 'bahn' ? istBahn(f) : !istBahn(f));
  // Meldungen zu Verspätungen: 'alle' | 's5' (nur die S5) | 'aus' — Stand aus den Benachrichtigungs-Einstellungen.
  let meldungenStand = null;
  const meldungenLesen = (d) => {
    const an = Object.fromEntries((d.arten || []).map((a) => [a.id, a.an]));
    meldungenStand = an.verspaetungen ? 'alle' : an.verspaetungen_s5 ? 's5' : 'aus';
  };
  const meldungenSetzen = async (wert) => {
    try {
      await senden('/api/einstellungen/benachrichtigungen', { art: 'verspaetungen', an: wert === 'alle' });
      meldungenLesen(await senden('/api/einstellungen/benachrichtigungen', { art: 'verspaetungen_s5', an: wert === 's5' }));
    } catch (e) { meldung = e.message; }
    zeichnen();
  };
  const s5Zuerst = (liste) => [...liste.filter(istS5), ...liste.filter((f) => !istS5(f))];
  const fahrzeugSetzen = (x) => {
    fahrzeug = x;
    seiten.gesucht = 0;
    seiten.gemerkt = 0;
    try { localStorage.setItem('friday.garantie.fahrzeug', x); } catch (_) {}
    zeichnen();
  };

  // Was gerade gezeigt wird: die Übersicht oder eine Person — beide tragen orte, aktiv, abschicken und reisen.
  const daten = () => (ansicht === 'person' ? stand : uebersicht);
  const personName = () => (profil ? ((stand && stand.angaben && stand.angaben.name) || 'diese Person') : 'dir');

  function personOeffnen(id) {
    profil = Number(id) || 0;
    ansicht = 'person';
    stand = null;
    profilForm = null;
    fahrtenOffen = false;
    offen = null; verlauf = null; meldung = ''; ticketText = '';
    seiten.antraege = 0; seiten.post = 0;
    $('garantie-inhalt').textContent = '';
    $('garantie-inhalt').append(el('p', 'leer-klein', 'lädt …'));
    laden();
    window.scrollTo({ top: 0 });
  }

  function zurUebersicht() {
    ansicht = 'alle';
    profilForm = null;
    offen = null; verlauf = null; meldung = ''; ticketText = '';
    laden();
    window.scrollTo({ top: 0 });
  }

  const PRO_SEITE = 10;
  const seiten = {};       // Liste → aktuelle Seite

  // Eine Liste in Seiten zu je zehn, mit „Zurück“ und „Weiter“.
  function blaettern(ziel, name, liste, zeichneEintrag) {
    const anzahl = Math.max(1, Math.ceil(liste.length / PRO_SEITE));
    const seite = Math.min(seiten[name] || 0, anzahl - 1);
    for (const x of liste.slice(seite * PRO_SEITE, (seite + 1) * PRO_SEITE)) ziel.append(zeichneEintrag(x));
    if (anzahl < 2) return;
    const reihe = el('div', 'knoepfe g-blaettern');
    const zurueck = knopf('‹ Zurück', 'leise-knopf', () => { seiten[name] = seite - 1; zeichnen(); });
    const weiter = knopf('Weiter ›', 'leise-knopf', () => { seiten[name] = seite + 1; zeichnen(); });
    zurueck.disabled = seite === 0;
    weiter.disabled = seite >= anzahl - 1;
    reihe.append(zurueck, el('span', 'klein', `Seite ${seite + 1} von ${anzahl} · ${liste.length} Einträge`), weiter);
    ziel.append(reihe);
  }

  // Ein Balken: genehmigt (voll), dahinter schraffiert, was abgeschickt ist und noch auf den Bescheid wartet.
  function balken(n, offenN, max) {
    const b = el('div', 'g-balken');
    const voll = el('div', `g-fuellung${n >= max ? ' voll' : ''}`);
    voll.style.width = `${Math.min(100, Math.round(100 * n / Math.max(max, 1)))}%`;
    b.append(voll);
    if (offenN) {
      const rest = el('div', 'g-fuellung g-offen');
      rest.style.width = `${Math.max(0, Math.min(100 - Math.round(100 * n / Math.max(max, 1)), Math.round(100 * offenN / Math.max(max, 1))))}%`;
      b.append(rest);
    }
    return b;
  }

  function zaehlerBlock(z) {
    // Nur genehmigte Fahrten zählen für die Höchstzahlen (nach Fahrtag); schraffiert, was noch auf den Bescheid wartet.
    const b = block(profil ? `Limits von ${personName()}` : 'Limits');
    const zeile = (text, n, max, offenN = 0) => {
      const e = el('div', 'g-monat');
      e.append(el('span', 'klein', text), balken(n, offenN, max),
               el('span', `klein ${n >= max ? 'status-fehler' : ''}`, `${n} von ${max}${offenN ? ` (+${offenN} offen)` : ''}`));
      return e;
    };
    const liste = el('div', 'g-monate');
    if (z.mit_dticket) liste.append(zeile(`${z.monat_name} (D-Ticket)`, z.dticket, z.dticket_max, z.offen_monat || 0));
    liste.append(zeile(z.monat_name, z.monat, z.monat_max, z.mit_dticket ? 0 : (z.offen_monat || 0)));
    if (z.preis_cent) {
      // Über die ganze Gültigkeit des Tickets (nicht nur diesen Kalendermonat) — wie viel schon zur Auszahlung
      // bestätigt ist. Die Warnungen unten bleiben beim Kalendermonat, weil nur der fürs Sperren zählt.
      const erstattet = Math.min(z.ticket_erstattet_cent || 0, z.preis_cent);
      const e = el('div', 'g-monat');
      e.append(el('span', 'klein', 'Kartenpreis'), balken(erstattet, 0, z.preis_cent),
               el('span', `klein ${erstattet >= z.preis_cent ? 'status-fehler' : ''}`, `${euro(erstattet)} von ${euro(z.preis_cent)}`));
      liste.append(e);
    }
    b.append(liste);
    const preisVoll = z.preis_rest_cent !== null && z.preis_rest_cent !== undefined && z.preis_rest_cent <= 0;
    if (z.monat_rest === 0 || preisVoll) {
      b.append(el('p', 'status-fehler', preisVoll
        ? `Für den ${z.monat_name} ist der Kartenpreis erstattet — weitere Anträge bringen nichts mehr.`
        : `Für den ${z.monat_name} ist das Maximum erreicht — weitere Anträge werden nicht erstattet.`));
    } else {
      const frei = z.frei_monat ?? z.monat_rest;
      const warum = [];
      if (z.offen_monat) warum.push(`${z.offen_monat} abgeschickte${z.offen_monat === 1 ? 'r' : ''} Antrag${z.offen_monat === 1 ? '' : 'e'} ohne Bescheid zähl${z.offen_monat === 1 ? 't' : 'en'} schon mit`);
      if (z.preis_cent && frei < z.monat_rest - (z.offen_monat || 0)) warum.push(`vom Kartenpreis sind für diese Karte nur noch ${euro(Math.max(0, z.preis_rest_cent))} übrig`);
      b.append(el('p', '', `Noch möglich: ${frei} im ${z.monat_name}`
        + (frei < z.monat_rest && warum.length ? ` (${warum.join('; ')})` : '') + '.'));
    }
    if (z.offen) b.append(el('p', 'leise', `${z.offen} Antrag/Anträge warten noch auf einen Bescheid — genehmigt zählen sie mit.`));
    b.append(el('p', 'leise', `Gezählt werden nur genehmigte Fahrten (insgesamt ${z.insgesamt}), nach dem Fahrtag. `
                              + `Vorbereitet: ${z.vorbereitet} (${z.bereit} bereit, ${z.wartet} warten auf die Ankunft`
                              + (z.limit ? `, ${z.limit} über dem Limit` : '') + ').'));
    return b;
  }

  // Ein Balken je Monat (Geld, Fallzahl) — für eine Person oder, mit `monate` aus `statistik(None)`, für alle zusammen.
  function monateListe(monate) {
    if (!monate.length) return null;
    const max = Math.max(...monate.map((m) => m.cent), 1);
    const liste = el('div', 'g-monate');
    for (const m of monate) {
      const zeile = el('div', 'g-monat');
      const b2 = el('div', 'g-balken');
      const fuellung = el('div', 'g-fuellung');
      fuellung.style.width = `${Math.round(100 * m.cent / max)}%`;
      b2.append(fuellung);
      zeile.append(el('span', 'klein', monatsname(m.monat)), b2, el('span', 'klein', `${euro(m.cent)} · ${m.anzahl}×`));
      liste.append(zeile);
    }
    return liste;
  }

  function geldBlock(st) {
    const b = block(profil ? `Eingenommen von ${personName()}` : 'Eingenommen');
    const kacheln = el('div', 'g-kacheln');
    const kachel = (wert, text) => { const k = el('div', 'g-kachel'); k.append(el('div', 'g-wert', wert), el('div', 'klein', text)); kacheln.append(k); };
    kachel(euro(st.insgesamt_cent), 'insgesamt');
    kachel(euro(st.monat_cent), `diesen Monat (${st.monat_anzahl})`);
    kachel(euro(st.jahr_cent), 'dieses Jahr');
    kachel(euro(st.mit_friday_cent), `mit Fridays Hilfe (${st.mit_friday_anzahl})`);
    kachel(st.quote === null ? '–' : `${st.quote} %`, 'genehmigt von entschiedenen');
    b.append(kacheln);
    b.append(el('p', 'leise', `${st.faelle} Fälle: ${st.genehmigt} genehmigt, ${st.abgelehnt} abgelehnt, `
                              + `${st.offen} offen${st.rueckfragen ? ` (davon ${st.rueckfragen} mit Rückfrage)` : ''}`
                              + (st.ohne_bescheid ? `, ${st.ohne_bescheid} ohne Bescheid` : '') + '.'));
    const liste = monateListe(st.monate);
    if (liste) b.append(liste);
    b.append(el('p', 'leise', 'Gezählt nach dem Tag des Bescheids. Quelle: die Mails der ÜSTRA in deinem Gmail.'));
    return b;
  }

  function abgleichKnopf(info) {
    return knopf('Bescheide jetzt abgleichen', 'leise-knopf', async (k) => {
      k.disabled = true; info.textContent = 'Friday liest die Mails der ÜSTRA …';
      try {
        const d = await senden('/api/garantie/post');
        info.textContent = `${d.mails} Mails gelesen, ${d.geaendert} Fälle neu oder geändert, ${d.verknuepft} mit Anträgen verknüpft.`;
        await laden();
      } catch (e) { info.textContent = e.message; k.disabled = false; }
    });
  }

  // Eine alte Rückfrage abhaken — oder zurückholen. Kommt später eine neue Mail zu dem Fall, ist sie von selbst wieder offen.
  function erledigtKnopf(v, ziel) {
    const ab = v.stand === 'rueckfrage';
    return knopf(ab ? 'Erledigt – ausblenden' : 'Rückfrage wieder öffnen', 'leise-knopf', async (k) => {
      k.disabled = true;
      try { await senden(`/api/garantie/vorgang/${encodeURIComponent(v.vorgang)}/erledigt`, { erledigt: ab }); await laden(); }
      catch (e) { k.disabled = false; ziel.append(el('div', 'klein status-fehler', e.message)); }
    });
  }

  function postBlock(verbunden, vorgaenge) {
    const b = block('Bescheide aus Gmail');
    if (!verbunden) {
      b.append(el('p', 'leise', 'Noch nicht verbunden. Freigaben → „Gmail“: Gmail-Adresse und '
                                + 'ein App-Passwort eintragen. Für die Bescheide liest Friday dann die Mails von info@uestra.de.'));
      return b;
    }
    const info = el('div', 'klein');
    const reihe = el('div', 'knoepfe');
    reihe.append(abgleichKnopf(info));
    b.append(reihe, info);
    if (!vorgaenge.length) { b.append(el('p', 'leer-klein', 'Noch keine Garantiefälle gefunden.')); return b; }
    const farbe = { genehmigt: 'status-ok', abgelehnt: 'status-fehler', rueckfrage: 'status-warn', ohne_bescheid: 'leise', erledigt: 'leise' };
    const sortiert = [...vorgaenge].sort((x, y) => (y.fahrtdatum || y.antragstag || '').localeCompare(x.fahrtdatum || x.antragstag || ''));
    blaettern(b, 'post', sortiert, (v) => {
      const karte = el('div', 'eintrag');
      const kopf = el('div', 'kopfzeile');
      const tagIso = v.fahrtdatum || v.antragstag;
      const tagText = tagIso ? `${tagIso.slice(8, 10)}.${tagIso.slice(5, 7)}.${tagIso.slice(2, 4)}` : '';
      const strecke = v.start ? `${v.start}${v.ziel ? ' → ' + v.ziel : ''}` : `Fall ${v.vorgang}`;
      kopf.append(el('span', 'titel', `${tagText} ${v.linie ? 'Linie ' + v.linie + ': ' : ''}${strecke}`),
                  v.stand === 'genehmigt' ? el('span', 'g-plus-euro', `+${v.euro}`)
                                          : el('span', `klein ${farbe[v.stand] || ''}`, v.stand_text || v.stand || '?'));
      karte.append(kopf);
      const teile = [`Fall ${v.vorgang}`];
      if (v.ab && v.an) teile.push(`${v.ab}–${v.an}`);
      if (v.fahrkarte) teile.push(v.fahrkarte);
      if (v.bearbeiter && v.bearbeiter !== 'ÜSTRA') teile.push(`bearbeitet von ${v.bearbeiter}`);
      karte.append(el('div', 'klein', teile.join(' · ')));
      if (v.grund) karte.append(el('div', 'leise', v.grund));
      if (v.stand === 'rueckfrage' || v.stand === 'erledigt') {
        const reihe2 = el('div', 'knoepfe');
        reihe2.append(erledigtKnopf(v, karte));
        karte.append(reihe2);
      }
      return karte;
    });
    return b;
  }

  /* ---------- Fahrten: suchen, Verlauf, Start/Ziel, für wen ---------- */

  function fahrtKarte(f) {
    const karte = el('div', `eintrag klickbar${offen === f.id ? ' gewaehlt' : ''}`);
    const kopf = el('div', 'kopfzeile');
    kopf.append(el('span', 'titel', `${f.kategorie} ${f.linie} → ${f.richtung}`),
                el('span', `klein ${f.verspaetung > 20 ? 'status-warn' : ''}`, `+${f.verspaetung} Min`));
    karte.append(kopf);
    karte.append(el('div', 'klein', `${tag(f.plan)} ${uhr(f.plan)} · gesehen: ${f.halt}`
                                    + (f.verspaetung > 20 ? '' : ' · bis 20 Min noch kein Anspruch')));
    karte.addEventListener('click', () => fahrtOeffnen(f.id));
    if (offen === f.id) karte.append(verlaufPanel());
    return karte;
  }

  async function fuerWenLaden() {
    try { fuerWen = (await holen(`/api/garantie/fahrt/personen?id=${encodeURIComponent(offen)}`)).personen; }
    catch (_) { fuerWen = []; }
  }

  async function fahrtOeffnen(id) {
    if (offen === id) { offen = null; verlauf = null; fuerWen = null; zeichnen(); return; }
    offen = id; verlauf = null; fuerWen = null; wahl = { start: null, ziel: null }; meldung = '';
    auswahl.clear();
    zeichnen();
    const personen = ansicht === 'alle' ? fuerWenLaden() : Promise.resolve();
    try { verlauf = await holen(`/api/garantie/fahrt?id=${encodeURIComponent(id)}`); }
    catch (e) { meldung = e.message; }
    await personen;
    // Geht die Fahrt nur für eine einzige Person, ist sie schon gewählt.
    if (fuerWen) { const moeglich = fuerWen.filter((p) => p.geht); if (moeglich.length === 1) auswahl.add(moeglich[0].id); }
    zeichnen();
  }

  function auslastung(z, kompakt = false) {
    const mini = el('div', 'g-mini');
    // Zwei Balken in Prozent, Blau = abgeschickt, aber noch ohne Bescheid: das Ticket (Geld im Monat gegen den Kartenpreis)
    // und der Monat (bestätigte Anträge gegen das Monatslimit).
    const prozent = (n, max) => Math.round(100 * n / Math.max(max, 1));
    const zeile = (name, n, offenN, max, absolut) => {
      const p0 = prozent(n, max), p1 = Math.min(prozent(n + offenN, max), 100) - Math.min(p0, 100);
      mini.append(el('span', 'klein', name), balken(n, offenN, max),
                  el('span', `klein ${n >= max ? 'status-fehler' : ''}`, kompakt ? absolut : `${p0} %${offenN ? ` +${Math.max(p1, 0)} %` : ''} (${absolut})`));
    };
    if (z.preis_cent) {
      // Über die Gültigkeit der Karte (nicht den Kalendermonat — sonst läuft das Geld der vorigen Karte in die neue):
      // bestätigtes Geld und, geschätzt mit 5 € je offenem Antrag, das noch wartende.
      const erst = Math.min(z.ticket_erstattet_cent || 0, z.preis_cent);
      const offenCent = Math.min(z.ticket_offen_cent || 0, z.preis_cent - erst);
      // Kompakt ohne Euro: in Anträgen zu 5 € gerechnet (aufgerundet), nur die Anzahl.
      if (kompakt) {
        const je = (c) => Math.ceil(c / 500);
        zeile('Ticket', erst, offenCent, z.preis_cent, `${je(erst)} von ${je(z.preis_cent)}`);
      } else zeile('Ticket', erst, offenCent, z.preis_cent, `${euro(erst)}${offenCent ? ` +${euro(offenCent)}` : ''} von ${euro(z.preis_cent)}`);
    }
    zeile(z.monat_name.slice(0, 3) + (z.mit_dticket ? ' D' : ''), z.gezaehlt, z.offen_monat || 0, z.grenze,
           kompakt ? `${z.gezaehlt} von ${z.grenze}` : `${z.gezaehlt}${z.offen_monat ? ` +${z.offen_monat}` : ''} von ${z.grenze}`);
    return mini;
  }

  // Übersicht: Für wen soll der Antrag vorbereitet werden? Je Person, ob es geht — und warum nicht.
  function personWahl(ziel) {
    ziel.append(el('div', 'g-frage', 'Für wen?'));
    if (!fuerWen) { ziel.append(el('div', 'klein', 'Friday prüft, für wen die Fahrt geht …')); return; }
    const zeile = (p) => {
      const z = el('label', `g-wahlzeile${p.geht ? '' : ' aus'}`);
      const haken = el('input');
      haken.type = 'checkbox';
      haken.disabled = !p.geht;
      haken.checked = auswahl.has(p.id);
      haken.addEventListener('change', () => { if (haken.checked) auswahl.add(p.id); else auswahl.delete(p.id); zeichnen(); });
      const text = el('div');
      text.append(el('b', '', p.id ? p.name : 'Ich'));
      const info = p.geht ? p.warnungen : p.gruende;
      if (p.zaehler) text.append(auslastung(p.zaehler, true));
      if (info.length) text.append(el('div', p.geht ? 'leise' : 'klein status-fehler', info.join(' · ')));
      z.append(haken, text);
      return z;
    };
    const geht = fuerWen.filter((p) => p.geht);
    const nicht = fuerWen.filter((p) => !p.geht);
    const liste = el('div', 'g-wahlliste');
    for (const p of geht) liste.append(zeile(p));
    ziel.append(liste);
    if (!geht.length) ziel.append(el('div', 'klein status-fehler', 'Für niemanden möglich.'));
    if (nicht.length) {
      const d = el('details', 'g-archiv');
      d.append(el('summary', 'klein', `Nicht möglich (${nicht.length})`));
      const l2 = el('div', 'g-wahlliste');
      for (const p of nicht) l2.append(zeile(p));
      d.append(l2);
      ziel.append(d);
    }
  }

  function verlaufPanel() {
    const panel = el('div', 'g-panel');
    panel.addEventListener('click', (e) => e.stopPropagation());
    if (!verlauf) { panel.append(el('div', 'klein', meldung || 'Friday holt den Verlauf …')); return panel; }
    panel.append(el('div', 'klein', ansicht === 'person' && profil ? `Tippe an, wo ${personName()} ein- und ausgestiegen ist.`
                                                                    : 'Tippe an, wo eingestiegen und wo ausgestiegen wurde.'));
    const tabelle = el('div', 'g-verlauf');
    verlauf.halte.forEach((h, i) => {
      const zeile = el('div', 'g-halt');
      if (wahl.start === i || wahl.ziel === i) zeile.classList.add('gewaehlt');
      const plan = h.an_plan || h.ab_plan;
      const v = h.faellt_aus ? 'fällt aus' : (h.verspaetung === null ? '–' : `+${h.verspaetung}`);
      zeile.append(el('span', 'g-zeit', uhr(plan)), el('span', 'g-name', h.name),
                   el('span', `g-plus ${h.verspaetung > 20 ? 'status-warn' : ''}`, v));
      const knoepfe = el('span', 'g-wahl');
      knoepfe.append(knopf(wahl.start === i ? 'Start ✓' : 'Start', 'leise-knopf', () => { wahl.start = i; if (wahl.ziel !== null && wahl.ziel <= i) wahl.ziel = null; zeichnen(); }),
                     knopf(wahl.ziel === i ? 'Ziel ✓' : 'Ziel', 'leise-knopf', () => { wahl.ziel = i; zeichnen(); }));
      zeile.append(knoepfe);
      tabelle.append(zeile);
    });
    panel.append(tabelle);
    const bereit = wahl.start !== null && wahl.ziel !== null && wahl.start < wahl.ziel;
    if (wahl.start !== null && wahl.ziel !== null && !bereit) panel.append(el('div', 'klein status-fehler', 'Das Ziel muss nach dem Start kommen.'));

    // Auf der Personenseite gilt der Antrag dieser Person; in der Übersicht wird gewählt, für wen (auch mehrere).
    const ziele = ansicht === 'person' ? [profil] : [...auswahl];
    if (ansicht !== 'person') personWahl(panel);
    const reihe = el('div', 'knoepfe');
    const text = ansicht === 'person'
      ? (profil ? `Antrag für ${personName()} vorbereiten` : 'Antrag vorbereiten')
      : (ziele.length > 1 ? `${ziele.length} Anträge vorbereiten` : 'Antrag vorbereiten');
    const los = knopf(text, '', async (b) => {
      b.disabled = true;
      const start = verlauf.halte[wahl.start].reference;
      const ziel = verlauf.halte[wahl.ziel].reference;
      const namen = Object.fromEntries((fuerWen || []).map((p) => [p.id, p.id ? p.name : 'Ich']));
      const texte = [];
      let neu = 0;
      for (const [i, pid] of ziele.entries()) {
        meldung = `Friday sucht die Fahrt im Formular der ÜSTRA und prüft die Bedingungen …${ziele.length > 1 ? ` (${i + 1} von ${ziele.length})` : ''}`;
        zeichnen();
        try {
          const d = await senden('/api/garantie/vorbereiten', { fahrt: offen, start, ziel, profil: pid });
          texte.push(ziele.length > 1 || ansicht !== 'person' ? `${namen[pid] || 'Person'}: ${d.text}` : d.text);
          if (d.ok) neu += 1;
        } catch (e) { texte.push(`${namen[pid] || 'Person'}: ${e.message}`); }
      }
      meldung = texte.join('\n\n');
      auswahl.clear();
      if (ansicht !== 'person') await fuerWenLaden();
      if (neu) { await laden(); return; }
      zeichnen();
    });
    los.disabled = !bereit || !ziele.length;
    reihe.append(los);
    panel.append(reihe);
    if (bereit && !ziele.length && ansicht !== 'person') panel.append(el('div', 'leise', 'Wähle oben, für wen.'));
    if (meldung) panel.append(el('div', 'ausgabe', meldung));
    return panel;
  }

  function suchBlock() {
    const d = daten();
    const b = block('Verspätete Fahrten');
    b.append(el('p', 'leise', `Bus, Stadtbahn und S-Bahn in ${d.orte.join(', ')} — findet sich dort nichts `
                              + 'über 20 Minuten, auch in den Nachbarorten und im ganzen Netz. Die Fahrten sind für alle '
                              + 'dieselben; nach Start und Ziel wählst du, für wen.'));
    const reihe = el('div', 'knoepfe');
    const such = knopf('Suchen', '', async (k) => {
      k.disabled = true; k.textContent = 'Friday sucht … (bis zu einer halben Minute)';
      try { gesucht = { ...(await senden('/api/garantie/suchen')), neu: true }; }
      catch (e) { gesucht = { fahrten: [], bereich: '', fehler: e.message }; }
      await laden();
    });
    such.disabled = !d.aktiv;
    reihe.append(such);
    b.append(reihe);

    if (meldungenStand === null) holen('/api/einstellungen/benachrichtigungen').then((x) => { meldungenLesen(x); zeichnen(); }).catch(() => {});
    else {
      b.append(el('div', 'klein', 'Meldung aufs Handy bei Verspätung:'));
      const m = el('div', 'chips');
      for (const [wert, text] of [['s5', 'Nur S5'], ['alle', 'Alle Linien'], ['aus', 'Aus']]) {
        m.append(knopf(text, meldungenStand === wert ? 'aktiv' : '', () => meldungenSetzen(wert)));
      }
      b.append(m);
    }

    // Womit gefahren? Zählt über beide Listen, damit man sieht, was der Filter verbirgt.
    const alleFahrten = [...((gesucht || {}).fahrten || []), ...d.reisen];
    const anzahl = { alle: alleFahrten.length, s5: alleFahrten.filter(istS5).length, bahn: alleFahrten.filter(istBahn).length };
    anzahl.bus = anzahl.alle - anzahl.bahn;
    if (alleFahrten.length) {
      const chips = el('div', 'chips');
      for (const [wert, text] of [['s5', 'S5'], ['alle', 'Bus und Bahn'], ['bus', 'Nur Bus'], ['bahn', 'Nur Bahn']]) {
        chips.append(knopf(`${text} (${anzahl[wert]})`, fahrzeug === wert ? 'aktiv' : '', () => fahrzeugSetzen(wert)));
      }
      b.append(chips);
    }

    if (gesucht) {
      if (gesucht.neu) { seiten.gesucht = 0; gesucht.neu = false; }
      if (gesucht.fehler) b.append(el('div', 'klein status-fehler', gesucht.fehler));
      if (gesucht.bereich && gesucht.bereich !== 'deine Orte') {
        b.append(el('p', 'leise', `In deinen Orten war nichts über 20 Minuten — gesucht bis: ${gesucht.bereich}.`));
      }
      b.append(el('h4', '', 'Gerade verspätet'));
      const jetzt = s5Zuerst(gesucht.fahrten.filter(passtFahrzeug));
      if (!gesucht.fahrten.length) b.append(el('p', 'leer-klein', 'Gerade keine Verspätung ab 10 Minuten.'));
      else if (!jetzt.length) b.append(el('p', 'leer-klein', `Gerade keine Verspätung ${fahrzeug === 's5' ? 'bei der S5' : fahrzeug === 'bahn' ? 'bei der Bahn' : 'beim Bus'}.`));
      blaettern(b, 'gesucht', jetzt, fahrtKarte);
    }
    const schon = new Set(((gesucht || {}).fahrten || []).map((f) => f.id));
    const gemerkt = s5Zuerst(d.reisen.filter((f) => !schon.has(f.id) && passtFahrzeug(f)));
    if (gemerkt.length) {
      b.append(el('h4', '', 'Gemerkt (ab 20 Min, letzte 48 Stunden)'));
      blaettern(b, 'gemerkt', gemerkt, fahrtKarte);
    }
    return b;
  }

  /* ---------- Anträge ---------- */

  // Alle Angaben in der Reihenfolge des ÜSTRA-Formulars, jede zum Kopieren — für Fahrkarten, bei denen Friday nicht selbst abschickt.
  function abtippenAnzeigen(box, d) {
    if (d.warnungen && d.warnungen.length) {
      const w = el('div', 'g-warnung');
      w.append(el('b', '', 'Erst klären — so gehört der Antrag nicht raus:'));
      for (const x of d.warnungen) w.append(el('div', '', x));
      box.append(w);
    }
    box.append(el('p', 'ausgabe', `Angaben für ${d.person === 'Ich' ? 'dich' : d.person}, sortiert wie im Formular der ÜSTRA. `
      + 'Öffne das Formular, tippe die Angaben ab (jede lässt sich kopieren) und tippe danach hier auf „Selbst eingereicht“.'));
    const link = el('a', 'ar-knopf', 'Formular der ÜSTRA öffnen');
    link.href = 'https://www.uestra.de/service-und-mobilitaet/uestra-garantie/';
    link.target = '_blank';
    link.rel = 'noopener';
    box.append(link);
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
            try { await navigator.clipboard.writeText(wert); k.textContent = '✓ kopiert'; }
            catch (_) { k.textContent = 'nicht möglich'; }
            setTimeout(() => { k.textContent = 'Kopieren'; }, 1500);
          }));
        }
        teil.append(zeile);
      }
      box.append(teil);
    }
  }

  // Ein Antrag. `mitPerson`: in der Übersicht, wo Anträge verschiedener Personen untereinander stehen — dann steht die
  // Person oben dran, und Abschicken nennt sie in der Rückfrage.
  function antragKarte(e, mitPerson = false) {
    const d = daten();
    const pid = Number(e.profil || 0);
    const wer = mitPerson ? (pid ? e.person : 'Ich') : (profil ? personName() : 'Ich');
    const farbe = { bereit: 'status-ok', abgeschickt: 'status-ok', genehmigt: 'status-ok', eingegangen: 'status-ok',
                    wartet: '', 'wird nachgeprüft': '' };
    const karte = el('div', 'eintrag');
    if (mitPerson) {
      const tagP = knopf(wer, 'g-person-tag', () => personOeffnen(pid));
      tagP.title = 'Person öffnen';
      karte.append(tagP);
    }
    const kopf = el('div', 'kopfzeile');
    kopf.append(el('span', 'titel', `#${e.id} Linie ${e.linie}: ${e.start} → ${e.ziel}`),
                el('span', `klein ${farbe[e.stand] ?? 'status-fehler'}`,
                   e.stand === 'wartet' ? `wartet bis ${uhr(e.faellig_ab)}` : e.stand));
    karte.append(kopf);
    karte.append(el('div', 'klein', `${tag(e.ab)} ${uhr(e.ab)}–${uhr(e.an)} · `
      + (e.verspaetung === null ? 'noch keine Messung' : `${e.verspaetung} Min später (${e.quelle})`)));
    if (e.frist_tage !== undefined && e.frist_tage !== null) {
      karte.append(el('div', `klein ${e.frist_tage <= 3 ? 'status-warn' : ''}`,
        e.frist_tage <= 0 ? 'Frist endet heute' : `Frist: noch ${e.frist_tage} Tag${e.frist_tage === 1 ? '' : 'e'} (bis ${datum(e.frist_bis)})`));
    }
    for (const h of [...(e.hindernisse || []), ...(e.hinweise || [])]) karte.append(el('div', 'leise', h));
    if (e.fehlt && e.fehlt.length) karte.append(el('div', 'leise', `Fehlt noch: ${e.fehlt.join(', ')}`));
    const reihe = el('div', 'knoepfe');
    const status = (s) => async () => {
      try { await senden(`/api/garantie/antrag/${e.id}`, { status: s }); laden(); }
      catch (err) { karte.append(el('div', 'klein status-fehler', err.message)); }
    };
    if (e.stand === 'bereit' && d.abschicken) {
      reihe.append(knopf('Abschicken', '', async (k) => {
        k.disabled = true;
        const info = el('div', 'ausgabe', 'Friday prüft alle Angaben beim Formular …');
        karte.append(info);
        try {
          const p = await holen(`/api/garantie/antrag/${e.id}/pruefen`);
          if (!p.ok) { info.textContent = 'Nicht abschickbar: ' + p.probleme.join(' '); k.disabled = false; return; }
          if (!confirm(`Antrag #${e.id} für ${pid ? wer.toUpperCase() : 'DICH'} jetzt an die ÜSTRA schicken?\n\n` + p.punkte.join('\n'))) {
            info.remove(); k.disabled = false; return;
          }
          info.textContent = 'Wird abgeschickt …';
          // Die Person, die diese Karte zeigt — der Server vergleicht sie mit der des Antrags.
          const r = await senden(`/api/garantie/antrag/${e.id}/abschicken`, { bestaetigt: true, profil: mitPerson ? pid : profil });
          info.textContent = r.text;
          if (r.ok) setTimeout(laden, 1500); else k.disabled = false;
        } catch (err) { info.textContent = err.message; k.disabled = false; }
      }));
    }
    const abtippen = el('div');
    if (e.stand === 'bereit') {
      reihe.append(knopf('Zum Abtippen', 'leise-knopf', async () => {
        if (abtippen.children.length) { abtippen.textContent = ''; return; }
        try { abtippenAnzeigen(abtippen, await holen(`/api/garantie/antrag/${e.id}/abtippen`)); }
        catch (err) { abtippen.append(el('div', 'klein status-fehler', err.message)); }
      }));
      reihe.append(knopf('Selbst eingereicht', 'leise-knopf', status('abgeschickt')));
    }
    if (e.vorgang) karte.append(el('div', 'klein', `Vorgangsnummer ${e.vorgang}`));
    if (e.gesendet) {
      const eb = el('details', 'g-bestaetigung');
      const wann = e.gesendet.zeit ? `${tag(e.gesendet.zeit)} ${uhr(e.gesendet.zeit)}` : '';
      eb.append(el('summary', 'klein status-ok',
        `✓ Eingangsbestätigung${e.gesendet.id ? ` Nr. ${e.gesendet.id}` : ''} · ${wann}`
        + (e.entscheidung && e.entscheidung !== 'eingang' ? '' : ' · Mail der ÜSTRA steht noch aus')));
      eb.append(el('div', 'leise', e.gesendet.bestaetigung
        || (e.gesendet.unklar ? 'Die ÜSTRA hat beim Abschicken nicht klar geantwortet — die Eingangsmail zeigt, ob der Antrag ankam.'
                              : 'Das Formular hat den Antrag angenommen und diese Vorgangsnummer vergeben.')));
      karte.append(eb);
    }
    if (e.status === 'abgeschickt') {
      if (!e.vorgang) reihe.append(knopf('Zurück auf offen', 'leise-knopf', status('offen')));
    } else {
      reihe.append(knopf('Entwurf löschen', 'weg', async (k) => {
        if (!confirm(`Entwurf #${e.id} (Linie ${e.linie}${mitPerson ? `, ${wer}` : ''}) löschen?`)) return;
        k.disabled = true;
        try { await antwort(await fetch(`/api/garantie/antrag/${e.id}`, { method: 'DELETE' })); laden(); }
        catch (err) { k.disabled = false; karte.append(el('div', 'klein status-fehler', err.message)); }
      }));
    }
    karte.append(reihe, abtippen);
    return karte;
  }

  function antragsBlock(liste) {
    const b = block('Anträge');
    if (!liste.length) { b.append(el('p', 'leer-klein', 'Noch keiner vorbereitet. Such unten eine verspätete Fahrt aus.')); return b; }
    // Was Eike jetzt tun kann, nach oben: bereit, dann was noch fehlt, dann der Rest (neueste zuerst).
    const rang = { bereit: 0, 'unvollständig': 1, wartet: 2, 'wird nachgeprüft': 2, 'Rückfrage': 1 };
    const sortiert = [...liste].sort((x, y) => (rang[x.stand] ?? 5) - (rang[y.stand] ?? 5) || y.id - x.id);
    blaettern(b, 'antraege', sortiert, (e) => antragKarte(e));
    if (!stand.abschicken) b.append(el('p', 'leise', 'Abschicken durch Friday: in den Freigaben bei „ÜSTRA-Garantie“ „Anträge abschicken“ einschalten.'));
    b.append(el('p', 'leise', '„Selbst eingereicht“ schickt nichts ab — es zählt nur mit, was du auf der Webseite eingereicht hast.'));
    return b;
  }

  /* ---------- Ticket ---------- */

  // Ein Ticket hochladen — für die Person der Seite oder, in der Übersicht, für die, deren Knopf es ist.
  function hochladen(pid, fertig) {
    const datei = el('input');
    datei.type = 'file';
    datei.multiple = true;
    datei.accept = 'application/pdf,image/*';
    datei.addEventListener('change', async () => {
      if (!datei.files.length) return;
      const texte = [];
      fertig('Friday liest das Ticket …', false);
      for (const f of datei.files) {
        const form = new FormData();
        form.append('datei', f);
        try { texte.push((await antwort(await fetch(`/api/garantie/ticket?profil=${pid}`, { method: 'POST', body: form }))).text); }
        catch (e) { texte.push(`${f.name}: ${e.message}`); }
      }
      fertig(texte.join('\n'), true);
    });
    return datei;
  }

  function ticketBlock(tickets, fahrkarten) {
    const b = block(profil ? `Ticket von ${personName()}` : 'Mein Ticket');
    // Das Ticket mit Gültigkeit (nicht ein später hochgeladenes Foto der Kundenkarte).
    const t = stand.ticket || tickets[0];
    const heute = new Date().toISOString().slice(0, 10);
    const a = stand.angaben || {};
    if (!t) b.append(el('p', 'leise', profil ? `Noch keins. Lade das Ticket von ${personName()} hoch (der Screenshot oder ein Foto der Papierkarte) — Friday liest Art, Gültigkeit, Namen und Nummern selbst aus.`
                                       : 'Noch keins. Lade dein Ticket hoch — Friday liest Art und Gültigkeit selbst aus.'));
    else {
      const abgelaufen = t.gueltig_bis && t.gueltig_bis < heute;
      b.append(el('p', abgelaufen ? 'status-fehler' : 'leise',
        `${t.bezeichnung || t.art || t.name}${t.form === 'papier' ? ' (Papier)' : ''} · gültig ${datum(t.gueltig_von)} bis ${datum(t.gueltig_bis)}`
        + (abgelaufen ? ' — abgelaufen, lade das neue hoch.' : '')
        + (t.herausgeber ? ` · ausgegeben von ${t.herausgeber}` : '')));
      const extra = [];
      const kk = t.kundenkarte || a.kundenkarte;
      if (kk) extra.push(`Kundenkarte ${kk}`);
      if (t.ticketnummer) extra.push(`Ticketnr. ${t.ticketnummer}${t.nummer_gedruckt && t.nummer_gedruckt !== t.ticketnummer ? ` (gedruckt: ${t.nummer_gedruckt})` : ''}`);
      if (t.preis) extra.push(`Preis ${euro(t.preis * 100)}`);
      if (extra.length) b.append(el('div', 'leise', extra.join(' · ')));
      if (t.zweifel) b.append(el('div', 'leise', `Friday war unsicher: ${t.zweifel}`));
      if (t.inhaber) b.append(el('div', 'leise', `Auf dem Ticket steht: ${t.inhaber}`));
      if (t.kundenkarte && a.kundenkarte && t.kundenkarte !== a.kundenkarte) {
        b.append(el('div', 'klein status-fehler', `Auf dem Ticket steht Kundenkarte ${t.kundenkarte}, gespeichert ist ${a.kundenkarte} — eine davon ist falsch. So schickt Friday nicht ab.`));
      }
      const ansehen = el('a', 'g-link', 'Ticket ansehen');
      ansehen.href = `/api/garantie/ticket/${encodeURIComponent(t.id)}/datei`;
      ansehen.target = '_blank';
      ansehen.rel = 'noopener';
      b.append(ansehen);
    }
    const beschriftung = el('label', 'hochladen');
    const datei = hochladen(profil, (text, fertig) => { ticketText = text; if (fertig) laden(); else zeichnen(); });
    datei.hidden = true;
    beschriftung.append(datei, el('span', '', `＋ Ticket für diesen Monat${profil ? ' von ' + personName() : ''} hochladen — Foto, Screenshot oder PDF; `
      + 'bei der Papierkarte Wertmarke und Kundenkarte'));
    b.append(beschriftung);
    if (ticketText) b.append(el('div', 'ausgabe', ticketText));

    // Nur falls Friday etwas falsch gelesen hat — oder bei Papier, wo Eike die Nummern einmal vergleicht.
    if (t) {
      const jugend = JUGEND.test(t.bezeichnung || '');
      // Wie ticket.muss_verglichen_werden: Papier, Handschrift oder unklare Form (auch alles vor dem 21.09.2026 Gelesene).
      const vergleichen = jugend && !t.nummern_geprueft && (t.form !== 'digital' || t.kundenkarte_handschrift);
      const korr = el('details');
      // Bei Jugend-/Ausbildungsnetzkarten verlangt das Formular Nummern, die Friday oft nicht lesen kann: gleich offen.
      korr.open = (jugend && (!(t.kundenkarte || a.kundenkarte) || !t.ticketnummer)) || vergleichen;
      korr.append(el('summary', `klein${vergleichen ? ' status-warn' : ''}`,
        vergleichen ? 'Nummern mit dem Ticket vergleichen' : 'Erkanntes korrigieren und ergänzen'));
      if (vergleichen) {
        korr.append(el('p', 'g-hinweisband', (t.form === 'papier' ? 'Papierticket: ' : (t.form ? '' : 'Ob App oder Papier, ist unklar. '))
          + 'Vergleiche Kundenkarten- und Ticketnummer Ziffer für Ziffer mit dem Ticket, berichtige sie falls nötig, stell die Form ein '
          + 'und setz dann das Häkchen. Vorher schickt Friday nicht ab. Auf der Papierkarte steht die Ticketnummer ganz unten '
          + '(Zahlengruppen vor Datum und Uhrzeit).'));
      }
      if ((t.mime || '').startsWith('image/')) {
        const bild = el('img', 'g-ticketbild');
        bild.src = `/api/garantie/ticket/${encodeURIComponent(t.id)}/datei`;
        bild.alt = 'Das hochgeladene Ticket';
        bild.loading = 'lazy';
        bild.addEventListener('error', () => bild.remove());
        korr.append(bild);
      }
      const raster = el('div', 'g-raster');
      const feld = (text, f) => { const k = el('label', 'g-feld'); k.append(el('span', 'klein', text), f); raster.append(k); return f; };
      const art = el('select');
      for (const o of fahrkarten) { const opt = el('option', '', o); opt.value = o; art.append(opt); }
      art.value = t.art || 'Sonstige';
      const form = el('select');
      for (const [wert, text] of [['', '— unklar —'], ['digital', 'Digital (App, PDF)'], ['papier', 'Papier']]) {
        const opt = el('option', '', text); opt.value = wert; form.append(opt);
      }
      form.value = t.form || '';
      const von = el('input'); von.type = 'date'; von.value = t.gueltig_von || '';
      const bis = el('input'); bis.type = 'date'; bis.value = t.gueltig_bis || '';
      const inhaber = el('input'); inhaber.type = 'text'; inhaber.maxLength = 80; inhaber.value = t.inhaber || '';
      const karte = el('input'); karte.type = 'text'; karte.inputMode = 'numeric'; karte.maxLength = 6; karte.value = t.kundenkarte || '';
      const nummer = el('input'); nummer.type = 'text'; nummer.placeholder = 'z. B. 2608127024'; nummer.maxLength = 40; nummer.value = t.ticketnummer || '';
      const preis = el('input'); preis.type = 'number'; preis.step = '0.01'; preis.min = '0'; preis.value = t.preis || '';
      feld('Art', art); feld('Form', form); feld('Gültig von', von); feld('Gültig bis', bis);
      feld('Name auf dem Ticket', inhaber);
      feld(`Kundenkartennummer (6 Ziffern, steht auf der Kundenkarte${a.kundenkarte ? `; gespeichert: ${a.kundenkarte}` : ''})`, karte);
      feld('Ticketnummer (App: oben hinter dem Datum; Papier: die Zahlengruppen ganz unten auf der Wertmarke, ohne Datum und Uhrzeit)', nummer);
      feld('Preis der Karte in € (Monatskarten: mehr als den Preis erstattet die ÜSTRA je Monat nicht)', preis);
      korr.append(raster);
      let haken = null;
      if (jugend) {
        const zust = el('label', 'check klein g-check');
        haken = el('input'); haken.type = 'checkbox'; haken.checked = !!t.nummern_geprueft;
        zust.append(haken, document.createTextNode(' Nummern verglichen: Kundenkarten- und Ticketnummer stimmen Ziffer für Ziffer mit dem Ticket überein.'));
        korr.append(zust);
      }
      if (t.pruefung_quelle === 'person') {
        korr.append(el('div', 'klein', `Nummern von der Person bestätigt${t.person_korrigiert ? ' (sie hat eine Nummer korrigiert)' : ''}.`));
      }
      const info = el('div', 'klein');
      const reihe = el('div', 'knoepfe');
      reihe.append(knopf('Übernehmen', '', async () => {
        try {
          const neu = { art: art.value, form: form.value, gueltig_von: von.value, gueltig_bis: bis.value, inhaber: inhaber.value.trim(),
            kundenkarte: karte.value.trim(), ticketnummer: nummer.value.trim(), preis: preis.value === '' ? 0 : Number(preis.value) };
          if (haken) neu.nummern_geprueft = haken.checked;
          await senden(`/api/garantie/ticket/${t.id}`, neu);
          info.textContent = 'Übernommen.'; laden();
        }
        catch (e) { info.textContent = e.message; }
      }));
      korr.append(reihe, info);
      b.append(korr);
    }
    const frueher = tickets.filter((x) => !t || x.id !== t.id);
    if (frueher.length) {
      b.append(el('div', 'leise', 'Außerdem: ' + frueher.map((x) => (x.ist_kundenkarte ? `Kundenkarte${x.kundenkarte ? ' ' + x.kundenkarte : ''}`
        : `${x.art || x.name} bis ${datum(x.gueltig_bis)}`)).join(' · ')));
    }
    return b;
  }

  function angabenBlock(a) {
    // Einmal eintragen genügt; ist alles da, bleibt der Block zugeklappt.
    const kasten = el('details', 'block');
    kasten.open = !!(a.fehlt && a.fehlt.length);
    kasten.append(el('summary', '', a.fehlt && a.fehlt.length ? `Meine Angaben — fehlt: ${a.fehlt.join(', ')}` : 'Meine Angaben ✓'));
    const felder = {};
    const raster = el('div', 'g-raster');
    const feld = (name, text, typ = 'text', optionen = null) => {
      const k = el('label', 'g-feld');
      k.append(el('span', 'klein', text));
      let f;
      if (optionen) {
        f = el('select');
        for (const o of ['', ...optionen]) { const opt = el('option', '', o || '— wählen —'); opt.value = o; f.append(opt); }
      } else { f = el('input'); f.type = typ; }
      f.value = a[name] || '';
      felder[name] = f;
      k.append(f);
      raster.append(k);
    };
    feld('anrede', 'Anrede', 'text', ['Herr', 'Frau', 'Divers']);
    feld('vorname', 'Vorname');
    feld('nachname', 'Nachname');
    feld('email', 'E-Mail', 'email');
    feld('strasse', 'Straße und Hausnummer');
    feld('plz', 'PLZ');
    feld('ort', 'Ort');
    feld('abo_nummer', 'Abo-Kundennummer (bei Abo-Tickets)');
    feld('kundenkarte', 'Kundenkartennummer (6 Ziffern, bei Jugend-/Ausbildungsnetzkarte)');
    feld('kontoinhaber', 'Kontoinhaber');
    feld('iban', 'IBAN');
    // Die gespeicherte IBAN kommt nie zurück in den Browser — nur verdeckt als Hinweis.
    felder.iban.value = '';
    felder.iban.placeholder = a.iban_verdeckt || 'DE…';
    felder.iban.autocomplete = 'off';
    kasten.append(raster, el('p', 'leise', 'Fahrkarte und Gültigkeit kommen aus deinem Ticket. Gespeichert auf dem Server, jede Nacht gesichert.'));
    const info = el('div', 'klein');
    const reihe = el('div', 'knoepfe');
    reihe.append(knopf('Speichern', '', async () => {
      const daten2 = {};
      for (const [k, f] of Object.entries(felder)) daten2[k] = f.value.trim();
      if (!daten2.iban) delete daten2.iban;                   // leer = die gespeicherte behalten
      try { await senden('/api/garantie/angaben', daten2); info.textContent = 'Gespeichert.'; laden(); }
      catch (e) { info.textContent = e.message; }
    }));
    kasten.append(reihe, info);
    return kasten;
  }

  /* ---------- Personen: Formular und Abrechnung ---------- */

  // Ein Freund: alle Angaben, die ins Formular gehen — dazu Auszahlung, Anteil und die Zustimmung.
  function profilFormular(a) {
    const neu = !a;
    const kasten = el('div', 'g-panel');
    kasten.append(el('h3', '', neu ? 'Neue Person' : `Angaben von ${a.name}`));
    kasten.append(el('p', 'leise', 'Alles, was in den Antrag kommt, gehört der Person selbst — nicht dir: Name, Adresse und '
      + 'E-Mail-Adresse. Trag nur ein, was sie dir ausdrücklich gegeben hat.'));
    const felder = {};
    const raster = el('div', 'g-raster');
    const feld = (name, text, typ = 'text', optionen = null, hilfe = '') => {
      const k = el('label', 'g-feld');
      k.append(el('span', 'klein', text));
      let f;
      if (optionen) {
        f = el('select');
        for (const [wert, beschriftung] of optionen) { const opt = el('option', '', beschriftung); opt.value = wert; f.append(opt); }
      } else { f = el('input'); f.type = typ; }
      f.value = neu ? (name === 'auszahlung' ? 'ich' : '') : (a[name] ?? '');
      felder[name] = f;
      k.append(f);
      if (hilfe) k.append(el('span', 'leise', hilfe));
      raster.append(k);
      return f;
    };
    feld('name', 'Name im Profil (nur für dich)');
    feld('anrede', 'Anrede', 'text', [['', '— wählen —'], ['Herr', 'Herr'], ['Frau', 'Frau'], ['Divers', 'Divers']]);
    feld('vorname', 'Vorname');
    feld('nachname', 'Nachname');
    const mail = feld('email', 'E-Mail-Adresse für die ÜSTRA', 'email', null,
      'Jede Person braucht ihre eigene Adresse. Mit Gmail geht dafür dein eigenes Postfach mit einem Plus-Alias '
      + '(deinname+lea@gmail.com): Die Bescheide kommen bei dir an, und Friday sieht an der Adresse, wessen Bescheid es ist.');
    if (stand.email_basis) {
      const vorschlag = knopf('Alias vorschlagen', 'leise-knopf', () => {
        mail.value = aliasFuer(felder.name.value || felder.vorname.value);
      });
      mail.parentNode && mail.parentNode.append(vorschlag);
    }
    feld('strasse', 'Straße und Hausnummer');
    feld('plz', 'PLZ');
    feld('ort', 'Ort');
    feld('abo_nummer', 'Abo-Kundennummer (bei Abo-Tickets)');
    feld('kundenkarte', 'Kundenkartennummer (6 Ziffern, bei Jugend-/Ausbildungsnetzkarte)');
    const aus = feld('auszahlung', 'Wohin überweist die ÜSTRA?', 'text',
      [['ich', 'Auf mein Konto — ich zahle den Teil aus'], ['eigenes', 'Auf das Konto der Person — sie gibt mir meinen Anteil']]);
    const konto = el('div', 'g-raster');
    const kontoFeld = (name, text) => {
      const k = el('label', 'g-feld');
      k.append(el('span', 'klein', text));
      const f = el('input'); f.type = 'text'; f.value = neu ? '' : (a[name] ?? ''); felder[name] = f;
      k.append(f); konto.append(k); return f;
    };
    kontoFeld('kontoinhaber', 'Kontoinhaber der Person');
    const iban = kontoFeld('iban', 'IBAN der Person');
    iban.value = '';
    iban.placeholder = neu ? 'DE…' : (a.iban_verdeckt || 'DE…');
    iban.autocomplete = 'off';
    const kontoZeigen = () => { konto.hidden = aus.value !== 'eigenes'; };
    aus.addEventListener('change', kontoZeigen);
    kontoZeigen();
    feld('anteil', 'Mein Anteil am Geld (in %)', 'number');
    const notiz = el('textarea');
    notiz.rows = 2; notiz.maxLength = 500; notiz.value = neu ? '' : (a.notiz || '');
    notiz.placeholder = 'Notiz, z. B. wie ihr euch geeinigt habt';
    const zust = el('label', 'check klein');
    const haken = el('input'); haken.type = 'checkbox'; haken.checked = neu ? false : !!a.vollmacht;
    zust.append(haken, document.createTextNode(' Die Person hat zugestimmt, dass ich für sie einreiche, ihre Daten hier speichere und dass Google Gemini ihr Ticket '
      + 'ausliest. Ohne das Häkchen schickt Friday nichts ab.'));
    const info = el('div', 'klein');
    const reihe = el('div', 'knoepfe');
    reihe.append(knopf('Speichern', '', async () => {
      const d = {};
      for (const [k, f] of Object.entries(felder)) d[k] = k === 'anteil' ? (f.value === '' ? 0 : Number(f.value)) : f.value.trim();
      if (!d.iban) delete d.iban;                       // leer = die gespeicherte behalten
      d.vollmacht = haken.checked;
      d.notiz = notiz.value;
      try {
        if (neu) {
          const r = await senden('/api/garantie/profile', d);
          personOeffnen(r.id);
          return;
        }
        await stellen(`/api/garantie/profile/${a.id}`, d);
        profilForm = null;
        await laden();
      } catch (e) { info.textContent = e.message; }
    }));
    if (!neu) {
      reihe.append(knopf(a.archiviert ? 'Zurückholen' : 'Archivieren', '', async () => {
        try { await stellen(`/api/garantie/profile/${a.id}`, { archiviert: !a.archiviert }); await laden(); }
        catch (e) { info.textContent = e.message; }
      }));
      reihe.append(knopf('Profil löschen', 'weg', (k) => {
        if (!k.classList.contains('sicher')) {                    // erst fragen, dann löschen
          k.classList.add('sicher'); k.textContent = 'Wirklich löschen? Nochmal tippen';
          setTimeout(() => { k.classList.remove('sicher'); k.textContent = 'Profil löschen'; }, 5000);
          return;
        }
        fetch(`/api/garantie/profile/${a.id}`, { method: 'DELETE' }).then(antwort).then(zurUebersicht)
          .catch((e) => { info.textContent = e.message; });
      }));
    }
    kasten.append(raster, konto, notiz, zust, reihe, info);
    kasten.append(el('p', 'leise', 'Fahrkarte und Gültigkeit kommen aus dem hochgeladenen Ticket. Gespeichert auf dem Server '
      + '(Rechte 600), jede Nacht gesichert. Die IBAN kommt nie zurück in den Browser.'));
    return kasten;
  }

  function profilAngabenBlock(a) {
    const kasten = el('details', 'block');
    kasten.open = !!(a.fehlt && a.fehlt.length) || profilForm === a.id || !a.vollmacht;
    kasten.append(el('summary', '', a.fehlt && a.fehlt.length ? `Angaben von ${a.name} — fehlt: ${a.fehlt.join(', ')}`
                                    : (a.vollmacht ? `Angaben von ${a.name} ✓` : `Angaben von ${a.name} — Zustimmung fehlt`)));
    kasten.append(profilFormular(a));
    return kasten;
  }

  // Was mit einem Freund abzurechnen ist: genehmigte Fälle, dein Anteil, wer wem noch etwas schuldet.
  function abrechnungBlock(r) {
    const b = block(`Abrechnung mit ${personName()}`);
    if (!r.anzahl) {
      b.append(el('p', 'leer-klein', 'Noch nichts genehmigt. Sobald die ÜSTRA zusagt, steht hier, wer wie viel bekommt.'));
      return b;
    }
    const kacheln = el('div', 'g-kacheln');
    const kachel = (wert, text) => { const k = el('div', 'g-kachel'); k.append(el('div', 'g-wert', wert), el('div', 'klein', text)); kacheln.append(k); };
    kachel(euro(r.summe_cent), `genehmigt (${r.anzahl})`);
    kachel(euro(r.mein_cent), `dein Anteil (${String(r.anteil).replace('.', ',')} %)`);
    kachel(euro(r.freund_cent), `für ${personName()}`);
    b.append(kacheln);
    b.append(el('p', r.offen_cent ? 'status-warn' : 'status-ok', r.offen_cent
      ? (r.ich_zahle_aus ? `Du schuldest ${personName()} noch ${euro(r.offen_cent)}.` : `${personName()} schuldet dir noch ${euro(r.offen_cent)}.`)
      : 'Alles verrechnet.'));
    b.append(el('p', 'leise', r.ich_zahle_aus ? 'Die ÜSTRA überweist auf dein Konto — du zahlst den Teil der Person aus.'
                                              : 'Die ÜSTRA überweist auf das Konto der Person — sie gibt dir deinen Anteil ab.'));
    for (const z of r.zeilen) {
      const karte = el('div', `eintrag${z.erledigt ? ' ueberholt' : ''}`);
      const kopf = el('div', 'kopfzeile');
      kopf.append(el('span', 'titel', `${z.fahrtdatum ? datum(z.fahrtdatum) : ''} Linie ${z.linie || '?'}${z.start ? ': ' + z.start : ''}${z.ziel ? ' → ' + z.ziel : ''}`),
                  el('span', 'g-plus-euro', euro(z.cent)));
      karte.append(kopf);
      karte.append(el('div', 'klein', `Dein Anteil ${euro(z.mein_cent)} · ${personName()} ${euro(z.freund_cent)} · Fall ${z.vorgang}`));
      const reihe = el('div', 'knoepfe');
      reihe.append(knopf(z.erledigt ? '✓ verrechnet — zurücknehmen' : 'Als verrechnet markieren', z.erledigt ? 'leise-knopf' : '', async () => {
        try { await senden(`/api/garantie/profile/${profil}/erledigt`, { vorgang: z.vorgang, erledigt: !z.erledigt }); laden(); }
        catch (e) { karte.append(el('div', 'klein status-fehler', e.message)); }
      }));
      karte.append(reihe);
      b.append(karte);
    }
    return b;
  }

  /* ---------- Übersicht ---------- */

  const RANG = { frei: 0, heute_voll: 1, fehlt: 2, wartet: 3, voll: 4, archiviert: 5 };
  const FILTER = {
    alle: ['Alle', () => true],
    frei: ['Noch frei', (p) => p.ampel === 'frei' || p.ampel === 'heute_voll'],
    fehlt: ['Fehlt was', (p) => p.ampel === 'fehlt'],
    voll: ['Voll', (p) => p.ampel === 'voll' || p.ampel === 'wartet'],
  };

  // Kurz, was die Person gerade aufhält — für die Marke rechts in ihrer Zeile.
  function marke(p) {
    const z = p.zaehler;
    if (p.ampel === 'voll') return (z.preis_rest_cent !== null && z.preis_rest_cent <= 0) ? 'Kartenpreis erreicht' : `${z.monat_name} voll`;
    if (p.ampel === 'wartet') return 'Rest wartet auf Bescheid';
    if (p.ampel === 'fehlt') {
      const g = p.gruende[0] || '';
      if (/^Ticket für/.test(g)) return 'Ticket fehlt';
      if (/^Angaben/.test(g)) return 'Angaben fehlen';
      if (/Nummern vergleichen/.test(g)) return 'Nummern prüfen';
      return g.length > 22 ? 'prüfen' : g;
    }
    return `${euro(z.monat_erstattet_cent || 0)} im ${z.monat_name} erstattet`;
  }

  function personZeile(p) {
    const z = p.zaehler;
    const karte = el('div', 'eintrag klickbar g-person');
    const kopf = el('div', 'kopfzeile');
    const titel = el('span', 'titel');
    titel.append(el('span', `g-punkt g-${p.ampel}`), document.createTextNode(p.ich ? 'Ich' : p.name));
    kopf.append(titel, el('span', `g-marke g-${p.ampel}`, marke(p)));
    karte.append(kopf);
    const t = p.ticket;
    karte.append(el('div', 'klein', t
      ? `${t.sorte || t.bezeichnung}${t.form === 'papier' ? ' · Papier' : ''} · ${p.ticket_gilt ? `bis ${kurzDatum(t.gueltig_bis)}` : `abgelaufen ${kurzDatum(t.gueltig_bis)}`}`
      : 'Noch kein Ticket'));
    const mini = auslastung(z);
    karte.append(mini);
    const teile = [];
    if (p.antraege.bereit) teile.push(`${p.antraege.bereit} bereit`);
    if (p.antraege.wartet) teile.push(`${p.antraege.wartet} wartet`);
    if (p.antraege.unvollstaendig) teile.push(`${p.antraege.unvollstaendig} unvollständig`);
    if (!p.ich && p.geld.offen_cent) teile.push(p.geld.ich_zahle_aus ? `du schuldest ${euro(p.geld.offen_cent)}` : `schuldet dir ${euro(p.geld.offen_cent)}`);
    if (teile.length) karte.append(el('div', 'klein', teile.join(' · ')));
    if (p.gruende.length && p.ampel !== 'voll') karte.append(el('div', 'klein g-grund', p.gruende.join(' · ')));
    karte.addEventListener('click', () => personOeffnen(p.id));
    return karte;
  }

  function personenListe(ziel, personen) {
    ziel.textContent = '';
    // Filter und Suche gelten nur, wenn sie auch zu sehen sind (ab 3 bzw. 6 Personen) — sonst verschwände jemand unbemerkt.
    const passt = personen.length > 2 && FILTER[filter] ? FILTER[filter][1] : FILTER.alle[1];
    const such = personen.length > 5 ? namensuche.trim().toLowerCase() : '';
    const liste = personen.filter((p) => passt(p) && (!such || (p.ich ? 'ich' : p.name).toLowerCase().includes(such)));
    if (!liste.length) { ziel.append(el('p', 'leer-klein', 'Niemand passt dazu.')); return; }
    for (const p of liste) ziel.append(personZeile(p));
  }

  function personenBlock(u) {
    const b = el('div', 'block');
    const kopf = el('div', 'kopfzeile');
    kopf.append(el('h2', '', 'Personen'), knopf('＋ Neue Person', 'leise-knopf g-neu-knopf', neuStarten));
    b.append(kopf);
    const aktiveP = u.personen.filter((p) => !p.archiviert);
    const sortiert = [...aktiveP].sort((x, y) => (x.ich ? -1 : y.ich ? 1 : 0)
      || (RANG[x.ampel] - RANG[y.ampel]) || x.name.localeCompare(y.name, 'de'));
    const frei = aktiveP.filter(FILTER.frei[1]).length;
    const voll = aktiveP.filter(FILTER.voll[1]).length;
    b.append(el('p', 'leise', `Noch frei bei ${frei} von ${aktiveP.length}${voll ? ` · voll: ${voll}` : ''}. `
      + 'Grün: es geht noch · gelb: es fehlt etwas oder der Rest wartet auf Bescheide · rot: Monat voll. Antippen öffnet die Person.'));
    const listeEl = el('div', 'g-liste');
    if (aktiveP.length > 5) {
      const suche = el('input', 'g-suche');
      suche.type = 'search';
      suche.placeholder = 'Name suchen';
      suche.value = namensuche;
      suche.addEventListener('input', () => { namensuche = suche.value; personenListe(listeEl, sortiert); });
      b.append(suche);
    }
    if (aktiveP.length > 2) {
      const chips = el('div', 'chips');
      for (const [wert, [text, fn]] of Object.entries(FILTER)) {
        chips.append(knopf(`${text} (${aktiveP.filter(fn).length})`, filter === wert ? 'aktiv' : '', () => {
          filter = wert;
          try { localStorage.setItem('friday.garantie.filter', wert); } catch (_) {}
          zeichnen();
        }));
      }
      b.append(chips);
    }
    b.append(listeEl);
    personenListe(listeEl, sortiert);
    const archiv = u.personen.filter((p) => p.archiviert);
    if (archiv.length) {
      const d = el('details', 'g-archiv');
      d.append(el('summary', 'klein', `Archiviert (${archiv.length})`));
      const c = el('div', 'chips');
      for (const p of archiv) c.append(knopf(p.name, '', () => personOeffnen(p.id)));
      d.append(c);
      b.append(d);
    }
    return b;
  }

  // Über alle Personen: was jetzt getan werden kann. Leer, wenn nichts ansteht.
  function aufgabenBlock(u) {
    const bereit = u.aufgaben.bereit;
    const rueck = u.aufgaben.rueckfragen;
    const ohneTicket = u.personen.filter((p) => !p.archiviert && !p.ticket_gilt);
    // Wen sonst etwas aufhält (Nummern vergleichen, Zustimmung, Angaben) — ein Tipp führt zur Person.
    const haengt = u.personen.filter((p) => !p.archiviert && p.ampel === 'fehlt' && p.gruende.some((g) => !/^Ticket für/.test(g)));
    if (!bereit.length && !rueck.length && !ohneTicket.length && !haengt.length && !aufgabenText) return null;
    const b = block('Zu erledigen');
    if (bereit.length) {
      b.append(el('h4', '', `Bereit zum Abschicken (${bereit.length}) — knappste Frist zuerst`));
      blaettern(b, 'bereit', bereit, (e) => antragKarte(e, true));
      if (!u.abschicken) b.append(el('p', 'leise', 'Abschicken durch Friday: in den Freigaben bei „ÜSTRA-Garantie“ „Anträge abschicken“ einschalten.'));
    }
    if (rueck.length) {
      b.append(el('h4', '', `Rückfragen der ÜSTRA (${rueck.length})`));
      for (const v of rueck) {
        const karte = el('div', 'eintrag klickbar');
        karte.append(el('span', 'g-person-tag', v.profil ? v.person : 'Ich'));
        const kopf = el('div', 'kopfzeile');
        kopf.append(el('span', 'titel', `${v.fahrtdatum ? kurzDatum(v.fahrtdatum) + ' ' : ''}${v.linie ? 'Linie ' + v.linie + ': ' : ''}${v.start || 'Fall ' + v.vorgang}${v.ziel ? ' → ' + v.ziel : ''}`),
                    el('span', 'klein status-warn', 'Rückfrage'));
        karte.append(kopf, el('div', 'klein', `Fall ${v.vorgang}`));
        if (v.grund) karte.append(el('div', 'leise', v.grund));
        const reihe2 = el('div', 'knoepfe');
        reihe2.append(erledigtKnopf({ ...v, stand: 'rueckfrage' }, karte));
        karte.append(reihe2);
        karte.addEventListener('click', () => personOeffnen(v.profil));
        b.append(karte);
      }
    }
    if (ohneTicket.length) {
      const monat = (ohneTicket[0].zaehler || {}).monat_name || 'diesen Monat';
      b.append(el('h4', '', `Ticket für ${monat} fehlt (${ohneTicket.length})`));
      b.append(el('p', 'leise', 'Antippen und das Ticket der Person wählen — Friday liest es aus und legt es bei ihr ab.'));
      const reihe = el('div', 'knoepfe g-tickets');
      for (const p of ohneTicket) {
        const label = el('label', 'g-hochladen-knopf');
        const datei = hochladen(p.id, (text, fertig) => { aufgabenText = `${p.ich ? 'Ich' : p.name}: ${text}`; if (fertig) laden(); else zeichnen(); });
        datei.hidden = true;
        label.append(datei, document.createTextNode(`＋ ${p.ich ? 'Ich' : p.name}`));
        reihe.append(label);
      }
      b.append(reihe);
    }
    if (aufgabenText) b.append(el('div', 'ausgabe', aufgabenText));
    if (haengt.length) {
      b.append(el('h4', '', `Braucht dich (${haengt.length})`));
      for (const p of haengt) {
        const zeile = el('div', 'eintrag klickbar g-haengt');
        const kopf = el('div', 'kopfzeile');
        kopf.append(el('span', 'titel', p.ich ? 'Ich' : p.name), el('span', 'klein', 'öffnen ›'));
        zeile.append(kopf, el('div', 'klein g-grund', p.gruende.filter((g) => !/^Ticket für/.test(g)).join(' · ')));
        zeile.addEventListener('click', () => personOeffnen(p.id));
        b.append(zeile);
      }
    }
    return b;
  }

  function geldKurzBlock(u) {
    const ich = u.personen.find((p) => p.ich);
    const freunde = u.personen.filter((p) => !p.ich && !p.archiviert);
    const b = block('Geld');
    const kacheln = el('div', 'g-kacheln');
    const kachel = (wert, text, klasse = '') => { const k = el('div', `g-kachel ${klasse}`); k.append(el('div', 'g-wert', wert), el('div', 'klein', text)); kacheln.append(k); };
    kachel(euro(ich.geld.summe_cent), 'eingenommen (du)');
    kachel(euro(ich.geld.monat_cent), 'davon diesen Monat');
    if (freunde.length) {
      kachel(euro(freunde.reduce((x, p) => x + p.geld.mein_cent, 0)), 'dein Anteil aus Freunden');
      const schulde = freunde.reduce((x, p) => x + (p.geld.ich_zahle_aus ? p.geld.offen_cent : 0), 0);
      const kriege = freunde.reduce((x, p) => x + (p.geld.ich_zahle_aus ? 0 : p.geld.offen_cent), 0);
      if (schulde) kachel(euro(schulde), 'du zahlst noch aus');
      if (kriege) kachel(euro(kriege), 'bekommst du noch');
    }
    b.append(kacheln);
    b.append(el('p', 'leise', 'Quote und Abrechnung bei der jeweiligen Person.'));
    if (u.post) {
      const info = el('div', 'klein');
      const reihe = el('div', 'knoepfe');
      reihe.append(abgleichKnopf(info));
      b.append(reihe, info);
    }
    return b;
  }

  // Über alle Personen zusammen, nach dem Monat des Bescheids — anders als geldKurzBlock (nur Eikes eigenes Geld
  // plus die Anteile), hier die GESAMTSUMME, die die ÜSTRA insgesamt bestätigt und ausgezahlt hat.
  function gesamtMonateBlock(st) {
    const b = block('Ausgezahlt pro Monat (alle)');
    const liste = monateListe(st.monate);
    if (!liste) { b.append(el('p', 'leer-klein', 'Noch nichts genehmigt.')); return b; }
    b.append(liste);
    b.append(el('p', 'leise', `${euro(st.insgesamt_cent)} insgesamt, davon ${euro(st.monat_cent)} diesen Monat — `
                              + 'über alle Personen zusammen, gezählt nach dem Tag des Bescheids.'));
    return b;
  }

  // Verfolgung bis zum Bescheid (kern/garantie/verwaltung.py): je offenem Vorgang Status, nächster Schritt, Frist. Nur anzeigen.
  function verwaltungBlock(u) {
    const liste = u.verwaltung || [];
    if (!liste.length) return null;
    // Zugeklappt unter „Suchen“ — nur Buchführung nach dem Abschicken, nichts, was täglich ansteht.
    const b = el('details', 'block g-fahrten');
    b.open = verfolgungOffen;
    b.addEventListener('toggle', () => { verfolgungOffen = b.open; });
    b.append(el('summary', '', `Verfolgung (${liste.length})`));
    // Sammelabschluss: alle offenen Vorgänge beenden (Altbestand). Nur Buchführung, nichts wird gesendet; die Historie bleibt.
    const alle = knopf('Alle abhaken', 'vw-alle', async (k) => {
      if (!confirm(`Alle ${liste.length} offenen Vorgänge abhaken?\n\nNoch nie Eingereichtes wird verworfen, alles andere als erledigt markiert. Es wird nichts abgeschickt, die Historie bleibt.`)) return;
      k.disabled = true;
      try { await senden('/api/garantie/verwaltung/alle-abschliessen', { bestaetigt: true }); laden(); }
      catch (e) { k.disabled = false; alert(e.message); }
    });
    b.append(alle);
    blaettern(b, 'verwaltung', liste, (v) => {
      const karte = el('div', 'eintrag klickbar');
      if (v.profil) karte.append(el('span', 'g-person-tag', v.person));
      const kopf = el('div', 'kopfzeile');
      kopf.append(el('span', 'titel', v.titel), el('span', `klein${v.faellig ? ' status-warn' : ''}`, v.phase_text));
      karte.append(kopf);
      const frist = v.frist ? ` · Frist ${kurzDatum(v.frist)}${v.frist_tage != null ? ` (${v.frist_tage < 0 ? 'vorbei' : `noch ${v.frist_tage} T.`})` : ''}` : '';
      karte.append(el('div', 'klein', `${v.vorgang ? `Fall ${v.vorgang} · ` : ''}seit ${kurzDatum(v.seit)}${frist}`));
      karte.append(el('div', v.faellig ? 'klein status-warn' : 'leise', `${v.faellig ? 'Fällig: ' : `Ab ${kurzDatum(v.faellig_ab)}: `}${v.schritt_text}`));
      // Phase setzen (POST /api/garantie/verwaltung/<ref>/phase): nur die laut Server erlaubten Folgephasen, nie „abgeschickt“.
      // Das ist nur Buchführung — es wird nichts gesendet. Fehler (z. B. Übergang nicht vorgesehen) stehen im Klartext an der Karte.
      if ((v.folgephasen || []).length) {
        const fehler = el('div', 'ausgabe status-fehler');
        fehler.hidden = true;
        const knoepfe = el('div', 'knoepfe');
        for (const f of v.folgephasen) {
          knoepfe.append(knopf(f.text, 'vw-phase', async (k) => {
            if (!confirm(`„${v.titel}“ auf „${f.text}“ setzen?\n\nDas ändert nur den Stand in Friday. Es wird nichts abgeschickt.`)) return;
            const daten = { phase: f.phase, bestaetigt: true };
            if (v.ref.startsWith('B') && f.phase === 'bescheid_positiv') {
              const t = prompt('Erhaltener Betrag in Euro?');
              if (t === null) return;
              daten.betrag = Number(t.replace(',', '.'));
              if (!Number.isFinite(daten.betrag) || daten.betrag < 0) { fehler.hidden = false; fehler.textContent = 'Bitte einen Betrag wie 12,50 eingeben.'; return; }
            }
            k.disabled = true; fehler.hidden = true;
            try { await senden(`/api/garantie/verwaltung/${encodeURIComponent(v.ref)}/phase`, daten); laden(); }
            catch (e) { k.disabled = false; fehler.hidden = false; fehler.textContent = e.message; }
          }));
        }
        karte.append(knoepfe, fehler);
      }
      karte.addEventListener('click', () => personOeffnen(v.profil));
      return karte;
    });
    return b;
  }

  function uebersichtSeite(ziel) {
    const u = uebersicht;
    const a = aufgabenBlock(u);
    if (a) ziel.append(a);
    ziel.append(personenBlock(u), suchBlock());
    const vw = verwaltungBlock(u);
    if (vw) ziel.append(vw);
    // Bahn-Fahrgastrechte (fahrgastrechte.js): Züge von der S-Bahn bis zum ICE, für alle Personen.
    if (window.FridayBahn && u.bahn) ziel.append(window.FridayBahn.block(u.bahn, { profil: null, zeichnen, laden }));
    ziel.append(geldKurzBlock(u));
    if (u.statistik_gesamt) ziel.append(gesamtMonateBlock(u.statistik_gesamt));
  }

  /* ---------- Personenseite ---------- */

  function personKopf() {
    const k = el('div', 'g-kopf');
    k.append(knopf('‹ Alle', 'leise-knopf g-zurueck', zurUebersicht));
    const wechsel = el('select', 'g-wechsel');
    wechsel.setAttribute('aria-label', 'Person wechseln');
    const opt = (id, text) => { const o = el('option', '', text); o.value = String(id); wechsel.append(o); };
    opt(0, 'Ich');
    const alle = stand.profile || [];
    for (const p of alle.filter((x) => !x.archiviert).sort((x, y) => x.name.localeCompare(y.name, 'de'))) opt(p.id, p.name);
    for (const p of alle.filter((x) => x.archiviert)) opt(p.id, `${p.name} (archiviert)`);
    wechsel.value = String(profil);
    wechsel.addEventListener('change', () => personOeffnen(Number(wechsel.value)));
    k.append(wechsel);
    return k;
  }

  function personSeite(ziel) {
    ziel.append(personKopf());
    if (profil) {
      ziel.append(el('p', 'g-hinweisband', `Du arbeitest gerade für ${personName()}: Anträge, Ticket, Angaben, Limits und Bescheide `
        + 'gelten nur für diese Person. Abgeschickt wird nur mit ihren Daten.'));
    }
    ziel.append(zaehlerBlock(stand.zaehler), antragsBlock(stand.antraege), ticketBlock(stand.tickets || [], stand.fahrkarten));
    // Eine Fahrt für genau diese Person vorbereiten — dieselbe Liste wie in der Übersicht, zugeklappt.
    const fahrten = el('details', 'block g-fahrten');
    fahrten.open = fahrtenOffen || !!offen;
    fahrten.addEventListener('toggle', () => { fahrtenOffen = fahrten.open; });
    fahrten.append(el('summary', '', profil ? `Fahrt für ${personName()} vorbereiten` : 'Fahrt für mich vorbereiten'));
    fahrten.append(suchBlock());
    ziel.append(fahrten);
    if (window.FridayBahn && stand.bahn) ziel.append(window.FridayBahn.block(stand.bahn, { profil, zeichnen, laden }));
    ziel.append(postBlock(stand.post, stand.vorgaenge || []));
    if (profil) ziel.append(abrechnungBlock(stand.abrechnung));
    ziel.append(geldBlock(stand.statistik));
    ziel.append(profil ? profilAngabenBlock(stand.angaben) : angabenBlock(stand.angaben));
  }

  /* ---------- Neue Person: ein eigener, kurzer Ablauf ---------- */
  // Erst nur ein Name und die Tickets. Friday liest daraus Art, Gültigkeit, Namen und Nummern und entscheidet selbst, welche
  // Fahrkarte es ist; danach fragt dieser Ablauf nur, was auf dem Ticket nicht steht. Er hat eine eigene Seite — nichts
  // von Eikes Angaben, Anträgen oder Limits steht daneben. Alle Eingaben liegen in `neu`, ein Neuzeichnen verliert nichts.

  let neu = null;
  let neuVorher = 'alle';   // wohin „Abbrechen“ zurückführt

  const emailBasis = () => ((daten() || uebersicht || stand || {}).email_basis || '');
  const aliasFuer = (text) => {
    const basis = emailBasis();
    if (!basis) return '';
    const [nutzer, host] = basis.split('@');
    const wer = (text || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '') || 'person';
    return `${nutzer.split('+')[0]}+${wer}@${host}`;
  };

  function neuStarten() {
    neu = { schritt: 'ticket', name: '', dateien: [], laeuft: false, fehler: '', vorhanden: null, id: null, tickets: [], text: '',
            w: {}, erkannt: new Set(), mailAngefasst: false };
    neuVorher = ansicht === 'neu' ? neuVorher : ansicht;
    ansicht = 'neu';
    profilForm = 'neu';
    zeichnen();
    window.scrollTo({ top: 0 });
  }

  async function neuAbbrechen() {
    const id = neu && neu.id;
    neu = null;
    profilForm = null;
    // Ein halb angelegtes Profil samt Ticket wieder wegräumen (geht nur, solange noch kein Antrag dran hängt).
    if (id) { try { await fetch(`/api/garantie/profile/${id}`, { method: 'DELETE' }); } catch (_) {} }
    ansicht = neuVorher === 'person' && stand ? 'person' : 'alle';
    await laden();
  }

  async function neuTicketsLesen() {
    const form = new FormData();
    form.append('name', neu.name);
    for (const d of neu.dateien) form.append('dateien', d, d.name);
    neu.laeuft = true; neu.fehler = ''; neu.vorhanden = null;
    zeichnen();
    try {
      const r = await antwort(await fetch('/api/garantie/profile/aus-ticket', { method: 'POST', body: form }));
      if (r.vorhanden) { neu.vorhanden = r.vorhanden; neu.text = r.text; }
      else {
        neu.id = r.id; neu.tickets = r.tickets; neu.text = r.text;
        neuWerte(r.angaben);
        neu.schritt = 'daten';
      }
    } catch (e) { neu.fehler = e.message; }
    neu.laeuft = false;
    zeichnen();
  }

  // Die Felder des zweiten Schritts vorbelegen — mit dem, was das Ticket verraten hat.
  function neuWerte(a) {
    const b = a || {};
    // Die Kundenkarte kann auf einem eigenen Foto stehen, das Monatsticket auf einem anderen: aus allen Tickets nehmen.
    const monat = neu.tickets.find((x) => x.gueltig_bis) || null;
    const w = { name: neu.name || b.name || '', anrede: b.anrede || '', vorname: b.vorname || '', nachname: b.nachname || '',
                strasse: b.strasse || '', plz: b.plz || '', ort: b.ort || '', email: b.email || '', abo_nummer: b.abo_nummer || '',
                kundenkarte: b.kundenkarte || (neu.tickets.find((x) => x.kundenkarte) || {}).kundenkarte || '',
                ticketnummer: monat ? monat.ticketnummer || '' : '', preis: monat && monat.preis ? String(monat.preis) : '',
                auszahlung: 'ich', kontoinhaber: '', iban: '', anteil: '', vollmacht: false, verglichen: false };
    neu.erkannt = new Set(Object.keys(w).filter((k) => w[k] && !['auszahlung'].includes(k) && !(k === 'name' && neu.name)));
    if (!w.email) w.email = aliasFuer(w.vorname || w.name);
    neu.w = w;
  }

  function neuePersonSeite() {
    const seite = el('div', 'g-neu');
    const kopf = el('div', 'g-neu-kopf');
    kopf.append(knopf(neu.schritt === 'daten' ? '‹ Abbrechen' : '‹ Zurück', 'leise-knopf', neuAbbrechen), el('h2', '', 'Neue Person'));
    seite.append(kopf);
    if (neu.schritt === 'ticket') neuTicketSchritt(seite); else neuDatenSchritt(seite);
    return seite;
  }

  function neuTicketSchritt(seite) {
    seite.append(el('p', 'leise', 'Gib der Person einen Namen (nur für dich) und lade ihr Ticket hoch — Foto, Screenshot oder PDF, gern mehrere '
      + 'auf einmal. Bei der Jugendnetzkarte auf Papier: Wertmarke und Kundenkarte fotografieren. Friday liest Art, Gültigkeit, Namen und '
      + 'Nummern selbst aus und entscheidet, um welche Fahrkarte es geht. Danach fragt sie nur noch, was auf dem Ticket nicht steht.'));
    const name = el('input');
    name.type = 'text'; name.maxLength = 60; name.placeholder = 'Name im Profil, z. B. Lea'; name.value = neu.name;
    name.addEventListener('input', () => { neu.name = name.value; });
    const k = el('label', 'g-feld');
    k.append(el('span', 'klein', 'Name (nur für dich)'), name);
    seite.append(k);

    const datei = el('input');
    datei.type = 'file'; datei.multiple = true; datei.accept = 'application/pdf,image/*'; datei.hidden = true;
    datei.addEventListener('change', () => { neu.dateien = [...datei.files]; neu.vorhanden = null; neu.fehler = ''; zeichnen(); });
    const feld = el('label', 'hochladen');
    feld.append(datei, el('span', '', neu.dateien.length ? `${neu.dateien.length} Datei(en) gewählt — antippen zum Ändern` : '＋ Ticket hochladen'));
    seite.append(feld);
    for (const d of neu.dateien) seite.append(el('div', 'klein', `• ${d.name}`));

    if (neu.fehler) seite.append(el('p', 'status-fehler', neu.fehler));
    if (neu.vorhanden) {
      seite.append(el('p', 'g-hinweisband', neu.text));
      seite.append(knopf(`Tickets zu ${neu.vorhanden.name} hinzufügen`, 'voll-knopf', async (b) => {
        b.disabled = true;
        try {
          for (const d of neu.dateien) {
            const form = new FormData();
            form.append('datei', d, d.name);
            await antwort(await fetch(`/api/garantie/ticket?profil=${neu.vorhanden.id}`, { method: 'POST', body: form }));
          }
          const id = neu.vorhanden.id;
          neu = null; profilForm = null;
          personOeffnen(id);
        } catch (e) { neu.fehler = e.message; b.disabled = false; zeichnen(); }
      }));
    }
    const weiter = knopf(neu.laeuft ? 'Friday liest das Ticket …' : 'Weiter — Ticket lesen', 'voll-knopf', neuTicketsLesen);
    weiter.disabled = neu.laeuft || !neu.dateien.length;
    seite.append(weiter);
    seite.append(knopf('Ohne Ticket anlegen', 'leise-knopf', () => {
      if (!neu.name.trim()) { neu.fehler = 'Gib der Person zuerst einen Namen.'; zeichnen(); return; }
      neuWerte(null); neu.schritt = 'daten'; zeichnen();
    }));
  }

  function neuDatenSchritt(seite) {
    const w = neu.w;
    if (neu.text) seite.append(el('p', 'g-hinweisband', neu.text));
    const gefunden = neu.tickets.length ? neu.tickets.map((t) => (t.ist_kundenkarte ? 'Kundenkarte' : t.bezeichnung || t.art)).filter(Boolean).join(', ') : '';
    seite.append(el('p', 'leise', neu.id && neu.erkannt.size
      ? `Aus dem Ticket übernommen${gefunden ? ` (${gefunden})` : ''}: alles mit „✓“. Trag ein, was noch fehlt.`
      : 'Trag die Angaben der Person ein.'));

    let mailFeld = null;
    const feld = (key, text, typ = 'text', hilfe = '') => {
      const k = el('label', 'g-feld');
      k.append(el('span', 'klein', (neu.erkannt.has(key) ? '✓ ' : '') + text));
      const f = el('input');
      f.type = typ; f.value = w[key] || '';
      if (typ === 'number') { f.step = '0.01'; f.min = '0'; }
      f.addEventListener('input', () => {
        w[key] = f.value;
        if (key === 'email') neu.mailAngefasst = true;
        // Solange die E-Mail nicht angefasst ist, folgt der vorgeschlagene Alias dem Vornamen.
        if (key === 'vorname' && !neu.mailAngefasst && !neu.erkannt.has('email') && mailFeld) { w.email = aliasFuer(f.value); mailFeld.value = w.email; }
      });
      k.append(f);
      if (hilfe) k.append(el('span', 'leise', hilfe));
      seite.append(k);
      return f;
    };
    const gruppe = (titel) => seite.append(el('h4', '', titel));

    gruppe('Wer ist die Person?');
    feld('name', 'Name im Profil (nur für dich)');
    const anrede = el('div', 'chips');
    for (const a of ['Herr', 'Frau', 'Divers']) anrede.append(knopf(a, w.anrede === a ? 'aktiv' : '', () => { w.anrede = a; zeichnen(); }));
    const ak = el('div', 'g-feld');
    ak.append(el('span', 'klein', 'Anrede'), anrede);
    seite.append(ak);
    feld('vorname', 'Vorname');
    feld('nachname', 'Nachname');

    gruppe('Adresse');
    feld('strasse', 'Straße und Hausnummer');
    feld('plz', 'PLZ');
    feld('ort', 'Ort');

    gruppe('E-Mail für die ÜSTRA');
    mailFeld = feld('email', 'E-Mail-Adresse', 'email', 'Jede Person braucht ihre eigene. Dein Gmail mit Plus-Alias geht: Die Bescheide kommen bei dir an, '
      + 'und Friday sieht an der Adresse, wessen Bescheid es ist. Vorgeschlagen ist ein Alias aus dem Namen.');

    const braucht = neu.tickets.some((t) => JUGEND.test(t.bezeichnung || ''));
    const papier = neu.tickets.some((t) => JUGEND.test(t.bezeichnung || '') && !t.ist_kundenkarte && (t.form !== 'digital' || t.kundenkarte_handschrift));
    if (braucht) {
      gruppe(`Fahrkarte (Jugend-/Ausbildungsnetzkarte${papier ? ', Papier' : ''})`);
      feld('kundenkarte', 'Kundenkartennummer (6 Ziffern, steht auf der Kundenkarte)');
      feld('ticketnummer', papier ? 'Ticketnummer (Zahlengruppen ganz unten auf der Wertmarke, ohne Datum und Uhrzeit)'
                                  : 'Nummer des Monatstickets (in der ÜSTRA-App oben hinter dem Datum)');
      feld('preis', 'Preis der Karte in € (mehr als den Preis erstattet die ÜSTRA je Monat nicht)', 'number');
      if (papier) {
        const v = el('label', 'check klein');
        const h = el('input'); h.type = 'checkbox'; h.checked = w.verglichen;
        h.addEventListener('change', () => { w.verglichen = h.checked; });
        v.append(h, document.createTextNode(' Nummern verglichen: Kundenkarten- und Ticketnummer stimmen Ziffer für Ziffer mit der Papierkarte überein. '
          + 'Ohne das Häkchen schickt Friday für diese Karte nichts ab.'));
        seite.append(v);
      }
    } else if (!neu.id || neu.tickets.some((t) => t.art === 'Deutschlandticket')) {
      gruppe('Fahrkarte');
      feld('abo_nummer', 'Abo-Kundennummer (bei Abo-Tickets)');
    }

    gruppe('Das Geld');
    const aus = el('select');
    for (const [wert, text] of [['ich', 'Auf mein Konto — ich zahle den Teil aus'], ['eigenes', 'Auf das Konto der Person — sie gibt mir meinen Anteil']]) {
      const o = el('option', '', text); o.value = wert; aus.append(o);
    }
    aus.value = w.auszahlung;
    const konto = el('div');
    const kontoZeigen = () => { konto.hidden = aus.value !== 'eigenes'; };
    aus.addEventListener('change', () => { w.auszahlung = aus.value; kontoZeigen(); });
    const ak2 = el('label', 'g-feld');
    ak2.append(el('span', 'klein', 'Wohin überweist die ÜSTRA?'), aus);
    seite.append(ak2);
    const kf = (key, text) => { const k = el('label', 'g-feld'); k.append(el('span', 'klein', text)); const f = el('input'); f.type = 'text'; f.value = w[key];
      f.autocomplete = 'off'; f.addEventListener('input', () => { w[key] = f.value; }); k.append(f); konto.append(k); };
    kf('kontoinhaber', 'Kontoinhaber der Person');
    kf('iban', 'IBAN der Person');
    seite.append(konto);
    kontoZeigen();
    feld('anteil', 'Mein Anteil am Geld (in %)', 'number');

    const zust = el('label', 'check klein');
    const haken = el('input');
    haken.type = 'checkbox'; haken.checked = w.vollmacht;
    haken.addEventListener('change', () => { w.vollmacht = haken.checked; });
    zust.append(haken, document.createTextNode(' Die Person hat zugestimmt, dass ich für sie einreiche, ihre Daten hier speichere und dass Google Gemini ihr Ticket '
      + 'ausliest. Ohne das Häkchen schickt Friday nichts ab.'));
    seite.append(zust);

    const fehler = el('p', 'status-fehler', neu.fehler);
    const speichern = knopf('Person speichern', 'voll-knopf', async (b) => {
      b.disabled = true; fehler.textContent = '';
      const d = { name: w.name, anrede: w.anrede, vorname: w.vorname, nachname: w.nachname, strasse: w.strasse, plz: w.plz,
                  ort: w.ort, email: w.email, abo_nummer: w.abo_nummer, kundenkarte: w.kundenkarte.trim(),
                  auszahlung: w.auszahlung, kontoinhaber: w.kontoinhaber,
                  anteil: w.anteil === '' ? 0 : Number(w.anteil), vollmacht: w.vollmacht };
      if (w.iban.trim()) d.iban = w.iban.trim();
      try {
        let pid = neu.id;
        if (pid) await stellen(`/api/garantie/profile/${pid}`, d);
        else pid = (await senden('/api/garantie/profile', d)).id;
        const t = neu.tickets.find((x) => x.gueltig_bis) || neu.tickets.find((x) => !x.ist_kundenkarte);
        if (t && (w.ticketnummer.trim() || w.preis !== '' || papier)) {
          const tk = { ticketnummer: w.ticketnummer.trim(), preis: w.preis === '' ? 0 : Number(w.preis) };
          if (braucht) tk.nummern_geprueft = papier ? !!w.verglichen : undefined;
          await senden(`/api/garantie/ticket/${t.id}`, tk);
        }
        neu = null; profilForm = null;
        personOeffnen(pid);
      } catch (e) { fehler.textContent = e.message; b.disabled = false; }
    });
    seite.append(fehler, speichern);
    seite.append(el('p', 'leise', 'Es muss nicht alles ausgefüllt sein — was fehlt, steht später als „fehlt“ bei der Person, und Friday schickt erst ab, wenn alles da ist.'));
  }

  /* ---------- Zeichnen und Laden ---------- */
  function zeichnen() {
    const ziel = $('garantie-inhalt');
    if (ansicht === 'neu' && neu) { ziel.textContent = ''; ziel.append(neuePersonSeite()); return; }   // eigene Seite, sonst nichts daneben
    const d = daten();
    if (!d) return;
    ziel.textContent = '';
    if (!d.aktiv) {
      ziel.append(el('p', 'leise', 'Die Freigabe „ÜSTRA-Garantie“ ist aus — dann sucht Friday nicht und prüft keine '
                                  + 'Fahrten. Einschalten auf der Freigaben-Seite.'));
    }
    if (ansicht === 'person') personSeite(ziel); else uebersichtSeite(ziel);
  }

  async function laden() {
    if (location.hash !== '#studio' || reiter !== 'garantie') return;
    if (ansicht === 'neu') { zeichnen(); return; }
    try {
      if (ansicht === 'person') stand = await holen(`/api/garantie?profil=${profil}`);
      else uebersicht = await holen('/api/garantie/uebersicht');
    } catch (e) {
      // Das Profil gibt es nicht mehr (anderswo gelöscht): zurück zur Übersicht.
      if (ansicht === 'person' && /Profil/.test(e.message || '')) { ansicht = 'alle'; return laden(); }
      $('garantie-inhalt').textContent = 'Die Garantie ist gerade nicht erreichbar.'; return;
    }
    zeichnen();
  }

  // Aus einer Benachrichtigung („/?garantie=<id>#studio“): gleich den Reiter und die Fahrt öffnen.
  async function ausBenachrichtigung() {
    const id = new URLSearchParams(location.search).get('garantie');
    if (!id) return;
    history.replaceState(null, '', `${location.pathname}#studio`);
    ansicht = 'alle';
    const knopfGarantie = document.querySelector('#studio .reiter button[data-reiter="garantie"]');
    if (knopfGarantie) knopfGarantie.click();
    await laden();
    if (id !== 'post') await fahrtOeffnen(id);          // „post“: ein Bescheid — die Übersicht zeigt Rückfragen
  }

  window.addEventListener('hashchange', laden);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && !offen && ansicht !== 'neu') laden(); });
  if (new URLSearchParams(location.search).get('garantie')) ausBenachrichtigung();
  else laden();
})();
