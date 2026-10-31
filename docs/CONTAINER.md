# Container auf hphome — Bestand und Steuerung

Stand: **29.10.2026**. Diese Datei beantwortet drei Fragen: **Welche Container gibt es?** Wofür sind sie
da? **Wie steuert Friday sie — und wie nicht?** Sie ist damit das Nachschlagewerk für alles, was später
wieder einen Container anfassen will (Mensch, Werkbank-Worker, Prüf-Worker).

- **Lebender Zustand** (läuft/läuft nicht, gesund, Ports, Logs): die App unter *Extras → hphome*,
  `GET /api/hphome` — Quelle ist der Helfer auf hphome.
- **Fester Bestand** (was ist das überhaupt, wie darf man es steuern): diese Datei, maschinenlesbar in
  `kern/dienste/container_doku.py`, abrufbar über `GET /api/hphome/doku`.
- **Alle Container, auch fremde**: `docker ps -a` zeigt mehr als diese Datei. Die App listet **jeden**
  Container, den der Helfer sieht, und reichert nur die bekannten mit Titel und Link an. Was hier fehlt,
  ist deshalb nicht unsichtbar — nur nicht erklärt.

Gelesen wurde das aus `docker-compose.yml`, `kern/dienste/hphome.py`, `kern/dienste/container_doku.py`,
`kern/werkzeuge/hphome.py`, `host-helfer/hphome_helfer.py` und `host-helfer/werkbank_docker.py`.
Schlüssel und Zugangsdaten stehen **nirgends** in dieser Datei — nur, *wo* sie liegen.

---

## Auf einen Blick

„App“ = Eike in der App (Extras → hphome): neustarten, starten, stoppen, Logs ansehen.
„Compose“ = auf dem Host `docker compose …`. „Helfer“ = Claude-Helfer baut/ersetzt. „Worker“ = der
Werkbank-Worker über `werkbank_docker.py`. „Datei“ = nur Auftragsdateien in `data/`.

| Container | Stack | Wofür | Wege | Stoppen in der App | Worker |
|---|---|---|---|---|---|
| `friday` | friday-web | Der Kern: Sprechen, Chat, Werkzeuge, Gedächtnis | App, Compose, Helfer | **nein** | gesperrt |
| `friday-waha` | friday-web | WhatsApp über WAHA | App, Compose | ja | gesperrt |
| `friday-falkordb` | friday-web | Graphdatenbank für das Gedächtnis | App, Compose | ja | gesperrt |
| `friday-embedder` | friday-web | Lokale Embeddings für Graphiti | App, Compose | ja | gesperrt |
| `friday-graphiti` | friday-web | Langzeitgedächtnis (MCP) | App, Compose, Helfer | ja | gesperrt |
| `friday-studio` | friday-web | YouTube-Videos rendern | App, Compose, Datei | ja | gesperrt |
| `friday-snapmem` | friday-web | Snapchat-Memories filtern | App, Compose, Datei | ja | gesperrt |
| `friday-werkbank-sandbox` | friday-web | OpenCode-Sandbox (Profil `werkbank`) | App, Compose, Datei | ja | gesperrt |
| `friday-werkbank-proxy` | friday-web | Einziger Ausgang der Sandbox | App, Compose | ja | gesperrt |
| `friday-personenstandort` | friday-personenstandort | Letzter Standort der Familie | App, Worker | ja | **erlaubt** |
| `jellyfin` | hphome | Filme und Serien | App, Worker | ja | erlaubt |
| `sonarr` | hphome | Serien verwalten | App, Worker | ja | erlaubt |
| `radarr` | hphome | Filme verwalten | App, Worker | ja | erlaubt |
| `prowlarr` | hphome | Suchquellen für Sonarr/Radarr | App, Worker | ja | erlaubt |
| `qbittorrent` | hphome | Downloads | App, Worker | ja | erlaubt |
| `portainer` | hphome | Docker-Oberfläche | App, Worker | ja | erlaubt |
| `adguardhome` | hphome | DNS-Filter im Heimnetz | App, Worker | ja | erlaubt |
| `immich_server` | immich | Fotos und Videos | App | ja | **gesperrt** |
| `homeassistant` | hphome | Smarthome-Zentrale | App, Worker | ja | erlaubt |
| `heimdall` | hphome | Startseite mit Verknüpfungen | App, Worker | ja | erlaubt |

---

## Die fünf Steuerwege

### 1. Eike in der App (Extras → hphome)

Friday selbst hat **keinen** Docker-Zugriff (gehärteter Container, kein Socket). Die App legt deshalb
nur einen Auftrag als Datei ab; der Helfer auf hphome führt ihn aus.

| Was | Route | Grenze |
|---|---|---|
| Zustand aller Container, Host, Platte, Tailnet, Sicherung | `GET /api/hphome` | Nur lesen; ohne Helfer `helfer: false` |
| Logs eines Containers | `POST /api/hphome/container/<name>/logs` | Nur POST (ein GET wäre per `<img>` auslösbar), Ausgabe geschwärzt |
| Neustart / Stopp / Start | `POST /api/hphome/container/<name>/<neustart\|stopp\|start>` | Name muss im Zustand des Helfers stehen |
| Host neu starten | `POST /api/hphome/host/neustart` | Braucht die sudoers-Zeile; Wache schweigt 600 s |
| Platz schaffen | `POST /api/hphome/platz/schaffen` | Nur Build-Cache und Images älter als 24 h, **nie** Volumes |
| Handy-Steuerung starten | `POST /api/hphome/handy/start` | Kein Container; endet nach 20 s ohne verbundene Seite |
| Fester Bestand | `GET /api/hphome/doku` | Nur lesen, statisch (diese Datei als Daten) |

Der Weg im Einzelnen: `kern/dienste/hphome.py` schreibt `data/hphome/auftrag.json` (atomar, 600) und
wartet auf `letzte_antwort` in `data/hphome/zustand.json`. Der Helfer
`host-helfer/hphome_helfer.py` läuft in der tmux-Sitzung `friday-hphome-helfer` (cron `@reboot` und alle
5 Minuten als Wächter), nimmt den Auftrag per Umbenennen an, prüft den Namen gegen `docker ps -a`
(Muster `^[a-z0-9][a-z0-9_.-]{0,62}$`, exakt, keine ID, kein Muster) und ruft `docker` als
Argumentliste ohne Shell. Zeitlimits: Logs 30 s, Neustart/Stopp 90 s, Start 60 s, Host-Neustart 90 s,
Platz schaffen 330 s.

**Was die App absichtlich nicht kann:** `friday` stoppen (`NICHT_STOPPEN` in `kern/dienste/hphome.py`
und im Helfer — sonst gäbe es keine Oberfläche mehr, um ihn wieder zu starten), herunterfahren,
`docker compose down`, Stack-Neustart, Shell, Images/Volumes löschen, Docker-Updates. Logs stehen nie im
Zustand für die Seite und nie im Verlauf (dort nur Aktion, Container, ok/Fehler, 200 Zeichen).

Die POST-Routen sind durch `frisch_halten` geschützt: Schema, Host **und Port** müssen exakt zu
`FRIDAY_URL` passen (oder `http://127.0.0.1|localhost|[::1]:8124`). Sonarr, Jellyfin & Co. auf demselben
Hostnamen zählen als fremd. GET wird nicht geprüft und darf deshalb nichts auslösen.

### 2. Friday selbst (das Modell)

Das Modell darf Container **nur ansehen**, nicht steuern: Werkzeug `server_status`
(`kern/werkzeuge/hphome.py`), Konnektor `hphome`, standardmäßig **aus**. Es liefert Platte, Auslastung,
Laufzeit, welche Dienste laufen/gestoppt/krank sind, Tailnet und die letzte Sicherung — **keine Logs**
(dort kann fremder Text oder ein Schlüssel stehen), keinen Neustart, kein Aufräumen. Diese Befehle gibt
es bewusst nur als Eikes Tipp in der App.

### 3. Der Werkbank-Worker

Der Claude-Worker der Werkbank darf `docker` **nie** selbst aufrufen (`Bash(docker:*)`, `Bash(sudo:*)`,
`Bash(podman:*)` stehen auf der Verbotsliste). Die einzige Tür ist
`host-helfer/werkbank_docker.py`:

```
werkbank_docker.py ps
werkbank_docker.py logs <container> [zeilen]        # Standard 100, höchstens 2000
werkbank_docker.py inspect <container>
werkbank_docker.py start|stop|restart <container>
werkbank_docker.py exec <container> <befehl> [argumente …]   # ohne Shell, ohne Terminal
```

Grenzen des Skripts: exakter Name aus `docker ps -a` (keine ID, kein Muster), Ausgabe höchstens 20 000
Zeichen, Zeitlimit 120 s. **Gesperrt** sind alle Container mit `immich` im Namen und alles aus dem
Compose-Projekt `friday-web` — also Fridays ganzer Stack samt WAHA, Graphiti, FalkorDB, Embedder,
Studio, Snapmem und der Werkbank-Sandbox. Grund: Im Projekt `friday-web` liegen `data/` und `.env`, die
der Worker nie lesen darf. Die Prüfung steht im Skript, nicht im Prompt. Jeder Aufruf landet in
`data/werkbank/docker-aufrufe.log` (Zeit, Aktion, Container, **kein** Ausgabetext).

Der **Prüf-Worker** (Machbarkeitsfragen) benutzt dieselbe Tür, sonst nur `Read`/`Grep`/`Glob`. Der
**Bau-Worker** darf zusätzlich im eigenen Worktree schreiben, aber nur unter `kern/`, `web/`, `test/`,
`eval/szenarien/` und `docs/`. Beide haben kein Netz für fremde Adressen.

### 4. Auf dem Host (Eike und die Helfer)

Fridays eigener Stack wird auf dem Host mit Compose gesteuert, nicht aus einem Container heraus:

```
docker compose up -d                                   # alles außer den Profil-Diensten
docker compose up -d --build friday                    # Friday neu bauen und ersetzen
docker compose --profile werkbank up -d werkbank-sandbox werkbank-proxy
docker compose logs --tail 100 friday
curl -s http://127.0.0.1:8124/api/health
```

Der **Claude-Helfer** (`host-helfer/claude_helfer.py`, tmux `friday-helfer`, cron wie oben) baut und
ersetzt Friday bei einer Übernahme (`docker compose up -d --build friday`), sichert vorher das laufende
Image als `friday-web:vor-werkbank-<id>` und legt für die Tests Wegwerf-Container
`werkbank-test-<id>` an, die er danach entfernt. Der **Schlüssel-Helfer**
(`host-helfer/schluessel_helfer.py`) schaltet bei knapper Reserve den Graphiti-Schlüssel in `.env` um
und startet dafür `docker compose up -d graphiti` (der Container liest `.env` neu).

Nach einer Änderung an einem Helfer muss er neu gestartet werden — der laufende Prozess kennt sonst nur
den alten Stand:

```
tmux kill-session -t friday-hphome-helfer; host-helfer/hphome_helfer_starten.sh
tmux kill-session -t friday-helfer;        host-helfer/helfer_starten.sh
```

### 5. Nur über Auftragsdateien

Drei Arbeiter haben absichtlich **keinen** Docker-Socket und kein Netz nach außen. Friday legt eine
Datei ab, der Arbeiter rechnet und schreibt das Ergebnis zurück:

| Container | Austausch |
|---|---|
| `friday-studio` | `data/studio/auftraege/<id>.json` → Videos unter `data/studio/` |
| `friday-snapmem` | `data/snapmem/auftraege/`, Uploads, `ergebnisse/` |
| `friday-werkbank-sandbox` | `data/werkbank/sandbox/<id>/in.json` → `out.json` daneben |

Das ist der Grund, warum diese Container auch per App nur neu gestartet, aber nicht „befehligt“ werden:
Ihre Arbeit kommt aus Dateien, nicht aus Docker.

---

## Fridays eigener Stack — Compose-Projekt `friday-web`

`docker-compose.yml` im Repo. Das Projekt heißt `friday-web`; alle Container daraus sind für den
Werkbank-Worker gesperrt. Daten liegen in `./data` (nicht im Repo, Sicherung jede Nacht nach
`data/sicherung/`), Geheimnisse in `.env` (nie ausgeben). Netz: das automatisch angelegte
`friday-web_default`; die beiden Werkbank-Dienste haben zusätzlich ein eigenes internes Netz.

### `friday`

- **Aufbau:** `build: .`, Image `friday-web:local`, Container `friday`, `restart: unless-stopped`.
- **Zweck:** der FastAPI-Kern (`kern/main.py`): Sprechen (`/ws/live`), Chat, Werkzeuge, Gedächtnis,
  Planer, Hintergrundläufe; liefert die PWA unter `web/` aus.
- **Netz/Port:** intern 8080, auf dem Host nur `127.0.0.1:8124`. HTTPS macht
  `tailscale serve --bg 8124` → `https://hphome.beispiel.ts.net/`. Im Compose-Netz erreicht er
  `waha`, `graphiti`, `falkordb`, `embedder` und über `friday-web_default` auch
  `friday-personenstandort`. `host.docker.internal` führt auf den Host (qBittorrent).
- **Daten:** `./data` → `/app/data`; `~/media-data/downloads` (rw) und `~/media-data/media` (ro) für den
  Medien-Tab; `~/.config/friday` → `/run/friday-geheim` (ro, dort liegt der DeepSeek-Schlüssel).
- **Steuerung:** App (neustarten, starten, Logs — **nicht** stoppen), Compose, Claude-Helfer (Bauen und
  Ersetzen, Image-Sicherung). Für den Werkbank-Worker gesperrt.
- **Zu beachten:** gehärtet mit `read_only`, `cap_drop: ALL`, `no-new-privileges`, 3 GB Speicher,
  512 Prozesse, tmpfs für `/tmp` und `~/.cache`, kein Docker-Socket. Fällt ein Baustein unter der Härtung
  um, zuerst nachsehen, wohin er außerhalb von `data/` schreiben will — nicht die Härtung lockern.
  `NICHT_STOPPEN = {"friday"}` gilt in `kern/dienste/hphome.py` und im Helfer.

### `friday-waha`

- **Aufbau:** `devlikeapro/waha:gows-2026.8.2`, Container `friday-waha`, `restart: unless-stopped`.
- **Zweck:** WhatsApp als REST-Schnittstelle (Maschine GOWS, kein Chromium). Friday liest Chats nur nach
  Freigabe je Chat und sendet nur mit Chat-Häkchen, Schreibrecht und Eikes Ja auf den Wortlaut.
- **Netz/Port:** nur intern, Port 3000 nicht veröffentlicht.
- **Daten:** `./data/waha` — die Kopplung überlebt einen Neubau; ohne sie müsste Eike neu per QR koppeln.
- **Steuerung:** App, Compose. Für den Worker gesperrt (Projekt `friday-web`).
- **Zu beachten:** Schlüssel `WAHA_SCHLUESSEL` in `.env`. WhatsApp erlaubt keine fremden Programme; das
  automatisierte Senden ist der Hauptgrund für Sperren, deshalb bleibt es bei Freigaben in Friday.

### `friday-falkordb`

- **Aufbau:** `falkordb/falkordb:v4.20.4`, Container `friday-falkordb`, `restart: unless-stopped`.
- **Zweck:** Graphdatenbank für das Langzeitgedächtnis.
- **Netz/Port:** nur intern, Port 6379 (Redis-Protokoll).
- **Daten:** `./data/graph` — der Wissensgraph. Das Gedächtnis löscht nie (Ausnahme: Kopie einer
  Personengruppe beim Widerruf einer Freigabe).
- **Steuerung:** App, Compose. Für den Worker gesperrt.
- **Zu beachten:** Healthcheck `redis-cli ping`; Browser-Oberfläche aus (`BROWSER=0`). Graphiti startet
  erst, wenn der Healthcheck gesund ist.

### `friday-embedder`

- **Aufbau:** `ghcr.io/huggingface/text-embeddings-inference:cpu-1.8`, Container `friday-embedder`,
  `restart: unless-stopped`, 3 GB Speicher.
- **Zweck:** rechnet Embeddings für Graphiti auf hphome selbst — kein Tageskontingent, nichts verlässt
  den Server.
- **Netz/Port:** nur intern, Port 8080.
- **Daten:** `./data/embedder` (Modell-Cache; das Modell lädt beim ersten Start, einige hundert MB).
- **Steuerung:** App, Compose. Für den Worker gesperrt.
- **Zu beachten:** Standardmodell `intfloat/multilingual-e5-small` (`EMBEDDER_LOKAL_MODELL`). Graphiti
  benutzt ihn erst, wenn `EMBEDDER_PROVIDER_GRAPHITI=openai` gesetzt ist — und zwar **nach** dem
  Neu-Einbetten, sonst passen die Dimensionen nicht.

### `friday-graphiti`

- **Aufbau:** `zepai/knowledge-graph-mcp:1.1.0-graphiti-0.30.1-standalone`, Container `friday-graphiti`,
  `restart: unless-stopped`, `depends_on` FalkorDB (healthy).
- **Zweck:** Langzeitgedächtnis als MCP-Server: Friday legt Episoden ab und fragt Wissen ab.
- **Netz/Port:** nur intern, Port 8000 (`/mcp`). `GRAPHITI_URL=http://graphiti:8000/mcp` im Kern.
- **Daten:** der Graph liegt in FalkorDB (`./data/graph`); `graphiti/config.yaml` und
  `graphiti/friday_start.py` sind read-only eingehängt.
- **Steuerung:** App, Compose, Schlüssel-Helfer. Für den Worker gesperrt.
- **Zu beachten:** `SEMAPHORE_LIMIT=1` (eine Episode nach der anderen), Telemetrie aus, eigener
  Gemini-Schlüssel aus eigenem Google-Projekt. Der Schlüssel-Helfer kann den Schlüssel in `.env`
  umschalten und startet dafür `docker compose up -d graphiti`.

### `friday-studio`

- **Aufbau:** `build: ./studio`, Image `friday-studio:local`, Container `friday-studio`,
  `restart: unless-stopped`, läuft als Eike (`UID`/`GID`).
- **Zweck:** rendert Videos für den YouTube-Kanal (ffmpeg und eigene Abhängigkeiten) — bewusst nicht im
  schlanken Friday-Abbild.
- **Netz/Port:** kein Netz nötig, kein Docker-Socket, kein Port.
- **Daten:** `./data/studio` → `/studio` (Aufträge rein, Videos raus), dazu die vier Arbeitsordner
  `assets/temp`, `assets/audio_clips`, `assets/video_clips`, `assets/final`.
- **Steuerung:** App, Compose, Auftragsdateien (`data/studio/auftraege/<id>.json`). Für den Worker
  gesperrt.
- **Zu beachten:** `STUDIO_ZEITLIMIT=3600` (ein hängender Auftrag blockiert den Arbeiter nicht ewig).
  Der Mount auf die vier Arbeitsordner ist Absicht: ein Mount auf ganz `assets` würde `assets/fonts` aus
  dem Abbild überdecken.

### `friday-snapmem`

- **Aufbau:** `build: ./snapmem`, Image `friday-snapmem:local`, Container `friday-snapmem`,
  `restart: unless-stopped`, `network_mode: none`, 4 GB Speicher, läuft als Eike.
- **Zweck:** führt Snapchat-Memories zusammen und filtert sie (ffmpeg, OpenCV, ONNX-Modell).
- **Netz/Port:** **kein Netz** — nichts geht nach außen.
- **Daten:** `./data/snapmem` → `/snapmem` (Aufträge, Uploads, Ergebnisse).
- **Steuerung:** App, Compose, Auftragsdateien (`data/snapmem/auftraege/`, Routen `/api/snapmem…`). Für
  den Worker gesperrt.
- **Zu beachten:** Das ONNX-Modell `clip_vision_q.onnx` liegt nicht im Git; Details in
  `snapmem/models/README.md`.

### `friday-werkbank-sandbox`

- **Aufbau:** nur mit `--profile werkbank`; `build: ./werkbank-sandbox`, Image
  `friday-werkbank-sandbox:local`, Container `friday-werkbank-sandbox`, `restart: unless-stopped`,
  `read_only`, `cap_drop: ALL`, 1 GB, 256 Prozesse, läuft als Eike.
- **Zweck:** führt Werkbank-Aufträge mit OpenCode aus.
- **Netz/Port:** nur im internen Netz `werkbank-sandbox` (kein Weg nach draußen, nicht ins Host-LAN,
  nicht zu den Friday-Diensten). Einziger Ausgang ist `friday-werkbank-proxy` mit Positivliste.
- **Daten:** `./data/werkbank/sandbox` → `/auftrag`; je Auftrag `<id>/in.json`, der Läufer schreibt
  `out.json` daneben. `docs/`, `AGENTS.md` und `CLAUDE.md` sind read-only unter `/projekt` eingehängt —
  dort steht auch diese Datei.
- **Steuerung:** App, Compose, Auftragsdateien. Für den Worker gesperrt (Projekt `friday-web`).
- **Zu beachten:** keine `.env`, keine Schlüssel, außer dem Auftragsordner kein `data/`, kein Docker.
  Vor dem ersten Start muss `data/werkbank/sandbox` als Eike existieren — der Mount legt den Ordner
  bewusst nicht an (`create_host_path: false`), sonst gehörte er root.

### `friday-werkbank-proxy`

- **Aufbau:** nur mit `--profile werkbank`; dasselbe Image wie die Sandbox (`pull_policy: never`, also
  nur lokal gebaut), Container `friday-werkbank-proxy`, `read_only`, `cap_drop: ALL`, 256 MB.
- **Zweck:** einziger Ausgang der Sandbox: HTTP-CONNECT-Proxy mit Positivliste (`opencode.ai`,
  `models.dev`, nur Port 443, keine privaten Zieladressen).
- **Netz/Port:** hängt am internen Netz `werkbank-sandbox` und am normalen Netz `werkbank-aussen`.
- **Daten:** keine Mounts, keine Geheimnisse.
- **Steuerung:** App, Compose. Für den Worker gesperrt.
- **Zu beachten:** Stirbt der Proxy, hat die Sandbox keinen Ausgang mehr; sie selbst bleibt erreichbar.
  Ob OpenCode die Proxy-Variablen beachtet, ist beim Live-Gang zu prüfen.

---

## Ein anderer Stack auf hphome

### `friday-personenstandort`

- **Aufbau:** eigener Stack (`~/docker/friday-personenstandort`), nicht in diesem Repo; hängt für Friday
  in `friday-web_default`.
- **Zweck:** speichert je Familienmitglied den **letzten** selbst gemeldeten Standort (OwnTracks,
  HTTP-Modus). Friday liest nur und nur auf Frage (`wo_ist`).
- **Netz/Port:** Port 8131 **nicht** veröffentlicht; Empfang aus dem Tailnet und Lesen aus dem internen
  Netz sind getrennt.
- **Daten:** nicht in diesem Repo. Je Person nur der letzte Punkt (kein Verlauf), nach 30 Tagen weg;
  Passwörter nur als Hash.
- **Steuerung:** App, **Werkbank-Worker**. Sein Compose-Projekt heißt `friday-personenstandort`, nicht
  `friday-web` — deshalb ist er für den Worker **nicht** gesperrt.
- **Zu beachten:** Friday liest über `PERSONENSTANDORT_URL` (Standard
  `http://friday-personenstandort:8131`) und `PERSONENSTANDORT_SCHLUESSEL`. Der Ortsname kommt von
  Nominatim (Koordinaten gehen dorthin), nie aus fremdem Text.

---

## Weitere Dienste auf hphome (nicht in diesem Repo)

Diese Container gehören nicht zu Fridays Compose, laufen aber auf hphome und werden in der App angezeigt.
Titel, Port und Kurzbeschreibung stammen aus `kern/dienste/hphome.py` (`DIENSTE`); die Seite ordnet sie
über den Containernamen zu. Daten und Stack-Konfiguration sind hier **nicht** dokumentiert — wer mehr
braucht, sieht im jeweiligen Stack nach. Alle sind für den Werkbank-Worker erlaubt (außer Immich).

### `jellyfin`

- **Zweck:** Medienserver für Filme und Serien; Friday zeigt die Karte und kann die Wiedergabe steuern
  (Konnektor `jellyfin`, schreibbar).
- **Netz/Port:** 8096 auf hphome. **Steuerung:** App, Worker.

### `sonarr`

- **Zweck:** verwaltet Serien; Friday sucht und lädt Serien über den Dienst (Konnektor `sonarr`).
- **Netz/Port:** 8989 auf hphome. **Steuerung:** App, Worker.

### `radarr`

- **Zweck:** verwaltet Filme; Friday sucht und lädt Filme über den Dienst (Konnektor `radarr`).
- **Netz/Port:** 7878 auf hphome. **Steuerung:** App, Worker.

### `prowlarr`

- **Zweck:** stellt Sonarr und Radarr die Suchquellen bereit. Kein eigenes Friday-Werkzeug.
- **Netz/Port:** 9696 auf hphome. **Steuerung:** App, Worker.

### `qbittorrent`

- **Zweck:** Downloads für den Medien-Tab. Läuft direkt auf dem Host, nicht im Docker-Netz von Friday;
  Friday erreicht ihn über `host.docker.internal`.
- **Netz/Port:** 8080 auf hphome. **Steuerung:** App, Worker.

### `portainer`

- **Zweck:** Docker-Oberfläche im Browser. Kein Friday-Werkzeug.
- **Netz/Port:** 9443 (HTTPS) auf hphome; die Zertifikatswarnung ist normal. **Steuerung:** App, Worker.

### `adguardhome`

- **Zweck:** DNS-Filter im Heimnetz. Kein Friday-Werkzeug.
- **Netz/Port:** 80 auf hphome. **Steuerung:** App, Worker. **Zu beachten:** Stoppen unterbricht den
  DNS-Filter im Heimnetz — nur bewusst.

### `immich_server`

- **Zweck:** Fotos und Videos ansehen und sichern.
- **Netz/Port:** 2283 auf hphome.
- **Daten:** Immich-eigene Volumes, nicht in diesem Repo dokumentiert. Fotos sind privat.
- **Steuerung:** App (neustarten, stoppen, starten, Logs). **Für den Werkbank-Worker gesperrt**:
  `werkbank_docker.py` lehnt jeden Namen mit `immich` und jedes Compose-Projekt `immich` ab.

### `homeassistant`

- **Zweck:** Smarthome-Zentrale. Kein Friday-Werkzeug.
- **Netz/Port:** 8123 auf hphome, nur im Tailnet erreichbar. **Steuerung:** App, Worker.

### `heimdall`

- **Zweck:** Startseite mit Verknüpfungen zu den Diensten. Kein Friday-Werkzeug.
- **Netz/Port:** 8090 auf hphome. **Steuerung:** App, Worker.

**Kein Container:** Cockpit (Port 9090) verwaltet den Server im Browser — Terminal, Dienste, Updates.
Es erscheint deshalb nicht in der Container-Liste, wohl aber als „Extra“ auf der hphome-Seite.

---

## Regeln, die für alle gelten

- **`friday` wird nie gestoppt.** `NICHT_STOPPEN` steht in `kern/dienste/hphome.py` **und** in
  `host-helfer/hphome_helfer.py`. Wer ihn wirklich stoppen muss, tut das auf dem Host — und startet ihn
  dort auch wieder.
- **Der Werkbank-Worker kommt nie an Fridays Stack.** Gesperrt sind das Compose-Projekt `friday-web`
  und alles mit `immich` im Namen. Grund: `data/` und `.env` liegen dort. Die Prüfung steht im Skript,
  nicht im Prompt; `docker`, `sudo` und `podman` sind für den Worker verboten.
- **Kein Docker-Socket im Container.** Weder `friday` noch `studio`, `snapmem` oder die Sandbox haben
  einen. Wer den Socket hat, ist faktisch Administrator des Servers.
- **Logs nur per POST und geschwärzt.** Sie können Schlüssel enthalten; sie gehen nie in den Zustand für
  die Seite und nie in den Verlauf. `GET /api/hphome` liefert bewusst kein `letzte_antwort`.
- **Namen werden geprüft.** Die App akzeptiert nur `^[a-z0-9][a-z0-9_.-]{0,62}$` und nur Namen aus dem
  zuletzt geschriebenen Zustand; der Helfer prüft noch einmal gegen `docker ps -a`. Keine IDs, keine
  Muster, keine Shell.
- **Herkunft wird geprüft.** Ändernde Routen (`POST`) brauchen exakt Fridays Origin. GET darf nichts
  auslösen — deshalb ist auch `logs` ein POST.
- **`.env` und `data/` sind tabu** für jeden Worker und für das Modell. In der Doku stehen höchstens
  Datei- und Variablennamen, nie Werte.
- **Unbekannte Container sind nicht unsichtbar.** Die hphome-Seite zeigt alles aus `docker ps -a`, auch
  was hier nicht steht. Wegwerf-Container heißen `werkbank-test-<id>` und werden vom Helfer nach dem
  Testlauf entfernt. Ein ganz verschwundener Container gilt als bewusst entfernt (`compose down`) und
  löst keine Wachen-Meldung aus.

---

## Wenn ein Container dazukommt

1. Eintrag in `docker-compose.yml` (bzw. im eigenen Stack) — außerhalb der Werkbank-Übernahme, also von
   Eike.
2. Abschnitt in dieser Datei (`### \`name\``) mit Zweck, Netz/Port, Daten, Steuerung, Besonderheit.
3. Eintrag in `kern/dienste/container_doku.py` (`CONTAINER`) — dieselben Felder, plus `stack`, `wege`,
   `stoppbar`, `werkbank_gesperrt`.
4. Ist es ein bekannter Dienst für die hphome-Seite: `DIENSTE` in `kern/dienste/hphome.py` ergänzen
   (Titel, Port, Info). Die Seite ordnet sonst nur den Namen zu.
5. `python -m unittest test.test_container_doku -v` laufen lassen. Der Test vergleicht gegen
   `docker-compose.yml`, `hphome.DIENSTE`, `NICHT_STOPPEN` und die Sperre in `werkbank_docker.py` — so
   fällt auf, wenn eine Stelle vergessen wurde. Im Wegwerf-Container:
   `scripts/test-isoliert.sh test.test_container_doku -v`.
6. Neue Datenordner unter `data/` in die Sicherung aufnehmen; neue Dienste, die nach außen gehen, in
   `docs/LEHREN.md` mit Grund und Datum eintragen.

---

## Wo was steht

| Was | Datei / Route |
|---|---|
| Fester Bestand (Maschine) | `kern/dienste/container_doku.py` |
| Fester Bestand (lesbar) | `docs/CONTAINER.md` (diese Datei) |
| Fester Bestand (API) | `GET /api/hphome/doku` |
| Lebender Zustand | `GET /api/hphome`, Helfer `host-helfer/hphome_helfer.py` |
| Aufträge an den Helfer | `kern/dienste/hphome.py` → `data/hphome/auftrag.json` |
| Modell-Werkzeug (nur lesen) | `kern/werkzeuge/hphome.py` (`server_status`) |
| Wache (Platte, ausgefallene Dienste) | `kern/dienste/hphome_wache.py` |
| Werkbank-Tür zu Docker | `host-helfer/werkbank_docker.py` |
| Sperre und Übernahme | `host-helfer/claude_helfer.py` |
| Schlüssel-Umschaltung Graphiti | `host-helfer/schluessel_helfer.py` |
| Test | `test/test_container_doku.py` |
| Container-Definitionen | `docker-compose.yml` |
