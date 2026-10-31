/* Freigaben-Seite: Konnektoren mit Schalter; antippen zeigt Details und Optionen.

   Default-Deny: alles ist aus, bis Eike es einschaltet. Das Passwort wird
   nur hingeschickt, nie zurückgeholt — der Server gibt es nicht heraus.
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
    b.addEventListener('click', (e) => { e.stopPropagation(); aktion(b); });
    return b;
  };
  const senden = async (url, daten, methode = 'POST') => {
    const r = await fetch(url, {
      method: methode, headers: { 'Content-Type': 'application/json' },
      body: daten === undefined ? undefined : JSON.stringify(daten),
    });
    return r.json().catch(() => ({}));
  };
  const zeit = (iso) => { try { return new Date(iso).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }); } catch (_) { return ''; } };
  const pfad = (k, rest = '') => `/api/konnektoren/${encodeURIComponent(k.id)}${rest}`;

  // Was die Knöpfe je Konnektor heißen.
  const TEXTE = {
    schulmanager: { vorschau: 'Heute ansehen', abgleich: 'Elternbriefe jetzt abgleichen' },
    iserv_mail: { vorschau: 'Neueste Mails', abgleich: 'Postfach jetzt abgleichen' },
    icloud_kalender: { vorschau: 'Diese Woche ansehen' },   // kein Abgleich: wird live gelesen
    spotify: { vorschau: 'Was läuft gerade?' },
    gvh: { vorschau: 'Nächste Abfahrten' },                // kein Abgleich: wird live gelesen
    studio: { vorschau: 'Wie läuft der Kanal?' },
    wetter: { vorschau: 'Wetter heute' },
    pc: { vorschau: 'Wie geht es dem PC?' },
    goodnotes: { vorschau: 'Stand ansehen', abgleich: 'Hefte jetzt abgleichen' },
    hausaufgaben: { vorschau: 'Bücher ansehen' },
  };

  // Wo Eike die Rückadresse eintragen muss, und wie der Knopf heißt.
  const OAUTH = {
    spotify: { wo: 'den Spotify-Entwicklerbereich', dienst: 'Spotify' },
    studio: { wo: 'die Google Cloud Console', dienst: 'YouTube' },
  };

  let offen = null;

  function stand(k) {
    if (!k.aktiv) return { text: 'aus', klasse: '' };
    if (k.ohneZugang) return { text: 'an', klasse: 'status-ok' };   // braucht keine Anmeldung
    if (!k.benutzer) return { text: k.zugangVon ? `an — erst ${k.zugangVon} einrichten` : 'an — Zugangsdaten fehlen', klasse: 'status-fehler' };
    if (k.pruefung && !k.pruefung.ok) return { text: 'an — Anmeldung fehlgeschlagen', klasse: 'status-fehler' };
    return { text: 'an', klasse: 'status-ok' };
  }

  function zugangsFormular(d, k) {
    d.append(el('div', 'klein', k.benutzer ? `Eingetragen für: ${k.benutzer}` : `Noch nicht eingetragen (${k.zugangArt}).`));
    const form = el('form', 'neu-fakt');
    // Beschriftung kommt vom Konnektor. Nur beim Schulmanager darf der
    // Passwortmanager mitreden — sonst böte er dort die IServ-Daten an.
    const iserv = k.id === 'schulmanager';
    const benutzer = el('input');
    benutzer.type = 'text'; benutzer.autocomplete = iserv ? 'username' : 'off';
    benutzer.placeholder = k.benutzerFeld || 'Benutzername';
    benutzer.maxLength = 200; benutzer.value = k.benutzer || ''; benutzer.autocapitalize = 'off';
    benutzer.spellcheck = false;
    const passwort = el('input');
    passwort.type = 'password'; passwort.autocomplete = iserv ? 'current-password' : 'new-password';
    passwort.placeholder = k.passwortFeld || 'Passwort'; passwort.maxLength = 500;
    const ok = el('button', '', k.benutzer ? 'Ändern' : 'Speichern'); ok.type = 'submit';
    form.append(benutzer, passwort, ok);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!benutzer.value.trim() || !passwort.value) return;
      ok.disabled = true;
      await senden(pfad(k, '/zugang'), { benutzer: benutzer.value.trim(), passwort: passwort.value });
      passwort.value = '';
      laden();
    });
    d.append(form);
  }

  function details(d, k) {
    const texte = TEXTE[k.id] || { vorschau: 'Vorschau' };
    d.append(el('div', 'text', k.beschreibung));

    d.append(el('h4', '', 'Was Friday damit kann'));
    const ul = el('ul');
    for (const w of k.werkzeuge) ul.append(el('li', 'klein', w.text));
    d.append(ul, el('p', 'klein', k.hinweis));

    d.append(el('h4', '', 'Zugang'));
    // Direkt dorthin, wo es die Zugangsdaten gibt — bei jedem Dienst.
    if (k.hilfeUrl) {
      const a = document.createElement('a');
      a.className = 'hilfe-link';
      a.href = k.hilfeUrl;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = `${k.hilfeText} ↗`;
      d.append(a);
    }
    if (k.ohneZugang) {
      d.append(el('div', 'klein', k.zugangArt));   // kein Formular: es gibt nichts einzutragen
    } else if (k.zugangVon) {
      d.append(el('div', 'klein', k.benutzer
        ? `Nutzt die Anmeldung vom ${k.zugangVon} (${k.benutzer}).`
        : `Nutzt die Anmeldung vom ${k.zugangVon} — dort zuerst eintragen.`));
    } else {
      zugangsFormular(d, k);
    }
    // PC: Schlüssel und Installationsbefehl für den Helfer (web/pc.js).
    if (k.id === 'pc' && window.FridayPc) window.FridayPc.einrichten(d, k, laden);
    // Standort der Familie: Personen für OwnTracks anlegen, Passwörter, pausieren, entfernen (web/personen.js).
    if (k.id === 'personenstandort' && window.FridayPersonen) window.FridayPersonen.einrichten(d, k, laden);

    // Spotify braucht zusätzlich eine einmalige Erlaubnis — und die Rückadresse
    // muss im Entwicklerbereich exakt so eingetragen sein.
    // Dienste mit einmaliger Erlaubnis (OAuth). Eine Tabelle statt zwei fast
    // gleicher Blöcke: Die Endpunkte heißen überall /api/<id>/start bzw. /trennen.
    const oauth = OAUTH[k.id];
    if (oauth) {
      d.append(el('div', 'klein', `Rückadresse für ${oauth.wo}: ${k.rueckadresse || ''}`));
      if (k.benutzer) {
        const reihe = el('div', 'knoepfe');
        reihe.append(knopf(k.verbunden ? 'Neu verbinden' : `Mit ${oauth.dienst} verbinden`, '', () => {
          window.location.href = `/api/${k.id}/start`;
        }));
        if (k.verbunden) {
          reihe.append(knopf('Verbindung trennen', 'weg', async () => {
            await senden(`/api/${k.id}/trennen`, {});
            laden();
          }));
        }
        d.append(reihe);
      }
    }

    // Geräte: Eike kreuzt an, worauf Friday abspielen darf. Ohne Häkchen wird
    // nie gestartet — er hat viele Geräte (Echo Dots), die dafür nicht taugen.
    if (k.id === 'spotify' && k.verbunden) {
      d.append(el('h4', '', 'Geräte, auf denen Friday abspielen darf'));
      const liste = el('div', 'liste');
      d.append(liste);
      (async () => {
        let daten;
        try {
          daten = await (await fetch('/api/spotify/geraete', { cache: 'no-store' })).json();
        } catch (_) { liste.append(el('p', 'leer-klein', 'Geräteliste nicht abrufbar.')); return; }
        const geraete = daten.geraete || [];
        if (!geraete.length) {
          liste.append(el('p', 'leer-klein', 'Spotify sieht gerade kein Gerät. Spiel kurz etwas ab, dann erscheinen sie hier.'));
          return;
        }
        for (const g of geraete) {
          const zeile = el('label', 'check klein');
          const cb = el('input');
          cb.type = 'checkbox'; cb.checked = !!g.erlaubt;
          cb.addEventListener('change', async () => {
            const gewaehlt = [...liste.querySelectorAll('input:checked')].map((x) => ({
              id: x.dataset.id, name: x.dataset.name,
            }));
            await senden('/api/spotify/geraete', { geraete: gewaehlt });
          });
          cb.dataset.id = g.id;
          cb.dataset.name = g.name;
          zeile.append(cb, el('span', '', `${g.name} (${g.typ})${g.aktiv ? ' — aktiv' : ''}`));
          liste.append(zeile);
        }
      })();
    }

    // WhatsApp: erst koppeln (QR), dann JEDEN Chat einzeln ankreuzen. Ohne
    // Häkchen liest Friday keinen — die Sperre sitzt im Server.
    if (k.id === 'whatsapp' && k.aktiv) {
      const kopf = el('div', 'liste');
      const chatliste = el('div', 'liste');
      d.append(kopf, chatliste);

      const chatsLaden = async () => {
        chatliste.replaceChildren();
        let daten;
        try {
          daten = await (await fetch('/api/whatsapp/chats', { cache: 'no-store' })).json();
        } catch (_) { chatliste.append(el('p', 'leer-klein', 'Chats nicht abrufbar.')); return; }
        const chats = daten.chats || [];
        if (!chats.length) { chatliste.append(el('p', 'leer-klein', 'Noch keine Chats da.')); return; }
        chatliste.append(el('h4', '', 'Chats, die Friday lesen darf'));
        chatliste.append(el('p', 'klein', 'Das zweite Häkchen erlaubt zusätzlich das Antworten. '
          + 'Friday liest dir jede Nachricht vorher im Wortlaut vor und schickt sie erst auf dein Ja.'));

        // Lesen und Antworten sind zwei getrennte Häkchen — hier und im Server.
        // Gespeichert wird die ganze Liste auf einmal, damit ein Klick nie einen
        // halben Stand hinterlässt.
        const sammeln = () => [...chatliste.querySelectorAll('.wa-zeile')]
          .filter((z) => z.querySelector('.wa-lesen').checked)
          .map((z) => ({ id: z.dataset.id, name: z.dataset.name,
                         senden: z.querySelector('.wa-senden').checked }));

        for (const c of chats) {
          const zeile = el('div', 'wa-zeile check-reihe');
          zeile.dataset.id = c.id; zeile.dataset.name = c.name;
          const lesen = el('label', 'check klein');
          const cbL = el('input', 'wa-lesen');
          cbL.type = 'checkbox'; cbL.checked = !!c.frei;
          lesen.append(cbL, el('span', '', `${c.name}${c.gruppe ? ' (Gruppe)' : ''}`));
          const antworten = el('label', 'check klein leise');
          const cbS = el('input', 'wa-senden');
          cbS.type = 'checkbox'; cbS.checked = !!c.senden;
          antworten.append(cbS, el('span', '', 'antworten'));
          const sichern = async () => { await senden('/api/whatsapp/chats', { chats: sammeln() }); };
          // Antworten ohne Lesen gibt es nicht — und ein weggenommenes Lesen
          // nimmt das Antworten mit, sonst bliebe ein Häkchen ohne Wirkung stehen.
          cbL.addEventListener('change', async () => {
            if (!cbL.checked) cbS.checked = false;
            await sichern();
          });
          cbS.addEventListener('change', async () => {
            if (cbS.checked) cbL.checked = true;
            await sichern();
          });
          zeile.append(lesen, antworten);
          chatliste.append(zeile);
        }
      };

      // WAHA dreht den QR-Code alle 20 Sekunden weiter und lässt die Reihe danach
      // ablaufen (Sitzung dann FAILED). Ein einmal geladenes Bild ist beim Scannen
      // also längst tot — WhatsApp meldete „Verbindung abgebrochen" (20.09.2026).
      // Deshalb: Code laufend erneuern, Stand mitlesen, abgelaufene Sitzung neu
      // starten — und aufhören, sobald verbunden.
      const QR_NEU_MS = 12000;
      let laeuft = false;

      const koppeln = async () => {
        if (laeuft) return;
        laeuft = true;
        kopf.replaceChildren();
        kopf.append(el('p', 'klein', 'In WhatsApp: Einstellungen → Verknüpfte Geräte → Gerät verknüpfen.'));
        const bild = el('img', 'qr');
        bild.alt = 'QR-Code zum Koppeln';
        const hinweis = el('p', 'leer-klein', 'QR-Code wird geholt …');
        kopf.append(bild, hinweis);

        while (laeuft) {
          let st = {};
          try {
            st = await (await fetch('/api/whatsapp/status', { cache: 'no-store' })).json();
          } catch (_) { /* gleich nochmal */ }
          if (st.zustand === 'WORKING') {          // fertig gekoppelt
            laeuft = false;
            kopf.replaceChildren(el('p', 'klein', `Verbindung: verbunden${st.nummer ? ` — ${st.nummer}` : ''}`));
            kopf.append(knopf('Verbindung trennen', 'weg', async () => {
              await senden('/api/whatsapp/trennen', {}); laden();
            }));
            chatsLaden();
            return;
          }
          if (st.zustand !== 'SCAN_QR_CODE') {     // abgelaufen oder noch nicht gestartet
            await senden('/api/whatsapp/start', {});
          }
          const frisch = `/api/whatsapp/qr?t=${Date.now()}`;
          await new Promise((fertig) => {
            const neu = new Image();
            neu.onload = () => { bild.src = frisch; hinweis.textContent = 'Jetzt scannen — der Code erneuert sich von selbst.'; fertig(); };
            neu.onerror = () => { hinweis.textContent = 'Warte auf einen neuen Code …'; fertig(); };
            neu.src = frisch;
          });
          await new Promise((w) => setTimeout(w, QR_NEU_MS));
        }
      };

      // Beim Verlassen der Seite aufhören: sonst liefe die Schleife weiter.
      window.addEventListener('hashchange', () => { laeuft = false; }, { once: true });

      (async () => {
        let st;
        try {
          st = await (await fetch('/api/whatsapp/status', { cache: 'no-store' })).json();
        } catch (_) { kopf.append(el('p', 'leer-klein', 'Der WhatsApp-Dienst antwortet nicht.')); return; }
        if (st.zustand === 'WORKING') {
          kopf.append(el('p', 'klein', `Verbindung: verbunden${st.nummer ? ` — ${st.nummer}` : ''}`));
          kopf.append(knopf('Verbindung trennen', 'weg', async () => {
            await senden('/api/whatsapp/trennen', {}); laden();
          }));
          chatsLaden();
          return;
        }
        kopf.append(el('p', 'klein', `Verbindung: ${st.text}`));
        kopf.append(knopf('Koppeln starten', '', koppeln));
      })();
    }

    // Snapchat: Das Handy am USB schläft nachts (23–7 Uhr), Aufträge warten bis morgens.
    // Mit dem Schalter arbeitet der Helfer rund um die Uhr — gilt sofort, ohne Neustart.
    if (k.id === 'snapchat' && k.aktiv) {
      d.append(el('h4', '', 'Nachts'));
      const zeile = el('label', 'check klein');
      const cb = el('input');
      cb.type = 'checkbox';
      const hinweis = el('span', '', 'Rund um die Uhr (auch nachts 23–7 Uhr)');
      zeile.append(cb, hinweis);
      d.append(zeile);
      (async () => {
        try {
          const w = await (await fetch('/api/snapchat/wachzeiten', { cache: 'no-store' })).json();
          cb.checked = !!w.rund_um_die_uhr;
        } catch (_) { hinweis.textContent = 'Bereitschaft nicht abrufbar.'; return; }
        cb.addEventListener('change', async () => {
          await senden('/api/snapchat/wachzeiten', { aktiv: cb.checked });
        });
      })();
    }

    const ausgabe = el('div', 'ausgabe');
    ausgabe.hidden = true;
    const zeigen = (text, klasse) => { ausgabe.hidden = false; ausgabe.className = `ausgabe ${klasse || ''}`; ausgabe.textContent = text; };
    const knoepfe = el('div', 'knoepfe');

    // Minecraft-Bot: Gemini oder DeepSeek. Umgestellt wird auf hphome (Profil und Schlüssel des Bots),
    // der Container startet dabei neu — deshalb ein eigener Knopf statt eines Schalters.
    if (k.id === 'minecraft_bot') {
      d.append(el('h4', '', 'Modell des Bots'));
      const jetzt = k.modell || '';
      d.append(el('p', 'klein', jetzt
        ? `Der Bot läuft gerade auf ${jetzt === 'deepseek' ? 'DeepSeek (deepseek-flash)' : 'Gemini (gemini-3.1-flash-lite)'}.`
        : 'Welches Modell der Bot gerade benutzt, weiß ich nicht — der Helfer auf hphome meldet sich nicht.'));
      d.append(el('p', 'klein', 'DeepSeek läuft über deinen vorhandenen Schlüssel und denkt vor jeder Aktion nach; '
        + 'die Antworten dauern damit etwas länger. Beim Umstellen startet der Bot neu und ist kurz aus dem Spiel.'));
      if (k.aktiv) {
        const reihe = el('div', 'knoepfe');
        const waehlen = (wert, text) => {
          if (wert === jetzt) return;        // das ist schon eingestellt
          reihe.append(knopf(text, '', async (b) => {
            b.disabled = true;
            zeigen('Der Bot wird umgestellt und neu gestartet — das dauert einen Moment …');
            let r = {};
            try { r = await senden(pfad(k, '/modell'), { modell: wert }); } catch (_) { /* unten gemeldet */ }
            zeigen(r.text || 'Keine Antwort.', r.ok ? 'status-ok' : 'status-fehler');
            if (r.ok) setTimeout(laden, 1500);
            b.disabled = false;
          }));
        };
        waehlen('deepseek', 'Auf DeepSeek umstellen');
        waehlen('gemini', 'Zurück auf Gemini');
        if (reihe.childElementCount) knoepfe.append(reihe);
      } else {
        d.append(el('p', 'klein', 'Erst den Minecraft-Bot einschalten, dann lässt sich das Modell umstellen.'));
      }
    }

    if (k.benutzer || k.ohneZugang) {
      // Ohne Anmeldung gibt es nichts zu prüfen — nur die Vorschau.
      if (k.benutzer) {
        knoepfe.append(knopf('Anmeldung prüfen', '', async (b) => {
          b.disabled = true; zeigen('Friday meldet sich an …');
          const r = await senden(pfad(k, '/pruefen'), {});
          zeigen(r.text || 'Keine Antwort.', r.ok ? 'status-ok' : 'status-fehler');
          b.disabled = false;
          setTimeout(laden, 1500);
        }));
      }
      knoepfe.append(
        knopf(texte.vorschau, '', async (b) => {
          b.disabled = true; zeigen('lädt …');
          try {
            const r = await (await fetch(pfad(k, '/vorschau'), { cache: 'no-store' })).json();
            zeigen(r.text || r.detail || 'Keine Antwort.');
          } catch (_) { zeigen('Keine Antwort.', 'status-fehler'); }
          b.disabled = false;
        }),
      );
      if (k.aktiv && texte.abgleich) {
        knoepfe.append(knopf(texte.abgleich, '', async (b) => {
          b.disabled = true;
          const r = await senden(pfad(k, '/abgleichen'), {});
          zeigen(r.text || r.detail || 'Keine Antwort.', r.ok ? 'status-ok' : 'status-fehler');
          b.disabled = false;
        }));
      }
      if (!k.zugangVon && !k.ohneZugang) {
        knoepfe.append(knopf('Zugang löschen', 'weg', async () => {
          if (!confirm(`Zugangsdaten löschen? Friday kommt dann nicht mehr an ${k.name}.`)) return;
          await senden(pfad(k, '/zugang'), undefined, 'DELETE');
          laden();
        }));
      }
    }
    if (k.schreibbar) {
      d.append(el('h4', '', 'Schreiben'));
      const zeile = el('div', 'kopfzeile');
      zeile.append(el('span', 'klein', k.schreibenText || 'Änderungen erlauben'));
      const schalter = el('label', 'schalter');
      const cb = el('input');
      cb.type = 'checkbox'; cb.checked = !!k.schreiben; cb.setAttribute('aria-label', 'Schreiben erlauben');
      // Konnektoren ohne Zugangsdaten (ÜSTRA-Garantie) haben keinen Benutzernamen — trotzdem schaltbar.
      cb.disabled = !k.aktiv || (!k.benutzer && !k.ohneZugang);
      schalter.append(cb, el('span', 'bahn'));
      cb.addEventListener('change', async () => {
        await senden(pfad(k, '/schreiben'), { aktiv: cb.checked });
        laden();
      });
      zeile.append(schalter);
      d.append(zeile);
    }
    if (k.stand) d.append(el('h4', '', 'Stand'), el('div', 'klein', k.stand));
    d.append(knoepfe, ausgabe);
    if (k.pruefung) {
      d.append(el('div', `klein ${k.pruefung.ok ? 'status-ok' : 'status-fehler'}`,
                  `Letzte Prüfung ${zeit(k.pruefung.zeit)}: ${k.pruefung.text}`));
    }
  }

  function zeigenListe(liste) {
    const ziel = $('k-liste');
    ziel.textContent = '';
    for (const k of liste) {
      const karte = el('div', 'eintrag konnektor klickbar');
      const kopf = el('div', 'kopfzeile');
      const name = el('div', 'k-name');
      const s = stand(k);
      name.append(el('span', 'titel', k.name), el('span', `klein ${s.klasse}`, s.text));

      const schalter = el('label', 'schalter');
      const cb = el('input');
      cb.type = 'checkbox'; cb.checked = k.aktiv; cb.setAttribute('aria-label', `${k.name} freigeben`);
      schalter.append(cb, el('span', 'bahn'));
      schalter.addEventListener('click', (e) => e.stopPropagation());
      cb.addEventListener('change', async () => {
        await senden(pfad(k), { aktiv: cb.checked });
        if (cb.checked && !k.benutzer) offen = k.id;   // gleich zeigen, was noch fehlt
        laden();
      });
      kopf.append(name, schalter);
      karte.append(kopf);

      if (offen === k.id) {
        const d = el('div', 'details');
        d.addEventListener('click', (e) => e.stopPropagation());
        details(d, k);
        karte.append(d);
      }
      karte.addEventListener('click', () => { offen = offen === k.id ? null : k.id; laden(); });
      ziel.append(karte);
    }
  }

  async function laden() {
    try {
      zeigenListe(await (await fetch('/api/konnektoren', { cache: 'no-store' })).json());
    } catch (_) {
      $('k-liste').textContent = 'Die Freigaben sind gerade nicht erreichbar.';
    }
  }

  const beiAnzeige = () => { if (location.hash === '#freigaben') laden(); };
  window.addEventListener('hashchange', beiAnzeige);
  beiAnzeige();
})();
