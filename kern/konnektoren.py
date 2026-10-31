"""Konnektoren — Verbindungen zu fremden Diensten, jede einzeln freigegeben.

Default-Deny (Entscheidung aus dem alten Friday, B-04): ein Konnektor ist
aus, bis Eike ihn auf der Freigaben-Seite einschaltet. Ausgeschaltet bekommt
Gemini seine Werkzeuge gar nicht erst angeboten — und ruft das Modell sie
trotzdem auf, lehnt der Katalog ab.

Zugangsdaten liegen in `data/zugaenge.json` (Rechte 600). Sie gehen nie
zurück an den Browser und nie ans Modell: Werkzeuge bekommen keine
Zugangsdaten als Angabe, der Dienst holt sie sich hier selbst.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from kern import config

KONNEKTOREN: dict[str, dict] = {
    "schulmanager": {
        "name": "Schulmanager",
        "beschreibung": "Stundenplan mit Vertretungen und Ausfällen, Hausaufgaben und Elternbriefe — "
                        "über deine IServ-Anmeldung.",
        "zugang": "IServ-Benutzername und Passwort",
        "benutzer_feld": "IServ-Benutzername",
        "passwort_feld": "IServ-Passwort",
        "hilfe_text": "IServ öffnen",
        "werkzeuge": {
            "stundenplan": "Stundenplan für einen Tag oder eine Woche, mit Räumen, Lehrkräften, "
                           "Vertretungen und Ausfällen",
            "hausaufgaben": "Hausaufgaben der letzten Tage",
            "elternbriefe": "Elternbriefe — schon beim Ankommen gelesen und zusammengefasst, samt PDFs, "
                            "Bildern und Word-Dateien",
            "elternbrief": "Einen Elternbrief ganz: Zusammenfassung, Termine, Fristen, was zu tun ist, Kosten",
            "schultermine": "Was als Nächstes ansteht — Termine und Fristen aus allen Briefen",
        },
        "hinweis": "Neue Elternbriefe verarbeitet Friday im Hintergrund, sobald sie ankommen (alle 30 Minuten "
                   "zwischen 6 und 22 Uhr, mit dem zweiten Gemini-Schlüssel). Friday liest nur —sie kann im Schulmanager nichts ändern, nichts abschicken und "
                   "nichts bestätigen. Angemeldet wird genau einmal; weist IServ die Daten ab, "
                   "probiert Friday sie nicht noch einmal, damit das Schulkonto nicht gesperrt wird.",
    },
    "hausaufgaben": {
        "name": "Hausaufgaben bearbeiten",
        "beschreibung": "Friday bearbeitet Hausaufgaben aus dem Schulmanager mit deinen eingescannten Schulbüchern "
                        "und zeigt das Ergebnis im Tab Extras → HA.",
        "zugang": "keine — die Bücher lädst du im Tab Extras → HA hoch",
        "ohne_zugang": True,
        "hilfe_url": "https://ai.google.dev/gemini-api/terms",
        "hilfe_text": "Gemini-Nutzungsbedingungen",
        "werkzeuge": {},
        "hinweis": "Braucht zusätzlich die Freigabe „Schulmanager“. Ist das hier an, liest Friday hochgeladene "
                   "Bücher einmal durch (welche Buchseite liegt wo) und bearbeitet neue Hausaufgaben der letzten "
                   "7 Tage von selbst, die sie für machbar hält — sonst nur auf deinen Knopfdruck. Dafür gehen die "
                   "Aufgabe und einzelne Buchseiten als Bild an Gemini (Schlüssel BUECHER_GEMINI_API_KEY bzw. "
                   "LOESEN_GEMINI_API_KEY, sonst der Hintergrund-Schlüssel). Friday gibt nichts ab und schreibt nichts in den "
                   "Schulmanager.",
    },
    "arbeiten": {
        "name": "Klassenarbeiten auswerten",
        "beschreibung": "Du lädst eine korrigierte Klassenarbeit hoch (Fotos oder PDF). Friday liest Fach, Datum, Note und "
                        "die Randnotizen der Lehrkraft, trägt die Note ein und merkt sich, was du beim nächsten Mal "
                        "verbessern kannst. Alles liegt gesammelt in Schule → Arbeiten.",
        "zugang": "keine — die Arbeiten lädst du in Schule → Arbeiten hoch",
        "ohne_zugang": True,
        "hilfe_url": "https://ai.google.dev/gemini-api/terms",
        "hilfe_text": "Gemini-Nutzungsbedingungen",
        "werkzeuge": {},
        "hinweis": "Ist das hier aus, legt Friday hochgeladene Arbeiten nur ab (du trägst die Angaben selbst ein, "
                   "und kannst sie als PDF wieder herunterladen). Ist es an, gehen die Seiten der Arbeit — auch mit "
                   "deiner Handschrift und den Anmerkungen der Lehrkraft — als Bild an Gemini (Schlüssel "
                   "ARBEITEN_GEMINI_API_KEY, sonst der Hausaufgaben-Schlüssel). Friday trägt die erkannte Note nur ein, "
                   "wenn Fach, Semester und Note eindeutig sind; sonst fragt sie dich. Sie gibt nichts ab.",
    },
    "goodnotes": {
        "name": "Goodnotes-Hefte",
        "beschreibung": "Deine Goodnotes-Hefte aus dem IServ-Backup (WebDAV) — Seite für Seite abgeschrieben, "
                        "auch die Handschrift, und durchsuchbar. Frag Friday nach dem Unterrichtsstoff.",
        "zugang": "IServ-Anmeldung",
        "zugang_von": "schulmanager",
        "hilfe_text": "IServ öffnen",
        "werkzeuge": {
            "notizen_suchen": "In allen Heften suchen, mit Heft, Seite und Textstelle",
            "notizseite": "Eine Heftseite ganz vorlesen",
            "notizhefte": "Welche Hefte es gibt und was zuletzt drinsteht",
        },
        "hinweis": "Friday liest nur: Sie listet den Ordner GoodNotes im IServ-WebDAV (alle 30 Minuten) und lädt "
                   "neue oder geänderte PDFs. Nur Seiten, die sich verändert haben, gehen als Bild an Gemini "
                   "(Schlüssel GOODNOTES_GEMINI_API_KEY) und werden abgeschrieben. Ordner, die Friday nicht "
                   "lesen soll, schließt du im Tab Extras → HA aus. Beim Bearbeiten von Hausaufgaben schaut "
                   "Friday auch in die Hefte (z. B. nach Arbeitsblättern). Sie schreibt nichts nach IServ.",
    },
    "iserv_gruppenordner": {
        "name": "IServ-Gruppenordner",
        "beschreibung": "Die Kursordner im IServ-WebDAV (Arbeitsblätter, Folien, Dokumente je Fach) — gelesen, "
                        "zusammengefasst und durchsuchbar, fürs Lernen an einem Ort.",
        "zugang": "IServ-Anmeldung",
        "zugang_von": "schulmanager",
        "hilfe_text": "IServ öffnen",
        "werkzeuge": {},
        "hinweis": "Friday liest nur: Sie listet den Gruppenordner im IServ-WebDAV (alle 30 Minuten), Unterordner "
                   "= Kurse. PDFs mit eingebettetem Text liest sie direkt (ohne Gemini); Scans, Bilder, "
                   "PowerPoint- und Word-Dateien gehen an Gemini (Schlüssel BUECHER_GEMINI_API_KEY, sonst der "
                   "Hausaufgaben-Schlüssel) zum Lesen und Zusammenfassen. Nicht unterstützte Dateien werden nur "
                   "gelistet. Sie schreibt nichts nach IServ.",
    },
    "iserv_mail": {
        "name": "IServ-Postfach",
        "beschreibung": "Deine IServ-Mails — gespeichert, durchsuchbar und beim Ankommen vorbereitet, "
                        "samt PDFs, Bildern und Word-Dateien.",
        "zugang": "IServ-Anmeldung",
        "zugang_von": "schulmanager",
        "hilfe_text": "IServ öffnen",
        "werkzeuge": {
            "mails": "Mails suchen und auflisten, mit Kurzfassung",
            "mail": "Eine Mail ganz: Zusammenfassung, Termine, Fristen, was zu tun ist, ob eine Antwort erwartet wird",
            "mail_termine": "Was als Nächstes ansteht — Termine und Fristen aus den Mails",
        },
        "hinweis": "Friday liest nur: über IMAP, Ordner schreibgeschützt geöffnet — sie markiert nichts als "
                   "gelesen, verschiebt, löscht oder sendet nichts. Alle Mails werden gespeichert und "
                   "durchsuchbar; mit Gemini vorbereitet werden neue und die der letzten 30 Tage, ältere "
                   "sobald Friday sie öffnet. Entwürfe, Papierkorb und Spam bleiben draußen.",
    },
    "jellyfin": {
        "name": "Jellyfin",
        "beschreibung": "Die Übersicht zeigt, was gerade auf Jellyfin läuft — mit Vorschaubild, Fortschritt und Steuerung.",
        "zugang": "Adresse von Jellyfin und ein API-Schlüssel",
        "benutzer_feld": "Adresse (aus Friday heraus, z. B. http://172.17.0.1:8096)",
        "passwort_feld": "API-Schlüssel",
        "hilfe_url": "https://jellyfin.org/docs/",   # https ist Pflicht (Test); den Schlüssel gibt es im eigenen Jellyfin
        "hilfe_text": "Jellyfin-Dokumentation (Schlüssel: Dashboard → API-Schlüssel)",
        "werkzeuge": {},
        "schreibbar": True,
        "schreiben_text": "Wiedergabe steuern erlauben (Pause, weiter, zurück)",
        "hinweis": "Friday liest nur die laufenden Sitzungen und holt das Vorschaubild; sie ändert nichts an "
                   "deiner Bibliothek. Der Schlüssel steht in Jellyfin unter Dashboard → API-Schlüssel "
                   "(„+“, Name „Friday“). Die Adresse muss vom Friday-Container aus erreichbar sein.",
    },
    "medien": {
        "name": "Medien (Serien & Filme)",
        "beschreibung": "Friday kann in der Mediathek suchen (Größe, was da ist) und auf deine Bitte Serien und Filme herunterladen "
                        "sowie Serien, Filme, Staffeln oder Folgen überall löschen.",
        "zugang": "keine — nutzt die Zugänge von Sonarr, Radarr und qBittorrent",
        "ohne_zugang": True,
        "hilfe_url": "https://wiki.servarr.com/",
        "hilfe_text": "Servarr-Dokumentation (Sonarr/Radarr)",
        "werkzeuge": {
            "medien_suchen": "Serien und Filme suchen, Größe nennen",
            "medien_herunterladen": "Serie oder Film herunterladen (in Sonarr/Radarr anlegen, Suche starten)",
            "medien_deutsch_holen": "Film zusätzlich auf Deutsch holen (Multi-Fassung suchen, ersetzt nur, wenn besser)",
            "medien_loeschen": "Löschen vorbereiten (fragt zurück, löscht nichts)",
            "medien_loeschen_bestaetigen": "Nach deinem Ja endgültig löschen",
        },
        "schreibbar": True,
        "schreiben_text": "Herunterladen und Löschen erlauben (Löschen nur nach Rückfrage und deinem Ja)",
        "hinweis": "Braucht die Zugänge von Sonarr und Radarr (und qBittorrent fürs Aufräumen der Downloads). Der Medien-Tab "
                   "(Extras → hphome) funktioniert unabhängig davon.",
    },
    "sonarr": {
        "name": "Sonarr",
        "beschreibung": "Serien verwalten — Friday kann Duplikate aufräumen und Serien überall korrekt löschen.",
        "zugang": "Adresse von Sonarr und ein API-Schlüssel",
        "benutzer_feld": "Adresse (aus Friday heraus, z. B. http://host.docker.internal:8989)",
        "passwort_feld": "API-Schlüssel",
        "hilfe_url": "https://wiki.servarr.com/sonarr/settings#general",
        "hilfe_text": "Sonarr-Einstellungen (Schlüssel: Settings → General → Security → API Key)",
        "werkzeuge": {},
        "hinweis": "Nur über den Medien-Unter-Tab (Extras → hphome) erreichbar, kein Werkzeug fürs Modell.",
    },
    "radarr": {
        "name": "Radarr",
        "beschreibung": "Filme verwalten — Friday kann Duplikate aufräumen und Filme überall korrekt löschen.",
        "zugang": "Adresse von Radarr und ein API-Schlüssel",
        "benutzer_feld": "Adresse (aus Friday heraus, z. B. http://host.docker.internal:7878)",
        "passwort_feld": "API-Schlüssel",
        "hilfe_url": "https://wiki.servarr.com/radarr/settings#general",
        "hilfe_text": "Radarr-Einstellungen (Schlüssel: Settings → General → Security → API Key)",
        "werkzeuge": {},
        "hinweis": "Nur über den Medien-Unter-Tab (Extras → hphome) erreichbar, kein Werkzeug fürs Modell.",
    },
    "qbittorrent": {
        "name": "qBittorrent",
        "beschreibung": "Downloads — Friday entfernt beim Löschen einer Serie/eines Films den passenden Torrent.",
        "zugang": "qBittorrent-WebUI-Nutzername und Passwort",
        "benutzer_feld": "Nutzername",
        "passwort_feld": "Passwort",
        "hilfe_url": "https://github.com/qbittorrent/qBittorrent/wiki/WebUI-Access",
        "hilfe_text": "qBittorrent-WebUI-Dokumentation",
        "werkzeuge": {},
        "hinweis": "Die Adresse ist fest (derselbe Host wie Friday) — nur Nutzername und Passwort eintragen. "
                   "Nur über den Medien-Unter-Tab (Extras → hphome) erreichbar, kein Werkzeug fürs Modell.",
    },
    "spotify": {
        "name": "Spotify",
        "beschreibung": "Friday sieht, was gerade läuft — Titel, Künstler, Album und wie weit es ist.",
        "zugang": "Client ID und Client Secret aus dem Spotify-Entwicklerbereich",
        "benutzer_feld": "Client ID",
        "passwort_feld": "Client Secret",
        "hilfe_url": "https://developer.spotify.com/dashboard",
        "hilfe_text": "Spotify-Entwicklerbereich öffnen",
        "werkzeuge": {
            "musik": "Sagt, was gerade auf Spotify läuft",
            "musik_pause": "Pause, fortsetzen, nächster und vorheriger Titel (nur mit Schreibrecht)",
            "warteschlange": "Titel auf die Warteschlange setzen, auch passend zu einem Thema",
        },
        "schreibbar": True,
        "schreiben_text": "Wiedergabe steuern erlauben (Pause, weiter, Warteschlange)",
        "hinweis": "Einrichtung: auf developer.spotify.com eine App anlegen, dort die Rückadresse "
                   "eintragen (steht unten), dann Client ID und Secret hier speichern und auf "
                   "„Mit Spotify verbinden“ tippen. Auf deinen Wunsch fordert Friday alle Rechte an, "
                   "damit du später nichts neu erlauben musst — benutzen kann sie nur, wofür es ein "
                   "Werkzeug gibt, und das ist bislang nur „was läuft gerade“ (lesend).",
    },
    "icloud_kalender": {
        "name": "iCloud-Kalender",
        "beschreibung": "Deine Apple-Termine — Friday sieht nach, was ansteht, und rechnet sie in "
                        "„Was habe ich morgen vor?“ mit ein.",
        "zugang": "Apple-ID und ein app-spezifisches Passwort",
        "benutzer_feld": "Apple-ID (E-Mail)",
        "passwort_feld": "App-spezifisches Passwort (xxxx-xxxx-xxxx-xxxx)",
        "hilfe_url": "https://account.apple.com/account/manage",
        "hilfe_text": "App-spezifisches Passwort erstellen",
        "werkzeuge": {
            "kalender": "Termine für einen Tag, eine Woche oder den kommenden Monat",
            "termin_anlegen": "Einen Termin vorschlagen — eingetragen wird erst nach deinem Ja",
        },
        "schreibbar": True,
        "schreiben_text": "Termine eintragen erlauben (Friday fragt vorher immer nach)",
        "hinweis": "Friday liest nur — sie legt keine Termine an, ändert und löscht keine. Wichtig: Apple "
                   "verlangt ein app-spezifisches Passwort (appleid.apple.com → Anmeldung und Sicherheit → "
                   "App-spezifische Passwörter). Das normale Apple-Passwort funktioniert nicht.",
    },
    "studio": {
        "name": "Studio — YouTube-Kanal",
        "beschreibung": "Friday plant, baut und veröffentlicht Videos für den Kanal Beispielkanal — "
                        "ein Video am Tag, und hochgeladen wird erst nach deiner Freigabe.",
        "zugang": "Client ID und Client Secret aus der Google Cloud Console",
        "benutzer_feld": "Client ID",
        "passwort_feld": "Client Secret",
        "hilfe_url": "https://console.cloud.google.com/apis/credentials",
        "hilfe_text": "Google Cloud Console öffnen",
        "werkzeuge": {
            "studio_stand": "Was der Kanal macht: Aufrufe, Abonnenten, wie die letzten Videos liefen",
            "studio_wartet": "Welche Videos auf deine Freigabe warten",
            "studio_hochladen": "Ein freigegebenes Video veröffentlichen (nur mit Schreibrecht und deinem Ja)",
            "studio_verwerfen": "Ein Video verwerfen und neu bauen lassen",
        },
        "schreibbar": True,
        "schreiben_text": "Videos hochladen erlauben (Friday fragt vorher immer nach)",
        "hinweis": "Einrichtung: in der Google Cloud Console einen OAuth-Client vom Typ „Web“ anlegen, "
                   "als Rückadresse die unten genannte eintragen, dann Client ID und Secret hier "
                   "speichern und auf „Mit YouTube verbinden“ tippen. Friday plant und rendert "
                   "selbstständig ein Video pro Tag und meldet sich, wenn es fertig ist — "
                   "veröffentlicht wird NIE ohne deine ausdrückliche Zustimmung. "
                   "Die Bilder sind echte Stockaufnahmen (Pexels, Pixabay), nicht KI-erzeugt, damit der "
                   "Kanal monetarisierbar bleibt; dass Skript und Stimme aus KI stammen, wird in der "
                   "Beschreibung und im YouTube-Feld für synthetische Inhalte offengelegt.",
    },
    "gvh": {
        "name": "GVH — Bus und Bahn",
        "beschreibung": "Fahrpläne im Großraum Hannover: nächste Abfahrten und Verbindungen von "
                        "Haltestelle zu Haltestelle, inklusive Umland.",
        # Erster Konnektor ohne Anmeldung: Die GVH-Auskunft ist offen zugänglich.
        "zugang": "keine — die GVH-Auskunft ist offen",
        "ohne_zugang": True,
        "hilfe_url": "https://www.gvh.de/",
        "hilfe_text": "GVH-Fahrplanauskunft öffnen",
        "werkzeuge": {
            "haltestellen_hier": "Die nächsten Haltestellen um deinen Standort, mit Luftlinie",
            "abfahrten": "Nächste Abfahrten von einer Haltestelle, mit Linie und Richtung",
            "verbindung": "Verbindung von A nach B, mit Umstiegen und Fußwegen",
        },
        "hinweis": "Friday liest nur — buchen oder reservieren kann sie nichts. Abfahrten kommen mit "
                   "ECHTZEIT aus der ÜSTRA-Abfahrtstafel (Prognose und Verspätung, auch für S-Bahnen) "
                   "sowie Störungsmeldungen. Verbindungen von A nach B und Abfahrten außerhalb der "
                   "Region kommen vom GVH — mit dessen Echtzeit, wo er eine meldet (meist für die "
                   "nächsten Fahrten). Wo keine kommt, sagt Friday, dass es Fahrplanzeiten sind. Steige "
                   "kennt sie nicht. Zugangsdaten brauchst du keine.",
    },
    "wetter": {
        "name": "Wetter",
        "beschreibung": "Wettervorhersage für heute und die nächsten sechs Tage — für deinen Wohnort oder "
                        "jeden anderen Ort.",
        "zugang": "keine — Open-Meteo ist offen",
        "ohne_zugang": True,
        "hilfe_url": "https://open-meteo.com/",
        "hilfe_text": "Open-Meteo öffnen",
        "werkzeuge": {
            "wetter": "Vorhersage mit Temperatur, Regenrisiko und ab wann es regnet",
        },
        "hinweis": "Friday liest nur. Ohne Ortsangabe gilt dein Wohnort (FRIDAY_ORT in der .env). Gleichnamige "
                   "Orte grenzt Friday über die Gemeinde ein („Holtensen, Musterhausen“) und nennt immer, welchen "
                   "Ort sie gefunden hat. Ist das Wetter freigegeben, steht es auch im Morgenbriefing "
                   "(Einstellungen). Zugangsdaten brauchst du keine.",
    },
    "hphome": {
        "name": "hphome (Server)",
        "beschreibung": "Friday kann Auskunft über den Server geben: wie voll die Platte ist, welche Dienste laufen, "
                        "wer im Tailnet online ist, wann die letzte Sicherung war.",
        "zugang": "keine — der Helfer auf hphome liefert die Werte",
        "ohne_zugang": True,
        "hilfe_url": "https://tailscale.com/kb/",
        "hilfe_text": "Tailscale-Dokumentation",
        "werkzeuge": {
            "server_status": "Platte, Auslastung, Dienste, Tailnet-Geräte und Sicherungsstand",
        },
        "hinweis": "Nur lesen. Neustart, Stopp, Aufräumen und Logs der übrigen Dienste gibt es nur per Tipp in der "
                   "App (Extras → hphome), nie für Friday im Gespräch — der Minecraft-Bot hat dafür eigene Werkzeuge "
                   "(Freigabe „Minecraft-Bot“). Der Tab selbst braucht diese Freigabe nicht.",
    },
    "minecraft_bot": {
        "name": "Minecraft-Bot (Mindcraft)",
        "beschreibung": "Friday sieht, was der Minecraft-Bot auf hphome tut: Containerzustand, Protokoll und — mit "
                        "MindServer-Adresse — welche Bots im Spiel sind, wo sie stehen und was sie im Inventar haben. "
                        "Auf deine Bitte startet, stoppt oder startet sie ihn neu und gibt einem Bot eine Aufgabe im Spiel.",
        "zugang": ("keine — der Bot läuft als Container auf hphome; die MindServer-Adresse liest Friday aus der "
                   "Port-Zuordnung des Containers (oder aus MINECRAFT_BOT_URL in .env)"),
        "ohne_zugang": True,
        "hilfe_url": "https://github.com/kolbytn/mindcraft",
        "hilfe_text": "Mindcraft (GitHub)",
        "werkzeuge": {
            "minecraft_bot_status": "Containerzustand, Bots im Spiel und ihr Live-Ort",
            "minecraft_bot_logs": "Die letzten Zeilen aus dem Protokoll des Bots",
            "minecraft_bot_inventar": "Was ein Bot im Spiel bei sich hat",
            "minecraft_bot_aufgabe": "Einem Bot eine Aufgabe im Spiel geben",
            "minecraft_bot_steuern": "Starten, stoppen oder neu starten (nur diese drei)",
        },
        "schreibbar": True,
        "schreiben_text": ("Den Minecraft-Bot starten, stoppen, neu starten und ihm Aufgaben im Spiel geben erlauben "
                           "(nur auf deine Bitte)"),
        "hinweis": "Friday hat keinen eigenen Docker-Zugang: Alles läuft über den Helfer auf hphome, der auch die "
                   "übrigen Dienste steuert. Gestartet, gestoppt und neu gestartet wird nur auf deine ausdrückliche "
                   "Bitte und nur mit der Positivliste start/stopp/neustart; Aufgaben bekommt ein Bot nur auf deine "
                   "Bitte. Jede Aktion landet in data/minecraft_bot/aktionen.jsonl. Findet Friday weder eine "
                   "Port-Zuordnung noch MINECRAFT_BOT_URL, sagt der Status das ehrlich — dann ist nur der Container "
                   "sichtbar, nicht das Spielgeschehen. Ohne "
                   "Schreibrecht kann Friday nur nachsehen (Status, Protokoll, Inventar). Das Modell des Bots "
                   "stellst du hier um: Gemini (bisheriger Stand) oder DeepSeek über deinen vorhandenen "
                   "Schlüssel — dabei wird der Bot neu gestartet und ist kurz aus dem Spiel.",
    },
    "standort": {
        "name": "Standort",
        "beschreibung": "Wo du gerade bist — Friday kann es sagen, wenn du fragst („Wo bin ich?“, „Bin ich "
                        "schon in Musterhausen?“).",
        "zugang": "keine — die App deines Handys meldet die Position",
        "ohne_zugang": True,
        "hilfe_url": "https://operations.osmfoundation.org/policies/nominatim/",
        "hilfe_text": "Nutzungsregeln des Ortsdienstes",
        "werkzeuge": {
            "standort": "Sagt Straße und Ort, wie alt und wie genau die Angabe ist und wie weit es von "
                        "deinem Zuhause ist",
            "entfernung": "Luftlinie von dir zu einem Ort oder einer Adresse (keine Gehstrecke)",
        },
        "hinweis": "Erst wenn du das hier einschaltest — und dann nur in dem Moment, in dem du Friday nach "
                   "deinem Standort fragst — bittet die App den Browser um deine Position, und der Browser "
                   "fragt dich, ob du zustimmst. Nicht beim Öffnen und nicht im Takt. Auf dem iPhone fragt "
                   "die installierte App jedes Mal neu; das lässt sich nicht abstellen. Die Koordinaten "
                   "liegen NUR im Arbeitsspeicher des Servers, nie auf der Platte; "
                   "nach einem Neustart sind sie weg. Nur wenn du fragst, schlägt Friday den Ortsnamen bei "
                   "OpenStreetMap (Nominatim) nach — dorthin gehen die Koordinaten. Ist FRIDAY_HEIMADRESSE "
                   "gesetzt, wird auch diese Adresse einmal dort nachgeschlagen (damit sie „zu Hause“ sagen "
                   "kann, sonst nur den Ort). Sie nennt Straße, "
                   "keine Hausnummer, und gibt den Standort nie aus fremdem Text (Mail, Webseite) heraus. "
                   "Eine Erinnerung „wenn ich nach Hause komme“ liest im Hintergrund nur die schon gemeldete "
                   "Position (und OwnTracks oder Home Assistant, falls freigegeben) — sie fragt das Handy dabei nie. "
                   "Was sie dir antwortet, steht wie jede Antwort im Gesprächsprotokoll. Ausschalten "
                   "genügt: die App hört dann sofort auf zu melden.",
    },
    "personenstandort": {
        "name": "Standort der Familie",
        "beschreibung": "Wo Familienmitglieder zuletzt waren („Wo ist Kontakt P?“) — nur, wenn sie ihren Standort "
                        "selbst per OwnTracks-App melden.",
        "zugang": "keine — die Familie meldet selbst; Personen legst du unten in diesem Eintrag an",
        "ohne_zugang": True,
        "hilfe_url": "https://owntracks.org/booklet/features/http/",
        "hilfe_text": "OwnTracks: HTTP-Modus",
        "werkzeuge": {
            "wo_ist": "Sagt, wo eine Person zuletzt war: Ort, wie lange her, wie genau (ohne Namen: alle)",
        },
        "hinweis": "Läuft in einem eigenen Container (friday-personenstandort), getrennt von Friday. Jede Person "
                   "richtet OwnTracks selbst ein, kann jederzeit abschalten und hat ein eigenes Passwort; "
                   "gespeichert wird nur der LETZTE Punkt je Person (nach 30 Tagen weg), kein Verlauf. Friday "
                   "liest nur, wenn du fragst (oder solange eine Erinnerung auf deine Ankunft zu Hause wartet), und nie "
                   "aus fremdem Text (Mail, Webseite). Um den Ortsnamen zu "
                   "nennen, gehen die Koordinaten der Person an OpenStreetMap (Nominatim) — wie beim eigenen "
                   "Standort. Personen anlegen, Passwörter ausgeben, pausieren und entfernen: unten in diesem Eintrag (Ein-Tipp-Einrichtung für OwnTracks).",
    },
    "homeassistant": {
        "name": "Home Assistant (Standort)",
        "beschreibung": "Friday liest deine Position aus Home Assistant — damit sie merkt, wann du zu Hause "
                        "ankommst („erinnere mich, wenn ich nach Hause komme“).",
        "zugang": "Adresse von Home Assistant und ein langlebiges Zugriffstoken",
        "benutzer_feld": "Adresse (aus Friday heraus, z. B. http://homeassistant:8123)",
        "passwort_feld": "Langlebiges Token",
        "hilfe_url": "https://www.home-assistant.io/docs/authentication/",
        "hilfe_text": "Home Assistant: Zugriffstoken anlegen",
        "werkzeuge": {},
        "hinweis": "Nur lesend: Friday fragt genau EINE Entität ab (HOMEASSISTANT_ENTITAET, Standard person.eike) "
                   "und schreibt nichts in Home Assistant. Die Position dient der Ankunftserkennung für "
                   "Erinnerungen (200 m weg = unterwegs, 75 m nah = zu Hause) und als Start der Verbindungskarte; "
                   "das Werkzeug „Standort“ fragt weiterhin die App. "
                   "Das Token erstellst du in Home Assistant unter Profil → Sicherheit → Langlebige Zugriffstoken "
                   "und trägst es hier als „Passwort“ ein — nie in die Adresse. Die Adresse muss vom "
                   "Friday-Container aus erreichbar sein.",
    },
    "whatsapp": {
        "name": "WhatsApp",
        "beschreibung": "Chats lesen, die du einzeln freigibst („was hat Kontakt P geschrieben?“). Läuft über "
                        "den WAHA-Container im internen Netz — Friday spricht nie direkt mit WhatsApp.",
        "zugang": "keine — gekoppelt wird per QR-Code aus WhatsApp",
        "ohne_zugang": True,
        "hilfe_url": "https://waha.devlike.pro/docs/overview/quick-start/",
        "hilfe_text": "WAHA-Anleitung öffnen",
        "schreibbar": True,
        "schreiben_text": "Antworten erlauben (nur in Chats mit dem zweiten Häkchen, und nur nachdem "
                          "Friday dir den Wortlaut vorgelesen hat und du zugestimmt hast)",
        "werkzeuge": {
            "whatsapp_chats": "Sagt, welche Chats freigegeben sind",
            "whatsapp_lesen": "Liest die letzten Nachrichten aus einem freigegebenen Chat",
            "whatsapp_antwort": "Bereitet eine Nachricht vor und liest sie dir vor — sendet noch nichts",
            "whatsapp_senden": "Schickt die vorbereitete Nachricht ab, nachdem du zugestimmt hast",
        },
        "hinweis": "Erst koppeln (QR-Code in WhatsApp unter Einstellungen → Verknüpfte Geräte), dann JEDEN "
                   "Chat einzeln ankreuzen. Ohne Häkchen liest Friday keinen einzigen — die Sperre sitzt im "
                   "Server, nicht im Modell. ANTWORTEN braucht drei Dinge zugleich: den Schalter „Antworten "
                   "erlauben“ hier, ein zweites Häkchen neben dem einzelnen Chat, und dein ausdrückliches Ja "
                   "auf den Wortlaut, den Friday dir vorliest. Höchstens 8 Nachrichten je Stunde. "
                   "Chatinhalte gelten als fremder Text, Friday merkt sich daraus nichts "
                   "von selbst. Gelesen wird live, gespeichert wird nur, WELCHE Chats frei sind. "
                   "ACHTUNG: WhatsApp erlaubt keine fremden Programme — das Koppeln verstößt gegen deren "
                   "Nutzungsbedingungen und kann im schlimmsten Fall zur Sperre der Nummer führen.",
    },
    "gmail": {
        "name": "Gmail",
        "beschreibung": "Dein Gmail-Postfach: Friday sucht und liest Mails, wenn du fragst — und gleicht "
                        "stündlich die Bescheide zu deinen ÜSTRA-Garantie-Anträgen ab.",
        "zugang": "Gmail-Adresse und ein App-Passwort von Google",
        "benutzer_feld": "Gmail-Adresse",
        "passwort_feld": "App-Passwort (16 Zeichen)",
        "hilfe_url": "https://myaccount.google.com/apppasswords",
        "hilfe_text": "App-Passwort anlegen",
        "werkzeuge": {
            "gmail_mails": "Mails suchen und auflisten (Gmail-Suche, ohne Suche der Posteingang)",
            "gmail_mail": "Eine Mail ganz: Absender, Betreff, Anhänge und Text",
        },
        "hinweis": "Nur lesend über IMAP: Friday markiert nichts als gelesen, verschiebt, löscht oder sendet nichts. "
                   "Gelesen wird live, wenn du fragst — gespeichert und durch Gemini geschickt wird nichts; "
                   "Spam und Papierkorb bleiben draußen. Im Hintergrund fragt Friday nur die Mails von "
                   "info@uestra.de ab (Garantie-Bescheide). Google verlangt ein App-Passwort (dafür muss die Bestätigung in zwei Schritten an sein); "
                   "das normale Passwort funktioniert nicht. Abgeglichen wird stündlich.",
    },
    "pc": {
        "name": "PC (Windows)",
        "beschreibung": "Friday bedient deinen Windows-PC: Programme starten, Dateien finden, öffnen, verschieben und "
                        "aufräumen, Fenster, Lautstärke, Sperre — und ein eigener Browser, in dem sie für dich "
                        "sucht, klickt und Formulare ausfüllt.",
        "zugang": "Adresse des PCs im Tailnet und ein Schlüssel — beides trägt „Einrichten“ unten selbst ein",
        "benutzer_feld": "Adresse des PCs (z. B. 100.64.0.20:8765)",
        "passwort_feld": "Schlüssel des Helfers",
        "hilfe_url": "https://tailscale.com/download/windows",
        "hilfe_text": "Tailscale für Windows",
        "werkzeuge": {
            "pc_status": "Ob der PC an ist, gesperrt, was vorne ist",
            "pc_programme": "Welche Programme es gibt",
            "pc_fenster": "Welche Fenster offen sind",
            "pc_dateien": "Dateien in den freigegebenen Ordnern ansehen und suchen",
            "pc_bestaetigungen": "Was auf deine Bestätigung wartet und was zuletzt passiert ist",
            "pc_starten": "Programme und Apps starten",
            "pc_datei_oeffnen": "Dateien und Ordner öffnen (Programme/Skripte nur nach Bestätigung)",
            "pc_verschieben": "Dateien verschieben — nach Bestätigung",
            "pc_umbenennen": "Umbenennen — nach Bestätigung",
            "pc_loeschen": "In den Papierkorb legen — nach Bestätigung",
            "pc_system": "Sperren, Lautstärke, Musik; Herunterfahren nach Bestätigung",
            "browser_oeffnen": "Im Friday-Browser Seiten öffnen, lesen, klicken und tippen",
        },
        "schreibbar": True,
        "schreiben_text": "Am PC handeln erlauben (Heikles — verschieben, löschen, herunterfahren, Kaufen- oder "
                          "Senden-Knöpfe — bestätigst du jedes Mal in der App)",
        "hinweis": "Auf dem PC läuft ein kleiner Helfer (Python), den „Einrichten“ mit einem Befehl installiert. Er hört "
                   "nur auf Friday (Tailnet-Adresse von hphome und Schlüssel) und kennt eine feste Liste von "
                   "Aktionen — keine Eingabeaufforderung, keine freien Befehle. Dateien nur in den freigegebenen "
                   "Ordnern (Desktop, Dokumente, Downloads, Bilder, Musik, Videos; weitere in config.json auf dem PC), "
                   "überschrieben wird nie, gelöscht wird in den Papierkorb. Heikles legt Friday dir als Karte in "
                   "die App — „Ausführen“ oder „Ablehnen“, zehn Minuten gültig; ein Ja im Gespräch reicht dafür "
                   "nicht. Der Friday-Browser ist ein eigenes Edge-Fenster mit eigenem Profil, ohne deine "
                   "Anmeldungen; Passwörter und Zahlungsdaten tippt Friday nie. Ohne Schreibrecht kann Friday nur "
                   "nachsehen.",
    },
    "uestra_garantie": {
        "name": "ÜSTRA-Garantie",
        "beschreibung": "Friday sucht Fahrten mit mehr als 20 Minuten Verspätung — Bus, Stadtbahn, S-Bahn — und "
                        "bereitet deinen Antrag vor. Du wählst Fahrt und Haltestellen im Tab Extras.",
        "zugang": "keine — deine Angaben und dein Ticket trägst du im Tab Extras → Garantie ein",
        "ohne_zugang": True,
        "hilfe_url": "https://www.uestra.de/service-und-mobilitaet/uestra-garantie/",
        "hilfe_text": "Bedingungen der ÜSTRA-Garantie",
        "schreibbar": True,
        "schreiben_text": "Anträge abschicken erlauben (nur auf deinen Knopfdruck in der App, nach Prüfung aller Angaben)",
        "werkzeuge": {
            "garantie_suchen": "Verspätete Stadtbahnen und Busse in deiner Gegend suchen",
            "garantie_fahrt": "Den Verlauf einer Fahrt mit allen Halten und Verspätungen zeigen",
            "garantie_vorbereiten": "Einen Antrag vorbereiten: Verspätung am Ziel, Frist und Höchstzahlen prüfen",
            "garantie_antraege": "Die vorbereiteten Anträge und wie viele schon abgegeben sind",
            "garantie_stand": "Offene Vorgänge mit Status, nächstem Schritt und Frist (nur lesen)",
            "garantie_status_setzen": "Deinen Bescheid oder „Geld ist da“ festhalten (schickt nichts ab)",
        },
        "hinweis": "Friday fragt dafür stündlich die Abfahrtstafeln der GVH-Auskunft in deinen Orten ab "
                   "(Musterdorf, Bennigsen, Musterhausen, Wennigsen, Hemmingen, Musterhausen, Pattensen, Empelde, Weetzen, "
                   "dazu einige Knoten), auf Knopfdruck sofort — findet sich nichts, auch die Nachbarorte und das "
                   "ganze Netz —, und das Garantie-Formular, wenn du eine Fahrt prüfst. Die Garantie gilt für alle "
                   "Fahrten mit ÜSTRA-Fahrausweis, auch S-Bahn; dein Ticket liest Friday beim Hochladen aus. "
                   "Abgeschickt wird nur, wenn „Anträge abschicken“ an ist UND du beim Antrag auf „Abschicken“ "
                   "drückst — vorher prüft Friday alle Angaben live beim Formular. Friday selbst (Sprache, Chat) "
                   "kann nichts abschicken.",
    },
    "fahrgastrechte": {
        "name": "Bahn-Fahrgastrechte",
        "beschreibung": "Entschädigung bei Zugverspätung — S-Bahn, RE/RB, metronom, enno, ICE: Friday sammelt die Fälle, "
                        "rechnet nach Tarif und bereitet den Antrag vor (Tab Extras → Garantie).",
        "zugang": "keine — Angaben und Ticket kommen aus der ÜSTRA-Garantie",
        "ohne_zugang": True,
        "hilfe_url": "https://www.bahn.de/service/informationen-buchung/fahrgastrechte",
        "hilfe_text": "Fahrgastrechte bei der Bahn",
        "werkzeuge": {},
        "hinweis": "Friday misst die Verspätung am Ziel mit der Echtzeit der GVH-Auskunft (wie bei der Garantie) und sucht "
                   "auf Wunsch Züge an einem Bahnhof, auch ICE und IC. Daraus werden Sammelanträge je Monatskarte und "
                   "Bahn oder einer je Einzelfahrkarte — als ausgefülltes DB-Formular (PDF) oder zum Abtippen für die "
                   "S-Bahn Hannover. Friday schickt NICHTS ab: Du druckst, unterschreibst (bei Freunden: sie selbst) "
                   "und reichst ein. IBAN und BIC stehen nie im PDF. Für dieselbe Fahrt gibt es entweder die "
                   "ÜSTRA-Garantie oder Fahrgastrechte, nie beides — Friday sperrt das in beide Richtungen.",
    },
    "snapchat": {
        "name": "Snapchat",
        "beschreibung": ("Friday kann dir spontan oder als Erinnerung einen echten Snap schicken "
                          "(mit einem Bild aus ihrem Gedächtnis) und auf deine Antwort normal weiterreden."),
        "zugang": "keine — die Anmeldung liegt auf dem angeschlossenen Handy, nicht in Friday",
        "ohne_zugang": True,
        "hilfe_url": "https://support.snapchat.com/",
        "hilfe_text": "Snapchat-Hilfe",
        "werkzeuge": {
            "schicke_snap": "Schickt dir einen echten Snap mit einem Bild (aus dem Gedächtnis oder dem Netz), auf Wunsch mit Text darauf",
            "schicke_nachricht": "Schreibt einer Kontakt-Person auf deine Bitte selbst formuliert eine Chat-Nachricht (ohne Vorlesen)",
        },
        "hinweis": ("Läuft über ein echtes Samsung-Handy per USB an hphome (kein Emulator, kein Root — "
                     "siehe Spec). Ohne Schreibrecht kann Friday dir keine Snaps schicken, nur deine Nachrichten "
                     "und Snaps lesen und im Chat darauf antworten."),
        # Der Schalter „Schreiben“ steht in den Details (Karte antippen). Er gilt für `schicke_snap`, `schicke_nachricht` (nicht für Antworten im Chat): Antworten
        # im Chat gehören zum normalen Gespräch und brauchen ihn nicht.
        "schreibbar": True,
        "schreiben_text": "Friday darf dir Snaps mit Bildern schicken (auf deine Bitte oder von selbst) und Kontakt-Personen auf deine Bitte direkt (ohne Vorlesen)",
    },
}

_sperre = threading.Lock()


def _datei(name: str):
    return config.daten_ordner() / name


def _lesen(name: str) -> dict:
    try:
        return json.loads(_datei(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _schreiben(name: str, daten: dict) -> None:
    """Erst in eine Nebendatei, dann umbenennen — nie eine halbe Datei. Immer 600."""
    ziel = _datei(name)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1)
    os.chmod(neu, 0o600)
    os.replace(neu, ziel)


def hilfe_url(kid: str) -> str:
    """Wo Eike die Zugangsdaten herbekommt. Bei IServ die Schuladresse aus der
    Umgebung, damit sie nicht doppelt gepflegt werden muss."""
    k = KONNEKTOREN.get(kid, {})
    if k.get("hilfe_url"):
        return k["hilfe_url"]
    if kid == "schulmanager" or k.get("zugang_von") == "schulmanager":
        return os.getenv("ISERV_URL", "https://schule.example/iserv").rstrip("/")
    return ""


def gibt_es(kid: str) -> bool:
    return kid in KONNEKTOREN


def aktiv(kid: str) -> bool:
    return _lesen("konnektoren.json").get(kid, {}).get("aktiv") is True


def setzen(kid: str, an: bool) -> None:
    with _sperre:
        d = _lesen("konnektoren.json")
        d.setdefault(kid, {})["aktiv"] = bool(an)
        _schreiben("konnektoren.json", d)


def schreiben_erlaubt(kid: str) -> bool:
    """Schreiben ist eine EIGENE Freigabe, getrennt vom Lesen — und standardmäßig aus."""
    return bool(kid) and _lesen("konnektoren.json").get(kid, {}).get("schreiben") is True


def schreiben_setzen(kid: str, an: bool) -> None:
    with _sperre:
        d = _lesen("konnektoren.json")
        d.setdefault(kid, {})["schreiben"] = bool(an)
        _schreiben("konnektoren.json", d)


def vermerken(kid: str, ok: bool, text: str) -> None:
    """Ergebnis der letzten Prüfung — für die Freigaben-Seite."""
    with _sperre:
        d = _lesen("konnektoren.json")
        d.setdefault(kid, {})["pruefung"] = {
            "ok": ok, "text": text[:300], "zeit": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _schreiben("konnektoren.json", d)


def zugang(kid: str) -> tuple[str, str] | None:
    # Dienste ohne Anmeldung haben keinen Zugang — auch dann nicht, wenn in
    # zugaenge.json noch etwas steht. Am 13.09.2026 lagen dort versehentlich
    # eingetragene Daten für den GVH, und die Freigaben-Seite zeigte sie an.
    if KONNEKTOREN.get(kid, {}).get("ohne_zugang"):
        return None
    if von := KONNEKTOREN.get(kid, {}).get("zugang_von"):
        return zugang(von)      # dieselbe Anmeldung, nur einmal eingetragen
    z = _lesen("zugaenge.json").get(kid) or {}
    if z.get("benutzer") and z.get("passwort"):
        return z["benutzer"], z["passwort"]
    if kid == "schulmanager":  # wie im alten Friday: auch aus der Umgebung
        b, p = os.getenv("ISERV_USER", "").strip(), os.getenv("ISERV_PASS", "").strip()
        if b and p:
            return b, p
    return None


def zugang_setzen(kid: str, benutzer: str, passwort: str) -> None:
    with _sperre:
        d = _lesen("zugaenge.json")
        d[kid] = {"benutzer": benutzer.strip(), "passwort": passwort}
        _schreiben("zugaenge.json", d)


def zugang_loeschen(kid: str) -> None:
    with _sperre:
        d = _lesen("zugaenge.json")
        d.pop(kid, None)
        _schreiben("zugaenge.json", d)


def benutzbar(kid: str) -> bool:
    # Manche Dienste brauchen keine Anmeldung — der GVH-Fahrplan ist offen.
    # Ohne diesen Fall wären sie nie benutzbar, ihre Werkzeuge nie sichtbar,
    # und die Freigaben-Seite meldete für immer „Zugangsdaten fehlen".
    if KONNEKTOREN.get(kid, {}).get("ohne_zugang"):
        return aktiv(kid)
    return aktiv(kid) and zugang(kid) is not None


def uebersicht() -> list[dict]:
    stand = _lesen("konnektoren.json")
    liste = []
    for kid, k in KONNEKTOREN.items():
        z = zugang(kid)
        liste.append({
            "id": kid, "name": k["name"], "beschreibung": k["beschreibung"], "hinweis": k["hinweis"],
            "zugangArt": k["zugang"],
            # Beschriftung der Eingabefelder je Konnektor — sonst stünde beim
            # Kalender „IServ-Benutzername" und der Passwortmanager böte das
            # falsche Passwort an.
            "benutzerFeld": k.get("benutzer_feld", "Benutzername"),
            "passwortFeld": k.get("passwort_feld", "Passwort"),
            # Immer ein Link dorthin, wo es die Zugangsdaten gibt (per Test erzwungen).
            "hilfeUrl": hilfe_url(kid),
            "hilfeText": k.get("hilfe_text", "Zugangsdaten holen"),
            "zugangVon": KONNEKTOREN[k["zugang_von"]]["name"] if k.get("zugang_von") else None,
            # Kein Anmeldeformular, kein „Zugangsdaten fehlen" — siehe benutzbar().
            "ohneZugang": bool(k.get("ohne_zugang")),
            "werkzeuge": [{"name": n, "text": t} for n, t in k["werkzeuge"].items()],
            "aktiv": aktiv(kid),
            "schreibbar": bool(k.get("schreibbar")),
            "schreiben": schreiben_erlaubt(kid),
            "schreibenText": k.get("schreiben_text", ""),
            # Nur der Benutzername — das Passwort verlässt den Server nie.
            "benutzer": z[0] if z else None,
            "pruefung": stand.get(kid, {}).get("pruefung"),
        })
    return liste
