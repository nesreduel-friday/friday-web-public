/* Schule-Seite: Reiter „Lernen" — alles zu einem Fach an einem Ort.

   Liste der Fächer mit einer Zahl, wie viel da ist (Arbeiten, Muster, Gruppenordner-
   Material, Goodnotes-Hefte). Tippen auf ein Fach zeigt Noten, Arbeiten-Muster (eigene
   und von anderen), Gruppenordner-Material und passende Goodnotes-Hefte zusammen.
   Alles per textContent, nie als HTML — die Inhalte stammen aus Arbeiten/Materialien. */
(() => {
  const { $ } = window.Friday;

  const el = (tag, klasse, text) => {
    const e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };
  const holen = async (url) => {
    const r = await fetch(url, { cache: 'no-store' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : 'Das hat nicht geklappt.');
    return d;
  };
  const tag = (iso) => iso ? new Date(iso).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' }) : '';

  let faecher = [];
  let ausgewaehlt = null;    // Fach-Id oder null (Liste)
  let detail = null;
  let fehler = '';
  let geladen = false;

  const farbton = (name) => [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);

  function badge(name) {
    const b = el('span', 'ln-badge', name.trim().slice(0, 2));
    b.style.setProperty('--farbton', farbton(name));
    return b;
  }

  function zaehler(z) {
    const zeile = el('span', 'ln-chips');
    const teile = [
      [z.arbeiten, 'Arbeit', 'Arbeiten'], [z.muster, 'Muster', 'Muster'],
      [z.material, 'Material', 'Material'], [z.goodnotes, 'Heft', 'Hefte'],
    ];
    for (const [n, ein, mehr] of teile) {
      if (!n) continue;
      const c = el('span', 'ln-chip');
      c.append(el('b', '', String(n)), ` ${n === 1 ? ein : mehr}`);
      zeile.append(c);
    }
    if (!zeile.children.length) zeile.append(el('span', 'ln-chip ln-leer', 'noch nichts'));
    return zeile;
  }

  function karte(f) {
    const k = el('button', 'ln-karte');
    k.type = 'button';
    const mitte = el('span', 'ln-mitte');
    mitte.append(el('b', 'ln-name', f.name), zaehler(f));
    k.append(badge(f.name), mitte, el('span', 'ln-pfeil', '›'));
    k.addEventListener('click', () => { ausgewaehlt = f.id; detail = null; fehler = ''; laden(); });
    return k;
  }

  function abschnitt(titel, zeilen, klasse) {
    if (!zeilen.length) return null;
    const b = el('section', `ln-block${klasse ? ` ${klasse}` : ''}`);
    const kopf = el('h4', '');
    kopf.append(el('span', '', titel), el('span', 'ln-anzahl', String(zeilen.length)));
    b.append(kopf);
    for (const z of zeilen) b.append(z);
    return b;
  }

  function musterZeile(m) {
    const z = el('div', 'ar-rand');
    z.append(el('span', 'ar-art ar-hinweis', m.person || 'Eike'), el('span', '', m.text),
      el('span', 'leise', `aus ${m.anzahl} ${m.anzahl === 1 ? 'Arbeit' : 'Arbeiten'}`));
    return z;
  }

  function arbeitZeile(a) {
    const z = el('div', 'ar-rand');
    const links = el('span', '', a.titel || 'Ohne Titel');
    z.append(links, el('span', 'leise', [a.person, tag(a.datum)].filter(Boolean).join(' · ')),
      el('span', `ar-art ar-${a.status === 'eingetragen' || a.status === 'gelesen' ? 'lob' : 'hinweis'}`, a.status));
    return z;
  }

  function materialZeile(d) {
    const z = el('div', 'ar-rand');
    const link = el('a', '', d.dateiname);
    if (d.status === 'gelesen') { link.href = `/api/gruppenordner/${d.id}/original`; link.target = '_blank'; link.rel = 'noopener'; }
    else link.href = '#';
    z.append(link, el('span', 'leise', d.zusammenfassung || d.status), el('span', 'leise', d.kurs_text));
    return z;
  }

  function heftZeile(h) {
    const z = el('div', 'ar-rand');
    z.append(el('span', '', h.name), el('span', 'leise', `${h.ordner} · Extras → HA zum Nachlesen`));
    return z;
  }

  function wichtigesListe(punkte) {
    const t = el('div', 'chips ar-themen');
    for (const w of punkte) t.append(el('span', 'ar-thema', w));
    return t;
  }

  function aktuellesMaterial(d) {
    const box = el('div', 'ar-rand');
    const link = el('a', '', d.dateiname);
    link.href = `/api/gruppenordner/${d.id}/original`; link.target = '_blank'; link.rel = 'noopener';
    box.append(link, el('span', 'leise', d.zusammenfassung));
    if (d.wichtiges && d.wichtiges.length) box.append(wichtigesListe(d.wichtiges));
    return box;
  }

  function aktuelleSeite(s) {
    const box = el('div', 'ar-rand');
    box.append(el('span', '', s.titel || s.name), el('span', 'leise', [s.name, tag(s.datum)].filter(Boolean).join(' · ')));
    if (s.text) box.append(el('p', 'ln-auszug', s.text.length > 400 ? s.text.slice(0, 400) + ' …' : s.text));
    return box;
  }

  function zeichnen() {
    const ziel = $('lernen-inhalt');
    if (!ziel) return;
    ziel.textContent = '';
    if (!geladen) { ziel.append(el('p', 'leer-klein', 'lädt …')); return; }
    if (fehler) ziel.append(el('p', 'status-fehler', fehler));

    if (ausgewaehlt === null) {
      if (!faecher.length) { ziel.append(el('p', 'leise', 'Noch keine Fächer eingerichtet — erst unter Noten anlegen.')); return; }
      ziel.append(el('p', 'ln-einleitung', 'Wähle ein Fach: Noten, Fehlermuster, Kursmaterial und Hefte an einem Ort.'));
      const raster = el('div', 'ln-raster');
      for (const f of faecher) raster.append(karte(f));
      ziel.append(raster);
      return;
    }
    if (!detail) return;
    const zurueck = el('button', 'ln-zurueck', '‹ Alle Fächer');
    zurueck.type = 'button';
    zurueck.addEventListener('click', () => { ausgewaehlt = null; zeichnen(); });
    const kopf = el('div', 'ln-kopf');
    const titel = el('div', 'ln-titel');
    titel.append(el('h3', '', detail.fach.name), zaehler({
      arbeiten: detail.arbeiten.length, muster: detail.muster.length,
      material: detail.material.length, goodnotes: detail.goodnotes.length }));
    kopf.append(badge(detail.fach.name), titel);
    ziel.append(zurueck, kopf);

    const aktuell = detail.aktuell || { material: [], seiten: [] };
    const b0 = abschnitt('Aktuell im Unterricht', [
      ...aktuell.material.map(aktuellesMaterial), ...aktuell.seiten.map(aktuelleSeite)], 'ln-aktuell');
    if (b0) ziel.append(b0);

    const noten = [];
    for (const [hj, s] of Object.entries(detail.fach.semester || {})) {
      if (s.eintraege && s.eintraege.length) noten.push(el('div', 'ar-rand', `${hj}: Schnitt ${s.wert ?? '–'}`));
    }
    const b1 = abschnitt('Noten', noten);
    if (b1) ziel.append(b1);

    const b2 = abschnitt('Muster (wiederkehrende Fehler)', detail.muster.map(musterZeile));
    if (b2) ziel.append(b2);

    const b3 = abschnitt('Arbeiten', detail.arbeiten.map(arbeitZeile));
    if (b3) ziel.append(b3);

    const b4 = abschnitt('Material aus dem Gruppenordner', detail.material.map(materialZeile));
    if (b4) ziel.append(b4);

    const b5 = abschnitt('Goodnotes-Hefte', detail.goodnotes.map(heftZeile));
    if (b5) ziel.append(b5);

    if (!noten.length && !detail.muster.length && !detail.arbeiten.length && !detail.material.length && !detail.goodnotes.length) {
      ziel.append(el('p', 'leise', 'Zu diesem Fach liegt noch nichts vor.'));
    }
  }

  const aktiv = () => location.hash === '#schule' && $('r-lernen') && !$('r-lernen').hidden;

  async function laden() {
    if (!aktiv()) return;
    try {
      if (ausgewaehlt === null) {
        faecher = (await holen('/api/lernen')).faecher;
      } else {
        detail = await holen(`/api/lernen/${ausgewaehlt}`);
      }
      geladen = true;
    } catch (e) {
      fehler = e.message || 'Lernen ist gerade nicht erreichbar.';
    }
    zeichnen();
  }

  document.addEventListener('schule-reiter', laden);
  window.addEventListener('hashchange', laden);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') laden(); });
  laden();
})();
