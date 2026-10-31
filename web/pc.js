/* PC: Rückfragen für heikle Aktionen am PC — auf jeder Seite —, der Reiter
   Extras → PC und das Einrichten auf der Freigaben-Seite.

   Heikles (verschieben, löschen, herunterfahren, Kaufen-Knöpfe) führt Friday erst
   aus, wenn Eike hier auf „Ausführen“ tippt. Die Karte zeigt den Wortlaut, den der
   Helfer auf dem PC selbst erzeugt hat — nicht den des Modells. Alles per
   textContent, nie als HTML. */
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
  const senden = async (url, daten) => {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                 body: JSON.stringify(daten ?? {}) });
    const d = await r.json().catch(() => ({}));
    return { ok: r.ok && d.ok !== false, text: d.text || d.detail || (r.ok ? '' : `Fehler ${r.status}`), ...d };
  };
  const uhr = (iso) => { try { return new Date(iso).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }); } catch (_) { return ''; } };

  /* ---------- Rückfragen ---------- */

  const leiste = $('pc-karten');
  let letzte = { aktiv: false, offen: [], verlauf: [] };
  const entschieden = new Map();       // id → Ergebnistext, kurz stehen lassen

  function kartenZeigen() {
    leiste.replaceChildren();
    for (const o of letzte.offen) {
      if (entschieden.has(o.id)) continue;
      const k = el('div', 'pc-karte');
      k.append(el('div', 'pc-karte-kopf', 'Friday möchte am PC:'), el('div', 'pc-karte-text', o.beschreibung));
      if (o.fremd) {
        k.append(el('div', 'pc-karte-warn',
          'Entstand nach fremdem Text (Webseite, Mail, Dateinamen). Prüf genau, ob du das wirklich willst.'));
      }
      k.append(el('div', 'leise', `gilt noch ${Math.max(1, Math.ceil(o.rest_s / 60))} Min.`));
      const reihe = el('div', 'knoepfe');
      const entscheiden = async (ja, b) => {
        for (const x of reihe.querySelectorAll('button')) x.disabled = true;
        b.textContent = ja ? 'wird ausgeführt …' : 'lehne ab …';
        const r = await senden(`/api/pc/offen/${encodeURIComponent(o.id)}`, { ja });
        entschieden.set(o.id, r);
        ergebnisZeigen(o.beschreibung, r);
        holen();
      };
      reihe.append(knopf('Ausführen', 'ja', (b) => entscheiden(true, b)),
                   knopf('Ablehnen', 'weg', (b) => entscheiden(false, b)));
      k.append(reihe);
      leiste.append(k);
    }
    leiste.hidden = !leiste.childElementCount;
  }

  function ergebnisZeigen(beschreibung, r) {
    const k = el('div', `pc-karte ${r.ok ? 'fertig' : 'fehler'}`);
    k.append(el('div', 'pc-karte-kopf', beschreibung), el('div', 'pc-karte-text', r.text || (r.ok ? 'Erledigt.' : 'Hat nicht geklappt.')));
    k.addEventListener('click', () => k.remove());
    leiste.prepend(k);
    leiste.hidden = false;
    setTimeout(() => { k.remove(); leiste.hidden = !leiste.childElementCount; }, 6000);
  }

  let laeuft = false;
  async function holen() {
    if (laeuft) return;
    laeuft = true;
    try {
      const r = await fetch('/api/pc/offen', { cache: 'no-store' });
      if (r.ok) letzte = await r.json();
    } catch (_) { /* nächstes Mal */ }
    laeuft = false;
    const ids = new Set(letzte.offen.map((o) => o.id));
    for (const id of [...entschieden.keys()]) if (!ids.has(id)) entschieden.delete(id);
    // Ergebniskarten nicht wegwischen: nur die offenen neu zeichnen.
    for (const k of [...leiste.querySelectorAll('.pc-karte:not(.fertig):not(.fehler)')]) k.remove();
    const fertige = [...leiste.children];
    kartenZeigen();
    leiste.prepend(...fertige);
    leiste.hidden = !leiste.childElementCount;
    if (location.hash === '#studio' && reiterAktiv()) pcSeite(false);
  }

  // Alle 4 s, solange die App sichtbar ist — seltener, wenn der PC gar nicht freigegeben ist.
  let zaehler = 0;
  setInterval(() => {
    if (document.visibilityState !== 'visible') return;
    zaehler += 1;
    if (letzte.aktiv || zaehler % 15 === 0) holen();
  }, 4000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') holen(); });
  holen();

  /* ---------- Extras → PC ---------- */

  const reiterAktiv = () => document.querySelector('#studio .reiter button[data-reiter="pc"]')?.classList.contains('aktiv');

  const WIE = { direkt: 'ausgeführt', bestaetigt: 'bestätigt', abgelehnt: 'abgelehnt', verfallen: 'verfallen', fehler: 'Fehler' };

  // Gezeichnet wird in web/pcseite.js (Reiter für alles, was der Helfer kann); hier bleibt nur der Anstoß.
  function pcSeite(neuLaden = true) {
    window.FridayPcSeite?.zeichnen($('pc-inhalt'), { letzte, neuLaden });
  }

  const reiterKnopf = document.querySelector('#studio .reiter button[data-reiter="pc"]');
  reiterKnopf?.addEventListener('click', () => pcSeite(true));
  window.addEventListener('hashchange', () => { if (location.hash === '#studio' && reiterAktiv()) pcSeite(true); });

  /* ---------- Einrichten (Freigaben → PC) ---------- */

  function einrichten(d, k, neuLaden) {
    d.append(el('h4', '', 'Einrichten'));
    d.append(el('p', 'klein', 'Friday erzeugt einen Befehl. Den führst du auf dem PC in PowerShell aus (Start → „PowerShell“ '
      + '→ einfügen → Enter). Er installiert Python falls nötig, den Helfer, den Autostart und eine Firewall-Regel, die nur '
      + 'hphome hereinlässt (Windows fragt dafür einmal nach Administratorrechten). Der Befehl gilt 30 Minuten und nur einmal. '
      + 'Nochmal ausführen = aktualisieren.'));
    const form = el('form', 'neu-fakt');
    const adresse = el('input');
    adresse.type = 'text'; adresse.placeholder = 'Adresse des PCs, z. B. 100.64.0.20:8765';
    adresse.value = k.benutzer || ''; adresse.autocapitalize = 'off'; adresse.spellcheck = false;
    const ok = el('button', '', 'Befehl erzeugen'); ok.type = 'submit';
    form.append(adresse, ok);
    const neu = el('label', 'check klein');
    const cb = el('input'); cb.type = 'checkbox';
    neu.append(cb, el('span', '', 'Neuen Schlüssel erzeugen (der alte gilt dann nicht mehr)'));
    const ausgabe = el('div', 'ausgabe'); ausgabe.hidden = true;
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      ok.disabled = true;
      const r = await senden('/api/pc/einrichten', { adresse: adresse.value.trim(), neu: cb.checked });
      ok.disabled = false;
      ausgabe.hidden = false;
      ausgabe.replaceChildren();
      if (!r.ok) { ausgabe.textContent = r.text; return; }
      const befehl = el('code', 'pc-befehl', r.befehl);
      ausgabe.append(el('div', 'klein', `Für ${r.adresse}, gültig ${r.gueltig_min} Minuten:`), befehl);
      ausgabe.append(knopf('Kopieren', '', async (b) => {
        try { await navigator.clipboard.writeText(r.befehl); b.textContent = 'Kopiert'; } catch (_) { b.textContent = 'Bitte markieren und kopieren'; }
      }));
    });
    d.append(form, neu, ausgabe);
  }

  window.FridayPc = { jetzt: holen, einrichten, el, knopf, senden, uhr, WIE, letzte: () => letzte };
})();
