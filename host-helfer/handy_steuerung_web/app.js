'use strict';
/* Handy-Steuerung — Seite. Bild: H.264 über WebSocket → WebCodecs → Canvas. Eingaben: JSON-Ereignisse an den Dienst. */
const bild = document.getElementById('bild');
const zeichner = bild.getContext('2d');
const statusZeile = document.getElementById('status');
const neuKnopf = document.getElementById('neu');

let ws = null, decoder = null, konfig = null, hatSchluessel = false, breite = 0, hoehe = 0, ping = null, bewegung = null;

function sagen(text, fehler = false) {
  statusZeile.textContent = text;
  statusZeile.classList.toggle('fehler', fehler);
}

function senden(e) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(e));
}

function decoderStarten(codec) {
  if (decoder) { try { decoder.close(); } catch (_) { /* schon zu */ } }
  hatSchluessel = false;
  decoder = new VideoDecoder({
    output: (frame) => { zeichner.drawImage(frame, 0, 0, bild.width, bild.height); frame.close(); },
    error: (e) => sagen('Bild-Decoder: ' + e.message, true),
  });
  decoder.configure({ codec, optimizeForLatency: true });
}

function paket(daten) {
  const v = new DataView(daten);
  const art = v.getUint8(0);
  const pts = Number(v.getBigUint64(1));
  const nutz = new Uint8Array(daten, 9);
  if (art === 0) { konfig = nutz; return; }
  if (!decoder || decoder.state !== 'configured') return;
  if (art === 1) {
    hatSchluessel = true;
    const voll = new Uint8Array((konfig ? konfig.length : 0) + nutz.length);
    if (konfig) voll.set(konfig, 0);
    voll.set(nutz, konfig ? konfig.length : 0);
    decoder.decode(new EncodedVideoChunk({ type: 'key', timestamp: pts, data: voll }));
  } else if (hatSchluessel) {
    decoder.decode(new EncodedVideoChunk({ type: 'delta', timestamp: pts, data: nutz }));
  }
}

function verbinden() {
  if (!('VideoDecoder' in window)) {
    sagen('Dieser Browser kann kein Video entschlüsseln (WebCodecs). Bitte Chrome oder Edge über https öffnen.', true);
    return;
  }
  neuKnopf.hidden = true;
  sagen('Verbinde …');
  ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  ws.binaryType = 'arraybuffer';
  ws.onmessage = (m) => {
    if (typeof m.data !== 'string') { paket(m.data); return; }
    const d = JSON.parse(m.data);
    if (d.type === 'status') sagen(d.text);
    else if (d.type === 'info') {
      breite = d.w; hoehe = d.h; bild.width = d.w; bild.height = d.h;
      bild.style.aspectRatio = d.w + ' / ' + d.h;
      sagen('Verbunden: ' + d.name);
      bild.focus();
    } else if (d.type === 'codec') decoderStarten(d.codec);
    else if (d.type === 'fehler') sagen(d.text, true);
  };
  ws.onclose = () => {
    clearInterval(ping);
    if (decoder) { try { decoder.close(); } catch (_) { /* schon zu */ } decoder = null; }
    if (!statusZeile.classList.contains('fehler')) sagen('Getrennt');
    neuKnopf.hidden = false;
  };
  ws.onopen = () => { ping = setInterval(() => senden({ t: 'ping' }), 5000); };
}

function punkt(e) {
  const r = bild.getBoundingClientRect();
  return { x: Math.round((e.clientX - r.left) / r.width * breite), y: Math.round((e.clientY - r.top) / r.height * hoehe) };
}

bild.addEventListener('pointerdown', (e) => {
  if (!breite) return;
  bild.setPointerCapture(e.pointerId);
  bild.focus();
  senden({ t: 'touch', a: 'down', id: 0, ...punkt(e) });
});
bild.addEventListener('pointermove', (e) => {
  if (!bild.hasPointerCapture(e.pointerId)) return;
  const p = punkt(e);
  if (bewegung) { bewegung = p; return; }          // höchstens eine Bewegung je Bildwechsel
  bewegung = p;
  requestAnimationFrame(() => { senden({ t: 'touch', a: 'move', id: 0, ...bewegung }); bewegung = null; });
});
const loslassen = (e) => {
  if (!bild.hasPointerCapture(e.pointerId)) return;
  bild.releasePointerCapture(e.pointerId);
  senden({ t: 'touch', a: 'up', id: 0, ...punkt(e) });
};
bild.addEventListener('pointerup', loslassen);
bild.addEventListener('pointercancel', loslassen);
bild.addEventListener('wheel', (e) => {
  e.preventDefault();
  const raster = e.deltaMode === 0 ? e.deltaY / 100 : e.deltaY;
  senden({ t: 'scroll', dx: 0, dy: -raster, ...punkt(e) });
}, { passive: false });
bild.addEventListener('contextmenu', (e) => { e.preventDefault(); senden({ t: 'taste', name: 'zurueck' }); });   // Rechtsklick = Zurück

const SONDER = { Enter: 'enter', Backspace: 'loeschen', Tab: 'tab', Escape: 'esc', ArrowUp: 'hoch', ArrowDown: 'runter',
  ArrowLeft: 'links', ArrowRight: 'rechts', Home: 'pos1', End: 'ende', Delete: 'entf' };
bild.addEventListener('keydown', (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;     // Strg+V löst `paste` aus, der Rest bleibt beim Browser
  if (SONDER[e.key]) { e.preventDefault(); senden({ t: 'taste', name: SONDER[e.key] }); }
  else if (e.key.length === 1) { e.preventDefault(); senden({ t: 'text', s: e.key }); }
});
document.addEventListener('paste', (e) => {
  const s = e.clipboardData && e.clipboardData.getData('text');
  if (s) { e.preventDefault(); senden({ t: 'text', s: s.slice(0, 300) }); }
});

document.querySelectorAll('button[data-taste]').forEach((b) =>
  b.addEventListener('click', () => { senden({ t: 'taste', name: b.dataset.taste }); bild.focus(); }));
neuKnopf.addEventListener('click', verbinden);
window.addEventListener('beforeunload', () => { if (ws) ws.close(); });

verbinden();
