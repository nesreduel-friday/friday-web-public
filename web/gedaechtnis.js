/* Gedächtnis-Seite: Wissen (nach Typen), Notizbuch, Bilder, Gespräche.

   Alles wird per textContent eingesetzt, nie als HTML. Inhalte können aus
   Webseiten stammen — die sollen im Browser keinen Code ausführen können. */
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
  const senden = (url, daten) => fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(daten),
  });
  const holen = async (url) => (await fetch(url, { cache: 'no-store' })).json();
  const bildUrl = (id) => `/api/bilder/${encodeURIComponent(id)}`;

  /* ---------- Reiter ---------- */
  let reiter = 'wissen';
  for (const b of document.querySelectorAll('#gedaechtnis .reiter button')) {
    b.addEventListener('click', () => {
      reiter = b.dataset.reiter;
      for (const x of document.querySelectorAll('#gedaechtnis .reiter button')) x.classList.toggle('aktiv', x === b);
      for (const id of ['wissen', 'notizbuch', 'bilder', 'verlaeufe', 'gespraeche', 'gedanken']) $(`r-${id}`).hidden = id !== reiter;
      // „Wissen“ antippen führt immer zurück zur Hauptseite — auch aus tiefen Unterseiten (Seite einer Person, Antwort, Treffer).
      if (reiter === 'wissen') { zuDenThemen(); window.scrollTo({ top: 0 }); }
      laden();
    });
  }
  // Auch der Eintrag „Gedächtnis“ unten in der Leiste: auf der Seite angetippt, wieder zur Hauptseite von „Wissen“.
  const navLink = document.querySelector('nav a[href="#gedaechtnis"]');
  if (navLink) navLink.addEventListener('click', () => {
    if (location.hash === '#gedaechtnis') document.querySelector('#gedaechtnis .reiter button[data-reiter="wissen"]').click();
  });

  /* ---------- Vorschläge ---------- */
  function vorschlaege(liste) {
    $('g-vorschlaege-block').hidden = liste.length === 0;
    const ziel = $('g-vorschlaege');
    ziel.textContent = '';
    for (const v of liste) {
      const e = el('div', 'eintrag vorschlag');
      const kopf = el('div', 'kopfzeile');
      kopf.append(el('span', 'titel', v.thema), el('span', 'klein', v.zeit));
      const knoepfe = el('div', 'knoepfe');
      const entscheiden = async (antwort) => {
        await senden(`/api/gedaechtnis/vorschlaege/${encodeURIComponent(v.id)}`, { entscheidung: antwort });
        laden();
      };
      knoepfe.append(knopf('Stimmt', 'ja', () => entscheiden('ja')),
                     knopf('Stimmt nicht', 'nein', () => entscheiden('nein')));
      e.append(kopf, el('div', 'text', v.inhalt), el('div', 'klein', `Herkunft: ${v.herkunft}`), knoepfe);
      ziel.append(e);
    }
  }

  /* ---------- Wissen: Bausteine ---------- */
  let wissen = null, typAuswahl = null;
  const SVG = 'http://www.w3.org/2000/svg';
  // Einfache Linien-Symbole (24×24) je Typ; Personen bekommen stattdessen ihre Initialen.
  const SYMBOLE = {
    Location: ['M12 21s-7-5.2-7-11a7 7 0 0 1 14 0c0 5.8-7 11-7 11z', 'M12 7.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z'],
    Organization: ['M5 21V5a1 1 0 0 1 1-1h7a1 1 0 0 1 1 1v16', 'M14 10h4a1 1 0 0 1 1 1v10', 'M3 21h18', 'M8 8h2M8 12h2M8 16h2'],
    Event: ['M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z', 'M4 10h16M8 3v4M16 3v4'],
    Topic: ['M4 9h16M4 15h16M10 3 8 21M16 3l-2 18'],
    Project: ['M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z'],
  };
  function symbol(typ, klasse) {
    const art = SYMBOLE[typ] ? typ : 'Topic';
    const huelle = el('span', `sym t-${art}${klasse ? ' ' + klasse : ''}`);
    const svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    for (const d of SYMBOLE[art]) { const pfad = document.createElementNS(SVG, 'path'); pfad.setAttribute('d', d); svg.append(pfad); }
    huelle.append(svg);
    return huelle;
  }
  function avatar(name, klasse) {
    const teile = name.trim().split(/\s+/);
    const a = el('span', `avatar${klasse ? ' ' + klasse : ''}`,
                 ((teile[0] || '?')[0] + (teile.length > 1 ? teile[teile.length - 1][0] : '')).toUpperCase());
    let farbton = 0;
    for (const c of name) farbton = (farbton * 31 + c.charCodeAt(0)) % 360;
    a.style.setProperty('--farbton', farbton);
    a.setAttribute('aria-hidden', 'true');
    return a;
  }
  const bild = (typ, name, klasse) => (typ === 'Person' ? avatar(name, klasse) : symbol(typ, klasse));

  // Eine Zeile wie in Apples Einstellungen: Symbol oder Initialen, Name (optional mit Zusatzzeile), Zahl, Pfeil.
  function zeile(e, aktion) {
    const b = el('button', e.unsicher ? 'wissen-zeile unsicher' : 'wissen-zeile');
    b.type = 'button';
    const text = el('span', 'zeile-text');
    text.append(el('span', 'zeile-name', e.name));
    if (e.zusatz || e.unsicher) text.append(el('span', 'zeile-zusatz', `${e.zusatz || ''}${e.unsicher ? (e.zusatz ? ' · ' : '') + 'unsicher' : ''}`));
    b.append(bild(e.typ, e.name), text);
    if (e.zahl !== undefined && e.zahl !== null) b.append(el('span', 'zeile-zahl', String(e.zahl)));
    b.append(el('span', 'pfeil'));
    b.addEventListener('click', aktion);
    return b;
  }
  const gruppenliste = (zeilen) => { const l = el('div', 'gruppenliste'); l.append(...zeilen); return l; };

  /* ---------- Fokus-Personen ---------- */
  let fokusListe = [];
  const fokusEintrag = (uuid) => fokusListe.find((p) => p.uuid === uuid);

  async function fokusLaden() {
    try { fokusListe = (await holen('/api/fokus')).personen || []; } catch (_) { /* ohne Fokus-Liste geht die Seite weiter */ }
  }

  async function fokusSchalten(uuid, an) {
    const r = await fetch(`/api/fokus/${encodeURIComponent(uuid)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ an }),
    });
    if (!r.ok) throw new Error('fokus');
    await fokusLaden();
  }

  const zeitVor = (iso) => {
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return '';
    const min = Math.max(0, Math.round((Date.now() - t) / 60000));
    if (min < 60) return min <= 1 ? 'gerade eben' : `vor ${min} Minuten`;
    if (min < 1440) return `vor ${Math.round(min / 60)} Stunden`;
    return vor(Math.round(min / 1440));
  };

  // „Im Fokus“ ganz oben auf der Startansicht: je Person Initialen, Name, jüngste Neuigkeit, „neu“ bis zum Antippen.
  function fokusZeigen() {
    const ziel = $('w-fokus');
    ziel.textContent = '';
    ziel.hidden = false;
    ziel.append(el('h2', '', 'Im Fokus'));
    if (!fokusListe.length) { ziel.append(el('p', 'leer-klein', 'Markiere Personen mit dem Stern auf ihrer Seite.')); return; }
    const l = el('div', 'gruppenliste');
    for (const p of fokusListe) {
      const z = zeile({ name: p.name, typ: 'Person', zusatz: [p.untertitel, p.neu.length ? p.neu[0].text : 'Noch nichts Neues'].filter(Boolean).join(' · ') }, () => seiteOeffnen(p.uuid));
      z.classList.add('fokus-zeile');
      if (p.neu.length) z.append(el('span', 'neu-marke', zeitVor(p.neu[0].zeit)));
      l.append(z);
    }
    ziel.append(l);
  }

  // Der Stern unter dem Namen einer Person: Tippen schaltet den Fokus.
  function fokusStern(d) {
    const an = !!fokusEintrag(d.uuid);
    const stern = el('button', an ? 'stern an' : 'stern');
    stern.type = 'button';
    stern.setAttribute('aria-pressed', String(an));
    stern.setAttribute('aria-label', an ? 'Aus dem Fokus nehmen' : 'In den Fokus nehmen');
    stern.textContent = an ? '★ Im Fokus' : '☆ In den Fokus';
    stern.addEventListener('click', async () => {
      stern.disabled = true;
      try { await fokusSchalten(d.uuid, !an); } catch (_) { stern.disabled = false; return; }
      seiteZeigen(d);
    });
    return stern;
  }

  // „Neu“ mit Quellenstand und „Jetzt nachsehen“ — nur für Personen im Fokus; steht nach dem Steckbrief und „Aktuell“.
  function fokusNeu(d) {
    const e = fokusEintrag(d.uuid);
    const neu = el('section', 'abschnitt');
    neu.append(el('h2', '', 'Neu'));
    if (e.neu.length) {
      const l = el('div', 'gruppenliste');
      for (const n of e.neu.slice(0, 8)) {
        const z = el('div', 'neu-eintrag');
        z.append(el('p', 'neu-text', n.text), el('span', 'neu-meta', `${n.quelle === 'mail' ? 'Mail' : 'WhatsApp'} · ${zeitVor(n.zeit)}`));
        l.append(z);
      }
      neu.append(l);
    } else {
      neu.append(el('p', 'leer-klein', e.quellen.letzter_lauf ? `Zuletzt nachgesehen ${zeitVor(e.quellen.letzter_lauf)}: nichts Neues.` : 'Noch nicht nachgesehen.'));
    }
    const stand = el('p', 'leer-klein');
    stand.textContent = [e.quellen.whatsapp, e.quellen.mail].join(' · ');
    const fehler = Object.values(e.quellen.fehler || {}).filter(Boolean);
    const meldung = el('p', 'leer-klein', fehler.length ? `Zuletzt gab es ein Problem: ${fehler[0]}` : '');
    const jetzt = knopf('Jetzt nachsehen', 'link', async (b) => {
      b.disabled = true;
      meldung.textContent = 'Friday sieht nach …';
      try {
        const r = await fetch(`/api/fokus/${encodeURIComponent(d.uuid)}/jetzt`, { method: 'POST' });
        if (r.status === 429) meldung.textContent = 'Gerade erst nachgesehen — in ein paar Minuten wieder.';
        else if (!r.ok) throw new Error('jetzt');
        else { await fokusLaden(); seiteZeigen(d); return; }
      } catch (_) { meldung.textContent = 'Das hat nicht geklappt.'; }
      b.disabled = false;
    });
    neu.append(stand, meldung, jetzt);
    // Namensgleichheit: In Gruppen schreibt jemand nur mit Vornamen. Eike sagt, ob das diese Person ist.
    const vorname = d.name.split(' ')[0];
    const zuordnen = async (u, an, b) => {
      b.disabled = true;
      try {
        const r = await fetch(`/api/fokus/${encodeURIComponent(d.uuid)}/zuordnung`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chat_id: u.chat_id, absender: u.absender, an }),
        });
        if (!r.ok) throw new Error('zuordnung');
        await fokusLaden();
        seiteZeigen(d);
      } catch (_) { b.disabled = false; meldung.textContent = 'Das hat nicht geklappt.'; }
    };
    if (e.unklar && e.unklar.length) {
      neu.append(el('h2', '', 'Unklare Absender'),
                 el('p', 'leer-klein', `In Gruppen schreibt jemand nur mit dem Vornamen „${vorname}“, und es gibt mehrere Menschen mit diesem Namen. Ist das ${d.name}?`));
      const l = el('div', 'gruppenliste');
      for (const u of e.unklar) {
        const z = el('div', 'neu-eintrag');
        z.append(el('p', 'neu-text', `„${u.absender}“ in ${u.chat} (${u.anzahl} ${u.anzahl === 1 ? 'Nachricht' : 'Nachrichten'})`),
                 knopf(`Ja, das ist ${vorname}`, 'link', (b) => zuordnen(u, true, b)));
        l.append(z);
      }
      neu.append(l);
    }
    if (e.zuordnung && e.zuordnung.length) {
      const z = el('details', 'alle');
      z.append(el('summary', '', `Zugeordnete Absender (${e.zuordnung.length})`));
      const l = el('div', 'gruppenliste');
      for (const u of e.zuordnung) {
        const zeile_ = el('div', 'neu-eintrag');
        zeile_.append(el('p', 'neu-text', `„${u.absender}“ in ${u.chat} ist ${d.name}`), knopf('Zuordnung entfernen', 'link', (b) => zuordnen(u, false, b)));
        l.append(zeile_);
      }
      z.append(l);
      neu.append(z);
    }
    return neu;
  }

  /* ---------- Wissen: Ansichten (Start, Antwort, Seite) ---------- */
  const seitenverlauf = [];                          // geöffnete Seiten; „Zurück“ geht eine Seite zurück, zuletzt zum Start
  function ansicht(welche) {
    $('w-start').hidden = welche !== 'start';
    $('w-antwort').hidden = welche !== 'antwort';
    $('w-seite').hidden = welche !== 'seite';
  }
  const zurueckKnopf = () => knopf(seitenverlauf.length > 1 ? '‹ Zurück' : '‹ Gedächtnis', 'w-zurueck', zurueck);
  function zurueck() {
    seitenverlauf.pop();
    if (seitenverlauf.length) seiteOeffnen(seitenverlauf[seitenverlauf.length - 1], true);
    else ansicht('start');
  }

  async function seiteHolen(uuid, modell) {
    try {
      const r = await fetch(`/api/gedaechtnis/seite/${encodeURIComponent(uuid)}?modell=${modell}`, { cache: 'no-store' });
      return r.ok ? await r.json() : null;
    } catch (_) { return null; }
  }

  // Erst sofort die gemerkte oder einfache Fassung zeigen, dann — falls nötig — die ausgearbeitete einsetzen.
  async function seiteOeffnen(uuid, zurueckGehen) {
    if (!zurueckGehen) seitenverlauf.push(uuid);
    frageNr++;                                       // eine laufende Antwort verwerfen
    ansicht('seite');
    const huelle = $('w-seite');
    huelle.textContent = '';
    huelle.append(zurueckKnopf(), el('p', 'leer-klein', 'Lädt …'));
    window.scrollTo({ top: 0 });
    const aktuell = () => seitenverlauf[seitenverlauf.length - 1] === uuid;
    const d = await seiteHolen(uuid, 0);
    if (!aktuell()) return;
    if (!d) { huelle.textContent = ''; huelle.append(zurueckKnopf(), el('p', 'leer-klein', 'Diese Seite lässt sich gerade nicht laden.')); return; }
    seiteZeigen(d);
    if (d.veredelt) return;
    const besser = await seiteHolen(uuid, 1);
    if (aktuell() && besser && besser.veredelt) seiteZeigen(besser);
  }

  const NOTIZ_STAND = { offen: 'wartet auf Verarbeitung', gesendet: 'wird verarbeitet', angekommen: 'im Gedächtnis', aufgegeben: 'ist nicht angekommen' };

  // Eikes eigene Angaben zu diesem Eintrag mit ihrem Stand — „im Gedächtnis“, sobald der Graph sie aufgenommen hat.
  function notizenListe(d) {
    const huelle = el('section', 'abschnitt');
    huelle.append(el('h2', '', 'Deine Angaben'));
    const l = el('div', 'gruppenliste');
    for (const n of d.notizen.slice(0, 8)) {
      const z = el('div', 'neu-eintrag');
      z.append(el('p', 'neu-text', n.text), el('span', 'neu-meta', `${NOTIZ_STAND[n.status] || n.status} · ${zeitVor(n.zeit)}`));
      l.append(z);
    }
    huelle.append(l);
    return huelle;
  }

  // Bearbeiten: Eike sagt etwas zu diesem Eintrag („Kontakt Bs Mutter heißt Kontakt H“). Es wird KEINE zweite Wahrheit neben dem
  // Graphen angelegt: Der Satz geht ins Notizbuch (wirkt sofort auf die Seite) und mit Vorrang als Episode an den Graphen.
  function bearbeiten(d) {
    const det = el('details', 'alle korrektur');
    det.append(el('summary', '', 'Bearbeiten'));
    const art = el('select', 'bearbeiten-art');
    art.setAttribute('aria-label', 'Was möchtest du sagen?');
    for (const [wert, text] of [['name', 'Der Name ist …'], ['angabe', 'Angabe hinzufügen'], ['frei', 'Etwas anderes sagen oder korrigieren']]) {
      const o = el('option', '', text);
      o.value = wert;
      art.append(o);
    }
    const wert = el('input'); wert.type = 'text'; wert.maxLength = 200; wert.placeholder = `Name, zum Beispiel „Kontakt H“`;
    const label = el('input'); label.type = 'text'; label.maxLength = 40; label.placeholder = 'Bezeichnung, zum Beispiel „Beruf“';
    const text = el('textarea', 'korrektur-feld'); text.rows = 3; text.maxLength = 600;
    text.placeholder = 'Was ist richtig? Zum Beispiel: „Kontakt R ist vor Jahren aus der Jugendfeuerwehr ausgetreten.“';
    const meldung = el('p', 'leer-klein');
    const anzeigen = () => {
      label.hidden = art.value !== 'angabe';
      wert.hidden = art.value === 'frei';
      text.hidden = art.value !== 'frei';
      wert.placeholder = art.value === 'name' ? 'Name, zum Beispiel „Kontakt H“' : 'Wert, zum Beispiel „Lehrerin“';
    };
    art.addEventListener('change', anzeigen);
    anzeigen();
    const speichern = knopf('Speichern', 'link', async (b) => {
      const koerper = { art: art.value, wert: wert.value, label: label.value, text: text.value };
      if ((art.value === 'frei' && !text.value.trim()) || (art.value !== 'frei' && !wert.value.trim()) || (art.value === 'angabe' && !label.value.trim())) {
        meldung.textContent = 'Bitte fülle das Feld aus.';
        return;
      }
      b.disabled = true;
      try {
        const r = await fetch(`/api/gedaechtnis/seite/${encodeURIComponent(d.uuid)}/notiz`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(koerper),
        });
        if (!r.ok) throw new Error('notiz');
        meldung.textContent = 'Gespeichert. Die Seite wird neu aufgebaut …';
        const neu = await seiteHolen(d.uuid, 1);
        if (seitenverlauf[seitenverlauf.length - 1] === d.uuid && neu) seiteZeigen(neu);
      } catch (_) { meldung.textContent = 'Das hat nicht geklappt. Versuch es gleich noch einmal.'; b.disabled = false; }
    });
    det.append(art, label, wert, text, speichern, meldung);
    return det;
  }

  function seiteZeigen(d) {
    const h = $('w-seite');
    h.textContent = '';
    h.append(zurueckKnopf());
    const kopf = el('header', 'seitenkopf');
    const titel = el('div', 'seitentitel');
    titel.append(el('h1', '', d.titel || d.name));
    if (d.untertitel) titel.append(el('p', 'untertitel', d.untertitel));
    titel.append(el('p', 'seitenmeta', `${d.anzahl} ${d.anzahl === 1 ? 'Fakt' : 'Fakten'}`));
    kopf.append(bild(d.typ, d.name, 'gross'), titel);
    h.append(kopf);
    if (d.typ === 'Person') h.append(fokusStern(d));
    if (!d.veredelt && d.fakten.length) h.append(el('p', 'seitenhinweis', 'Friday liest nach und ordnet die Seite …'));
    if (d.bilder && d.bilder.length) {
      const reihe = el('div', 'bildreihe');
      for (const b of d.bilder) { const i = new Image(); i.src = bildUrl(b.id); i.alt = b.name || ''; reihe.append(i); }
      h.append(reihe);
    }
    // Person: Steckbrief (Eckdaten in Zeilen), dann „Aktuell“, dann „Neu“ (wenn im Fokus). Sonst „Das Wichtigste“.
    if (d.steckbrief && d.steckbrief.length) {
      const a = el('section', 'abschnitt');
      a.append(el('h2', '', 'Steckbrief'));
      const l = el('div', 'gruppenliste');
      for (const p of d.steckbrief) {
        const z = el('div', 'eckdatum');
        z.append(el('span', 'eckdatum-label', p.label), el('span', 'eckdatum-wert', p.wert));
        l.append(z);
      }
      a.append(l);
      h.append(a);
    }
    if (d.kernaussagen && d.kernaussagen.length) {
      const a = el('section', 'abschnitt');
      a.append(el('h2', '', d.typ === 'Person' ? 'Aktuell' : 'Das Wichtigste'));
      const l = el('div', 'gruppenliste');
      for (const t of d.kernaussagen) l.append(el('p', 'aussage', t));
      a.append(l);
      h.append(a);
    }
    if (d.typ === 'Person' && fokusEintrag(d.uuid)) h.append(fokusNeu(d));
    // Angaben nach Bereich getrennt: je Block eine Überschrift („Aktiver Dienst“, „Jugendfeuerwehr (früher)“) mit eigenen Zeilen.
    for (const blk of d.angaben || []) {
      const a = el('section', 'abschnitt');
      a.append(el('h2', '', blk.titel));
      const l = el('div', 'gruppenliste');
      for (const p of blk.zeilen) {
        const z = el('div', 'eckdatum');
        z.append(el('span', 'eckdatum-label', p.label), el('span', 'eckdatum-wert', p.wert));
        l.append(z);
      }
      a.append(l);
      h.append(a);
    }
    for (const g of d.gruppen) {
      const zeilen = g.eintraege.map((e) => zeile({ name: e.name, typ: e.typ, zusatz: e.zusatz, unsicher: e.unsicher }, () => seiteOeffnen(e.uuid)));
      if (g.weitere) {                               // vom Modell ausgelassen (unklar): eingeklappt, aber nicht verloren
        const det = el('details', 'alle');
        det.append(el('summary', '', `${g.titel} (${g.eintraege.length})`), gruppenliste(zeilen));
        h.append(det);
        continue;
      }
      const a = el('section', 'abschnitt');
      a.append(el('h2', '', g.titel), gruppenliste(zeilen));
      h.append(a);
    }
    if (d.fakten.length) {
      const det = el('details', 'alle');
      det.append(el('summary', '', `Alle Fakten (${d.fakten.length})`));
      const ul = el('ul', 'belege');
      for (const f of d.fakten) ul.append(beleg(f));
      det.append(ul);
      h.append(det);
    }
    if (d.notizen && d.notizen.length) h.append(notizenListe(d));
    h.append(bearbeiten(d));
    if (!d.kernaussagen.length && !(d.angaben || []).length && !d.gruppen.length && !d.fakten.length) h.append(el('p', 'leer-klein', 'Dazu ist noch nichts gespeichert.'));
  }

  /* ---------- Wissen: alle Einträge (unten, eingeklappt) ---------- */
  function entitaetKarte(e) {
    return zeile({ name: e.name, typ: e.typ, zahl: e.fakten }, () => seiteOeffnen(e.uuid));
  }

  function wissenZeigen() {
    const typen = $('w-typen'), liste = $('wissen-liste');
    typen.textContent = ''; liste.textContent = '';
    if (!wissen || !wissen.eingerichtet || wissen.erreichbar === false) return;
    if (!wissen.typen.length) return;
    if (!typAuswahl || !wissen.typen.some((t) => t.typ === typAuswahl)) typAuswahl = wissen.typen[0].typ;
    for (const t of wissen.typen) {
      const b = knopf(`${t.name} ${t.anzahl}`, t.typ === typAuswahl ? 'aktiv' : '', () => { typAuswahl = t.typ; wissenZeigen(); });
      typen.append(b);
    }
    liste.append(gruppenliste(wissen.entitaeten.filter((x) => x.typ === typAuswahl).map(entitaetKarte)));
  }

  function graphStand(g) {
    const ziel = $('g-graph-stand');
    if (!g || !g.eingerichtet) { ziel.textContent = ''; return; }
    const teile = [`${g.angekommen || 0} Gespräche gelernt`];
    if (g.gesendet) teile.push(`${g.gesendet} in Arbeit`);
    if (g.wartet) teile.push(`${g.wartet} warten`);
    if (g.aufgegeben) teile.push(`${g.aufgegeben} nicht angekommen`);
    ziel.textContent = teile.join(' · ');
  }

  /* ---------- Start: Profil, Wichtig, Abschnitte ---------- */
  const TYP_NAME = { Topic: 'Thema', Event: 'Ereignis', Organization: 'Organisation', Project: 'Projekt', Location: 'Ort', Person: 'Person' };
  const ABSCHNITTE = [['Location', 'Orte'], ['Organization', 'Organisationen'], ['Event', 'Ereignisse'], ['Topic', 'Themen'], ['Project', 'Projekte']];
  const ZEILEN_ZUERST = 4, KACHELN = 6;
  let startStand = '';
  const offen = new Set();                           // Abschnitte, die ganz aufgeklappt sind

  function vor(tage) {
    if (tage === null || tage === undefined) return '';
    if (tage <= 0) return 'heute';
    if (tage === 1) return 'gestern';
    if (tage < 14) return `vor ${tage} Tagen`;
    if (tage < 60) return `vor ${Math.round(tage / 7)} Wochen`;
    return `vor ${Math.round(tage / 30)} Monaten`;
  }
  const fakten_ = (n) => `${n} ${n === 1 ? 'Fakt' : 'Fakten'}`;

  function abschnitt(titel, eintraege, schluessel, zuerst) {
    const a = el('section', 'abschnitt');
    const kopf = el('div', 'abschnittskopf');
    kopf.append(el('h2', '', titel));
    const alle = offen.has(schluessel);
    if (eintraege.length > zuerst) {
      kopf.append(knopf(alle ? 'Weniger' : `Alle ${eintraege.length}`, 'link', () => {
        if (offen.has(schluessel)) offen.delete(schluessel); else offen.add(schluessel);
        startZeigen(letzteThemen);
      }));
    }
    a.append(kopf, gruppenliste((alle ? eintraege : eintraege.slice(0, zuerst))
      .map((e) => zeile(e, () => seiteOeffnen(e.uuid)))));
    return a;
  }

  let letzteThemen = { eingerichtet: true, erreichbar: true, themen: [] };
  function startZeigen(t) {
    letzteThemen = t;
    const stand = JSON.stringify([t, wissen && wissen.du, wissen && wissen.entitaeten.length, [...offen]]);
    if (stand === startStand) return;                // nichts Neues: nicht neu zeichnen (alle 15 s wird nachgesehen)
    startStand = stand;

    const du = $('w-du');
    du.textContent = '';
    du.hidden = !(wissen && wissen.du);
    if (wissen && wissen.du) {
      const text = el('span', 'profil-text');
      text.append(el('span', 'profil-name', 'Über dich'), el('span', 'profil-meta', fakten_(wissen.du.fakten)));
      du.append(avatar('Eike', 'gross'), text, el('span', 'pfeil'));
      du.onclick = () => seiteOeffnen(wissen.du.uuid);
    }

    const ziel = $('w-abschnitte');
    ziel.textContent = '';
    if (!t.eingerichtet) { ziel.append(el('p', 'leer-klein', 'Das Langzeitgedächtnis ist nicht eingerichtet.')); return; }
    if (t.erreichbar === false) { ziel.append(el('p', 'leer-klein', 'Das Langzeitgedächtnis antwortet gerade nicht.')); return; }

    // Wichtig: die obersten Themen als Kacheln (Name, Art, Zahl der Fakten — ohne Text).
    const wichtig = t.themen.slice(0, KACHELN);
    if (wichtig.length) {
      const a = el('section', 'abschnitt');
      a.append(el('h2', '', 'Wichtig'));
      const raster = el('div', 'kacheln');
      for (const x of wichtig) {
        const k = el('button', 'kachel');
        k.type = 'button';
        k.append(symbol(x.typ), el('span', 'kachel-name', x.name),
                 el('span', 'kachel-meta', `${TYP_NAME[x.typ] || 'Thema'} · ${fakten_(x.fakten)}`));
        k.addEventListener('click', () => seiteOeffnen(x.uuid));
        raster.append(k);
      }
      a.append(raster);
      ziel.append(a);
    }
    const inWichtig = new Set(wichtig.map((x) => x.uuid));

    const personen = ((wissen && wissen.entitaeten) || []).filter((e) => e.typ === 'Person' && e.fakten > 0)
      .sort((x, y) => y.fakten - x.fakten).map((e) => ({ uuid: e.uuid, name: e.name, typ: 'Person', zahl: e.fakten }));
    if (personen.length) ziel.append(abschnitt('Personen', personen, 'Person', 5));

    // Je Art ein Abschnitt, die mit dem wichtigsten Eintrag zuerst.
    const arten = ABSCHNITTE.map(([typ, titel]) => ({
      typ, titel, eintraege: t.themen.filter((x) => x.typ === typ && !inWichtig.has(x.uuid))
        .map((x) => ({ uuid: x.uuid, name: x.name, typ, zahl: x.fakten })),
      beste: Math.max(0, ...t.themen.filter((x) => x.typ === typ).map((x) => x.wichtigkeit)),
    })).filter((a) => a.eintraege.length).sort((x, y) => y.beste - x.beste);
    for (const a of arten) ziel.append(abschnitt(a.titel, a.eintraege, a.typ, ZEILEN_ZUERST));
    if (!wichtig.length && !personen.length) ziel.append(el('p', 'leer-klein', 'Noch nichts gelernt. Das kommt nach deinen ersten Gesprächen mit Friday.'));
  }

  function beleg(b) {
    const li = el('li', b.bis ? 'beleg ueberholt' : 'beleg');
    const meta = [];
    if (b.anzahl > 1) meta.push(`${b.anzahl} Mal so gesagt`);
    if (b.bis) meta.push(`galt bis ${b.bis}`); else if (b.seit) meta.push(`seit ${b.seit}`);
    li.append(el('span', 'beleg-text', b.fakt));
    if (meta.length) li.append(el('span', 'beleg-meta', meta.join(' · ')));
    return li;
  }

  function antwortZeigen(d) {
    const lead = $('w-lead'), haupt = $('w-belege'), weitere = $('w-weitere'), mehr = $('w-mehr');
    haupt.textContent = ''; weitere.textContent = ''; weitere.hidden = true; mehr.hidden = true;
    if (d.quelle === 'modell') { lead.className = 'lead'; lead.textContent = d.antwort; }
    else if (d.quelle === 'leer') { lead.className = 'lead leer'; lead.textContent = d.antwort; }
    else { lead.className = 'lead leer'; lead.textContent = 'Gerade keine Zusammenfassung möglich. Unten stehen die gespeicherten Fakten dazu.'; }
    // Mit Antwortsatz: die Fakten, auf die sie sich stützt; der Rest ist eingeklappt. Ohne Antwortsatz: die ersten acht.
    const genutzt = d.belege.filter((b) => b.genutzt);
    const oben = d.quelle === 'modell' ? genutzt : d.belege.slice(0, 8);
    const rest = d.quelle === 'modell' ? d.belege.filter((b) => !b.genutzt) : d.belege.slice(8);
    $('w-belege-abschnitt').hidden = !(oben.length || rest.length);
    for (const b of oben) haupt.append(beleg(b));
    if (!rest.length) return;
    for (const b of rest) weitere.append(beleg(b));
    const text = (offen) => (oben.length ? `Weitere Treffer ${offen ? 'ausblenden' : 'anzeigen'} (${rest.length})` : `Gefundene Fakten ${offen ? 'ausblenden' : 'anzeigen'} (${rest.length})`);
    mehr.textContent = text(false);
    mehr.hidden = false;
    mehr.onclick = () => { weitere.hidden = !weitere.hidden; mehr.textContent = text(!weitere.hidden); };
  }

  function trefferZeigen(treffer) {
    const ziel = $('w-treffer');
    ziel.textContent = '';
    ziel.hidden = !treffer.length;
    if (!treffer.length) return;
    ziel.append(el('h2', '', 'Passende Einträge'),
                gruppenliste(treffer.map((t) => zeile({ name: t.name, typ: t.typ, zahl: t.zahl }, () => seiteOeffnen(t.uuid)))));
  }

  let frageNr = 0;
  async function fragen(text) {
    const frage = (text || '').trim();
    if (!frage) return;
    $('g-graph-frage').value = frage;
    const nr = ++frageNr;
    ansicht('antwort');
    $('w-frage').textContent = `Suche nach „${frage}“`;
    const lead = $('w-lead');
    lead.className = 'lead lade';
    lead.textContent = 'Friday sieht nach …';
    $('w-treffer').hidden = true; $('w-belege-abschnitt').hidden = true;
    $('w-belege').textContent = ''; $('w-weitere').textContent = ''; $('w-weitere').hidden = true; $('w-mehr').hidden = true;
    // 1. Passende Einträge — schnell. Eindeutig? Dann gleich die Seite öffnen statt Blocktext zu zeigen.
    try {
      const t = await holen(`/api/gedaechtnis/treffer?frage=${encodeURIComponent(frage)}`);
      if (nr !== frageNr) return;
      if (t.oeffnen) { seiteOeffnen(t.oeffnen); return; }
      trefferZeigen(t.treffer || []);
    } catch (_) { /* ohne Trefferliste geht es weiter */ }
    // 2. Die Antwort aus den Fakten — braucht das Modell und dauert etwas.
    let d = null;
    try { d = await holen(`/api/gedaechtnis/antwort?frage=${encodeURIComponent(frage)}`); } catch (_) { d = null; }
    if (nr !== frageNr) return;                      // inzwischen wurde etwas anderes gefragt
    if (!d || !Array.isArray(d.belege)) { lead.className = 'lead leer'; lead.textContent = 'Das Gedächtnis antwortet gerade nicht. Versuch es gleich noch einmal.'; return; }
    antwortZeigen(d);
  }

  function zuDenThemen() {
    frageNr++;                                       // eine laufende Antwort verwerfen
    seitenverlauf.length = 0;
    ansicht('start');
    $('g-graph-frage').value = '';
  }

  $('g-graph-form').addEventListener('submit', (e) => { e.preventDefault(); fragen($('g-graph-frage').value); });
  $('w-zurueck').addEventListener('click', zuDenThemen);

  /* ---------- Notizbuch ---------- */
  function fakten(liste) {
    const ziel = $('g-fakten');
    ziel.textContent = '';
    if (!liste.length) { ziel.append(el('p', 'leer-klein', 'Noch nichts gemerkt.')); return; }
    for (const f of liste) {
      const e = el('div', 'eintrag');
      const kopf = el('div', 'kopfzeile');
      kopf.append(el('span', 'titel', f.thema), el('span', 'klein', `seit ${f.seit}`));
      const details = el('div', 'details');
      details.hidden = true;
      const knoepfe = el('div', 'knoepfe');
      knoepfe.append(
        knopf('Verlauf', '', async () => {
          if (!details.hidden) { details.hidden = true; return; }
          details.textContent = '';
          for (const v of await holen(`/api/gedaechtnis/historie?thema=${encodeURIComponent(f.thema)}`)) {
            details.append(el('div', '', `${v.von} – ${v.bis || 'gilt'}: ${v.inhalt}  (${v.quelle})`));
          }
          details.hidden = false;
        }),
        knopf('Vergessen', 'weg', async () => {
          if (!confirm(`„${f.thema}" vergessen? Der Verlauf bleibt nachlesbar.`)) return;
          await senden('/api/gedaechtnis/vergessen', { thema: f.thema });
          laden();
        }),
      );
      e.append(kopf, el('div', 'text', f.inhalt), knoepfe, details);
      ziel.append(e);
    }
  }

  function vorgaben(liste) {
    const ziel = $('g-vorgaben');
    ziel.textContent = '';
    if (!liste.length) { ziel.append(el('p', 'leer-klein', 'Keine Vorgaben.')); return; }
    for (const v of liste) {
      const e = el('div', 'eintrag');
      const kopf = el('div', 'kopfzeile');
      kopf.append(el('span', 'titel', v.bereich), el('span', 'klein', `Nr. ${v.id}`));
      const knoepfe = el('div', 'knoepfe');
      knoepfe.append(knopf('Ändern', '', () => vorgabeBearbeiten(v)), knopf('Widerrufen', 'weg', async () => {
        if (!confirm('Diese Vorgabe widerrufen? Sie bleibt im Verlauf, gilt aber nicht mehr.')) return;
        await senden(`/api/vorgaben/${v.id}/widerrufen`, {});
        laden();
      }));
      e.append(kopf, el('div', 'text', v.text), knoepfe);
      ziel.append(e);
    }
  }

  // Anlegen und Ändern per Formular (POST /api/vorgaben, /api/vorgaben/<id>/aendern): serverseitig dieselben Prüfungen wie die
  // Werkzeuge; eine Absage steht im Klartext unter dem Formular. Ändern ersetzt — die alte Vorgabe bleibt im Verlauf.
  function vorgabeFormZuruecksetzen() {
    $('g-vorgabe-id').value = ''; $('g-vorgabe-text').value = ''; $('g-vorgabe-bereich').value = 'allgemein';
    $('g-vorgabe-titel').textContent = 'Neue Vorgabe'; $('g-vorgabe-ok').textContent = 'Anlegen';
    $('g-vorgabe-abbruch').hidden = true;
  }
  function vorgabeBearbeiten(v) {
    $('g-vorgabe-id').value = String(v.id); $('g-vorgabe-text').value = v.text; $('g-vorgabe-bereich').value = v.bereich;
    $('g-vorgabe-titel').textContent = `Vorgabe Nr. ${v.id} ändern`; $('g-vorgabe-ok').textContent = 'Ändern';
    $('g-vorgabe-abbruch').hidden = false; $('g-vorgabe-status').hidden = true;
    $('g-vorgabe-text').focus();
  }
  $('g-vorgabe-abbruch').addEventListener('click', () => { vorgabeFormZuruecksetzen(); $('g-vorgabe-status').hidden = true; });
  $('g-vorgabe-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const status = $('g-vorgabe-status'), id = $('g-vorgabe-id').value;
    const text = $('g-vorgabe-text').value.trim();
    if (!text) { status.hidden = false; status.textContent = 'Eine Vorgabe braucht einen Text.'; return; }
    const url = id ? `/api/vorgaben/${encodeURIComponent(id)}/aendern` : '/api/vorgaben';
    try {
      const r = await senden(url, { text, bereich: $('g-vorgabe-bereich').value });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        status.hidden = false; status.textContent = d.detail || `Fehler ${r.status}`;
        return;
      }
    } catch (_) { status.hidden = false; status.textContent = 'Das ging gerade nicht.'; return; }
    status.hidden = true;
    vorgabeFormZuruecksetzen();
    laden();
  });

  $('g-verdichten').addEventListener('click', async () => {
    const status = $('g-verdichten-status');
    status.hidden = false; status.className = 'ausgabe'; status.textContent = 'Friday sucht Doppeltes …';
    try {
      const r = await (await senden('/api/gedaechtnis/verdichten', {})).json();
      if (!r.anzahl) { status.textContent = 'Nichts zusammenzuführen — das Notizbuch ist schon aufgeräumt.'; }
      else {
        status.className = 'ausgabe status-ok';
        status.textContent = r.zusammengefuehrt.map((g) => `„${g.thema}“ ← ${g.ersetzt.join(', ')}`).join(' · ');
        laden();
      }
    } catch (_) { status.className = 'ausgabe status-fehler'; status.textContent = 'Das ging gerade nicht.'; }
  });

  $('g-neu').addEventListener('submit', async (e) => {
    e.preventDefault();
    const thema = $('g-thema').value.trim(), inhalt = $('g-inhalt').value.trim();
    if (!thema || !inhalt) return;
    await senden('/api/gedaechtnis/fakten', { thema, inhalt });
    $('g-thema').value = '';
    $('g-inhalt').value = '';
    laden();
  });

  /* ---------- Bilder ---------- */
  function bilderZeigen(liste) {
    const raster = $('b-raster');
    raster.textContent = '';
    if (!liste.length) { raster.append(el('p', 'leer-klein', 'Noch keine Bilder. Zeig Friday eins im Chat oder beim Sprechen.')); return; }
    for (const b of liste) {
      const k = el('button', 'kachel');
      k.type = 'button';
      const i = new Image(); i.src = bildUrl(b.id); i.alt = b.name || 'Bild'; i.loading = 'lazy';
      k.append(i, el('span', '', b.name || b.zeit));
      k.addEventListener('click', () => bildDetail(b));
      raster.append(k);
    }
  }

  function bildDetail(b) {
    const d = $('b-detail');
    d.hidden = false;
    d.textContent = '';
    d.append(knopf('← zurück', 'zurueck', () => { d.hidden = true; }));
    const i = new Image(); i.src = bildUrl(b.id); i.className = 'gross'; i.alt = b.name || 'Bild';
    d.append(el('h3', '', b.name || 'Ohne Namen'), i, el('div', 'klein', b.zeit));
    if (b.beschreibung) d.append(el('div', 'text', b.beschreibung));
    const form = el('form', 'neu-fakt');
    const feld = el('input');
    feld.type = 'text'; feld.maxLength = 200; feld.placeholder = 'Wer oder was ist das? z.B. Kontakt Q'; feld.value = b.name || '';
    const ok = el('button', '', 'Zuordnen'); ok.type = 'submit';
    form.append(feld, ok);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!feld.value.trim()) return;
      await senden(`/api/bilder/${encodeURIComponent(b.id)}/name`, { name: feld.value.trim() });
      d.hidden = true;
      laden();
    });
    d.append(form);

    // Entfernen: erst fragen, dann endgültig. Löscht nur das Bild; was Friday sich über den Namen gemerkt hat, bleibt im Wissen.
    const weg = el('div', 'knoepfe bild-entfernen');
    const entfernen = knopf('Bild entfernen', 'weg', () => {
      entfernen.hidden = true;
      const frage = el('div', 'eintrag');
      frage.append(el('div', 'titel', 'Dieses Bild endgültig entfernen?'),
                   el('div', 'klein', 'Es ist danach weg und lässt sich nicht wiederherstellen. Was Friday sich zum Namen gemerkt hat, bleibt im Wissen.'));
      const reihe = el('div', 'knoepfe');
      reihe.append(knopf('Endgültig entfernen', 'weg', async (b) => {
        b.disabled = true;
        const r = await fetch(`/api/bilder/${encodeURIComponent(b.dataset.id)}`, { method: 'DELETE' });
        if (!r.ok) { b.disabled = false; frage.append(el('div', 'klein', 'Das hat nicht geklappt.')); return; }
        d.hidden = true;
        laden();
      }), knopf('Abbrechen', '', () => { frage.remove(); entfernen.hidden = false; }));
      reihe.firstChild.dataset.id = b.id;
      frage.append(reihe);
      weg.append(frage);
    });
    weg.append(entfernen);
    d.append(weg);
    d.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  /* ---------- Gespräche ---------- */
  function gespraeche(liste) {
    const ziel = $('g-gespraeche');
    ziel.textContent = '';
    if (!liste.length) { ziel.append(el('p', 'leer-klein', 'Noch keine Gespräche.')); return; }
    for (const g of liste) {
      const e = el('div', 'eintrag klickbar');
      const kopf = el('div', 'kopfzeile');
      kopf.append(el('span', 'titel', `${g.beginn} · ${g.art === 'sprache' ? 'Sprache' : g.art === 'snapchat' ? 'Snapchat' : 'Chat'}${g.person ? ` · mit ${g.person}` : ''}`),
                  el('span', 'klein', `${g.beitraege} Beiträge`));
      const text = el('div', 'text', g.zusammenfassung || 'noch ohne Zusammenfassung');
      if (!g.zusammenfassung) text.classList.add('klein');
      const details = el('div', 'details');
      details.hidden = true;
      e.addEventListener('click', async () => {
        if (!details.hidden) { details.hidden = true; return; }
        details.textContent = '';
        for (const b of await holen(`/api/gespraeche/${g.id}`)) {
          const wer = b.rolle === 'du' ? (g.person || 'Du') : b.rolle === 'friday' ? 'Friday' : '·';
          details.append(el('div', '', `${b.zeit.slice(-5)}  ${wer}: ${b.text}`));
        }
        details.hidden = false;
      });
      e.append(kopf, text, details);
      ziel.append(e);
    }
  }

  /* ---------- Gedanken ---------- */
  const ART = { erkenntnis: 'Erkenntnis', verbindung: 'Verbindung', luecke: 'Lücke', frage: 'Frage' };
  function faedenZeigen(liste) {
    const ziel = $('gd-faeden');
    ziel.textContent = '';
    if (!liste.length) { ziel.append(el('p', 'leer-klein', 'Noch keine Fäden.')); return; }
    for (const f of liste) {
      const e = el('div', 'eintrag');
      const kopf = el('div', 'kopfzeile');
      const hinweise = [f.status, `${f.schritte} Schritte`, f.quelle === 'eike' ? 'von dir' : 'von Friday',
                        f.fremd ? 'gelesen, unbestätigt' : ''].filter(Boolean).join(' · ');
      kopf.append(el('span', 'titel', f.titel), el('span', 'klein', hinweise));
      e.append(kopf, el('div', 'text', f.aufgabe));
      if (f.ergebnis) e.append(el('div', 'text', `Ergebnis: ${f.ergebnis}`));
      else if (f.stand) e.append(el('div', 'klein', `Stand: ${f.stand}`));
      const aktionen = el('div', 'kopfzeile');
      for (const [status, text] of [['offen', 'Öffnen'], ['ruht', 'Ruhen lassen'], ['erledigt', 'Abschließen']]) {
        if (status === f.status) continue;
        aktionen.append(knopf(text, '', async () => {
          const r = await (await senden(`/api/gedanken/faeden/${f.id}`, { status })).json();
          $('gd-stand').textContent = r.ok ? '' : (r.grund || 'Das ging nicht.');
          laden();
        }));
      }
      e.append(aktionen);
      ziel.append(e);
    }
  }
  $('gd-faden-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const titel = $('gd-faden-titel').value.trim();
    const aufgabe = $('gd-faden-aufgabe').value.trim();
    if (!titel || !aufgabe) { $('gd-stand').textContent = 'Titel und Aufgabe fehlen.'; return; }
    const r = await (await senden('/api/gedanken/faeden', { titel, aufgabe })).json();
    $('gd-stand').textContent = r.ok ? 'Faden angelegt. Friday denkt in der nächsten stillen Runde darüber nach.' : (r.grund || 'Das ging nicht.');
    if (r.ok) { $('gd-faden-titel').value = ''; $('gd-faden-aufgabe').value = ''; }
    laden();
  });
  function fragenZeigen(d) {
    const ziel = $('gd-fragen');
    // Nicht neu zeichnen, solange sich nichts geändert hat oder Eike gerade tippt (laden() läuft alle 15 s): sonst ist sein Text weg.
    const sig = d.offen.map((f) => f.id).join(',') + '|' + d.beantwortet.map((f) => f.id).join(',');
    if (ziel.dataset.sig === sig) return;
    if (ziel.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;
    ziel.dataset.sig = sig;
    ziel.textContent = '';
    if (!d.offen.length && !d.beantwortet.length) { ziel.append(el('p', 'leer-klein', 'Friday hat gerade keine Fragen.')); return; }
    for (const f of d.offen) {
      const e = el('div', 'eintrag');
      e.append(el('div', 'titel', f.titel), el('div', 'text', f.text));
      const zeile = el('div', 'kopfzeile');
      const felder = [];
      const antworten = async (daten) => {
        for (const x of felder) x.disabled = true;           // kein doppeltes Absenden
        const r = await senden(`/api/fragen/${f.id}/antwort`, daten);
        $('gd-stand').textContent = r.ok ? 'Danke, ich mache weiter.' : ((await r.json().catch(() => ({}))).detail || 'Das ging nicht.');
        laden();
      };
      f.optionen.forEach((o, i) => {
        const k = knopf(o, '', () => antworten({ option: i + 1 }));
        felder.push(k);
        zeile.append(k);
      });
      const eigene = document.createElement('input');
      eigene.type = 'text'; eigene.maxLength = 300; eigene.placeholder = 'Eigene Antwort'; eigene.setAttribute('aria-label', 'Eigene Antwort');
      const los = knopf('Senden', '', () => { const t = eigene.value.trim(); if (t) antworten({ text: t }); });
      felder.push(eigene, los);
      e.append(zeile, el('div', 'kopfzeile'));
      e.lastChild.append(eigene, los);
      ziel.append(e);
    }
    for (const f of d.beantwortet) {
      const e = el('div', 'eintrag');
      e.append(el('div', 'klein', `Beantwortet: ${f.titel} → ${f.antwort}`));
      ziel.append(e);
    }
  }
  function gedankenZeigen(d) {
    faedenZeigen(d.faeden || []);
    $('gd-an').checked = d.an;
    $('gd-backend').value = d.backend || 'gemini';
    $('gd-backend-hinweis').hidden = d.backend !== 'opencode';
    const liste = $('gd-liste');
    liste.textContent = '';
    if (!d.gedanken.length) liste.append(el('p', 'leer-klein', 'Noch keine Gedanken.'));
    for (const g of d.gedanken) {
      const e = el('div', 'eintrag');
      const kopf = el('div', 'kopfzeile');
      const hinweise = [g.sicherheit, g.fremd ? 'gelesen, nicht bestätigt' : ''].filter(Boolean).join(' · ');
      kopf.append(el('span', 'titel', `${g.zeit.slice(0, 16).replace('T', ' ')} · ${ART[g.art] || g.art}`),
                  el('span', 'klein', hinweise));
      e.append(kopf, el('div', 'text', g.text));
      if (g.quellen.length) e.append(el('div', 'klein', `Quellen: ${g.quellen.join('; ')}`));
      liste.append(e);
    }
    const runden = $('gd-runden');
    runden.textContent = '';
    for (const r of d.runden) {
      const e = el('div', 'eintrag');
      const kopf = el('div', 'kopfzeile');
      // Backend je Runde: Runden tragen modell „opencode:<modell>“ oder „deepseek:<modell>“, alles andere ist Gemini
      const backend = (r.modell || '').startsWith('opencode:') ? `OpenCode (${r.modell.slice(9)})` : (r.modell || '').startsWith('deepseek:') ? `DeepSeek (${r.modell.slice(9)})` : 'Gemini';
      kopf.append(el('span', 'titel', `${r.start.slice(0, 16).replace('T', ' ')} · ${backend}`),
                  el('span', 'klein', r.fehler ? 'Fehler' : `${r.gedanken} Gedanken`));
      e.append(kopf);
      if (r.fehler) e.append(el('div', 'klein', r.fehler));
      else if (r.werkzeuge) {
        e.append(el('div', 'klein', `${(r.modell || '').startsWith('opencode:') ? 'Gespeichert über' : 'Werkzeuge'}: ${r.werkzeuge}`));
      }
      runden.append(e);
    }
  }
  $('gd-an').addEventListener('change', async (ev) => {
    await senden('/api/gedanken/einstellung', { an: ev.target.checked });
  });
  $('gd-backend').addEventListener('change', async (ev) => {
    const r = await senden('/api/gedanken/backend', { backend: ev.target.value });
    const d = r.ok ? await r.json() : null;
    $('gd-stand').textContent = d ? ({ opencode: 'Die nächsten Runden denken mit OpenCode.', deepseek: 'Die nächsten Runden denken mit DeepSeek.' }[d.backend] || 'Die nächsten Runden denken mit Gemini.')
                                  : 'Das ließ sich nicht umstellen.';
    laden();
  });
  $('gd-jetzt').addEventListener('click', async (ev) => {
    ev.target.disabled = true;
    const r = await (await senden('/api/gedanken/jetzt', {})).json();
    $('gd-stand').textContent = r.gestartet ? 'Friday denkt nach … lade in einer Minute neu.' : r.grund;
    setTimeout(() => { ev.target.disabled = false; }, 5000);
  });

  /* ---------- Laden ---------- */
  async function laden() {
    try {
      const d = await holen('/api/gedaechtnis');
      vorschlaege(d.vorschlaege);
      graphStand(d.graph);
      if (reiter === 'notizbuch') { fakten(d.fakten); vorgaben((await holen('/api/vorgaben')).aktive); }
      if (reiter === 'gespraeche') gespraeche(d.gespraeche);
      if (reiter === 'gedanken') { gedankenZeigen(await holen('/api/gedanken')); fragenZeigen(await holen('/api/fragen')); }
      if (reiter === 'wissen') {
        const [w, t] = await Promise.all([holen('/api/gedaechtnis/wissen'), holen('/api/gedaechtnis/themen'), fokusLaden()]);
        wissen = w; wissenZeigen(); startZeigen(t); fokusZeigen();
      }
      if (reiter === 'bilder') bilderZeigen(await holen('/api/bilder'));
    } catch (_) {
      $('g-graph-stand').textContent = 'Das Gedächtnis ist gerade nicht erreichbar.';
    }
  }

  // Das Gedächtnis wächst im Hintergrund weiter (Rückblick, Chat-Auswertung).
  // Deshalb regelmäßig nachladen, solange die Seite sichtbar ist, und sofort,
  // wenn die App wieder in den Vordergrund kommt — sonst steht ein alter Stand da.
  const sichtbar = () => location.hash === '#gedaechtnis' && document.visibilityState === 'visible';
  const beiAnzeige = () => { if (sichtbar()) laden(); };
  window.addEventListener('hashchange', beiAnzeige);
  document.addEventListener('visibilitychange', beiAnzeige);
  setInterval(beiAnzeige, 15000);
  beiAnzeige();
})();
