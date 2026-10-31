# AGENTS.md — Friday (Web)

> Lebender Kontext für KI-Helfer, die an diesem Projekt arbeiten. Vor jeder
> Änderung lesen. Kurz halten: Hier steht, was JETZT gilt. Warum etwas so
> ist — Messungen, Vorfälle, eigene Fehler — steht in **`docs/LEHREN.md`**,
> nach Datum. Vor einer Änderung an einem Baustein dort den Abschnitt lesen.

## Was ist das?

Friday ist Eikes Sprachassistent und zweites Gedächtnis: hilft, erledigt
Dinge, merkt sich Dinge und erinnert von selbst. Bedient über den Browser
(PWA), läuft als Docker-Stack auf `hphome`.

Bausteine, Phasen und Entscheidungen: **`docs/STRUKTUR.md`**. Das ist die
verbindliche Grundlage.

## Stand (14.09.2026)

**Phase 4 läuft, Teile von Phase 5 sind da.** Version 0.4.0.

- **Sprechen** (Gemini Live, Push-to-Talk, Halb-Duplex) und **Chat** — dieselbe
  Persona, dieselben Werkzeuge, alles im Protokoll (`data/friday.db`).
- **Übersicht** (Startseite, 24.09.2026): Karten für das, was JETZT relevant ist — Meldungen (was auch als Push kam, bis gesehen,
  höchstens 24 h; `benachrichtigungen.senden` legt sie zusätzlich in `data/dashboard_meldungen.json` ab, auch bei Ruhezeit,
  nie bei ausgeschalteter Art), von Friday gelegte Karten (Werkzeug `dashboard_karte`, Lebensdauer 1–720 min, höchstens 6),
  Schule (Stunde/Pause mit Countdown, nur werktags 6–18 Uhr, Konnektor `schulmanager`) und Musik (Cover, Pause/Weiter/Nächster
  mit Schreibrecht, Konnektor `spotify`), PC (25.09.2026: nur wenn er antwortet — `pc.py` fragt im Hintergrund und blockiert nie;
  Laufzeit, offene Programme, Schnellknöpfe, Rückfragen von Herunterfahren/Energiesparen stehen in der Karte, Konnektor `pc`) und
  Jellyfin (25.09.2026: jede laufende Sitzung mit Vorschaubild und Steuerung; Konnektor `jellyfin` mit Adresse + API-Schlüssel wie
  der PC, das Bild läuft nur über `/api/jellyfin/bild/<id>`, der Schlüssel nie im Browser). Ein Anbieter ist eine Funktion `karten()` plus Eintrag in `kern/dashboard/karten.py`
  (`ANBIETER`); auf der Seite ein Eintrag in `ZEICHNER` (`web/uebersicht.js`). „Sprechen“ ist keine Seite mehr: Der blaue Kreis im
  Kopf startet/beendet das Gespräch auf jeder Seite; Transkript und Eingabe stehen im Chat (ein Verlauf, geht bei `/api/chat` mit).
  `#sprechen` bleibt als Alias auf die Übersicht.
- **Gedächtnis:** Notizbuch (SQLite, löscht nie, Vorschläge aus fremdem Text) plus
Langzeitgedächtnis in Graphiti; Rückblick nach jedem Gespräch, Verdichtung alle
6 h, nächtliche Sicherung nach `data/sicherung/`. Vektoren rechnet seit 05.10.2026 der lokale Embedder
(Container `friday-embedder`, `intfloat/multilingual-e5-small`, 384 Dimensionen, nur intern; `.env`:
`EMBEDDER_PROVIDER_GRAPHITI=openai`, `EMBEDDER_MODEL`, `EMBEDDER_DIMS`) statt Gemini — kein Tageskontingent mehr.
Zurück auf Gemini nur mit Neu-Einbetten (`scripts/graph_neu_einbetten.py`), sonst Zufallstreffer; Plan
`docs/superpowers/plans/2026-10-04-lokaler-embedder.md`. Den Muse-Fallback gibt es nicht mehr (entfernt 05.10.2026).
- **Nachdenken** (07.10.2026; Spec `docs/superpowers/specs/2026-10-07-nachdenken-design.md`, Plan `docs/superpowers/plans/2026-10-07-nachdenken.md`): Alle 2 h
  (`NACHDENKEN_TAKT_MIN`, 7–23 Uhr, ≤ 12 am Tag, nicht während Eike spricht) denkt Friday still nach (`kern/denken/`): Überblick (`kontext.py`) +
  **nur lesende Werkzeuge** (Positivliste `katalog.DENKEN_ERLAUBT`, Modus `denken`; die zwei Runden-Werkzeuge `gedanke_festhalten` und `hinweis_geben`
  gibt es nur dort) → Gedanken im Gedankenbuch (`data/gedanken.db`, löscht nie, ≤ 6 je Runde) und über `brucke.py` als Episode an Graphiti („keine
  Aussage von Eike“). Die letzten 3 Gedanken stehen im Prompt (`speicher.kontext_sicher`, nie solche aus Fremdtext); `gedanken_lesen` sucht ältere.
  Hinweise an Eike selten: `normal` = Karte, `hoch` = Push (Art `nachdenken`), ≤ 1 je Runde, ≤ 3 am Tag, Dublette 48 h. Schalter „Nachdenken“ in
  `einstellungen.json` (`nachdenken_an`, Standard an) und im Reiter Wissen → Gedanken (dort auch „Jetzt nachdenken“). **Stufe 3 (08.10.2026, Spec `docs/superpowers/specs/2026-10-08-nachdenken-faeden-design.md`):** Fäden (`faeden` in `gedanken.db`): Themen über mehrere Runden, höchstens 5 offene, jede Runde
  arbeitet an dem vom Server gewählten **Fokus-Faden** (Eikes zuerst, sonst am längsten unbewegt); `faden_anlegen`/`faden_weiter`/`faden_schliessen` nur in der Runde, `faeden_lesen`
  auch im Chat, `faden_vorschlagen` („denk mal über X nach“) nur im Chat. 14 Tage ohne Bewegung oder 6 Schritte → `ruht` (nie gelöscht). Ein Fremd-Faden als Fokus startet die Runde
  mit Fremd-Flag. Server-Regeln für Qualität: Hinweis erst nach Gedächtnis-Abfrage derselben Runde, `hoch` nur mit belegter Lücke (≥ 2 Quellen, sonst Karte), `verbindung`/`luecke` brauchen
  ≥ 2 Quellen; nur Verbindungen, Lücken und Faden-Ergebnisse gehen an Graphiti, und nur, wenn keine Gespräche auf Graphiti warten. Eike steuert die Fäden im Reiter Wissen → Gedanken. **Schwachstellen (28.10.2026, Plan `docs/superpowers/plans/2026-10-28-denkrunde-schwachstellen.md`):** Vor jeder Runde prüft `neues.seit` ohne Modell, ob seit der letzten etwas Neues da ist (Gespräche, Notizen, fällige Erinnerungen, offener Faden; nach 6 h läuft sie trotzdem, Lesefehler = neu); sonst fällt sie aus (`faellig` = `nichts_neu`), der Knopf umgeht das. Der Überblick trägt „Auffälligkeiten“ (`auffaelligkeiten.py`: Gemini-Limits, gescheiterte Vorhaben, aufgegebene Snaps; nur Zahlen). Die Runde darf Verbesserungen an Friday per `vorhaben_vorschlagen` anregen (Prompt-Abschnitt; im Code höchstens 2 neue je Tag, `speicher.DENKRUNDE_PRO_TAG`). Fehler behoben: „Gespräche seit der letzten Runde“ nutzt den Start der VORHERIGEN Runde.
- **Anliegen** (08.10.2026; Spec `docs/superpowers/specs/2026-10-08-anliegen-design.md`, Plan `docs/superpowers/plans/2026-10-08-anliegen.md`): Friday erwähnt Wichtiges
  beiläufig und einmal im Gespräch. Der **Server** wählt aus (`kern/anliegen/fach.py`, `data/anliegen.db`, löscht nie): höchstens ein Anliegen je Gespräch, 2 normale je Tag,
  in der Ruhezeit nur hohe, Verfall nach 3 Einblendungen oder 48 h; das Modell formuliert nur das Wie. Der Prompt-Block `[ANLIEGEN]` (`anliegen/block.py`) kommt in Chat und
  Snapchat mit Eike (jeder Zug), in die Sprache (nur beim Sitzungsstart, `live._anweisung`) und in Personen-Prompts (nur eigene Anliegen, nur bei „Proaktiv“; der Server hakt sie
  nach dem Zug ab). Werkzeug `anliegen_erledigt(id, erwähnt|später|nicht_mehr)` nur für Eike. Erzeuger: normale/hohe Hinweise der Denkrunde, fertige Fäden, gescheiterte Erinnerungen.
- **Fragen an Eike** (28.10.2026; Plan `docs/superpowers/plans/2026-10-28-fragen-an-eike.md`): Hängt Fridays nächster Schritt von Eikes Vorliebe ab, stellt sie ihm mit `eike_fragen` (Denkrunde und Chat, nach Fremdtext gesperrt, für Personen unsichtbar) eine Frage mit bis zu 4 kurzen Optionen und Push (Art `fragen`). Fach `kern/fragen/fach.py` (`data/fragen.db`, löscht nie): höchstens 3 offene, 3 neue je Tag, keine Dubletten, Verfall nach 72 h. Eike antwortet im Reiter Gedanken → „Fragen an dich“ (`GET /api/fragen`, `POST /api/fragen/<id>/antwort`: Option ODER eigener Text) oder im Chat (`frage_beantworten`, nur Eike, nie nach Fremdtext, nie in der Denkrunde). Der Block `[FRAGEN]` (`fragen/block.py`) steht in Chat und Sprache und trägt die Regel „nicht selbst entscheiden“; die Denkrunde sieht offene Fragen und Eikes Antworten (mit `bezug` faden:N / vorhaben:N) im Überblick und führt den Faden damit weiter.
- **Erinnerungen per Snapchat** (08.10.2026): `erinnerung(titel, text, zeitpunkt, kanal="push", an="")` — Kanal `push|snapchat|beides|gespraech`, `an` = Person (leer = Eike).
  Snapchat/andere brauchen das Snapchat-Schreibrecht, sind nach Fremdtext gesperrt, ein geratener Name führt zur Rückfrage, `sendepruefung.heikel` prüft beim Anlegen und vor
  dem Senden; der Text ist fest (≤ 280). An Personen nie 22:30–06:30 (verschoben auf 07:00), ≤ 3 offene je Person, ≤ 5 gesendete je Person und Tag, ≤ 20 offene an Personen.
  Zustellung in `erinnerungen.py` über den Sendeweg `snapchat.auftrag_legen(..., einmalig=True)` (gibt die Auftrags-Id zurück; der Helfer schreibt sie in `ergebnisse.jsonl`);
  `nachsehen()` bestätigt oder löst nach `aufgegeben`/`verworfen`/30 min Schweigen den Rückfall aus (Push an Eike plus hohes Anliegen). Kanal `gespraech` wird zur Fälligkeit ein Anliegen.
- **Konnektoren** (alle Default-Deny): Schulmanager samt Elternbriefen, IServ-Postfach,
  iCloud-Kalender (Termine eintragen nur mit Rückfrage), Spotify, GVH (Abfahrten mit
  ÜSTRA-Echtzeit, Verbindungen mit GVH-Echtzeit, wo sie kommt), Wetter, Studio.
  Ohne Konnektor: Uhrzeit, Internetsuche, Erinnerungen, WhatsApp-Verläufe, Webseiten und
  Apps auf Eikes Gerät öffnen (per „Öffnen“-Knopf in der App).
- **Proaktiv:** Web Push, Erinnerungen, Meldung fertiger Videos, Morgenbriefing — das
  ist eine **Einstellung** (Einstellungen-Seite oder per Zuruf), keine Freigabe, und
  enthält nur, was freigegeben ist.
- **Studio:** plant und rendert ein Kurzvideo am Tag für den Kanal Beispielkanal;
  veröffentlicht wird nie ohne Eikes Ja.
  **Studio-Tab (21.09.2026, Extras → Studio):** vier Ansichten — Überblick (Kennzahlen mit Veränderung zum Zeitraum
  davor, Verlauf, Wochentage), Videos (alle Videos, sortier- und durchsuchbar, je Video Rückhaltekurve, Tagesverlauf,
  Quellen, Länder), Publikum (Quellen, Suchbegriffe, Länder, Alter/Geschlecht, Geräte, Teilen …) und Werkstatt (was
  entsteht/wartet, bisherige Themen). Zeiträume 7/28/90/365 Tage und „Alles“. Nur lesen: `kern/studio/statistik.py`
  über die YouTube Analytics und Data API, Routen `/api/studio/statistik/…`; die Antwort trägt `zustand`
  (`ok`/`aus`/`getrennt`/`fehler`), damit die Seite nie leer bleibt. Ein fehlgeschlagener Bericht lässt nur seinen
  Abschnitt ausfallen (`ZugangFehler` bricht dagegen alles ab). Zwischenspeicher 10 Minuten im RAM. Impressionen und
  Klickrate gibt die API nicht her.
- **ÜSTRA-Garantie** (Tab Extras → Garantie, Konnektor `uestra_garantie`): sucht
  verspätete Fahrten (Bus, Stadtbahn, S-Bahn — die Garantie gilt nach Ticket, nicht nach
  Verkehrsmittel), liest das monatliche Ticket aus und bereitet Anträge vor. Abschicken
  (`kern/garantie/abschicken.py`) nur mit Schreib-Freigabe, per Knopf in der App und nach
  vollständiger Prüfung; kein Werkzeug fürs Modell. Fürs Limit (Anzeige, Stand „Limit erreicht“) zählen nur genehmigte
  Fahrten; BEIM ABSCHICKEN gilt zusätzlich eine harte Sperre mit genehmigten + noch offenen Anträgen (`antrag.hoechstzahl_mit_offenen`, 15.10.2026). Echtzeit-Verläufe werden gespeichert. Der Sammler scannt stündlich
  (4–1 Uhr) und meldet jede neue Fahrt über 20 Minuten in Eikes Orten per Push.
  **Profile (20.09.2026):** Eike reicht auch für Freunde ein. Profil 0 = Eike (Dateien unverändert), Freunde
  liegen in `garantie_profile.json`. Antrag, Angaben, Ticket, Höchstzahlen und Bescheide gehören je einer Person;
  Friday-Werkzeuge und Push kennen nur Eike. Abschicken für einen Freund braucht zusätzlich dessen Zustimmung
  (Häkchen), eine eigene E-Mail je Person (Plus-Alias) und ein passendes Ticket (`abschicken.py`). Jugend-/Ausbildungsnetzkarte
  mit Kundenkarten- und Ticketnummer und Preisgrenze.
  **Übersicht (21.09.2026):** Die Seite beginnt mit allen Personen (Ampel, Limits, „noch frei“, Zu erledigen) und wählt beim
  Vorbereiten, für wen (`garantie/uebersicht.py`). Nichts Widersprüchliches geht raus (`abschicken.widersprueche`), Papier-
  oder unklare Tickets erst nach Eikes „Nummern verglichen“ (siehe `docs/LEHREN.md`, 21.09.2026).
- **Garantie-Verwaltung** (11.10.2026, `kern/garantie/verwaltung.py`): feste Phasen (offen → vorbereitet → … → bescheid_* → widerspruch/erledigt) mit Übergangsprüfung und
  Historie, abgeleitet aus dem Bestand ohne ihn zu ändern; `faellig()` nennt je offenem Vorgang den nächsten Schritt, `erinnern()` meldet jeden einmal an Eike (Anliegen + Push, im
  `garantiesammler`-Lauf), nie an Dritte, nie Absenden. Werkzeuge `garantie_stand` (auch Denkrunde) und `garantie_status_setzen` (nur Eike, nicht nach Fremdtext). **App (12.10.2026):** je Vorgang im Block „Verfolgung“ Knöpfe für die erlaubten Folgephasen (`folgephasen` in `offene()`, nie „abgeschickt“) über `POST /api/garantie/verwaltung/<ref>/phase` (Freigabe + `bestaetigt`, Übergangsprüfung wie das Werkzeug, 4xx mit Klartext; Profile erlaubt, anders als beim Werkzeug); Vorgaben im Notizbuch per Formular anlegen/ändern (`POST /api/vorgaben`, `/<id>/aendern`, Prüfung im Fach).
- **Bahn-Fahrgastrechte** (22.09.2026, Konnektor `fahrgastrechte`, standardmäßig aus; Block im Tab Extras → Garantie): Züge von der
  S-Bahn bis zum ICE. Friday misst die Verspätung am Ziel wie bei der Garantie (GVH-Echtzeit, auch „Zug erfassen“ an einem Bahnhof),
  rechnet nach Tarif (Einzelfahrkarte 25/50 %, Zeitkarte 1,50 € je Fall bzw. beim Deutschlandticket je volle Stunde ab 20 Minuten,
  25 % der Karte INSGESAMT, 4-€-Grenze) und bildet je Karte und Stelle einen Sammelantrag: DB-Formular als PDF (ohne IBAN, Unterschrift
  von Hand) fürs Servicecenter, „Zum Abtippen“ für die S-Bahn Hannover. **Friday schickt nichts ab**; „Eingereicht ✓“ und den Bescheid
  setzt Eike. ÜSTRA-Garantie ODER Fahrgastrechte je Fahrt — gesperrt in beide Richtungen. Quellen und offene Punkte: `docs/LEHREN.md`, 21./22.09.2026.
- **Hausaufgaben** (Tab Extras → HA, über den Konnektor `schulmanager`): zeigt die letzten 7 Tage (per Knopf je
  7 mehr) und zerlegt jeden Eintrag einmal im Hintergrund: Quelle, Seiten, Aufgaben, machbar ja/teilweise/nein
  (`kern/schule/hausaufgaben.py`). Antippen zeigt Aufgabe, Einschätzung, Materialien und Bearbeitung.
  Schulbücher (mehrere PDFs, auch Doppelseiten) werden dort hochgeladen und einmal indexiert (`buecher.py`,
  eigener Schlüssel `BUECHER_GEMINI_API_KEY`).
  Bearbeitet wird mit der Freigabe `hausaufgaben`: Gemini blättert selbst im Buch (`loesen.py`), eigene Schlüssel
  `LOESEN_GEMINI_API_KEY` und `LOESEN_RESERVE_GEMINI_API_KEY`. Entfernte Bücher lassen sich dort endgültig
  löschen (Eintrag und Scans, nur nach dem Entfernen).
- **Goodnotes-Hefte** (Konnektor `goodnotes`, IServ-Anmeldung): Friday liest das Backup im IServ-WebDAV
  (`GoodNotes/`) alle 30 Minuten, schreibt geänderte Seiten mit Gemini ab (auch Handschrift) und macht sie
  durchsuchbar (`kern/goodnotes/`, Werkzeuge `notizen_suchen`, `notizseite`, `notizhefte`). Eigener Schlüssel
  `GOODNOTES_GEMINI_API_KEY`. Stand, Suche und Ordnerauswahl: Extras → HA.
- **Gruppenordner** (Konnektor `iserv_gruppenordner`, standardmäßig aus, dieselbe IServ-Anmeldung wie Goodnotes,
  22.09.2026): Kursmaterialien aus dem IServ-WebDAV (`Gruppenordner/`, ein Unterordner je Kurs) alle 30 Minuten
  abgleichen (`kern/schule/gruppenordner.py`). Ohne Gemini, wo möglich: PDF mit Textschicht, PowerPoint, Word
  werden direkt ausgelesen und nur zusammengefasst; Scans und Bilder gehen wie bei Klassenarbeiten als Ganzes
  an Gemini. Baustein 2 von 3 fürs Lernen-Tab (1: Arbeiten-Muster, 3: `lernen.py`, siehe dort).
- **Lernen** (Tab Schule → Lernen, 23.09.2026, `kern/schule/lernen.py`): zieht je Fach zusammen, was
  schon da ist — Noten (mit Semestern), Arbeiten-Muster (eigene und von anderen), Gruppenordner-
  Material, passende Goodnotes-Hefte (`arbeiten.fach_zuordnen` erkennt den Ordner). Liest nichts neu,
  ruft kein Gemini auf. Fächerliste statt Prüfungskalender: Schulmanager liefert keine anstehenden
  Klassenarbeiten, nur Stundenplan/Hausaufgaben/Elternbriefe (Eikes Entscheidung, 23.09.2026).
  Block „Aktuell im Unterricht" oben auf der Fach-Seite: die neuesten gelesenen Gruppenordner-Dateien
  (mit `wichtiges` — Formeln/Definitionen wörtlich, eigenes Feld neben `themen`) und die neuesten
  Goodnotes-Seiten der passenden Hefte, nach dem auf der Seite notierten Datum sortiert.
- **Standort** (Konnektor `standort`, standardmäßig aus): Die App holt die Browser-Position NUR AUF ANFRAGE (Long-Poll
  `/api/standort/warten`, `web/standort.js`) — nie beim Öffnen und nicht im Takt, denn iOS fragt die installierte App
  bei jedem Start neu nach der Erlaubnis. Friday löst das mit dem Werkzeug `standort` aus, wenn Eike fragt. Koordinaten nur im Arbeitsspeicher; Ortsname (Straße, ohne Hausnummer) auf Nachfrage von Nominatim;
  nie aus fremdem Text. Dazu `entfernung` (Luftlinie zu einem Ort, KEINE Gehstrecke — der OSRM-Demoserver
  liefert für „zu Fuß“ eine Autoroute) und `haltestellen_hier` (GVH-Haltestellen um den Standort, Namen
  immer mit Ort). Entscheidungen und offene Punkte: `docs/LEHREN.md`, 20.09.2026.
- **Standort der Familie** (Konnektor `personenstandort`, standardmäßig aus, 25.09.2026): „Wo ist Kontakt P?“ → Werkzeug `wo_ist`. Die Daten liegen NICHT in Friday,
  sondern im eigenen Container `friday-personenstandort` (Stack `~/docker/friday-personenstandort`, eigenes Repo): Die Familie meldet selbst per
  OwnTracks-App (HTTP-Modus mit HTTPS-Adresse `https://hphome.beispiel.ts.net:8443/pub` über `tailscale serve --https=8443` → Container-Port 8130; iOS blockiert reines HTTP), je Person nur der letzte Punkt. Friday liest über das interne Netz
  (`http://friday-personenstandort:8131`, `PERSONENSTANDORT_SCHLUESSEL` in `.env`). **Personen verwalten in der App (26.09.2026):** Freigaben → Standort der Familie → Personen (`web/personen.js`, Routen `/api/personenstandort/personen…`): anlegen, neues Passwort, pausieren, entfernen, mit Ein-Tipp-Link `owntracks:///config?inline=…`. Der Container hat dafür eine eigene Tür mit EIGENEM Schlüssel (`VERWALTUNGS_SCHLUESSEL` dort, `PERSONENSTANDORT_VERWALTUNGS_SCHLUESSEL` in Friday); das Passwort gibt es nur einmal (nicht gespeichert, nicht geloggt), Koordinaten kommen nie in die Liste; Entfernen mit zweitem Tipp, kein Werkzeug fürs Modell. Kein Apple-„Wo ist?“: kein wartbarer Linux-Client, Sperrrisiko
  für die Apple-ID (`docs/LEHREN.md`, 25.09.2026). Nie aus fremdem Text.
- **WhatsApp** (Konnektor `whatsapp`, standardmäßig aus, Container `waha`): Friday LIEST Chats, die Eike
  einzeln freigibt (`data/whatsapp_chats.json`) — gekoppelt per QR auf der Freigaben-Seite. Der Riegel sitzt in
  `kern/dienste/whatsapp.py`, nicht im Werkzeug. **Senden** nur mit zweitem Chat-Häkchen, Schreibrecht und
  Eikes Ja auf den vorgelesenen Wortlaut (`whatsapp_antwort` → `whatsapp_senden`, Stundengrenze).
  Chatinhalte sind fremder Text. Nicht zu verwechseln mit `kern/chats/` (hochgeladene Verläufe).
  Nachrichten gelten **eine Stunde** als frisch (Zwischenspeicher nur im RAM); ein **stündlicher Lauf**
  (gestreut, 7–23 Uhr, nur freigegebene Chats, nie `sendSeen`) hält sie warm und füttert das Morgenbriefing
  mit ZAHLEN, nie mit Inhalt. Abschaltbar über `WHATSAPP_HINTERGRUND=aus`.
  Sperrrisiko und offene Punkte fürs Antworten: `docs/LEHREN.md`, 20.09.2026.
- **Snapchat** (Konnektor `snapchat`, standardmäßig aus, 27.–29.09.2026): Friday schickt Eike spontan oder als
  Erinnerung (Bild aus dem Gedächtnis) einen echten Snap und **unterhält sich mit ihm im Chat** — über ein echtes
  Samsung-Handy per USB an hphome (`host-helfer/snap_helfer.py`, `adb`, kein Emulator wegen Snapchats
  Play-Integrity-Prüfung, kein Root). Werkzeug `schicke_snap` (Schreibrecht nötig, nach Fremdtext im selben Zug gesperrt): ein Bild aus dem Gedächtnis oder
  dem Netz, auf Wunsch mit Text darauf (`kern/dienste/snapbild.py` rechnet den Text als Band ins Bild, nicht über das
  Text-Werkzeug von Snapchat); nur auf Eikes Verlangen im Zug (auch Snaps an ihn selbst, seit 12.10.2026), Empfänger Eike oder eine Kontakt-Person. Text an Eike selbst: `schicke_nachricht` mit person „Eike“/„mich“ (derselbe Sendeweg wie die Erinnerung). Ohne Bild kein Snap. Antwortmotor ist derselbe wie im Web
  (`chat.antworten()` mit `persona.SNAPCHAT_KANAL`): volle Persona, Gedächtnis, Werkzeuge; der Verlauf verfällt nach
  **25 Minuten** Ruhe. **Snaps von Eike** öffnet der Helfer aus der Chatliste („New Snap“), holt das **reine Bild**
  (`adb exec-out screencap`, zugeschnitten auf `base_image_layer_container`, keine Datei auf dem Handy, kein
  Screenshot-Alarm) nach `data/snapchat/bilder/` und die Wache legt es wie ein Web-Bild ins Bildgedächtnis; Text und
  Snap innerhalb von 8 s sind **ein** Zug, ein Auftrag. Die Beschreibung im Bildgedächtnis ist **sachlich**
  (`kern/gedaechtnis/bildauswertung.py`, eigene Hintergrundrunde, nicht Fridays Antwort) und trägt die Quelle
  („Snapchat · Eike“, Spalte `quelle`); Ergänzungen (`bild_benennen` mit `info`, Spalte `notizen`) und beides geht über den
  Rückblick in den Langzeitspeicher. Getippt wird über die **ADB-Tastatur**
  (`com.android.adbkeyboard`, Umlaute/Emoji), sonst Rückfall auf `input text` (nur ASCII). Der Gesprächsmodus hält den
  Chat nach dem Lesen offen (Antwort ohne App-Neustart). Die Wache (`kern/dienste/snapchat_wache.py`, Takt 3 s) meldet
  einen stillen Helfer auf der Freigaben-Seite. Details und offene Punkte: `docs/superpowers/specs/2026-09-29-snapchat-gespraech-design.md`,
  `docs/LEHREN.md` (29.09.2026).
  **Person anschreiben (02.10.2026, umgebaut 07.10.2026):** „Schick Kontakt C meinen Stundenplan“ → Werkzeug `schicke_nachricht` (Schreibrecht nötig, nach Fremdtext gesperrt): Friday holt die Daten, formuliert selbst (Rollenmodell `persona.ROLLE_AN_DRITTE`: sie schreibt von IHREM Konto im Auftrag von Eike, in dritter Person über ihn) und sendet SOFORT — Eikes Auftrag ist die Freigabe, kein Vorlesen und kein Ja mehr (der alte Weg `schreibe_person`/`person_senden` ist abgelöst). Serverseitig gesperrt (Rückfrage statt Senden) bei mehrdeutigem/unbekanntem Empfänger, Fremdtext im Zug, pausierter Person, leerem oder zu langem Text (`TEXT_MAX` 280) und heiklem Inhalt (`werkzeuge/sendepruefung.py`: Passwörter, Zahlungsdaten, Adresse/Nummer Dritter; nur mit `trotzdem`). Derselbe Text an dieselbe Person binnen 60 s geht nur einmal raus (`auftrag_legen(..., einmalig=True)`, `DUBLETTE_S`); jeder Auftrag trägt eine `id`, der Helfer sendet jede ID höchstens einmal (`gesendet_ids.json`). Ein geratener Empfänger („Tom“ → Kontakt A) wird NIE direkt bedient, Friday fragt erst nach (Senden ist nicht rückholbar; gilt auch für `schicke_snap`). `trotzdem` bei heiklem Inhalt gilt nur mit Eikes Ja in seiner Äußerung (`kennt_zustimmung`). Das Register-Feld `senden_erlaubt` und `senden_entziehen` gibt es im Code nicht mehr als Sperre (Eike, 04.10.2026: kein Merken). Die Wache lässt einen Schlangenplatz für Eikes Direktaufträge frei. Bricht „Senden“ unbestätigt ab, wird NICHT erneut getippt (`SendenUnbestaetigt`, Ergebnis `unbestaetigt`). Jeder Zug trägt `[ZUG] Absender … Deine Antwort geht an …` (`chat.antworten`). Antworten einer Person kommen per Push-Art `kontakt_antwort` mit Inhalt zu Eike. **WhatsApp bleibt beim Vorlesen mit Ja** (gleiches Muster folgt, `sendepruefung.heikel` wiederverwenden).
  **Schnell und für viele Kontakte (07.10.2026; Spec `docs/superpowers/specs/2026-10-07-snapchat-schnell-design.md`):** **Heimat-Modus** (`data/snapchat/einstellungen.json` `{"heimat": true}`, Standard aus, ohne Neustart umschaltbar): Snapchat bleibt auf der Chatliste, kein App-Neustart je Auftrag, kein Gesprächsmodus (Wartezeit im Chat entfällt, auch für Eike), `lauf()` arbeitet fertige Aufträge zuerst in einer schnellen Schleife (`einmal(nur_auftraege=True)`), danach wird gelesen; `_chat_tab_oeffnen` tippt den Chat-Tab nur, wenn die Liste nicht schon offen ist (der Tipp scrollt sie sonst — das löste den Neustart-Neuversuch aus). Lesen scrollt bis `SEITEN_MAX` (3) Seiten / `ZEILEN_MAX` (25), jeder Name einmal je Durchgang. **Schutzschalter:** 5 Fehler in Folge → 10 min Pause (`pausiert_bis` im Zustand, `helfer_lage()` = „pausiert“). **Wache:** bis `PARALLEL` (3) Personen gleichzeitig an Gemini, Eike zuerst, je Person eine Anfrage, nach einem Fehler 1, nach 3 ruhigen Ticks wieder hoch. **Zufall:** Tippen mit Versatz ≤ ±15 % der halben Elementgröße und ≤ 8 px, nie außerhalb (`zufallspunkt`), Pausen 0,4–1,0 s. **Messen:** jedes Ergebnis in `ergebnisse.jsonl` trägt `dauer_s`, `adb_aufrufe`, `dumps`, `dump_s`, `pause_s`, `dump_stellen` (wer wie viele Dumps verlangte), `erster_fehler`, `gelegt`, `ausloeser`; Auswertung `python3 host-helfer/snap_latenz.py`.
  **Kontakt anfragen (03.10.2026):** „Frag Mara an, sie ist noch nicht in Snapchat“ → `kontakt_anfragen` (Name aus Register oder exakt von Eike, Vorlesen) und `anfrage_senden` (ein Ja = eine Anfrage, kein Neuversuch) legen Auftrag Art `"anfrage"` plus Vermerk in `data/snapchat/anfragen.json`; der Helfer sucht und tippt Hinzufügen nur bei genau einem Treffer. Annahme meldet die Wache per Push-Art `kontakte`; danach erst Register-Eintrag und `schicke_nachricht`-Weg. Rückzug im Gespräch oder auf der Kontakte-Seite.
  **Ticket-Annahme (09.10.2026, Teil 2 von 3; Spec `docs/superpowers/specs/2026-10-09-ticket-annahme-design.md`, Plan daneben):** Schickt eine Person mit offener Ticketanfrage ein Ticketbild, holt der Helfer das **Original** (Bild ~900 ms drücken → „Save to Camera Roll“ → `adb pull` aus `DCIM/Snapchat` → auf dem Handy löschen; nie blind tippen, ein Original je Durchgang, sonst Screencap) und meldet `art: "ticketbild"`. Die Wache nimmt es über `garantie/annahme.py` an: lesen (`ticket.auslesen`), bewerten ohne Modell (`angenommen`/`nachfragen`/`eike_pruefen`), nur bei `angenommen` und Papier im Profil der absendenden Person speichern, Hinweisblock für Friday nur aus festen Sätzen (nie Bildtext). Schalter `ticket_annahme` in `data/snapchat/einstellungen.json`: `aus` (Standard) · `trocken` (nichts speichern, nichts senden, Protokoll `data/snapchat/ticket_probe.jsonl`) · `an`; erwartete Personen in `data/snapchat/ticket_erwartet.json` (Register-Id oder `eike` im Probelauf). Teil 3 (Wächter: wer braucht ein Ticket, Nachfassen 1×/Tag, max. 3 Tage) setzt diese Liste; bis dahin bleibt Teil 2 im Alltag wirkungslos.
- **Kontakt-Module und Flammen** (14.10.2026; Spec `docs/superpowers/specs/2026-10-14-kontakte-module-design.md`, Plan daneben): Jede Kontakt-Person bekommt eine einfache Friday
  (Grundfunktionen `uhrzeit`, `suchen`, `nachrichten` immer an, kein Hebel) plus Module, die Eike pro Person in Kontakte (oder Eikes Friday per `modul_schalten`, nur Eike, im
  Sprachkanal mit Gegenlesen) umlegt: `plan` (aus/frei/voll), `garantie` (auto/an/aus; auto = an, wenn die Person ein Garantie-Profil hat, „an“ ohne Profil geht nicht),
  `medien`, `snap_wuensche`, `flammen`. Einzige Wahrheit: `kern/personen/module.py` (`wirksam`, `werkzeuge_fuer`, `primitiv_erlaubt`, `oeffnet`, `baustein_text`); Register-Feld
  `module` (validiert, jede Änderung in `data/modul_protokoll.jsonl`, `von` = app/friday/migration, Migration einmal beim Start). Garantie/Medien sind ein Riegel UND-verknüpft mit den
  Skills (`katalog._person_darf`), keine eigenständige Fähigkeit — die Feinheiten der Garantie klärt Eike nach der Umsetzung. Der Prompt (`persona.person_prompt`) setzt sich aus Basis +
  Bausteinen der wirksamen Module zusammen und verspricht nur Erreichbares. **Flammen** (`kern/personen/flammen.py`, Zustand `data/flammen.json`, Schalter `flammen` in
  `data/snapchat/einstellungen.json`: aus · trocken (nur `flammen_probe.jsonl`) · an): ein Server-Lauf (alle 5 min) legt je Person und Tag höchstens EINEN Snap, nur wenn der letzte
  Snap von Friday ≥ 20 h her ist, im Fenster 07:00–21:30 (Versatz 0–120 min je Person/Tag), nur mit Snapchat-Freigabe + Schreibrecht; reserviert die Person vor der Bildsuche, nach 3
  Fehlschlägen Pause bis morgen (dazu einmal je Tag und Person ein Push „schau nach dem Handy“ und die Störungsmeldung der Werkbank), `unbestaetigt` zählt als gesendet. **Snap-Wünsche** (`kern/werkzeuge/snapwunsch.py`, nur Personen): `snap_wunsch` sendet
  nur an die schreibende Person (Empfänger aus dem Register), höchstens 5/Tag, nie in der Ruhezeit, nur nach geprüftem, harmlosem Bild (`spassbild.finden` → `bildsuche.suchplan`
  + `pruefen(harmlos=True)`). Warum und Befunde: `docs/LEHREN.md`, 14.10.2026.
- **Recherche** (15.10.2026; Spec `docs/superpowers/specs/2026-10-15-recherche-design.md`, Plan daneben): Eike gibt Friday einen Rechercheauftrag (`recherche_starten`); ein Hintergrundlauf (bis 60 min, nur lesende Werkzeuge, Seiten und PDFs, Prüfpass gegen die gelesenen Texte) liefert einen Bericht mit Quellen als Push, Karte und Gedanke; `recherche_lesen` liest ihn vor. Details: `docs/LEHREN.md` (Abschnitt Recherche).
- **Regeln** (15.10.2026; Spec `docs/superpowers/specs/2026-10-15-regeln-design.md`, Plan daneben): Eike legt per Gespräch „Wenn X schreibt, tu Y“-Regeln an (`regel_schreiben` → Entwurf → Ja → `regel_bestaetigen`). Auslöser: Nachrichten (Snapchat live, WhatsApp ~10 min nur für Regel-Chats) und Uhrzeit (einmalig/täglich/Wochentage), Aktionen aus einer Positivliste (Push, Antwort an Auslöser/feste Empfänger, Kalender anlegen), Empfänger und Limits im Code, Karte „Regeln“ in der Übersicht (mit Knopf „Löschen“ = endgültig). Kein Hebel in Kontakte. WhatsApp-Nachrichten von Friday enden mit ` 🤖`. Details: `docs/LEHREN.md` (Abschnitt Regeln).
- **Aufgabenblätter** (15.10.2026; Spec `docs/superpowers/specs/2026-10-15-aufgaben-design.md`, Plan daneben): Eike hängt im Chat ein Aufgaben-PDF an (Knopf „Datei“, ≤ 10 MB, ≤ 40 Seiten). Standard: Friday löst direkt im Chat (PDF geht als `inlineData` an Gemini, der Zug zählt als Fremdtext). Sagt Eike „mit erweitertem Denken“, legt `aufgaben_starten` einen Hintergrundauftrag an (`kern/aufgabenblatt/`, nur lesende Werkzeuge, Prüfpass mit dem PDF, Push und Karte, KEIN Gedanke); `aufgaben_lesen` liest die Lösung vor, `aufgaben_pdf` baut die Ausarbeitung (Blattseiten + Lösungen) unter `/api/aufgabenblatt/<id>/ausarbeitung`. Recherche und Aufgabenläufe teilen sich `dienste/hintergrund.SPERRE`. Weiterleiten nur nach Eikes Auftrag mit den bestehenden Sende-Werkzeugen (nur Text). Details: `docs/LEHREN.md` (Abschnitt Aufgabenblätter).
- **Garantie-Annahme per Snapchat** (14.10.2026; Spec `docs/superpowers/specs/2026-10-14-garantie-annahme-design.md`, Plan daneben): Personen mit wirksamem Modul Garantie (Profil, Hebel nicht aus) bekommen den
  eingebauten **Standard-Skill `garantie_annahme`** (`kern/skills/standard.py`, nie in `skills.db`; ein eigener aktiver Skill mit `garantie_antrag_aus_akte` hat Vorrang, `zugriff.skills_fuer`). Der Ablauf: erkennen (Tafel-Foto,
  Text, „ich will was einreichen“), Fehlendes erfragen, Ticket per Foto lesen und die Nummern von der Person bestätigen lassen, feste Zusammenfassung, erst nach ihrer Antwort den Antrag vorbereiten; abgeschickt wird nur
  mit Eikes Knopf. Werkzeuge: `akte_zusammenfassung` (`werkzeuge/garantie_annahme.py`), `ticket_lesen`/`ticket_bestaetigen` (`werkzeuge/ticket_annahme.py`; vorläufiges Leseergebnis 30 min im Speicher, Bestätigung der Person ersetzt
  „Nummern verglichen“ mit Vermerk `pruefung_quelle: person`, Pflichtnummern der Jugendnetzkarte muss die Person selbst nennen, abweichende Kundenkarte wird nie überschrieben → Anliegen). Die **Bestätigungs-Sperre** in
  `garantie_antrag_aus_akte` (nur Standard-Skill): Zusammenfassung muss gezeigt sein, der Stand (`akten.stand_hash`) unverändert und der Zug ein anderer (`kern/skills/zug.py`: `ContextVar` mit Zug-Kennung und letztem Bild der Person,
  gesetzt in `chat.antworten`). Die Zusammenfassung baut der Server. Tests: `test_garantie_annahme_ablauf` (ganzer Ablauf über `chat.antworten`, Gemini als Skript).
- **Snapchat-Memories** (Tab Extras → Memories, 06.10.2026; Spec `docs/superpowers/specs/2026-10-06-snapchat-memories-design.md`, Plan daneben): Eike lädt Snapchat-Export-ZIPs
  hoch (in 32-MB-Teilen, bis 25 GB je ZIP), wählt **alles** (Overlay eingebrannt, Metadaten), **sauber** (nur Bilder ohne Overlay/Sticker/Text/Linse) oder **personen** (sauber und mit
  Person) und bekommt eine ZIP zurück. Gerechnet wird im eigenen Container `snapmem` (ffmpeg, OpenCV, onnxruntime; Friday rechnet nichts): Friday legt Aufträge in
  `data/snapmem/auftraege/`, der Arbeiter meldet Stand und Ergebnis in `data/snapmem/ergebnisse/`. Kein Werkzeug fürs Modell. **Grenzen:** Zeit und Ort gibt es nur, wo der Export
  sie eindeutig zuordnet (ZIP 1: 465 von 2102), sonst steht nur das Datum; „eingebrannt/Linse“ ist eine **Schätzung** (Geo-Regel + CLIP-Modell, Zahlen in `docs/LEHREN.md`, 06.10.2026).
- **Gmail** (Konnektor `gmail`, App-Passwort, nur IMAP lesend): allgemeiner Mail-Konnektor
  mit `gmail_mails` (Gmail-Suchsyntax) und `gmail_mail` — live gelesen, nichts gespeichert,
  nichts durch Gemini vorbereitet (`kern/werkzeuge/gmail.py`). Im Hintergrund liest
  `kern/garantie/post.py` stündlich nur die Bescheide der ÜSTRA (`from:info@uestra.de`),
  verknüpft sie mit Anträgen und zählt sie für die Höchstgrenzen.

- **Claude-Sitzungen** (Abschnitt im Tab Extras → hphome, 22.09.2026): startet auf hphome eine Claude-Code-Sitzung
  mit Fernzugriff, zeigt die laufenden samt Link (weiterarbeiten am Handy) und beendet sie wieder.
  Friday im Container startet keine Prozesse auf dem Host: Die Route legt nur einen Auftrag in
  `data/claude/` ab, ausgeführt wird er vom Helfer `host-helfer/claude_helfer.py` (tmux-Sitzungen
  `claude-<name>`, höchstens 3, `--permission-mode manual`). **Kein Werkzeug fürs Modell** — nur Eikes
  Tipp in der App, wie beim Abschicken der Garantie. (Die Werkbank unten ist die einzige Ausnahme.)
- **Werkbank** (27.10.2026; Spec `docs/superpowers/specs/2026-10-27-werkbank-design.md`): Friday sammelt Verbesserungen an sich selbst als **Vorhaben** (`data/werkbank.db`, löscht nie). Sie entstehen im Gespräch
  (`vorhaben_vorschlagen`, auch Denkrunde), aus wiederkehrenden Störungen (`werkbank/stoerung.py`: Flammen-Snap meldet einmal statt täglich, über `anliegen`) und aus der Denkrunde. Gestartet wird nur auf Eikes Wort:
  `vorhaben_starten` (Auftrag im selben Zug, nie nach Fremdtext, sonst Rückfrage) oder Knopf in Extras → hphome. Worker **Claude Code** (headless im eigenen Worktree/Branch `werkbank/<id>`, eingesperrt, Tests mit leerem Datenordner,
  Compose/Host-Pfade hart gesperrt) oder **OpenCode** nur in der Sandbox (`werkbank-sandbox/`, kein `.env`/`data/`, Netz nur über Egress-Proxy). Ergebnis = Branch + Diff + Testbericht; sind Tests neu rot, gibt der Helfer die Fehler automatisch an denselben Worker zurück (**Nachbesserung**, höchstens 2, ein Zeitbudget für alle Claude-Läufe, Karte zeigt „Nachbesserung n von 2“; `docs/LEHREN.md`, 28.10.2026); **Übernehmen** (Merge, Rückfall) nur per Knopf und nur für `kern/`, `web/`, `test/`, `eval/szenarien/`, `docs/` (ohne Agenten-Dateien und Abhängigkeiten), alles andere von Hand. Ein Merge-Konflikt **nur** in `docs/LEHREN.md` wird dabei automatisch vereinigt (beide Einträge bleiben, 30.10.2026); jeder andere Konflikt bricht ab wie bisher. Ein gescheitertes Vorhaben (`fehler`) wird verworfen und bei Bedarf neu vorgeschlagen, nicht neu gestartet.
  Manuelle Starts sind unbegrenzt (werden protokolliert). **Automatik** (28.10.2026, `werkbank/automatik.py`): Eikes Hebel in der Karte (`werkbank_auto` aus|an, Standard aus; `werkbank_auto_backend` claude|opencode; `einstellungen.json`, nur Route `/api/werkbank/einstellungen`, kein Werkzeug) lässt Friday alle 5 min das älteste passende Vorhaben starten (nicht fremd, nicht aus der Denkrunde, bei OpenCode nur test/idee, nur wenn nichts läuft), höchstens 5 automatische Starts am Tag; übernehmen kann sie nichts (`docs/LEHREN.md`, 28.10.2026). **Prüf-Worker** (28.10.2026, `werkbank/pruefen.py`, Helfer-Aktion `pruefen`): Friday kennt ihren Code nicht; `machbarkeit_fragen` lässt einen Claude Informationen sammeln und Machbarkeitsfragen beantworten (Prompt: Fundstellen belegen; Code NUR lesen in frischem Worktree von `main`, kein `data/`/`.env`; zusätzlich Read nur in freigegebenen Host-Ordnern `PRUEF_HOST_LESEN` = Minecraft-Betrieb unter `~/docker/friday-mc` ohne Schlüssel/Tokens, Netz lesen per WebFetch/WebSearch ohne localhost/Tailnet, Bash nur `ls/stat/df/du/ps/wc/file` ohne Dateiinhalt; Container nur über `host-helfer/werkbank_docker.py`, Immich und `friday-web` gesperrt; Eike, 28.10.2026). `backend` claude (Standard) oder deepseek: derselbe Käfig, Modell und Umgebung wie beim Bau-Worker (`deepseek_umgebung()`), Schlüssel aus `~/.config/friday/deepseek.key` nur in der Umgebung des Prozesses, `machbarkeit_lesen` holt die Antwort (nach 1–3 min; Nachfragen setzen dieselbe Sitzung fort, die Id bleibt auf dem Host). Nur Eike im Chat, nie nach Fremdtext, nie in Denkrunde/für Personen; die Antwort zählt als Fremdmaterial. Eine Prüfung gleichzeitig, ≤ 10 Starts/Tag, ≤ 3 Nachfragen je Prüfung. Weiteres Modell: Eintrag in `pruefen.BACKENDS` und `PRUEF_BACKENDS` des Helfers. Nach Änderung am Helfer neu starten. Die Denkrunde läuft wahlweise mit Gemini (Standard) oder OpenCode (nur mit Eikes Eigenem als Kontext, keine Fremdtexte). Live seit 28.10.2026. Sandbox-Modell `deepseek-v4.1-flash-free`; die Sandbox sieht nur `docs/`, `AGENTS.md`, `CLAUDE.md` schreibgeschützt unter `/projekt`. Der Worker (Claude) läuft mit `--model sonnet`. Das Sandbox-Netz braucht die Host-Sperre `sudo scripts/werkbank-firewall.sh an` (nach Neustart/Netz-Neuanlage wiederholen).
- **hphome** (Tab Extras → hphome, 26.09.2026, ersetzt den Reiter „Claude“; Spec `docs/superpowers/specs/2026-09-26-hphome-tab-design.md`): Zustand des Servers
  (Uptime, Last, RAM, Temperatur, Platte, Container mit Zustand/Gesundheit/CPU/RAM, Tailnet-Geräte, Sicherung, größte Ordner, Updates), „Öffnen“ zu Jellyfin, Sonarr, Radarr,
  Prowlarr, qBittorrent, Heimdall, Portainer, AdGuard und Cockpit, je Container Neustart/Stopp/Start/Logs, dazu **hphome neu starten** und **Docker aufräumen**. Der Friday-Container
  hat keinen Docker-Zugriff: Alles läuft über den Helfer `host-helfer/hphome_helfer.py` (tmux `friday-hphome-helfer`, cron wie beim Claude-Helfer), der genau acht feste
  Aktionen kennt (die siebte, `handy_start`, schaltet die Handy-Steuerung an; die achte, `bot_modell`, stellt den Minecraft-Bot auf Gemini oder DeepSeek um — siehe Freigaben)
  (Namen gegen `docker ps -a`, keine Shell, `friday` nicht stoppbar, Abstand 3 s, Verlauf in `data/hphome/verlauf.jsonl`). Host-Neustart nur mit sudoers-Zeile
  (`NOPASSWD: /usr/bin/systemctl reboot`, einmalig von Eike), Aufräumen nur Build-Cache und Images über 24 h. **Kein Werkzeug fürs Modell** für alles Ändernde; `server_status`
  (Konnektor `hphome`, standardmäßig aus) liest nur, Logs gehen nie ans Modell. Wache `kern/dienste/hphome_wache.py` (Art `server`): Platte ≥ 90 %, Container unhealthy oder
  ≥ 3 min weg (nur, wenn er vorher lief, nicht in der App gestoppt wurde und kein Host-Neustart läuft), höchstens alle 6 h je Befund.
  **Nach jeder Änderung am Helfer neu starten:** `tmux kill-session -t friday-hphome-helfer; host-helfer/hphome_helfer_starten.sh`.

- **Medien** (Tab Extras → hphome → Medien; 08.10.2026 auch per Werkzeug): `medien_suchen` (lesend), `medien_herunterladen` („lade Film/Serie X runter“: legt in Sonarr/Radarr an, Suche startet; nur bei eindeutigem Treffer, sonst Rückfrage; Schreibrecht, nach Fremdtext gesperrt), `medien_loeschen` (löscht NICHTS, fragt zurück) und `medien_loeschen_bestaetigen` (löscht erst nach Eikes Ja, nie nach Fremdtext) für Serie, Film, Staffel oder Folge; gleicher Ablauf wie im Tab (Sonarr/Radarr, dann qBittorrent, Protokoll `data/medien/aktionen.log`). Konnektor `medien` (ohne Zugang, standardmäßig aus; Löschen braucht zusätzlich das Schreibrecht). Nicht für Fremde und nicht in der Denkrunde.
- **Kontakte** (Tab Extras → Kontakte, 29.09.2026, Teil 1 von 5; Spec `docs/superpowers/specs/2026-09-29-kontakte-personen-design.md`, Plan
  `docs/superpowers/plans/2026-09-29-kontakte-teil1-register-menue.md`): Register der Personen, mit denen Friday schreiben soll (`kern/personen/`,
  `data/personen.json`, 600): Name, Snapchat-Anzeigename, Persona (nur Ton), erlaubte Werkzeuge (feste Positivliste `rechte.FREMDEN_ERLAUBT`:
  Uhrzeit, Suche, Nachrichten — nie etwas auf Eikes Daten; `wetter` und `webseite` bewusst nicht, siehe LEHREN), Proaktiv, Pausiert. Personen werden pausiert, nie gelöscht.
  Ein Kontakt ist eine BEKANNTE Person (`graph_name`, ein Eintrag je Person) plus Snapchat-Anzeigename; beim Anlegen wählt Eike aus allen Personen, die Friday
  kennt (`personen/bekannt.py`: Wissensgraph, Familie, Garantie-Profile, WhatsApp, benannte Bilder; fällt eine Quelle aus, fehlt nur sie). Eike hält die
  Anzeigenamen in Snapchat selbst eindeutig — der Anzeigename ist der Schlüssel.
  **Teil 3 (Wissen mit Herkunft, 29.09.2026):** Graph-Gruppe je Person (`graph.pruefe_gruppe`: nur `eike`, `p<Zahl>`, `friday-test`; nie vom Modell wählbar).
  Gespräche mit einer anderen Person (`gespraeche.person`) gehen mit ihrem Namen als Sprecher in IHRE Gruppe und danach einmal mit Vermerk („Herkunft: Kontakt B,
  nicht Eike“) als Kopie in Eikes Gruppe (`rueckblick.kopie_an_eike`). Freigabe = Kopie in die Gruppe der Person (`personen/freigaben.py`), Widerruf löscht sie
  (`delete_episode`, Nachlauf; Fehler geben nie auf, „noch nicht da“ nach 7 Tagen). **Teil 2 MUSS** vor jeder Graph-Suche einer Person `freigaben.suche_gesperrt(pid)` prüfen
  (fail-closed) und die Gruppe aus dem Register nehmen, nie vom Modell. Bilder tragen `person`. Noch nicht verdrahtet: Antwortmotor je Person (Teil 2), Snapchat mit mehreren Kontakten (Teil 4).
  **Teil 2 (Antwortmotor, 30.09.2026):** `chat.antworten(..., person=<Register-Profil>)`: eigener Prompt (`persona.person_prompt`, NICHT Eikes `CHARAKTER`/`FAEHIGKEITEN`,
  ohne Gedächtnisblock und Umgebung), Werkzeuge nur die Schnittmenge aus `rechte.FREMDEN_ERLAUBT` und dem Register-Eintrag plus `frage_eike` (`katalog._person_darf`, beim Anbieten
  UND Ausführen; `frage_eike` sieht Eike nie), Wissen nur aus ihrer Graph-Gruppe (`personen/wissen.py`, bei offenem Widerruf gesperrt). Vom Aufrufer zählt nur `person["id"]` (Format `p<Zahl>`): Der Motor holt Profil, Werkzeuge, Persona und Pause frisch aus dem Register (`person=None`/unbekannt → `ChatFehler`), und `kanal` ist bei
  einer Person nur ein Kanalname aus `chat.KANAELE_PERSON` (nie freier Text). Ihr Text ist immer fremd, ihr „ja“ nie Zustimmung,
  ihre Links werden nicht abgerufen, eine pausierte Person bekommt keine Antwort. Rückfragen (`personen/rueckfragen.py`, `data/freigabe_anfragen.json`): höchstens 5 offen je Person, Verfall
  24 h, Push + Menü; Eikes Ja formuliert eine Freigabe. **Noch nicht verdrahtet:** Kein Kanal ruft `chat.antworten(person=…)` auf (Teil 4: Snapchat mit mehreren Kontakten; dort auch
  Verlauf je Person, Sendegrenze und die Antwort an die Person nach Eikes Ja).
  Wer Friday schreibt und nicht angelegt ist, landet in `data/personen_unbekannt.json` (Push-Art `kontakte`) und wird nie beantwortet.
  Kein Werkzeug fürs Modell.
  **Teil 4a (Senden an den richtigen Kontakt, 30.09.2026):** Sendeaufträge tragen `empfaenger` (Anzeigename) und `person` (`snapchat.auftrag_legen(..., person=)`, ohne Angabe = Eike;
  unbekannte, pausierte und Personen ohne Snapchat-Namen bekommen keinen Auftrag). Der Dienst schreibt `data/snapchat/kontakte.json` (Eike + aktive Personen mit Snapchat-Namen, Eikes Name
  über `SNAPCHAT_EIKE_NAME`, Standard „Eike“). Der Helfer sendet NUR an Namen aus dieser Liste (fehlt sie: nur Eike), verwirft Aufträge ohne oder mit unbekanntem Empfänger und prüft nach dem
  Antippen, wo er gelandet ist: Chat-Kopf == Empfänger (sonst Abbruch vor dem Tippen), Auswahlleiste in Send-to == genau der Empfänger (sonst Abbruch vor „Senden“). Er öffnet nur Snaps aus Eikes
  Zeile (ein geöffneter Snap ist verbraucht) und verwirft Editor-Entwürfe nach „Preview“ über „Abandon“ (blinde Bildschirme, Farberkennung, `docs/LEHREN.md`). Der Namensvergleich `_norm` ist
  dieselbe Regel wie `register.schluessel` (Paritätstest). **Jeder Freund im Friday-Konto braucht einen eigenen Spitznamen:** Ist ein Name in Chatliste oder Send-to mehrdeutig (zwei Freunde, Gruppe mit gleichem Namen), oder schreiben im Chat fremde Absender, sendet der Helfer nichts. Auftrag ohne/mit unbekanntem Empfänger oder falschem Person/Empfänger-Paar wird verworfen (`AuftragVerworfen`), der Helfer läuft in Berliner Zeit (`ist_wach`). **Teil 4b (Wache je Person, 30.09.2026):** Eingangseinträge tragen `person` (Register-Id; fehlt es, ist es Eike; ein ungültiger Wert wird NIE als Eike gedeutet: `snapchat.person_von/gruppen`). Die Wache bearbeitet je Tick die Gruppe mit dem ältesten Eintrag: Eike auf dem alten Weg, eine Person über `chat.antworten(person=…, kanal="Snapchat")` mit eigenem Faden/Verlauf (`snapchat.faden(person)`, Limit 500 Gespräche), Bildern mit `person` und Quelle „Snapchat · <Name>“, `auftrag_legen(..., person=pid)`; pausierte, gelöschte oder unbekannte Personen bekommen keine Antwort. Unbekannte Namen: `data/snapchat/unbekannt_eingang.json` (schreibt der Helfer) → `unbekannt.melden` (ein Push je Name). Rückfrage-Antworten: `rueckfragen.zu_melden`, Wache `_rueckfragen_melden` (Freigabetext oder neutrale Absage „Ich habe bei Eike nachgefragt: Dazu kann ich dir leider nichts sagen.“, höchstens einmal, nie an Fremde). **Teil 4c (Helfer liest alle Kontakte, 30.09.2026):** Nach Eike (unverändert) liest der Helfer alle Kontakte aus `kontakte.json`, bei denen die Chatliste Neues zeigt (Status „New …“/Snap; `neue_kontakte`): erst ein Snap aus SEINER Zeile, dann der Chat (`kontakt_lesen`); jeder Eingangseintrag trägt `person`, der Zustand je Kontakt liegt in `kontakt_zustand.json` (Eikes bleibt in `sichtbar*.json`). Unbekannte Namen werden nur vermerkt (`unbekannt_eingang.json`, der Helfer hängt nur an, die Wache meldet und löscht nichts) und nie geöffnet; mehrdeutige Namen werden übersprungen. Ein Fehler bei einem Kontakt beendet nur diesen. Beim Senden an einen anderen Kontakt liest der Helfer dessen Nachrichten mit (`_kontakt_mitlesen`) und holt einen wartenden Snap von ihm zuerst. Gesprächsmodus je Kontakt (`gespraech_halten(kontakt=, person=)`: nur Aufträge mit passendem Empfänger UND Person). Jedes Auftragsergebnis (`gesendet`/`verworfen`/`aufgegeben`) steht mit Länge, nie mit Text, in `data/snapchat/ergebnisse.jsonl`. **Noch nicht:** Eike bittet Friday um einen Snap an eine Person (Ja auf Wortlaut und Empfänger); Menü-Rest und Gesichtsvergleich (Teil 5). Der Gesprächsmodus mit einem Kontakt hält den Helfer bis zu 2 Minuten im Chat — Eike und andere warten so lange.

Offen: abends ein Blick auf morgen; Werkstatt/Reparaturlauf (Phase 6); weitere
Werkzeuge über MCP.

## Wo liegt was

| Pfad | Inhalt |
|---|---|
| `kern/main.py` | Alle HTTP-Routen, startet die Hintergrundläufe |
| `kern/config.py` | Modelle, Schlüssel je Zweck, Zeitzone, Datenordner |
| `kern/konnektoren.py` | Konnektor-Liste, Freigaben, Zugangsdaten (`data/zugaenge.json`) |
| `kern/sitzung/` | `live.py` (Sprache, Gemini-Live-Proxy), `chat.py` (Text, Werkzeugrunden) |
| `kern/gehirn/persona.py` | Der Prompt — die wichtigste Regel steht am Ende |
| `kern/werkzeuge/` | Ein Modul je Bereich; `katalog.py` sammelt, filtert und führt aus |
| `kern/dienste/` | Anbindungen nach außen (Schulmanager, IServ, iCloud, GVH, Spotify, Wetter, Push, Studio, Web) |
| `kern/gedaechtnis/` | Notizbuch, Graphiti, Rückblick, Verdichtung, Bilder |
| `kern/planer/` | Morgenbriefing samt seiner Einstellung (`data/einstellungen.json`) |
| `kern/denken/` | Nachdenken: `buch.py` (Gedankenbuch `gedanken.db`), `kontext.py`, `runde.py` (Werkzeugschleife), `hinweis.py` (Karte/Push mit Grenzen), `lauf.py` (Takt, Schalter), `brucke.py` (Graphiti). Werkzeuge `kern/werkzeuge/gedanken.py`, Reiter „Gedanken“ in `web/gedaechtnis.js` |
| `kern/ehrlichkeit/` | `wirkung.py` (12.10.2026): jedes Werkzeugergebnis hat `status` (ok/nicht/rueckfrage), `art` und eine kurze `quittung`; Wirkungswerkzeuge sind mit `@wirkt` beschrieben (Erfolg = Whitelist), `katalog.ausfuehren` liefert die Felder, das Modell sieht nur `ergebnis`/`fehler` (bei „nicht“ mit „NICHT ERLEDIGT: “ davor), Chat/Live sammeln `wirkungen` je Zug. **Seit 12.10.2026 (P2–P4):** Quittungen als Fakt-Zeilen (Chat `quittungen`, Live-Event `quittung`, Protokoll `art=quittung`); `behauptungen.py`+`waechter.py` (eine Logik für Live, Chat und Eval-Harness): Live prüft den Zug nach `turnComplete` (rote Karte `korrektur` + Textzug `[KERN]`, einmal je Zug), Chat vor der Auslieferung (ein Neuversuch ohne Werkzeuge, dann Schablone, Rückgabe `geprueft`); `gegenlesen.py`+`personen/abgleich.py`: im Sprachkanal erst „Ich halte fest: … — richtig?“ und Eikes Ja in späterer Äußerung vor Vorgaben, Arbeitsweisen und Nachrichten an Personen, verhörte Namen werden zu „Meinst du …?“. Warum und Grenzen: `docs/LEHREN.md`, 12.10.2026 |
| `kern/anliegen/` | Anliegen: `fach.py` (Ablage `anliegen.db`, Auswahl, Verfall), `block.py` (Prompt-Block). Werkzeug `kern/werkzeuge/anliegen.py` (`anliegen_erledigt`); eingesetzt in `chat.antworten`, `live._anweisung` und den Personen-Prompts |
| `kern/dienste/netz.py` | Gemeinsamer HTTP-Client mit Wiederholung des Verbindungsaufbaus (`client()`) und `nicht_erreichbar()`, das die Ursache eines Verbindungsfehlers ins Log schreibt. Spotify und Schulmanager nutzen es |
| `kern/werkzeuge/lernen.py`, `buecher.py` | Werkzeuge `lernen` (ein Fach: Aktuelles, Noten, Muster, Material; Konnektor Gruppenordner) und `schulbuecher` (hochgeladene Bücher; Konnektor hausaufgaben). Nur lesen |
| `kern/dienste/whatsapp.py` | WhatsApp über den WAHA-Container: Sitzung/QR, Chatliste, Nachrichten — und die Freigabe je Chat, lesen und senden getrennt |
| `kern/dienste/snapchat.py`, `kern/dienste/snapchat_wache.py` | Snapchat: Auftrags-/Zustands-/Eingangsverwaltung über `data/snapchat/`, Wache speist Antworten ins Gespräch ein |
| `host-helfer/snap_helfer.py` | Der Helfer auf hphome: steuert das Handy per `adb` (senden über Director-Modus/Chat, Antworten per `uiautomator dump` erkennen). Dauerbetrieb über `host-helfer/snap_helfer_starten.sh` (cron, tmux-Sitzung `friday-snap-helfer`) |
| `kern/dienste/benachrichtigungen.py` | Der einzige Weg aufs Handy: eine Art je Anlass, ein Schalter je Art, Ruhezeit (`data/benachrichtigungen.json`). `push.py` bleibt die Technik darunter |
| `kern/dienste/wache.py` | „Abfahrten im Blick“: gemerkte Fahrten (`data/wachen.json`) und der Lauf, der bei Verspätung oder Ausfall meldet |
| `kern/dienste/personenstandort.py` | Standort der Familie: liest den Container `friday-personenstandort` (nur lesen, Schlüssel `PERSONENSTANDORT_SCHLUESSEL`) |
| `kern/dienste/standort.py` | Standort: Meldungen der App (nur RAM), beste Position, Ortsname über Nominatim, Abstand zum Wohnort |
| `kern/erinnerungen.py` | Erinnerungen und ihr Versandlauf |
| `kern/aufgabenblatt/`, `kern/werkzeuge/aufgabenblatt.py` | Aufgaben-PDFs (15.10.2026): `speicher` (Blätter + Aufträge, `data/aufgabenblatt.db`, PDFs in `data/aufgabenblatt/`), `lauf` (Hintergrundschleife), `ergebnis` (Push/Karte), `planer`, `ausarbeitung` (PDF zum Herunterladen); Werkzeuge `aufgaben_starten/lesen/abbrechen/pdf` |
| `kern/goodnotes/` | Goodnotes-Backup aus dem IServ-WebDAV: Abgleich, Abschriften, Volltextsuche |
| `kern/schule/`, `kern/postfach/`, `kern/chats/` | Ablagen und Hintergrundverarbeitung für Briefe, Mails, WhatsApp |
| `kern/garantie/profile.py` | Freunde-Profile der ÜSTRA-Garantie: prüfen, Abrechnung (Eikes Anteil, wer wem schuldet). Speicher in `garantie/ablage.py` (`data/garantie_profile.json`, 600) |
| `kern/garantie/uebersicht.py` | Garantie über alle Personen: Ampel und „noch frei“ je Person, Aufgaben über alle, für wen eine Fahrt geht. Nur lesen und zählen |
| `kern/garantie/fahrgastrechte.py` | Bahn-Fahrgastrechte: Regeln mit Quellen, Fälle erfassen und nachmessen, Sperren gegen die Garantie, Sammelanträge je Karte und Stelle, Erinnerungen. Schickt nichts ab |
| `kern/garantie/bahnablage.py`, `bahnformular.py` | Tabellen `bahnfaelle`/`bahnantraege` in `garantie.db`; das DB-Formular (`vorlagen/`) als PDF, Anschreiben, „Zum Abtippen“ für die S-Bahn |
| `kern/schule/noten.py` | Noten (Schule → Noten), Oberstufe Niedersachsen: Fächerwahl (P1–P5, Semester 12/1–13/2), Bereiche mit Gewichtung je Semester, Notenpunkte 0–15, Abitur-Schätzung. Eigene `data/noten.db`, rechnen rein in Python |
| `kern/schule/arbeiten.py` | Klassenarbeiten (Schule → Arbeiten): Upload (Fotos/PDF), Gemini liest Person, Fach, Datum, Note, Randnotizen und trägt die Note in `noten.py` ein; Seiten unter `data/noten/arbeiten/`. Freigabe „arbeiten“ (aus, bis eingeschaltet). Fremde Arbeiten (Person ≠ Eike, z. B. Kontakt F/Kontakt B) gehen nie in Eikes Noten; `muster_aktualisieren()` fasst wiederkehrende Fehler je Person+Fach zusammen (Tabelle `muster`) und speist sie ins Gedächtnis ein (22.09.2026, ohne Bestätigungs-Häkchen — Eike fragt mündlich) |
| `kern/studio/planer.py` | Tagesplanung für den Kanal; gerendert wird im Container `studio/` |
| `kern/studio/statistik.py` | Kanal- und Videostatistik für den Studio-Tab (YouTube Analytics/Data API, nur lesend, kurz zwischengespeichert) |
| `web/` | Die PWA, ein Skript je Seite, `sw.js` für Push (`fahrgastrechte.js`: der Bahn-Block, den `garantie.js` einhängt) |
| `kern/dashboard/` | Übersicht: `karten.py` (Anbieterliste `ANBIETER`, Konnektor-Sperre, Fehlerisolation), `meldungen.py`, `friday_karten.py`, `schule.py`, `musik.py`, `pc.py`, `pcmedien.py`, `jellyfin.py`; Werkzeug `kern/werkzeuge/dashboard.py`; Seite `web/uebersicht.js` |
| `web/pc.js`, `web/pcseite.js`, `pc-helfer/friday_pc.py` | PC (25.09.2026): `pc.js` = Rückfragen-Leiste und Einrichten, `pcseite.js` = die PC-Seite (Extras → PC, Reiter Übersicht/Wiedergabe/Fenster/Dateien/Bildschirm/Senden/Verlauf). Lesen über `/api/pc/lesen/<was>`, Handeln über `POST /api/pc/tun` (Helfer stuft ein: sicher sofort, bestätigen = Rückfrage, nie `bestaetigt` von der Seite). Der Helfer (Version 1.3.0) bleibt die einzige Stelle, die einstuft; er wird per Installer-Befehl aktualisiert, NICHT vom Server (Selbstupdate ist bewusst nicht gebaut: RCE-Fläche) |
| `kern/werkzeuge/bilder.py`, `kern/dienste/bildsuche.py` | Werkzeug `bild_zeigen` (25.09.2026): zeigt Bilder im Gespräch — aus dem Gedächtnis (`gedaechtnis/bilder.py: suchen`) oder aus dem Netz (Wikimedia Commons, ersatzweise Openverse; kostenlos, ohne Schlüssel). Der SERVER holt das Bild (Adresse als öffentlich geprüft, ≤ 4 MB, nur JPEG/PNG/WebP/GIF) und legt die Kopie nur in den RAM (100 Stück); der Browser bekommt sie von `/api/netzbild/<16 Hex>`, nie von einem fremden Server. Titel/Urheber sind fremder Text (`FREMDE_WERKZEUGE`, nur bei Quelle Internet). Die App zeigt sie als Bildblase (`Verlauf.bilder`, Aktion `bild` im Chat und im Live-Kanal) |
| `kern/werkzeuge/route.py`, `kern/dienste/routen.py` | Werkzeug `verbindung_karte` (25.09.2026): Wege zu einem Ziel als Karte auf der Übersicht (`art: route`): Bus & Bahn Tür zu Tür (GVH-HAFAS mit Koordinaten, `gvh.verbindungen_koordinaten`, mit Echtzeit), zu Fuß und Auto (Valhalla `pedestrian`/`auto`, NICHT OSRM). Start = Standort (nur mit Freigabe „Standort“, nie aus fremdem Text) oder genannter Ort; Ziel über `standort.ort_finden`. Auto ohne Verkehr, steht dabei. Karte antippen → Google Maps (App `comgooglemaps://`, sonst Browser) nur für Fuß/Auto; Bus/Bahn ohne Google-Link. **Live (26.09.2026):** `kern/dashboard/routelive.py` (Anbieter `route_live`, liefert selbst keine Karten) rechnet gelegte Verbindungskarten alle ~30 s neu, solange die Übersicht sie abfragt: Standort neu aus OwnTracks/App OHNE das Handy zu fragen, Abfahrten/Verspätungen neu, „angekommen“ bei < 100 m am Ziel, „nicht aktuell“ ohne brauchbare Zahlen nach 3 min; die Karte trägt dazu `auftrag` (Ziel, Start oder „mein Standort“, Verkehrsmittel, gewünschte Zeit) und `stand` |
| `host-helfer/handy_steuerung.py`, `host-helfer/handy_steuerung_web/` | Handy-Steuerung (16.10.2026): Samsung im Browser sehen und bedienen (scrcpy-server + eigener WebSocket, nur auf Zuruf über `host-helfer/handy_steuerung_starten.sh` oder den Knopf im hphome-Tab → Handy (Aktion `handy_start`), HTTPS-Port 8444 im Tailnet; beendet sich selbst nach 20 s ohne verbundene Seite, `HANDY_STEUERUNG_LEERLAUF_S`, 0 = nie); pausiert den Snap-Helfer über einen Herzschlag (`data/snapchat/handy_steuerung.json`, `snap_helfer.fernsteuerung_aktiv`). Kein Werkzeug fürs Modell. Fallen: `docs/LEHREN.md` (Handy-Steuerung) |
| `kern/dienste/claude_sitzung.py` | Claude-Code-Sitzungen: legt Aufträge in `data/claude/` ab, liest den Zustand des Helfers. Läuft der Helfer nicht, sagt die App das, statt zu warten |
| `kern/werkbank/`, `kern/werkzeuge/werkbank.py`, `werkbank-sandbox/`, `web/werkbank.js` | Werkbank (27.10.2026): Vorhaben-Liste, Störungen, Start/Lauf/Übernehmen, OpenCode-Sandbox mit Proxy, Karte in Extras → hphome |
| `kern/denken/deepseek.py` | DeepSeek (28.10.2026): als dritte Option der Denkrunde (`denken_backend` = deepseek; Übersetzer Gemini-Format ↔ OpenAI-Format, `/chat/completions`, Modell `deepseek-flash` (Eikes Wahl, nur Flash), Env `DEEPSEEK_MODELL`; gleiche Werkzeuge und Positivliste wie Gemini, `HALTUNG` = Eikes Wille zuerst) und als Werkbank-Backend `deepseek` (Claude-CLI im Helfer mit `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`, `--model sonnet` (= deepseek-flash), Rechte wie `claude`). Schlüssel NUR in `~/.config/friday/deepseek.key` (Host, 600; im Container schreibgeschützt unter `/run/friday-geheim/`), nie in `.env`. |
| `kern/chatstrang/speicher.py` | Chat-Strang (28.10.2026): einzige Wahrheit der Chat-Anzeige (`data/chatstrang.db`). „Chat leeren“ setzt nur `leer_ab`, nichts wird gelöscht; Kontext = Zeilen seit der Marke (max 40), Älteres holt Friday per `chat_verlauf_lesen/suchen`. Meldungen (Prüfung, Kontakte, Werkbank …) landen über `benachrichtigungen.senden(chat_text=…)` im Chat; Fremdmaterial steht im Kontext nur als Stub (`kontext_text`). |
| `host-helfer/claude_helfer.py` | Der Helfer auf hphome: kennt `starten`, `beenden` und (Werkbank) `werkbank` (headless im eigenen Worktree), `uebernehmen` und `pruefen` (nur lesend) (Merge per Knopf, nur `kern/`, `web/`, `test/`, `eval/szenarien/`, `docs/`; Konflikt nur in `docs/LEHREN.md` wird automatisch vereinigt), prüft jeden Namen, ruft tmux ohne Shell auf. Dauerbetrieb über `host-helfer/helfer_starten.sh` (cron, tmux-Sitzung `friday-helfer`) |
| `host-helfer/hphome_helfer.py`, `kern/dienste/hphome.py` | hphome-Tab (26.09.2026): Der Helfer auf hphome liest Host, Docker, Tailnet und führt genau acht feste Aktionen aus — darunter `bot_modell` (31.10.2026): stellt den Minecraft-Bot auf Gemini oder DeepSeek um, schreibt Modell und DeepSeek-Schlüssel in die Dateien DES BOTS und startet ihn neu; der Schlüssel kommt aus `~/.config/friday/deepseek.key`, nie aus dem Auftrag. Dateien in `data/hphome/`, Dauerbetrieb über `host-helfer/hphome_helfer_starten.sh`; `hphome.py` liest den Zustand, legt Aufträge ab und schwärzt Logs. Wache: `hphome_wache.py`, Werkzeug `server_status` (nur lesend) |
| `kern/personen/` | Kontakte: `register.py` (Personen, Persona, Rechte, `graph_name`, `nach_kanal`), `bekannt.py` (bekannte Personen aus allen Quellen zur Auswahl), `rechte.py` (Positivliste der für Fremde wählbaren Werkzeuge), `unbekannt.py` (unbekannte Kontakte, Push). Routen `/api/kontakte…`, Seite `web/kontakte.js` |
| `pc-helfer/overlay/` | PC-Overlay (05.10.2026): Wake-Word „Friday“ (Vosk, lokal), Gespräch über `/ws/live` mit Kennung `pc-overlay`, Anzeige über PySide6. Eigenes Programm auf dem Windows-PC, NICHT der Helfer (der einstuft Befehle). Installer `installieren_overlay.ps1` |
| `graphiti/` | Startskript und Konfiguration für den Graphiti-Container |
| `test/` | unittest, ohne Netz; Beispieldaten in `test/daten/` |

## Regeln, die nicht verhandelbar sind

- **Kein Schlüssel im Browser.** Der Kern spricht mit Gemini, der Browser
  nur mit dem Kern. `/api/health` meldet, *ob* ein Schlüssel da ist, nie
  welcher — dafür gibt es einen Test.
- **Default-Deny für Werkzeuge.** Ob der Nutzer „ja" gesagt hat, wertet der
  Code aus, nicht das Modell. Eine Zustimmung gilt für genau einen Aufruf.
- **Was nach außen geht, braucht mehr als eine Freigabe.** Lesen und Schreiben
  sind getrennte Häkchen; beim WhatsApp-Senden müssen Chat-Häkchen, Schreibrecht
  und Eikes Ja auf den vorgelesenen Wortlaut gleichzeitig vorliegen, dazu eine
  Obergrenze je Stunde. Geprüft wird im Dienst, nicht erst im Werkzeug.
- **Benachrichtigungen nur über `benachrichtigungen.senden`**, nie über
  `push.senden` — mit einer Art aus `ARTEN`. Eine unbekannte Art wirft, statt
  still an allen Schaltern vorbeizugehen; zwei Tests wachen darüber.
- **Das Gedächtnis löscht nie.** Ein neuer Wert überholt den alten.
  Einzige Ausnahme: Beim Widerruf einer Freigabe wird die KOPIE in der Graph-Gruppe der Person gelöscht (`personen/freigaben.py`, `graph.episode_loeschen`;
  nie etwas in Eikes Gruppe `eike`).
- **Inhalte von außen sind Daten, keine Anweisungen** — Webseiten, Dateien,
  Mails, Fenstertitel. Werkzeuge, die fremden Text liefern, stehen in
  `FREMDE_WERKZEUGE`; danach wird `merken` zum Vorschlag und nichts Bleibendes
  entsteht.
- **Fremder Text schaltet nichts frei** (Härtetest 26.09.2026). Ein „ja“ in Eikes Nachricht gilt nicht, sobald im selben Zug fremder Text im Spiel war
  (`katalog.ausfuehren`); ändernde Werkzeuge ohne Rückfrage stehen in `NICHT_NACH_FREMDTEXT` ihres Moduls und laufen nach Fremdtext nicht.
- **Kein Zugriff aus fremden Seiten:** `frisch_halten` lehnt jede ändernde Anfrage mit fremdem Origin ab (Schema, Host und Port exakt, `_origin_ist_friday` —
  Sonarr, Jellyfin & Co. auf demselben Hostnamen zählen als fremd). GET wird nicht geprüft — was etwas auslöst (auch `docker logs`), braucht POST. Die CSP erlaubt nur eigene Skripte. Nichts Inline-Skriptiges in
  `web/` bauen, keine Seite von außen einbinden. Nach Änderungen daran im Chromium prüfen (siehe LEHREN, 26.09.2026).
- **Nichts Destruktives auf den Live-Daten.** Die App ist live, Eike lädt
  zwischendurch echte Daten hoch. Kein `DELETE`, `GRAPH.DELETE` oder `os.remove`
  auf `data/`; zum Probieren ein eigenes `FRIDAY_DATEN`, im Graphen die Gruppe
  `friday-test`. Vor jedem Eingriff sichern und Zähler vorher/nachher vergleichen.
- **Orientieren, nicht nachbauen.** Das alte Windows-Friday
  (`nesreduel/Friday`) ist Nachschlagewerk; seine `AGENTS.md` enthält viele
  bewährte Entscheidungen. Vor jedem Baustein erst prüfen, ob es ihn fertig
  gibt (Bibliothek, MCP-Server, Dienst).

## Arbeitsweise — was sich immer wieder bewährt hat

Jeder Punkt ist mindestens einmal schiefgegangen; die Fälle stehen in `docs/LEHREN.md`.

- **Erst messen, dann ändern.** Die Rohantwort einer API einmal ausgeben, Feldnamen
  nachsehen statt raten. Eine Prüfung ohne jede Ausgabe ist ein Fehler im Aufruf,
  kein Befund (`docker exec` braucht für Heredocs `-i`).
- **Nie blind den ersten Treffer nehmen.** Spotify lieferte den falschen Song, der GVH
  eine Haltestelle in Ludwigshafen, die Ortssuche fünf „Holtensen“. Treffer gegen den
  Wunsch prüfen; passt nichts, nachfragen statt raten.
- **„Geht nicht" heißt oft „kein Werkzeug".** Warteschlange und Stundenzeiten waren
  lesbar, es fehlte nur das Werkzeug. Erst die API prüfen, bevor eine Grenze in die
  Persona wandert — und eine Ehrlichkeitsregel wieder streichen, sobald die Grenze fällt.
- **Zustand eines laufenden Dienstes** nur über den Dienst ändern (Schnittstelle oder
  Neustart), nie über einen zweiten Prozess im selben Container.
- **Tests mit Gegenprobe:** Ein Regressionstest muss ohne den Fix rot werden. Bei
  Geheimnissen beide Seiten prüfen — was hinausgeht UND was auf der Platte liegenbleibt.
- **Echte API sparsam und nacheinander.** Jeder Aufruf kostet Kontingent von Eikes
  echtem Friday.

## Fallen, die schon Zeit gekostet haben

- **iOS gibt das Mikrofon nur über HTTPS frei.** Deshalb `tailscale serve`.
  Ohne das lädt die Seite normal, nur der Mikrofonknopf tut nichts.
- **Modellwahl:** `gemini-2.5-flash-native-audio-preview-12-2025` bricht
  mitten im Gespräch mit `CONTENT_TYPE_AUDIO is not supported` ab; das Alias
  `-latest` zeigte darauf. `gemini-3.1-flash-live-preview` lief in der
  Messung vom 09.09.2026 dreimal 5/5. Verfügbare Modelle über
  `GET /v1beta/models` prüfen, nicht raten — und gelistet heißt nicht nutzbar,
  nur ein Testaufruf beweist es.
- **Die wichtigste Prompt-Regel gehört ans Ende.** In der Mitte eines langen
  Prompts ging sie nachweislich unter.
- **Halb-Duplex ist Pflicht** (wie im alten Friday): solange Friday spricht,
  plus 0,6 s Nachlauf, geht kein Mikrofon-Audio raus. Mit `3.1-flash-live`
  hörte Friday am Handy sonst ihre eigene Stimme und antwortete sich selbst.
  Unterbrechen geht über den gedrückten Push-to-Talk-Knopf.
- **Push-to-Talk braucht `audioStreamEnd`.** Hört der Client einfach auf zu
  senden, wartet Gemini auf eine Stille, die nie kommt. Auch vor Text oder Bild
  mitten im Gespräch — sonst bleibt der Zug stumm.
- **`mediaChunks` ist bei `3.1-flash-live-preview` abgeschafft.** Audio geht als
  `realtimeInput.audio`, im Takt (100 ms pro Paket) — alles auf einmal ergab
  `1008 policy violation`. Tests müssen den Takt nachbilden.
- **Sitzungsfortsetzung:** `sessionResumption` im Setup, das Handle aus
  `sessionResumptionUpdate` merken, bei Abbruch damit neu verbinden.
- **Der Schlüssel geht als Header `x-goog-api-key`**, nicht in der URL —
  so kann er in keiner Fehlermeldung landen.
- **Uhrzeit:** Gemini hat eine eigene Uhr in UTC und übergeht Zeitangaben im
  Prompt. Deshalb das Werkzeug `uhrzeit` (`kern/werkzeuge/basis.py`) und der
  Versatz (MESZ, UTC+2) in der Zeitangabe, bei jedem Sitzungsstart neu berechnet.
- **Werkzeuge im Live-Format:** `toolCall.functionCalls[{id,name,args}]`, Antwort als
  `toolResponse.functionResponses[{id,name,response}]` — die id muss mit.
- **Werkzeuge im Chat:** das Modellteil mit dem `functionCall` unverändert
  zurückgeben, es trägt eine `thoughtSignature`. Die Folgeanfrage bleibt
  beim selben Modell. Der letzte Durchgang läuft ohne `tools`, damit das Modell
  antworten MUSS.
- **Dateien immer als UTF-8 speichern.** Ein Windows-Editor mit ANSI machte
  aus einem Umlaut ein ungültiges Byte — Container in Neustartschleife.
- **Links nur auf öffentliche Adressen** (`kern/dienste/web.py`, geprüft vor dem
  Abruf und nach jeder Weiterleitung) — sonst fragt ein Link Router oder Tailnet ab.
- **`[hidden]` gegen `display:`** — eine Klasse mit `display: flex` sticht das
  Attribut aus. Global gilt `[hidden] { display: none !important; }`.
- **Web Push an Apple** braucht eine `mailto:`-Kontaktangabe mit echter Domain; mit
  `.local` kam auf jeden Versuch `403 BadJwtToken`. Die Antwort des Push-Dienstes
  immer mitloggen.
- **Datenschutz:** Im EWR gelten für die kostenlose Gemini-Stufe die
  Datenbedingungen der bezahlten (kein Training). Das gilt **nicht** für Groq
  oder OpenRouter-`:free`-Modelle.

## Betrieb

- Sieben Container: `friday-snapmem` (Snapchat-Memories, ohne Netz, Aufträge als Dateien über `data/snapmem/`), `friday` (intern 8080, auf dem Host `127.0.0.1:8124`), `friday-waha` (WhatsApp, nur intern),
  `friday-graphiti`, `friday-falkordb` und `friday-embedder` (nur im internen Netz), `friday-studio`
  (ohne Docker-Socket; Aufträge laufen als Dateien über `data/studio/`).
- HTTPS: `tailscale serve --bg 8124` → https://hphome.beispiel.ts.net/
- Daten in `./data` (nicht im Repo), Zustandsdateien mit Rechten 600. Sicherung
  jede Nacht nach `data/sicherung/`.
- **Gemini-Schlüssel je Zweck, jeder aus einem eigenen Google-Projekt** (Limits
  gelten pro Projekt): `GEMINI_API_KEY` Chat und Sprache, `RESERVE_GEMINI_API_KEY`
  Reserve für den Chat, `GRAPH_GEMINI_API_KEY` Graphiti, `HINTERGRUND_GEMINI_API_KEY`
  Briefe/Mails/Verläufe mit `GRAPHITI_GEMINI_API_KEY` als Reserve, `LOESEN_GEMINI_API_KEY`
  Hausaufgaben lösen (getrennt vom Einlesen) mit `LOESEN_RESERVE_GEMINI_API_KEY` als
  Reserve dazu und `HAUSAUFGABEN_GEMINI_API_KEY` als Reserve für beides. Schulbücher
  indexieren mit `BUECHER_GEMINI_API_KEY`. Neue Schlüssel
  vor dem Eintragen gegen die vorhandenen vergleichen.
  **Schlüssel-Pool (22.09.2026):** Zusätzlich ein wachsender Vorrat in `data/gemini_pool.json`
  (Chat/Sprechen darf jeden nehmen, Hintergrund die letzten 2 nicht — feste Reserve).
  Auf Zuruf („Jetzt anlegen", Einstellungen) oder automatisch bei knapper Reserve legt
  `host-helfer/schluessel_helfer.py` auf hphome (gcloud, einmalig angemeldet, Dauerbetrieb
  per `schluessel_helfer_starten.sh` über cron) ein neues Google-Cloud-Projekt samt Schlüssel
  an. Der Helfer schaltet auf denselben Weg auch den Graphiti-Container (`GRAPH_GEMINI_API_KEY`
  in `.env`, Neustart) auf einen freien Pool-Schlüssel um, wenn seiner am Tageslimit ist —
  vorher gab's nur die Pause bis zum Reset. Details: `docs/superpowers/specs/2026-09-22-gemini-schluessel-pool-design.md`.
  **Nach jeder Codeänderung am Helfer läuft er neu starten** (`tmux kill-session -t
  friday-schluessel-helfer` + `schluessel_helfer_starten.sh`) — der laufende Prozess
  kennt sonst nur den alten Stand.
- **Werkbank-Docker (28.10.2026, Eikes Entscheidung):** Der Claude-Worker darf andere Container über `host-helfer/werkbank_docker.py` ansehen und steuern
  (`ps`, `logs`, `inspect`, `start`/`stop`/`restart`, `exec`) — alle außer Immich und Fridays eigenem Stack (Compose-Projekt `friday-web`). Die Sperre steht im Skript,
  `docker` selbst ist in der Worker-Liste ausdrücklich verboten (`Bash(docker:*)`). Jeder Aufruf steht in `data/werkbank/docker-aufrufe.log`.
- **Werkbank:** Der Helfer braucht nach Codeänderung einen Neustart (`tmux kill-session -t friday-helfer; host-helfer/helfer_starten.sh`). Worktrees liegen unter `…/friday-web-werkbank/<id>`. Der Sandbox-Dienst läuft nur im Profil `werkbank` (`docker compose --profile werkbank …`); das Image ist gebaut (opencode-ai@1.19.4). Vorher als Eike `mkdir -p data/werkbank/sandbox` (der Mount legt den Ordner bewusst nicht an, sonst gehört er root).
- Weitere Einstellungen in `.env`: `FRIDAY_ORT` (Wetter), `FRIDAY_HEIMADRESSE` (Standort: „zu Hause“), `WAHA_SCHLUESSEL` und `WHATSAPP_HINTERGRUND` (WhatsApp), `BRIEFING_UHRZEIT`,
  `PUSH_KONTAKT`, `ISERV_URL`, `HAUSAUFGABEN_GEMINI_API_KEY`, `GOODNOTES_GEMINI_API_KEY`, `GOODNOTES_ORDNER`; `FRIDAY_HINTERGRUND=aus` schaltet alle Hintergrundläufe ab.
- Tests vor jedem Commit, **mit `--build`** — `run` nimmt sonst das alte Image,
  und die Tests prüfen den alten Code:
  `docker compose run --rm --build -e FRIDAY_HINTERGRUND=aus friday python -m unittest discover -s test -t . -v`
  Was `./data` nicht berühren darf, läuft besser über `scripts/test-isoliert.sh` (Wegwerf-Container, Temp-Daten, kein Netz); `scripts/eval-isoliert.sh` misst das ECHTE Gemini-Verhalten (Eval-Harness, 12.10.2026: Szenarien in `eval/szenarien/`, Werkzeuge durch Attrappe ersetzt, Netz nur für Gemini, nimmt den Hintergrund-Schlüssel, nie Eikes Hauptschlüssel; Ergebnisse in `eval/ergebnisse/`, kostet Kontingent, nur Chat-Kanal); `scripts/browser-pruefen.mjs` prüft eine Seite im Chromium samt CSP-/Konsolenmeldungen.

## Pflegehinweis

- Neue Entscheidungen und Vorfälle in **`docs/LEHREN.md`** unten anhängen — mit Datum
  und Begründung, nicht nur dem Ergebnis. Die Begründung ist das, was ein späterer
  Helfer braucht, um sie nicht versehentlich rückgängig zu machen.
- Hier nur nachziehen, was sich am Überblick ändert: „Stand", „Wo liegt was",
  eine neue Regel oder Falle, die für ALLE Bausteine gilt.
