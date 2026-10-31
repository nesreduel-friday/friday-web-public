// Verhaltenstest für web/werkbank.js mit eigenem DOM-Stub (keine Pakete). Aufruf: node werkbank_verhalten.js <pfad zu werkbank.js>
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const quelle = fs.readFileSync(process.argv[2], 'utf8');

class El {
  constructor(tag) { this.tag = tag; this.children = []; this.className = ''; this.textContent = ''; this.dataset = {};
    this.handlers = {}; this.disabled = false; this.scrollTop = 0; }
  append(...k) { this.children.push(...k); }
  replaceChildren(...k) { this.children = k; }
  addEventListener(n, f) { this.handlers[n] = f; }
  setAttribute(n, w) { this[n] = w; }
  click() { if (!this.disabled && this.handlers.click) this.handlers.click(); }
  all(f, out = []) { if (f(this)) out.push(this); this.children.forEach((c) => c.all(f, out)); return out; }
  querySelectorAll(sel) { const k = sel.slice(1); return this.all((e) => e.className.split(' ').includes(k)); }
}
let jetzt = 0, timer = [], naechste = 1;
const calls = [];
const wurzel = new El('div');
const vorhaben = { id: 7, titel: 'T', art: 'fehler', status: 'bereit', backend: 'claude', tests: 'ok', diff: true,
  zaehler: 2, erstmals: '2026-10-01T00:00:00', uebernahme: null, fremd: false, beleg: 'b', bericht: 'ok' };
let diffFehler = false;
const einst = { werkbank_auto: 'aus', werkbank_auto_backend: 'claude', auto_heute: 2, auto_limit: 5 };
let einstFehler = false;              // POST /api/werkbank/einstellungen schlägt fehl (z. B. 403)
let einstHalten = null;               // Funktion → Promise, das der Test selbst auflöst
let diffHalten = null;                // Funktion → liefert ein Promise, das der Test selbst auflöst
const ereignisse = {};
const sb = {
  window: { Friday: { $: (id) => ({ 'hphome-werkbank': wurzel, 'r-hphome': { hidden: false } })[id] }, addEventListener() {} },
  document: { addEventListener: (n, f) => { ereignisse[n] = f; }, querySelectorAll: () => [], createElement: (t) => new El(t) },
  location: { hash: '#studio' }, Date, JSON, Map, Set, Number, String, Promise, Array, Math,
  setTimeout: (f, ms) => { const id = naechste++; timer.push({ id, f, at: jetzt + ms }); return id; },
  clearTimeout: (id) => { timer = timer.filter((t) => t.id !== id); },
  setInterval: () => 0,
  fetch: async (url, opt) => {
    calls.push([url, opt && opt.method]);
    if (url === '/api/werkbank') return { ok: true, json: async () => ({ vorhaben: [vorhaben], heute_gestartet: 0, auto_heute: 2, auto_limit: 5, nachbesserungen_max: 2 }) };
    if (url === '/api/werkbank/einstellungen') {
      if (opt && opt.method === 'POST') {
        if (einstHalten) await einstHalten();
        if (einstFehler) return { ok: false, status: 403, json: async () => ({ detail: 'Nicht erlaubt.' }) };
        Object.assign(einst, JSON.parse(opt.body));
      }
      return { ok: true, json: async () => ({ ...einst }) };
    }
    if (url.endsWith('/diff')) {
      if (diffHalten) { const text = await diffHalten(); return { ok: true, json: async () => ({ diff: text, gekuerzt: false }) }; }
      return diffFehler ? { ok: false, status: 500, json: async () => ({ detail: { x: 1 } }) }
                        : { ok: true, json: async () => ({ diff: '+x', gekuerzt: false }) };
    }
    return { ok: true, json: async () => ({ ok: true, text: 'fertig' }) };
  },
};
vm.runInNewContext(quelle, sb);
const lass = () => new Promise((r) => setImmediate(r));
const vor = async (ms) => { jetzt += ms; for (const t of timer.filter((x) => x.at <= jetzt)) { timer = timer.filter((x) => x !== t); t.f(); } await lass(); await lass(); };
const knopf = (text) => wurzel.all((e) => e.tag === 'button' && e.textContent.startsWith(text))[0];
const posts = () => calls.filter((c) => c[1] === 'POST').map((c) => c[0]);

(async () => {
  // 1. Diff kaputt: Übernehmen erst gar nicht scharfstellbar, Fehlerantwort mit Objekt-detail wird nicht gecacht
  diffFehler = true;
  ereignisse.DOMContentLoaded(); await lass(); await lass();
  assert(!knopf('Übernehmen'), 'ohne Diff kein Übernehmen');
  const diffAufrufe = () => calls.filter((c) => c[0].endsWith('/diff')).length;
  const n1 = diffAufrufe();
  diffFehler = false;
  // Neuversuch beim nächsten Abruf
  ereignisse.DOMContentLoaded(); await lass(); await lass();
  assert(diffAufrufe() > n1, 'Diff-Fehler wird erneut versucht');
  assert(knopf('Übernehmen'), 'mit Diff ist Übernehmen da');

  // 2. Doppeltipp: erster Tipp schärft, zweiter sofort danach löst nichts aus
  knopf('Übernehmen').click(); await lass();
  const scharf = knopf('Wirklich übernehmen');
  assert(scharf && scharf.disabled, 'Bestätigung zunächst gesperrt');
  scharf.click(); await lass();
  assert.deepStrictEqual(posts(), [], 'Doppeltipp löst nichts aus');

  // 3. Nach 1,5 s frei; nach 10 s fällt die Scharfstellung zurück
  await vor(1500);
  assert(!knopf('Wirklich übernehmen').disabled, 'nach 1,5 s bedienbar');
  await vor(8500);
  assert(!knopf('Wirklich übernehmen') && knopf('Übernehmen'), 'nach 10 s zurückgenommen');

  // 4. Regulär: schärfen, warten, bestätigen → genau ein POST
  knopf('Übernehmen').click(); await vor(1500);
  knopf('Wirklich übernehmen').click(); await lass(); await lass();
  assert.deepStrictEqual(posts(), ['/api/werkbank/7/uebernehmen']);

  // 5. Zurückgerollt: kein Knopf, Hinweis
  vorhaben.uebernahme = 'zurueckgerollt';
  ereignisse.DOMContentLoaded(); await lass(); await lass();
  assert(!knopf('Übernehmen'));
  assert(wurzel.all((e) => e.textContent.includes('Zurückgerollt')).length);

  // 6. Statuswechsel während eines Diff-Abrufs: das veraltete Ergebnis wird nie eingetragen
  vorhaben.uebernahme = null; vorhaben.id = 8;
  const offen = [];
  diffHalten = () => new Promise((r) => offen.push(r));
  ereignisse.DOMContentLoaded(); await lass(); await lass();
  assert.strictEqual(offen.length, 1, 'erster Diff-Abruf läuft');
  vorhaben.status = 'laeuft'; vorhaben.diff = false;
  ereignisse.DOMContentLoaded(); await lass(); await lass();
  vorhaben.status = 'bereit'; vorhaben.diff = true;
  ereignisse.DOMContentLoaded(); await lass(); await lass();
  assert.strictEqual(offen.length, 2, 'nach dem Wechsel ein neuer Abruf');
  offen[0]('+alt'); await lass(); await lass();
  const diffText = () => wurzel.all((e) => e.tag === 'pre').map((e) => e.textContent).join('|');
  assert(!diffText().includes('+alt'), 'veralteter Diff wird nicht gezeigt');
  assert(!knopf('Übernehmen'), 'mit veraltetem Diff kein Übernehmen');
  offen[1]('+neu'); await lass(); await lass();
  assert(diffText().includes('+neu'), 'aktueller Diff wird gezeigt');
  assert(knopf('Übernehmen'), 'mit aktuellem Diff Übernehmen');
  diffHalten = null;

  // 7. Fehlgeschlagen: kein Start-Knopf, Hinweis auf Verwerfen
  vorhaben.status = 'fehler'; vorhaben.diff = false;
  ereignisse.DOMContentLoaded(); await lass(); await lass();
  assert(!knopf('Starten'), 'fehler ist nicht startbar');
  assert(wurzel.all((e) => e.textContent.includes('neu vorschlagen')).length, 'Hinweis auf Verwerfen');
  vorhaben.status = 'vorgeschlagen';
  ereignisse.DOMContentLoaded(); await lass(); await lass();
  assert(knopf('Starten (Claude)'), 'vorgeschlagen ist startbar');

  // 8. Nachbesserung: während des Laufs „Nachbesserung n von 2“, danach die Zahl; bei Rot kein Übernehmen
  const texte = () => wurzel.all((e) => typeof e.textContent === 'string').map((e) => e.textContent).join('|');
  vorhaben.status = 'laeuft'; vorhaben.nachbesserung = 1; vorhaben.nachbesserungen = 1; vorhaben.diff = false;
  ereignisse.DOMContentLoaded(); await lass(); await lass();
  assert(texte().includes('Nachbesserung 1 von 2 läuft'), 'laufende Nachbesserung wird gezeigt');
  vorhaben.status = 'bereit'; vorhaben.nachbesserung = null; vorhaben.nachbesserungen = 2; vorhaben.tests = 'rot';
  vorhaben.diff = true;
  ereignisse.DOMContentLoaded(); await lass(); await lass();
  assert(!texte().includes('läuft …'), 'nach dem Lauf keine laufende Nachbesserung');
  assert(texte().includes('Trotz 2 Nachbesserungen nicht grün.'), 'ehrlich bei Rot');
  assert(!knopf('Übernehmen'), 'rot bleibt nicht übernehmbar');
  vorhaben.nachbesserungen = 1; vorhaben.tests = 'ok';
  ereignisse.DOMContentLoaded(); await lass(); await lass();
  assert(texte().includes('Grün nach 1 Nachbesserung.'), 'Zahl bei grün');
  vorhaben.nachbesserung = '<img src=x onerror=1>'; vorhaben.status = 'laeuft';
  ereignisse.DOMContentLoaded(); await lass(); await lass();
  assert(!texte().includes('<img'), 'nur Zahlen, kein fremder Text');

  // 9. Hebel: Standard aus, Anzeige „Automatisch heute“, Umschalten, Fehler setzt zurück, Doppeltipp nur einmal
  vorhaben.nachbesserung = null; vorhaben.status = 'vorgeschlagen';
  ereignisse.DOMContentLoaded(); await lass(); await lass();
  const schalter = () => wurzel.all((e) => e.tag === 'input' && e.type === 'checkbox')[0];
  const wahl = () => wurzel.all((e) => e.tag === 'select')[0];
  const einstPosts = () => calls.filter((c) => c[0] === '/api/werkbank/einstellungen' && c[1] === 'POST').length;
  assert(schalter() && schalter().checked === false, 'Automatik steht auf aus');
  assert.strictEqual(wahl().value, 'claude');
  assert(texte().includes('Automatisch heute: 2 von 5 · manuell unbegrenzt'), 'Zähler nur für Automatik');
  assert(!texte().includes('Heute gestartet'), 'kein altes Tageslimit');
  schalter().checked = true; schalter().handlers.change(); await lass(); await lass(); await lass();
  assert.strictEqual(einst.werkbank_auto, 'an');
  assert(schalter().checked === true, 'nach dem Umschalten an');
  // Fehler: die Auswahl springt auf den gespeicherten Wert zurück, Meldung sichtbar
  einstFehler = true;
  wahl().value = 'opencode'; wahl().handlers.change(); await lass(); await lass(); await lass();
  assert.strictEqual(wahl().value, 'claude', 'nach Fehler zurückgesetzt');
  assert(texte().includes('Nicht erlaubt.'), 'Fehler wird gezeigt');
  einstFehler = false;
  // Doppeltipp: zweiter Wechsel während des ersten Aufrufs löst keinen zweiten POST aus
  const vorher = einstPosts();
  let loesen; einstHalten = () => new Promise((r) => { loesen = r; });
  const w1 = wahl();
  w1.value = 'opencode'; w1.handlers.change(); await lass();
  assert(w1.disabled, 'während des Aufrufs gesperrt');
  w1.handlers.change(); await lass();
  assert.strictEqual(einstPosts(), vorher + 1, 'nur ein POST');
  einstHalten = null; loesen(); await lass(); await lass(); await lass();
  assert.strictEqual(einst.werkbank_auto_backend, 'opencode');
  assert.strictEqual(wahl().value, 'opencode');
  assert(texte().includes('Gratis-Modelle können auf Eingaben trainieren'), 'Hinweis bei OpenCode');
  console.log('ok');
})().catch((e) => { console.error(e); process.exit(1); });
