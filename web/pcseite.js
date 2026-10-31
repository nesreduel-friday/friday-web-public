/* PC-Seite (Extras → PC): Reiter für alles, was der Helfer am PC kann.

   Gezeichnet wird von pc.js aus (`window.FridayPcSeite.zeichnen`): beim Öffnen komplett, danach alle paar Sekunden nur
   die Live-Teile des offenen Reiters — Eingaben gehen so nicht verloren. Gelesen wird über /api/pc/lesen/…, gehandelt
   über /api/pc/tun: Sicheres läuft sofort, Heikles wird zur Rückfrage (Leiste unten, pc.js); die Bestätigung selbst
   gibt es hier nie. Alles Fremde (Fenstertitel, Dateinamen, Prozessnamen) kommt nur per textContent auf die Seite. */
(() => {
  const { $ } = window.Friday;
  const { el, knopf, senden, uhr, WIE } = window.FridayPc;

  /* ---------- Bausteine ---------- */

  const abfrage = async (was, fragen = {}) => {
    const q = new URLSearchParams(Object.entries(fragen).filter(([, v]) => v)).toString();
    try {
      const r = await fetch(`/api/pc/lesen/${was}${q ? `?${q}` : ''}`, { cache: 'no-store' });
      const d = await r.json().catch(() => ({}));
      return r.ok ? d : { ok: false, text: d.detail || `Fehler ${r.status}` };
    } catch (_) { return { ok: false, text: 'Friday ist nicht erreichbar.' }; }
  };

  const tun = async (art, angaben = {}) => {
    const r = await senden('/api/pc/tun', { art, angaben });
    if (r.rueckfrage) window.FridayPc.jetzt();          // die Rückfrage soll sofort unten stehen
    return r;
  };

  // Eine Zeile für das Ergebnis der letzten Aktion (grün/rot).
  const rueckmeldung = () => {
    const p = el('div', 'klein pc-rueckmeldung');
    p.hidden = true;
    p.zeigen = (r) => {
      p.textContent = r.text || (r.ok ? 'Erledigt.' : 'Hat nicht geklappt.');
      p.className = `klein pc-rueckmeldung ${r.ok ? 'status-ok' : 'status-fehler'}`;
      p.hidden = false;
    };
    return p;
  };

  // Ein Knopf, der eine Aktion auslöst und das Ergebnis in `ausgabe` zeigt. `nachher` läuft danach (z. B. Liste neu laden).
  const aktionsknopf = (text, art, angaben, ausgabe, { klasse = '', nachher, gesperrt = false } = {}) => {
    const b = knopf(text, klasse, async (btn) => {
      btn.disabled = true;
      const r = await tun(art, typeof angaben === 'function' ? angaben() : angaben);
      ausgabe.zeigen(r);
      btn.disabled = false;
      if (nachher) nachher(r);
    });
    b.disabled = gesperrt;
    return b;
  };

  const groesse = (n) => {
    if (n === null || n === undefined) return '';
    for (const e of ['B', 'KB', 'MB', 'GB']) { if (n < 1024 || e === 'GB') return e === 'B' ? `${n} B` : `${n.toFixed(1).replace('.', ',')} ${e}`; n /= 1024; }
    return '';
  };
  const dauer = (s) => {
    const min = Math.floor(s / 60), h = Math.floor(min / 60);
    return h ? `${h} h ${String(min % 60).padStart(2, '0')} min` : `${min} min`;
  };
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  const kopf = (titel, klein) => { const d = el('div', 'kopfzeile'); d.append(el('div', 'titel', titel)); if (klein) d.append(el('span', 'klein', klein)); return d; };

  const balken = (name, prozent, text, warnBei = 90) => {
    const z = el('div', 'pc-mess'), k = el('div', 'pc-mess-kopf'), b = el('div', 'pc-balken'), f = el('div', 'pc-balken-fuellung');
    k.append(el('span', '', name), el('span', 'klein', text));
    f.style.width = `${Math.max(0, Math.min(100, prozent))}%`;
    if (prozent >= warnBei) f.classList.add('voll');
    b.append(f); z.append(k, b);
    return z;
  };

  // Ein Eingabefeld mit Knopf (Enter geht auch).
  const eingabe = (platzhalter, knopfText, beiOk, { start = '', mehrzeilig = false } = {}) => {
    const form = el('form', 'pc-eingabe');
    const feld = el(mehrzeilig ? 'textarea' : 'input');
    if (!mehrzeilig) feld.type = 'text';
    feld.placeholder = platzhalter; feld.value = start; feld.autocapitalize = 'off'; feld.spellcheck = false;
    if (mehrzeilig) feld.rows = 3;
    const ok = el('button', '', knopfText); ok.type = 'submit';
    form.append(feld, ok);
    form.addEventListener('submit', async (e) => { e.preventDefault(); ok.disabled = true; await beiOk(feld.value.trim(), feld); ok.disabled = false; });
    return { form, feld };
  };

  /* ---------- Reiter: Übersicht ---------- */

  const SCHNELL = [
    ['Sperren', 'sperren', false], ['Bildschirm aus', 'bildschirm_aus', false], ['Energiesparen', 'energiesparen', true],
    ['Neustart', 'neustart', true], ['Abmelden', 'abmelden', true], ['Herunterfahren', 'herunterfahren', true],
    ['Herunterfahren abbrechen', 'abbrechen', false],
  ];

  function reiterUebersicht(ctx) {
    const w = el('div', 'liste');
    const zustand = el('div', 'eintrag'), zeilen = el('div', 'pc-messwerte');
    zustand.append(kopf('Zustand'), zeilen);
    async function laden() {
      const m = await abfrage('messwerte');
      zeilen.replaceChildren();
      if (!m.ok) { zeilen.append(el('div', 'klein', m.text)); return; }
      if (m.cpu_prozent !== null && m.cpu_prozent !== undefined) zeilen.append(balken('Prozessor', m.cpu_prozent, `${m.cpu_prozent} %`));
      if (m.ram) zeilen.append(balken('Arbeitsspeicher', m.ram.genutzt_prozent, `${m.ram.genutzt_prozent} % von ${m.ram.gesamt_gb} GB`));
      for (const l of m.laufwerke || []) {
        const belegt = l.gesamt_gb ? Math.round(100 * (1 - l.frei_gb / l.gesamt_gb)) : 0;
        zeilen.append(balken(`Laufwerk ${l.name}`, belegt, `${l.frei_gb} GB frei von ${l.gesamt_gb} GB`));
      }
      if (m.akku) zeilen.append(balken('Akku', m.akku.prozent ?? 0, `${m.akku.prozent ?? '?'} %${m.akku.laedt ? ' · lädt' : (m.akku.netz ? ' · am Netz' : '')}`, 101));
      if ((m.netz || []).length) zeilen.append(el('div', 'klein', `Netzadresse: ${m.netz.join(', ')}`));
    }
    w.append(zustand);

    const schnell = el('div', 'eintrag'), ausgabe = rueckmeldung();
    schnell.append(kopf('Schnell', '* fragt vorher nach'));
    if (ctx.letzte.schreiben) {
      const reihe = el('div', 'knoepfe');
      for (const [text, aktion, fragt] of SCHNELL) {
        reihe.append(aktionsknopf(fragt ? `${text} *` : text, 'system', { aktion }, ausgabe, { klasse: aktion === 'herunterfahren' ? 'weg' : '' }));
      }
      schnell.append(reihe, ausgabe);
    } else {
      schnell.append(el('div', 'klein', 'Nur nachsehen: „Am PC handeln erlauben“ ist auf der Freigaben-Seite aus.'));
    }
    w.append(schnell);
    laden();
    return { wurzel: w, aktualisieren: laden, alleMs: 6000 };
  }

  /* ---------- Reiter: Wiedergabe ---------- */

  function reiterWiedergabe(ctx) {
    const w = el('div', 'liste');
    const box = el('div', 'eintrag'), ausgabe = rueckmeldung();
    async function laden() {
      const d = await abfrage('medien');
      box.replaceChildren();
      const m = d.medien;
      if (!d.ok || !m) {
        box.append(kopf('Nichts läuft'), el('div', 'klein', d.ok === false ? d.text
          : 'Am PC spielt gerade nichts, das Windows als Medienwiedergabe kennt (YouTube im Browser, VLC …). Braucht den Helfer ab Version 1.2.'));
        return;
      }
      const reihe = el('div', 'pc-wiedergabe');
      if (m.bild) { const img = el('img', 'pc-cover'); img.alt = ''; img.src = m.bild; reihe.append(img); }
      const text = el('div', 'pc-wiedergabe-text');
      text.append(el('div', 'titel', m.titel), el('div', 'klein', [m.kuenstler, m.app, m.laeuft ? 'läuft' : 'pausiert'].filter(Boolean).join(' · ')));
      reihe.append(text);
      box.append(reihe);
      if (m.dauer_s) {
        const gelaufen = m.laeuft ? Math.max(0, (Date.now() - Date.parse(m.stand)) / 1000) : 0;
        const p = Math.min(m.dauer_s, m.position_s + gelaufen);
        box.append(balken('', (100 * p) / m.dauer_s, `${mmss(p)} / ${mmss(m.dauer_s)}`, 101));
      }
      if (ctx.letzte.schreiben) {
        const nachher = () => setTimeout(laden, 1500);
        const k = el('div', 'knoepfe');
        k.append(aktionsknopf('⏮', 'medien', { befehl: 'vorheriger' }, ausgabe, { nachher }),
                 aktionsknopf(m.laeuft ? '⏸ Pause' : '▶ Weiter', 'medien', { befehl: m.laeuft ? 'pause' : 'weiter' }, ausgabe, { klasse: 'ja', nachher }),
                 aktionsknopf('⏭', 'medien', { befehl: 'naechster' }, ausgabe, { nachher }));
        box.append(k);
      }
    }
    w.append(box);
    const ton = el('div', 'eintrag');
    ton.append(kopf('Ton'));
    if (ctx.letzte.schreiben) {
      const k = el('div', 'knoepfe');
      k.append(aktionsknopf('Leiser', 'system', { aktion: 'leiser', schritte: 5 }, ausgabe),
               aktionsknopf('Lauter', 'system', { aktion: 'lauter', schritte: 5 }, ausgabe),
               aktionsknopf('Stumm / an', 'system', { aktion: 'stumm' }, ausgabe));
      ton.append(k);
    } else {
      ton.append(el('div', 'klein', 'Nur nachsehen: „Am PC handeln erlauben“ ist aus.'));
    }
    ton.append(ausgabe);
    w.append(ton);
    laden();
    return { wurzel: w, aktualisieren: laden, alleMs: 3000 };
  }

  /* ---------- Reiter: Fenster & Programme ---------- */

  function reiterFenster(ctx) {
    const w = el('div', 'liste'), darf = ctx.letzte.schreiben;

    // Offene Fenster
    const fBox = el('div', 'eintrag'), fListe = el('div'), fAus = rueckmeldung();
    fBox.append(kopf('Offene Fenster'), fListe, fAus);
    async function fLaden() {
      const d = await abfrage('fenster');
      fListe.replaceChildren();
      if (!d.ok) { fListe.append(el('div', 'klein', d.text)); return; }
      if (!d.fenster.length) fListe.append(el('div', 'klein', 'Keine sichtbaren Fenster.'));
      for (const f of d.fenster) {
        const z = el('div', 'pc-zeile'), t = el('div', 'pc-zeile-text');
        t.append(el('div', 'titel', f.titel || f.programm), el('div', 'klein', `${f.programm}${f.minimiert ? ' · minimiert' : ''}`));
        z.append(t);
        if (darf) {
          const k = el('div', 'knoepfe');
          k.append(aktionsknopf('Nach vorn', 'fenster_wechseln', { fenster: f.titel || f.programm }, fAus, { nachher: fLaden }),
                   aktionsknopf('Schließen *', 'fenster_schliessen', { fenster: f.titel || f.programm }, fAus, { klasse: 'weg' }));
          z.append(k);
        }
        fListe.append(z);
      }
    }
    w.append(fBox);

    // Programme starten
    const pBox = el('div', 'eintrag'), pListe = el('div'), pAus = rueckmeldung();
    pBox.append(kopf('Programm starten', 'Startmenü'));
    let pZaehler = 0;
    async function pLaden(suche) {
      const mein = ++pZaehler;
      const d = await abfrage('programme', { suche });
      if (mein !== pZaehler) return;                        // eine neuere Suche läuft schon
      pListe.replaceChildren();
      if (!d.ok) { pListe.append(el('div', 'klein', d.text)); return; }
      if (!d.programme.length) pListe.append(el('div', 'klein', suche ? `Nichts gefunden zu „${suche}“.` : 'Keine Programme.'));
      for (const p of d.programme.slice(0, 30)) {
        const z = el('div', 'pc-zeile pc-zeile-eng');
        z.append(el('div', 'pc-zeile-text', p.name));
        if (darf) z.append(aktionsknopf('Starten', 'starten', { programm: p.name }, pAus));
        pListe.append(z);
      }
    }
    const suche = eingabe('Programm suchen …', 'Suchen', (t) => pLaden(t));
    let warte = null;
    suche.feld.addEventListener('input', () => { clearTimeout(warte); warte = setTimeout(() => pLaden(suche.feld.value.trim()), 350); });
    pBox.append(suche.form, pListe, pAus);
    w.append(pBox);

    // Prozesse
    const zBox = el('div', 'eintrag'), zListe = el('div'), zAus = rueckmeldung();
    zBox.append(kopf('Prozesse', 'nach Speicher'));
    async function zLaden() {
      const d = await abfrage('prozesse');
      zListe.replaceChildren();
      if (!d.ok) { zListe.append(el('div', 'klein', d.text)); return; }
      for (const p of d.prozesse) {
        const z = el('div', 'pc-zeile pc-zeile-eng'), t = el('div', 'pc-zeile-text');
        t.append(el('span', '', p.name), el('span', 'klein', ` ${p.ram_mb} MB · Nr. ${p.pid}`));
        z.append(t);
        if (darf && !p.geschuetzt) z.append(aktionsknopf('Beenden *', 'prozess_beenden', { pid: p.pid, name: p.name }, zAus, { klasse: 'weg' }));
        zListe.append(z);
      }
    }
    const zeigen = knopf('Prozesse anzeigen', '', async (b) => { b.disabled = true; await zLaden(); b.disabled = false; b.textContent = 'Neu laden'; });
    const zk = el('div', 'knoepfe'); zk.append(zeigen);
    zBox.append(zk, zListe, zAus);
    w.append(zBox);

    w.append(el('p', 'klein', '* fragt vorher nach. Windows-Prozesse und der Friday-Helfer erscheinen hier ohne „Beenden“.'));
    fLaden(); pLaden('');
    return { wurzel: w, aktualisieren: fLaden, alleMs: 5000 };
  }

  /* ---------- Reiter: Dateien ---------- */

  function reiterDateien(ctx) {
    const w = el('div', 'liste'), darf = ctx.letzte.schreiben;
    let pfad = '', suche = '', offen = null;
    const leiste = el('div', 'eintrag'), liste = el('div', 'eintrag'), aus = rueckmeldung();

    async function laden() {
      const d = await abfrage('dateien', { pfad, suche });
      leiste.replaceChildren(); liste.replaceChildren(); offen = null;
      const zeile = el('div', 'pc-pfad');
      if (pfad || suche) {
        zeile.append(knopf('‹ Zurück', '', () => { if (suche) { suche = ''; } else { pfad = (d.oben || ''); } laden(); }));
      }
      zeile.append(el('span', 'pc-pfad-text', suche ? `Suche „${suche}“${pfad ? ` in ${pfad}` : ''}` : (pfad || 'Freigegebene Ordner')));
      leiste.append(zeile);
      const s = eingabe('Dateien suchen …', 'Suchen', (t) => { suche = t; return laden(); }, { start: suche });
      leiste.append(s.form);
      if (darf && pfad && !suche) {
        const n = eingabe('Neuer Ordner …', 'Anlegen', async (name) => {
          if (!name) return;
          const r = await tun('ordner_anlegen', { pfad: `${pfad}/${name}` });
          aus.zeigen(r);
          if (r.ok) laden();
        });
        leiste.append(n.form);
      }
      if (!d.ok) { liste.append(el('div', 'klein', d.text)); return; }
      if (!d.eintraege.length) liste.append(el('div', 'klein', suche ? 'Nichts gefunden.' : 'Der Ordner ist leer.'));
      for (const e of d.eintraege) liste.append(zeileFuer(e));
      if (d.mehr) liste.append(el('div', 'klein', 'Es gibt noch mehr Treffer — die Suche eingrenzen.'));
    }

    function zeileFuer(e) {
      const z = el('div', 'pc-datei'), kopfZeile = el('div', 'pc-zeile');
      const t = el('div', 'pc-zeile-text');
      t.append(el('div', 'titel', `${e.ordner ? '📁 ' : ''}${e.name}`),
               el('div', 'klein', [e.ordner ? '' : groesse(e.groesse), e.geaendert.replace('T', ' '), suche ? e.pfad : ''].filter(Boolean).join(' · ')));
      kopfZeile.append(t);
      const aktionen = el('div', 'pc-datei-aktionen'); aktionen.hidden = true;
      kopfZeile.addEventListener('click', () => {
        if (e.ordner) { pfad = e.pfad; suche = ''; laden(); return; }
        if (offen && offen !== aktionen) offen.hidden = true;
        aktionen.hidden = !aktionen.hidden;
        offen = aktionen.hidden ? null : aktionen;
      });
      z.append(kopfZeile, aktionen);
      if (!e.ordner) {
        const k = el('div', 'knoepfe');
        k.append(aktionsknopf('Öffnen', 'oeffnen', { pfad: e.pfad }, aus));
        if (darf) {
          const formularZu = (feld) => { const f = aktionen.querySelector('.pc-eingabe'); if (f) f.remove(); };
          const mitFormular = (text, platzhalter, start, senden_) => knopf(text, '', () => {
            formularZu();
            const f = eingabe(platzhalter, 'OK', async (wert) => {
              if (!wert) return;
              const r = await senden_(wert);
              aus.zeigen(r);
              if (r.ok) { formularZu(); laden(); }
            }, { start });
            aktionen.append(f.form); f.feld.focus();
          });
          k.append(mitFormular('Umbenennen *', 'Neuer Name', e.name, (v) => tun('umbenennen', { pfad: e.pfad, name: v })),
                   mitFormular('Verschieben *', 'Zielordner, z. B. Dokumente', pfad, (v) => tun('verschieben', { von: e.pfad, nach: v })),
                   mitFormular('Kopieren', 'Zielordner, z. B. Dokumente', pfad, (v) => tun('kopieren', { von: e.pfad, nach: v })),
                   aktionsknopf('Löschen *', 'loeschen', { pfad: e.pfad }, aus, { klasse: 'weg' }));
        }
        aktionen.append(k);
      }
      return z;
    }

    w.append(leiste, liste, aus, el('p', 'klein', '* fragt vorher nach. Gelöschtes kommt in den Papierkorb. Nur die freigegebenen Ordner sind sichtbar.'));
    laden();
    return { wurzel: w, alleMs: 0 };
  }

  /* ---------- Reiter: Bildschirm ---------- */

  function reiterBildschirm() {
    const w = el('div', 'liste'), box = el('div', 'eintrag');
    const hinweis = el('div', 'klein', 'Nur für dich — das Bild geht nicht an Gemini.');
    const img = el('img', 'pc-bild'); img.alt = 'Bildschirm des PCs'; img.hidden = true;
    let laeuft = false;
    async function holen() {
      if (laeuft) return;
      laeuft = true;
      try {
        const r = await fetch('/api/pc/bildschirm', { cache: 'no-store' });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Fehler ${r.status}`);
        if (img.src) URL.revokeObjectURL(img.src);
        img.src = URL.createObjectURL(await r.blob());
        img.hidden = false; hinweis.textContent = `Stand ${uhr(new Date().toISOString())}`;
      } catch (e) { hinweis.textContent = e.message; }
      laeuft = false;
    }
    const auto = el('input'); auto.type = 'checkbox';
    const autoText = el('label', 'check klein'); autoText.append(auto, el('span', '', 'Automatisch aktualisieren (alle 4 Sekunden)'));
    const k = el('div', 'knoepfe'); k.append(knopf('Bildschirm ansehen', 'ja', holen));
    box.append(kopf('Bildschirm'), k, autoText, hinweis, img);
    w.append(box);
    return { wurzel: w, aktualisieren: () => { if (auto.checked) holen(); }, alleMs: 4000 };
  }

  /* ---------- Reiter: Senden ---------- */

  function reiterSenden(ctx) {
    const w = el('div', 'liste');
    if (!ctx.letzte.schreiben) {
      const b = el('div', 'eintrag');
      b.append(kopf('Nur nachsehen'), el('div', 'klein', '„Am PC handeln erlauben“ ist auf der Freigaben-Seite aus — hier kann nichts gesendet werden.'));
      w.append(b);
    }
    const meldung = el('div', 'eintrag'), mAus = rueckmeldung();
    meldung.append(kopf('Meldung auf dem PC', 'erscheint auf dem Bildschirm'));
    const m = eingabe('Text der Meldung …', 'Anzeigen', async (t) => { if (t) mAus.zeigen(await tun('meldung', { text: t })); });
    meldung.append(m.form, mAus);

    const web = el('div', 'eintrag'), wAus = rueckmeldung();
    web.append(kopf('Webseite am PC öffnen', 'im Browser des PCs'));
    const u = eingabe('https://…', 'Öffnen', async (t) => { if (t) wAus.zeigen(await tun('webseite', { adresse: t })); });
    u.feld.type = 'url'; u.feld.inputMode = 'url';
    web.append(u.form, wAus);

    const ablage = el('div', 'eintrag'), aAus = rueckmeldung();
    ablage.append(kopf('Zwischenablage', 'des PCs'));
    const setzen = eingabe('Text für die Zwischenablage des PCs …', 'Auf den PC legen', async (t) => { if (t) aAus.zeigen(await tun('zwischenablage', { text: t })); }, { mehrzeilig: true });
    const lesenBox = el('pre', 'pc-ablage'); lesenBox.hidden = true;
    const lesenKnopf = knopf('Zwischenablage des PCs lesen', '', async (b) => {
      b.disabled = true;
      const d = await abfrage('zwischenablage');
      b.disabled = false;
      lesenBox.hidden = false;
      lesenBox.textContent = d.ok ? (d.text || '(leer)') : d.text;
      if (d.ok && d.gekuerzt) lesenBox.textContent += '\n… (gekürzt)';
    });
    const kopieren = knopf('Hierher kopieren', '', async (b) => {
      try { await navigator.clipboard.writeText(lesenBox.textContent); b.textContent = 'Kopiert'; } catch (_) { b.textContent = 'Bitte markieren und kopieren'; }
    });
    const lk = el('div', 'knoepfe'); lk.append(lesenKnopf, kopieren);
    ablage.append(setzen.form, aAus, lk, lesenBox);

    if (ctx.letzte.schreiben) w.append(meldung, web);
    w.append(ablage);
    return { wurzel: w, alleMs: 0 };
  }

  /* ---------- Reiter: Verlauf ---------- */

  function reiterVerlauf(ctx) {
    const w = el('div', 'liste'), box = el('div', 'eintrag');
    let filter = '';
    const auswahl = el('select');
    for (const [wert, text] of [['', 'Alles'], ['direkt', 'Ausgeführt'], ['bestaetigt', 'Bestätigt'], ['abgelehnt', 'Abgelehnt'], ['fehler', 'Fehler']]) {
      const o = el('option', '', text); o.value = wert; auswahl.append(o);
    }
    auswahl.addEventListener('change', () => { filter = auswahl.value; zeichnen(window.FridayPc.letzte()); });
    const liste = el('div');
    box.append(kopf('Zuletzt am PC'), auswahl, liste);
    function zeichnen(letzte) {
      liste.replaceChildren();
      const v = (letzte.verlauf || []).filter((x) => !filter || x.wie === filter);
      if (!v.length) liste.append(el('div', 'klein', 'Nichts.'));
      for (const x of v) {
        const z = el('div', 'pc-verlauf');
        z.append(el('span', 'leise', `${uhr(x.zeit)} · ${WIE[x.wie] || x.wie}`), el('div', '', x.beschreibung));
        if (x.ergebnis && x.wie !== 'direkt') z.append(el('div', 'klein', x.ergebnis));
        liste.append(z);
      }
    }
    zeichnen(ctx.letzte);
    w.append(box);
    if (ctx.letzte.offen.length) w.prepend(el('p', 'klein', `${ctx.letzte.offen.length} Rückfrage(n) warten — unten über der Leiste.`));
    w.append(el('p', 'klein', 'Den Helfer einrichten oder aktualisieren: Mehr → Freigaben → PC → Einrichten.'));
    return { wurzel: w, aktualisieren: (c) => zeichnen(c.letzte), alleMs: 4000 };
  }

  /* ---------- Rahmen: Kopf, Reiter, Aufbau ---------- */

  const REITER = [
    ['uebersicht', 'Übersicht', reiterUebersicht], ['wiedergabe', 'Wiedergabe', reiterWiedergabe],
    ['fenster', 'Fenster', reiterFenster], ['dateien', 'Dateien', reiterDateien],
    ['bildschirm', 'Bildschirm', reiterBildschirm], ['senden', 'Senden', reiterSenden], ['verlauf', 'Verlauf', reiterVerlauf],
  ];
  const gemerkt = () => { try { return sessionStorage.getItem('pcReiter') || 'uebersicht'; } catch (_) { return 'uebersicht'; } };
  const merken = (n) => { try { sessionStorage.setItem('pcReiter', n); } catch (_) { /* ohne Speicher geht es auch */ } };

  let huelle = null;          // { ziel, kopf, nav, inhalt, reiter, name, status, statusZeit, letzte }

  async function statusHolen(h) {
    try {
      const r = await fetch('/api/pc/status', { cache: 'no-store' });
      h.status = r.ok ? await r.json() : { ok: false, text: (await r.json().catch(() => ({}))).detail || 'Nicht freigegeben.' };
    } catch (_) { h.status = { ok: false, text: 'Friday ist nicht erreichbar.' }; }
    h.statusZeit = Date.now();
    kopfZeichnen(h);
    if (huelle === h && !h.reiter) reiterWaehlen(h, h.name);       // der PC ist inzwischen da (oder der Status kam erst jetzt)
  }

  function kopfZeichnen(h) {
    const s = h.status, k = h.kopf;
    k.replaceChildren();
    if (s?.ok) {
      k.append(el('div', 'titel', `${s.rechner} — ${s.gesperrt ? 'gesperrt' : 'bereit'}`),
        el('div', 'klein', [s.benutzer, s.system, s.laufzeit_s != null ? `an seit ${dauer(s.laufzeit_s)}` : '', `Helfer ${s.version}`,
          `Friday-Browser ${s.browser ? 'offen' : 'zu'}`].filter(Boolean).join(' · ')));
      if (s.helfer_neu) k.append(el('div', 'klein pc-update', `Neuer Helfer ${s.helfer_neu} verfügbar — Mehr → Freigaben → PC → Einrichten, den Befehl am PC ausführen.`));
    } else {
      k.append(el('div', 'titel status-fehler', 'PC nicht erreichbar'), el('div', 'klein', s?.text || 'wird geprüft …'));
    }
  }

  function reiterWaehlen(h, name) {
    const def = REITER.find((r) => r[0] === name) || REITER[0];
    h.name = def[0]; merken(h.name);
    for (const b of h.nav.querySelectorAll('button')) b.classList.toggle('aktiv', b.dataset.ansicht === h.name);
    h.inhalt.replaceChildren();
    h.reiter = null;
    if (h.name !== 'verlauf' && !h.status?.ok) {
      h.inhalt.append(el('p', 'leer-klein', h.status === null ? 'lädt …'
        : 'Der PC ist gerade nicht erreichbar — er ist aus, schläft, oder der Helfer läuft nicht.'));
      return;
    }
    h.reiter = def[2]({ letzte: h.letzte });
    h.reiter.zuletzt = Date.now();
    h.inhalt.append(h.reiter.wurzel);
  }

  function aufbauen(ziel, letzte) {
    ziel.replaceChildren();
    const h = { ziel, letzte, kopf: el('div', 'eintrag pc-kopf-block'), nav: el('div', 'st-nav pc-nav'), inhalt: el('div'), status: null, name: gemerkt() };
    for (const [id, text] of REITER) {
      const b = el('button', '', text); b.type = 'button'; b.dataset.ansicht = id;
      b.addEventListener('click', () => { h.letzte = huelle?.letzte || h.letzte; reiterWaehlen(h, id); });
      h.nav.append(b);
    }
    ziel.append(h.kopf, h.nav, h.inhalt);
    huelle = h;
    kopfZeichnen(h);
    reiterWaehlen(h, h.name);
    statusHolen(h);
  }

  function zeichnen(ziel, ctx) {
    const letzte = ctx.letzte;
    if (!letzte.aktiv) {
      huelle = null;
      const k = el('div', 'eintrag');
      k.append(el('div', 'titel', 'PC nicht freigegeben'),
        el('div', 'klein', 'Mehr → Freigaben → PC: einschalten und „Einrichten“ — ein Befehl auf dem PC genügt.'));
      ziel.replaceChildren(k);
      return;
    }
    if (!huelle || huelle.ziel !== ziel || !ziel.contains(huelle.kopf) || ctx.neuLaden) { aufbauen(ziel, letzte); return; }
    huelle.letzte = letzte;
    if (Date.now() - huelle.statusZeit > 30000) statusHolen(huelle);
    const r = huelle.reiter;
    if (r?.aktualisieren && r.alleMs && Date.now() - r.zuletzt >= r.alleMs) { r.zuletzt = Date.now(); r.aktualisieren({ letzte }); }
  }

  window.FridayPcSeite = { zeichnen };
})();
