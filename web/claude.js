/* Extras → hphome → Claude-Sitzungen: Claude-Code-Sitzungen auf hphome starten, sehen, beenden.

   Der Knopf startet keine Sitzung im Browser: Er legt einen Auftrag ab, den der Helfer
   auf dem Host ausführt (`host-helfer/claude_helfer.py`). Weitergearbeitet wird dann am
   Handy oder am PC über den Link — die Sitzung selbst läuft auf hphome weiter.
   Alles per textContent, nie als HTML. */
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
    return { ok: r.ok && d.ok !== false, text: d.text || d.detail || (r.ok ? '' : `Fehler ${r.status}`) };
  };
  const seit = (sek) => {
    const min = Math.max(0, Math.round((Date.now() / 1000 - (sek || 0)) / 60));
    if (min < 60) return `läuft seit ${min} min`;
    return `läuft seit ${Math.floor(min / 60)} h ${min % 60} min`;
  };

  let stand = { helfer: false, sitzungen: [], max: 0, text: '' };
  let meldung = '';
  let beschaeftigt = false;

  function zeichnen() {
    const ziel = $('hphome-claude');
    if (!ziel) return;
    ziel.replaceChildren();

    ziel.append(el('p', 'leise', 'Startet auf hphome eine Claude-Code-Sitzung mit Fernzugriff. '
      + 'Weiterarbeiten kannst du danach am Handy in der Claude-App oder über den Link — '
      + 'die Sitzung läuft auf dem Server weiter, auch wenn du weggehst. '
      + 'Claude fragt dort vor jeder Änderung nach.'));

    if (!stand.helfer) {
      ziel.append(el('p', 'status-fehler', stand.text || 'Der Helfer auf hphome läuft nicht.'));
    }

    if (!stand.sitzungen.length) {
      ziel.append(el('p', 'leer-klein', 'Keine Sitzung läuft.'));
    }
    for (const s of stand.sitzungen) {
      const k = el('div', 'eintrag');
      const kopf = el('div', 'kopfzeile');
      kopf.append(el('div', 'titel', s.name), el('span', 'leise', seit(s.seit)));
      k.append(kopf);
      if (s.link) {
        const a = el('a', 'cl-link', 'Am Handy öffnen');
        a.href = s.link; a.target = '_blank'; a.rel = 'noopener';
        k.append(a);
      } else {
        k.append(el('div', 'klein', 'Link kommt gleich …'));
      }
      const reihe = el('div', 'knoepfe');
      reihe.append(knopf('Beenden', 'weg', async (b) => {
        b.disabled = true;
        const r = await senden('/api/claude/beenden', { name: s.name });
        meldung = r.text; await laden();
      }));
      k.append(reihe);
      ziel.append(k);
    }

    const zeile = el('div', 'cl-zeile');
    const feld = el('input');
    feld.type = 'text'; feld.placeholder = 'Name (freiwillig)'; feld.maxLength = 24; feld.id = 'claude-name';
    const start = knopf('Neue Sitzung', '', async (b) => {
      if (beschaeftigt) return;
      beschaeftigt = true; b.disabled = true; b.textContent = 'startet …';
      const r = await senden('/api/claude/starten', { name: feld.value });
      meldung = r.text; beschaeftigt = false;
      await laden();
    });
    start.disabled = !stand.helfer || (stand.max > 0 && stand.sitzungen.length >= stand.max);
    zeile.append(feld, start);
    ziel.append(zeile);
    if (stand.max) ziel.append(el('p', 'leise', `Höchstens ${stand.max} Sitzungen gleichzeitig.`));
    if (meldung) ziel.append(el('p', 'leise', meldung));
  }

  function sichtbar() {
    const r = $('r-hphome');
    return location.hash === '#studio' && r && !r.hidden;
  }

  async function laden() {
    if (!sichtbar()) return;
    try {
      const r = await fetch('/api/claude/zustand');
      stand = await r.json();
    } catch (_) {
      stand = { helfer: false, sitzungen: [], max: 0, text: 'Friday ist gerade nicht erreichbar.' };
    }
    zeichnen();
  }

  window.addEventListener('hashchange', laden);
  document.addEventListener('DOMContentLoaded', laden);
  for (const b of document.querySelectorAll('#studio .reiter button')) {
    b.addEventListener('click', () => { if (b.dataset.reiter === 'hphome') { meldung = ''; laden(); } });
  }
  setInterval(laden, 5000);
})();
