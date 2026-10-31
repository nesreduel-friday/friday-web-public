/* Extras → hphome → Werkbank: Verbesserungsvorhaben an Friday ansehen, starten, verwerfen, übernehmen.

   Starten passiert auf Eikes Tipp oder – nur wenn er oben den Hebel „Automatik“ umlegt – von selbst
   (höchstens 5 am Tag). Übernehmen nur auf Eikes Tipp, mit zweitem Tipp (Bestätigung). Belege, Berichte
   und Diffs sind Fremdtext: alles per textContent, nie als HTML. */
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
    b.addEventListener('click', () => aktion(b));
    return b;
  };
  const senden = async (url, daten) => {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                 body: JSON.stringify(daten ?? {}) });
    const d = await r.json().catch(() => ({}));
    const t = (typeof d.text === 'string' && d.text) || (typeof d.detail === 'string' && d.detail) || '';
    return { ok: r.ok && d.ok !== false, text: t || (r.ok ? '' : `Fehler ${r.status}`) };
  };

  const ARTEN = { fehler: 'Fehler', wunsch: 'Wunsch', idee: 'Idee', test: 'Test' };
  const STATUS = {
    vorgeschlagen: 'vorgeschlagen', freigegeben: 'freigegeben', laeuft: 'läuft', bereit: 'Ergebnis liegt bereit',
    uebernommen: 'übernommen', verworfen: 'verworfen', ruht: 'ruht', fehler: 'fehlgeschlagen',
  };
  const UEBERNAHME = {
    laeuft: 'Übernahme läuft …', ok: 'Übernommen und live.',
    fehler: 'Übernahme fehlgeschlagen – bitte prüfen.',
  };
  const ZURUECK = 'Zurückgerollt – Branch nicht noch einmal übernehmbar';

  const tage = (iso) => {
    const t = Date.parse(iso || '');
    return Number.isNaN(t) ? 0 : Math.max(0, Math.floor((Date.now() - t) / 86400000));
  };

  // Scharfstellung („zweiter Tipp“): Der Bestätigungsknopf ist erst nach FREI_MS bedienbar (ein Doppeltipp auf
  // dieselbe Stelle löst nichts aus) und fällt nach ABLAUF_MS von selbst zurück.
  const FREI_MS = 1500;
  const ABLAUF_MS = 10000;

  let stand = { vorhaben: [], auto_heute: 0, auto_limit: 0 };
  let einst = null;                 // Hebel: { werkbank_auto, werkbank_auto_backend, auto_heute, auto_limit } oder null
  let gezeichnet = '';              // JSON des zuletzt gezeichneten Stands: nur bei Änderung neu aufbauen
  let fehler = '';
  let meldung = '';
  const offen = new Set();          // ausgeklappte Karten
  const diffs = new Map();          // id → { laedt } | { text, gekuerzt } | { fehler }
  const letzterStatus = new Map();  // id → Status; bei Wechsel Diff und Scharfstellung verwerfen
  const sicher = new Map();         // "art:id" → { frei, t1, t2 }
  let beschaeftigt = false;

  const scharfAus = (key) => {
    const z = sicher.get(key);
    if (!z) return;
    clearTimeout(z.t1); clearTimeout(z.t2);
    sicher.delete(key);
  };
  const alleScharfAus = () => { for (const key of [...sicher.keys()]) scharfAus(key); };
  const scharfMachen = (key) => {
    scharfAus(key);
    const z = { frei: false, t1: 0, t2: 0 };
    z.t1 = setTimeout(() => { z.frei = true; zeichnen(); }, FREI_MS);
    z.t2 = setTimeout(() => { scharfAus(key); zeichnen(); }, ABLAUF_MS);
    sicher.set(key, z);
    zeichnen();
  };

  function zaehler(v) {
    const n = Number(v.zaehler) || 1;
    const t = tage(v.erstmals);
    return `${n}× gemeldet` + (t > 0 ? `, seit ${t} Tag${t === 1 ? '' : 'en'}` : ', heute zuerst');
  }

  async function aktion(b, url, daten, unklar) {
    if (beschaeftigt) return;
    beschaeftigt = true; b.disabled = true;
    meldung = 'Einen Moment …'; zeichnen();          // sofort sichtbar, dass etwas passiert
    try {
      const r = await senden(url, daten);
      meldung = r.text || (r.ok ? 'Erledigt.' : 'Das hat nicht geklappt.');
    } catch (_) {
      meldung = unklar || 'Friday ist gerade nicht erreichbar.';
    } finally {
      beschaeftigt = false;
    }
    await laden(true);
  }

  // Hebel umlegen. Während eines Aufrufs nichts Zweites; danach wird vom Server neu gezeichnet – nach einem Fehler
  // steht die Auswahl so wieder auf dem gespeicherten Wert.
  async function hebel(eingabe, daten) {
    if (beschaeftigt) { zeichnen(); return; }
    beschaeftigt = true; eingabe.disabled = true;
    try {
      const r = await senden('/api/werkbank/einstellungen', daten);
      meldung = r.ok ? '' : (r.text || 'Das ließ sich nicht umstellen.');
    } catch (_) {
      meldung = 'Friday ist gerade nicht erreichbar – nichts umgestellt.';
    } finally {
      beschaeftigt = false;
    }
    await laden(true);
  }

  function hebelZeichnen(ziel) {
    const box = el('div', 'block');
    if (!einst) {
      box.append(el('p', 'klein', 'Automatik: Stand nicht geladen.'));
      ziel.append(box);
      return;
    }
    const an = einst.werkbank_auto === 'an';
    const zeile = el('div', 'kopfzeile');
    const l1 = el('label');
    const schalter = el('input');
    schalter.type = 'checkbox';
    schalter.checked = an;
    schalter.addEventListener('change', () => hebel(schalter, { werkbank_auto: schalter.checked ? 'an' : 'aus' }));
    l1.append(schalter, el('span', '', ' Automatik: Friday arbeitet die Vorhaben von selbst ab'));
    zeile.append(l1);
    box.append(zeile);

    const zeile2 = el('div', 'kopfzeile');
    const l2 = el('label', '', 'Worker bei Automatik ');
    const wahl = el('select');
    wahl.setAttribute('aria-label', 'Worker bei Automatik');
    for (const [wert, text] of [['claude', 'Claude Code'], ['deepseek', 'Claude Code mit DeepSeek'], ['opencode', 'OpenCode']]) {
      const o = el('option', '', text);
      o.value = wert;
      wahl.append(o);
    }
    wahl.value = ['opencode', 'deepseek'].includes(einst.werkbank_auto_backend) ? einst.werkbank_auto_backend : 'claude';
    wahl.addEventListener('change', () => hebel(wahl, { werkbank_auto_backend: wahl.value }));
    l2.append(wahl);
    zeile2.append(l2);
    box.append(zeile2);
    if (wahl.value === 'opencode') {
      box.append(el('p', 'klein', 'Mit OpenCode nimmt die Automatik nur Test- und Ideen-Vorhaben; die übrigen warten '
        + 'auf deinen Tipp. Gratis-Modelle können auf Eingaben trainieren: Der Titel des Vorhabens geht an das freie Modell.'));
    }
    const n = Number(einst.auto_heute) || 0;
    const max = Number(einst.auto_limit) || 0;
    box.append(el('p', 'leise', `Automatisch heute: ${n} von ${max} · manuell unbegrenzt`));
    const zeile3 = el('div', 'kopfzeile');
    const l3 = el('label');
    const selbst = el('input');
    selbst.type = 'checkbox';
    selbst.checked = einst.werkbank_auto_uebernehmen === 'an';
    selbst.addEventListener('change', () => hebel(selbst, { werkbank_auto_uebernehmen: selbst.checked ? 'an' : 'aus' }));
    l3.append(selbst, el('span', '', ' Selbst weiterentwickeln: Vorhaben aus der Denkrunde mitnehmen und grüne Werkzeug-Änderungen von selbst übernehmen'));
    zeile3.append(l3);
    box.append(zeile3);
    if (selbst.checked) {
      const zeile4 = el('div', 'kopfzeile');
      const l4 = el('label');
      const limit = el('input');
      limit.type = 'checkbox';
      limit.checked = einst.werkbank_selbst_limit !== 'aus';
      limit.addEventListener('change', () => hebel(limit, { werkbank_selbst_limit: limit.checked ? 'an' : 'aus' }));
      l4.append(limit, el('span', '', ` Tageslimit: höchstens ${Number(einst.selbst_limit) || 3} Übernahmen am Tag`));
      zeile4.append(l4);
      box.append(zeile4);
      box.append(el('p', 'klein', 'Übernommen wird nur, wenn alle Tests grün sind und die Änderung nur Werkzeug-Module, neue Tests oder Doku '
        + `betrifft${limit.checked ? '' : ' (ohne Tageslimit)'}; bei Problemen rollt Friday zurück. Alles andere bleibt dein Tipp.`));
    }
    if (an && !selbst.checked) {
      box.append(el('p', 'klein', 'Startet nur Vorhaben aus dem Gespräch oder aus Störungen, nie aus der Denkrunde '
        + 'oder aus fremdem Text. Übernehmen bleibt immer dein Tipp.'));
    }
    ziel.append(box);
  }

  async function diffLaden(id) {
    if (diffs.get(id)?.laedt) return;                // läuft schon
    const mein = { laedt: true };                    // Token: nur DIESER Abruf darf sein Ergebnis eintragen
    diffs.set(id, mein);
    let erg;
    try {
      const r = await fetch(`/api/werkbank/${id}/diff`);
      const d = await r.json().catch(() => ({}));
      erg = r.ok && typeof d.diff === 'string'
        ? { text: d.diff, gekuerzt: !!d.gekuerzt }
        : { fehler: (typeof d.detail === 'string' && d.detail) || `Fehler ${r.status}` };
    } catch (_) {
      erg = { fehler: 'Friday ist gerade nicht erreichbar.' };
    }
    // zwischenzeitlich verworfen oder Status gewechselt (Eintrag gelöscht/neu geholt) → veraltet, nichts eintragen
    if (diffs.get(id) === mein) { diffs.set(id, erg); zeichnen(); }
  }

  // Diffs von „bereit“-Vorhaben holen; ein Fehler wird beim nächsten Abruf erneut versucht.
  function diffsNachladen() {
    for (const v of stand.vorhaben) {
      if (!v.diff) continue;
      const d = diffs.get(v.id);
      if (!d || d.fehler) diffLaden(v.id);
    }
  }

  function statusWechsel() {
    for (const v of stand.vorhaben) {
      if (letzterStatus.has(v.id) && letzterStatus.get(v.id) !== v.status) {
        diffs.delete(v.id);
        scharfAus(`uebernehmen:${v.id}`); scharfAus(`verwerfen:${v.id}`);
      }
      letzterStatus.set(v.id, v.status);
    }
  }

  const diffGeladen = (v) => typeof diffs.get(v.id)?.text === 'string';

  function ergebnis(k, v) {
    if (v.bericht) k.append(el('div', 'text', v.bericht));
    const tests = v.tests || 'nicht gelaufen';
    k.append(el('div', 'klein', `Tests: ${tests === 'ok' ? 'grün' : tests.replace(/_/g, ' ')}`));
    const nb = Number(v.nachbesserungen) || 0;      // so oft hat der Helfer rote Tests an den Worker zurückgegeben
    if (nb > 0 && v.status !== 'laeuft') {
      k.append(el('div', 'klein', tests === 'ok' ? `Grün nach ${nachbesserungWort(nb)}.`
        : `Trotz ${nachbesserungWort(nb)} nicht grün.`));
    }
    if (v.diff) {
      const d = diffs.get(v.id);
      const pre = el('pre', 'wb-diff');
      pre.dataset.id = String(v.id);
      if (d && typeof d.text === 'string') {
        pre.textContent = d.text;
        k.append(pre);
        if (d.gekuerzt) k.append(el('div', 'klein', 'Der Diff ist hier gekürzt – nicht alles ist zu sehen.'));
      } else {
        pre.textContent = d && d.fehler ? `Diff nicht geladen: ${d.fehler}` : 'Diff lädt …';
        k.append(pre);
      }
    }
  }

  const nachbesserungWort = (n) => `${n} Nachbesserung${n === 1 ? '' : 'en'}`;

  function karte(v) {
    const k = el('div', 'eintrag');
    const kopf = el('div', 'kopfzeile');
    kopf.append(el('div', 'titel', v.titel), el('span', 'leise', STATUS[v.status] || v.status));
    k.append(kopf);
    k.append(el('div', 'klein', `${ARTEN[v.art] || v.art} · ${zaehler(v)}`));
    const laufend = Number(v.nachbesserung) || 0;   // Tests waren rot: der Worker bessert gerade nach
    if (v.status === 'laeuft' && laufend > 0) {
      const max = Number(stand.nachbesserungen_max) || 0;
      k.append(el('div', 'klein', `Tests waren rot – Nachbesserung ${laufend}${max ? ` von ${max}` : ''} läuft …`));
    }

    const auf = offen.has(v.id);
    if (auf && v.beleg) k.append(el('div', 'text', v.beleg));
    if (v.status === 'bereit' || v.status === 'fehler' || auf) ergebnis(k, v);
    if (v.uebernahme === 'zurueckgerollt') k.append(el('div', 'status-fehler', ZURUECK));
    else if (UEBERNAHME[v.uebernahme]) k.append(el('div', 'klein', UEBERNAHME[v.uebernahme]));

    const reihe = el('div', 'knoepfe');
    reihe.append(knopf(auf ? 'Weniger' : 'Mehr', '', () => {
      if (auf) offen.delete(v.id); else offen.add(v.id);
      zeichnen();
    }));
    // „fehler“ ist nicht startbar (Worktree/Branch des gescheiterten Laufs bleiben): verwerfen, bei Bedarf neu vorschlagen
    const startbar = ['vorgeschlagen', 'freigegeben'].includes(v.status) && !v.fremd;
    if (v.fremd && ['vorgeschlagen', 'freigegeben'].includes(v.status)) {
      k.append(el('div', 'klein', 'Stammt aus fremdem Text – hier nicht startbar.'));
    }
    if (v.status === 'fehler') {
      k.append(el('div', 'klein', 'Fehlgeschlagen – „Mach weiter“ versucht es im selben Worktree noch einmal, oder verwerfen.'));
    }
    if (startbar) {
      reihe.append(knopf('Starten (DeepSeek)', '', (b) => aktion(b, `/api/werkbank/${v.id}/starten`, { backend: 'deepseek' })));
      reihe.append(knopf('Starten (Claude)', '', (b) => aktion(b, `/api/werkbank/${v.id}/starten`, { backend: 'claude' })));
      if (v.art === 'test' || v.art === 'idee') {
        reihe.append(knopf('Starten (OpenCode-Test)', '', (b) => aktion(b, `/api/werkbank/${v.id}/starten`, { backend: 'opencode' })));
      }
    }
    if (['fehler', 'bereit'].includes(v.status) && ['claude', 'deepseek'].includes(v.backend) && !v.fremd && v.uebernahme !== 'ok') {
      reihe.append(knopf('Mach weiter', '', (b) => aktion(b, `/api/werkbank/${v.id}/weiter`, {})));
    }
    if (v.status === 'bereit' && ['claude', 'deepseek'].includes(v.backend) && v.uebernahme !== 'zurueckgerollt') {
      if (v.tests !== 'ok') {
        k.append(el('div', 'status-fehler', 'Tests nicht grün – Übernehmen ist hier nicht möglich.'));
      } else if (!diffGeladen(v)) {
        k.append(el('div', 'klein', 'Diff noch nicht geladen – Übernehmen erst danach.'));
      } else {
        // Ein Tipp genügt (31.10.2026): Die frühere Zwei-Tipp-Sperre wirkte wie ein kaputter Knopf —
        // er war nach dem ersten Tipp 1,5 s lang blind und fiel nach 10 s kommentarlos zurück.
        // Verwerfen bleibt bestätigt, weil es Daten wegwirft; Übernehmen ist umkehrbar (Rückfall per Revert).
        reihe.append(knopf('Übernehmen', '', (b) =>
          aktion(b, `/api/werkbank/${v.id}/uebernehmen`, {}, 'Stand unklar, bitte nachsehen (Übernahme eventuell schon gestartet).')));
      }
    }
    if (!['laeuft', 'uebernommen', 'verworfen'].includes(v.status)) {
      const key = `verwerfen:${v.id}`;
      const zwei = v.status === 'bereit';
      const z = zwei ? sicher.get(key) : null;
      const b = knopf(z ? 'Wirklich verwerfen' : 'Verwerfen', z ? 'weg sicher' : 'weg', (b2) => {
        if (zwei) {
          if (!sicher.get(key)) { scharfMachen(key); return; }
          if (!sicher.get(key).frei) return;
          scharfAus(key);
        }
        aktion(b2, `/api/werkbank/${v.id}/verwerfen`, {});
      });
      if (z && !z.frei) b.disabled = true;
      reihe.append(b);
    }
    k.append(reihe);
    return k;
  }

  function zeichnen() {
    const ziel = $('hphome-werkbank');
    if (!ziel) return;
    const scroll = new Map();
    for (const p of ziel.querySelectorAll('.wb-diff')) scroll.set(p.dataset.id, p.scrollTop);
    ziel.replaceChildren();
    ziel.append(el('p', 'leise', 'Verbesserungen an Friday selbst. Gestartet wird auf deinen Tipp oder mit der '
      + 'Automatik; das Ergebnis entsteht auf einem eigenen Branch und geht erst nach deinem „Übernehmen“ live.'));
    hebelZeichnen(ziel);
    if (fehler) ziel.append(el('p', 'status-fehler', fehler));
    const liste = stand.vorhaben.filter((v) => !['verworfen', 'ruht'].includes(v.status));
    if (!liste.length && !fehler) ziel.append(el('p', 'leer-klein', 'Keine offenen Vorhaben.'));
    for (const v of liste) ziel.append(karte(v));
    if (meldung) ziel.append(el('p', 'leise', meldung));
    for (const p of ziel.querySelectorAll('.wb-diff')) p.scrollTop = scroll.get(p.dataset.id) || 0;
    gezeichnet = JSON.stringify([stand, einst, fehler, meldung]);
  }

  function sichtbar() {
    const r = $('r-hphome');
    return location.hash === '#studio' && r && !r.hidden;
  }

  async function laden(erzwingen) {
    if (!sichtbar()) { alleScharfAus(); return; }
    try {
      const r = await fetch('/api/werkbank');
      if (!r.ok) throw new Error(String(r.status));
      stand = await r.json();
      fehler = '';
    } catch (_) {
      fehler = 'Friday ist gerade nicht erreichbar.';
    }
    try {
      const r = await fetch('/api/werkbank/einstellungen');
      if (!r.ok) throw new Error(String(r.status));
      einst = await r.json();
    } catch (_) {
      einst = null;
    }
    statusWechsel();
    diffsNachladen();
    // Nur bei Änderung neu aufbauen: Diff-Scroll, Textauswahl und ein laufender Tipp bleiben sonst erhalten.
    if (erzwingen === true || JSON.stringify([stand, einst, fehler, meldung]) !== gezeichnet) zeichnen();
  }

  const zuruecksetzen = () => { alleScharfAus(); meldung = ''; };

  window.addEventListener('hashchange', () => { alleScharfAus(); laden(); });
  document.addEventListener('DOMContentLoaded', () => laden());
  for (const b of document.querySelectorAll('#studio .reiter button')) {
    b.addEventListener('click', () => { alleScharfAus(); if (b.dataset.reiter === 'hphome') { zuruecksetzen(); laden(true); } });
  }
  setInterval(() => laden(), 5000);
})();
