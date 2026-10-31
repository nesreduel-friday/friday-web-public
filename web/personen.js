/* Personen für OwnTracks verwalten — Freigaben → Standort der Familie.

   Jede Person bekommt ein eigenes Passwort und richtet OwnTracks auf dem Handy ein (ein Tipp auf den Link genügt).
   Das Passwort zeigt der Server nur beim Anlegen oder Erneuern; hier wird es nur angezeigt, nie gespeichert.
   Koordinaten kommen nie hierher: Die Liste trägt Name, Zustand, Alter und Genauigkeit. Alles per textContent. */
(() => {
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
    try {
      const r = await fetch(url, { method: methode, headers: { 'Content-Type': 'application/json' },
                                   body: methode === 'DELETE' ? undefined : JSON.stringify(daten ?? {}) });
      const d = await r.json().catch(() => ({}));
      return { ok: r.ok && d.ok !== false, text: d.text || d.detail || (r.ok ? '' : `Fehler ${r.status}`), ...d };
    } catch (_) { return { ok: false, text: 'Friday ist nicht erreichbar.' }; }
  };

  const vor = (s) => {
    if (s === null || s === undefined) return '';
    if (s < 90) return 'gerade eben';
    const min = Math.round(s / 60);
    if (min < 90) return `vor ${min} min`;
    const std = Math.round(min / 60);
    return std < 48 ? `vor ${std} Std` : `vor ${Math.round(std / 24)} Tagen`;
  };

  function status(p) {
    if (p.zustand === 'pausiert') return 'Teilen pausiert — es wird nichts herausgegeben.';
    if (p.zustand === 'noch_nie') return 'Hat noch nichts gemeldet — OwnTracks ist noch nicht eingerichtet (oder Tailscale aus).';
    const teile = [`zuletzt ${vor(p.alter_s)}`];
    if (typeof p.genauigkeit_m === 'number') teile.push(`Genauigkeit rund ${p.genauigkeit_m < 950 ? `${Math.round(p.genauigkeit_m)} m` : `${(p.genauigkeit_m / 1000).toFixed(1)} km`}`);
    if (typeof p.akku_prozent === 'number') teile.push(`Akku ${p.akku_prozent} %`);
    return teile.join(' · ');
  }

  // Das Passwort gibt es nur JETZT: mit Kopierknöpfen und dem Ein-Tipp-Link für OwnTracks.
  function einrichtungZeigen(ziel, e) {
    ziel.replaceChildren();
    const box = el('div', 'eintrag pers-einrichtung');
    box.append(el('div', 'titel', `OwnTracks einrichten${e.name ? ` — ${e.name}` : ''}`),
               el('div', 'pers-warn', 'Das Passwort wird nur jetzt angezeigt. Verloren? Dann unter der Person „Neues Passwort“ wählen.'));
    const schritte = el('ol', 'pers-schritte');
    for (const t of ['Auf dem Handy die App „OwnTracks“ installieren und Tailscale einschalten (das Handy muss im Tailnet sein).',
                     'ZUERST in OwnTracks: Einstellungen (Settings) → Remote Control → „Allow external configuration“ einschalten. Ohne das lehnt die App den Link ab '
                     + '(Meldung „URI or file configuration not allowed“) — das ist eine Sicherheitssperre von OwnTracks.',
                     'Dann hier auf „In OwnTracks einrichten“ tippen und die Übernahme bestätigen. Oder die Werte per Hand eintragen: Modus HTTP.',
                     'Danach den Schalter „Allow external configuration“ wieder ausschalten.',
                     'In den iPhone-Einstellungen für OwnTracks den Standortzugriff auf „Immer“ stellen, damit es auch im Hintergrund meldet.',
                     'Hier in Freigaben „Standort der Familie“ einschalten (falls noch aus).']) schritte.append(el('li', '', t));
    box.append(schritte);
    if (typeof e.link === 'string' && e.link.startsWith('owntracks:///config?inline=')) {
      const a = el('a', 'pers-link', 'In OwnTracks einrichten');
      a.href = e.link;
      box.append(a);
    }
    const werte = el('div', 'pers-werte');
    for (const [name, wert] of [['URL', e.url], ['Benutzer', e.benutzer], ['Passwort', e.passwort]]) {
      const z = el('div', 'pers-wert');
      z.append(el('span', 'pers-name', name), el('code', 'pers-code', wert || ''),
        knopf('Kopieren', '', async (b) => {
          try { await navigator.clipboard.writeText(wert || ''); b.textContent = 'Kopiert'; } catch (_) { b.textContent = 'Bitte markieren'; }
        }));
      werte.append(z);
    }
    box.append(werte, knopf('Schließen', '', () => ziel.replaceChildren()));
    ziel.append(box);
    box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function einrichten(d, k, neuLaden) {
    d.append(el('h4', '', 'Personen (OwnTracks)'));
    d.append(el('p', 'klein', 'Jede Person meldet ihren Standort selbst per OwnTracks-App und kann jederzeit abschalten. Friday sieht nur den LETZTEN Punkt je Person. '
      + 'Hier legst du Personen an, gibst Passwörter aus, pausierst und entfernst sie. Für deine eigene Route brauchst du eine Person mit der Kennung „eike“.'));
    const liste = el('div', 'liste pers-liste');
    const ausgabe = el('div');
    const meldung = el('div', 'klein');
    d.append(liste, meldung);

    async function laden() {
      let r;
      try { r = await (await fetch('/api/personenstandort/personen', { cache: 'no-store' })).json(); } catch (_) { r = { ok: false, text: 'Friday ist nicht erreichbar.' }; }
      liste.replaceChildren();
      if (!r.ok) { liste.append(el('p', 'leer-klein', r.text || 'Die Personen lassen sich gerade nicht laden.')); return; }
      if (!r.personen.length) liste.append(el('p', 'leer-klein', 'Noch niemand angelegt.'));
      for (const p of r.personen) liste.append(person(p));
    }

    function person(p) {
      const e = el('div', 'eintrag');
      e.append(el('div', 'titel', `${p.name} (${p.kennung})`), el('div', 'klein', status(p)));
      const reihe = el('div', 'knoepfe');
      reihe.append(
        knopf('Neues Passwort / einrichten', '', async (b) => {
          b.disabled = true;
          const x = await senden(`/api/personenstandort/personen/${encodeURIComponent(p.kennung)}/token`);
          b.disabled = false;
          if (!x.ok) { meldung.textContent = x.text; return; }
          meldung.textContent = '';
          einrichtungZeigen(ausgabe, { ...x.einrichtung, name: p.name });
        }),
        knopf(p.zustand === 'pausiert' ? 'Weiter melden' : 'Pausieren', '', async (b) => {
          b.disabled = true;
          const x = await senden(`/api/personenstandort/personen/${encodeURIComponent(p.kennung)}/pause`, { pause: p.zustand !== 'pausiert' });
          meldung.textContent = x.ok ? '' : x.text;
          await laden();
        }),
        knopf('Entfernen', 'weg', (b) => {
          b.hidden = true;
          const frage = el('div', 'eintrag');
          frage.append(el('div', 'titel', `${p.name} endgültig entfernen?`),
                       el('div', 'klein', 'Person und der letzte Standort sind danach weg. Wiederherstellen geht nicht.'));
          const r2 = el('div', 'knoepfe');
          r2.append(knopf('Endgültig entfernen', 'weg', async (b2) => {
            b2.disabled = true;
            const x = await senden(`/api/personenstandort/personen/${encodeURIComponent(p.kennung)}`, undefined, 'DELETE');
            meldung.textContent = x.ok ? '' : x.text;
            await laden();
          }), knopf('Abbrechen', '', () => { frage.remove(); b.hidden = false; }));
          frage.append(r2);
          e.append(frage);
        }));
      e.append(reihe);
      return e;
    }

    // Person hinzufügen
    const form = el('form', 'pc-eingabe pers-neu');
    const name = el('input'); name.type = 'text'; name.placeholder = 'Name, z. B. Eike'; name.maxLength = 40; name.autocapitalize = 'words';
    const kennung = el('input'); kennung.type = 'text'; kennung.placeholder = 'Kennung (optional, z. B. eike)'; kennung.maxLength = 24;
    kennung.autocapitalize = 'off'; kennung.spellcheck = false;
    const ok = el('button', '', 'Person hinzufügen'); ok.type = 'submit';
    form.append(name, kennung, ok);
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (!name.value.trim()) { meldung.textContent = 'Bitte einen Namen eintragen.'; return; }
      ok.disabled = true;
      const x = await senden('/api/personenstandort/personen', { name: name.value.trim(), kennung: kennung.value.trim() });
      ok.disabled = false;
      if (!x.ok) { meldung.textContent = x.text; return; }
      meldung.textContent = '';
      name.value = ''; kennung.value = '';
      einrichtungZeigen(ausgabe, x.einrichtung);
      await laden();
    });
    d.append(form, ausgabe);
    laden();
  }

  window.FridayPersonen = { einrichten };
})();
