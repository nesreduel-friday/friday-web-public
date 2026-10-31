/* Gedächtnis → Verläufe: WhatsApp-Exporte hochladen, Personen zuordnen, auswerten.

   Alles per textContent, nie als HTML — die Namen kommen aus fremden Chats. */
(() => {
  const { $ } = window.Friday;

  const el = (tag, klasse, text) => {
    const e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };
  const knopf = (text, klasse, aktion) => {
    const b = el('button', klasse, text); b.type = 'button';
    b.addEventListener('click', (e) => { e.stopPropagation(); aktion(b); });
    return b;
  };
  const senden = async (url, daten, methode = 'POST') => {
    const r = await fetch(url, { method: methode, headers: { 'Content-Type': 'application/json' },
      body: daten === undefined ? undefined : JSON.stringify(daten) });
    return r.json().catch(() => ({}));
  };
  const holen = async (url) => (await fetch(url, { cache: 'no-store' })).json();

  let daten = null;

  /* ---------- Hochladen ---------- */
  $('v-datei').addEventListener('change', async (e) => {
    const dateien = [...e.target.files];
    e.target.value = '';
    if (!dateien.length) return;
    const status = $('v-status');
    status.hidden = false; status.className = 'ausgabe';
    let n = 0, nachrichten = 0, zuordnung = null;
    for (const datei of dateien) {
      n += 1;
      status.textContent = `Lade ${n}/${dateien.length}: ${datei.name} …`;
      const fd = new FormData();
      fd.append('datei', datei);
      try {
        const r = await (await fetch('/api/chats/hochladen', { method: 'POST', body: fd })).json();
        if (r.ok) { nachrichten += r.nachrichten || 0; zuordnung = r.zuordnung || zuordnung; }
        else status.textContent = r.detail || 'Fehler beim Einlesen.';
      } catch (_) { status.textContent = 'Upload fehlgeschlagen.'; }
    }
    status.className = 'ausgabe status-ok';
    let text = `Fertig: ${nachrichten.toLocaleString('de-DE')} Nachrichten eingelesen.`;
    if (zuordnung) {
      text += ` ${zuordnung.zugeordnet} Absender zugeordnet`;
      if (zuordnung.neu) text += `, ${zuordnung.neu} neue Personen angelegt`;
      if (zuordnung.ich) text += `, dich erkannt als „${zuordnung.ich}“`;
      text += zuordnung.offen ? `. Bei ${zuordnung.offen} bin ich mir nicht sicher — die unten bitte kurz zuordnen.`
                              : '. Nichts weiter zu tun.';
    }
    status.textContent = text;
    laden();
  });

  /* ---------- Absender zuordnen ---------- */
  function offen(liste) {
    $('v-offen-block').hidden = liste.length === 0;
    const ziel = $('v-offen');
    ziel.textContent = '';
    if (liste.length) {
      ziel.append(knopf('Rest automatisch zuordnen', '', async (b) => {
        b.disabled = true;
        await senden('/api/chats/zuordnen/automatisch', {});
        laden();
      }));
    }
    for (const o of liste) {
      const e = el('div', 'eintrag');
      const kopf = el('div', 'kopfzeile');
      kopf.append(el('span', 'titel', o.absender),
                  el('span', 'klein', `${o.anzahl.toLocaleString('de-DE')} Nachrichten · ${o.chats} Chat(s)`));
      e.append(kopf);

      const reihe = el('div', 'knoepfe');
      const wahl = el('select', 'auswahl');
      wahl.append(new Option('— zuordnen zu …', ''));
      for (const p of daten.personen) wahl.append(new Option(p.ich ? `${p.name} (ich)` : p.name, p.id));
      wahl.append(new Option('+ neue Person', 'neu'), new Option('+ das bin ich', 'ich'));
      wahl.addEventListener('change', async () => {
        const wert = wahl.value;
        if (!wert) return;
        let person;
        if (wert === 'neu' || wert === 'ich') {
          const name = prompt(wert === 'ich' ? 'Dein Name in den Chats:' : `Wer ist „${o.absender}“?`, o.absender);
          if (!name) { wahl.value = ''; return; }
          const rolle = wert === 'ich' ? '' : (prompt('Wie steht diese Person zu dir? (z.B. Mutter, Freund) — optional', '') || '');
          const r = await senden('/api/chats/personen', { name: name.trim(), rolle: rolle.trim(), ich: wert === 'ich' });
          person = r.id;
        } else {
          person = Number(wert);
        }
        await senden('/api/chats/zuordnen', { absender: o.absender, person });
        laden();
      });
      reihe.append(wahl);
      e.append(reihe);
      ziel.append(e);
    }
  }

  function personen(liste) {
    const echte = liste.filter((p) => !p.ich);
    $('v-personen-block').hidden = liste.length === 0;
    const ziel = $('v-personen');
    ziel.textContent = '';
    for (const p of liste) {
      const e = el('div', 'eintrag');
      const kopf = el('div', 'kopfzeile');
      kopf.append(el('span', 'titel', p.ich ? `${p.name} (du)` : p.name + (p.rolle ? ` · ${p.rolle}` : '')),
                  el('span', 'klein', `${p.aliase} Name(n)${p.profil ? ' · ausgewertet' : ''}`));
      e.append(kopf);
      ziel.append(e);
    }
    if (!echte.length) ziel.append(el('p', 'leer-klein', 'Noch keine Personen angelegt.'));
  }

  function chats(liste) {
    $('v-chats-block').hidden = liste.length === 0;
    const ziel = $('v-chats');
    ziel.textContent = '';
    for (const c of liste) {
      const e = el('div', 'eintrag');
      const kopf = el('div', 'kopfzeile');
      kopf.append(el('span', 'titel', c.partner),
                  el('span', 'klein', `${c.anzahl.toLocaleString('de-DE')} · ${c.von || '?'} – ${c.bis || '?'}`));
      const label = el('label', 'check klein');
      const cb = el('input'); cb.type = 'checkbox'; cb.checked = c.auswerten;
      cb.addEventListener('change', () => senden(`/api/chats/${c.id}/auswerten`, { auswerten: cb.checked }));
      label.append(cb, el('span', '', 'auswerten'));
      e.append(kopf, label);
      ziel.append(e);
    }
  }

  function stand(s, auswertung) {
    const ziel = $('v-stand');
    if (!s || !s.chats) { ziel.textContent = ''; }
    else {
      const ab = s.abschnitte || {};
      const teile = [`${s.chats} Verläufe`, `${(s.nachrichten || 0).toLocaleString('de-DE')} Nachrichten`,
                     `${s.profile}/${s.personen} Personen ausgewertet`];
      if (ab.fertig || ab.neu) teile.push(`Abschnitte: ${ab.fertig || 0} fertig, ${(ab.neu || 0) + (ab.fehler || 0)} offen`);
      if (s.nicht_zugeordnet) teile.push(`${s.nicht_zugeordnet.toLocaleString('de-DE')} Nachrichten noch ohne Person`);
      ziel.textContent = teile.join(' · ');
    }
    // Grünes Band aus dem Serverzustand — bleibt nach dem Neuöffnen der App bestehen.
    const band = $('v-laeuft');
    const a = auswertung || {};
    band.hidden = !a.text;
    band.className = `ausgabe ${a.laeuft ? 'status-laeuft' : (a.fertig ? 'status-ok' : '')}`;
    band.textContent = a.text || '';
    const knopf = $('v-auswerten');
    knopf.hidden = !s || !s.chats || s.nicht_zugeordnet > 0 || !s.personen || a.laeuft;
  }

  $('v-auswerten').addEventListener('click', async (b) => {
    const status = $('v-status');
    const r = await senden('/api/chats/auswerten', {});
    status.hidden = false;
    status.className = `ausgabe ${r.ok ? 'status-ok' : 'status-fehler'}`;
    status.textContent = r.text || r.detail || 'Keine Antwort.';
    setTimeout(laden, 1500);
  });

  async function laden() {
    try {
      daten = await holen('/api/chats');
      offen(daten.offen);
      personen(daten.personen);
      chats(daten.chats);
      stand(daten.stand, daten.auswertung);
    } catch (_) { $('v-stand').textContent = 'Die Verläufe sind gerade nicht erreichbar.'; }
  }

  // Auf den Reiter reagieren und regelmäßig den Stand auffrischen, solange sichtbar.
  const sichtbar = () => location.hash === '#gedaechtnis' && !$('r-verlaeufe').hidden;
  for (const b of document.querySelectorAll('.reiter button')) {
    if (b.dataset.reiter === 'verlaeufe') b.addEventListener('click', laden);
  }
  setInterval(() => { if (sichtbar()) laden(); }, 15000);
})();
