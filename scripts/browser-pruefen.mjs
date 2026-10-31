// Seite im echten Chromium prüfen (nur lesen und Reiter klicken): sammelt Konsolen-, Sicherheits- (CSP) und Netzwerkfehler.
//   node scripts/browser-pruefen.mjs URL [hash …] [--klick=SELEKTOR] [--eval=JS] [--bild=DATEI.png [--breite=430] [--hoehe=900] [--hell]]
//   Beispiel: node scripts/browser-pruefen.mjs http://127.0.0.1:8124/ studio --klick='[data-reiter="hphome"]' --eval='document.querySelector("#hp-dienste").innerText'
// Für jeden Hash: setzen, warten, dann (falls angegeben) den Klick ausführen, warten und den --eval-Ausdruck ausgeben.
// Chromium: Umgebungsvariable CHROME oder der von Playwright unter ~/.cache/ms-playwright.
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { existsSync, mkdtempSync, readdirSync, rmSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';

const args = process.argv.slice(2);
const opt = (name) => (args.find((a) => a.startsWith(`--${name}=`)) || '').slice(name.length + 3);
const [url, ...rest] = args.filter((a) => !a.startsWith('--'));
if (!url) { console.error('Aufruf: node browser-pruefen.mjs URL [hash …] [--klick=SEL] [--eval=JS]'); process.exit(2); }
const seiten = rest.length ? rest : [''];

function chromeFinden() {
  if (process.env.CHROME && existsSync(process.env.CHROME)) return process.env.CHROME;
  const wurzel = join(homedir(), '.cache', 'ms-playwright');
  const ordner = existsSync(wurzel) ? readdirSync(wurzel).filter((n) => /^chromium-\d+$/.test(n)).sort().reverse() : [];
  for (const o of ordner) { const p = join(wurzel, o, 'chrome-linux64', 'chrome'); if (existsSync(p)) return p; }
  throw new Error('Kein Chromium gefunden (CHROME setzen).');
}

const profil = mkdtempSync(join(tmpdir(), 'bp-'));
const port = 9300 + Math.floor(Math.random() * 500);
const chrome = spawn(chromeFinden(), ['--headless=new', '--no-sandbox', '--disable-gpu', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profil}`, 'about:blank'], { stdio: 'ignore' });
const warte = (ms) => new Promise((r) => setTimeout(r, ms));
const ende = (code) => { try { chrome.kill(); } catch (_) {} try { rmSync(profil, { recursive: true, force: true }); } catch (_) {} process.exit(code); };

let tabs;
for (let i = 0; i < 40; i++) {
  try { tabs = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); if (tabs.find((t) => t.type === 'page')) break; } catch (_) {}
  await warte(250);
}
if (!tabs) { console.error('Chromium startet nicht.'); ende(2); }
const ws = new WebSocket(tabs.find((t) => t.type === 'page').webSocketDebuggerUrl);
await new Promise((r) => { ws.onopen = r; });
let nr = 0;
const meldungen = [];
const ausgaben = [];
const wartend = new Map();
const senden = (method, params = {}) => new Promise((r) => { const id = ++nr; wartend.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && wartend.has(m.id)) { wartend.get(m.id)(m.result); wartend.delete(m.id); }
  if (m.method === 'Log.entryAdded') meldungen.push(`LOG ${m.params.entry.level} ${m.params.entry.source}: ${m.params.entry.text} ${m.params.entry.url || ''}`);
  if (m.method === 'Runtime.consoleAPICalled') meldungen.push(`CONSOLE ${m.params.type}: ${m.params.args.map((a) => a.value ?? a.description).join(' ')}`);
  if (m.method === 'Runtime.exceptionThrown') meldungen.push(`EXC ${m.params.exceptionDetails.text} ${m.params.exceptionDetails.exception?.description || ''}`);
  if (m.method === 'Network.loadingFailed') meldungen.push(`NETFAIL ${m.params.errorText} blocked=${m.params.blockedReason || ''}`);
};
for (const d of ['Log', 'Runtime', 'Network', 'Page']) await senden(`${d}.enable`);
// Handy-Ansicht für Bildschirmfotos (--bild): Breite/Höhe wählbar, Dunkel ist Standard, --hell prüft den hellen Modus.
const breite = Number(opt('breite')) || 430, hoehe = Number(opt('hoehe')) || 900;
if (opt('bild') || args.includes('--hell')) {
  await senden('Emulation.setDeviceMetricsOverride', { width: breite, height: hoehe, deviceScaleFactor: 2, mobile: true });
  await senden('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: args.includes('--hell') ? 'light' : 'dark' }] });
}
await senden('Page.navigate', { url });
await warte(5000);
const ausdruck = async (js) => (await senden('Runtime.evaluate', { expression: js, returnByValue: true, awaitPromise: true })).result?.value;
for (const s of seiten) {
  if (s) { await ausdruck(`location.hash = ${JSON.stringify('#' + s)}`); await warte(2000); }
  if (opt('klick')) { await ausdruck(`document.querySelector(${JSON.stringify(opt('klick'))})?.click()`); await warte(3000); }
  if (opt('eval')) ausgaben.push(`EVAL[${s || '-'}]: ${await ausdruck(opt('eval'))}`);
}
if (opt('bild')) {
  const m = await senden('Page.getLayoutMetrics');
  const h = Math.min(Math.ceil(m.cssContentSize?.height || hoehe), 4000);
  const bild = await senden('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: breite, height: h, scale: 1 } });
  writeFileSync(opt('bild'), Buffer.from(bild.data, 'base64'));
  ausgaben.push(`BILD: ${opt('bild')} (${breite}×${h})`);
}
console.log(ausgaben.join('\n'));
console.log(meldungen.length ? meldungen.join('\n') : 'KEINE Meldungen');
ende(0);
