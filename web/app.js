/* Friday — gemeinsame Teile: Seitenwechsel, Zustand, Verlauf, Bilder, Eingabefeld. */

// „protokoll" war nur ein Platzhalter und ist auf Eikes Wunsch entfernt
// (14.09.2026). Das Backend-Modul kern/protokoll bleibt: Es führt die
// Gesprächsmitschriften, die Rückblick und Gedächtnis brauchen.
const SEITEN = ['uebersicht', 'chat', 'gedaechtnis', 'schule', 'studio', 'einstellungen', 'freigaben'];
const $ = (id) => document.getElementById(id);

/* Fridays Antworten enthalten Markdown: [Text](https://…) und **fett**. Roh
   angezeigt sah das am 14.09.2026 aus wie Quelltext, und Links waren nicht
   anklickbar.

   Bewusst KEIN Markdown-Renderer und nirgends innerHTML: Der Text kommt vom
   Modell und enthält oft Fremdinhalte (Webseiten, Mails, Briefe) — das wäre ein
   Einfallstor. Stattdessen wird der Text selbst zerlegt und aus Textknoten,
   <a> und <strong> zusammengesetzt. Als Adresse lässt das Muster nur http(s)
   zu, „javascript:" oder „data:" werden also gar nicht erst zu einem Link. */
const _LINK_MUSTER = /\[([^\]\n]{1,150})\]\((https?:\/\/[^\s)]{1,500})\)|(https?:\/\/[^\s<>"')\]]{1,500})/g;
const _FETT_MUSTER = /\*\*([^*\n]{1,300})\*\*/g;

function _textTeile(text) {
  const roh = String(text ?? '');
  const teile = [];
  let pos = 0;
  _LINK_MUSTER.lastIndex = 0;
  let t;
  while ((t = _LINK_MUSTER.exec(roh)) !== null) {
    if (t.index > pos) teile.push({ art: 'text', text: roh.slice(pos, t.index) });
    const url = t[2] || t[3];
    teile.push({ art: 'link', url, text: t[1] || url.replace(/^https?:\/\//, '') });
    pos = t.index + t[0].length;
  }
  if (pos < roh.length) teile.push({ art: 'text', text: roh.slice(pos) });
  return teile;
}

/* Ein Textstück: Formeln ($…$, $$…$$, \(…\), \[…\]) zeichnet web/mathe.js.
   Auch dort gilt: nur Textknoten und eigene Elemente — kein HTML aus
   Modell- oder Fremdtext. */
function _matheText(ziel, text) {
  const mathe = window.FridayMathe;
  const teile = mathe ? mathe.teile(text) : [{ art: 'text', text }];
  for (const teil of teile) {
    if (teil.art === 'mathe') mathe.zeichne(ziel, teil.text, teil.block);
    else ziel.appendChild(document.createTextNode(teil.text));
  }
}

function _fettAnhaengen(ziel, text) {
  let pos = 0;
  _FETT_MUSTER.lastIndex = 0;
  let t;
  while ((t = _FETT_MUSTER.exec(text)) !== null) {
    if (t.index > pos) _matheText(ziel, text.slice(pos, t.index));
    const b = document.createElement('strong');
    _matheText(b, t[1]);
    ziel.appendChild(b);
    pos = t.index + t[0].length;
  }
  if (pos < text.length) _matheText(ziel, text.slice(pos));
}

function _schreibeFliesstext(ziel, text) {
  for (const teil of _textTeile(text)) {
    if (teil.art === 'link') {
      const a = document.createElement('a');
      a.href = teil.url;                 // nur http(s) — siehe Muster oben
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = teil.text;
      ziel.appendChild(a);
    } else {
      _fettAnhaengen(ziel, teil.text);
    }
  }
}

function _schreibeText(ziel, text) {
  ziel.textContent = '';
  _schreibeFliesstext(ziel, String(text ?? ''));
}

/* Friday schickt eine Adresse zum Öffnen. Nur diese Schemata — dieselbe Liste
   wie im Kern (kern/werkzeuge/oeffnen.py), hier noch einmal geprüft: nie
   javascript:, data: oder file:. */
const OEFFNEN_ERLAUBT = /^(https?|tel|sms|mailto|facetime|maps|spotify|whatsapp|shortcuts):/i;

function oeffnenLink(url, titel) {
  if (!OEFFNEN_ERLAUBT.test(url || '')) return null;
  const a = document.createElement('a');
  a.href = url;
  a.className = 'oeffnen-link';
  if (/^https?:/i.test(url)) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
  a.textContent = `Öffnen: ${titel || url.replace(/^https?:\/\//, '').slice(0, 50)}`;
  return a;
}

/* Von selbst öffnen, ohne Fingertipp (Eikes Wunsch, 14.09.2026). Browser
   blockieren nur NEUE Fenster ohne Tipp — die Seite selbst umleiten dürfen sie.
   - Installierte App auf Handy/iPad: umleiten. iOS öffnet fremde Seiten dann
     eingeblendet über Friday („Fertig“ führt zurück), App-Links starten die App.
   - App-Links überall umleiten: die Seite bleibt, das System übernimmt.
   - Webseiten im Browser-Tab oder am PC NICHT umleiten — das hieße, Friday
     selbst zu verlassen (samt laufendem Gespräch). Dort nur ein neues Fenster
     versuchen, das oft blockiert wird.
   Der Knopf bleibt in jedem Fall stehen. Ob es aufging, sieht die Seite nicht. */
function versucheZuOeffnen(url) {
  if (!OEFFNEN_ERLAUBT.test(url || '')) return;
  const webseite = /^https?:/i.test(url);
  const installiert = navigator.standalone === true || window.matchMedia('(display-mode: standalone)').matches;
  const mobil = /iPhone|iPad|iPod|Android/.test(navigator.userAgent)
    || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);   // iPad meldet sich als Mac
  try {
    if (!webseite || (installiert && mobil)) window.location.assign(url);
    else window.open(url, '_blank', 'noopener');
  } catch (_) { /* dann eben per Knopf */ }
}

/* Ein Gesprächsverlauf: Zeilen von dir und Friday, dazu Bilder, Links, Hinweise.
   Transkripte kommen beim Sprechen stückweise — solange derselbe Sprecher
   weiterredet, wird dieselbe Zeile fortgeschrieben statt pro Silbe eine neue. */
class Verlauf {
  constructor(el) { this.el = el; this.offen = null; }

  _unten() { this.el.scrollTop = this.el.scrollHeight; }

  _neu(rolle) {
    const leer = this.el.querySelector('.leer');
    if (leer) leer.remove();
    const z = document.createElement('div');
    z.className = `zeile ${rolle}`;
    if (rolle === 'du' || rolle === 'friday') {
      const wer = document.createElement('span');
      wer.className = 'wer';
      wer.textContent = rolle === 'du' ? 'Du' : 'Friday';
      z.appendChild(wer);
    }
    const t = document.createElement('span');
    t.className = 't';
    z.appendChild(t);
    this.el.appendChild(z);
    return z;
  }

  text(rolle, text, stueckweise = false) {
    if (stueckweise && this.offen && this.offen.dataset.rolle === rolle) {
      // Sprachtranskript: wächst Stück für Stück und enthält kein Markdown
      // (die Sprachregeln verbieten Aufzählungen und Links). Bleibt roher Text.
      this.offen.querySelector('.t').textContent += text;
    } else {
      this._abschliessen();                 // vorherige Sprecher-Zeile fertig zeichnen
      const z = this._neu(rolle);
      z.dataset.rolle = rolle;
      if (stueckweise) z.querySelector('.t').textContent = text;
      else _schreibeText(z.querySelector('.t'), text);
      this.offen = stueckweise ? z : null;
    }
    this._unten();
  }

  // Gesprochenes wächst stückweise als Rohtext. Am Ende der Zeile Formeln
  // nachzeichnen — sonst bliebe eine vorgelesene Formel als „$…$“ stehen.
  _abschliessen() {
    const z = this.offen;
    if (z) {
      const t = z.querySelector('.t');
      if (t && window.FridayMathe && /[$\\]/.test(t.textContent)) _schreibeText(t, t.textContent);
    }
    this.offen = null;
  }

  schliessen() { this._abschliessen(); }

  bild(rolle, vorschau, text) {
    const z = this._neu(rolle);
    z.dataset.rolle = rolle;
    const img = new Image();
    img.src = vorschau;
    img.alt = 'gesendetes Bild';
    z.insertBefore(img, z.querySelector('.t'));
    z.querySelector('.t').textContent = text || '';
    this.offen = null;
    this._unten();
  }

  // Bilder, die Friday zeigt (aus dem Gedächtnis oder Kopien aus dem Netz). Nur Adressen von Fridays eigenen Routen —
  // Titel, Urheber und Quelle sind fremder Text und kommen nur per textContent auf die Seite.
  bilder(liste) {
    const eigen = /^\/api\/(?:bilder\/[0-9a-f]{32}|netzbild\/[0-9a-f]{16})$/;
    const bilder = (liste || []).filter((b) => b && eigen.test(b.url)).slice(0, 3);
    if (!bilder.length) return;
    const z = this._neu('friday');
    z.classList.add('bilder');
    z.dataset.rolle = 'friday';
    const t = z.querySelector('.t');
    for (const b of bilder) {
      const fig = document.createElement('figure');
      const a = document.createElement('a');
      a.href = b.url; a.target = '_blank'; a.rel = 'noopener';
      const img = new Image();
      img.src = b.url; img.alt = (b.titel || 'Bild').slice(0, 120); img.loading = 'lazy';
      a.appendChild(img);
      fig.appendChild(a);
      const cap = document.createElement('figcaption');
      cap.append(document.createTextNode([b.titel, b.quelle].filter(Boolean).join(' · ')));
      const lizenz = [b.urheber, b.lizenz].filter(Boolean).join(', ');
      if (lizenz) { const l = document.createElement('span'); l.textContent = ` — ${lizenz}`; cap.appendChild(l); }
      if (typeof b.link === 'string' && /^https:\/\//.test(b.link)) {
        const q = document.createElement('a');
        q.href = b.link; q.target = '_blank'; q.rel = 'noopener noreferrer'; q.textContent = ' Quelle';
        cap.appendChild(q);
      }
      fig.appendChild(cap);
      // „Auf die Übersicht“: legt eine Bildkarte (der Server prüft, dass es Fridays eigenes Bild ist).
      const karte = document.createElement('button');
      karte.type = 'button'; karte.className = 'bild-karte-knopf'; karte.textContent = 'Auf die Übersicht';
      karte.addEventListener('click', async () => {
        karte.disabled = true;
        try {
          const r = await fetch('/api/dashboard/bild', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: b.url, titel: b.titel || 'Bild', quelle: b.quelle || '', lizenz: b.lizenz || '', urheber: b.urheber || '', link: b.link || '' }) });
          karte.textContent = r.ok ? 'Liegt auf der Übersicht ✓' : 'Ging nicht';
          if (r.ok) window.Friday.uebersichtNeu?.();
          else karte.disabled = false;
        } catch (_) { karte.textContent = 'Ging nicht'; karte.disabled = false; }
      });
      fig.appendChild(karte);
      t.appendChild(fig);
    }
    this.offen = null;
    this._unten();
  }

  link(url, zustand, titel, grund) {
    let z = [...this.el.querySelectorAll('.zeile.link')]
      .find((e) => e.dataset.url === url && e.dataset.zustand === 'lese');
    if (!z) { z = this._neu('link'); z.dataset.url = url; }
    z.dataset.zustand = zustand;
    z.className = `zeile link ${zustand}`;
    const kurz = url.replace(/^https?:\/\//, '').slice(0, 60);
    z.querySelector('.t').textContent =
      zustand === 'lese' ? `Friday liest ${kurz} …`
      : zustand === 'gelesen' ? `Gelesen: ${titel || kurz}`
      : `Nicht lesbar: ${kurz} — ${grund || 'unbekannter Grund'}`;
    this.offen = null;
    this._unten();
  }

  oeffnen(url, titel, auto) {
    const a = oeffnenLink(url, titel);
    if (!a) { this.hinweis('Diese Adresse öffne ich nicht.'); return null; }
    const z = this._neu('oeffnen');
    z.querySelector('.t').appendChild(a);
    this.offen = null;
    this._unten();
    if (auto) versucheZuOeffnen(url);
    return a;
  }

  // Eine Datei zum Herunterladen (z. B. die Ausarbeitung eines Aufgabenblatts): nur Adressen auf Friday selbst.
  datei(url, titel) {
    if (!/^\/api\/[A-Za-z0-9_\/.-]+$/.test(url || '')) { this.hinweis('Diese Datei biete ich nicht an.'); return null; }
    const a = document.createElement('a');
    a.href = url;
    a.className = 'oeffnen-link';
    a.download = '';
    a.textContent = `Herunterladen: ${titel || 'Datei'}`;
    const z = this._neu('oeffnen');
    z.querySelector('.t').appendChild(a);
    this.offen = null;
    this._unten();
    return a;
  }

  hinweis(text) {
    const z = this._neu('system');
    z.querySelector('.t').textContent = text;
    this.offen = null;
    this._unten();
    return z;
  }

  // Fakt-Zeile des Kerns: was WIRKLICH passiert ist (ok / nicht / rueckfrage) — nicht, was Friday sagt.
  quittung(text, status) {
    const z = this._neu('system');
    z.classList.add('quittung', status === 'ok' ? 'ok' : status === 'rueckfrage' ? 'offen' : 'nicht');
    z.querySelector('.t').textContent = (status === 'ok' ? '✓ ' : status === 'rueckfrage' ? '… ' : '✗ ') + text;
    this.offen = null;
    this._unten();
    return z;
  }

  // Rote Karte: Friday hat etwas behauptet, was der Kern widerlegt.
  korrektur(text) {
    const z = this._neu('system');
    z.classList.add('quittung', 'korrektur');
    z.querySelector('.t').textContent = '⚠ ' + text;
    this.offen = null;
    this._unten();
    return z;
  }

  leeren(platzhalter) {
    this.el.textContent = '';
    this.offen = null;
    if (platzhalter) {
      const p = document.createElement('p');
      p.className = 'leer';
      p.textContent = platzhalter;
      this.el.appendChild(p);
    }
  }
}

/* Ein Handyfoto hat mehrere MB. An Gemini gehen höchstens 1280 px als JPEG —
   das reicht zum Erkennen und hält die Verbindung schlank. HEIC vom iPhone
   wandelt Safari beim Auswählen schon in JPEG um. */
function bildVerkleinern(datei, max = 1280) {
  return new Promise((fertig, fehler) => {
    const url = URL.createObjectURL(datei);
    const img = new Image();
    img.onload = () => {
      const f = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
      const c = document.createElement('canvas');
      c.width = Math.round(img.naturalWidth * f);
      c.height = Math.round(img.naturalHeight * f);
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      const vorschau = c.toDataURL('image/jpeg', 0.82);
      fertig({ mime: 'image/jpeg', data: vorschau.split(',')[1], vorschau });
    };
    img.onerror = () => { URL.revokeObjectURL(url); fehler(new Error('Bild nicht lesbar')); };
    img.src = url;
  });
}

/* Mitwachsendes Textfeld. Enter sendet am Rechner; am Handy bleibt Enter
   ein Zeilenumbruch, dort gibt es den Senden-Knopf. */
function eingabeFeld(feld, form) {
  const wachsen = () => { feld.style.height = 'auto'; feld.style.height = Math.min(feld.scrollHeight, 160) + 'px'; };
  feld.addEventListener('input', wachsen);
  const touch = window.matchMedia('(pointer: coarse)').matches;
  feld.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !touch) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  return { leeren() { feld.value = ''; wachsen(); } };
}

// Was Friday gerade nachsieht — sichtbar im Verlauf, damit nichts heimlich passiert.
const WERKZEUG_TEXT = {
  uhrzeit: 'Friday schaut auf die Uhr',
  dashboard_karte: 'Friday legt eine Karte auf die Übersicht',
  stundenplan: 'Friday schaut in den Stundenplan',
  hausaufgaben: 'Friday schaut nach den Hausaufgaben',
  elternbriefe: 'Friday sieht die Elternbriefe durch',
  elternbrief: 'Friday liest einen Elternbrief',
  schultermine: 'Friday schaut, was ansteht',
  mails: 'Friday sieht in die Post',
  mail: 'Friday liest eine Mail',
  mail_termine: 'Friday schaut, was die Post ankündigt',
  suchen: 'Friday sucht im Internet',
  nachrichten: 'Friday liest die Nachrichten',
  webseite: 'Friday liest eine Webseite',
  musik: 'Friday schaut, was läuft',
  musik_geraete: 'Friday schaut nach den Geräten',
  musik_pause: 'Friday pausiert die Musik',
  musik_weiter: 'Friday setzt die Musik fort',
  musik_naechster: 'Friday springt zum nächsten Titel',
  musik_vorheriger: 'Friday springt zurück',
  warteschlange: 'Friday füllt die Warteschlange',
  kalender: 'Friday schaut in den Kalender',
  kalender_suchen: 'Friday sucht einen Termin',
  erinnerung: 'Friday merkt sich eine Erinnerung',
  erinnerungen: 'Friday schaut nach offenen Erinnerungen',
  erinnerung_absagen: 'Friday sagt eine Erinnerung ab',
  termin_anlegen: 'Friday bereitet einen Termin vor',
  termin_bestaetigen: 'Friday trägt den Termin ein',
  person_ueber: 'Friday schaut in eure Verläufe',
  chat_suchen: 'Friday sucht in den Chats',
  pc_status: 'Friday schaut nach dem PC',
  pc_programme: 'Friday sieht die Programme am PC durch',
  pc_fenster: 'Friday schaut, was am PC offen ist',
  pc_dateien: 'Friday sieht in die Ordner am PC',
  pc_bestaetigungen: 'Friday schaut, was am PC passiert ist',
  pc_starten: 'Friday startet ein Programm am PC',
  pc_fenster_wechseln: 'Friday holt ein Fenster nach vorn',
  pc_fenster_schliessen: 'Friday will ein Fenster schließen',
  pc_datei_oeffnen: 'Friday öffnet etwas am PC',
  pc_verschieben: 'Friday will am PC verschieben',
  pc_kopieren: 'Friday kopiert am PC',
  pc_umbenennen: 'Friday will am PC umbenennen',
  pc_ordner_anlegen: 'Friday legt einen Ordner an',
  pc_loeschen: 'Friday will etwas in den Papierkorb legen',
  pc_system: 'Friday bedient den PC',
  pc_meldung: 'Friday zeigt eine Nachricht am PC',
  pc_webseite: 'Friday öffnet eine Seite am PC',
  browser_oeffnen: 'Friday öffnet eine Seite im Friday-Browser',
  browser_lesen: 'Friday liest die Seite',
  browser_klicken: 'Friday klickt im Browser',
  browser_tippen: 'Friday tippt im Browser',
  browser_zurueck: 'Friday geht eine Seite zurück',
  browser_scrollen: 'Friday scrollt',
  browser_schliessen: 'Friday schließt den Browser',
};
const werkzeugText = (name) => WERKZEUG_TEXT[name] || `Friday nutzt ${name}`;

window.Friday = { $, Verlauf, bildVerkleinern, eingabeFeld, werkzeugText };

/* ---------- Seitenwechsel ---------- */

// „sprechen“ war bis zum 24.09.2026 die Startseite: alte Lesezeichen und Push-Links
// (`/#sprechen`) landen auf der Übersicht, die ihren Platz übernommen hat.
const ALIAS = { sprechen: 'uebersicht' };

// Tiefe Links: `#seite/reiter` (z. B. `#studio/kontakte`) öffnet die Seite und tippt den Reiter an.
function reiterOeffnen() {
  const [seite, reiter] = location.hash.slice(1).split('/');
  if (!reiter || !SEITEN.includes(seite)) return;
  const knopf = document.querySelector(`#${seite} .reiter button[data-reiter="${reiter}"]`);
  if (knopf && !knopf.classList.contains('aktiv')) knopf.click();
}

function zeige() {
  const roh = location.hash.slice(1).split('/')[0];
  const wunsch = ALIAS[roh] || roh;
  const ziel = SEITEN.includes(wunsch) ? wunsch : 'uebersicht';
  for (const id of SEITEN) $(id).hidden = id !== ziel;
  for (const a of document.querySelectorAll('.leiste a')) {
    // Freigaben ist ein Untermenü von „Mehr“.
    a.classList.toggle('aktiv', a.getAttribute('href') === '#' + (ziel === 'freigaben' ? 'einstellungen' : ziel));
  }
}
window.addEventListener('hashchange', () => { zeige(); reiterOeffnen(); });
zeige();
// Die Reiter-Knöpfe hören erst, wenn alle Skripte geladen sind.
document.addEventListener('DOMContentLoaded', reiterOeffnen);

/* ---------- Zustand ---------- */

const alsApp = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

async function pruefe(nochmal = true) {
  try {
    const d = await (await fetch('/api/health', { cache: 'no-store' })).json();
    $('punkt').className = 'punkt gut';
    // Der Stand der Dateien: Steht hier nicht die Zeit der letzten Änderung, zeigt die App noch einen alten.
    const stand = document.querySelector('meta[name="build"]');
    $('zustand').textContent = stand ? `verbunden · Stand ${stand.content}` : 'verbunden';
  } catch (_) {
    // Direkt nach der Rückkehr in die App ist das Netz oft noch nicht wach: einmal kurz später noch versuchen.
    if (nochmal) { setTimeout(() => pruefe(false), 2000); return; }
    $('punkt').className = 'punkt weg';
    $('zustand').textContent = 'keine Verbindung';
  }
  $('w-https').textContent = window.isSecureContext ? 'ja' : 'NEIN — Mikrofon bleibt gesperrt';
  $('w-app').textContent = alsApp ? 'ja' : 'nein';
}
pruefe();
setInterval(() => pruefe(), 30000);
// Beim Zurückkehren in die App sofort neu prüfen, nicht erst beim nächsten 30-s-Takt (sonst bleibt „keine Verbindung“ stehen).
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') pruefe(); });
window.addEventListener('pageshow', () => pruefe());
window.addEventListener('online', () => pruefe());

/* Einstellungen: welche Gemini-Schlüssel gerade am Limit sind und wofür sie dienen.
   Google meldet ein Limit nur als Antwort auf eine echte Anfrage — der Server merkt
   es sich, hier wird nur gezeigt. Geladen wird nur bei geöffnetem Menü. */
async function schluesselZeigen() {
  const menue = $('s-menue');
  if (location.hash !== '#einstellungen' || document.visibilityState !== 'visible') return;
  let d;
  try { d = await (await fetch('/api/schluessel', { cache: 'no-store' })).json(); } catch (_) { return; }
  $('s-kurz').textContent = d.am_limit ? `· ${d.am_limit} am Limit` : '· alle frei';
  $('s-pool-automatik').checked = !!d.pool?.automatik;
  const teile = [];
  if (d.pool) {
    teile.push(`${d.pool.anzahl} im Pool`);
    if (!d.pool.helfer) teile.push('Helfer auf hphome läuft nicht');
    if (d.pool.fehler) teile.push(d.pool.fehler);
  }
  $('s-pool-info').textContent = teile.join(' · ');
  if (!menue.open) return;

  const ziel = $('s-liste');
  ziel.textContent = '';
  for (const z of d.zwecke || []) {
    const zeile = document.createElement('div');
    zeile.className = 'eintrag';
    const kopf = document.createElement('div');
    kopf.className = 'kopfzeile';
    const name = document.createElement('b');
    name.textContent = z.zweck;
    const stand = document.createElement('span');
    stand.className = z.verfuegbar ? 'leise' : 'warn';
    stand.textContent = z.verfuegbar ? 'verfügbar' : 'am Limit';
    kopf.append(name, stand);
    const detail = document.createElement('div');
    detail.className = 'leise';
    detail.textContent = z.schluessel.join(' · ') || 'kein Schlüssel eingerichtet';
    zeile.append(kopf, detail);
    ziel.append(zeile);
  }

  const zielSchluessel = $('s-liste-schluessel');
  zielSchluessel.textContent = '';
  for (const s of d.schluessel) {
    const zeile = document.createElement('div');
    zeile.className = 'eintrag';
    const kopf = document.createElement('div');
    kopf.className = 'kopfzeile';
    const name = document.createElement('b');
    name.textContent = s.name;
    const stand = document.createElement('span');
    stand.className = s.limits.length ? 'warn' : 'leise';
    stand.textContent = s.limits.length ? 'am Limit' : 'frei';
    kopf.append(name, stand);
    const detail = document.createElement('div');
    detail.className = 'leise';
    const wofuer = s.zwecke.map((z) => z.zweck + (z.von > 1 ? ` (${z.rang}. von ${z.von})` : ''));
    detail.textContent = wofuer.join(' · ') || 'nicht in Gebrauch';
    zeile.append(kopf, detail);
    for (const l of s.limits) {
      const p = document.createElement('div');
      p.className = 'warn';
      p.textContent = `${l.modell}: ${l.art === 'tag' ? 'Tageslimit, wieder ab' : 'zu viele Anfragen, wieder ab'} ${l.bis} Uhr`;
      zeile.append(p);
    }
    zielSchluessel.append(zeile);
  }
}
$('s-menue').addEventListener('toggle', schluesselZeigen);
window.addEventListener('hashchange', schluesselZeigen);
document.addEventListener('visibilitychange', schluesselZeigen);
setInterval(schluesselZeigen, 30000);
schluesselZeigen();

$('s-pool-automatik').addEventListener('change', async (ev) => {
  await fetch('/api/schluessel/automatik', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                             body: JSON.stringify({ an: ev.target.checked }) });
  schluesselZeigen();
});
$('s-pool-anlegen').addEventListener('click', async (ev) => {
  ev.target.disabled = true;
  let r;
  try { r = await (await fetch('/api/schluessel/anlegen', { method: 'POST' })).json(); }
  catch (_) { r = { text: 'Friday ist gerade nicht erreichbar.' }; }
  const m = $('s-pool-meldung');
  m.hidden = false;
  m.textContent = r.text || '';
  ev.target.disabled = false;
  schluesselZeigen();
});

/* Einstellungen: welche Hintergrundläufe laufen oder abgestürzt sind. Nur beim
   geöffneten Menü geladen, wie bei der Schlüssel-Übersicht. */
async function prozesseZeigen() {
  const menue = $('p-menue');
  if (location.hash !== '#einstellungen' || document.visibilityState !== 'visible') return;
  let d;
  try { d = await (await fetch('/api/prozesse', { cache: 'no-store' })).json(); } catch (_) { return; }
  const abgestuerzt = d.prozesse.filter((p) => !p.laeuft).length;
  $('p-kurz').textContent = abgestuerzt ? `· ${abgestuerzt} abgestürzt` : '· alle laufen';
  if (!menue.open) return;
  const ziel = $('p-liste');
  ziel.textContent = '';
  const seitDauer = (sek) => {
    const min = Math.max(0, Math.round((Date.now() / 1000 - sek) / 60));
    return min < 60 ? `seit ${min} min` : `seit ${Math.floor(min / 60)} h ${min % 60} min`;
  };
  for (const p of d.prozesse) {
    const zeile = document.createElement('div');
    zeile.className = 'eintrag';
    const kopf = document.createElement('div');
    kopf.className = 'kopfzeile';
    const name = document.createElement('b');
    name.textContent = p.anzeige;
    const stand = document.createElement('span');
    stand.className = p.laeuft ? 'leise' : 'warn';
    stand.textContent = p.laeuft ? 'läuft' : 'abgestürzt';
    kopf.append(name, stand);
    const detail = document.createElement('div');
    detail.className = 'leise';
    detail.textContent = p.laeuft ? seitDauer(p.seit) : p.fehler;
    zeile.append(kopf, detail);
    ziel.append(zeile);
  }
}
$('p-menue').addEventListener('toggle', prozesseZeigen);
window.addEventListener('hashchange', prozesseZeigen);
document.addEventListener('visibilitychange', prozesseZeigen);
setInterval(prozesseZeigen, 30000);
prozesseZeigen();

// Der Service Worker muss von Anfang an da sein: Web Push (Phase 4) läuft nur über ihn.
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

// Installationshinweis nur auf iOS, nur im Browser, nur bis einmal weggeklickt.
const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
            (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
let weggeklickt = false;
try { weggeklickt = localStorage.getItem('friday.hinweis') === 'zu'; } catch (_) {}
if (ios && !alsApp && !weggeklickt) $('installieren').hidden = false;
$('hinweis-zu').addEventListener('click', () => {
  $('installieren').hidden = true;
  try { localStorage.setItem('friday.hinweis', 'zu'); } catch (_) {}
});

// Bildung: zwischen „Schule“ und „Uni“ wechseln (nur Anzeige; die Schul-Reiter bleiben unberührt).
for (const b of document.querySelectorAll('.bildung-kopf button')) {
  b.addEventListener('click', () => {
    const uni = b.dataset.bildung === 'uni';
    document.getElementById('bildung-schule').hidden = uni;
    document.getElementById('bildung-uni').hidden = !uni;
    if (uni) {
      document.getElementById('uni-datum').textContent = new Date().toLocaleDateString('de-DE',
        { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
    }
    for (const x of document.querySelectorAll('.bildung-kopf button')) x.classList.toggle('aktiv', x === b);
  });
}
