/* Kontakte — Extras → Kontakte: Personen, mit denen Friday schreibt (Persona, Werkzeuge, Pausieren).

   Alles per textContent (Namen und Persona sind Fremdtext für die Seite). Unbekannte Kontakte stehen oben und
   werden nie beantwortet, bis sie hier angelegt sind. Kein Schlüssel und keine Chatinhalte kommen hierher. */
(() => {
  const wurzel = document.getElementById('kontakte-inhalt');
  if (!wurzel) return;
  const el = (tag, klasse, text) => {
    const e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };
  const knopf = (text, klasse, aktion) => {
    const b = el('button', klasse, text);
    b.type = 'button';
    b.addEventListener('click', () => aktion(b));
    return b;
  };
  const senden = async (url, daten) => {
    try {
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                   body: JSON.stringify(daten ?? {}) });
      const d = await r.json().catch(() => ({}));
      return { ok: r.ok && d.ok !== false, text: d.text || d.detail || (r.ok ? '' : `Fehler ${r.status}`), ...d };
    } catch (_) { return { ok: false, text: 'Friday ist nicht erreichbar.' }; }
  };

  let meldung = '';
  let offen = null;            // id der aufgeklappten Person
  let suche = '';              // Suchtext und Filter bleiben beim Neuzeichnen
  let filter = 'alle';
  const entwurf = {};          // ungespeicherte Eingaben je Person: { name, snapchat, persona }
  let dialog = null;           // „Person anlegen“ offen: { snapchat, gewaehlt, eintippen }
  let bekannt = null;          // bekannte Personen { personen, ausgefallen }; null = lädt
  let daten = { personen: [], unbekannt: [], werkzeuge: [] };

  const feld = (beschriftung, eingabe) => {
    const z = el('label', 'ko-feld');
    z.append(el('span', 'ko-beschr', beschriftung), eingabe);
    return z;
  };
  const textfeld = (wert, platzhalter) => {
    const i = el('input');
    i.type = 'text';
    i.value = wert || '';
    i.placeholder = platzhalter || '';
    i.maxLength = 200;
    return i;
  };

  async function speichern(url, koerper) {
    const r = await senden(url, koerper);
    meldung = r.ok ? '' : r.text;
    if (r.ok) dialog = null;
    await laden();
    return r;
  }

  const vor = (epoche) => {
    if (!epoche) return '';
    const s = Math.max(0, Date.now() / 1000 - epoche);
    if (s < 90) return 'gerade eben';
    if (s < 3600) return `vor ${Math.round(s / 60)} Min.`;
    if (s < 86400) return `vor ${Math.round(s / 3600)} Std.`;
    const t = Math.round(s / 86400);
    return t === 1 ? 'gestern' : `vor ${t} Tagen`;
  };
  const austauschText = (a) => {
    if (!a || !a.zeit) return 'noch kein Austausch in Snapchat';
    return `letzter Austausch ${vor(a.zeit)} (${a.von === 'person' ? 'schrieb sie/er' : 'schrieb Friday'})`;
  };
  const initialen = (name) => (name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
  const pille = (st) => {
    const s = st || { stufe: 'grau', titel: 'unbekannt', grund: '' };
    const p = el('span', `ko-pille ko-${s.stufe}`, s.titel);
    p.title = s.grund || '';
    return p;
  };

  // Grundfunktionen: jede Person hat sie immer, es gibt dafür keinen Hebel — nur Information zum Ausklappen.
  function grundBlock() {
    const d = el('details', 'ko-info');
    d.append(el('summary', 'ko-klein', 'Das kann jede Person immer (Grundfunktionen)'));
    d.append(el('p', 'ko-beschr', 'Jede Person bekommt eine einfache Friday, die plaudert und weiß, wer sie ist. Dazu gehört immer:'));
    const liste = el('ul', 'ko-liste');
    for (const w of daten.werkzeuge) liste.append(el('li', '', w.text));
    d.append(liste);
    d.append(el('p', 'ko-beschr', 'Nie dabei: deine Mails, Dateien, Nachrichten, dein Standort oder Kalender — außer du schaltest unten ein Modul dazu.'));
    return d;
  }

  const schalter = (p, schluessel, name, text, extra) => {
    const m = p.module || {};
    const z = el('label', 'ko-schalter' + (m[schluessel] ? ' ko-an' : ''));
    const h = el('input');
    h.type = 'checkbox';
    h.checked = !!m[schluessel];
    h.addEventListener('change', () => speichern(`/api/kontakte/${p.id}`, { module: { [schluessel]: h.checked } }));
    const t = el('span', 'ko-schalter-text');
    t.append(el('strong', '', name), document.createTextNode(` — ${text}`));
    z.append(h, t);
    if (extra) z.append(extra);
    return z;
  };

  const modulAuswahl = (p, schluessel, name, werte) => {
    const m = p.module || {};
    const z = el('label', 'ko-schalter ko-auswahl');
    const s = el('select');
    for (const [wert, beschriftung] of werte) {
      const o = el('option', '', beschriftung);
      o.value = wert;
      o.selected = m[schluessel] === wert;
      s.append(o);
    }
    s.addEventListener('change', () => speichern(`/api/kontakte/${p.id}`, { module: { [schluessel]: s.value } }));
    z.append(el('span', 'ko-schalter-text', name), s);
    return z;
  };

  // Module: was Friday für diese Person zusätzlich kann. Jeder Schalter speichert sofort.
  function modulBlock(p) {
    const b = el('div', 'ko-gruppe');
    b.append(el('h3', 'ko-unter', 'Module'));
    b.append(el('p', 'ko-beschr', 'Fähigkeiten, die du für diese Person dazuschaltest. Sie lassen sich beliebig kombinieren.'));
    const da = new Set(p.module_verfuegbar || []);
    const wirksam = new Set(p.module_wirksam || []);
    b.append(modulAuswahl(p, 'plan', 'Eikes Plan', [['aus', 'aus'], ['frei', 'nur ob du Zeit hast'], ['voll', 'Stundenplan und Kalender']]));
    const g = modulAuswahl(p, 'garantie', 'Garantie', [['auto', p.garantie_profil ? 'automatisch (hat Profil)' : 'automatisch (kein Profil)'], ['an', 'an'], ['aus', 'aus']]);
    if (!p.garantie_profil) g.append(el('span', 'ko-chip', 'braucht Garantie-Profil'));
    if (wirksam.has('garantie')) g.append(el('span', 'ko-chip', 'wirkt'));
    b.append(g);
    b.append(schalter(p, 'medien', 'Medien-Wunsch', 'Filme und Serien wünschen (zusätzlich zur Arbeitsweise).'));
    if (da.has('snap_wuensche')) b.append(schalter(p, 'snap_wuensche', 'Snap-Wünsche', 'ein Bild als Snap wünschen (nur an sie, höchstens 5 am Tag).'));
    b.append(schalter(p, 'ueberwachung', 'Aktive Überwachung', 'Du bekommst eine Meldung mit dem Inhalt, wenn diese Person Friday schreibt. Standard: aus.'));
    b.append(arbeitsweisenBlock(p));
    return b;
  }

  // Flammen: eigener Bereich, weil Friday hier von sich aus schreibt.
  function flammenBlock(p) {
    const b = el('div', 'ko-gruppe');
    b.append(el('h3', 'ko-unter', 'Flammen'));
    b.append(el('p', 'ko-beschr', 'Friday schickt dieser Person einmal am Tag einen Snap, damit die Flamme nicht erlischt, und ermutigt sie freundlich, dabeizubleiben.'));
    const modus = p.flammen_modus;
    const hinweis = modus === 'an' ? null : el('span', 'ko-chip', modus === 'trocken' ? 'Probelauf: nur Protokoll' : 'Lauf ist noch aus');
    b.append(schalter(p, 'flammen', 'Flamme halten', 'täglich ein lustiger Snap.', hinweis));
    return b;
  }

  // Arbeitsweisen = aktive Skills, die diese Person treffen (z. B. Filme herunterladen). Anzeige; geändert wird im Gespräch mit Friday.
  function arbeitsweisenBlock(p) {
    const b = el('details', 'ko-info');
    const liste = p.arbeitsweisen || [];
    b.open = liste.length > 0;
    b.append(el('summary', 'ko-klein', `Arbeitsweisen, die Friday für sie übernimmt (${liste.length})`));
    if (!liste.length) {
      b.append(el('p', 'ko-beschr', 'Nichts Besonderes vereinbart. Sag Friday z. B. „Kontakt B darf Filme und Serien herunterladen“, dann steht es hier.'));
      return b;
    }
    for (const s of liste) {
      const k = el('div', 'ko-skill');
      k.append(el('strong', '', s.name.replace(/_/g, ' ')), el('p', 'ko-klein', s.zweck));
      const chips = el('div', 'ko-werkzeuge');
      for (const w of s.werkzeuge) chips.append(el('span', 'ko-chip', w.text));
      for (const o of s.optionen) {
        const c = el('span', 'ko-chip ko-opt', `${o.name.replace(/_/g, ' ')}: ${o.wert}`);
        if (o.text) c.title = o.text;
        chips.append(c);
      }
      k.append(chips);
      b.append(k);
    }
    b.append(el('p', 'ko-beschr', 'Ändern oder widerrufen sagst du Friday im Chat; es gilt erst nach deinem Ja.'));
    return b;
  }

  // Profil: Name, Snapchat-Konto (mit Prüfknopf) und Ton — alles, was die Person selbst betrifft, an einer Stelle.
  function profilBlock(p, name, sc, persona) {
    const st = p.snapchat_status || {};
    const b = el('div', 'ko-gruppe ko-sc-' + (st.stufe || 'grau'));
    b.append(el('h3', 'ko-unter', 'Profil'), feld('Name', name), feld('Anzeigename in Snapchat (genau so, wie er dort steht)', sc));
    if (st.grund) b.append(el('p', 'ko-klein', st.grund));
    if (p.kanaele && p.kanaele.snapchat) {
      b.append(knopf('Am Handy prüfen', '', async () => {
        const r = await senden(`/api/kontakte/${p.id}/freundschaft`, {});
        meldung = r.text || '';
        await laden();
      }));
    }
    b.append(feld('Persona (Ton und Beziehung)', persona));
    return b;
  }

  function personKarte(p) {
    const st = p.snapchat_status || {};
    const k = el('div', 'ko-person' + (p.pausiert ? ' ko-pause' : '') + (offen === p.id ? ' ko-offen' : ''));
    const kopf = el('div', 'ko-kopf');
    const mitte = el('div', 'ko-kopf-mitte');
    const oben = el('div', 'ko-kopf-oben');
    oben.append(el('strong', '', p.name), pille(st));
    mitte.append(oben, el('span', 'ko-klein', austauschText(p.austausch)));
    kopf.append(el('span', 'ko-avatar', initialen(p.name)), mitte, el('span', 'ko-pfeil', offen === p.id ? '▾' : '▸'));
    kopf.addEventListener('click', () => { offen = offen === p.id ? null : p.id; zeichnen(); });
    k.append(kopf);
    if (offen !== p.id) return k;

    const e0 = entwurf[p.id] || {};
    const name = textfeld(e0.name ?? p.name);
    const sc = textfeld(e0.snapchat ?? (p.kanaele && p.kanaele.snapchat), 'Anzeigename in Snapchat');
    const persona = el('textarea');
    persona.value = e0.persona ?? (p.persona || '');
    persona.maxLength = 2000;
    persona.rows = 4;
    persona.placeholder = 'Ton und Beziehung, z. B. „Freundin, duzt, locker“. Die Sicherheitsregeln bleiben fest.';
    const hinweis = el('p', 'ko-ungespeichert', 'Ungespeichert — unten „Speichern“ tippen.');
    const geaendert = () => {
      entwurf[p.id] = { name: name.value, snapchat: sc.value, persona: persona.value };
      const neu = name.value !== p.name || sc.value !== ((p.kanaele && p.kanaele.snapchat) || '') || persona.value !== (p.persona || '');
      if (!neu) delete entwurf[p.id];
      hinweis.hidden = !neu;
    };
    for (const f of [name, sc, persona]) f.addEventListener('input', geaendert);
    hinweis.hidden = !entwurf[p.id];
    k.append(hinweis);
    k.append(profilBlock(p, name, sc, persona), modulBlock(p), flammenBlock(p));

    const vergeben = (daten.quellen && daten.quellen[p.id]) || {};
    const vk = el('details', 'ko-info');
    vk.append(el('summary', 'ko-klein', `Verknüpft mit anderen Bereichen (${Object.keys(vergeben).length})`));
    if (!Object.keys(vergeben).length) vk.append(el('p', 'ko-beschr', 'Nichts verknüpft (WhatsApp-Chat, Garantie-Profil).'));
    for (const [q, n] of Object.entries(vergeben)) {
      const zeile = el('div', 'ko-frei');
      zeile.append(el('span', '', `${q === 'whatsapp' ? 'WhatsApp-Chat' : 'Garantie-Profil'} „${n}“`));
      zeile.append(knopf('Lösen', '', () => speichern(`/api/kontakte/${p.id}/entknuepfen`, { quelle: q })));
      vk.append(zeile);
    }
    const fz = el('details', 'ko-info ko-freigaben');
    const frei = (p.freigaben || []).filter((x) => x.status === 'aktiv').length;
    fz.open = frei > 0;
    fz.append(el('summary', 'ko-klein', `Was Friday dieser Person über dich sagen darf (${frei})`));
    fz.append(el('p', 'ko-beschr', 'Nur was hier steht, darf Friday dieser Person über dich sagen.'));
    if (!(p.freigaben || []).length) fz.append(el('p', 'ko-klein', 'Noch nichts freigegeben.'));
    for (const fr of p.freigaben || []) {
      const zeile = el('div', 'ko-frei' + (fr.status === 'aktiv' ? '' : ' ko-frei-aus'));
      zeile.append(el('span', '', fr.text), el('span', 'ko-klein', ` · ${fr.status}${fr.status === 'widerrufen' && !fr.geloescht ? ' (Löschen im Graphen offen)' : ''}`));
      if (fr.status === 'aktiv') {
        zeile.append(knopf('Widerrufen', '', () => speichern(`/api/kontakte/${p.id}/freigaben/${fr.id}/widerruf`, {})));
      }
      fz.append(zeile);
    }
    const neuText = el('textarea');
    neuText.rows = 2;
    neuText.maxLength = 2000;
    neuText.placeholder = 'Was darf Friday dieser Person sagen? z. B. „Eike hat im Oktober Geburtstag.“';
    fz.append(neuText, knopf('Freigeben', '', () => speichern(`/api/kontakte/${p.id}/freigaben`, { text: neuText.value })));
    k.append(fz, vk, grundBlock());
    const leiste = el('div', 'ko-leiste');
    leiste.append(
      knopf('Speichern', 'primaer', async () => {
        const r = await speichern(`/api/kontakte/${p.id}`, { name: name.value, snapchat: sc.value, persona: persona.value });
        if (r.ok) delete entwurf[p.id];
      }),
      knopf(p.pausiert ? 'Wieder aktivieren' : 'Pausieren', '', () => speichern(`/api/kontakte/${p.id}`, { pausiert: !p.pausiert })));
    k.append(leiste);
    return k;
  }

  function rueckfrageKarte(r) {
    const k = el('div', 'ko-person ko-rueckfrage');
    if (r.art === 'termin' && r.termin) {
      const t = r.termin;
      k.append(el('strong', '', `${r.person_name} schlägt vor: ${t.titel}`));
      k.append(el('p', 'ko-klein', `${t.beginn ? t.beginn.slice(0, 16).replace('T', ' ') : ''} Uhr · ${t.dauer_minuten || 60} Min.${t.ort ? ` · ${t.ort}` : ''}`));
      if (r.kollision) k.append(el('p', 'fehler', 'Achtung: überschneidet sich mit deinem Kalender.'));
      const leiste = el('div', 'ko-leiste');
      leiste.append(
        knopf('Annehmen & eintragen', 'primaer', () => speichern(`/api/kontakte/rueckfragen/${r.id}/annehmen`, {})),
        knopf('Ablehnen', '', () => speichern(`/api/kontakte/rueckfragen/${r.id}/ablehnen`, {})));
      k.append(leiste);
      return k;
    }
    k.append(el('strong', '', `${r.person_name} fragt: ${r.thema}`));
    if (r.wunsch) k.append(el('p', 'ko-klein', r.wunsch));
    const text = el('textarea');
    text.rows = 2;
    text.maxLength = 2000;
    text.placeholder = 'Was darf Friday dazu sagen? z. B. „Eike hat im Oktober Geburtstag.“ (leer lassen = ablehnen)';
    k.append(text);
    const leiste = el('div', 'ko-leiste');
    leiste.append(
      knopf('Freigeben', 'primaer', () => speichern(`/api/kontakte/rueckfragen/${r.id}/freigeben`, { text: text.value })),
      knopf('Ablehnen', '', () => speichern(`/api/kontakte/rueckfragen/${r.id}/ablehnen`, {})));
    k.append(leiste);
    return k;
  }

  function vorschlagKarte(v) {
    const k = el('div', 'ko-person ko-unbekannt');
    const was = v.quelle === 'whatsapp' ? 'WhatsApp-Chat' : 'Garantie-Profil';
    const warum = (v.warum || []).join(', ');
    k.append(el('strong', '', v.name),
             el('span', 'ko-klein', ` · ${was} „${v.anzeige}“${warum ? ` (${warum})` : ''} — dieselbe Person?`));
    const leiste = el('div', 'ko-leiste');
    leiste.append(knopf('Verknüpfen', 'primaer', () => speichern(`/api/kontakte/${v.pid}/verknuepfen`, { quelle: v.quelle, ref: v.ref })));
    k.append(leiste);
    return k;
  }

  function unbekanntKarte(u) {
    const k = el('div', 'ko-person ko-unbekannt');
    k.append(el('strong', '', u.anzeige),
             el('span', 'ko-klein', ' · schreibt Friday, ist aber nicht angelegt — Friday antwortet nicht'));
    const leiste = el('div', 'ko-leiste');
    leiste.append(
      knopf('Anlegen', 'primaer', () => dialogOeffnen(u.anzeige)),
      knopf('Ignorieren', '', () => speichern('/api/kontakte/unbekannt/ignorieren', { kanal: u.kanal, anzeige: u.anzeige })));
    k.append(leiste);
    return k;
  }

  function anfrageKarte(a) {
    const k = el('div', 'ko-person ko-unbekannt');
    let stand = ' · Anfrage ist raus, wartet auf Annahme (Friday fragt nicht zweimal)';
    if (a.zustand === 'angenommen') stand = ' · hat die Anfrage angenommen — als Kontakt anlegen, dann kann Friday schreiben';
    if (a.zustand === 'gescheitert') stand = ' · Anfrage kam nicht an' + (a.grund ? `: ${a.grund}` : '') + ' — einfach erneut beauftragen';
    k.append(el('strong', '', a.snapchat), el('span', 'ko-klein', stand));
    const leiste = el('div', 'ko-leiste');
    if (a.zustand === 'angenommen') leiste.append(knopf('Anlegen', 'primaer', () => dialogOeffnen(a.snapchat)));
    leiste.append(knopf('Zurückziehen', '', () => speichern('/api/kontakte/anfragen/zurueckziehen', { snapchat: a.snapchat })));
    k.append(leiste);
    return k;
  }

  async function dialogOeffnen(snapchat) {
    dialog = { snapchat: snapchat || '', gewaehlt: null, eintippen: false };
    bekannt = null;
    zeichnen();
    try {
      const r = await fetch('/api/kontakte/bekannt');
      if (r.ok) bekannt = await r.json();
    } catch (_) { /* unten: leere Liste mit Hinweis */ }
    if (!bekannt) bekannt = { personen: [], ausgefallen: ['alle Quellen'] };
    zeichnen();
  }

  // Wen Friday schon kennt: durchsuchbar, je Person die Quellen als Chips. Das Suchfeld bleibt beim Tippen bestehen
  // (nur die Liste wird neu gefüllt), sonst ginge der Fokus verloren.
  function auswahl(f) {
    if (bekannt === null) { f.append(el('p', 'leer-klein', 'lädt …')); return; }
    const suche = el('input');
    suche.type = 'search';
    suche.placeholder = 'Person suchen';
    suche.maxLength = 80;
    const eintraege = el('div', 'ko-auswahl');
    const fuellen = () => {
      const q = suche.value.trim().toLowerCase();
      eintraege.replaceChildren();
      const treffer = bekannt.personen.filter((p) => p.name.toLowerCase().includes(q));
      for (const p of treffer.slice(0, 60)) {
        const b = knopf(p.name, 'ko-wahl', () => { dialog.gewaehlt = p; zeichnen(); });
        for (const quelle of p.quellen) b.append(el('span', 'ko-chip', quelle));
        eintraege.append(b);
      }
      if (!treffer.length) eintraege.append(el('p', 'leer-klein', 'Keine bekannte Person gefunden.'));
    };
    suche.addEventListener('input', fuellen);
    f.append(suche, eintraege);
    fuellen();
    if (bekannt.ausgefallen.length) {
      f.append(el('p', 'leer-klein', `Nicht lesbar: ${bekannt.ausgefallen.join(', ')} — die Liste kann unvollständig sein.`));
    }
    f.append(knopf('Neue Person eintippen', '', () => { dialog.eintippen = true; zeichnen(); }));
  }

  function anlegenDialog() {
    const f = el('div', 'ko-person');
    f.append(el('strong', '', 'Person anlegen'));
    if (!dialog.gewaehlt && !dialog.eintippen) {
      f.append(el('p', 'leer-klein', 'Wähle, wen Friday schon kennt. Erinnerungen und Bilder der Person gehören dann dazu.'));
      auswahl(f);
      f.append(knopf('Abbrechen', '', () => { dialog = null; zeichnen(); }));
      return f;
    }
    const name = textfeld(dialog.gewaehlt ? dialog.gewaehlt.name : '', 'Name');
    name.readOnly = !!dialog.gewaehlt;
    const sc = textfeld(dialog.snapchat, 'Anzeigename in Snapchat (genau so, wie er dort steht)');
    if (dialog.gewaehlt) f.append(el('p', 'leer-klein', `Quellen: ${dialog.gewaehlt.quellen.join(', ')}`));
    f.append(feld('Name', name), feld('Snapchat-Anzeigename', sc));
    const leiste = el('div', 'ko-leiste');
    leiste.append(
      knopf('Anlegen', 'primaer', () => speichern('/api/kontakte', {
        name: name.value, graph_name: name.value, snapchat: sc.value })),
      knopf('Zurück', '', () => { dialog.snapchat = sc.value; dialog.gewaehlt = null; dialog.eintippen = false; zeichnen(); }),
      knopf('Abbrechen', '', () => { dialog = null; zeichnen(); }));
    f.append(leiste);
    return f;
  }

  // Suchfeld und Filter: das Feld bleibt dasselbe Element, damit Fokus und Cursor beim Neuzeichnen nicht verloren gehen.
  const suchfeld = el('input');
  suchfeld.type = 'search';
  suchfeld.placeholder = 'Person suchen (Name oder Snapchat-Name)';
  suchfeld.maxLength = 80;
  suchfeld.setAttribute('aria-label', 'Person suchen');
  suchfeld.addEventListener('input', () => { suche = suchfeld.value; zeichnen(); });
  function leiste_(FILTER) {
    const z = el('div', 'ko-werkzeugleiste');
    const chips = el('div', 'ko-filter');
    for (const [id, text, f] of FILTER) {
      const n = daten.personen.filter(f).length;
      const b = knopf(`${text} ${n}`, 'ko-filterchip' + (filter === id ? ' ko-aktiv' : ''), () => { filter = id; zeichnen(); });
      chips.append(b);
    }
    z.append(suchfeld, chips);
    return z;
  }

  // Deine eigene Flamme mit Friday — kein Kontakt, darum ein eigener Schalter ganz oben.
  function flammeEikeKarte() {
    const b = el('div', 'ko-gruppe ko-flamme-eike');
    b.append(el('h3', 'ko-unter', 'Flamme mit mir'));
    b.append(el('p', 'ko-beschr', 'Friday schickt dir einmal am Tag einen lustigen Snap. Die Flamme hält, wenn du ihr täglich auch einen schickst.'));
    const z = el('label', 'ko-schalter' + (daten.flammen_eike ? ' ko-an' : ''));
    const h = el('input');
    h.type = 'checkbox';
    h.checked = !!daten.flammen_eike;
    h.addEventListener('change', async () => {
      const r = await senden('/api/flamme-eike', { an: h.checked });
      meldung = r.ok ? '' : r.text;
      await laden();
    });
    const t = el('span', 'ko-schalter-text');
    t.append(el('strong', '', 'Flamme halten'), document.createTextNode(' — täglich ein Snap von Friday.'));
    z.append(h, t);
    b.append(z);
    const mz = el('label', 'ko-schalter');
    const sel = el('select');
    for (const [w, t] of [['an', 'an — Snaps werden gesendet'], ['trocken', 'Probelauf — nur Protokoll'], ['aus', 'aus']]) {
      const o = el('option', '', t);
      o.value = w;
      sel.append(o);
    }
    sel.value = daten.flammen_modus || 'aus';
    sel.addEventListener('change', async () => {
      const r = await senden('/api/flammen-modus', { modus: sel.value });
      meldung = r.ok ? '' : r.text;
      await laden();
    });
    mz.append(el('span', 'ko-schalter-text', 'Flammen-Lauf für alle (Hauptschalter)'), sel);
    b.append(mz);
    return b;
  }

  function zeichnen() {
    const hatteFokus = document.activeElement === suchfeld;
    wurzel.replaceChildren();
    if (meldung) wurzel.append(el('p', 'fehler', meldung));
    wurzel.append(el('p', 'leer-klein',
      'Hier legst du fest, mit wem Friday schreibt. Wer nicht angelegt ist, bekommt keine Antwort. ' +
      'Jede Person bekommt eine einfache Friday; was darüber hinausgeht, schaltest du pro Person bei Module und Flammen dazu.'));
    wurzel.append(flammeEikeKarte());
    if ((daten.rueckfragen || []).length) {
      wurzel.append(el('h2', 'ko-titel', 'Zu entscheiden'));
      for (const r of daten.rueckfragen) wurzel.append(rueckfrageKarte(r));
    }
    if (daten.unbekannt.length) {
      wurzel.append(el('h2', 'ko-titel', 'Unbekannt — antwortet Friday nicht'));
      for (const u of daten.unbekannt) wurzel.append(unbekanntKarte(u));
    }
    if ((daten.quellen_vorschlaege || []).length) {
      wurzel.append(el('h2', 'ko-titel', 'Zusammenführen — dieselbe Person?'));
      for (const v of daten.quellen_vorschlaege) wurzel.append(vorschlagKarte(v));
    }
    const stufe = (p) => (p.snapchat_status || {}).stufe;
    const FILTER = [
      ['alle', 'Alle', () => true],
      ['gruen', 'Kann schreiben', (p) => !p.pausiert && stufe(p) === 'gruen'],
      ['gelb', 'Unklar', (p) => !p.pausiert && stufe(p) === 'gelb'],
      ['problem', 'Problem', (p) => !p.pausiert && (stufe(p) === 'rot' || stufe(p) === 'grau')],
      ['pausiert', 'Pausiert', (p) => p.pausiert],
    ];
    const q = suche.trim().toLowerCase();
    const trifft = (p) => (FILTER.find((f) => f[0] === filter) || FILTER[0])[2](p)
      && (!q || p.name.toLowerCase().includes(q) || ((p.kanaele && p.kanaele.snapchat) || '').toLowerCase().includes(q));
    if (daten.personen.length) {
      wurzel.append(leiste_(FILTER));
    }
    const sichtbar = daten.personen.filter(trifft);
    const aktiv = sichtbar.filter((p) => !p.pausiert);
    const pausiert = sichtbar.filter((p) => p.pausiert);
    const anfragen = (q || filter !== 'alle') ? [] : (daten.anfragen || []);
    if (anfragen.length) {
      wurzel.append(el('h2', 'ko-titel', `Offene Freundschaftsanfragen (${anfragen.length})`));
      for (const a of anfragen) wurzel.append(anfrageKarte(a));
    }
    if (aktiv.length) {
      wurzel.append(el('h2', 'ko-titel', `Kontakte (${aktiv.length})`));
      for (const p of aktiv) wurzel.append(personKarte(p));
    }
    if (pausiert.length) {
      wurzel.append(el('h2', 'ko-titel', `Pausiert (${pausiert.length})`));
      for (const p of pausiert) wurzel.append(personKarte(p));
    }
    if (daten.personen.length && !sichtbar.length) wurzel.append(el('p', 'leer-klein', 'Keine Person passt zu Suche und Filter.'));
    if (!daten.personen.length && !daten.unbekannt.length && !(daten.anfragen || []).length) {
      wurzel.append(el('p', 'leer-klein', 'Noch keine Kontakte.'));
    }
    wurzel.append(dialog ? anlegenDialog() : knopf('Person anlegen', '', () => dialogOeffnen('')));
    if (hatteFokus) { suchfeld.focus(); const l = suchfeld.value.length; suchfeld.setSelectionRange(l, l); }
  }

  async function laden() {
    try {
      const r = await fetch('/api/kontakte');
      if (r.ok) daten = await r.json();
    } catch (_) { meldung = 'Friday ist nicht erreichbar.'; }
    zeichnen();
  }

  for (const b of document.querySelectorAll('#studio .reiter button')) {
    b.addEventListener('click', () => { if (b.dataset.reiter === 'kontakte') setTimeout(laden, 0); });
  }
})();
