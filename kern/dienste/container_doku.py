"""Welche Container es auf hphome gibt und wie Friday sie steuert — der feste Bestand als Nachschlagewerk.

Die ausführliche Fassung für Menschen steht in `docs/CONTAINER.md`. Hier steht derselbe Bestand
maschinenlesbar, damit (a) `test/test_container_doku.py` ihn gegen `docker-compose.yml`, `hphome.DIENSTE`
und `host-helfer/werkbank_docker.py` prüfen kann und (b) Friday ihn über `GET /api/hphome/doku` lesen
kann. Es sind nur Beschreibungen — keine Schlüssel, keine Zugangsdaten, keine Live-Werte.

Abgrenzung: `kern/dienste/hphome.py` beschreibt den *lebenden* Zustand (was der Helfer auf hphome gerade
meldet, inklusive fremder Container). Hier steht das *feste Wissen* über die Container, die zu Friday
gehören oder die er regelmäßig anspricht. Kommt ein Container dazu, wird er hier UND in
`docs/CONTAINER.md` eingetragen; der Test wacht darüber, dass keiner fehlt.

Warum eine eigene Datei unter `kern/` und nicht nur die Markdown-Datei: Der Test-Container bindet
`docs/` nicht ein (`scripts/test-isoliert.sh`), `kern/` schon. So prüft die Suite den Bestand auch dort,
wo nur der Code liegt.
"""

from __future__ import annotations

STAND = "2026-10-29"

# Die Wege, auf denen überhaupt etwas mit einem Container geschieht. Ein Eintrag nennt nur die Wege,
# die für IHN gelten; die Einzelheiten stehen in docs/CONTAINER.md.
WEGE: dict[str, str] = {
    "hphome": ("Eike in der App (Extras → hphome): neustart, stopp, start und logs je Container — "
               "ausgeführt vom Helfer auf hphome (`host-helfer/hphome_helfer.py`)."),
    "compose": ("Auf dem Host: `docker compose …` (bauen, hochfahren, stoppen, Profile). Nur Eike bzw. der "
                "Claude-Helfer — nie ein Container selbst."),
    "helfer": ("Der Claude-Helfer auf hphome (`host-helfer/claude_helfer.py`): baut/ersetzt Friday über "
               "`docker compose up -d --build friday`, sichert vorher das laufende Image."),
    "werkbank": ("Der Werkbank-Worker über `host-helfer/werkbank_docker.py` (ps, logs, inspect, start, stop, "
                 "restart, exec). Immich und das Compose-Projekt `friday-web` lehnt das Skript ab."),
    "datei": ("Nur über Auftragsdateien in `data/`: Der Container hat keinen Docker-Zugriff und wird von "
              "Friday nicht direkt gesteuert, sondern liest seinen Auftrag."),
}

# `stack` gruppiert nur die Herkunft: "friday-web" = diese docker-compose.yml (Projekt `friday-web`),
# "friday-personenstandort" = eigener Stack auf hphome, "hphome" = andere Dienste auf hphome (nicht in
# diesem Repo), "immich" = der Immich-Stack (für den Werkbank-Worker gesperrt).
# `stoppbar` False heißt: Die App bietet „stoppen“ nicht an (NICHT_STOPPEN in hphome.py und im Helfer).
# `werkbank_gesperrt` True heißt: `werkbank_docker.py` lehnt den Container ab.

CONTAINER: tuple[dict, ...] = (
    {
        "name": "friday",
        "titel": "Friday (Kern)",
        "stack": "friday-web",
        "zweck": ("Der FastAPI-Kern: Sprechen (`/ws/live`), Chat, Werkzeuge, Gedächtnis, Planer; liefert auch "
                  "die PWA aus."),
        "netz": ("Im Container 8080, auf dem Host nur `127.0.0.1:8124`; HTTPS ausschließlich über "
                 "`tailscale serve --bg 8124`. Im Compose-Netz `friday-web_default`, darüber erreicht er "
                 "waha, graphiti, falkordb, embedder und `friday-personenstandort`."),
        "daten": ("./data ist /app/data (Gedächtnis, Ablagen, Zustand); ~/media-data/downloads (rw) und "
                  "~/media-data/media (ro); ~/.config/friday ist /run/friday-geheim (ro, DeepSeek-Schlüssel)."),
        "wege": ("hphome", "compose", "helfer"),
        "werkbank_gesperrt": True,
        "stoppbar": False,
        "besonderheit": ("Gehärtet: read_only, cap_drop ALL, no-new-privileges, 3 GB, 512 Prozesse, kein "
                         "Docker-Socket. In der App nur neu starten/starten/logs — stoppen ist gesperrt, "
                         "sonst gäbe es keine Oberfläche mehr, um ihn zu starten. Der Claude-Helfer baut und "
                         "ersetzt ihn; für den Werkbank-Worker ist er Teil des gesperrten Projekts."),
    },
    {
        "name": "friday-waha",
        "titel": "WhatsApp (WAHA)",
        "stack": "friday-web",
        "zweck": ("WhatsApp als REST-Schnittstelle (WAHA, Maschine GOWS). Friday liest Chats, wenn Eike fragt; "
                  "gesendet wird nur mit Freigabe je Chat."),
        "netz": "Nur im internen Compose-Netz, Port 3000 nicht veröffentlicht.",
        "daten": "./data/waha (die Kopplung; überlebt einen Neubau).",
        "wege": ("hphome", "compose"),
        "werkbank_gesperrt": True,
        "stoppbar": True,
        "besonderheit": ("Schlüssel `WAHA_SCHLUESSEL` aus .env. Nach einem Neubau ohne ./data/waha müsste Eike "
                         "neu per QR koppeln. Ob gesendet werden darf, entscheidet Friday (Freigabe je Chat), "
                         "nicht der Container."),
    },
    {
        "name": "friday-falkordb",
        "titel": "FalkorDB (Graphdatenbank)",
        "stack": "friday-web",
        "zweck": "Graphdatenbank für das Langzeitgedächtnis (Graphiti).",
        "netz": "Nur im internen Compose-Netz, Port 6379.",
        "daten": "./data/graph (der Wissensgraph).",
        "wege": ("hphome", "compose"),
        "werkbank_gesperrt": True,
        "stoppbar": True,
        "besonderheit": ("Healthcheck über `redis-cli ping`; Browser-Oberfläche aus (`BROWSER=0`). Graphiti "
                         "startet erst, wenn der Healthcheck gesund ist."),
    },
    {
        "name": "friday-embedder",
        "titel": "Lokaler Embedder",
        "stack": "friday-web",
        "zweck": ("text-embeddings-inference auf hphome: rechnet Embeddings für Graphiti, ohne Tageskontingent; "
                  "nichts verlässt den Server."),
        "netz": "Nur im internen Compose-Netz, Port 8080.",
        "daten": ("./data/embedder (Modell-Cache; das Modell lädt beim ersten Start, einige hundert MB)."),
        "wege": ("hphome", "compose"),
        "werkbank_gesperrt": True,
        "stoppbar": True,
        "besonderheit": ("Standardmodell `intfloat/multilingual-e5-small` (`EMBEDDER_LOKAL_MODELL`), 3 GB "
                         "Speicher. Graphiti nutzt ihn erst mit `EMBEDDER_PROVIDER_GRAPHITI=openai`."),
    },
    {
        "name": "friday-graphiti",
        "titel": "Graphiti (Langzeitgedächtnis)",
        "stack": "friday-web",
        "zweck": "Wissensgraph als MCP-Server: Friday legt Episoden ab und fragt Wissen ab.",
        "netz": "Nur im internen Compose-Netz, Port 8000 (`/mcp`).",
        "daten": ("Der Graph liegt in falkordb (./data/graph); `graphiti/config.yaml` und "
                  "`graphiti/friday_start.py` sind read-only eingehängt."),
        "wege": ("hphome", "compose", "helfer"),
        "werkbank_gesperrt": True,
        "stoppbar": True,
        "besonderheit": ("Wartet auf falkordb (healthy), `SEMAPHORE_LIMIT=1`, Telemetrie aus. Der "
                         "Schlüssel-Helfer kann den Graphiti-Schlüssel in .env umschalten und startet dafür "
                         "`docker compose up -d graphiti` (der Container liest .env neu)."),
    },
    {
        "name": "friday-studio",
        "titel": "Studio (YouTube)",
        "stack": "friday-web",
        "zweck": "Rendert Videos für den YouTube-Kanal aus Auftragsdateien (ffmpeg, eigene Abhängigkeiten).",
        "netz": "Kein Netz nötig; bewusst kein Docker-Socket.",
        "daten": ("./data/studio (/studio: Aufträge rein, fertige Videos raus); zusätzlich die vier "
                  "Arbeitsordner assets/temp, assets/audio_clips, assets/video_clips, assets/final."),
        "wege": ("hphome", "compose", "datei"),
        "werkbank_gesperrt": True,
        "stoppbar": True,
        "besonderheit": ("Läuft als Eike (UID/GID aus .env), `STUDIO_ZEITLIMIT=3600`. Friday legt Aufträge "
                         "als Datei ab und liest das Ergebnis; der Container selbst hat keinen Docker-Zugriff."),
    },
    {
        "name": "friday-snapmem",
        "titel": "Snapchat-Memories",
        "stack": "friday-web",
        "zweck": ("Führt Snapchat-Memories zusammen und filtert sie (ffmpeg, OpenCV, ONNX-Modell), ohne Netz."),
        "netz": "`network_mode: none` — kein Netz, nichts geht nach außen.",
        "daten": "./data/snapmem (/snapmem: Aufträge, Fortschritt, Ergebnis).",
        "wege": ("hphome", "compose", "datei"),
        "werkbank_gesperrt": True,
        "stoppbar": True,
        "besonderheit": ("4 GB Speicher, läuft als Eike. Friday spricht nur über `data/snapmem/auftraege` und "
                         "die Routen `/api/snapmem…` mit ihm."),
    },
    {
        "name": "friday-werkbank-sandbox",
        "titel": "Werkbank-Sandbox (OpenCode)",
        "stack": "friday-web",
        "zweck": ("Führt Werkbank-Aufträge mit OpenCode aus. Startet nur mit "
                  "`docker compose --profile werkbank …`."),
        "netz": ("Nur im internen Netz `werkbank-sandbox` (kein Weg nach draußen oder ins Host-LAN); der "
                 "einzige Ausgang ist friday-werkbank-proxy mit Positivliste."),
        "daten": ("./data/werkbank/sandbox ist /auftrag; je Auftrag liegt dort <id>/in.json, der Läufer "
                  "schreibt out.json daneben. docs/, AGENTS.md und CLAUDE.md sind read-only unter /projekt "
                  "eingehängt."),
        "wege": ("hphome", "compose", "datei"),
        "werkbank_gesperrt": True,
        "stoppbar": True,
        "besonderheit": ("read_only, cap_drop ALL, 1 GB, 256 Prozesse; keine .env, keine Schlüssel, außer dem "
                         "Auftragsordner kein data/, kein Docker. Vor dem ersten Start muss "
                         "`data/werkbank/sandbox` als Eike existieren (`create_host_path: false`)."),
    },
    {
        "name": "friday-werkbank-proxy",
        "titel": "Werkbank-Ausgang (Proxy)",
        "stack": "friday-web",
        "zweck": "Einziger Ausgang der Sandbox: HTTP-CONNECT-Proxy mit Positivliste (opencode.ai, models.dev, nur 443).",
        "netz": "Hängt am internen Netz `werkbank-sandbox` und am normalen Netz `werkbank-aussen`.",
        "daten": "Keine Mounts, keine Geheimnisse.",
        "wege": ("hphome", "compose"),
        "werkbank_gesperrt": True,
        "stoppbar": True,
        "besonderheit": ("Nutzt dasselbe Image wie die Sandbox (`pull_policy: never`, also nur lokal gebaut), "
                         "256 MB Speicher. Startet wie die Sandbox nur im Profil `werkbank`."),
    },
    {
        "name": "friday-personenstandort",
        "titel": "Personenstandort (OwnTracks)",
        "stack": "friday-personenstandort",
        "zweck": ("Speichert je Familienmitglied den letzten selbst gemeldeten Standort (OwnTracks). Friday "
                  "liest nur und nur auf Frage."),
        "netz": ("Eigener Stack (`~/docker/friday-personenstandort`), hängt in `friday-web_default`; Port "
                 "8131 ist nicht veröffentlicht. Empfang (Tailnet) und Lesen (internes Netz) sind getrennt."),
        "daten": ("Nicht in diesem Repo. Je Person nur der letzte Punkt (kein Verlauf), nach 30 Tagen weg; "
                  "Passwörter nur als Hash."),
        "wege": ("hphome", "werkbank"),
        "werkbank_gesperrt": False,
        "stoppbar": True,
        "besonderheit": ("Friday liest über `PERSONENSTANDORT_URL` (Standard "
                         "`http://friday-personenstandort:8131`) und `PERSONENSTANDORT_SCHLUESSEL`, Werkzeug "
                         "`wo_ist`. Sein Compose-Projekt ist NICHT `friday-web`, darum darf der Werkbank-Worker "
                         "ihn steuern."),
    },
    {
        "name": "jellyfin",
        "titel": "Jellyfin",
        "stack": "hphome",
        "zweck": "Medienserver für Filme und Serien; Friday zeigt die Karte und kann die Wiedergabe steuern.",
        "netz": "Port 8096 auf hphome.",
        "daten": "Nicht in diesem Repo dokumentiert.",
        "wege": ("hphome", "werkbank"),
        "werkbank_gesperrt": False,
        "stoppbar": True,
        "besonderheit": "Konnektor `jellyfin` (schreibbar); Titel und Link kommen aus `hphome.DIENSTE`.",
    },
    {
        "name": "sonarr",
        "titel": "Sonarr",
        "stack": "hphome",
        "zweck": "Verwaltet Serien; Friday sucht und lädt Serien über den Dienst.",
        "netz": "Port 8989 auf hphome.",
        "daten": "Nicht in diesem Repo dokumentiert.",
        "wege": ("hphome", "werkbank"),
        "werkbank_gesperrt": False,
        "stoppbar": True,
        "besonderheit": "Konnektor `sonarr`; Titel und Link kommen aus `hphome.DIENSTE`.",
    },
    {
        "name": "radarr",
        "titel": "Radarr",
        "stack": "hphome",
        "zweck": "Verwaltet Filme; Friday sucht und lädt Filme über den Dienst.",
        "netz": "Port 7878 auf hphome.",
        "daten": "Nicht in diesem Repo dokumentiert.",
        "wege": ("hphome", "werkbank"),
        "werkbank_gesperrt": False,
        "stoppbar": True,
        "besonderheit": "Konnektor `radarr`; Titel und Link kommen aus `hphome.DIENSTE`.",
    },
    {
        "name": "prowlarr",
        "titel": "Prowlarr",
        "stack": "hphome",
        "zweck": "Stellt Sonarr und Radarr die Suchquellen bereit.",
        "netz": "Port 9696 auf hphome.",
        "daten": "Nicht in diesem Repo dokumentiert.",
        "wege": ("hphome", "werkbank"),
        "werkbank_gesperrt": False,
        "stoppbar": True,
        "besonderheit": "Nur Anzeige und App-Steuerung; kein eigenes Friday-Werkzeug.",
    },
    {
        "name": "qbittorrent",
        "titel": "qBittorrent",
        "stack": "hphome",
        "zweck": "Downloads für den Medien-Tab (läuft direkt auf dem Host, nicht im Docker-Netz von Friday).",
        "netz": "Port 8080 auf hphome; Friday erreicht ihn über `host.docker.internal`.",
        "daten": "Nicht in diesem Repo dokumentiert.",
        "wege": ("hphome", "werkbank"),
        "werkbank_gesperrt": False,
        "stoppbar": True,
        "besonderheit": "Konnektor `qbittorrent`; Download-Ordner ~/media-data/downloads ist in Friday rw eingehängt.",
    },
    {
        "name": "portainer",
        "titel": "Portainer",
        "stack": "hphome",
        "zweck": "Docker-Oberfläche im Browser.",
        "netz": "Port 9443 (HTTPS) auf hphome.",
        "daten": "Nicht in diesem Repo dokumentiert.",
        "wege": ("hphome", "werkbank"),
        "werkbank_gesperrt": False,
        "stoppbar": True,
        "besonderheit": "Selbstsigniertes Zertifikat, die Browser-Warnung ist normal. Kein Friday-Werkzeug.",
    },
    {
        "name": "adguardhome",
        "titel": "AdGuard Home",
        "stack": "hphome",
        "zweck": "DNS-Filter im Heimnetz.",
        "netz": "Port 80 auf hphome.",
        "daten": "Nicht in diesem Repo dokumentiert.",
        "wege": ("hphome", "werkbank"),
        "werkbank_gesperrt": False,
        "stoppbar": True,
        "besonderheit": "Stoppen unterbricht den DNS-Filter im Heimnetz — nur bewusst.",
    },
    {
        "name": "immich_server",
        "titel": "Immich",
        "stack": "immich",
        "zweck": "Fotos und Videos ansehen und sichern.",
        "netz": "Port 2283 auf hphome.",
        "daten": "Nicht in diesem Repo dokumentiert (Immich-eigene Volumes); Fotos sind privat.",
        "wege": ("hphome",),
        "werkbank_gesperrt": True,
        "stoppbar": True,
        "besonderheit": ("Für den Werkbank-Worker gesperrt: `werkbank_docker.py` lehnt jeden Namen mit "
                         "`immich` und jedes Compose-Projekt `immich` ab. Die App darf ihn weiter neu "
                         "starten/stoppen/logs zeigen."),
    },
    {
        "name": "homeassistant",
        "titel": "Home Assistant",
        "stack": "hphome",
        "zweck": "Smarthome-Zentrale.",
        "netz": "Port 8123 auf hphome, nur im Tailnet erreichbar.",
        "daten": "Nicht in diesem Repo dokumentiert.",
        "wege": ("hphome", "werkbank"),
        "werkbank_gesperrt": False,
        "stoppbar": True,
        "besonderheit": "Kein Friday-Werkzeug; nur Anzeige und App-Steuerung.",
    },
    {
        "name": "heimdall",
        "titel": "Heimdall",
        "stack": "hphome",
        "zweck": "Startseite mit Verknüpfungen zu den Diensten.",
        "netz": "Port 8090 auf hphome.",
        "daten": "Nicht in diesem Repo dokumentiert.",
        "wege": ("hphome", "werkbank"),
        "werkbank_gesperrt": False,
        "stoppbar": True,
        "besonderheit": "Kein Friday-Werkzeug; nur Anzeige und App-Steuerung.",
    },
)


def namen() -> list[str]:
    """Alle dokumentierten Containernamen in der Reihenfolge dieser Datei."""
    return [c["name"] for c in CONTAINER]


def eintrag(name: str) -> dict | None:
    """Der Eintrag zu einem Containernamen — oder `None`, wenn er nicht dokumentiert ist."""
    for c in CONTAINER:
        if c["name"] == name:
            return c
    return None
