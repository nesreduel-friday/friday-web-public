/* Extras → hphome: den Server ansehen und verwalten.

   Alles kommt von `/api/hphome` (Friday liest, was der Helfer auf hphome meldet) und geht als Auftrag
   zurück — der Browser führt nichts selbst aus. Fremder Text (Containernamen, Logs) landet nie als HTML:
   überall `textContent`. Links nur http(s). Die Claude-Sitzungen zeichnet `claude.js` in `#hphome-claude`. */
(() => {
  const { $ } = window.Friday;

  const el = (tag, klasse, text) => {
    const e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };
  const sicherLink = (url) => typeof url === 'string' && /^https?:\/\//.test(url);
  const oeffnen = (text, url, klasse = 'hp-oeffnen') => {
    const a = el('a', klasse, text);
    if (sicherLink(url)) { a.href = url; a.target = '_blank'; a.rel = 'noopener'; }
    return a;
  };
  const anruf = async (url, methode = 'POST') => {
    try {
      const r = await fetch(url, { method: methode, headers: { 'Content-Type': 'application/json' },
                                   body: methode === 'POST' ? '{}' : undefined });
      const d = await r.json().catch(() => ({}));
      return { ok: r.ok && d.ok !== false, text: d.text || d.detail || (r.ok ? '' : `Fehler ${r.status}`) };
    } catch (_) {
      return { ok: false, text: 'Friday ist gerade nicht erreichbar.' };
    }
  };

  const dauer = (s) => {
    if (s == null) return '–';
    const t = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return t ? `${t} T ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
  };
  const gb = (x) => (x == null ? '–' : `${x >= 10 ? Math.round(x) : Number(x).toFixed(1)} GB`);
  const vor = (ts) => {
    if (!ts) return 'unbekannt';
    const s = Math.max(0, Date.now() / 1000 - ts);
    return s >= 86400 ? `vor ${Math.floor(s / 86400)} Tagen` : s >= 3600 ? `vor ${Math.floor(s / 3600)} h` : `vor ${Math.floor(s / 60)} min`;
  };

  let stand = null;            // GET /api/hphome
  let meldung = '';            // Ergebnis der letzten Aktion
  let beschaeftigt = 0;        // laufende Aktionen: solange wird nicht neu gezeichnet
  let neustartSeit = 0;        // ms; gesetzt erst, wenn der Auftrag „hphome neu starten“ angenommen wurde
  const NEUSTART_MS = 240000;  // so lange gilt „startet neu“; danach werden Fehler wieder gemeldet
  const armiert = new Set();   // Knöpfe, die gerade auf den nächsten Tipp warten
  const logs = {};             // Containername → Logtext (bleibt stehen, bis man ihn schließt)

  const blockiert = () => beschaeftigt > 0 || armiert.size > 0
    || !!document.querySelector('#hp-dienste details[open], #hp-kopf details[open]');

  /* Ein Knopf, der mehrere Tipps braucht: jeder Tipp zeigt, was als Nächstes passiert; nach 6 s ohne Tipp
     ist alles wieder wie vorher. Während er wartet, wird die Seite nicht neu gezeichnet. */
  const mehrfach = (b, stufen, aktion) => {
    const start = b.textContent;
    let stufe = 0, uhr = null;
    b.dataset.label = start;   // `tun` stellt diese Beschriftung wieder her, nie den Text einer Bestätigungsstufe
    const zurueck = () => { clearTimeout(uhr); stufe = 0; b.textContent = start; b.classList.remove('warnt'); armiert.delete(b); };
    b.addEventListener('click', async () => {
      if (b.disabled) return;
      if (stufe < stufen.length) {
        b.textContent = stufen[stufe++];
        b.classList.add('warnt');
        armiert.add(b);
        clearTimeout(uhr);
        uhr = setTimeout(zurueck, 6000);
        return;
      }
      clearTimeout(uhr);
      armiert.delete(b);
      stufe = 0;               // ab dem Auslösen gilt jeder weitere Tipp wieder als erster (nur Bestätigung, nie Auslösen)
      // Danach immer zurück auf Stufe 0: ein alter Knopf, der stehen bleibt (Seite verlassen, Neustart), verlangt wieder die Bestätigung.
      try { await aktion(b); } finally { zurueck(); }
    });
    return b;
  };
  const knopf = (text, klasse) => { const b = el('button', klasse || '', text); b.type = 'button'; return b; };

  async function tun(b, url, methode = 'POST') {
    beschaeftigt++;
    b.disabled = true;
    const alt = b.dataset.label ?? b.textContent;
    b.textContent = '…';
    let r;
    try {
      try { r = await anruf(url, methode); } finally { beschaeftigt--; }
      meldung = r.text || (r.ok ? 'Erledigt.' : 'Das hat nicht geklappt.');
      await laden(true);
    } finally {
      // Erst nach der Aktualisierung wieder bedienbar: solange Auftrag und Neuladen laufen, ist nichts antippbar.
      b.textContent = alt;
      b.disabled = false;
    }
    return r.ok;
  }

  /* ---------- Kopf ---------- */
  function zeichneKopf() {
    const ziel = $('hp-kopf');
    ziel.replaceChildren();
    if (!stand) return;
    if (neustartSeit && Date.now() - neustartSeit < NEUSTART_MS) {
      ziel.append(el('p', 'status-fehler', 'hphome startet neu — das dauert etwa zwei Minuten. Diese Seite meldet sich von selbst zurück.'));
    }
    if (!stand.helfer) {
      ziel.append(el('p', 'status-fehler', stand.text || 'Der Helfer auf hphome läuft nicht.'));
    }
    const h = stand.host || {};
    const kacheln = el('div', 'hp-kacheln');
    const wert = (gross, klein) => { const d = el('div', 'hp-wert'); d.append(el('b', '', gross), el('span', '', klein)); kacheln.append(d); };
    wert(dauer(h.uptime_s), 'läuft seit');
    if (h.last) wert(h.last.map((x) => x.toFixed(2)).join(' / '), `Last (${h.kerne || '?'} Kerne)`);
    if (h.ram) wert(`${((h.ram.gesamt_mb - h.ram.verfuegbar_mb) / 1024).toFixed(1)} / ${(h.ram.gesamt_mb / 1024).toFixed(1)} GB`, 'Arbeitsspeicher');
    if (h.temperatur_c != null) wert(`${Math.round(h.temperatur_c)} °C`, 'Temperatur');
    ziel.append(kacheln);
    for (const p of h.platten || []) {
      const z = el('div', 'hp-platte');
      // frei_gb kommt vom Helfer (nutzbar, ohne root-Reserve, wie df) — nie gesamt − belegt; fehlt es: „?“.
      const frei = typeof p.frei_gb === 'number' ? gb(p.frei_gb) : '? GB';
      z.append(el('div', 'hp-zeile-oben', `Platte ${p.pfad}: ${gb(p.belegt_gb)} von ${gb(p.gesamt_gb)} (${Math.round(p.prozent)} %), ${frei} frei`));
      const balken = el('div', 'hp-balken');
      const fuell = el('div', 'hp-fuellung' + (p.prozent >= 90 ? ' warn' : ''));
      fuell.style.width = `${Math.min(100, Math.max(0, p.prozent))}%`;
      balken.append(fuell);
      z.append(balken);
      ziel.append(z);
    }
    const hinweise = [];
    if (h.neustart_noetig) hinweise.push('Ein Neustart ist nötig (Updates warten darauf).');
    if (h.updates) hinweise.push(`${h.updates} Updates stehen an (nur Anzeige).`);
    for (const t of hinweise) ziel.append(el('p', 'leise', t));

    const menue = el('details', 'hp-menue');
    menue.append(el('summary', '', '⋯'));
    const reihe = el('div', 'knoepfe');
    const neu = mehrfach(knopf('hphome neu starten', 'weg'),
      ['Sicher? Nochmal tippen', 'Alle Dienste ~2 min weg, auch diese Seite — jetzt neu starten'],
      async (b) => {
        // „startet neu“ erst, wenn der Auftrag angenommen wurde — bei einem Fehler bleibt alles wie es war.
        if (await tun(b, '/api/hphome/host/neustart')) { neustartSeit = Date.now(); zeichneKopf(); }
      });
    neu.disabled = !stand.helfer;
    reihe.append(neu);
    menue.append(reihe);
    ziel.append(menue);
    if (meldung) ziel.append(el('p', 'leise', meldung));
  }

  /* ---------- Dienste ---------- */
  function kachel(c) {
    const k = el('div', 'hp-dienst');
    const kopf = el('div', 'hp-dienst-kopf');
    const laeuft = c.zustand === 'running';
    const gelb = laeuft && (c.gesundheit === 'unhealthy' || c.gesundheit === 'starting') || c.zustand === 'restarting';
    kopf.append(el('span', 'punkt ' + (gelb ? 'gelb' : laeuft ? 'gut' : 'weg')));
    kopf.append(el('div', 'titel', c.titel || c.name));
    if (c.link) kopf.append(oeffnen('Öffnen', c.link));
    const menue = el('details', 'hp-menue');
    menue.append(el('summary', '', '⋯'));
    const reihe = el('div', 'knoepfe');
    const url = (aktion) => `/api/hphome/container/${encodeURIComponent(c.name)}/${aktion}`;
    const ok = !!stand.helfer;
    const neu = mehrfach(knopf('Neustart'), ['Sicher? Nochmal tippen'], (b) => tun(b, url('neustart')));
    neu.disabled = !ok;
    reihe.append(neu);
    // „restarting“ läuft nicht, lässt sich aber stoppen (ein Start wäre dort sinnlos).
    if ((laeuft || c.zustand === 'restarting') && c.darf_stoppen) {
      const stopp = mehrfach(knopf('Stopp', 'weg'), ['Wirklich stoppen?', 'Jetzt endgültig stoppen'], (b) => tun(b, url('stopp')));
      stopp.disabled = !ok;
      reihe.append(stopp);
    }
    if (!laeuft && c.zustand !== 'restarting') {
      const start = knopf('Start');
      start.disabled = !ok;
      start.addEventListener('click', () => tun(start, url('start')));
      reihe.append(start);
    }
    const lg = knopf('Logs');
    lg.disabled = !ok;
    lg.addEventListener('click', async () => {
      menue.open = false;
      lg.disabled = true;
      const r = await anruf(`/api/hphome/container/${encodeURIComponent(c.name)}/logs`);   // POST: Herkunft wird geprüft
      logs[c.name] = r.text || '(keine Zeilen)';
      lg.disabled = false;
      zeichneDienste();
    });
    reihe.append(lg);
    menue.append(reihe);
    kopf.append(menue);
    k.append(kopf);

    const teile = [];
    if (laeuft && c.seit) teile.push(`läuft ${dauer(Date.now() / 1000 - c.seit)}`);
    else if (c.absichtlich_gestoppt) teile.push('gestoppt (von dir)');
    else teile.push(c.zustand || 'unbekannt');
    if (c.gesundheit && c.gesundheit !== 'healthy') teile.push(c.gesundheit);
    if (c.cpu_prozent != null) teile.push(`CPU ${c.cpu_prozent.toFixed(1)} %`);
    if (c.ram_mb) teile.push(`${Math.round(c.ram_mb)} MB`);
    k.append(el('div', 'leise klein', teile.join(' · ')));
    if (c.info) k.append(el('div', 'leise klein', c.info));
    if (logs[c.name] !== undefined) {
      k.append(el('pre', 'hp-log', logs[c.name]));
      const zu = knopf('Logs schließen');
      zu.addEventListener('click', () => { delete logs[c.name]; zeichneDienste(); });
      k.append(zu);
    }
    return k;
  }

  function zeichneDienste() {
    const ziel = $('hp-dienste');
    ziel.replaceChildren();
    if (!stand) { ziel.append(el('p', 'leer-klein', 'lädt …')); return; }
    if (stand.container_ok === false) ziel.append(el('p', 'status-fehler', 'Docker antwortet nicht.'));
    else if (!(stand.container || []).length) ziel.append(el('p', 'leer-klein', stand.helfer ? 'Keine Container.' : 'Keine Angaben.'));
    for (const c of stand.container || []) ziel.append(kachel(c));
    for (const e of stand.extra || []) {
      const k = el('div', 'hp-dienst');
      const kopf = el('div', 'hp-dienst-kopf');
      kopf.append(el('span', 'punkt gut'), el('div', 'titel', e.titel), oeffnen('Öffnen', e.link));
      k.append(kopf, el('div', 'leise klein', e.info || ''));
      ziel.append(k);
    }
  }

  /* ---------- Tailnet, Sicherung, Speicher ---------- */
  const abschnitt = (id, titel) => { const z = $(id); z.replaceChildren(); z.append(el('h2', 'hp-titel', titel)); return z; };

  function zeichneRest() {
    if (!stand) return;
    const t = stand.tailnet;
    const zt = abschnitt('hp-tailnet', 'Tailnet');
    if (!t || !(t.geraete || []).length) zt.append(el('p', 'leer-klein', 'Keine Angaben.'));
    else for (const g of t.geraete) {
      const z = el('div', 'hp-zeile');
      z.append(el('span', '', `${g.name}${g.selbst ? ' (dieser Server)' : ''}${g.os ? ' · ' + g.os : ''}`),
               el('span', 'leise', g.online ? 'online' : `zuletzt ${vor(g.zuletzt)}`));
      zt.append(z);
    }
    const s = stand.sicherung;
    const zs = abschnitt('hp-sicherung', 'Sicherung von Friday');
    zs.append(el('p', 'leise', s ? `Zuletzt ${vor(s.zuletzt)} · ${s.dateien} Dateien · ${s.groesse_mb} MB` : 'Keine Angaben.'));

    const sp = stand.speicher || {};
    const zp = abschnitt('hp-speicher', 'Speicher');
    if (!(sp.ordner || []).length) zp.append(el('p', 'leer-klein', 'Die größten Ordner werden noch ermittelt …'));
    for (const o of sp.ordner || []) {
      const z = el('div', 'hp-zeile');
      z.append(el('span', '', o.pfad), el('span', 'leise', gb(o.groesse_gb)));
      zp.append(z);
    }
    const frei = sp.docker ? (sp.docker.build_cache_gb || 0) + (sp.docker.images_frei_gb || 0) : null;
    const platz = mehrfach(knopf(frei == null ? 'Docker aufräumen' : `Docker aufräumen (≈ ${gb(frei)})`),
      ['Sicher? Nur ungenutzte Images und Build-Cache über 24 h'], (b) => tun(b, '/api/hphome/platz/schaffen'));
    platz.disabled = !stand.helfer || (frei != null && frei < 0.05);
    const reihe = el('div', 'knoepfe');
    reihe.append(platz);
    zp.append(reihe);
    const hinweis = el('p', 'leise klein', 'Duplikate aufräumen und Serien/Filme endgültig löschen: Unter-Tab „Medien“.');
    zp.append(hinweis);
  }

  function zeichnen() {
    zeichneKopf();
    zeichneDienste();
    zeichneRest();
  }

  const sichtbar = () => {
    const r = $('r-hphome');
    return location.hash === '#studio' && r && !r.hidden;
  };

  async function laden(erzwingen = false) {
    if (!sichtbar()) return;
    if (neustartSeit && Date.now() - neustartSeit > NEUSTART_MS) neustartSeit = 0;
    try {
      const r = await fetch('/api/hphome');
      stand = await r.json();
      // Nach dem Host-Neustart: sobald der Helfer wieder meldet und die Laufzeit kurz ist, ist er zurück.
      if (neustartSeit && stand.helfer && stand.host && stand.host.uptime_s != null
          && stand.host.uptime_s < (Date.now() - neustartSeit) / 1000 + 60) neustartSeit = 0;
    } catch (_) {
      if (!neustartSeit) stand = { helfer: false, text: 'Friday ist gerade nicht erreichbar.', container: [], extra: [] };
      else return;                           // beim Neustart ist „nicht erreichbar“ erwartet
    }
    if (erzwingen || !blockiert()) zeichnen();
  }

  window.addEventListener('hashchange', () => laden(true));
  document.addEventListener('DOMContentLoaded', () => laden(true));
  for (const b of document.querySelectorAll('#studio .reiter button')) {
    b.addEventListener('click', () => { if (b.dataset.reiter === 'hphome') { meldung = ''; setTimeout(() => laden(true), 0); } });
  }
  setInterval(() => laden(false), 5000);

  // Unter-Tabs Server/Medien innerhalb von hphome — eigene Klasse .unterreiter
  // (nicht .reiter), sonst greifen die generischen `#studio .reiter button`-
  // Umschalter aus studio.js/garantie.js/pc.js versehentlich mit zu (Absturz,
  // siehe LEHREN 27.09.2026).
  // Reiter „Handy“ (16.10.2026): Link zur Handy-Steuerung (host-helfer/handy_steuerung.py, Port 8444 am selben Tailnet-Namen).
  // Gebaut hier statt in index.html, damit der Reiter an einer Stelle steht. Der Dienst läuft nur auf Zuruf.
  const handyKnopf = el('button', '', 'Handy');
  handyKnopf.type = 'button';
  handyKnopf.dataset.unterreiter = 'handy';
  document.querySelector('#r-hphome .unterreiter').append(handyKnopf);
  const handyBlock = el('div', '');
  handyBlock.id = 'hp-handy';
  handyBlock.hidden = true;
  const handyStart = knopf('Handy-Steuerung starten');
  const handyLink = oeffnen('Handy öffnen', `https://${location.hostname}:8444/`);
  handyLink.hidden = true;
  handyStart.addEventListener('click', async () => {
    if (await tun(handyStart, '/api/hphome/handy/start')) {
      handyLink.hidden = false;
      setTimeout(() => { handyLink.hidden = true; }, 20000);
    }
  });
  handyBlock.append(
    el('h2', 'hp-titel', 'Handy steuern'),
    el('p', 'leise', 'Zeigt den Bildschirm des Samsung live und lässt dich klicken, wischen und tippen. Solange die Seite offen ist, '
      + 'pausiert der Snap-Helfer. Das Handy muss per USB an hphome hängen.'),
    handyStart,
    handyLink,
    el('p', 'leise klein', 'Der Dienst läuft nur auf Knopfdruck und beendet sich nach 20 Sekunden von selbst, wenn keine Seite verbunden ist '
      + '(sonst bleibt Snapchat blockiert).'));
  $('hp-medien').after(handyBlock);

  for (const b of document.querySelectorAll('#r-hphome .unterreiter button')) {
    b.addEventListener('click', () => {
      for (const x of document.querySelectorAll('#r-hphome .unterreiter button')) x.classList.toggle('aktiv', x === b);
      const ziel = b.dataset.unterreiter;
      $('hp-server').hidden = ziel !== 'server';
      $('hp-medien').hidden = ziel !== 'medien';
      handyBlock.hidden = ziel !== 'handy';
      if (ziel === 'medien' && window.FridayMedien) {
        window.FridayMedien.ladeDuplikate();
        window.FridayMedien.ladeLoeschliste();
      }
    });
  }
})();
