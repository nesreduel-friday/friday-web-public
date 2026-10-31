/* Studio: Kanal und Videos — Zahlen, Publikum und die Werkstatt für neue Videos.

   Vier Ansichten: Überblick (Kanal im Zeitraum), Videos (jedes einzeln, mit
   Rückhaltekurve), Publikum (woher, wer, womit) und Werkstatt (was gerade
   entsteht und wartet).

   Veröffentlichen (Werkstatt) läuft nur über einen eigenen Knopf je Video,
   mit einer echten Rückfrage davor (wie beim Garantie-Abschicken) — der
   Kanal ist öffentlich, und veröffentlicht ist veröffentlicht. Alles per
   textContent, nie als HTML: Titel und Themen kommen aus einem Modell oder
   von YouTube. */
(() => {
  const { $ } = window.Friday;

  /* ---------- kleine Helfer ---------- */
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
  const SVG = 'http://www.w3.org/2000/svg';
  const svg = (tag, attr, klasse) => {
    const e = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attr || {})) e.setAttribute(k, v);
    if (klasse) e.setAttribute('class', klasse);
    return e;
  };
  const zeit = (iso) => {
    try { return new Date(iso).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }); }
    catch (_) { return ''; }
  };
  const uhr = (iso) => {
    try { return new Date(iso).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }); }
    catch (_) { return ''; }
  };
  const datum = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}` : '?');
  const kurz = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.` : '');
  const nf = new Intl.NumberFormat('de-DE');
  const zahl = (n) => nf.format(Math.round(n || 0));
  const dez = (n, s = 1) => (n || 0).toLocaleString('de-DE', { minimumFractionDigits: s, maximumFractionDigits: s });
  const prozent = (n) => `${dez(n, 1)} %`;
  const dauer = (s) => {
    const g = Math.round(s || 0);
    return `${Math.floor(g / 60)}:${String(g % 60).padStart(2, '0')}`;
  };
  const wiedergabe = (min) => (min >= 60 ? `${dez(min / 60, 1)} Std.` : `${zahl(min)} Min.`);
  const laender = (() => {
    try { return new Intl.DisplayNames(['de'], { type: 'region' }); } catch (_) { return null; }
  })();
  const landName = (code) => { try { return (laender && laender.of(code)) || code; } catch (_) { return code; } };

  /* ---------- Zustand ---------- */
  const SPEICHER = 'friday-studio';
  const lesen = () => { try { return JSON.parse(localStorage.getItem(SPEICHER)) || {}; } catch (_) { return {}; } };
  const merken = () => { try { localStorage.setItem(SPEICHER, JSON.stringify({ ansicht, tage })); } catch (_) { /* egal */ } };
  const gemerkt = lesen();
  let ansicht = ['ueberblick', 'videos', 'publikum', 'werkstatt'].includes(gemerkt.ansicht) ? gemerkt.ansicht : 'ueberblick';
  let tage = [7, 28, 90, 365, 0].includes(gemerkt.tage) ? gemerkt.tage : 28;
  let offenesVideo = null;            // Kennung, wenn ein Video im Detail steht
  let metrik = 'aufrufe';             // Kennzahl des Verlaufsdiagramms
  let sortierung = 'neu';
  let suche = '';
  let art = 'alle';                   // alle | kurz | lang
  let sichtbar = 12;                  // so viele Videos zeigt die Liste
  let lauf = 0;                       // Nummer der jüngsten Anfrage — ältere Antworten verfallen
  let takt = null;

  const inhalt = () => $('studio-liste');
  const aktiv = () => location.hash === '#studio'
    && document.querySelector('#studio .reiter button[data-reiter="studio"]')?.classList.contains('aktiv');

  async function holen(url) {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }

  /* ---------- Bausteine ---------- */
  function block(titel, erklaerung) {
    const b = el('div', 'block');
    b.append(el('h2', '', titel));
    if (erklaerung) b.append(el('p', 'leise', erklaerung));
    return b;
  }

  function aenderung(jetzt, davor, punkte) {
    if (davor === null || davor === undefined) return null;
    const s = el('span', 'st-delta');
    if (punkte) {
      const d = jetzt - davor;
      if (Math.abs(d) < 0.5) { s.classList.add('gleich'); s.textContent = '± 0 Pkt.'; }
      else { s.classList.add(d > 0 ? 'hoch' : 'runter'); s.textContent = `${d > 0 ? '▲' : '▼'} ${dez(Math.abs(d), 1)} Pkt.`; }
    } else if (!davor) {
      if (jetzt > 0) { s.classList.add('hoch'); s.textContent = '▲ neu'; }
      else { s.classList.add('gleich'); s.textContent = '± 0 %'; }
    } else {
      const p = (jetzt - davor) / Math.abs(davor) * 100;
      if (Math.abs(p) < 1) { s.classList.add('gleich'); s.textContent = '± 0 %'; }
      else { s.classList.add(p > 0 ? 'hoch' : 'runter'); s.textContent = `${p > 0 ? '▲' : '▼'} ${zahl(Math.abs(p))} %`; }
    }
    s.title = `Zeitraum davor: ${punkte ? prozent(davor) : zahl(davor)}`;
    return s;
  }

  function kachel(name, wert, davor, hinweis) {
    const k = el('div', 'st-kpi');
    k.append(el('div', 'name', name), el('div', 'wert', wert));
    const u = el('div', 'unter');
    if (davor) u.append(davor, ' ');
    if (hinweis) u.append(hinweis);
    k.append(u);
    return k;
  }

  /* Balkenliste in EINER Farbe: sortiert nach Größe, der Balken zeigt die Größe
     relativ zum längsten, daneben steht der Anteil am Ganzen. */
  function balken(zeilen, opt = {}) {
    const wrap = el('div', 'st-balken');
    const max = Math.max(1, ...zeilen.map((z) => z.wert));
    for (const z of zeilen) {
      const r = el('div', 'br');
      r.append(el('span', 'name', opt.name ? opt.name(z) : z.name));
      r.append(el('span', 'zahl', `${zahl(z.wert)} · ${dez(z.anteil, 1)} %`));
      const bahn = el('div', 'bahn');
      const f = el('div', 'fuell');
      f.style.width = `${Math.max(1.5, z.wert / max * 100)}%`;
      bahn.append(f);
      r.append(bahn);
      wrap.append(r);
    }
    return wrap;
  }

  // Ein Abschnitt aus dem Backend: {ok, fehler, zeilen}. Fehlt er, steht der Grund da — nicht null.
  function abschnitt(titel, erklaerung, teil, zeichne) {
    const b = block(titel, erklaerung);
    if (!teil || !teil.ok) {
      b.append(el('p', 'st-leer', `Dazu liefert YouTube gerade nichts${teil && teil.fehler ? ` (${teil.fehler.slice(0, 110)})` : ''}.`));
    } else if (!teil.zeilen.length) {
      b.append(el('p', 'st-leer', 'Noch keine Daten in diesem Zeitraum — dafür sind zu wenige Aufrufe da.'));
    } else {
      b.append(zeichne(teil.zeilen));
    }
    return b;
  }

  function tabelle(kopf, zeilen, titel = 'Zahlen als Tabelle') {
    const d = el('details');
    d.append(el('summary', '', titel));
    const t = el('table');
    const tr = el('tr');
    for (const k of kopf) tr.append(el('th', '', k));
    const th = el('thead'); th.append(tr); t.append(th);
    const tb = el('tbody');
    for (const z of zeilen) {
      const r = el('tr');
      for (const c of z) r.append(el('td', '', c));
      tb.append(r);
    }
    t.append(tb); d.append(t);
    return d;
  }

  /* ---------- Diagramme (SVG, ohne Bibliothek) ---------- */
  const B = 320, H = 150, L = 34, R = 6, O = 8, U = 20;   // Breite, Höhe, Ränder

  function nett(max) {                                     // „schöne“ Obergrenze für die Achse
    if (max <= 0) return 1;
    const e = 10 ** Math.floor(Math.log10(max));
    for (const f of [1, 2, 2.5, 5, 10]) if (max <= f * e) return f * e;
    return 10 * e;
  }

  function achsenwerte(w) {
    if (w >= 10000) return `${dez(w / 1000, 0)}k`;
    return Number.isInteger(w) ? zahl(w) : dez(w, 1);
  }

  /* Verlauf über die Zeit: Säulen bei wenigen Punkten, sonst Linie mit Fläche.
     Zeiger/Finger zeigt den Wert des Tages. */
  function verlauf(punkte, format, opt = {}) {
    const wrap = el('div', 'st-diagramm');
    if (!punkte.length) { wrap.append(el('p', 'st-leer', 'Keine Tageswerte.')); return wrap; }
    const s = svg('svg', { viewBox: `0 0 ${B} ${H}`, role: 'img', 'aria-label': opt.beschreibung || 'Verlauf' });
    const max = nett(Math.max(...punkte.map((p) => p.y), opt.mindestMax || 0));
    const bw = B - L - R, bh = H - O - U;
    const x = (i) => L + (punkte.length === 1 ? bw / 2 : (i / (punkte.length - 1)) * bw);
    const y = (v) => O + bh - (v / max) * bh;

    for (const f of [0, 0.5, 1]) {
      const gy = O + bh - f * bh;
      s.append(svg('line', { x1: L, x2: B - R, y1: gy, y2: gy }, 'gitter'));
      const t = svg('text', { x: L - 4, y: gy + 3, 'text-anchor': 'end' }, 'achse');
      t.textContent = (opt.achse || achsenwerte)(max * f);
      s.append(t);
    }
    const saeulen = punkte.length <= 45;
    if (saeulen) {
      const breite = bw / punkte.length;
      punkte.forEach((p, i) => {
        const h = Math.max(p.y > 0 ? 1.5 : 0, (p.y / max) * bh);
        s.append(svg('rect', { x: L + i * breite + breite * 0.12, y: O + bh - h, width: Math.max(1, breite * 0.76),
          height: h, rx: Math.min(3, breite * 0.3) }, 'saeule'));
      });
    } else {
      const pfad = punkte.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.y).toFixed(1)}`).join('');
      s.append(svg('path', { d: `${pfad}L${x(punkte.length - 1).toFixed(1)},${O + bh}L${x(0).toFixed(1)},${O + bh}Z` }, 'flaeche'));
      s.append(svg('path', { d: pfad }, 'linie'));
    }
    // Achsenbeschriftung unten: erster, mittlerer, letzter Tag.
    for (const i of new Set([0, Math.floor((punkte.length - 1) / 2), punkte.length - 1])) {
      const t = svg('text', { x: saeulen ? L + (i + 0.5) * (bw / punkte.length) : x(i), y: H - 5,
        'text-anchor': i === 0 ? 'start' : (i === punkte.length - 1 ? 'end' : 'middle') }, 'achse');
      t.textContent = punkte[i].name;
      s.append(t);
    }
    if (opt.marke) {
      const i = punkte.findIndex((p) => p.id === opt.marke.id);
      if (i >= 0) s.append(svg('circle', { cx: x(i), cy: y(punkte[i].y), r: 4.5 }, 'marke'));
    }
    wrap.append(s);
    zeiger(wrap, s, punkte, saeulen ? (i) => L + (i + 0.5) * (bw / punkte.length) : x, y, format, { L, bw }, saeulen);
    return wrap;
  }

  // Fadenkreuz und Hinweis: Maus, Finger und Stift auf demselben Weg (Pointer Events).
  function zeiger(wrap, s, punkte, xf, yf, format, { L: links, bw }, saeulen = false) {
    const linie = svg('line', { y1: O, y2: H - U, visibility: 'hidden' }, 'zeiger');
    const punkt = svg('circle', { r: 4, visibility: 'hidden' }, 'punkt');
    s.append(linie, punkt);
    const tip = el('div', 'st-tip'); tip.hidden = true;
    wrap.append(tip);
    const zeige = (ev) => {
      const r = s.getBoundingClientRect();
      const vx = (ev.clientX - r.left) / r.width * B;
      const anteil = Math.min(1, Math.max(0, (vx - links) / bw));
      const i = saeulen ? Math.min(punkte.length - 1, Math.floor(anteil * punkte.length))
        : Math.round(anteil * (punkte.length - 1));
      const p = punkte[i];
      const px = xf(i);
      linie.setAttribute('x1', px); linie.setAttribute('x2', px); linie.setAttribute('visibility', 'visible');
      punkt.setAttribute('cx', px); punkt.setAttribute('cy', yf(p.y)); punkt.setAttribute('visibility', 'visible');
      tip.hidden = false;
      tip.textContent = '';
      tip.append(el('b', '', format(p.y)), ` · ${p.lang || p.name}`);
      const links_px = px / B * r.width;
      tip.style.left = `${Math.min(r.width - 50, Math.max(50, links_px))}px`;
    };
    const weg = () => { linie.setAttribute('visibility', 'hidden'); punkt.setAttribute('visibility', 'hidden'); tip.hidden = true; };
    s.addEventListener('pointerdown', zeige);
    s.addEventListener('pointermove', zeige);
    s.addEventListener('pointerleave', weg);
    s.addEventListener('pointercancel', weg);
  }

  /* Rückhaltekurve: wie viel Prozent der Zuschauer sind an dieser Stelle noch da.
     Über 100 % ist möglich (Wiederholungen bei Shorts), die Achse wächst dann mit. */
  function kurve(punkte, sekunden, einbruch) {
    const daten = punkte.map((p) => ({
      y: p.gesehen * 100,
      name: sekunden ? dauer(p.anteil * sekunden) : `${Math.round(p.anteil * 100)} %`,
      lang: sekunden ? `bei ${dauer(p.anteil * sekunden)} (${Math.round(p.anteil * 100)} % des Videos)` : `bei ${Math.round(p.anteil * 100)} %`,
    }));
    const wrap = el('div', 'st-diagramm');
    const s = svg('svg', { viewBox: `0 0 ${B} ${H}`, role: 'img', 'aria-label': 'Wie viele Zuschauer bis wohin bleiben' });
    const max = nett(Math.max(100, ...daten.map((d) => d.y)));
    const bw = B - L - R, bh = H - O - U;
    const x = (i) => L + (i / (daten.length - 1)) * bw;
    const y = (v) => O + bh - (v / max) * bh;
    for (const f of [0, 0.5, 1]) {
      const gy = O + bh - f * bh;
      s.append(svg('line', { x1: L, x2: B - R, y1: gy, y2: gy }, 'gitter'));
      const t = svg('text', { x: L - 4, y: gy + 3, 'text-anchor': 'end' }, 'achse');
      t.textContent = `${Math.round(max * f)} %`;
      s.append(t);
    }
    if (max > 100) s.append(svg('line', { x1: L, x2: B - R, y1: y(100), y2: y(100) }, 'bezug'));
    const pfad = daten.map((d, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(d.y).toFixed(1)}`).join('');
    s.append(svg('path', { d: `${pfad}L${x(daten.length - 1).toFixed(1)},${O + bh}L${x(0).toFixed(1)},${O + bh}Z` }, 'flaeche'));
    s.append(svg('path', { d: pfad }, 'linie'));
    for (const i of [0, Math.floor((daten.length - 1) / 2), daten.length - 1]) {
      const t = svg('text', { x: x(i), y: H - 5, 'text-anchor': i === 0 ? 'start' : (i === daten.length - 1 ? 'end' : 'middle') }, 'achse');
      t.textContent = sekunden ? dauer(punkte[i].anteil * sekunden) : `${Math.round(punkte[i].anteil * 100)} %`;
      s.append(t);
    }
    if (einbruch) {
      const i = punkte.findIndex((p) => p.anteil >= einbruch.anteil);
      if (i >= 0) s.append(svg('circle', { cx: x(i), cy: y(daten[i].y), r: 4.5 }, 'marke'));
    }
    wrap.append(s);
    zeiger(wrap, s, daten, x, y, (v) => `${dez(v, 0)} % noch dabei`, { L, bw });
    return wrap;
  }

  /* ---------- Zustand melden (Freigabe aus, nicht verbunden, YouTube-Fehler) ---------- */
  function zustandsHinweis(d, neuLaden) {
    const b = el('div', 'st-hinweis');
    b.append(el('p', '', d.text || 'Die Zahlen sind gerade nicht erreichbar.'));
    if (d.zustand === 'getrennt') {
      const a = el('a', 'st-knopf haupt', 'Mit YouTube verbinden');
      a.href = '/api/studio/start';
      b.append(a);
    } else if (d.zustand === 'aus') {
      const a = el('a', 'st-knopf haupt', 'Zu den Freigaben');
      a.href = '#freigaben';
      b.append(a);
    } else {
      b.append(knopf('Erneut versuchen', 'st-knopf', () => neuLaden(true)));
    }
    return b;
  }

  function fuss(d, neuLaden) {
    const f = el('div', 'st-fuss');
    f.append(el('span', '', d.erneuert ? `Stand ${uhr(d.erneuert)} Uhr · YouTube meldet Zahlen mit ein bis zwei Tagen Verzug` : ''));
    f.append(knopf('Aktualisieren', '', () => neuLaden(true)));
    return f;
  }

  /* ---------- Ansicht: Überblick ---------- */
  const METRIKEN = [
    ['aufrufe', 'Aufrufe', (v) => zahl(v)],
    ['minuten', 'Wiedergabezeit', (v) => wiedergabe(v)],
    ['abos_neu', 'Neue Abos', (v) => zahl(v)],
    ['likes', 'Likes', (v) => zahl(v)],
    ['gehalten', 'Ø angesehen', (v) => prozent(v)],
  ];

  function zeitraumText(z) {
    if (z.tage === 0) return `seit Kanalstart (${datum(z.von)})`;
    return `${datum(z.von)} – ${datum(z.bis)}`;
  }

  function ueberblick(d, ziel, neuLaden) {
    const k = d.kanal, s = d.summe, v = d.davor;
    const fakten = el('div', 'st-fakten');
    for (const [n, w] of [['Abonnenten', zahl(k.abonnenten)], ['Videos', zahl(k.videos)], ['Aufrufe gesamt', zahl(k.aufrufe)]]) {
      const f = el('div', 'st-fakt'); f.append(el('b', '', w), el('span', '', n)); fakten.append(f);
    }
    ziel.append(fakten);

    const kopf = el('div', 'st-kopfzeile');
    kopf.append(el('span', 'leise', `${k.name} · ${zeitraumText(d.zeitraum)}`));
    ziel.append(kopf);

    const raster = el('div', 'st-kpis');
    const w = (feld, punkte) => aenderung(s[feld], v ? v[feld] : null, punkte);
    raster.append(
      kachel('Aufrufe', zahl(s.aufrufe), w('aufrufe')),
      kachel('Wiedergabezeit', wiedergabe(s.minuten), w('minuten')),
      kachel('Ø Sehdauer', dauer(s.sekunden), w('sekunden')),
      kachel('Ø angesehen', prozent(s.gehalten), w('gehalten', true), 'vom Video'),
      kachel('Abos netto', `${s.abos > 0 ? '+' : ''}${zahl(s.abos)}`, w('abos'), `+${zahl(s.abos_neu)} / −${zahl(s.abos_weg)}`),
      kachel('Abos je 1000', dez(s.abo_je_tausend, 1), w('abo_je_tausend'), 'Aufrufe'),
      kachel('Likes', zahl(s.likes), w('likes')),
      kachel('Kommentare', zahl(s.kommentare), w('kommentare')),
      kachel('Geteilt', zahl(s.teilen), w('teilen')),
      kachel('Interaktion', prozent(s.interaktion), w('interaktion', true), 'Likes, Komm., Teilen je Aufruf'));
    ziel.append(raster);

    // Verlauf mit umschaltbarer Kennzahl
    const b = block('Verlauf', 'Tag für Tag. Tippen oder darüberfahren zeigt den Wert.');
    const chips = el('div', 'chips st-metrik');
    const diag = el('div');
    const zeichne = () => {
      for (const c of chips.children) c.classList.toggle('aktiv', c.dataset.metrik === metrik);
      const [, name, format] = METRIKEN.find((m) => m[0] === metrik);
      const punkte = d.reihe.map((r) => ({ y: r[metrik], name: kurz(r.tag), lang: datum(r.tag) }));
      diag.textContent = '';
      if (!d.reihe_ok) diag.append(el('p', 'st-leer', `Der Verlauf ist gerade nicht abrufbar (${d.reihe_fehler.slice(0, 110)}).`));
      else {
        diag.append(verlauf(punkte, format, { beschreibung: `${name} pro Tag`, mindestMax: metrik === 'gehalten' ? 100 : 0 }));
        diag.append(tabelle(['Tag', name], d.reihe.map((r) => [datum(r.tag), format(r[metrik])])));
      }
    };
    for (const [id, name] of METRIKEN) {
      const c = knopf(name, '', () => { metrik = id; zeichne(); });
      c.dataset.metrik = id;
      chips.append(c);
    }
    b.append(chips, diag);
    ziel.append(b);
    zeichne();

    // Wochentage
    const tageBlock = block('Wochentage', 'Durchschnittliche Aufrufe je Wochentag im Zeitraum.');
    const wt = d.wochentage;
    const spitze = Math.max(...wt.map((t) => t.aufrufe));
    if (!d.reihe.length || spitze <= 0) tageBlock.append(el('p', 'st-leer', 'Noch keine Daten.'));
    else {
      const reihe = el('div', 'st-tage');
      for (const t of wt) {
        const c = el('div', `tag${t.aufrufe === spitze ? ' best' : ''}`);
        c.append(el('b', '', zahl(t.aufrufe)));
        const sl = el('div', 'saeule');
        sl.style.height = `${Math.max(2, t.aufrufe / spitze * 62)}px`;
        c.append(sl, el('span', '', t.tag));
        reihe.append(c);
      }
      tageBlock.append(reihe);
    }
    ziel.append(tageBlock);

    // Rhythmus und Spitzenreiter
    const rhythmus = block('Im Zeitraum');
    const teile = [`${d.neue_videos} neue${d.neue_videos === 1 ? 's' : ''} Video${d.neue_videos === 1 ? '' : 's'} veröffentlicht`];
    if (d.letztes_video) teile.push(`letztes vom ${datum(d.letztes_video)}`);
    rhythmus.append(el('p', 'st-leer', teile.join(' · ')));
    if (d.spitzen.length) {
      rhythmus.append(el('p', 'leise', 'Meistgesehen:'));
      const liste = el('div', 'st-liste');
      for (const t of d.spitzen) {
        const zeile = knopf('', 'st-video', () => videoOeffnen(t.id));
        zeile.append(t.bild ? Object.assign(el('img'), { src: t.bild, alt: '', loading: 'lazy' }) : el('div', 'ohne-bild'));
        const rechts = el('div');
        rechts.append(el('div', 'titel', t.titel), el('div', 'zahlen', `${zahl(t.aufrufe)} Aufrufe · ${prozent(t.gehalten)} angesehen`));
        zeile.append(rechts);
        liste.append(zeile);
      }
      rhythmus.append(liste);
    }
    ziel.append(rhythmus);
    ziel.append(fuss(d, neuLaden));
  }

  /* ---------- Ansicht: Videos ---------- */
  const SORTIERUNGEN = [
    ['neu', 'Neueste zuerst', (v) => v.veroeffentlicht],
    ['aufrufe', 'Meiste Aufrufe (Zeitraum)', (v) => v.zeitraum.aufrufe],
    ['gesamt', 'Meiste Aufrufe (insgesamt)', (v) => v.gesamt.aufrufe],
    ['gehalten', 'Am besten angesehen', (v) => v.zeitraum.gehalten],
    ['abos', 'Meiste neue Abos', (v) => v.zeitraum.abos_neu],
    ['interaktion', 'Höchste Interaktion', (v) => v.zeitraum.interaktion],
    ['teilen', 'Am häufigsten geteilt', (v) => v.zeitraum.teilen],
  ];

  function videoZeile(v) {
    const z = knopf('', 'st-video', () => videoOeffnen(v.id));
    z.append(v.bild ? Object.assign(el('img'), { src: v.bild, alt: '', loading: 'lazy' }) : el('div', 'ohne-bild'));
    const r = el('div');
    const t = el('div', 'titel', v.titel);
    if (v.zeitraum.aufrufe && v.gegen_mitte >= 1.8) t.append(el('span', 'st-marke gut', `${dez(v.gegen_mitte, 1)}× üblich`));
    else if (v.zeitraum.aufrufe && v.gegen_mitte <= 0.4) t.append(el('span', 'st-marke schwach', 'schwach'));
    r.append(t);
    r.append(el('div', 'meta', [datum(v.veroeffentlicht), dauer(v.dauer), v.kurz ? 'Short' : 'Video',
      v.sichtbarkeit && v.sichtbarkeit !== 'public' ? v.sichtbarkeit : ''].filter(Boolean).join(' · ')));
    const zahlen = el('div', 'zahlen');
    for (const [wert, name] of [[zahl(v.zeitraum.aufrufe), 'Aufrufe'], [prozent(v.zeitraum.gehalten), 'angesehen'],
      [`+${zahl(v.zeitraum.abos_neu)}`, 'Abos'], [zahl(v.gesamt.aufrufe), 'gesamt']]) {
      const sp = el('span', '', wert); sp.append(' ', el('small', '', name)); zahlen.append(sp);
    }
    r.append(zahlen);
    z.append(r);
    return z;
  }

  function videos(d, ziel, neuLaden) {
    const alle = d.videos;
    if (!alle.length) { ziel.append(el('p', 'leer-klein', 'Auf dem Kanal liegen noch keine Videos.')); return; }

    const werkzeug = el('div', 'st-werkzeug');
    const feld = el('input');
    feld.type = 'search'; feld.placeholder = 'Video suchen …'; feld.value = suche;
    feld.setAttribute('aria-label', 'Videos durchsuchen');
    const wahl = el('select', 'auswahl');
    wahl.setAttribute('aria-label', 'Sortierung');
    for (const [id, name] of SORTIERUNGEN) { const o = el('option', '', name); o.value = id; o.selected = id === sortierung; wahl.append(o); }
    werkzeug.append(feld, wahl);
    ziel.append(werkzeug);

    const mitKurz = alle.some((v) => v.kurz), mitLang = alle.some((v) => !v.kurz);
    if (mitKurz && mitLang) {
      const chips = el('div', 'chips');
      for (const [id, name] of [['alle', 'Alle'], ['kurz', 'Shorts'], ['lang', 'Lange Videos']]) {
        const c = knopf(name, id === art ? 'aktiv' : '', () => { art = id; sichtbar = 12; zeichne(); });
        chips.append(c);
      }
      ziel.append(chips);
    }

    const kopf = el('p', 'leise');
    const liste = el('div', 'st-liste');
    const mehr = knopf('Mehr anzeigen', 'st-mehr', () => { sichtbar += 12; zeichne(); });
    ziel.append(kopf, liste, mehr, fuss(d, neuLaden));

    function zeichne() {
      for (const c of ziel.querySelectorAll('.chips button')) c.classList.toggle('aktiv', c.textContent === ({ alle: 'Alle', kurz: 'Shorts', lang: 'Lange Videos' })[art]);
      const wort = suche.trim().toLowerCase();
      let treffer = alle.filter((v) => (art === 'alle' || (art === 'kurz') === v.kurz)
        && (!wort || v.titel.toLowerCase().includes(wort)));
      const [, , schluessel] = SORTIERUNGEN.find((s) => s[0] === sortierung);
      treffer = [...treffer].sort((a, b) => {
        const x = schluessel(a), y = schluessel(b);
        return x < y ? 1 : (x > y ? -1 : 0);
      });
      kopf.textContent = `${treffer.length} von ${alle.length} Videos · Zahlen für ${zeitraumText(d.zeitraum)}`;
      liste.textContent = '';
      for (const v of treffer.slice(0, sichtbar)) liste.append(videoZeile(v));
      if (!treffer.length) liste.append(el('p', 'st-leer', 'Kein Video passt.'));
      mehr.hidden = treffer.length <= sichtbar;
    }
    feld.addEventListener('input', () => { suche = feld.value; sichtbar = 12; zeichne(); });
    wahl.addEventListener('change', () => { sortierung = wahl.value; sichtbar = 12; zeichne(); });
    zeichne();
  }

  /* ---------- Einzelnes Video ---------- */
  function videoOeffnen(kennung) {
    offenesVideo = kennung;
    ansicht = 'videos';
    schalterZeigen();
    laden();
    window.scrollTo({ top: 0 });
  }

  function videoDetail(d, ziel, neuLaden) {
    const v = d.video;
    ziel.append(knopf('‹ Alle Videos', 'st-zurueck', () => { offenesVideo = null; laden(); }));
    const kopf = el('div', 'st-detailkopf block');
    kopf.append(el('h2', '', v.titel));
    kopf.append(el('div', 'leise', [datum(v.veroeffentlicht), dauer(v.dauer), v.kurz ? 'Short' : 'Video',
      v.sichtbarkeit === 'public' ? 'öffentlich' : v.sichtbarkeit].filter(Boolean).join(' · ')));
    const l1 = el('a', 'hilfe-link', 'Auf YouTube ansehen ↗');
    l1.href = `https://youtu.be/${encodeURIComponent(v.id)}`; l1.target = '_blank'; l1.rel = 'noopener noreferrer';
    const l2 = el('a', 'hilfe-link', 'Alle Zahlen im YouTube Studio ↗');
    l2.href = `https://studio.youtube.com/video/${encodeURIComponent(v.id)}/analytics`; l2.target = '_blank'; l2.rel = 'noopener noreferrer';
    kopf.append(l1, document.createTextNode('  '), l2);
    ziel.append(kopf);

    const s = d.summe;
    if (!s) {
      const h = el('div', 'st-hinweis');
      h.append(el('p', '', `Die Zahlen zu diesem Video sind gerade nicht abrufbar (${(d.summe_fehler || '').slice(0, 110)}).`));
      ziel.append(h);
    } else {
      ziel.append(el('p', 'leise', `Seit der Veröffentlichung (${datum(d.zeitraum.seit_veroeffentlichung)}).`));
      const raster = el('div', 'st-kpis');
      raster.append(
        kachel('Aufrufe', zahl(s.aufrufe), null, `${zahl(v.gesamt.aufrufe)} laut Kanalseite`),
        kachel('Wiedergabezeit', wiedergabe(s.minuten)),
        kachel('Ø Sehdauer', dauer(s.sekunden), null, v.dauer ? `von ${dauer(v.dauer)}` : ''),
        kachel('Ø angesehen', prozent(s.gehalten)),
        kachel('Neue Abos', `+${zahl(s.abos_neu)}`, null, s.abos_weg ? `${zahl(s.abos_weg)} wieder weg` : ''),
        kachel('Interaktion', prozent(s.interaktion), null, 'Likes, Komm., Teilen je Aufruf'),
        kachel('Likes', zahl(s.likes)),
        kachel('Kommentare', zahl(s.kommentare)),
        kachel('Geteilt', zahl(s.teilen)));
      ziel.append(raster);
    }

    // Rückhaltekurve — das Wichtigste bei Kurzvideos.
    const kb = block('Wo Zuschauer aufhören', 'Anteil der Zuschauer, die an dieser Stelle noch dabei sind. Über 100 % heißt: Es wurde zurückgespult oder wiederholt.');
    if (!d.kurve_ok) kb.append(el('p', 'st-leer', `Die Kurve ist gerade nicht abrufbar (${d.kurve_fehler.slice(0, 110)}).`));
    else if (d.kurve.length < 3) kb.append(el('p', 'st-leer', 'Noch keine Kurve — dafür sind zu wenige Aufrufe da.'));
    else {
      kb.append(kurve(d.kurve, v.dauer, d.einbruch));
      if (d.einbruch) {
        const stelle = d.einbruch.sekunde !== null ? `bei ${dauer(d.einbruch.sekunde)}` : `bei ${Math.round(d.einbruch.anteil * 100)} %`;
        kb.append(el('p', 'st-leer', `Größter Einbruch ${stelle}: dort gehen ${dez(d.einbruch.verlust, 0)} Prozentpunkte der Zuschauer verloren (roter Punkt).`));
      }
      if (d.kurve_relativ !== null) {
        const r = d.kurve_relativ;
        kb.append(el('p', 'st-leer', `Verglichen mit ähnlich langen Videos auf YouTube: ${r >= 0.55 ? 'besser als üblich' : (r <= 0.45 ? 'schlechter als üblich' : 'durchschnittlich')} (Wert ${dez(r, 2)} von 1).`));
      }
      const schritt = Math.max(1, Math.floor(d.kurve.length / 10));
      kb.append(tabelle(['Stelle', 'Noch dabei'], d.kurve.filter((_, i) => i % schritt === 0)
        .map((p) => [v.dauer ? dauer(p.anteil * v.dauer) : `${Math.round(p.anteil * 100)} %`, prozent(p.gesehen * 100)])));
    }
    ziel.append(kb);

    // Tagesverlauf
    const tb = block('Aufrufe pro Tag', d.zeitraum.tage >= 120 ? 'Die letzten 120 Tage.' : 'Seit der Veröffentlichung.');
    if (!d.reihe_ok) tb.append(el('p', 'st-leer', `Nicht abrufbar (${d.reihe_fehler.slice(0, 110)}).`));
    else {
      tb.append(verlauf(d.reihe.map((r) => ({ y: r.aufrufe, name: kurz(r.tag), lang: datum(r.tag) })), zahl,
        { beschreibung: 'Aufrufe pro Tag' }));
      tb.append(tabelle(['Tag', 'Aufrufe'], d.reihe.map((r) => [datum(r.tag), zahl(r.aufrufe)])));
    }
    ziel.append(tb);

    ziel.append(abschnitt('Woher die Aufrufe kommen', '', d.quellen, (z) => balken(z)));
    ziel.append(abschnitt('Länder', '', d.laender, (z) => balken(z, { name: (r) => landName(r.name) })));
    ziel.append(abschnitt('Abonnenten und andere', 'Schauen Abonnenten oder Fremde?', d.abo, (z) => balken(z)));
    ziel.append(abschnitt('Geräte', '', d.geraete, (z) => balken(z)));
    ziel.append(fuss(d, neuLaden));
  }

  /* ---------- Ansicht: Publikum ---------- */
  function alterBlock(zeilen) {
    // Zeilen je Altersgruppe zusammenfassen, das Geschlecht darunter als Zeile.
    const gruppen = new Map();
    const geschlecht = new Map();
    let gesamt = 0;
    for (const z of zeilen) {
      gruppen.set(z.alter, (gruppen.get(z.alter) || 0) + z.anteil);
      geschlecht.set(z.geschlecht, (geschlecht.get(z.geschlecht) || 0) + z.anteil);
      gesamt += z.anteil;
    }
    const wrap = el('div');
    const liste = [...gruppen.entries()].map(([name, a]) => ({ name: name.replace('-', '–').replace('65–', '65+'), wert: a, anteil: gesamt ? a / gesamt * 100 : 0 }));
    const b = balken(liste.map((l) => ({ ...l, wert: Math.round(l.anteil * 10) / 10 })), {});
    for (const r of b.querySelectorAll('.zahl')) r.textContent = r.textContent.split(' · ')[1];   // hier zählt nur der Anteil
    wrap.append(b);
    if (geschlecht.size) {
      const teile = [...geschlecht.entries()].map(([n, a]) => `${n} ${dez(gesamt ? a / gesamt * 100 : 0, 0)} %`);
      wrap.append(el('p', 'st-leer', teile.join(' · ')));
    }
    return wrap;
  }

  function publikum(d, ziel, neuLaden) {
    ziel.append(el('p', 'leise', `Zeitraum: ${zeitraumText(d.zeitraum)}. Balken zeigen Aufrufe und ihren Anteil.`));
    ziel.append(abschnitt('Woher die Aufrufe kommen', 'Kanal-Sicht: Shorts-Feed, Suche, Abo-Feed und mehr.', d.quellen, (z) => balken(z)));
    ziel.append(abschnitt('Suchbegriffe', 'Womit Zuschauer über die YouTube-Suche kamen.', d.suche, (z) => balken(z)));
    ziel.append(abschnitt('Shorts oder lange Videos', '', d.inhalt, (z) => balken(z)));
    ziel.append(abschnitt('Abonnenten und andere', 'Wie viele Aufrufe kommen von Abonnenten?', d.abo, (z) => balken(z)));
    ziel.append(abschnitt('Länder', '', d.laender, (z) => balken(z, { name: (r) => landName(r.name) })));
    ziel.append(abschnitt('Alter und Geschlecht', 'Anteil der angemeldeten Zuschauer. Bei kleinen Kanälen oft leer.', d.alter, alterBlock));
    ziel.append(abschnitt('Geräte', '', d.geraete, (z) => balken(z)));
    ziel.append(abschnitt('Betriebssysteme', '', d.systeme, (z) => balken(z)));
    ziel.append(abschnitt('Wo gesehen', '', d.orte, (z) => balken(z)));
    ziel.append(abschnitt('Wie geteilt wird', 'Teilen-Klicks nach Dienst.', d.teilen, (z) => balken(z)));
    ziel.append(el('p', 'leise', 'Impressionen und Klickrate stellt YouTube nicht über die Schnittstelle bereit — die gibt es nur im YouTube Studio selbst.'));
    ziel.append(fuss(d, neuLaden));
  }

  /* ---------- Ansicht: Werkstatt ---------- */
  async function werkstatt(ziel) {
    let daten;
    try { daten = await holen('/api/studio/wartend'); }
    catch (_) { ziel.textContent = 'Das Studio ist gerade nicht erreichbar.'; return; }
    let themen = [];
    try { themen = (await holen('/api/studio/themen')).themen || []; } catch (_) { /* die Werkstatt geht auch ohne */ }
    ziel.textContent = '';
    const videosFertig = daten.videos || [];
    const arbeit = daten.inArbeit || [];
    // Auffrischen nur, solange wirklich etwas läuft — danach wieder Ruhe.
    taktSetzen(arbeit.length > 0);
    zaehlerSetzen(videosFertig.filter((v) => v.status === 'fertig' && !v.hochgeladen).length);

    // Was gerade entsteht, zuerst — sonst starrt man auf eine leere Seite.
    for (const a of arbeit) {
      const karte = el('div', 'eintrag');
      const kopf = el('div', 'kopfzeile');
      kopf.append(el('span', 'titel', a.titel || a.thema || 'Video wird gebaut'));
      kopf.append(el('span', 'klein', `${a.fortschritt || 0} %`));
      karte.append(kopf);
      const bahn = el('div', 'fortschritt');
      const balkenEl = el('div', 'fortschritt-fuellung');
      balkenEl.style.width = `${Math.max(2, Math.min(100, a.fortschritt || 0))}%`;
      bahn.append(balkenEl);
      karte.append(bahn);
      karte.append(el('div', 'klein', a.schritt || 'startet …'));
      if (a.meldung) karte.append(el('div', 'leise', a.meldung));
      ziel.append(karte);
    }

    if (!videosFertig.length && !arbeit.length) {
      ziel.append(el('p', 'leer-klein', 'Gerade wartet kein Video. Friday plant eines pro Tag.'));
    }

    for (const v of videosFertig) {
      const karte = el('div', 'eintrag');
      const kopf = el('div', 'kopfzeile');
      kopf.append(el('span', 'titel', v.titel || v.thema || v.kennung));
      kopf.append(el('span', `klein ${v.hochgeladen ? 'status-ok' : ''}`,
        v.hochgeladen ? 'veröffentlicht' : (v.status === 'fehler' ? 'fehlgeschlagen' : 'wartet')));
      karte.append(kopf);

      if (v.status === 'fehler') {
        karte.append(el('div', 'text', String(v.fehler || '').split('\n')[0]));
      } else {
        const video = document.createElement('video');
        video.src = `/api/studio/video/${encodeURIComponent(v.kennung)}`;
        video.controls = true;
        video.preload = 'none';
        video.className = 'studio-video';
        karte.append(video);
        if (v.beschreibung) karte.append(el('div', 'text', v.beschreibung));
      }

      const angaben = [];
      if (v.groesse_mb) angaben.push(`${v.groesse_mb} MB`);
      if (v.beendet) angaben.push(`fertig ${zeit(v.beendet)}`);
      if (angaben.length) karte.append(el('div', 'klein', angaben.join(' · ')));

      if (v.hochgeladen && v.url) {
        const a = document.createElement('a');
        a.className = 'hilfe-link';
        a.href = v.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
        a.textContent = 'Auf YouTube ansehen ↗';
        karte.append(a);
      } else if (v.status === 'fertig') {
        const ausgabe = el('div', 'klein');
        const reihe = el('div', 'knoepfe');
        reihe.append(knopf('Veröffentlichen', 'weg', async (b) => {
          const titel = v.titel || v.thema || v.kennung;
          if (!confirm(`„${titel}“ jetzt auf dem Kanal veröffentlichen?\n\nGeht sofort öffentlich, nicht zurückzuholen.`)) return;
          b.disabled = true;
          ausgabe.textContent = 'lädt hoch …';
          const r = await fetch('/api/studio/hochladen', { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kennung: v.kennung }) });
          const d = await r.json().catch(() => ({}));
          if (r.ok && d.ok) { laden(); return; }
          ausgabe.textContent = d.text || d.detail || `Fehler ${r.status}`;
          ausgabe.className = 'klein status-fehler';
          b.disabled = false;
        }));
        karte.append(reihe, ausgabe);
      }
      ziel.append(karte);
    }

    if (themen.length) {
      const b = el('div', 'block');
      b.append(el('h2', '', 'Bisherige Themen'), el('p', 'leise', 'Was Friday schon geplant hat — das kommt nicht noch einmal dran.'));
      let zeigen = 6;
      const liste = el('div', 'st-liste');
      const mehr = knopf('Mehr anzeigen', 'st-mehr', () => { zeigen += 12; zeichne(); });
      const zeichne = () => {
        liste.textContent = '';
        for (const t of themen.slice(0, zeigen)) {
          const z = el('div', 'eintrag');
          z.append(el('div', 'titel', t.titel || t.thema), el('div', 'klein', `${t.wann ? zeit(t.wann) : ''}`));
          liste.append(z);
        }
        mehr.hidden = themen.length <= zeigen;
      };
      b.append(liste, mehr);
      ziel.append(b);
      zeichne();
    }
  }

  /* ---------- Steuerung ---------- */
  const zaehlerSetzen = (n) => {
    const knopfW = document.querySelector('#st-nav button[data-ansicht="werkstatt"]');
    if (!knopfW) return;
    knopfW.querySelector('.zaehler')?.remove();
    if (n > 0) knopfW.append(el('span', 'zaehler', String(n)));
  };

  function schalterZeigen() {
    for (const b of document.querySelectorAll('#st-nav button')) b.classList.toggle('aktiv', b.dataset.ansicht === ansicht);
    for (const b of document.querySelectorAll('#st-zeit button')) b.classList.toggle('aktiv', Number(b.dataset.tage) === tage);
    // Der Zeitraum gilt nicht für die Werkstatt und nicht für ein einzelnes Video (das zeigt seine ganze Laufzeit).
    $('st-zeit').hidden = ansicht === 'werkstatt' || (ansicht === 'videos' && !!offenesVideo);
  }

  async function laden(frisch = false) {
    const ziel = inhalt();
    if (!ziel) return;
    const nummer = ++lauf;
    schalterZeigen();
    if (ansicht !== 'werkstatt') taktSetzen(false);

    if (ansicht === 'werkstatt') { await werkstatt(ziel); return; }

    let url;
    if (ansicht === 'ueberblick') url = `/api/studio/statistik/uebersicht?tage=${tage}`;
    else if (ansicht === 'publikum') url = `/api/studio/statistik/publikum?tage=${tage}`;
    else if (offenesVideo) url = `/api/studio/statistik/video/${encodeURIComponent(offenesVideo)}?`;
    else url = `/api/studio/statistik/videos?tage=${tage}`;
    if (frisch) url += '&frisch=true';

    ziel.textContent = '';
    ziel.append(el('p', 'leer-klein', 'lädt …'));
    let d;
    try { d = await holen(url); }
    catch (_) {
      if (nummer !== lauf) return;
      ziel.textContent = '';
      ziel.append(zustandsHinweis({ zustand: 'fehler', text: 'Friday antwortet gerade nicht.' }, laden));
      return;
    }
    if (nummer !== lauf) return;                       // inzwischen etwas anderes gewählt
    ziel.textContent = '';
    if (d.zustand !== 'ok') { ziel.append(zustandsHinweis(d, laden)); return; }
    if (ansicht === 'ueberblick') ueberblick(d, ziel, laden);
    else if (ansicht === 'publikum') publikum(d, ziel, laden);
    else if (offenesVideo) videoDetail(d, ziel, laden);
    else videos(d, ziel, laden);
  }

  // Solange etwas gebaut wird, alle fünf Sekunden auffrischen — sonst müsste
  // Eike die Seite von Hand neu laden, um den Fortschritt zu sehen.
  function taktSetzen(laeuft) {
    if (laeuft && !takt) takt = setInterval(() => { if (aktiv() && ansicht === 'werkstatt') laden(); }, 5000);
    if (!laeuft && takt) { clearInterval(takt); takt = null; }
  }

  for (const b of document.querySelectorAll('#st-nav button')) {
    b.addEventListener('click', () => {
      if (b.dataset.ansicht === ansicht && !offenesVideo) return;
      ansicht = b.dataset.ansicht;
      offenesVideo = null;
      merken();
      laden();
    });
  }
  for (const b of document.querySelectorAll('#st-zeit button')) {
    b.addEventListener('click', () => { tage = Number(b.dataset.tage); sichtbar = 12; merken(); laden(); });
  }

  const beiAnzeige = () => {
    if (aktiv()) laden();
    else taktSetzen(false);
  };
  window.addEventListener('hashchange', beiAnzeige);
  document.querySelector('#studio .reiter button[data-reiter="studio"]')?.addEventListener('click', () => setTimeout(beiAnzeige, 0));
  schalterZeigen();
  beiAnzeige();
})();
