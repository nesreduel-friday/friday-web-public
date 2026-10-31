/* Einstellungen: Morgenbriefing an/aus, Uhrzeit, Vorschau.

   Friday kann das Briefing auch im Gespräch umschalten — deshalb lädt der
   Block beim Öffnen der Seite und beim Zurückkehren in die App neu, statt
   einen alten Stand zu zeigen. Alles per textContent, nie als HTML. */
(() => {
  const { $ } = window.Friday;
  const URL = '/api/einstellungen/morgenbriefing';

  function zeigen(d) {
    if (!d || d.an === undefined) { laden(); return; }    // Fehler: echten Stand zurückholen
    $('b-an').checked = d.an;
    $('b-uhrzeit').value = d.uhrzeit;
    // Nur ein Hinweis, wenn etwas fehlt — sonst bleibt der Block zwei Zeilen kurz.
    const fehlt = [];
    if (!d.teile.wetter) fehlt.push('Wetter');
    if (!d.teile.schule) fehlt.push('Schule');
    if (!d.teile.termine) fehlt.push('Termine');
    let text = fehlt.length ? `Ohne ${fehlt.join(', ')} — nicht freigegeben.` : '';
    if (d.an && !d.geraete) text += ' Kein Gerät angemeldet.';
    $('b-hinweis').textContent = text.trim();
    $('b-hinweis').hidden = !text;
  }

  async function laden() {
    if (location.hash !== '#einstellungen' || document.visibilityState !== 'visible') return;
    try { zeigen(await (await fetch(URL, { cache: 'no-store' })).json()); } catch (_) { /* nächstes Mal */ }
  }

  async function senden(daten) {
    const r = await fetch(URL, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(daten),
    });
    zeigen(await r.json().catch(() => ({})));
  }

  $('b-an').addEventListener('change', () => senden({ an: $('b-an').checked }));
  $('b-uhrzeit').addEventListener('change', () => { if ($('b-uhrzeit').value) senden({ uhrzeit: $('b-uhrzeit').value }); });
  $('b-vorschau').addEventListener('click', async () => {
    const aus = $('b-ausgabe');
    if (!aus.hidden) { aus.hidden = true; return; }        // zweites Tippen klappt zu
    aus.hidden = false;
    aus.textContent = 'Friday stellt es zusammen …';
    try {
      aus.textContent = (await (await fetch('/api/morgenbriefing/vorschau', { cache: 'no-store' })).json()).text;
    } catch (_) {
      aus.textContent = 'Die Vorschau hat gerade nicht geklappt.';
    }
  });

  window.addEventListener('hashchange', laden);
  document.addEventListener('visibilitychange', laden);
  laden();
})();
