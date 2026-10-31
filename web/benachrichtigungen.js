/* Einstellungen → was Friday aufs Handy schickt, und welche Abfahrten sie im Blick hat.

   Zwei getrennte Dinge auf einer Seite:
   - die ARTEN (Morgenbriefing, Erinnerungen, Studio …) samt Ruhezeit
   - die WACHEN („sag Bescheid, wenn der 300er Verspätung hat“)

   Friday kann beides auch im Gespräch stellen — deshalb wird beim Öffnen der
   Seite neu geladen und nie aus dem Speicher gezeigt: Was hier steht, könnte
   sonst älter sein als das, was Eike ihr gerade gesagt hat.

   Alles per textContent, nie als HTML. */
(() => {
  const { $ } = window.Friday;

  const el = (tag, klasse, text) => {
    const e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };

  const holen = (url) => fetch(url, { cache: 'no-store' }).then((r) => r.json());
  const senden = (url, daten, art) => fetch(url, {
    method: art || 'POST', headers: { 'Content-Type': 'application/json' },
    body: art === 'DELETE' ? undefined : JSON.stringify(daten || {}),
  }).then(async (r) => ({ ok: r.ok, ...(await r.json().catch(() => ({}))) }));

  // ---------- Arten und Ruhezeit ----------
  const liste = $('n-liste');
  const kurz = $('n-kurz');
  const ruhe = $('n-ruhe');
  const zeiten = $('n-zeiten');
  const ab = $('n-ab');
  const bis = $('n-bis');

  function zeigen(d) {
    liste.replaceChildren();
    const an = (d.arten || []).filter((a) => a.an).length;
    kurz.textContent = `— ${an} von ${(d.arten || []).length} an`;
    if (!d.geraete) {
      liste.append(el('p', 'leer-klein',
        'Es ist kein Gerät angemeldet — bei dir kommt gerade nichts an. Oben einschalten.'));
    }
    for (const a of d.arten || []) {
      const zeile = el('div', 'n-zeile');
      const text = el('span');
      text.append(el('b', '', a.name), el('span', 'leise', a.beschreibung));
      const schalter = el('label', 'schalter');
      const cb = el('input');
      cb.type = 'checkbox'; cb.checked = !!a.an;
      cb.setAttribute('aria-label', a.name);
      cb.addEventListener('change', async () => {
        cb.disabled = true;
        const neu = await senden('/api/einstellungen/benachrichtigungen', { art: a.id, an: cb.checked });
        cb.disabled = false;
        if (neu.arten) zeigen(neu);
      });
      schalter.append(cb, el('span', 'bahn'));
      zeile.append(text, schalter);
      liste.append(zeile);
    }
    ruhe.checked = !!(d.ruhe && d.ruhe.an);
    zeiten.hidden = !ruhe.checked;
    if (d.ruhe) { ab.value = d.ruhe.ab; bis.value = d.ruhe.bis; }
  }

  const ruheSenden = async () => {
    const neu = await senden('/api/einstellungen/benachrichtigungen',
      { ruhe_an: ruhe.checked, ruhe_ab: ab.value, ruhe_bis: bis.value });
    if (neu.arten) zeigen(neu);
  };
  ruhe.addEventListener('change', ruheSenden);
  ab.addEventListener('change', ruheSenden);
  bis.addEventListener('change', ruheSenden);

  // ---------- Abfahrten im Blick ----------
  const wListe = $('w-liste');
  const wKurz = $('w-kurz');
  const wHinweis = $('w-hinweis');

  function wZeigen(d) {
    wListe.replaceChildren();
    const wachen = d.wachen || [];
    wKurz.textContent = wachen.length ? `— ${wachen.length}` : '';
    if (!wachen.length) {
      wListe.append(el('p', 'leer-klein', 'Noch keine Fahrt im Blick.'));
    }
    // Ohne die Art kommt keine einzige Meldung an — das muss hier stehen, sonst
    // wartet Eike auf etwas, das nie klingelt.
    if (wachen.length && !d.art_an) {
      wListe.append(el('p', 'leer-klein',
        '„Abfahrten im Blick" ist oben ausgeschaltet — so kommt keine Meldung an.'));
    }
    for (const w of wachen) {
      const zeile = el('div', w.aktiv ? 'w-zeile' : 'w-zeile pause');
      zeile.append(el('span', 'w-text', w.text));
      const knoepfe = el('span', 'knoepfe');
      const pause = el('button', 'leise-knopf', w.aktiv ? 'Pause' : 'Weiter');
      pause.type = 'button';
      pause.addEventListener('click', async () => {
        await senden(`/api/wachen/${w.id}`, { aktiv: !w.aktiv });
        wLaden();
      });
      const weg = el('button', 'leise-knopf', 'Weg');
      weg.type = 'button';
      weg.addEventListener('click', async () => {
        await senden(`/api/wachen/${w.id}`, null, 'DELETE');
        wLaden();
      });
      knoepfe.append(pause, weg);
      zeile.append(knoepfe);
      wListe.append(zeile);
    }
  }

  $('w-neu').addEventListener('click', async (e) => {
    const knopf = e.currentTarget;
    const halt = $('w-halt').value.trim();
    wHinweis.hidden = false;
    if (!halt) { wHinweis.textContent = 'Welche Haltestelle?'; return; }
    knopf.disabled = true;
    wHinweis.textContent = 'Friday prüft die Haltestelle …';
    const antwort = await senden('/api/wachen', {
      haltestelle: halt, linie: $('w-linie').value.trim(), richtung: $('w-richtung').value.trim(),
      ab: $('w-ab').value, bis: $('w-bis').value,
      tage: $('w-werktags').checked ? [0, 1, 2, 3, 4] : [],
    });
    knopf.disabled = false;
    if (!antwort.ok) { wHinweis.textContent = antwort.detail || 'Das hat nicht geklappt.'; return; }
    wHinweis.hidden = true;
    $('w-halt').value = ''; $('w-linie').value = ''; $('w-richtung').value = '';
    wLaden();
  });

  async function wLaden() {
    try { wZeigen(await holen('/api/wachen')); }
    catch (_) { wListe.replaceChildren(el('p', 'leer-klein', 'Nicht abrufbar.')); }
  }

  async function laden() {
    try { zeigen(await holen('/api/einstellungen/benachrichtigungen')); }
    catch (_) { liste.replaceChildren(el('p', 'leer-klein', 'Nicht abrufbar.')); }
    wLaden();
  }

  const beiAnzeige = () => { if (location.hash === '#einstellungen') laden(); };
  window.addEventListener('hashchange', beiAnzeige);
  beiAnzeige();
})();
