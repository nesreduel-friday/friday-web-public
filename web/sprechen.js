/* Sprechen — die Maschine hinter dem Kreis im Kopf: Mikrofon, Audio, Verbindung
   zu /ws/live (Sprach-Loop aus seminar-ki, dort zuverlässig).

   Diese Datei zeichnet kein Transkript mehr und kennt keine Eingabefelder. Sie meldet
   über `window.Friday.live` Ereignisse (Text, Links, Werkzeuge, Zustand); chat.js
   schreibt sie in den Verlauf, uebersicht.js zeigt die letzte Zeile. Der Kreis links
   von „Friday“ ist der Gesprächsknopf und zeigt den Zustand allein durch Bewegung. */
(() => {
  const { $, werkzeugText } = window.Friday;

  const IN_RATE = 16000;   // was Gemini erwartet
  const OUT_RATE = 24000;  // was Gemini liefert

  // Halb-Duplex, übernommen aus dem alten Friday (core/voice.py, TAIL_S = 0.6):
  // solange Friday spricht und kurz danach geht nichts vom Mikrofon raus.
  // Sonst hört das Mikrofon am Handy Fridays eigene Stimme, Gemini hält sie für
  // Eike und antwortet sich selbst ("Endlos-Selbstgespräch"). Mit
  // 2.5-native-audio (seminar-ki) fiel das nicht auf, mit 3.1-flash-live schon.
  const NACHLAUF_S = 0.6;

  const ui = {
    kreis: $('kreis'), halten: $('halten'), ptt: $('ptt'),
    status: $('gespraech-status'), statusText: $('gespraech-status-text'), server: $('zustand-server'),
  };

  /* ---------- Ereignis-Bus: das Gesicht von Sprechen für chat.js und uebersicht.js ---------- */

  const hoerer = {};
  function auf(name, fn) { (hoerer[name] = hoerer[name] || []).push(fn); }
  const melde = (name, ...args) => {
    for (const fn of hoerer[name] || []) {
      try { fn(...args); } catch (e) { console.error(`live.${name}`, e); }
    }
  };

  // Das Mikrofon bleibt über Gespräche hinweg erhalten, statt bei jedem Start neu
  // angefordert zu werden. iOS fragt sonst bei jedem getUserMedia erneut nach der
  // Erlaubnis (Eike, 20.09.2026: „jedes Mal neu"); mit einem noch lebenden Strom
  // fragt es nicht. Zwischen den Gesprächen sind die Spuren nur stummgeschaltet und
  // werden nach dieser Frist ganz freigegeben — sonst bliebe die Mikrofon-Anzeige
  // dauerhaft an.
  const MIKRO_BEHALTEN_S = 600;
  let mikroFrist = null;

  let ws = null, ctx = null, mic = null, strom = null;
  let laeuft = false, bereit = false, spieltAb = 0;
  let fridaySpricht = false;   // vom ersten Ton bis turn_end/interrupted
  let startet = false;         // zwischen Tipp und „bereit“: ein zweiter Tipp startet nichts Zweites
  const quellen = new Set();

  /* ---------- Kreis und Statuszeile ---------- */

  // Ein Zustand nach dem anderen: leer (aus), wach, hoert, denkt, spricht. Über classList,
  // nicht über className — sonst fiele die Klasse `an` (laufendes Gespräch) bei jedem
  // Zustandswechsel weg.
  function orb(zustand) {
    ui.kreis.classList.remove('wach', 'hoert', 'denkt', 'spricht');
    if (zustand) ui.kreis.classList.add(zustand);
  }
  // Im Gespräch zeigt die Kopfzeile den Gesprächsstatus statt des Serverzustands.
  function anzeigen() {
    ui.status.hidden = !laeuft;
    ui.server.hidden = laeuft;
  }
  const status = (t, z) => {
    ui.statusText.textContent = t;
    if (z !== undefined) orb(z);
    melde('status', t);
  };
  const hoertZu = () => status(ui.ptt.checked ? 'bereit — grünen Knopf halten' : 'hört zu',
                              ui.ptt.checked ? 'wach' : 'hoert');
  const hinweis = (t) => melde('hinweis', t);

  const senden = (o) => {
    if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(o)); return true; }
    return false;
  };

  /* ---------- Audio (unverändert aus seminar-ki) ---------- */

  // Lineare Neuabtastung: der Browser liefert 44,1 oder 48 kHz, Gemini will 16.
  function neuAbtasten(eingang, von, nach) {
    if (von === nach) return eingang;
    const verh = von / nach;
    const aus = new Float32Array(Math.floor(eingang.length / verh));
    for (let i = 0; i < aus.length; i++) {
      const pos = i * verh, idx = Math.floor(pos), frac = pos - idx;
      const a = eingang[idx] || 0;
      const b = eingang[idx + 1] !== undefined ? eingang[idx + 1] : a;
      aus[i] = a + (b - a) * frac;
    }
    return aus;
  }

  function zuBase64(f32) {
    const pcm = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    const bytes = new Uint8Array(pcm.buffer);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(bin);
  }

  function abspielen(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const pcm = new Int16Array(bytes.buffer);
    const f32 = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) f32[i] = pcm[i] / 32768;

    // Puffer mit 24 kHz anlegen und die Web-Audio-API umrechnen lassen —
    // zuverlässiger, als iOS einen AudioContext mit fester Rate aufzuzwingen.
    const buf = ctx.createBuffer(1, f32.length, OUT_RATE);
    buf.copyToChannel(f32, 0);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    // Lückenlos aneinanderreihen, sonst stottern oder überlappen die Stücke.
    const jetzt = ctx.currentTime;
    if (spieltAb < jetzt) spieltAb = jetzt + 0.04;
    src.start(spieltAb);
    spieltAb += buf.duration;
    quellen.add(src);
    src.onended = () => quellen.delete(src);
    fridaySpricht = true;
    status('spricht …', 'spricht');
  }

  // Wer Friday bewusst unterbrechen will, hält den Push-to-Talk-Knopf —
  // dann geht das Mikrofon auch während sie spricht durch.
  function mikroGesperrt() {
    if (ui.halten.classList.contains('held')) return false;
    return fridaySpricht || (ctx !== null && ctx.currentTime < spieltAb + NACHLAUF_S);
  }

  function stoppeWiedergabe() {
    for (const s of quellen) { try { s.stop(); } catch (_) {} }
    quellen.clear();
    spieltAb = 0;
    fridaySpricht = false;
  }

  /* ---------- Bedienung ---------- */

  function bedienbar(an) { melde('bereit', an); }

  function syncPtt() { ui.halten.hidden = !(laeuft && ui.ptt.checked); }

  function mikroLebt() {
    const spuren = strom ? strom.getAudioTracks() : [];
    return spuren.length > 0 && spuren.every((t) => t.readyState === 'live');
  }

  async function mikroHolen() {
    clearTimeout(mikroFrist);
    if (mikroLebt()) {                       // Erlaubnis gilt noch: nicht erneut fragen
      strom.getAudioTracks().forEach((t) => { t.enabled = true; });
      return;
    }
    strom = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  }

  function mikroFreigeben() {
    clearTimeout(mikroFrist);
    strom && strom.getTracks().forEach((t) => t.stop());
    strom = null;
  }

  function mikroParken() {
    if (!strom) return;
    strom.getAudioTracks().forEach((t) => { t.enabled = false; });
    clearTimeout(mikroFrist);
    mikroFrist = setTimeout(mikroFreigeben, MIKRO_BEHALTEN_S * 1000);
  }

  async function startenIntern() {
    try {
      status('Mikrofon …', 'denkt');
      await mikroHolen();
    } catch (e) {
      status('Mikrofon verweigert', '');
      hinweis(`Mikrofonzugriff fehlgeschlagen (${e.name}). Ohne HTTPS gibt iOS das Mikrofon nicht frei.`);
      return;
    }
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    await ctx.resume();  // iOS: nur direkt nach einer Berührung erlaubt
    await ctx.audioWorklet.addModule('/pcm-processor.js');

    ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/live`);
    ws.onopen = () => {
      // Kennung mitgeben: sonst rät Friday bei „Wo bist du?" die zuletzt gemeldete Oberfläche.
      const wer = window.Friday.kennung ? window.Friday.kennung() : '';
      senden({ type: 'start', kennung: wer });
      status('verbinde …', 'denkt');
    };
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      switch (m.type) {
        case 'ready':
          bereit = true;
          bedienbar(true);
          hoertZu();
          if (m.fortgesetzt) hinweis('Verbindung fortgesetzt — Friday weiß noch, worum es ging.');
          break;
        case 'audio': abspielen(m.data); break;
        case 'text': melde('text', m.role, m.text); break;
        // Die Wiedergabe kann nach turn_end noch laufen (gepuffert) — das
        // deckt der Nachlauf über spieltAb ab.
        case 'turn_end': fridaySpricht = false; melde('turn_end'); hoertZu(); break;
        case 'interrupted': stoppeWiedergabe(); melde('interrupted'); break;
        case 'link': melde('link', m.url, m.zustand, m.titel, m.grund); break;
        case 'oeffnen': melde('oeffnen', m.url, m.titel, m.auto); break;
        case 'pc': melde('pc', m.text); break;
        case 'bild': melde('bild', m.bilder); break;
        case 'werkzeug': melde('werkzeug', m.name); status(werkzeugText(m.name), 'denkt'); break;
        case 'quittung': melde('quittung', m.text, m.status); break;
        case 'korrektur': melde('korrektur', m.text); break;
        case 'reconnecting':
          bereit = false;
          bedienbar(false);
          stoppeWiedergabe();
          status(m.fortsetzung ? 'Verbindung wird fortgesetzt …' : 'Verbindung wird neu aufgebaut …', 'denkt');
          break;
        case 'fehler': status('Fehler', ''); hinweis(m.message); break;
      }
    };
    ws.onclose = () => { if (laeuft) beenden(); };
    ws.onerror = () => status('Verbindungsfehler', '');

    const quelle = ctx.createMediaStreamSource(strom);
    mic = new AudioWorkletNode(ctx, 'pcm-processor');
    mic.port.onmessage = (ev) => {
      if (!bereit) return;
      if (ui.ptt.checked && !ui.halten.classList.contains('held')) return;
      if (mikroGesperrt()) return;
      senden({ type: 'audio', data: zuBase64(neuAbtasten(ev.data, ctx.sampleRate, IN_RATE)) });
    };
    quelle.connect(mic);
    // Der Worklet braucht eine Senke; Nullverstärkung, damit nichts zurück in den Raum geht.
    const stumm = ctx.createGain();
    stumm.gain.value = 0;
    mic.connect(stumm).connect(ctx.destination);

    laeuft = true;
    ui.kreis.classList.add('an');
    ui.kreis.setAttribute('aria-pressed', 'true');
    ui.kreis.setAttribute('aria-label', 'Gespräch mit Friday beenden');
    anzeigen();
    syncPtt();
    melde('start');
  }

  async function starten() {
    if (startet || laeuft) return;
    startet = true;
    try { await startenIntern(); } finally { startet = false; }
  }

  function beenden() {
    laeuft = false;
    bereit = false;
    stoppeWiedergabe();
    senden({ type: 'stop' });
    try { ws && ws.close(); } catch (_) {}
    mikroParken();
    try { mic && mic.disconnect(); } catch (_) {}
    try { ctx && ctx.close(); } catch (_) {}
    ws = ctx = mic = null;
    ui.kreis.classList.remove('an');
    orb('');
    ui.kreis.setAttribute('aria-pressed', 'false');
    ui.kreis.setAttribute('aria-label', 'Gespräch mit Friday starten');
    ui.halten.classList.remove('held');
    bedienbar(false);
    syncPtt();
    anzeigen();
    status('bereit', '');
    melde('ende');
  }

  window.addEventListener('pagehide', mikroFreigeben);

  ui.kreis.addEventListener('click', () => { if (laeuft) beenden(); else starten(); });
  ui.ptt.addEventListener('change', () => { syncPtt(); if (bereit) hoertZu(); });

  // Push-to-Talk über einen eigenen Knopf — nie über den, der das Gespräch beendet.
  for (const ev of ['mousedown', 'touchstart']) {
    ui.halten.addEventListener(ev, (e) => { e.preventDefault(); if (laeuft) ui.halten.classList.add('held'); });
  }
  for (const ev of ['mouseup', 'touchend', 'touchcancel', 'mouseleave']) {
    ui.halten.addEventListener(ev, () => {
      const war = ui.halten.classList.contains('held');
      ui.halten.classList.remove('held');
      // Ende melden, sonst wartet Gemini auf eine Stille, die nie mehr gesendet wird.
      if (war && senden({ type: 'ptt_end' })) status('denkt nach …', 'denkt');
    });
  }

  /* ---------- Senden im laufenden Gespräch (für chat.js) ---------- */

  // Gibt true zurück, wenn es rausging. chat.js schreibt die eigene Zeile selbst in den Verlauf.
  function sendenText(text) {
    if (!bereit || !text) return false;
    stoppeWiedergabe();
    if (!senden({ type: 'text', text })) return false;
    status('denkt nach …', 'denkt');
    return true;
  }

  function sendenBild(bild, text) {
    if (!bereit) return false;
    stoppeWiedergabe();
    if (!senden({ type: 'bild', mime: bild.mime, data: bild.data, text: text || '' })) return false;
    status('schaut sich das an …', 'denkt');
    return true;
  }

  window.Friday.live = {
    auf, starten, beenden, sendenText, sendenBild,
    laeuft: () => laeuft, bereit: () => bereit, ptt: () => ui.ptt.checked,
  };

  anzeigen();
  bedienbar(false);
})();
