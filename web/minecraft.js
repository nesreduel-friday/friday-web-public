/* Minecraft-Karte (21.10.2026): Spieler als 3D-Figur (CSS) mit Skin, Rüstung und Item in der Hand, daneben Ort, Herzen,
   Hunger und Hotbar mit den Original-Texturen aus dem Spiel (web/mc/).

   Die Daten kommen von der Mod `fridaycard` über den Kern (`kern/dashboard/minecraft.py`). Nichts wird als HTML
   eingesetzt, nur als Text; Kennungen von Items sind im Kern auf [a-z0-9_] zurückgeschnitten. Hotbar, Herzen und Hunger
   liegen in Prozent der 182-Pixel-Breite des Spiels und skalieren so mit jeder Kartenbreite. */
(() => {
  const MC = '/mc/';
  const ITEM_WINKEL = 0;                          // Neigung des Items in seiner Fläche
  const ITEM_DREHUNG = 32;                        // gegen die Drehung der Figur: das Item schaut den Betrachter an
  const S = 3.3;                                   // Bildpunkte je Textur-Pixel der Figur

  const el = (tag, klasse, text) => {
    const e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const bild = (src, klasse) => { const i = document.createElement('img'); i.src = src; i.alt = ''; i.draggable = false; if (klasse) i.className = klasse; return i; };

  /* ---------- Figur ---------- */

  // Ein Quader aus sechs Flächen, jede zeigt ihren Ausschnitt der Textur (u,v = Ursprung, w,h,d = Größe in Pixeln).
  function wuerfel(tex, tw, th, u, v, w, h, d, grow) {
    const g = grow || 0, W = w + 2 * g, H = h + 2 * g, D = d + 2 * g;
    const t = el('div', 'mc-teil');
    const flaeche = (ux, uy, fw, fh, w2, h2, tr) => {
      const e = el('div', 'mc-fl');
      e.style.width = w2 * S + 'px'; e.style.height = h2 * S + 'px';
      e.style.marginLeft = -w2 * S / 2 + 'px'; e.style.marginTop = -h2 * S / 2 + 'px';
      e.style.backgroundImage = `url(${tex})`;
      e.style.backgroundSize = `${tw * S * w2 / fw}px ${th * S * h2 / fh}px`;
      e.style.backgroundPosition = `${-ux * S * w2 / fw}px ${-uy * S * h2 / fh}px`;
      e.style.transform = tr;
      t.append(e);
    };
    flaeche(u + d, v + d, w, h, W, H, `translateZ(${D * S / 2}px)`);
    flaeche(u + 2 * d + w, v + d, w, h, W, H, `rotateY(180deg) translateZ(${D * S / 2}px)`);
    flaeche(u, v + d, d, h, D, H, `rotateY(-90deg) translateZ(${W * S / 2}px)`);
    flaeche(u + d + w, v + d, d, h, D, H, `rotateY(90deg) translateZ(${W * S / 2}px)`);
    flaeche(u + d, v, w, d, W, D, `rotateX(90deg) translateZ(${H * S / 2}px)`);
    flaeche(u + d + w, v, w, d, W, D, `rotateX(-90deg) translateZ(${H * S / 2}px)`);
    return t;
  }

  const MATERIAL = { leather: 'leather', chainmail: 'chainmail', iron: 'iron', golden: 'gold', diamond: 'diamond', netherite: 'netherite', copper: 'copper', turtle: 'turtle' };
  function ruestungsart(id) {
    const m = /^(leather|chainmail|iron|golden|diamond|netherite|copper|turtle)_(helmet|chestplate|leggings|boots)$/.exec(id || '');
    return m ? [MATERIAL[m[1]], m[2]] : null;
  }

  function skinAdresse(k) {
    if (k.skin_url || k.skin_da) return '/api/minecraft/skin?v=' + encodeURIComponent((k.skin_pfad || '').slice(-12));
    const m = /player\/(wide|slim)\/([a-z0-9_]+)/.exec(k.skin_pfad || '');
    return `${MC}skin/${m ? m[1] + '_' + m[2] : (k.skin_schmal ? 'slim_alex' : 'wide_steve')}.png`;
  }

  // Blöcke, die im Spiel als voller Würfel gezeichnet werden (scripts/mc_bloecke.py): Texturen oben / links / rechts.
  let BLOECKE = null, bloeckeLaden = null;
  const bloecke = () => bloeckeLaden || (bloeckeLaden = fetch(`${MC}bloecke.json`).then((r) => r.json()).then((j) => { BLOECKE = j; }, () => { BLOECKE = {}; }));

  // Die drei sichtbaren Flächen eines Würfels. `kante` ist die Kantenlänge als CSS-Länge, `fl` die Klasse der Fläche.
  function wuerfelFlaechen(b, kante, klasse) {
    const w = el('div', klasse);
    w.style.setProperty('--e', kante);
    const flaeche = (tex, tr, hell, tint) => {
      const f = el('div', 'mc-wf');
      f.style.backgroundImage = `url(${MC}block/${tex}.png)`;
      f.style.transform = tr;
      f.style.filter = `brightness(${hell})`;
      if (tint) { f.style.backgroundColor = tint; f.style.backgroundBlendMode = 'multiply'; }
      w.append(f);
    };
    const hoch = (t) => (b.nur_oben ? null : t);
    flaeche(b.o, 'rotateX(90deg) translateZ(calc(var(--e) / 2))', 1, b.tint);
    flaeche(b.l, 'translateZ(calc(var(--e) / 2))', 0.8, hoch(b.tint));
    flaeche(b.r, 'rotateY(90deg) translateZ(calc(var(--e) / 2))', 0.6, hoch(b.tint));
    return w;
  }

  // Item in einem Feld: Würfelblöcke als 3D-Würfel wie im Inventar, alles andere als flaches Symbol.
  function itemAnsicht(id) {
    const b = BLOECKE && BLOECKE[id];
    return b && b.o ? wuerfelFlaechen(b, '70cqw', 'mc-w3') : itemBild(id);
  }

  function itemBild(id, klasse) {
    const i = bild('', klasse);
    const flach = BLOECKE && BLOECKE[id] && BLOECKE[id].flach;
    const wege = [`item/${id}`, flach && `block/${flach}`, `block/${id}`, `block/${id}_side`].filter(Boolean);
    const weiter = () => { const w = wege.shift(); if (w) i.src = `${MC}${w}.png`; else i.hidden = true; };
    i.onerror = weiter;
    weiter();
    return i;
  }

  // Die Figur dreht sich leicht aus der Vorderansicht (diagonal). Teile mit Mittelpunkt in Texturpixeln, y nach unten.
  function figurBauen(k) {
    const schmal = !!k.skin_schmal, aw = schmal ? 3 : 4, ax = schmal ? 5.5 : 6;
    const skin = skinAdresse(k);
    const wurzel = el('div', 'mc-figur');
    const mitte = el('div', 'mc-teil');
    mitte.style.transform = `translateY(${-2 * S}px)`;
    const setze = (t, x, y, z, extra) => { t.style.transform = `translate3d(${x * S}px,${y * S}px,${z * S}px) ${extra || ''}`; return t; };
    const teil = (tex, tw, th, u, v, w, h, d, x, y, z, grow) => mitte.append(setze(wuerfel(tex, tw, th, u, v, w, h, d, grow), x, y, z));
    // Arm schwingt um die Schulter: Wurzel an der Schulter, Würfel hängt darunter.
    const arm = (tex, tw, th, u, v, w, x, grow, winkel, mitItem) => {
      const g = el('div', 'mc-teil');
      g.style.transform = `translate3d(${x * S}px,${-6 * S}px,0) rotateX(${winkel || 0}deg)`;
      const c = wuerfel(tex, tw, th, u, v, w, 12, 4, grow);
      c.style.transform = `translateY(${6 * S}px)`;
      g.append(c);
      if (mitItem) g.append(mitItem);
      mitte.append(g);
    };

    const hand = k.hand && k.hand.id;
    const winkelRechts = hand ? 38 : 0;                           // Arm mit Item leicht nach vorn
    let item = null;
    if (hand) {
      // Das Item sitzt mit seiner unteren Ecke (Griff) in der Faust und zeigt schräg nach oben; es dreht sich zum Betrachter, damit man es erkennt.
      item = el('div', 'mc-teil');
      const blk = BLOECKE && BLOECKE[hand];
      if (blk && blk.o) {                                                    // Block: kleiner Würfel in der Faust, wie im Spiel
        item.style.transform = `translate3d(0,${12 * S}px,${3 * S}px) rotateX(${-winkelRechts}deg)`;
        item.append(wuerfelFlaechen(blk, `${6 * S}px`, 'mc-w3h'));
      } else {
        item.style.transform = `translate3d(0,${11 * S}px,${1 * S}px) rotateX(${-winkelRechts}deg) rotateY(${ITEM_DREHUNG}deg) rotateZ(${ITEM_WINKEL}deg)`;
        const ib = itemBild(hand, 'mc-handitem');
        const gr = 16 * S * 0.85;
        ib.style.width = ib.style.height = gr + 'px';
        ib.style.left = -gr * 0.88 + 'px'; ib.style.top = -gr * 0.88 + 'px';
        ib.style.transform = 'scaleX(-1)';                          // Spitze zeigt nach außen, nicht quer vor den Körper
        item.append(ib);
      }
    }

    // Haut
    teil(skin, 64, 64, 0, 0, 8, 8, 8, 0, -10, 0);
    teil(skin, 64, 64, 16, 16, 8, 12, 4, 0, 0, 0);
    arm(skin, 64, 64, 40, 16, aw, -ax, 0, winkelRechts, item);
    arm(skin, 64, 64, 32, 48, aw, ax, 0, 0);
    teil(skin, 64, 64, 0, 16, 4, 12, 4, -2, 12, 0);
    teil(skin, 64, 64, 16, 48, 4, 12, 4, 2, 12, 0);
    // zweite Hautschicht (Jacke, Ärmel, Hosenbeine, Hut)
    teil(skin, 64, 64, 32, 0, 8, 8, 8, 0, -10, 0, 0.5);
    teil(skin, 64, 64, 16, 32, 8, 12, 4, 0, 0, 0, 0.25);
    arm(skin, 64, 64, 40, 32, aw, -ax, 0.25, winkelRechts);
    arm(skin, 64, 64, 48, 48, aw, ax, 0.25, 0);
    teil(skin, 64, 64, 0, 32, 4, 12, 4, -2, 12, 0, 0.25);
    teil(skin, 64, 64, 0, 48, 4, 12, 4, 2, 12, 0, 0.25);
    // Rüstung
    const r = k.ruestung || {};
    const kopf = ruestungsart(r.kopf), brust = ruestungsart(r.brust), beine = ruestungsart(r.beine), fuss = ruestungsart(r.fuesse);
    if (kopf && kopf[1] === 'helmet') teil(`${MC}armor/${kopf[0]}.png`, 64, 32, 0, 0, 8, 8, 8, 0, -10, 0, 1);
    if (brust && brust[1] === 'chestplate') {
      const t = `${MC}armor/${brust[0]}.png`;
      teil(t, 64, 32, 16, 16, 8, 12, 4, 0, 0, 0, 1);
      arm(t, 64, 32, 40, 16, 4, -6, 1, winkelRechts);
      arm(t, 64, 32, 40, 16, 4, 6, 1, 0);
    }
    if (beine && beine[1] === 'leggings') {
      const t = `${MC}armor/${beine[0]}_leggings.png`;
      teil(t, 64, 32, 16, 16, 8, 12, 4, 0, 0, 0, 0.6);
      teil(t, 64, 32, 0, 16, 4, 12, 4, -2, 12, 0, 0.5);
      teil(t, 64, 32, 0, 16, 4, 12, 4, 2, 12, 0, 0.5);
    }
    if (fuss && fuss[1] === 'boots') {
      const t = `${MC}armor/${fuss[0]}.png`;
      teil(t, 64, 32, 0, 16, 4, 12, 4, -2, 12, 0, 1);
      teil(t, 64, 32, 0, 16, 4, 12, 4, 2, 12, 0, 1);
    }
    wurzel.append(mitte);
    return wurzel;
  }

  /* ---------- Leisten ---------- */

  function herzenBauen(k) {
    const leiste = el('div', 'mc-leiste mc-herzen');
    const max = Math.max(1, Math.min(10, Math.ceil((k.leben_max || 20) / 2)));
    const halb = Math.ceil(k.leben || 0);                             // in halben Herzen
    for (let i = 0; i < max; i++) {
      const stelle = el('div', 'mc-sprite');
      stelle.style.left = i * 8 / 182 * 100 + '%';
      stelle.append(bild(`${MC}hud/heart_container.png`));
      if (halb >= i * 2 + 2) stelle.append(bild(`${MC}hud/heart_full.png`));
      else if (halb === i * 2 + 1) stelle.append(bild(`${MC}hud/heart_half.png`));
      leiste.append(stelle);
    }
    return leiste;
  }

  // Hunger füllt von rechts: Symbol 0 sitzt ganz rechts und ist zuerst voll, das linke leert sich zuerst.
  function hungerBauen(k) {
    const leiste = el('div', 'mc-leiste mc-hunger');
    for (let i = 0; i < 10; i++) {
      const stelle = el('div', 'mc-sprite');
      stelle.style.left = (182 - 9 - i * 8) / 182 * 100 + '%';
      stelle.append(bild(`${MC}hud/food_empty.png`));
      if ((k.hunger || 0) >= i * 2 + 2) stelle.append(bild(`${MC}hud/food_full.png`));
      else if ((k.hunger || 0) === i * 2 + 1) stelle.append(bild(`${MC}hud/food_half.png`));
      leiste.append(stelle);
    }
    return leiste;
  }

  function hotbarBauen(k) {
    const hot = el('div', 'mc-hotbar');
    hot.append(bild(`${MC}hud/hotbar.png`, 'mc-hotbar-bild'));
    (k.hotbar || []).forEach((s, i) => {
      if (!s.id) return;
      const feld = el('div', 'mc-slot');
      feld.style.left = (3 + 20 * i) / 182 * 100 + '%';
      feld.append(itemAnsicht(s.id));
      if (s.anzahl > 1) feld.append(el('span', 'mc-anzahl', String(s.anzahl)));
      hot.append(feld);
    });
    const sel = bild(`${MC}hud/hotbar_selection.png`, 'mc-auswahl');
    sel.style.left = (-1 + 20 * (k.auswahl || 0)) / 182 * 100 + '%';
    hot.append(sel);
    return hot;
  }

  // Das Rucksack-Raster (3 Reihen) wie im Inventar, in denselben Spalten wie die Hotbar.
  function inventarBauen(k) {
    const g = el('div', 'mc-inventar');
    (k.inventar || []).forEach((s, i) => {
      const feld = el('div', 'mc-feld');
      feld.style.left = (1 + 20 * (i % 9)) / 182 * 100 + '%';
      feld.style.top = (1 + 20 * Math.floor(i / 9)) / 62 * 100 + '%';
      feld.append(bild(`${MC}hud/slot.png`, 'mc-feld-bild'));
      if (s.id) {
        const it = el('div', 'mc-feld-item');
        it.append(itemAnsicht(s.id));
        if (s.anzahl > 1) it.append(el('span', 'mc-anzahl', String(s.anzahl)));
        feld.append(it);
      }
      g.append(feld);
    });
    return g;
  }

  /* ---------- Texte ---------- */

  const DIMENSION = { overworld: 'Oberwelt', the_nether: 'Nether', the_end: 'Ende' };
  function uhrzeit(t) {                                             // Spielzeit 0 = 6:00 Uhr
    const min = Math.floor(((t % 1000) * 60) / 1000), std = (Math.floor(t / 1000) + 6) % 24;
    const nacht = t >= 13000 && t < 23000;
    return `${nacht ? 'Nacht' : 'Tag'}, ${String(std).padStart(2, '0')}:${String(min).padStart(2, '0')}`;
  }
  const dreizeichen = (n) => String(n).replace('-', '−');

  /* ---------- Karte ---------- */

  function bauen(k) {
    const wurzel = el('article', 'karte minecraft');
    const figurBox = el('div', 'mc-figurbox');
    const rechts = el('div', 'mc-rechts');
    wurzel.append(figurBox, rechts);
    let figurSchluessel = '', daten = null, offen = false;
    const zeichnen = (n) => {
        const imSpiel = n.zustand === 'welt' || n.zustand === 'server';
        wurzel.classList.toggle('mc-menu', !imSpiel);
        figurBox.hidden = !imSpiel;
        const neu = [];
        const kopf = el('div', 'karte-marke', imSpiel ? 'Minecraft · im Spiel' : 'Minecraft · Hauptmenü');
        neu.push(kopf);
        const version = `${n.loader || 'Fabric'}${n.loader_version ? ' ' + n.loader_version : ''} · ${n.version || ''} · ${n.mods || 0} Mods`;
        if (!imSpiel) {
          neu.push(el('h2', 'karte-titel', `${n.loader || 'Fabric'} · ${n.version || ''}`), el('p', 'karte-text', `${n.mods || 0} Mods geladen`));
          rechts.replaceChildren(...neu);
          return;
        }
        const titel = el('h2', 'karte-titel', n.name || (n.zustand === 'server' ? 'Server' : 'Welt'));
        if (offen) { titel.classList.add('mc-zu'); titel.title = 'Inventar einklappen'; titel.addEventListener('click', () => { offen = false; zeichnen(daten); }); }
        neu.push(titel);
        if (n.zustand === 'server' && n.adresse && n.adresse !== n.name) neu.push(el('p', 'karte-hinweis', n.adresse));
        const zeilen = el('div', 'mc-zeilen');
        zeilen.append(el('b', '', `X ${dreizeichen(n.x)} · Y ${dreizeichen(n.y)} · Z ${dreizeichen(n.z)}`),
          el('span', '', `${DIMENSION[n.dimension] || n.dimension} · ${uhrzeit(n.tageszeit || 0)}`),
          el('span', 'mc-leise', version));
        neu.push(zeilen);
        const unten = el('div', 'mc-unten');
        const reihe = el('div', 'mc-reihe');
        reihe.append(herzenBauen(n), hungerBauen(n));
        if (n.erfahrung > 0) reihe.append(el('span', 'mc-xp', String(n.erfahrung)));     // Level in der Mitte, grün wie im Spiel
        const hot = hotbarBauen(n);
        if (!offen) { hot.classList.add('mc-klickbar'); hot.title = 'Inventar ausklappen'; hot.addEventListener('click', () => { offen = true; zeichnen(daten); }); }
        if (offen) unten.append(inventarBauen(n));
        unten.append(reihe, hot);
        neu.push(unten);
        rechts.replaceChildren(...neu);
        // Die Figur nur neu bauen, wenn sich Skin, Rüstung oder Hand geändert haben (sonst ruckelt sie alle 30 s).
        const sch = JSON.stringify([n.skin_url, n.skin_pfad, n.skin_schmal, n.ruestung, n.hand && n.hand.id]);
        if (sch !== figurSchluessel) {
          figurSchluessel = sch;
          const ansicht = el('div', 'mc-buehne');
          ansicht.append(figurBauen(n));
          figurBox.replaceChildren(ansicht);
        }
    };
    return {
      el: wurzel,
      update(n) {
        daten = n;
        if (BLOECKE) zeichnen(n);
        else bloecke().then(() => zeichnen(daten));                  // erst die Würfelliste laden, sonst zeichnet die Figur den Block flach
      },
    };
  }

  window.FridayMC = { bauen };
})();
