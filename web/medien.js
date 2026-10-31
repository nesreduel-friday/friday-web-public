/* Extras → hphome → Medien: Duplikate aufräumen, Serie/Film überall löschen.
   Fremder Text (Dateinamen, Serien-/Filmtitel) landet nie als HTML — überall textContent.
   Struktur wie überall sonst: .liste aus .eintrag mit .kopfzeile (Titel + Knopf). */
(() => {
  function eintrag(titel, klein, knopfText, klick, aufklappen) {
    const e = document.createElement('div'); e.className = 'eintrag';
    const kopf = document.createElement('div'); kopf.className = 'kopfzeile';
    const links = document.createElement('div');
    links.style.cursor = aufklappen ? 'pointer' : '';
    const t = document.createElement('div'); t.className = 'titel'; t.textContent = titel;
    links.appendChild(t);
    if (klein) { const k = document.createElement('div'); k.className = 'klein'; k.textContent = klein; links.appendChild(k); }
    const knopf = document.createElement('button'); knopf.className = 'weg sicher'; knopf.textContent = knopfText;
    knopf.addEventListener('click', (ev) => { ev.stopPropagation(); klick(knopf); });
    kopf.append(links, knopf);
    e.appendChild(kopf);

    if (aufklappen) {
      const details = document.createElement('div');
      details.style.marginLeft = '1em';
      details.hidden = true;
      let geladen = false;
      links.addEventListener('click', async () => {
        details.hidden = !details.hidden;
        if (!details.hidden && !geladen) {
          geladen = true;
          await aufklappen(details);
        }
      });
      e.appendChild(details);
    }
    return e;
  }

  function groesseText(obj) {
    const bytes = (obj.statistics && obj.statistics.sizeOnDisk) || obj.sizeOnDisk || 0;
    return bytes > 0 ? `${(bytes / 1e9).toFixed(1)} GB` : 'keine Dateien';
  }

  function fehlerZeile(text) {
    const p = document.createElement('p'); p.className = 'leer-klein'; p.textContent = text;
    return p;
  }

  async function antwortLesen(resp) {
    try {
      return await resp.json();
    } catch (_) {
      return null;
    }
  }

  async function ladeDuplikate() {
    const resp = await fetch('/api/medien/duplikate');
    const ziel = document.getElementById('hp-duplikate');
    ziel.innerHTML = '';
    if (!resp.ok) {
      ziel.appendChild(fehlerZeile('Duplikate konnten nicht geladen werden.'));
      return;
    }
    const daten = await resp.json();
    if (daten.length === 0) {
      ziel.appendChild(fehlerZeile('Keine Duplikate.'));
      return;
    }
    for (const d of daten) {
      ziel.appendChild(eintrag(d.download_pfad, `${Math.round(d.groesse / 1e6)} MB`, 'Löschen', async (knopf) => {
        if (!confirm(`"${d.download_pfad}" wirklich löschen?`)) return;
        knopf.disabled = true;
        const loeschResp = await fetch('/api/medien/duplikate/loeschen', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pfade: [d.download_pfad] }),
        });
        if (!loeschResp.ok) {
          alert('Löschen fehlgeschlagen — die Datei steht weiter in der Liste.');
          knopf.disabled = false;
          return;
        }
        const ergebnis = await antwortLesen(loeschResp);
        if (!ergebnis || ergebnis.geloescht.length === 0) {
          alert('Nicht gelöscht — die Datei ist inzwischen kein Duplikat mehr (oder außerhalb von downloads/).');
        }
        ladeDuplikate();
      }));
    }
  }

  async function loeschenUeberall(art, id, titel, knopf) {
    if (!confirm(`"${titel}" wirklich endgültig löschen (überall)?`)) return;
    knopf.disabled = true;
    try {
      const resp = await fetch(`/api/medien/${art}/${id}/loeschen`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ titel }),
      });
      const ergebnis = await antwortLesen(resp);
      if (!resp.ok || !ergebnis) {
        alert(`Fehler beim Löschen (${resp.status}). Bitte Liste neu laden und prüfen.`);
        return;
      }
      if (!ergebnis.hauptteil_ok) alert(`Fehler: ${ergebnis.fehler}`);
      else if (ergebnis.fehler) alert(`"${titel}" gelöscht, aber qBittorrent-Aufräumen fehlgeschlagen: ${ergebnis.fehler}`);
      else alert(`"${titel}" gelöscht. qBittorrent entfernt: ${ergebnis.qbittorrent_entfernt.join(', ') || 'nichts gefunden'}`);
      if (ergebnis.hauptteil_ok) ladeLoeschliste();
    } finally {
      knopf.disabled = false;
    }
  }

  async function loescheStaffel(serieId, staffelNr, titel, knopf) {
    if (!confirm(`"${titel}" wirklich endgültig löschen (überall)?`)) return;
    knopf.disabled = true;
    try {
      const resp = await fetch(`/api/medien/serie/${serieId}/staffel/${staffelNr}/loeschen`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ titel }),
      });
      const ergebnis = await antwortLesen(resp);
      if (!resp.ok || !ergebnis) { alert(`Fehler beim Löschen (${resp.status}).`); return; }
      if (!ergebnis.hauptteil_ok) alert(`Fehler: ${ergebnis.fehler}`);
      else if (ergebnis.fehler) alert(`"${titel}" gelöscht, aber qBittorrent-Aufräumen fehlgeschlagen: ${ergebnis.fehler}`);
      else alert(`"${titel}" gelöscht. qBittorrent entfernt: ${ergebnis.qbittorrent_entfernt.join(', ') || 'nichts gefunden'}`);
    } finally {
      knopf.disabled = false;
    }
  }

  async function loescheFolge(serieId, episodeId, titel, knopf) {
    if (!confirm(`"${titel}" wirklich endgültig löschen (überall)?`)) return;
    knopf.disabled = true;
    try {
      const resp = await fetch(`/api/medien/serie/${serieId}/episode/${episodeId}/loeschen`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ titel }),
      });
      const ergebnis = await antwortLesen(resp);
      if (!resp.ok || !ergebnis) { alert(`Fehler beim Löschen (${resp.status}).`); return; }
      if (!ergebnis.hauptteil_ok) alert(`Fehler: ${ergebnis.fehler}`);
      else if (ergebnis.fehler) alert(`"${titel}" gelöscht, aber qBittorrent-Aufräumen fehlgeschlagen: ${ergebnis.fehler}`);
      else alert(`"${titel}" gelöscht.`);
    } finally {
      knopf.disabled = false;
    }
  }

  async function ladeFolgen(serieId, staffelNr, serienTitel, ziel) {
    ziel.innerHTML = '';
    const resp = await fetch(`/api/medien/serie/${serieId}/folgen?staffel=${staffelNr}`);
    const daten = await antwortLesen(resp);
    if (!resp.ok || !daten || daten.fehler) { ziel.appendChild(fehlerZeile('Folgen konnten nicht geladen werden.')); return; }
    for (const f of daten.eintraege) {
      const bezeichnung = `S${String(staffelNr).padStart(2, '0')}E${String(f.episodeNumber).padStart(2, '0')} – ${f.title}`;
      const klein = f.hasFile ? 'Datei vorhanden' : 'keine Datei';
      ziel.appendChild(eintrag(bezeichnung, klein, 'Löschen',
        (knopf) => loescheFolge(serieId, f.id, `${serienTitel} ${bezeichnung}`, knopf)));
    }
  }

  async function ladeStaffeln(serieId, serienTitel, ziel) {
    ziel.innerHTML = '';
    const resp = await fetch(`/api/medien/serie/${serieId}/staffeln`);
    const daten = await antwortLesen(resp);
    if (!resp.ok || !daten || daten.fehler) { ziel.appendChild(fehlerZeile('Staffeln konnten nicht geladen werden.')); return; }
    for (const saison of daten.eintraege) {
      const groesse = groesseText(saison);
      ziel.appendChild(eintrag(
        `Staffel ${saison.seasonNumber}`, groesse, 'Löschen',
        (knopf) => loescheStaffel(serieId, saison.seasonNumber, `${serienTitel} Staffel ${saison.seasonNumber}`, knopf),
        (details) => ladeFolgen(serieId, saison.seasonNumber, serienTitel, details),
      ));
    }
  }

  async function ladeLoeschliste() {
    const [serienResp, filmeResp] = await Promise.all([
      fetch('/api/medien/serien').then((r) => r.json()),
      fetch('/api/medien/filme').then((r) => r.json()),
    ]);
    const ziel = document.getElementById('hp-loeschliste');
    ziel.innerHTML = '';
    if (serienResp.fehler) ziel.appendChild(fehlerZeile(`Serien: ${serienResp.fehler}`));
    if (filmeResp.fehler) ziel.appendChild(fehlerZeile(`Filme: ${filmeResp.fehler}`));
    if (!serienResp.fehler && !filmeResp.fehler
        && serienResp.eintraege.length === 0 && filmeResp.eintraege.length === 0) {
      ziel.appendChild(fehlerZeile('Keine Verbindung zu Sonarr/Radarr (Freigaben einrichten).'));
      return;
    }
    const suchtext = (o) => [o.title, o.originalTitle, o.year, ...(o.alternateTitles || []).map((a) => a.title)]
      .filter(Boolean).join(' ').toLowerCase();
    const mitSuche = (el, o) => { el.dataset.suche = suchtext(o); return el; };
    for (const s of serienResp.eintraege) {
      ziel.appendChild(mitSuche(eintrag(
        s.year ? `${s.title} (${s.year})` : s.title, `Serie · ${groesseText(s)}`, 'Löschen',
        (knopf) => loeschenUeberall('serie', s.id, s.title, knopf),
        (details) => ladeStaffeln(s.id, s.title, details),
      ), s));
    }
    for (const f of filmeResp.eintraege) {
      const mitJahr = f.year ? `${f.title} (${f.year})` : f.title;
      const filmTitel = f.originalTitle && f.originalTitle !== f.title ? `${mitJahr} / ${f.originalTitle}` : mitJahr;
      ziel.appendChild(mitSuche(eintrag(filmTitel, `Film · ${groesseText(f)}`, 'Löschen', (knopf) => loeschenUeberall('film', f.id, filmTitel, knopf)), f));
    }
    filtereLoeschliste();
  }

  function filtereLoeschliste() {
    const q = document.getElementById('hp-medien-suche').value.trim().toLowerCase();
    for (const e of document.querySelectorAll('#hp-loeschliste > .eintrag')) {
      e.hidden = !!q && !(e.dataset.suche || '').includes(q);
    }
  }
  document.getElementById('hp-medien-suche').addEventListener('input', filtereLoeschliste);

  window.FridayMedien = { ladeDuplikate, ladeLoeschliste };
})();
